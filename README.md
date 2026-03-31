# mcp-policy-enforcement

A FastMCP server providing **Policy Enforcement** capabilities for Claude Code workflows.

---

## Overview

Policy enforcement, compliance tracking, and comprehensive system health monitoring. Tracks enforcement status per session, logs tool usage, verifies compliance with required workflow steps, records flow traces, and provides multi-layer health checks (MCPs, databases, vector DB, LLM providers, disk).

---

## Tools

| Tool | Description |
|------|-------------|
| `check_enforcement_status` | Check current policy enforcement status for session |
| `enforce_policy_step` | Mark a policy step as enforced/completed |
| `log_tool_usage` | Append a tool usage record to the session log |
| `verify_compliance` | Verify all required policy steps are complete |
| `list_policies` | List all policy definitions from policies/ directory |
| `record_policy_execution` | Record policy execution result with timestamp |
| `get_session_id` | Get or create a session ID for current context |
| `get_flow_trace_summary` | Get execution flow trace summary for session |
| `check_module_health` | Check health of a specific pipeline module |
| `check_all_mcp_servers_health` | Health-check all registered MCP servers in parallel |
| `check_system_health` | Full system health: MCPs + DB + vector DB + LLM + disk |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/techdeveloper-org/mcp-policy-enforcement.git
cd mcp-policy-enforcement
```

### 2. Install dependencies

```bash
pip install mcp fastmcp
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

---

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CLAUDE_SESSION_DIR` | Session storage directory |
| `CLAUDE_POLICIES_DIR` | Policies directory (default: policies/) |
| `CLAUDE_MCP_CONFIG` | Path to MCP config JSON (default: ~/.claude/settings.json) |

---

## Usage in Claude Code

Add to your `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "policy-enforcement": {
      "command": "python",
      "args": [
        "/path/to/mcp-policy-enforcement/server.py"
      ],
      "env": {}
    }
  }
}
```

---

## Benefits

- Parallel MCP health checks complete in <2s (concurrent.futures ThreadPool)
- Flow trace gives full visibility into which pipeline steps ran
- Compliance gate prevents partial workflow executions from polluting state
- System health aggregates 5 subsystems into a single dashboard call

---

## Requirements

- Python 3.8+
- `mcp fastmcp`
- See `requirements.txt` for pinned versions

---

## Project Context

This MCP server is part of the **Claude Workflow Engine** ecosystem — a LangGraph-based
orchestration pipeline for automating Claude Code development workflows.

Related repos:
- [`claude-workflow-engine`](https://github.com/techdeveloper-org/claude-workflow-engine) — Main pipeline
- [`mcp-base`](https://github.com/techdeveloper-org/mcp-base) — Shared base utilities used by all MCP servers

---

## License

Private — techdeveloper-org
