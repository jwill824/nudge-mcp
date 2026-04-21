# Contributing

## Project Structure

```
nudge-mcp/
├── server.py            # MCP server — @mcp.tool wrappers and entry point
├── pricing.py           # Pricing tables and cost estimator
├── config.py            # Config loader — ~/.config/nudge/config.json
├── pyproject.toml       # Project metadata, dependencies, tool config
├── core/
│   ├── loaders.py       # Data loading — Claude JSONL + Copilot session-state
│   ├── claude.py        # Claude tool implementations
│   ├── copilot.py       # Copilot tool implementations + GitHub API helpers
│   └── analysis.py      # Copilot session analysis engine
├── tests/               # pytest test suite
│   ├── conftest.py      # Shared fixtures (fake session data)
│   ├── test_claude.py
│   ├── test_copilot.py
│   ├── test_loaders.py
│   └── test_analysis.py
└── docs/
    ├── README.md
    └── CONTRIBUTING.md
```

**Data flow:**

```
Claude Code session
  → ~/.claude/projects/{project}/{session}.jsonl  (written by Claude Code)
  → core/loaders.load_claude_sessions()           (parsed by nudge-mcp)
  → core/claude.*                                  (tool implementations)

Copilot CLI session
  → ~/.copilot/session-state/{session}/events.jsonl
  → core/loaders.load_copilot_sessions()
  → core/copilot.*
```

---

## Getting Started

### 1. Install uv

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
```

### 2. Clone and install

```bash
git clone https://github.com/jwill824/nudge-mcp
cd nudge-mcp
uv sync
```

`uv sync` installs all dependencies (including dev) from the lockfile into a `.venv`.

### 3. Register the MCP server locally

Create `.mcp.json` in the repo root (gitignored — each contributor creates their own):

```json
{
  "mcpServers": {
    "nudge-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "/path/to/nudge-mcp", "nudge-mcp"]
    }
  }
}
```

### 4. Run checks

```bash
uv run ruff check .          # lint
uv run pyright               # type check
uv run --extra dev pytest tests/ -q  # tests
```

---

## Working with uv

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and running scripts.

### Key commands

| Command | Purpose |
|---------|---------|
| `uv sync` | Install all deps from lockfile (creates `.venv`) |
| `uv run <cmd>` | Run a command in the project environment |
| `uv add <pkg>` | Add a runtime dependency |
| `uv add --dev <pkg>` | Add a dev-only dependency |
| `uv remove <pkg>` | Remove a dependency |
| `uv lock` | Regenerate `uv.lock` after manual edits to `pyproject.toml` |

### Adding a runtime dependency

```bash
uv add fastmcp
```

This updates `pyproject.toml` (`[project] dependencies`) and regenerates `uv.lock`. Commit both files.

### Adding a dev dependency

Dev dependencies (linters, type checkers, test frameworks) live in `[dependency-groups]` and are never bundled into the published package.

```bash
uv add --dev ruff
uv add --dev pyright
uv add --dev pytest
```

### Running tools without installing

```bash
uvx ruff check .       # run ruff from the registry without installing
uvx nudge-mcp          # run the published package directly (for testing)
uvx --from . nudge-mcp # run the local checkout as if published
```

### pyproject.toml structure

```toml
[project]
dependencies = ["fastmcp>=2.0.0"]   # runtime — bundled in the wheel

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]  # legacy optional group (used by CI --extra dev)

[dependency-groups]
dev = ["ruff>=0.15", "pyright>=1.1", "pytest>=9.0"]  # uv-native dev group
```

> **Note:** We have both `[project.optional-dependencies].dev` (for `uv run --extra dev`) and `[dependency-groups].dev` (for `uv sync`). New dev tools should go in `[dependency-groups].dev`.

---

## Linting and Type Checking

### ruff

ruff is our linter. Config lives in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W"]
ignore = ["E501"]                    # long lines are tolerated

[tool.ruff.lint.per-file-ignores]
"server.py" = ["E402"]              # sys.path setup before imports is intentional
"tests/conftest.py" = ["E402"]
```

Run:
```bash
uv run ruff check .          # check
uv run ruff check . --fix    # auto-fix what it can
```

ruff catches: unused imports, undefined names, style violations. It does **not** catch type errors.

### pyright

pyright is our type checker. Config lives in `pyproject.toml`:

```toml
[tool.pyright]
pythonVersion = "3.12"
typeCheckingMode = "standard"
reportPrivateUsage = false
```

Run:
```bash
uv run pyright
```

pyright catches: type mismatches, `None` subscript errors, missing attributes. ruff does not catch these.

> Use `# type: ignore[<rule>]` inline to suppress false positives in test files. Keep these minimal and always include the rule name.

---

## Testing

Tests live in `tests/` and use pytest with `pytest-asyncio` (all async tests run automatically).

```bash
uv run --extra dev pytest tests/ -q         # all tests
uv run --extra dev pytest tests/test_claude.py -q  # single file
uv run --extra dev pytest -k "test_model"   # filter by name
```

### Fixtures

Shared fixtures are in `tests/conftest.py`:

- **`fake_claude_sessions`** — creates 3 JSONL session files in a temp directory and patches `core.loaders.CLAUDE_PROJECTS_PATH`. Sessions: `proj-alpha` (Sonnet, Apr 1), `proj-beta` (Opus, Apr 2), `proj-gamma` (Sonnet, Mar 15).
- **`fake_copilot_sessions`** — creates a fake `~/.copilot/session-state/` with one April 2026 session.
- **`client`** — async FastMCP test client for end-to-end tool/resource testing.

### Patching core paths

Always patch the path constants rather than touching real user data:

```python
monkeypatch.setattr(core.loaders, "CLAUDE_PROJECTS_PATH", tmp_path)
monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)
```

---

## Adding a New MCP Tool

1. **Implement** the handler in `core/claude.py` or `core/copilot.py` as a plain function `my_tool(args: dict) -> str`
2. **Import** it in `server.py` with an alias: `from core.copilot import my_tool as _my_tool`
3. **Register** a `@mcp.tool` wrapper in `server.py` with typed parameters and a docstring
4. **Add tests** in `tests/test_claude.py` or `tests/test_copilot.py`
5. **Document** in `docs/README.md` under MCP Tools

The `_impl` alias pattern (`my_tool as _my_tool`) avoids name collisions between the imported handler and the `@mcp.tool` wrapper function.

---

## Updating Pricing

Edit `LIST_PRICES` in `pricing.py` to add new models or update prices. The model is detected per-turn from `message.model` in the Claude JSONL — no other changes needed for new models to be priced correctly.

---

## Packaging

Top-level modules are listed explicitly in `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
include = [
  "server.py",
  "config.py",
  "pricing.py",
  "core/**/*.py",
]
```

If you add a new top-level `.py` file, add it here. Subdirectory packages (`core/`) are included via glob.

---

## Pull Requests

- Create a feature branch: `git checkout -b feat/your-feature`
- Follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages
- All CI checks must pass: lint (ruff + pyright) and tests
- Squash merge only — one commit per PR
- PR title must follow Conventional Commits format

