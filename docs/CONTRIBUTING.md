# Contributing

## Project Structure

```
scrooge/
├── server.py            # MCP server — tool definitions and handlers
├── log.py               # Stop hook — parses JSONL, writes CSV row
├── scrooge              # CLI report — reads CSV, formats table
├── pricing.py           # Shared pricing config — discount factor, cost estimator
├── calibrate.py         # CLI calibration — derives discount factor from actual billing
├── config.py            # Config loader — ~/.config/scrooge/config.json
├── pyproject.toml       # Project metadata (requires Python ≥3.12)
├── tests/               # pytest test suite
└── lib/                 # Bundled dependencies (not committed; regenerate with uv)
```

**Data flow:**

```
Claude Code session ends
  → Stop hook (log.py)
      → reads session JSONL from ~/.claude/projects/
      → imports pricing.py for cost estimate
      → appends row to ~/.config/scrooge/sessions.csv

Claude asks about usage
  → MCP server (server.py)
      → session_report / monthly_summary  →  reads sessions.csv
      → monthly_summary (detailed)        →  also reads JSONL directly
      → calibrate_pricing                 →  calls calibrate.py subprocess
```

---

## Getting started

1. Clone the repository
2. Install dependencies: `uv pip install -e .`
3. Create a feature branch: `git checkout -b feat/your-feature`
4. Make your changes and commit following [Conventional Commits](https://www.conventionalcommits.org/)
5. Open a pull request against `main`

## Pull requests

- Squash merge only — one commit per PR
- PR title must follow Conventional Commits format
- Link to the relevant issue in the PR description

---

## Registering a New Tool for Impact Analysis

The `tool_impact` tool uses a lookup table in `server.py` to map short friendly names to full MCP tool name prefixes:

```python
_MCP_PREFIXES = {
    "serena":   "mcp__plugin_serena_serena__",
    "ck":       "mcp__ck-search__",
    "context7": "mcp__plugin_context7_context7__",
}
```

Add an entry here when a new MCP plugin is added to your Claude Code setup. For bash-invoked tools (like `ast-grep`, `rg`, `jq`), no registration is needed — they're matched as whole words inside bash commands automatically.

---

## Adding a New MCP Tool

1. **Define the tool** as a function decorated with `@mcp.tool` in `server.py`
2. **Implement** it as a plain function returning a string
3. **Test** it in `tests/test_server.py`
4. **Verify** with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector \
  /opt/homebrew/bin/uv \
  run --project ~/Developer/personal/scrooge python ~/Developer/personal/scrooge/server.py
```

---

## Updating Pricing

Edit `LIST_PRICES` in `pricing.py` to add new models or adjust prices. Recalibrate after each billing cycle:

```bash
uv run python calibrate.py <actual_billed>
```

---

## Dependencies

Dependencies are bundled in `lib/` (installed via `uv pip install -e .`). The `lib/` directory is excluded from git. To reinstall:

```bash
uv pip install -e .
```

Do not add runtime dependencies that aren't needed by all three entry points (`server.py`, `log.py`, `scrooge`). The Stop hook runs on every session end and must be fast and reliable.

---

## Python Version

Requires Python 3.13+ (uv-managed). Use `uv run` — it reads `pyproject.toml` and resolves the correct Python version automatically.
