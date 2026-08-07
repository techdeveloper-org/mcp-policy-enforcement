"""
Policy Enforcement MCP Server - FastMCP migration of enforcement_server.py.

Replaces the custom JSON-RPC class with proper FastMCP decorator-based tools.
Includes flow-trace recording, policy execution tracking, and full step mapping.
Backend: Direct file I/O (JSON state files, policy directory scanning)
Transport: stdio

Tools (11):
  check_enforcement_status, enforce_policy_step, log_tool_usage,
  verify_compliance, list_policies, record_policy_execution,
  get_flow_trace_summary, get_session_id, check_module_health,
  check_all_mcp_servers_health, check_system_health
Resources (2):
  enforcement://status, enforcement://compliance
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.path_resolver import (
    get_config_dir,
    get_data_dir,
    get_policies_dir,
    get_scripts_dir,
    get_settings_file,
)

# mcp 2.0 renamed FastMCP to MCPServer and moved it to mcp.server.mcpserver.
# Both names are probed so this server runs under either major version; the
# API used below (tool decorator, run(transport=...)) is identical in both.
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp < 2.0
    from mcp.server.fastmcp import FastMCP as MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from base.decorators import mcp_tool_handler
from base.persistence import AtomicJsonStore, SessionIdResolver

mcp = MCPServer("policy-enforcement", instructions="Policy enforcement and compliance tracking")

# Paths
MEMORY_PATH = get_config_dir()
ENFORCER_STATE_FILE = MEMORY_PATH / ".blocking-enforcer-state.json"
LOGS_PATH = MEMORY_PATH / "logs" / "sessions"

# This repository's own directory, and the workspace directory that holds it
# alongside its sibling mcp-* server repositories.
_REPO_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _REPO_DIR.parent

# Policy directory. Resolved through path_resolver so CLAUDE_POLICIES_DIR is
# honored; defaults to ~/.claude/policies.
POLICIES_DIR = get_policies_dir()

# Read-only annotation reused by every tool that only inspects state on disk.
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# Module-level store instance
_enforcer_store = AtomicJsonStore(ENFORCER_STATE_FILE)


# =============================================================================
# TOOLS (11)
# =============================================================================

@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def check_enforcement_status() -> dict:
    """Check current policy enforcement status for all steps."""
    state = _enforcer_store.load()
    return {
        "state": state,
        "timestamp": datetime.now().isoformat()
    }


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False,
    idempotentHint=True, openWorldHint=False))
@mcp_tool_handler
def enforce_policy_step(
    step_number: Annotated[int, Field(
        description="Pipeline step number to mark as enforced, 0 through 13.")],
    step_name: Annotated[str, Field(
        description="Human-readable step name recorded alongside the step "
                    "number, e.g. 'Task Breakdown'.")],
) -> dict:
    """Enforce a specific policy step in the execution pipeline.

    Records the step under a compare-and-swap ``modify`` cycle so a
    concurrent writer updating a different step key cannot lose this
    update, and vice versa: both retries replay against the freshly
    loaded state rather than each overwriting the other's key.

    Args:
        step_number: Step number (0-13)
        step_name: Human-readable step name
    """
    script_map = {
        0: "00-prompt-generation/prompt-generation-policy.md",
        1: "01-task-breakdown/automatic-task-breakdown-policy.md",
        2: "02-plan-mode/auto-plan-mode-suggestion-policy.md",
        3: "00-code-graph-analysis/code-graph-analysis-policy.md",
        4: "04-model-selection/intelligent-model-selection-policy.md",
        5: "05-skill-agent-selection/auto-skill-agent-selection-policy.md",
        6: "06-tool-optimization/tool-usage-optimization-policy.md",
        7: "00-context-reading/context-reading-policy.md",
        8: "08-progress-tracking/task-progress-tracking-policy.md",
        9: "09-git-commit/git-auto-commit-policy.md",
        10: "github-branch-pr-policy.md",
        11: "github-issues-integration-policy.md",
        12: "parallel-execution-policy.md",
        13: "failure-prevention/common-failures-prevention.md",
    }

    def _mark_step_enforced(state: dict) -> None:
        """Set the step_N entry on the dict handed to it by ``modify``.

        Recomputes the timestamp on every call so a retried attempt
        records the time its own write actually took effect rather than
        the time of an earlier, superseded attempt.
        """
        state[f"step_{step_number}"] = {
            "name": step_name,
            "status": "ENFORCED",
            "timestamp": datetime.now().isoformat(),
        }

    _enforcer_store.modify(_mark_step_enforced)

    policy_path = script_map.get(step_number)
    policy_exists = False
    full_path_str = ""
    if policy_path:
        full_path = POLICIES_DIR / "03-execution-system" / policy_path
        policy_exists = full_path.exists()
        full_path_str = str(full_path)

    return {
        "step": step_number,
        "name": step_name,
        "policy_file": policy_path,
        "policy_exists": policy_exists,
        "policy_path": full_path_str,
        "message": f"Step {step_number} ({step_name}) enforced"
    }


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False,
    idempotentHint=False, openWorldHint=False))
@mcp_tool_handler
def log_tool_usage(
    tool_name: Annotated[str, Field(
        description="Name of the tool that was called, e.g. 'Read', 'Write', "
                    "'Edit', 'Bash'.")],
    operation: Annotated[str, Field(
        description="Short description of what the tool call did.")],
    parameters: Annotated[str, Field(
        description="JSON object string of the tool's parameters. Invalid JSON "
                    "is dropped rather than logged.")] = "{}",
    result: Annotated[str, Field(
        description="Outcome of the call: SUCCESS, ERROR, or OPTIMIZED.")] = "SUCCESS",
) -> dict:
    """Log a tool call made by Claude for tracking.

    Args:
        tool_name: Tool name (Read, Write, Edit, Bash, etc.)
        operation: Description of what was done
        parameters: JSON string of tool parameters
        result: Result status (SUCCESS, ERROR, OPTIMIZED)
    """
    # Append to log file
    log_dir = MEMORY_PATH / ".tool-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"tools-{today}.jsonl"

    entry = {
        "tool": tool_name,
        "operation": operation,
        "result": result,
        "timestamp": datetime.now().isoformat()
    }

    try:
        params = json.loads(parameters)
        if params:
            entry["parameters"] = params
    except (json.JSONDecodeError, TypeError):
        pass

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    return {
        "tool": tool_name,
        "operation": operation,
        "logged": True,
        "timestamp": entry["timestamp"]
    }


@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def verify_compliance() -> dict:
    """Verify that all required policy steps have been enforced."""
    state = _enforcer_store.load()

    # Dynamic: check which step_N keys are present
    required_steps = {
        "step_0": "Prompt Generation",
        "step_1": "Task Breakdown",
        "step_2": "Plan Mode Decision",
        "step_3": "Code Graph Analysis",
        "step_4": "Model Selection",
        "step_5": "Skill/Agent Selection",
        "step_6": "Tool Optimization",
        "step_7": "Context Reading",
        "step_8": "Progress Tracking",
        "step_9": "Git Commit",
        "step_10": "GitHub Branch/PR",
        "step_11": "GitHub Issues",
        "step_12": "Parallel Execution",
        "step_13": "Failure Prevention",
    }

    # Also check legacy keys for backward compatibility
    legacy_keys = {
        "session_started": "Session Start",
        "context_checked": "Context Check",
        "standards_loaded": "Standards Loaded",
        "prompt_generated": "Prompt Generation",
        "tasks_created": "Task Breakdown",
        "plan_mode_decided": "Plan Mode Decision",
        "model_selected": "Model Selection",
        "skills_agents_checked": "Skills/Agents Check"
    }

    completed = []
    missing = []
    for key, name in required_steps.items():
        step_data = state.get(key)
        if step_data and step_data.get("status") == "ENFORCED":
            completed.append(name)
        else:
            missing.append(name)

    # Check legacy keys too
    legacy_completed = []
    for key, name in legacy_keys.items():
        if state.get(key):
            legacy_completed.append(name)

    compliant = len(missing) == 0 or len(legacy_completed) >= 6

    return {
        "compliant": compliant,
        "completed_steps": len(completed),
        "total_steps": len(required_steps),
        "missing_steps": missing,
        "legacy_completed": legacy_completed,
        "timestamp": datetime.now().isoformat()
    }


@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def list_policies(
    level: Annotated[str, Field(
        description="Level directory filter. 'all' lists every level; any other "
                    "value is matched as a substring of the level directory "
                    "name, e.g. '02-standards'.")] = "all",
) -> dict:
    """List all policy files with their status.

    Args:
        level: Filter by level - 'all', '01-sync', '02-standards', '03-execution', 'testing'
    """
    if not POLICIES_DIR.exists():
        return {
            "success": False,
            "error": f"Policies directory not found: {POLICIES_DIR}"
        }

    policies = []

    if level == "all":
        search_dirs = [d for d in POLICIES_DIR.iterdir() if d.is_dir()]
    else:
        # Match by prefix
        search_dirs = [d for d in POLICIES_DIR.iterdir() if d.is_dir() and level in d.name]

    for level_dir in sorted(search_dirs):
        for policy_file in sorted(level_dir.rglob("*.md")):
            if policy_file.name == "README.md":
                continue
            rel_path = policy_file.relative_to(POLICIES_DIR)
            stat = policy_file.stat()
            policies.append({
                "path": str(rel_path),
                "name": policy_file.stem.replace("-", " ").title(),
                "level": level_dir.name,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })

    return {
        "policies": policies,
        "count": len(policies),
        "policies_dir": str(POLICIES_DIR)
    }


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False,
    idempotentHint=False, openWorldHint=False))
@mcp_tool_handler
def record_policy_execution(
    policy_name: Annotated[str, Field(
        description="Policy identifier, e.g. 'session-id-generator'.")],
    policy_script: Annotated[str, Field(
        description="Script filename that implements the policy, e.g. "
                    "'session-id-generator.py'.")],
    policy_type: Annotated[str, Field(
        description="Policy category, e.g. 'Utility Hook' or 'Policy Script'.")],
    decision: Annotated[str, Field(
        description="The outcome the policy decided, recorded in the decisions "
                    "timeline.")],
    duration_ms: Annotated[int, Field(
        description="Wall-clock execution time of the policy in milliseconds.")],
    input_params: Annotated[str, Field(
        description="JSON object string of the policy's inputs. Invalid JSON is "
                    "recorded as an empty object.")] = "{}",
    output_results: Annotated[str, Field(
        description="JSON object string of the policy's outputs. Invalid JSON is "
                    "recorded as an empty object.")] = "{}",
    session_id: Annotated[Optional[str], Field(
        description="Session to record under. Auto-detected from the current "
                    "session file when omitted.")] = None,
    sub_operations: Annotated[Optional[str], Field(
        description="JSON array string of sub-operation records nested under "
                    "this policy execution.")] = None,
) -> dict:
    """Record a policy execution to flow-trace.json for tracking.

    Appends the record under a compare-and-swap ``modify`` cycle so a
    concurrent recorder for the same session cannot lose this execution:
    a losing attempt replays its append against the freshly loaded
    ``all_policies_executed`` list instead of overwriting it.

    Args:
        policy_name: Policy name (e.g., 'session-id-generator')
        policy_script: Script filename (e.g., 'session-id-generator.py')
        policy_type: Type (e.g., 'Utility Hook', 'Policy Script')
        decision: What the policy decided
        duration_ms: Execution duration in milliseconds
        input_params: JSON string of input parameters
        output_results: JSON string of output results
        session_id: Session ID (auto-detected from .current-session.json if empty)
        sub_operations: JSON string of sub-operation records
    """
    sid = session_id
    if not sid:
        sid = SessionIdResolver(MEMORY_PATH).get()
    if not sid:
        sid = "unknown"

    flow_trace_file = LOGS_PATH / sid / "flow-trace.json"
    flow_trace_store = AtomicJsonStore(flow_trace_file)

    try:
        inp = json.loads(input_params)
    except (json.JSONDecodeError, TypeError):
        inp = {}
    try:
        out = json.loads(output_results)
    except (json.JSONDecodeError, TypeError):
        out = {}

    parsed_sub_operations = None
    if sub_operations:
        try:
            parsed_sub_operations = json.loads(sub_operations)
        except (json.JSONDecodeError, TypeError):
            parsed_sub_operations = None

    default_flow_trace = {
        "meta": {
            "session_id": sid,
            "created_at": datetime.now().isoformat(),
            "schema_version": "1.0"
        },
        "user_input": {},
        "all_policies_executed": [],
        "execution_summary": {"total_policies_executed": 0},
        "decisions_timeline": []
    }

    def _append_execution(flow_trace: dict) -> None:
        """Append one policy-execution record and its timeline entry.

        Recomputes the record timestamp on every call so a retried
        attempt records the time its own write actually took effect
        rather than the time of an earlier, superseded attempt.
        """
        now = datetime.now().isoformat()
        record = {
            "policy_name": policy_name,
            "policy_script": policy_script,
            "policy_type": policy_type,
            "timestamp": now,
            "duration_ms": duration_ms,
            "input": inp,
            "output": out,
            "decision": decision
        }
        if parsed_sub_operations is not None:
            record["sub_operations"] = parsed_sub_operations

        flow_trace["all_policies_executed"].append(record)
        flow_trace["execution_summary"]["total_policies_executed"] = len(
            flow_trace["all_policies_executed"]
        )
        flow_trace["decisions_timeline"].append({
            "timestamp": now,
            "policy": policy_name,
            "decision": decision
        })

    published = flow_trace_store.modify(_append_execution, default=default_flow_trace)

    return {
        "session_id": sid,
        "policy": policy_name,
        "total_recorded": len(published["all_policies_executed"])
    }


@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def get_session_id() -> dict:
    """Get the current session ID from .current-session.json."""
    sid = SessionIdResolver(MEMORY_PATH).get()
    if not sid:
        sid = "unknown"
    return {
        "session_id": sid,
        "is_valid": sid.startswith("SESSION-")
    }


@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def get_flow_trace_summary(
    session_id: Annotated[Optional[str], Field(
        description="Session whose flow-trace to summarize. Auto-detected from "
                    "the current session file when omitted.")] = None,
) -> dict:
    """Get summary statistics from a session's flow-trace.

    Args:
        session_id: Session ID (auto-detected if empty)
    """
    sid = session_id or SessionIdResolver(MEMORY_PATH).get() or "unknown"
    flow_trace_file = LOGS_PATH / sid / "flow-trace.json"

    if not flow_trace_file.exists():
        return {
            "session_id": sid,
            "message": "No flow-trace found for this session"
        }

    flow_trace = json.loads(flow_trace_file.read_text(encoding="utf-8"))
    policies = flow_trace.get("all_policies_executed", [])

    sorted_by_speed = sorted(policies, key=lambda p: p.get("duration_ms", 0))

    return {
        "session_id": sid,
        "total_policies": len(policies),
        "total_duration_ms": sum(p.get("duration_ms", 0) for p in policies),
        "average_duration_ms": (
            sum(p.get("duration_ms", 0) for p in policies) / len(policies)
            if policies else 0
        ),
        "slowest_policy": (
            {"name": sorted_by_speed[-1]["policy_name"],
             "duration_ms": sorted_by_speed[-1]["duration_ms"]}
            if sorted_by_speed else None
        ),
        "fastest_policy": (
            {"name": sorted_by_speed[0]["policy_name"],
             "duration_ms": sorted_by_speed[0]["duration_ms"]}
            if sorted_by_speed else None
        ),
        "decisions_count": len(flow_trace.get("decisions_timeline", []))
    }


# Filenames that mark a repository directory as an executable stdio server.
# Probed in order; the first that exists wins.
_SERVER_ENTRY_NAMES = ("server.py", "mcp_server.py", "main.py", "__main__.py")

# Source-file suffixes a launcher argument must carry to be treated as a
# checkable entry-point path rather than a module name or package spec.
_SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts")


def _static_source_check(path: Path) -> dict:
    """Statically verify a Python source file parses, without executing it.

    Compiling proves the file is syntactically valid Python. It deliberately
    stops short of executing the module: these servers construct an MCPServer,
    mutate sys.path and open resources at import time, so importing every
    sibling server into this process would both cause side effects and collide
    on the identically-named ``base`` and ``utils`` packages each repo vendors.

    Non-Python entry points are reported as present without a parse check,
    since this server cannot compile them.

    Args:
        path: Filesystem path to the entry-point file.

    Returns:
        Dict with a ``status`` key (``OK`` or ``SYNTAX_ERROR``) plus
        ``size_bytes`` and, on failure, ``error``.
    """
    result = {"size_bytes": path.stat().st_size}
    if path.suffix.lower() != ".py":
        result["status"] = "OK"
        result["checked"] = "existence_only"
        return result
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        compile(source, str(path), "exec")
    except SyntaxError as e:
        result["status"] = "SYNTAX_ERROR"
        result["error"] = f"line {e.lineno}: {e.msg}"[:200]
        return result
    except OSError as e:
        result["status"] = "SYNTAX_ERROR"
        result["error"] = f"unreadable: {e}"[:200]
        return result
    result["status"] = "OK"
    result["checked"] = "parsed"
    return result


def _find_repo_entry_point(repo_dir: Path) -> Optional[Path]:
    """Locate the stdio entry-point script inside a server repository.

    Args:
        repo_dir: Directory of a sibling ``mcp-*`` repository.

    Returns:
        Path to the first recognized entry-point file, or None when the
        directory holds no runnable server (a shared-library repo, for example).
    """
    for candidate in _SERVER_ENTRY_NAMES:
        entry = repo_dir / candidate
        if entry.is_file():
            return entry
    prefixed = sorted(p for p in repo_dir.glob("*_server.py") if p.is_file())
    if prefixed:
        return prefixed[0]
    return None


def _entry_path_from_launcher(spec: dict) -> Optional[Path]:
    """Extract the entry-point file path from a registered server's config entry.

    Handles the ``command`` + ``args`` stdio shape used by Claude Code. Returns
    None for launchers that name a module or an installed package rather than a
    file on disk (``-m pkg``, a bare console-script name), since those have no
    path this server can verify.

    Args:
        spec: One ``mcpServers`` entry from the MCP configuration file.

    Returns:
        Absolute Path to the entry-point script, or None when not file-based.
    """
    candidates = list(spec.get("args") or [])
    command = spec.get("command")
    if isinstance(command, str):
        candidates.insert(0, command)

    for raw in candidates:
        if not isinstance(raw, str) or raw.startswith("-"):
            continue
        if not raw.lower().endswith(_SCRIPT_SUFFIXES):
            continue
        path = Path(os.path.expandvars(raw)).expanduser()
        if not path.is_absolute():
            path = (_WORKSPACE_ROOT / path).resolve()
        return path
    return None


def _load_registered_servers() -> tuple:
    """Read the ``mcpServers`` block from the active MCP configuration file.

    The path is taken from CLAUDE_MCP_CONFIG when set, otherwise from
    path_resolver's settings file (~/.claude/settings.json).

    Returns:
        Tuple of (config_path, servers_dict, error_message). ``servers_dict`` is
        empty and ``error_message`` is populated when the file is absent or
        unparseable; that is reported rather than raised so a missing user
        config degrades the tool to filesystem-only discovery.
    """
    override = os.environ.get("CLAUDE_MCP_CONFIG")
    config_path = Path(override).expanduser() if override else get_settings_file()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return config_path, {}, f"MCP config not found: {config_path}"
    except (json.JSONDecodeError, OSError) as e:
        return config_path, {}, f"MCP config unreadable: {str(e)[:150]}"

    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return config_path, {}, "MCP config has no 'mcpServers' object"
    return config_path, servers, ""


def _discover_sibling_repos() -> dict:
    """Scan the workspace root for sibling ``mcp-*`` server repositories.

    Directory names are matched case-insensitively so the scan behaves the same
    on Windows as on case-sensitive filesystems.

    Returns:
        Dict mapping repository directory name to its entry-point Path, or to
        None for repositories that contain no runnable entry point.
    """
    repos = {}
    try:
        children = sorted(_WORKSPACE_ROOT.iterdir())
    except OSError:
        return repos
    for child in children:
        if not child.is_dir():
            continue
        if not child.name.lower().startswith("mcp-"):
            continue
        repos[child.name] = _find_repo_entry_point(child)
    return repos


@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def check_module_health() -> dict:
    """Check which policy modules are present under the architecture directory.

    Discovers modules by scanning the architecture directory rather than
    consulting a fixed list, so modules added or removed since this server was
    written are reported accurately. Each discovered module is statically
    parsed; modules are never executed.

    Level is derived from the leading NN- of the module's top-level directory
    (01-sync-system -> level 1, and so on).
    """
    arch_dir, arch_source = _resolve_architecture_dir()

    if arch_dir is None:
        return {
            "verified_ok": 0,
            "total_modules": 0,
            "missing": [],
            "failed_parse": [],
            "by_level": {},
            "results": {},
            "architecture_dir": None,
            "architecture_dir_source": arch_source,
            "message": (
                "Architecture directory not found. Set CLAUDE_ARCHITECTURE_DIR "
                "to the directory holding the NN-*-system policy modules."
            ),
        }

    results_by_level = {}
    failed_parse = []

    for mod_path in sorted(arch_dir.rglob("*.py")):
        if "__pycache__" in mod_path.parts:
            continue
        rel = mod_path.relative_to(arch_dir)
        level = _level_from_relative_path(rel)
        check = _static_source_check(mod_path)
        record = {
            "name": mod_path.stem,
            "status": check["status"],
            "path": rel.as_posix(),
        }
        if check.get("error"):
            record["error"] = check["error"]
            failed_parse.append(mod_path.stem)
        results_by_level.setdefault(level, []).append(record)

    all_records = [r for records in results_by_level.values() for r in records]
    total = len(all_records)
    total_ok = sum(1 for r in all_records if r["status"] == "OK")

    by_level = {}
    for level, records in sorted(results_by_level.items()):
        by_level[f"level_{level}"] = {
            "ok": sum(1 for r in records if r["status"] == "OK"),
            "total": len(records),
        }

    return {
        "verified_ok": total_ok,
        "total_modules": total,
        "missing": [],
        "failed_parse": failed_parse,
        "by_level": by_level,
        "results": {f"level_{k}": v for k, v in sorted(results_by_level.items())},
        "architecture_dir": str(arch_dir),
        "architecture_dir_source": arch_source,
        "check_method": "static_parse_only",
    }


def _level_from_relative_path(rel: Path) -> str:
    """Derive a pipeline level label from a module path relative to the arch dir.

    Args:
        rel: Module path relative to the architecture directory.

    Returns:
        The numeric prefix of the top-level directory ('1', '2', '3'), or
        'unclassified' when the path carries no NN- prefix.
    """
    if not rel.parts:
        return "unclassified"
    head = rel.parts[0]
    prefix = head.split("-", 1)[0]
    if prefix.isdigit():
        return str(int(prefix))
    return "unclassified"


def _resolve_architecture_dir() -> tuple:
    """Locate the policy-module architecture directory.

    Resolution order: CLAUDE_ARCHITECTURE_DIR, then {scripts_dir}/architecture,
    then any sibling repository in the workspace exposing scripts/architecture.

    Returns:
        Tuple of (Path or None, source label describing how it was resolved).
    """
    override = os.environ.get("CLAUDE_ARCHITECTURE_DIR")
    if override:
        path = Path(override).expanduser()
        if path.is_dir():
            return path, "CLAUDE_ARCHITECTURE_DIR"
        return None, "CLAUDE_ARCHITECTURE_DIR (not a directory)"

    scripts_arch = get_scripts_dir() / "architecture"
    if scripts_arch.is_dir():
        return scripts_arch, "scripts_dir"

    try:
        children = sorted(_WORKSPACE_ROOT.iterdir())
    except OSError:
        children = []
    for child in children:
        candidate = child / "scripts" / "architecture"
        if candidate.is_dir():
            return candidate, f"workspace_scan:{child.name}"

    return None, "unresolved"


@mcp.tool(annotations=_READ_ONLY)
@mcp_tool_handler
def check_all_mcp_servers_health() -> dict:
    """Check every registered and locally-present MCP server entry point.

    The server set is derived at call time from two sources, never from a
    hardcoded list: the ``mcpServers`` block of the active MCP configuration
    (CLAUDE_MCP_CONFIG, else ~/.claude/settings.json), and a scan of the
    workspace directory for sibling ``mcp-*`` repositories. Every count in the
    response is computed from what was actually found.

    This is a STATIC check. It reports whether each server's entry-point file
    exists and parses; it does NOT start any server and therefore cannot prove
    a server is running, that its dependencies are installed, or that a live
    session can call its tools. A server reported OK here may still fail at
    launch, and a server not launchable from a file (a ``-m module`` or console
    -script launcher) is reported as UNVERIFIABLE rather than counted as
    unhealthy.
    """
    config_path, registered, config_error = _load_registered_servers()
    sibling_repos = _discover_sibling_repos()

    results = []
    unverifiable = []
    seen_paths = set()

    for name in sorted(registered):
        spec = registered[name]
        if not isinstance(spec, dict):
            unverifiable.append({"server": name, "status": "UNVERIFIABLE",
                                 "reason": "config entry is not an object"})
            continue

        if spec.get("url"):
            unverifiable.append({"server": name, "status": "UNVERIFIABLE",
                                 "reason": "remote transport (url), no local file to check",
                                 "url": spec["url"]})
            continue

        entry_path = _entry_path_from_launcher(spec)
        if entry_path is None:
            unverifiable.append({"server": name, "status": "UNVERIFIABLE",
                                 "reason": "launcher names a module or package, not a file",
                                 "command": spec.get("command", "")})
            continue

        record = {"server": name, "registered": True, "path": str(entry_path)}
        seen_paths.add(str(entry_path).lower())
        if not entry_path.is_file():
            record["status"] = "MISSING"
            record["error"] = f"Entry point not found: {entry_path}"
        else:
            record.update(_static_source_check(entry_path))
        results.append(record)

    for repo_name in sorted(sibling_repos):
        entry_path = sibling_repos[repo_name]
        if entry_path is None:
            unverifiable.append({"server": repo_name, "status": "UNVERIFIABLE",
                                 "reason": "repository has no recognized entry point "
                                           "(shared library, not a server)"})
            continue
        if str(entry_path).lower() in seen_paths:
            continue
        record = {"server": repo_name, "registered": False, "path": str(entry_path)}
        record.update(_static_source_check(entry_path))
        record["note"] = "present in workspace but not in the MCP configuration"
        results.append(record)

    healthy = sum(1 for r in results if r["status"] == "OK")
    total = len(results)

    response = {
        "healthy": healthy,
        "total": total,
        "all_healthy": total > 0 and healthy == total,
        "servers": results,
        "unverifiable": unverifiable,
        "unverifiable_count": len(unverifiable),
        "config_path": str(config_path),
        "workspace_root": str(_WORKSPACE_ROOT),
        "registered_count": len(registered),
        "workspace_repo_count": len(sibling_repos),
        "check_method": "static_entry_point_parse",
        "check_limitation": (
            "Static only: proves the entry point exists and parses. Does not "
            "start any server, so it cannot confirm a server is running or "
            "that its tools are reachable."
        ),
    }
    if config_error:
        response["config_warning"] = config_error
    return response


# =============================================================================
# RESOURCES (2)
# =============================================================================

@mcp.resource("enforcement://status")
def enforcement_status_resource() -> str:
    """Current enforcement state."""
    return check_enforcement_status()


@mcp.resource("enforcement://compliance")
def enforcement_compliance_resource() -> str:
    """Policy compliance report."""
    return verify_compliance()


@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True, destructiveHint=False,
    idempotentHint=True, openWorldHint=True))
@mcp_tool_handler
def check_system_health() -> dict:
    """Comprehensive system health check across all components.

    Checks: MCP servers, checkpoint DB, vector DB, LLM providers,
    orchestrator graph, and disk usage. Returns aggregated health status.
    """
    health = {
        "timestamp": datetime.now().isoformat(),
        "components": {},
        "overall": "HEALTHY",
    }
    unhealthy = []

    # 1. MCP Servers health
    try:
        mcp_result = json.loads(check_all_mcp_servers_health())
        health["components"]["mcp_servers"] = {
            "status": "HEALTHY" if mcp_result.get("all_healthy") else "DEGRADED",
            "healthy": mcp_result.get("healthy", 0),
            "total": mcp_result.get("total", 0),
        }
        if not mcp_result.get("all_healthy"):
            unhealthy.append("mcp_servers")
    except Exception as e:
        health["components"]["mcp_servers"] = {"status": "ERROR", "error": str(e)[:100]}
        unhealthy.append("mcp_servers")

    # 2. Checkpoint DB
    try:
        db_path = get_data_dir() / "langgraph-checkpoints.db"
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.execute("SELECT 1")
            conn.close()
            health["components"]["checkpoint_db"] = {
                "status": "HEALTHY",
                "path": str(db_path),
                "size_kb": round(db_path.stat().st_size / 1024, 1),
            }
        else:
            health["components"]["checkpoint_db"] = {
                "status": "NOT_INITIALIZED",
                "path": str(db_path),
            }
    except Exception as e:
        health["components"]["checkpoint_db"] = {"status": "ERROR", "error": str(e)[:100]}
        unhealthy.append("checkpoint_db")

    # 3. Vector DB (Qdrant)
    try:
        vector_db_path = get_data_dir("vector_db")
        if vector_db_path.exists():
            health["components"]["vector_db"] = {
                "status": "INITIALIZED",
                "path": str(vector_db_path),
                "size_kb": round(
                    sum(f.stat().st_size for f in vector_db_path.rglob("*") if f.is_file()) / 1024, 1
                ),
            }
        else:
            health["components"]["vector_db"] = {"status": "NOT_INITIALIZED"}
    except Exception as e:
        health["components"]["vector_db"] = {"status": "ERROR", "error": str(e)[:100]}

    # Vector DB collection-level status is owned by the vector-db server itself.
    # It is deliberately not imported here: it lives in a separate repository and
    # vendors its own base/ and utils/ packages, so adding its directory to
    # sys.path would shadow this server's own modules.
    health["vector_db_healthy"] = (
        health["components"].get("vector_db", {}).get("status") == "INITIALIZED"
    )
    health["vector_db_note"] = (
        "Storage-directory check only. Call the vector-db server's own health "
        "tool for collection-level verification."
    )

    # 4. LLM Providers (async-style concurrent check)
    providers_status = {}
    import concurrent.futures
    import time as _time

    def _check_ollama():
        try:
            import requests
            url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            start = _time.time()
            r = requests.get(f"{url}/api/tags", timeout=3)
            latency = round((_time.time() - start) * 1000)
            if r.status_code == 200:
                models = r.json().get("models", [])
                result = {"status": "HEALTHY", "latency_ms": latency, "models": len(models)}
                # Quick Ollama inference test (5s timeout)
                try:
                    resp = requests.post(
                        f"{url}/api/generate",
                        json={"model": "qwen2.5:7b", "prompt": "test", "stream": False},
                        timeout=5,
                    )
                    result["ollama_inference"] = resp.status_code == 200
                except Exception:
                    result["ollama_inference"] = False
                return result
            return {"status": "ERROR", "code": r.status_code}
        except Exception as e:
            return {"status": "UNAVAILABLE", "error": str(e)[:80]}

    def _check_anthropic():
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return {"status": "NO_API_KEY"}
            return {"status": "CONFIGURED", "key_prefix": api_key[:8] + "..."}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)[:80]}

    def _check_claude_cli():
        try:
            import subprocess
            start = _time.time()
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True, text=True, timeout=5
            )
            latency = round((_time.time() - start) * 1000)
            if result.returncode == 0:
                return {"status": "HEALTHY", "latency_ms": latency,
                        "version": result.stdout.strip()[:50]}
            return {"status": "ERROR", "code": result.returncode}
        except FileNotFoundError:
            return {"status": "NOT_INSTALLED"}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)[:80]}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_check_ollama): "ollama",
            executor.submit(_check_anthropic): "anthropic",
            executor.submit(_check_claude_cli): "claude_cli",
        }
        for future in concurrent.futures.as_completed(futures, timeout=10):
            name = futures[future]
            try:
                providers_status[name] = future.result()
            except Exception as e:
                providers_status[name] = {"status": "TIMEOUT", "error": str(e)[:80]}

    health["components"]["llm_providers"] = providers_status

    any_llm_healthy = any(
        v.get("status") in ("HEALTHY", "CONFIGURED")
        for v in providers_status.values()
    )
    if not any_llm_healthy:
        unhealthy.append("llm_providers")

    # 5. Policy modules
    try:
        mod_result = json.loads(check_module_health())
        verified = mod_result.get("verified_ok", 0)
        total = mod_result.get("total_modules", 0)
        if total == 0:
            status = "NOT_FOUND"
        elif verified == total:
            status = "HEALTHY"
        else:
            status = "DEGRADED"
        health["components"]["policy_modules"] = {
            "status": status,
            "verified": verified,
            "total": total,
            "failed_parse": mod_result.get("failed_parse", []),
            "architecture_dir": mod_result.get("architecture_dir"),
        }
        if status != "HEALTHY":
            unhealthy.append("policy_modules")
    except Exception as e:
        health["components"]["policy_modules"] = {"status": "ERROR", "error": str(e)[:100]}
        unhealthy.append("policy_modules")

    # 6. Disk usage
    try:
        memory_dir = get_data_dir()
        if memory_dir.exists():
            total_size = sum(f.stat().st_size for f in memory_dir.rglob("*") if f.is_file())
            memory_dir_mb = round(total_size / (1024 * 1024), 2)
            health["components"]["disk"] = {
                "memory_dir_mb": memory_dir_mb,
                "path": str(memory_dir),
            }
            if memory_dir_mb > 500:
                unhealthy.append("disk_usage_high")
                health["disk_warning"] = f"Memory dir is {memory_dir_mb:.0f}MB (threshold: 500MB)"
    except Exception:
        pass

    # Overall status
    if unhealthy:
        health["overall"] = "DEGRADED"
        health["unhealthy_components"] = unhealthy

    health["success"] = True
    return health


if __name__ == "__main__":
    mcp.run(transport="stdio")
