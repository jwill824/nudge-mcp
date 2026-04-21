# Copilot Instructions

## Project Context

**nudge-mcp** is a FastMCP server that exposes Claude Code and GitHub Copilot CLI session token usage and cost data as MCP tools. It reads session data directly from local files — no external API calls, no database.

- **Runtime:** Python 3.12+, FastMCP, uv for dependency management
- **Entry point:** `server.py` — all `@mcp.tool` wrappers live here
- **Core logic:** `core/` — data loading, tool implementations, analysis engine
- **Data sources:**
  - Claude Code: `~/.claude/projects/**/*.jsonl` (per-session JSONL)
  - Copilot CLI: `~/.copilot/session-state/` (per-session JSONL events)
- **Config:** `~/.config/nudge/config.json` (subscription plan, budget, discount factor)
- **No CLI.** There is no CLI entry point — this is purely an MCP server.

---

## Architecture

### Module responsibilities

| Module | Purpose |
|--------|---------|
| `server.py` | MCP entry point — `@mcp.tool` wrappers only, no business logic |
| `core/loaders.py` | All data loading — parses JSONL, builds session dicts |
| `core/claude.py` | Claude tool implementations (session report, monthly summary, etc.) |
| `core/copilot.py` | Copilot tool implementations + GitHub API helpers |
| `core/analysis.py` | Copilot session analysis engine (bash overuse, batching, etc.) |
| `core/model_analysis.py` | Model efficiency scoring (over/under-powered turns) |
| `pricing.py` | `LIST_PRICES` table + `estimate_cost(tokens, model, discount)` |
| `config.py` | Config loader — reads `~/.config/nudge/config.json` |

### Key design decisions

1. **`@mcp.tool` wrappers import core handlers with `as _name` aliases** to avoid `F811` name collisions (ruff) between imported names and wrapper function names.
   ```python
   from core.copilot import copilot_session_report as _copilot_session_report
   @mcp.tool()
   async def copilot_session_report(...): return await _copilot_session_report(...)
   ```

2. **Per-model cost detection.** Claude JSONL entries carry `message.model` (e.g. `"claude-sonnet-4-6"`, `"claude-opus-4-6"`). `load_claude_sessions()` accumulates `tokens_by_model` per session and prices each model separately. Entries with `"<synthetic>"` model are skipped.

3. **Public function names only.** All exported functions in `core/` use public names (no `_` prefix). Private helpers (`_get_gh_token`, `_matches_tool`, etc.) retain the prefix. `reportPrivateUsage = false` in pyright allows `server.py` to import `_matches_tool` without warnings.

4. **Path constants are patchable.** `core/loaders.py` exposes `CLAUDE_PROJECTS_PATH` and `COPILOT_SESSIONS_PATH` as module-level constants so tests can monkeypatch them without touching the real filesystem.

5. **`discount_factor`** (default `0.5868`) represents the ratio of subscription value to API list price. It is calibrated monthly via `claude_calibrate_pricing`.

---

## Development Workflow

```bash
uv sync                                        # install all deps
uv run ruff check .                            # lint
uv run pyright                                 # type check
uv run --extra dev pytest tests/ -q            # run tests
uv run --extra dev pytest tests/test_claude.py # single file
```

### Adding a dependency

```bash
uv add <pkg>           # runtime dep → pyproject.toml [project.dependencies]
uv add --dev <pkg>     # dev dep → [dependency-groups].dev
```

Always commit both `pyproject.toml` and `uv.lock`.

### Adding a new MCP tool

1. Implement handler in `core/claude.py` or `core/copilot.py`
2. Import with alias in `server.py`: `from core.copilot import my_tool as _my_tool`
3. Add `@mcp.tool()` wrapper in `server.py`
4. Add tests — use `fake_claude_sessions` / `fake_copilot_sessions` fixtures from `conftest.py`
5. Document in `docs/README.md`

---

## Code Conventions

- **ruff** for linting (`E`, `F`, `W`; E501 ignored globally; E402 suppressed for `server.py` and `tests/conftest.py`)
- **pyright** `standard` mode for type checking; `reportPrivateUsage = false`
- `# type: ignore[<rule>]` inline suppressions in tests only — always include the rule name
- Line length: 120
- No comments on obvious code — only comment logic that needs clarification
- All tool handler functions return `str`
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`; all async tests run automatically

---

## Testing Fixtures

- `fake_claude_sessions` — 3 sessions: `proj-alpha` (Sonnet, Apr 2026), `proj-beta` (Opus, Apr 2026), `proj-gamma` (Sonnet, Mar 2026). Patches `core.loaders.CLAUDE_PROJECTS_PATH`.
- `fake_copilot_sessions` — 1 Copilot session (Apr 2026). Patches `core.loaders.COPILOT_SESSIONS_PATH`.
- `client` — async FastMCP test client for end-to-end tool testing.

