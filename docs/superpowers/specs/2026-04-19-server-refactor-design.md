# Design: server.py Refactor into core/ Package

**Date:** 2026-04-19  
**Status:** Approved

## Problem

`server.py` has grown to 2,632 lines and `tests/test_server.py` to 1,511 lines. Both files mix several distinct responsibilities — data loading, session analysis, Claude tool implementations, Copilot tool implementations, GitHub API helpers, and MCP app registration — making them difficult to navigate and maintain.

## Approach

Extract logical sections from `server.py` into a `core/` subdirectory package. Keep `server.py` as the thin entry point responsible only for the FastMCP app instance, MCP resources, and `@mcp.tool` wrappers. Split `test_server.py` to match.

This is a **pure structural refactor** — no behavior changes, no signature changes, no new features.

## Module Structure

```
server.py               ← FastMCP app: constants, @mcp.resource, @mcp.tool wrappers (~300 lines)
core/
  __init__.py           ← re-exports of public API; readable table of contents
  loaders.py            ← CSV + Copilot session/event loading (~200 lines)
  analysis.py           ← _analyze_session_events, _format_session_analysis (~650 lines)
  claude.py             ← Claude tool implementations (~400 lines)
  copilot.py            ← Copilot tool implementations + GitHub API helpers (~1050 lines)
config.py               ← unchanged
pricing.py              ← unchanged
calibrate.py            ← unchanged
log.py                  ← unchanged
scrooge                 ← unchanged CLI script
```

## Dependency Graph

```
server.py
  ├── core.loaders       (load_csv, load_copilot_sessions, load_copilot_session_events, ...)
  ├── core.analysis      (_analyze_session_events, _format_session_analysis)
  ├── core.claude        (_session_report, _monthly_summary, _calibrate, _tool_impact)
  └── core.copilot       (_copilot_*, _get_gh_token, _copilot_premium_usage)
        ├── core.loaders  (shared)
        └── core.analysis (shared)

config.py, pricing.py   → imported by multiple modules; stay top-level
```

## Module Responsibilities

### `core/loaders.py`
Data access layer. Responsible for reading CSV session data and loading Copilot session-state from disk.

- `fmt(n)` — number formatter
- `load_csv()` — load Claude sessions from CSV
- `_default_copilot_model()` — detect active Copilot model
- `load_copilot_sessions()` — parse Copilot session-state directories
- `_find_active_session_id()` — detect in-use session via lock file
- `load_copilot_session_events(session_id)` — parse events.jsonl for a session

### `core/analysis.py`
Session health analysis engine. Responsible for analyzing Copilot CLI session events and formatting results.

- `_analyze_session_events(events, session_id)` — core analysis returning structured dict
- `_format_session_analysis(analysis, is_active)` — format analysis dict as human-readable string

### `core/claude.py`
Claude Code tool implementations. Contains the logic called by `@mcp.tool` wrappers for Claude-specific tools.

- `_session_report(args)` — recent sessions with cost/efficiency metrics
- `_monthly_summary(args)` — monthly token/cost breakdown
- `_calibrate(args)` — update discount factor from actual billing
- `_tool_impact(args)` — compare sessions by tool usage
- `_avg(rows, key)`, `_tok_per_turn(row)` — shared calculation helpers
- `_matches_tool(query, tool_name, tool_input)` — tool name matching
- `_scan_sessions_for_tool(query, csv_rows)` — scan sessions for tool usage

### `core/copilot.py`
Copilot CLI tool implementations and GitHub API helpers. Contains the logic for all Copilot-specific tools.

- `_copilot_tool_impact(args)` — Copilot sessions by tool usage
- `_copilot_session_report(args)` — recent Copilot sessions
- `_copilot_monthly_summary(args)` — monthly Copilot output token summary
- `_configure_subscription(args)` — update plan/budget config
- `_analyze_copilot_session(args)` — analyze a single session for inefficiencies
- `_copilot_behavior_report(args)` — cross-session pattern analysis
- `_copilot_budget_forecast(args)` — end-of-month spend projection
- `_record_copilot_spend(args)` — record actual overage spend
- `_get_gh_token()`, `_get_gh_username(token)` — GitHub API auth helpers
- `_copilot_premium_usage(args)` — fetch live premium request usage

### `core/__init__.py`
Re-exports all public functions called by `server.py`. Private helpers (leading `_`) remain internal. Serves as the table of contents for the package.

### `server.py` (after refactor)
Thin orchestration layer only:
- Path constants (`CSV_PATH`, `COPILOT_SESSIONS_PATH`, `COPILOT_CONFIG_PATH`)
- `_WORKFLOW_TOOLS` metadata dict
- `mcp = FastMCP("scrooge")`
- `@mcp.resource` definitions
- `@mcp.tool` wrapper functions (delegate to `core.*`)
- `if __name__ == "__main__": mcp.run()`

## Test Structure

```
tests/
  conftest.py       ← unchanged (shared fixtures)
  test_loaders.py   ← tests for core/loaders.py
  test_analysis.py  ← tests for core/analysis.py
  test_claude.py    ← tests for core/claude.py
  test_copilot.py   ← tests for core/copilot.py
```

`test_server.py` is deleted after all tests are redistributed. The existing section comments in `test_server.py` map naturally to the new files.

## Migration Steps

1. Create `core/` directory with empty `__init__.py`
2. Create `core/loaders.py` — extract loader functions from `server.py`
3. Create `core/analysis.py` — extract `_analyze_session_events` and `_format_session_analysis`
4. Create `core/claude.py` — extract Claude implementation functions
5. Create `core/copilot.py` — extract Copilot implementation functions + GitHub API helpers
6. Update `core/__init__.py` with re-exports
7. Update `server.py` to import from `core.*`
8. Check `scrooge` CLI script for any direct imports from `server.py`; update if needed
9. Split `test_server.py` into four new test files
10. Delete `test_server.py`
11. Run `uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q` to verify all tests pass

## Constraints

- No behavior changes of any kind
- All function signatures stay identical
- All MCP tool names and descriptions stay identical
- Tests must pass before and after the refactor
