# mcp-policy-enforcement

![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue)
![License MIT](https://img.shields.io/badge/License-MIT-green)
![Part of claude-workflow-engine](https://img.shields.io/badge/Part%20of-claude--workflow--engine-orange)

A Model Context Protocol (MCP) server that provides policy compliance checking, execution flow tracing, module health inspection, and full system health monitoring for the [claude-workflow-engine](https://github.com/techdeveloper-org/claude-workflow-engine) LangGraph orchestration pipeline. It exposes 11 tools and 2 resources over stdio JSON-RPC transport, enabling Claude Code to enforce pipeline coding standards, track every policy execution in a per-session flow-trace, verify step-by-step compliance across the 14-step execution pipeline, and surface real-time health diagnostics for all registered MCP servers and policy modules.

---

## Features

- **Policy step enforcement** — marks individual pipeline steps (0-13) as enforced and resolves the corresponding policy file path on disk
- **Compliance verification** — checks all 14 required pipeline steps against the enforcer state and reports missing steps
- **Flow-trace recording** — appends every policy execution (name, type, decision, duration, inputs, outputs) to a per-session `flow-trace.json` for audit and replay
- **Flow-trace summarization** — returns aggregate statistics (total policies, total duration, slowest/fastest policy) for any session
- **Tool usage logging** — writes a per-day JSONL log of every tool call made by Claude, including tool name, operation, and result status
- **Policy file discovery** — scans the `policies/` directory tree and returns all `.md` policy files with size, modification time, and level metadata
- **Module health checking** — inspects all 15 registered policy modules across pipeline levels 1-3 for existence and importability
- **MCP server health checking** — validates importability and file presence for all registered MCP server files without starting them
- **System health dashboard** — aggregates health across MCP servers, checkpoint SQLite DB, vector DB, LLM providers (Ollama, Anthropic API, Claude CLI), policy modules, and disk usage into a single call
- **Session ID resolution** — reads the active session ID from `.current-session.json` for use in flow-trace operations
- **Two MCP resources** — `enforcement://status` and `enforcement://compliance` expose enforcement state and compliance reports as named resources

---

## Tool Reference

| Tool | Description | Key Parameters |
|---|---|---|
| `check_enforcement_status` | Returns the current enforcement state for all pipeline steps from the persisted state file | None |
| `enforce_policy_step` | Marks a specific pipeline step as ENFORCED and resolves its policy file path | `step_number` (int, 0-13), `step_name` (str) |
| `log_tool_usage` | Appends a tool call record to a per-day JSONL log file | `tool_name` (str), `operation` (str), `parameters` (JSON str, optional), `result` (str, optional) |
| `verify_compliance` | Checks all 14 required steps and reports which are complete or missing; supports legacy key backward compatibility | None |
| `list_policies` | Scans the `policies/` directory and returns all `.md` policy files with level, size, and modification time | `level` (str: `all`, `01-sync`, `02-standards`, `03-execution`, `testing`) |
| `record_policy_execution` | Appends a full policy execution record to the session's `flow-trace.json` | `policy_name` (str), `policy_script` (str), `policy_type` (str), `decision` (str), `duration_ms` (int), `input_params` (JSON str), `output_results` (JSON str), `session_id` (str, optional), `sub_operations` (JSON str, optional) |
| `get_session_id` | Resolves and returns the current session ID from `.current-session.json` | None |
| `get_flow_trace_summary` | Returns aggregate statistics (total policies, total duration, slowest/fastest) for a session's flow-trace | `session_id` (str, optional — auto-detected if omitted) |
| `check_module_health` | Checks all 15 registered policy modules (levels 1-3) for file existence and Python importability | None |
| `check_all_mcp_servers_health` | Validates importability and file size for all registered MCP server files without starting them | None |
| `check_system_health` | Comprehensive health check across MCP servers, checkpoint DB, vector DB, LLM providers, policy modules, and disk usage | None |

### MCP Resources

| Resource URI | Description |
|---|---|
| `enforcement://status` | Current enforcement state (same payload as `check_enforcement_status`) |
| `enforcement://compliance` | Policy compliance report (same payload as `verify_compliance`) |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/techdeveloper-org/mcp-policy-enforcement.git
cd mcp-policy-enforcement
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` pins:

```
mcp>=1.0.0
fastmcp>=0.1.0
```

### 3. Register in Claude Code settings.json

Add the server to `~/.claude/settings.json` under the `mcpServers` key:

```json
{
  "mcpServers": {
    "policy-enforcement": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-policy-enforcement/server.py"],
      "env": {}
    }
  }
}
```

Replace `/absolute/path/to/mcp-policy-enforcement/` with the actual path on your machine.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | (unset) | Anthropic API key — checked by `check_system_health` to determine if the Anthropic provider is configured |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Base URL for the Ollama health probe in `check_system_health` |

No additional environment variables are required for core policy enforcement and flow-trace functionality.

### Policies Directory

The server resolves the `policies/` directory relative to the project root (three levels above `server.py`):

```
<project-root>/
  policies/
    00-auto-fix/
    01-sync-system/
    02-standards-system/
    03-execution-system/
    testing/
```

When used as part of claude-workflow-engine, this path resolves automatically. If you use this server standalone, place the `policies/` directory at the expected location or adjust `POLICIES_DIR` in `server.py`.

### State and Log Paths

All runtime state is written to the Claude memory directory (`~/.claude/memory/` by default):

| File / Directory | Purpose |
|---|---|
| `.blocking-enforcer-state.json` | Persisted enforcement state for all pipeline steps |
| `.tool-logs/tools-YYYY-MM-DD.jsonl` | Per-day tool usage log |
| `logs/sessions/<session-id>/flow-trace.json` | Per-session policy execution audit trail |

---

## Usage Examples

### Check whether all pipeline steps are compliant

```json
{
  "tool": "verify_compliance"
}
```

Example response:

```json
{
  "compliant": false,
  "completed_steps": 9,
  "total_steps": 14,
  "missing_steps": [
    "GitHub Branch/PR",
    "GitHub Issues",
    "Parallel Execution",
    "Failure Prevention",
    "Git Commit"
  ],
  "legacy_completed": [],
  "timestamp": "2026-04-14T10:22:31.445Z"
}
```

### Enforce a specific pipeline step

```json
{
  "tool": "enforce_policy_step",
  "arguments": {
    "step_number": 8,
    "step_name": "Progress Tracking"
  }
}
```

Example response:

```json
{
  "step": 8,
  "name": "Progress Tracking",
  "policy_file": "08-progress-tracking/task-progress-tracking-policy.md",
  "policy_exists": true,
  "policy_path": "/home/user/claude-workflow-engine/policies/03-execution-system/08-progress-tracking/task-progress-tracking-policy.md",
  "message": "Step 8 (Progress Tracking) enforced"
}
```

### Record a policy execution in the flow trace

```json
{
  "tool": "record_policy_execution",
  "arguments": {
    "policy_name": "prompt-generation-expert",
    "policy_script": "prompt-generator.py",
    "policy_type": "Policy Script",
    "decision": "Generated orchestration prompt with 4 agent phases",
    "duration_ms": 1340,
    "input_params": "{\"task\": \"add OAuth2 login\", \"complexity\": 14}",
    "output_results": "{\"prompt_length\": 3820, \"agents\": 4}"
  }
}
```

Example response:

```json
{
  "session_id": "SESSION-20260414-abc123",
  "policy": "prompt-generation-expert",
  "total_recorded": 7
}
```

### Run a full system health check

```json
{
  "tool": "check_system_health"
}
```

Example response (abbreviated):

```json
{
  "timestamp": "2026-04-14T10:25:00.000Z",
  "overall": "DEGRADED",
  "unhealthy_components": ["mcp_servers"],
  "components": {
    "mcp_servers": { "status": "DEGRADED", "healthy": 9, "total": 11 },
    "checkpoint_db": { "status": "HEALTHY", "size_kb": 128.4 },
    "vector_db": { "status": "INITIALIZED", "size_kb": 2048.0 },
    "llm_providers": {
      "anthropic": { "status": "CONFIGURED", "key_prefix": "sk-ant-..." },
      "claude_cli": { "status": "HEALTHY", "latency_ms": 210, "version": "1.x.x" },
      "ollama": { "status": "UNAVAILABLE" }
    },
    "policy_modules": { "status": "HEALTHY", "verified": 15, "total": 15 },
    "disk": { "memory_dir_mb": 12.4 }
  },
  "success": true
}
```

---

## Integration with Claude Workflow Engine

This server is one of 13 MCP servers that form the claude-workflow-engine ecosystem. It enforces coding standards and policy compliance at two points in the pipeline:

**Pre-execution (hook mode)**

The `PreToolUse` hook calls `check_enforcement_status` before every tool call to confirm that the required pipeline steps for the current session have been enforced. If a required step is not yet enforced, the hook can block the tool call or log a warning.

**Active execution steps**

During the 8-step active execution phase (Pre-0, Step 0, Steps 8-14), the pipeline calls `enforce_policy_step` as each step begins. The server writes the enforcement record to the persisted state file and resolves the corresponding policy document path so the orchestrator can inject it into the execution context.

**Flow-trace audit**

Every policy executed during a session is recorded via `record_policy_execution`. At the end of a session (or on demand), `get_flow_trace_summary` returns aggregate timing and decision statistics. This data feeds the claude-insight monitoring dashboard for session-level observability.

**Cross-server health monitoring**

`check_all_mcp_servers_health` and `check_system_health` give the orchestrator a single call to assess whether all peer MCP servers and supporting infrastructure are available before starting a long-running workflow.

### Pipeline Step to Policy File Mapping

| Step | Name | Policy File (under `03-execution-system/`) |
|---|---|---|
| 0 | Prompt Generation | `00-prompt-generation/prompt-generation-policy.md` |
| 1 | Task Breakdown | `01-task-breakdown/automatic-task-breakdown-policy.md` |
| 2 | Plan Mode Decision | `02-plan-mode/auto-plan-mode-suggestion-policy.md` |
| 3 | Code Graph Analysis | `00-code-graph-analysis/code-graph-analysis-policy.md` |
| 4 | Model Selection | `04-model-selection/intelligent-model-selection-policy.md` |
| 5 | Skill/Agent Selection | `05-skill-agent-selection/auto-skill-agent-selection-policy.md` |
| 6 | Tool Optimization | `06-tool-optimization/tool-usage-optimization-policy.md` |
| 7 | Context Reading | `00-context-reading/context-reading-policy.md` |
| 8 | Progress Tracking | `08-progress-tracking/task-progress-tracking-policy.md` |
| 9 | Git Commit | `09-git-commit/git-auto-commit-policy.md` |
| 10 | GitHub Branch/PR | `github-branch-pr-policy.md` |
| 11 | GitHub Issues | `github-issues-integration-policy.md` |
| 12 | Parallel Execution | `parallel-execution-policy.md` |
| 13 | Failure Prevention | `failure-prevention/failure-prevention-policy.md` |

---

## Project Structure

```
mcp-policy-enforcement/
  server.py            # FastMCP server -- 11 tools, 2 resources
  requirements.txt     # mcp>=1.0.0, fastmcp>=0.1.0
  input_validator.py   # Input sanitization (null-byte strip, length limit, injection detection)
  rate_limiter.py      # Token bucket rate limiter
  mcp_errors.py        # Structured MCP error types
  base/                # Shared mcp-base library (MCPResponse, mcp_tool_handler, AtomicJsonStore)
  CLAUDE.md            # Project context for Claude Code
```

---

## Contributing

1. Fork the repository and create a feature branch.
2. Make changes against `server.py` or the shared `base/` package.
3. Ensure all tools return valid JSON-serializable dicts.
4. Run existing tests if available, or add a test for any new tool.
5. Open a pull request with a clear description of what changed and why.

When adding a new tool:

- Decorate with both `@mcp.tool()` and `@mcp_tool_handler` (in that order).
- Return a plain `dict` — the `mcp_tool_handler` decorator handles JSON serialization and error wrapping.
- Add the tool to the docstring header at the top of `server.py` and update the tool reference table in this README.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Related Repositories

| Repo | Purpose |
|---|---|
| [claude-workflow-engine](https://github.com/techdeveloper-org/claude-workflow-engine) | Main LangGraph orchestration pipeline (hosts the `policies/` directory consumed by this server) |
| [mcp-base](https://github.com/techdeveloper-org/mcp-base) | Shared base package (`MCPResponse`, `AtomicJsonStore`, `mcp_tool_handler`) copied into `base/` |
| [mcp-session-mgr](https://github.com/techdeveloper-org/mcp-session-mgr) | Session lifecycle management — session IDs resolved by this server for flow-trace writes |
| [mcp-pre-tool-gate](https://github.com/techdeveloper-org/mcp-pre-tool-gate) | Pre-tool validation that calls this server to check enforcement status before each tool call |
| [mcp-standards-loader](https://github.com/techdeveloper-org/mcp-standards-loader) | Loads coding standards from `policies/02-standards-system/` at session start |
