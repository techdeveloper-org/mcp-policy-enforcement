# mcp-policy-enforcement — Claude Project Context

**Type:** FastMCP Server
**Transport:** stdio
**Python:** 3.8+

---

## What This Server Does

Policy enforcement, compliance tracking, and comprehensive system health monitoring. Tracks enforcement status per session, logs tool usage, verifies compliance with required workflow steps, records flow traces, and provides multi-layer health checks (MCPs, databases, vector DB, LLM providers, disk).

---

## Entry Point

```
server.py
```

Run via `python server.py` — communicates over stdio using the MCP protocol.

---

## Available Tools

- `check_enforcement_status` — Check current policy enforcement status for session
- `enforce_policy_step` — Mark a policy step as enforced/completed
- `log_tool_usage` — Append a tool usage record to the session log
- `verify_compliance` — Verify all required policy steps are complete
- `list_policies` — List all policy definitions from policies/ directory
- `record_policy_execution` — Record policy execution result with timestamp
- `get_session_id` — Get or create a session ID for current context
- `get_flow_trace_summary` — Get execution flow trace summary for session
- `check_module_health` — Check health of a specific pipeline module
- `check_all_mcp_servers_health` — Health-check all registered MCP servers in parallel
- `check_system_health` — Full system health: MCPs + DB + vector DB + LLM + disk

---

## Shared Utilities (in this repo)

- `base/` — Shared MCP infrastructure package (response builder, decorators, persistence, clients)
- `mcp_errors.py` — Structured error response helpers
- `input_validator.py` — Null-byte strip, length limits, prompt injection detection
- `rate_limiter.py` — Token bucket rate limiter (enable via ENABLE_RATE_LIMITING=1)

---

## Environment Variables

- `CLAUDE_SESSION_DIR` — Session storage directory
- `CLAUDE_POLICIES_DIR` — Policies directory (default: policies/)
- `CLAUDE_MCP_CONFIG` — Path to MCP config JSON (default: ~/.claude/settings.json)

---

## Development

### Running locally

```bash
# Install deps
pip install -r requirements.txt

# Run the MCP server (stdio mode)
python server.py
```

### Testing a tool call manually

```python
import subprocess, json

proc = subprocess.Popen(
    ["python", "server.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
# Send MCP initialize + tool call via stdin
```

### File structure

```
mcp-policy-enforcement/
+-- server.py          # Main FastMCP server (entry point)
+-- base/              # Shared base package (response, decorators, persistence, clients)
+-- mcp_errors.py      # Error helpers
+-- input_validator.py # Input validation
+-- rate_limiter.py    # Rate limiting
+-- requirements.txt
+-- .gitignore
+-- README.md
+-- CLAUDE.md
```

---

## Key Rules

1. Do NOT edit `base/` directly — it is a copy from `mcp-base` repo
2. To update shared utilities, edit in `mcp-base` and re-copy
3. Keep `server.py` as the single entry point
4. All tool handlers must use `@mcp_tool_handler` decorator for consistent error handling
5. All responses must use `success()` / `error()` / `MCPResponse` builder from `base.response`

---

**Last Updated:** 2026-03-31
