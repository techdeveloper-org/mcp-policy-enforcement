"""Cross-process regression test for the record_policy_execution CAS migration.

Modeled on mcp-base/tests/test_persistence_cas.py. Runs real OS subprocesses,
not threads, because the defect this guards against is cross-process: separate
MCP server invocations and workflow-engine hooks writing the same
flow-trace.json file, which a thread-only test cannot exercise.

Each worker sets CLAUDE_INSIGHT_DATA_DIR to an isolated tmp_path before
importing server.py, so no worker ever touches the real ~/.claude/memory
tree.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent

_SESSION_ID = "CAS-CROSS-PROCESS-TEST"


_GUARDED_WORKER = textwrap.dedent(
    """
    import json
    import os
    import sys

    os.environ["CLAUDE_INSIGHT_DATA_DIR"] = sys.argv[2]

    sys.path.insert(0, sys.argv[1])
    import server

    succeeded = 0
    for _ in range(int(sys.argv[3])):
        raw = server.record_policy_execution(
            policy_name="cas-worker",
            policy_script="cas_worker.py",
            policy_type="Test",
            decision="increment",
            duration_ms=0,
            session_id=sys.argv[4],
        )
        if json.loads(raw).get("success"):
            succeeded += 1

    # Sustained max-attempts exhaustion under deliberately synchronized
    # contention is surfaced to the caller as success=False rather than
    # silently dropped, per the ConcurrentModificationError contract; the
    # parent process sums this count to distinguish that loud, expected
    # failure from a silent lost update.
    print(succeeded)
    """
)

_UNGUARDED_WORKER = textwrap.dedent(
    """
    import json
    import sys
    import time
    from pathlib import Path

    flow_trace_file = Path(sys.argv[1])

    def default_trace():
        return {
            "meta": {"session_id": "control", "schema_version": "1.0"},
            "all_policies_executed": [],
            "execution_summary": {"total_policies_executed": 0},
            "decisions_timeline": [],
        }

    for _ in range(int(sys.argv[2])):
        try:
            if flow_trace_file.exists():
                flow_trace = json.loads(flow_trace_file.read_text(encoding="utf-8"))
            else:
                flow_trace = default_trace()

            flow_trace["all_policies_executed"].append({"policy_name": "cas-worker"})
            flow_trace["execution_summary"]["total_policies_executed"] = len(
                flow_trace["all_policies_executed"]
            )

            # Widens the read-modify-write window so the configured worker and
            # increment counts reliably lose updates without the CAS fix,
            # proving this test applies enough contention pressure for the
            # guarded test to mean anything.
            time.sleep(0.001)

            # The pre-fix code used this fixed temp name (unlike AtomicJsonStore's
            # unique-per-attempt name), so concurrent workers can also collide on
            # the temp file itself; that failure mode is left unhandled here
            # exactly as it was in the pre-fix tool, and is itself further
            # evidence of the defect this migration fixes.
            temp = flow_trace_file.with_suffix(".tmp")
            temp.write_text(json.dumps(flow_trace, indent=2, default=str), encoding="utf-8")
            temp.replace(flow_trace_file)
        except (OSError, ValueError):
            pass
    """
)


def _run_guarded_workers(data_dir, workers, increments):
    """Launch worker processes that call the real record_policy_execution tool.

    Args:
        data_dir: Isolated CLAUDE_INSIGHT_DATA_DIR each worker writes under.
        workers: Number of concurrent OS processes to launch.
        increments: Number of tool calls each worker makes.

    Returns:
        A ``(records, succeeded, flow_trace_file)`` tuple: the persisted
        ``all_policies_executed`` list, the total count of calls each worker
        itself reported as successful, and the path of the flow-trace file.
    """
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _GUARDED_WORKER,
             str(_REPO_ROOT), str(data_dir), str(increments), _SESSION_ID],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for _ in range(workers)
    ]
    outputs = [proc.communicate(timeout=180)[0] for proc in procs]
    for proc in procs:
        assert proc.returncode == 0

    succeeded = sum(int(out.strip()) for out in outputs)

    flow_trace_file = (
        Path(data_dir) / "config" / "logs" / "sessions" / _SESSION_ID / "flow-trace.json"
    )
    flow_trace = json.loads(flow_trace_file.read_text(encoding="utf-8"))
    return flow_trace["all_policies_executed"], succeeded, flow_trace_file


def _run_unguarded_workers(target, workers, increments):
    """Launch worker processes replaying the pre-fix load/mutate/save cycle.

    Args:
        target: File path the workers race to read-modify-write.
        workers: Number of concurrent OS processes to launch.
        increments: Number of append cycles each worker performs.

    Returns:
        The number of records found in all_policies_executed after every
        worker has exited.
    """
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _UNGUARDED_WORKER, str(target), str(increments)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(workers)
    ]
    for proc in procs:
        assert proc.wait(timeout=180) == 0

    flow_trace = json.loads(target.read_text(encoding="utf-8"))
    return flow_trace["all_policies_executed"]


class TestCrossProcessConcurrency:
    """Proves record_policy_execution no longer loses concurrent appends."""

    WORKERS = 6
    INCREMENTS = 12

    @property
    def expected(self):
        return self.WORKERS * self.INCREMENTS

    def test_unguarded_cycle_loses_updates(self, tmp_path):
        """Control: the pre-fix pattern must lose records under this pressure.

        If this does not lose a record, the guarded test below proves nothing
        because the worker/increment counts would not be generating real
        contention on this machine.
        """
        target = tmp_path / "flow-trace.json"

        records = _run_unguarded_workers(target, self.WORKERS, self.INCREMENTS)

        assert len(records) < self.expected, (
            "unguarded read-modify-write did not lose a record under "
            "contention; the concurrency guarantee below is therefore untested"
        )

    def test_record_policy_execution_loses_no_updates_across_processes(self, tmp_path):
        """Every call must both succeed and be persisted, at default settings.

        Two invariants are asserted, and both matter:

        - persisted equals reported-success, which catches the primary defect:
          a call reporting success while its data silently vanishes.
        - every call reports success, which catches a regression in the retry
          budget. An earlier unjittered backoff let synchronized losers retry
          in lock-step and exhaust the default attempt budget, surfacing
          ``success: False`` for a fraction of calls. That is a surfaced
          failure rather than silent loss, so the first invariant alone still
          held -- which is exactly why it is not sufficient on its own.

        Neither the tool nor this test raises ``max_attempts``. A test that
        passes only at an inflated budget proves nothing about the default
        that every real caller gets.
        """
        data_dir = tmp_path / "data"

        records, succeeded, flow_trace_file = _run_guarded_workers(
            data_dir, self.WORKERS, self.INCREMENTS
        )

        assert len(records) == succeeded, (
            f"{succeeded} calls reported success but only {len(records)} "
            "records were persisted -- a successful call's data was lost"
        )
        assert succeeded == self.expected, (
            f"only {succeeded} of {self.expected} calls succeeded; the retry "
            "budget is exhausting under contention it should absorb"
        )

        leftovers = [
            p.name for p in flow_trace_file.parent.iterdir()
            if p.name != "flow-trace.json"
        ]
        assert leftovers == [], "temp or claim files were left behind"
