# Server Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 2,632-line `server.py` and 1,511-line `test_server.py` into focused modules under a `core/` package, with matching test files, leaving `server.py` as a thin MCP entry point.

**Architecture:** Create `core/` containing `loaders.py`, `analysis.py`, `claude.py`, and `copilot.py`. Each module gets one clear responsibility. `server.py` shrinks to ~300 lines: constants, `mcp` instance, `@mcp.resource` handlers, and `@mcp.tool` wrappers that delegate to `core.*`. Tests split to match: `test_loaders.py`, `test_analysis.py`, `test_claude.py`, `test_copilot.py`.

**Tech Stack:** Python 3.13, FastMCP, pytest, pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `core/__init__.py` | Create | Package marker + re-exports |
| `core/loaders.py` | Create | Path constants, CSV/session/event loading |
| `core/analysis.py` | Create | Session event analysis engine + formatter |
| `core/claude.py` | Create | Claude tool implementations |
| `core/copilot.py` | Create | Copilot tool implementations + GitHub API |
| `server.py` | Modify | Thin entry point: mcp, resources, tool wrappers |
| `tests/conftest.py` | Modify | Move `client`/`fake_csv` fixtures here; fix CSV_PATH patch |
| `tests/test_loaders.py` | Create | Tests for `core/loaders.py` |
| `tests/test_analysis.py` | Create | Tests for `core/analysis.py` |
| `tests/test_claude.py` | Create | Tests for `core/claude.py` + MCP tool discovery |
| `tests/test_copilot.py` | Create | Tests for `core/copilot.py` |
| `tests/test_server.py` | Delete | Replaced by the four files above |

## Key Design Rules

1. **`core/copilot.py` accesses shared mutable state via module reference** — import loaders and analysis as `from core import loaders as _loaders`, `from core import analysis as _analysis`, then call `_loaders.COPILOT_SESSIONS_PATH`, `_loaders.load_copilot_sessions()`, `_analysis._analyze_session_events()`. This means tests only need to patch `core.loaders.*` / `core.analysis.*` once.
2. **`core/claude.py` imports functions directly** — `from core.loaders import load_csv`. Tests patch `core.claude.load_csv`.
3. **Monkeypatch translation table** (old `server.*` → new target):
   - `_srv.CSV_PATH` → `core.loaders.CSV_PATH`
   - `_srv.COPILOT_SESSIONS_PATH` → `core.loaders.COPILOT_SESSIONS_PATH`
   - `_srv.load_copilot_sessions` → `core.loaders.load_copilot_sessions`
   - `_srv.load_copilot_session_events` → `core.loaders.load_copilot_session_events`
   - `_srv.load_csv` → `core.claude.load_csv`
   - `_srv._analyze_session_events` → `core.analysis._analyze_session_events`
   - `patch("server.date", ...)` → `patch("core.copilot.date", ...)`

---

## Phase A — Extract Core Modules

### Task 1: Verify Baseline Tests Pass

**Files:**
- Read: `tests/test_server.py`

- [ ] **Step 1: Run the test suite**

```bash
cd /Users/jeff.williams/Developer/personal/scrooge
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass (green). Record the exact pass count — it must match at the end.

---

### Task 2: Create core/ Package Scaffold

**Files:**
- Create: `core/__init__.py`

- [ ] **Step 1: Create the `core/` directory and empty `__init__.py`**

```bash
mkdir -p /Users/jeff.williams/Developer/personal/scrooge/core
```

Create `core/__init__.py` with this content:

```python
"""
Scrooge core package.

Modules:
  loaders   — path constants, CSV/session/event loading
  analysis  — Copilot session analysis engine and formatter
  claude    — Claude Code tool implementations
  copilot   — Copilot CLI tool implementations + GitHub API helpers
"""
```

- [ ] **Step 2: Run tests to confirm scaffold doesn't break anything**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: same pass count as Task 1.

- [ ] **Step 3: Commit**

```bash
git add core/__init__.py
git commit -m "refactor: create core/ package scaffold"
```

---

### Task 3: Extract core/loaders.py

**Files:**
- Create: `core/loaders.py` (server.py lines 50–52 constants + lines 90–267)
- Modify: `server.py`

- [ ] **Step 1: Create `core/loaders.py`**

The file header and imports (copy exact content from server.py lines listed):

```python
"""
Data loading layer for Scrooge.

Responsibilities:
  - Path constants for data directories
  - Loading Claude session data from CSV
  - Loading Copilot CLI session-state from disk
  - Loading Copilot session events from JSONL
"""

import csv
import json
import subprocess
from collections import Counter
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Optional
```

After the imports, copy these sections verbatim from `server.py`:
- Lines 50–52: path constants (`CSV_PATH`, `COPILOT_SESSIONS_PATH`, `COPILOT_CONFIG_PATH`)
- Lines 90–267: all six functions (`fmt`, `load_csv`, `_default_copilot_model`, `load_copilot_sessions`, `_find_active_session_id`, `load_copilot_session_events`)

- [ ] **Step 2: Update `server.py` — replace the constant definitions and function definitions with imports**

Remove lines 50–52 and 90–267 from `server.py`. Add this import block after the existing stdlib imports (after line 44 `from typing import Literal, Optional`):

```python
from core.loaders import (
    CSV_PATH,
    COPILOT_SESSIONS_PATH,
    COPILOT_CONFIG_PATH,
    fmt,
    load_csv,
    load_copilot_sessions,
    _find_active_session_id,
    load_copilot_session_events,
)
```

- [ ] **Step 3: Run tests — they will fail on `fake_csv` fixture. Fix it now.**

The `fake_csv` fixture in `test_server.py` patches `_server.CSV_PATH`, but `load_csv()` now reads from `core.loaders.CSV_PATH`. Update the fixture in `tests/test_server.py`:

```python
# old (lines 57-67 in test_server.py)
@pytest.fixture
def fake_csv(tmp_path, monkeypatch):
    """Write sample CSV rows to a temp file and patch CSV_PATH."""
    csv_file = tmp_path / "sessions.csv"
    if SAMPLE_CSV_ROWS:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SAMPLE_CSV_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(SAMPLE_CSV_ROWS)
    monkeypatch.setattr(_server, "CSV_PATH", csv_file)
    return csv_file
```

Replace with:

```python
@pytest.fixture
def fake_csv(tmp_path, monkeypatch):
    """Write sample CSV rows to a temp file and patch CSV_PATH."""
    import core.loaders
    csv_file = tmp_path / "sessions.csv"
    if SAMPLE_CSV_ROWS:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SAMPLE_CSV_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(SAMPLE_CSV_ROWS)
    monkeypatch.setattr(core.loaders, "CSV_PATH", csv_file)
    return csv_file
```

Also update `test_tool_impact_no_disclaimer_when_sufficient` — it patches `_srv.load_csv`. Change:
```python
monkeypatch.setattr(_srv, "load_csv", lambda: rows)
```
to:
```python
import core.claude
monkeypatch.setattr(core.claude, "load_csv", lambda: rows)
```
(Note: `core.claude` doesn't exist yet; this will be wired up in Task 5. For now the test calls `_tool_impact` which is still in server.py and uses its local `load_csv`. So this patch can be left pointing at `_srv` until Task 5 when we move `_tool_impact`.)

Actually, leave this particular patch alone for now — it imports `_srv.load_csv` which is still a valid re-export from `server.py`. It will be updated in Task 5.

- [ ] **Step 4: Run tests**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/loaders.py server.py tests/test_server.py
git commit -m "refactor: extract core/loaders.py"
```

---

### Task 4: Extract core/analysis.py

**Files:**
- Create: `core/analysis.py` (server.py lines 54–85 `_WORKFLOW_TOOLS` + lines 270–911)
- Modify: `server.py`

- [ ] **Step 1: Create `core/analysis.py`**

File header and imports:

```python
"""
Copilot CLI session analysis engine.

Responsibilities:
  - Analyse session events for inefficiencies (vague prompts, batching, bash overuse, etc.)
  - Format analysis results as human-readable strings
"""

import re
from collections import Counter
from datetime import datetime
from typing import Optional
```

After the imports, copy from `server.py` verbatim:
- Lines 54–85: `_WORKFLOW_TOOLS` dict
- Lines 270–911: `_analyze_session_events` and `_format_session_analysis`

- [ ] **Step 2: Update `server.py` — replace `_WORKFLOW_TOOLS` and the two analysis functions with imports**

Remove lines 54–85 and 270–911 from `server.py`. Add to the import block:

```python
from core.analysis import (
    _WORKFLOW_TOOLS,
    _analyze_session_events,
    _format_session_analysis,
)
```

- [ ] **Step 3: Run tests**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass. (Tests still import `_analyze_session_events` from `server` which re-exports it.)

- [ ] **Step 4: Commit**

```bash
git add core/analysis.py server.py
git commit -m "refactor: extract core/analysis.py"
```

---

### Task 5: Extract core/claude.py

**Files:**
- Create: `core/claude.py` (server.py lines 1200–1605)
- Modify: `server.py`

- [ ] **Step 1: Create `core/claude.py`**

File header and imports:

```python
"""
Claude Code tool implementations.

Responsibilities:
  - Session report: recent sessions with cost and efficiency metrics
  - Monthly summary: token/cost breakdown by billing month
  - Calibrate pricing: update discount factor from actual billing
  - Tool impact: compare sessions that used a specific tool vs those that didn't
"""

import re
import subprocess
import calendar
from datetime import datetime
from pathlib import Path
from typing import Optional

import config as _config
from pricing import LIST_PRICES, CLAUDE_PLANS

from core.loaders import CSV_PATH, load_csv
```

After the imports, copy from `server.py` verbatim:
- Lines 1363–1371: `_MCP_PREFIXES` and `_BUILTIN_TOOLS` module-level constants
- Lines 1200–1605: all eight functions (`_session_report`, `_monthly_summary`, `_calibrate`, `_matches_tool`, `_scan_sessions_for_tool`, `_avg`, `_tok_per_turn`, `_tool_impact`)

Note: `_scan_sessions_for_tool` references `CSV_PATH` from `core.loaders`. Verify the call site uses the module-level `CSV_PATH` (not a local variable) — it should work since `from core.loaders import CSV_PATH` creates a binding in `core.claude`.

- [ ] **Step 2: Update `server.py` — replace Claude impl functions with imports**

Remove lines 1363–1371 and 1200–1605 from `server.py`. Add to the import block:

```python
from core.claude import (
    _session_report,
    _monthly_summary,
    _calibrate,
    _matches_tool,
    _tool_impact,
)
```

- [ ] **Step 3: Update monkeypatch in `test_server.py`**

Find `test_tool_impact_no_disclaimer_when_sufficient` (around line 339). Update the monkeypatch:

```python
# Before:
monkeypatch.setattr(_srv, "load_csv", lambda: rows)
result = _tool_impact({"tool": "Read"})

# After:
import core.claude
monkeypatch.setattr(core.claude, "load_csv", lambda: rows)
result = _tool_impact({"tool": "Read"})
```

Also update the import at the top of `test_server.py` to add `_tool_impact` from its new location (it's already imported via `from server import _tool_impact` which still works as server re-exports it — no change needed yet).

- [ ] **Step 4: Run tests**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/claude.py server.py tests/test_server.py
git commit -m "refactor: extract core/claude.py"
```

---

### Task 6: Extract core/copilot.py

**Files:**
- Create: `core/copilot.py` (server.py lines 1606–2628)
- Modify: `server.py`

- [ ] **Step 1: Create `core/copilot.py`**

File header and imports — critical: use module-reference pattern for shared mutable state:

```python
"""
Copilot CLI tool implementations and GitHub API helpers.

Responsibilities:
  - Copilot session report, monthly summary, tool impact
  - Subscription configuration
  - Session analysis and behavior reports
  - Budget forecasting
  - Spend recording
  - GitHub API: premium request usage
"""

import json
import re
import subprocess
import urllib.error
import urllib.request
import calendar
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import config as _config
from pricing import COPILOT_PLANS

# Use module references so tests can patch these in one place
from core import loaders as _loaders
from core import analysis as _analysis
```

After the imports, copy from `server.py` verbatim:
- Lines 1606–2628: all Copilot functions + GitHub API helpers

Then do a search-and-replace within the new file:
- Replace bare `COPILOT_SESSIONS_PATH` → `_loaders.COPILOT_SESSIONS_PATH`
- Replace bare `COPILOT_CONFIG_PATH` → `_loaders.COPILOT_CONFIG_PATH`
- Replace bare `load_copilot_sessions()` → `_loaders.load_copilot_sessions()`
- Replace bare `load_copilot_session_events(` → `_loaders.load_copilot_session_events(`
- Replace bare `_analyze_session_events(` → `_analysis._analyze_session_events(`
- Replace bare `fmt(` → `_loaders.fmt(`

Verify there are no remaining bare references to these symbols (run `grep -n "^[^_].*COPILOT_SESSIONS_PATH\|^[^_].*load_copilot_sessions\b" core/copilot.py` to check).

- [ ] **Step 2: Update `server.py` — replace Copilot impl functions with imports**

Remove lines 1606–2628 from `server.py`. Add to the import block:

```python
from core.copilot import (
    _copilot_tool_impact,
    _copilot_session_report,
    _copilot_monthly_summary,
    _configure_subscription,
    _analyze_copilot_session,
    _copilot_behavior_report,
    _copilot_budget_forecast,
    _record_copilot_spend,
    _copilot_premium_usage,
)
```

- [ ] **Step 3: Update monkeypatches in `test_server.py`**

This is the biggest patch update. Make the following substitutions throughout `test_server.py`:

**A. `COPILOT_SESSIONS_PATH` patches** — change all occurrences of:
```python
monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)
```
to:
```python
import core.loaders
monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)
```
(Affects: `test_copilot_behavior_report_low_sample_disclaimer`, `test_copilot_behavior_report_no_disclaimer_when_sufficient`, `test_copilot_tool_impact_no_matching_sessions`, `test_copilot_tool_impact_with_matching_session`, `test_copilot_tool_impact_low_sample_disclaimer_absent_when_sufficient`, `test_copilot_budget_forecast_shows_projection`, `test_copilot_budget_forecast_waste_section`, `test_copilot_budget_forecast_low_days_disclaimer`, `test_copilot_budget_forecast_no_disclaimer_after_7_days`)

**B. `load_copilot_sessions` patches** — change all occurrences of:
```python
monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [...])
```
to:
```python
import core.loaders
monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [...])
```
(Affects all `test_copilot_budget_forecast_*` and `test_copilot_tool_impact_*` tests)

**C. `_analyze_session_events` patch** in `test_copilot_budget_forecast_waste_section`:
```python
# Before:
monkeypatch.setattr(_srv, "_analyze_session_events", fake_analyze)

# After:
import core.analysis
monkeypatch.setattr(core.analysis, "_analyze_session_events", fake_analyze)
```

**D. `load_copilot_session_events` patch** in `test_copilot_budget_forecast_waste_section`:
```python
# Before:
monkeypatch.setattr(_srv, "load_copilot_session_events", lambda p: [{"type": "session.start"}])

# After:
import core.loaders
monkeypatch.setattr(core.loaders, "load_copilot_session_events", lambda p: [{"type": "session.start"}])
```

**E. `date` patch** — change the two budget forecast tests that use `patch("server.date", ...)`:
```python
# Before:
with patch("server.date", FakeDate):

# After:
with patch("core.copilot.date", FakeDate):
```
(Affects: `test_copilot_budget_forecast_low_days_disclaimer`, `test_copilot_budget_forecast_no_disclaimer_after_7_days`)

**F. `_srv._config.CONFIG_PATH` patches** — these patch the `config` module itself (not server). They already work correctly and need no change.

- [ ] **Step 4: Run tests**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/copilot.py server.py tests/test_server.py
git commit -m "refactor: extract core/copilot.py"
```

---

### Task 7: Update core/__init__.py with Re-exports

**Files:**
- Modify: `core/__init__.py`

- [ ] **Step 1: Update `core/__init__.py`** to document the public API:

```python
"""
Scrooge core package.

Modules:
  loaders   — path constants, CSV/session/event loading
  analysis  — Copilot session analysis engine and formatter
  claude    — Claude Code tool implementations
  copilot   — Copilot CLI tool implementations + GitHub API helpers

Public API (called by server.py):
"""

from core.loaders import (
    CSV_PATH,
    COPILOT_SESSIONS_PATH,
    COPILOT_CONFIG_PATH,
    fmt,
    load_csv,
    load_copilot_sessions,
    _find_active_session_id,
    load_copilot_session_events,
)
from core.analysis import (
    _WORKFLOW_TOOLS,
    _analyze_session_events,
    _format_session_analysis,
)
from core.claude import (
    _session_report,
    _monthly_summary,
    _calibrate,
    _matches_tool,
    _tool_impact,
)
from core.copilot import (
    _copilot_tool_impact,
    _copilot_session_report,
    _copilot_monthly_summary,
    _configure_subscription,
    _analyze_copilot_session,
    _copilot_behavior_report,
    _copilot_budget_forecast,
    _record_copilot_spend,
    _copilot_premium_usage,
)

__all__ = [
    "CSV_PATH", "COPILOT_SESSIONS_PATH", "COPILOT_CONFIG_PATH",
    "fmt", "load_csv", "load_copilot_sessions", "_find_active_session_id",
    "load_copilot_session_events",
    "_WORKFLOW_TOOLS", "_analyze_session_events", "_format_session_analysis",
    "_session_report", "_monthly_summary", "_calibrate", "_matches_tool", "_tool_impact",
    "_copilot_tool_impact", "_copilot_session_report", "_copilot_monthly_summary",
    "_configure_subscription", "_analyze_copilot_session", "_copilot_behavior_report",
    "_copilot_budget_forecast", "_record_copilot_spend", "_copilot_premium_usage",
]
```

- [ ] **Step 2: Run tests**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add core/__init__.py
git commit -m "refactor: populate core/__init__.py with re-exports"
```

---

## Phase B — Split the Test File

### Task 8: Update conftest.py and Create test_loaders.py

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_loaders.py`

- [ ] **Step 1: Move shared fixtures and helpers to `tests/conftest.py`**

Add the following to `tests/conftest.py` (move from `test_server.py`). The existing conftest only has path setup:

```python
import csv
import json
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
import core.loaders

from server import mcp


def _result_text(result) -> str:
    return result.data if hasattr(result, "data") else str(result)


SAMPLE_CSV_ROWS = [
    {
        "date": "2026-04-01 10:00", "session_id": "aaa00001", "project": "proj-alpha",
        "input_tokens": "10000", "output_tokens": "2000", "cache_read_tokens": "8000",
        "cache_creation_tokens": "1000", "total_tokens": "21000",
        "cache_hit_pct": "80.0", "est_cost_usd": "0.0120",
        "duration_min": "15.0", "turns": "10", "tools": "Read Grep",
    },
    {
        "date": "2026-04-02 11:00", "session_id": "bbb00002", "project": "proj-beta",
        "input_tokens": "5000", "output_tokens": "1000", "cache_read_tokens": "1000",
        "cache_creation_tokens": "500", "total_tokens": "7500",
        "cache_hit_pct": "20.0", "est_cost_usd": "0.0080",
        "duration_min": "8.0", "turns": "5", "tools": "Bash",
    },
    {
        "date": "2026-03-15 09:00", "session_id": "ccc00003", "project": "proj-gamma",
        "input_tokens": "3000", "output_tokens": "500", "cache_read_tokens": "2000",
        "cache_creation_tokens": "200", "total_tokens": "5700",
        "cache_hit_pct": "66.7", "est_cost_usd": "0.0040",
        "duration_min": "5.0", "turns": "3", "tools": "Read",
    },
]


@pytest.fixture
def fake_csv(tmp_path, monkeypatch):
    """Write sample CSV rows to a temp file and patch CSV_PATH in core.loaders."""
    csv_file = tmp_path / "sessions.csv"
    if SAMPLE_CSV_ROWS:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SAMPLE_CSV_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(SAMPLE_CSV_ROWS)
    monkeypatch.setattr(core.loaders, "CSV_PATH", csv_file)
    return csv_file


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c
```

- [ ] **Step 2: Create `tests/test_loaders.py`**

Copy only the loader-related tests from `test_server.py`:

```python
"""Tests for core/loaders.py."""

from core.loaders import (
    load_copilot_sessions,
    load_copilot_session_events,
    _find_active_session_id,
)


# ---------------------------------------------------------------------------
# load_copilot_sessions() unit tests
# ---------------------------------------------------------------------------

def test_load_copilot_sessions_returns_list():
    sessions = load_copilot_sessions()
    assert isinstance(sessions, list)


def test_load_copilot_sessions_fields():
    sessions = load_copilot_sessions()
    if sessions:
        s = sessions[0]
        assert "date" in s
        assert "output_tokens" in s
        assert "turns" in s
        assert "model" in s
        assert "duration_min" in s


def test_load_copilot_sessions_output_tokens_nonzero():
    """After the event parsing fix, at least some sessions should have output tokens."""
    sessions = load_copilot_sessions()
    if sessions:
        total = sum(s["output_tokens"] for s in sessions)
        assert total > 0, "Expected non-zero output tokens after event parsing fix"


# ---------------------------------------------------------------------------
# _find_active_session_id() unit test
# ---------------------------------------------------------------------------

def test_find_active_session_id_returns_string_or_none():
    result = _find_active_session_id()
    assert result is None or isinstance(result, str)
```

- [ ] **Step 3: Run new test file**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_loaders.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Run full suite**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass (count is now higher — new tests are additive, test_server.py still runs).

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_loaders.py
git commit -m "refactor: add test_loaders.py, update conftest.py with shared fixtures"
```

---

### Task 9: Create tests/test_analysis.py

**Files:**
- Create: `tests/test_analysis.py`

- [ ] **Step 1: Create `tests/test_analysis.py`**

```python
"""Tests for core/analysis.py."""

import json
import pytest
from datetime import datetime, timezone as _tz

from core.analysis import _analyze_session_events, _format_session_analysis


# ---------------------------------------------------------------------------
# Test helper: build synthetic event lists
# ---------------------------------------------------------------------------

def _make_events(
    user_prompts=None,
    tool_calls_per_turn=None,
    store_memory=False,
    bash_commands=None,
):
    """Build a minimal synthetic events.jsonl list for testing."""
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=_tz.utc)

    events = [
        {
            "type": "session.start",
            "data": {"sessionId": "aaaa-bbbb", "context": {"cwd": "/home/user/myproject"}},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        }
    ]

    for i, prompt in enumerate(user_prompts or ["hello world"]):
        events.append({
            "type": "user.message",
            "data": {"content": prompt},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })
        tool_names = (tool_calls_per_turn or [[]])[i] if tool_calls_per_turn and i < len(tool_calls_per_turn) else []
        tool_requests = [
            {"toolCallId": f"tc{i}{j}", "name": t, "arguments": {}, "type": "function"}
            for j, t in enumerate(tool_names)
        ]
        events.append({
            "type": "assistant.message",
            "data": {"messageId": f"m{i}", "content": "", "toolRequests": tool_requests, "outputTokens": 500},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })
        for j, t in enumerate(tool_names):
            events.append({
                "type": "tool.execution_start",
                "data": {"toolCallId": f"tc{i}{j}", "toolName": t, "arguments": {}},
                "timestamp": now.isoformat().replace("+00:00", "Z"),
            })

    if store_memory:
        events.append({
            "type": "tool.execution_start",
            "data": {"toolCallId": "mem1", "toolName": "store_memory", "arguments": {"fact": "x"}},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })

    for cmd in (bash_commands or []):
        events.append({
            "type": "tool.execution_start",
            "data": {"toolCallId": "b1", "toolName": "bash", "arguments": {"command": cmd}},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })

    return events
```

After the helper, copy all tests from `test_server.py` lines 551–895 verbatim (all `test_analyze_session_events_*` and `test_format_session_analysis_*` functions). These tests already call `_analyze_session_events` and `_format_session_analysis` directly — no monkeypatch changes are needed for these tests.

- [ ] **Step 2: Run new test file**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_analysis.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_analysis.py
git commit -m "refactor: add test_analysis.py"
```

---

### Task 10: Create tests/test_claude.py

**Files:**
- Create: `tests/test_claude.py`

- [ ] **Step 1: Create `tests/test_claude.py`**

```python
"""
Tests for core/claude.py and MCP server tool/resource discovery.

Uses the `client` and `fake_csv` fixtures from conftest.py.
"""

import pytest
from conftest import _result_text, SAMPLE_CSV_ROWS

import core.claude
from core.loaders import fmt
from core.claude import _matches_tool, _avg, _tok_per_turn, _tool_impact
```

After the imports, copy the following test groups verbatim from `test_server.py`, in order:
- Lines 76–90: `test_fmt` parametrized test
- Lines 93–127: all `test_matches_tool_*` tests
- Lines 129–155: `test_avg_*` and `test_tok_per_turn_*` tests
- Lines 157–220: `test_list_tools_*` and `test_list_resources_*` and `test_*_resource_is_valid_json` tests (MCP integration tests using `client` fixture)
- Lines 223–286: all `test_session_report_*` tests
- Lines 288–307: all `test_monthly_summary_*` tests
- Lines 310–357: all `test_tool_impact_*` tests

For `test_tool_impact_no_disclaimer_when_sufficient` (around line 339), update the monkeypatch to use `core.claude`:
```python
def test_tool_impact_no_disclaimer_when_sufficient(monkeypatch):
    rows = [
        {
            "date": f"2026-04-{i+1:02d} 10:00", "session_id": f"s{i:07d}",
            "project": "proj", "input_tokens": "5000", "output_tokens": "1000",
            "cache_read_tokens": "3000", "cache_creation_tokens": "500",
            "total_tokens": "9500", "turns": "5", "duration_min": "10",
            "est_cost_usd": "0.05", "cache_hit_pct": "60.0",
            "tool_calls": "Read,Read", "model": "claude-sonnet-4.6",
        }
        for i in range(10)
    ]
    monkeypatch.setattr(core.claude, "load_csv", lambda: rows)
    result = _tool_impact({"tool": "Read"})
    assert "⚠️  Low sample size" not in result
```

- [ ] **Step 2: Run new test file**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_claude.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_claude.py
git commit -m "refactor: add test_claude.py"
```

---

### Task 11: Create tests/test_copilot.py

**Files:**
- Create: `tests/test_copilot.py`

- [ ] **Step 1: Create `tests/test_copilot.py`**

```python
"""
Tests for core/copilot.py.

Uses the `client` fixture from conftest.py.
"""

import json
import pytest
from datetime import date
from unittest.mock import patch
from pathlib import Path

import core.loaders
import core.analysis
import core.copilot
import config as _cfg
from conftest import _result_text

from core.copilot import (
    _copilot_tool_impact,
    _copilot_behavior_report,
    _record_copilot_spend,
    _copilot_budget_forecast,
    _copilot_premium_usage,
)
```

After the imports, copy from `test_server.py` verbatim, making the monkeypatch substitutions listed in the Key Design Rules at the top of this plan:

- Lines 358–392: `test_copilot_session_report_*` (integration tests, use `client`)
- Lines 423–437: `test_copilot_monthly_summary_*`
- Lines 440–489: `test_configure_subscription_*`
- Lines 907–930: `test_analyze_copilot_session_*`
- Lines 933–994: `test_copilot_behavior_report_*`
- Line 996: `test_configure_subscription_plan_and_budget_override`
- Lines 1008–1140: `test_copilot_premium_usage_*`
- Lines 1142–1196: `test_record_copilot_spend_*`
- Lines 1199–1403: `test_copilot_budget_forecast_*`
- Lines 1406–1511: `test_copilot_tool_impact_*`

**All monkeypatch substitutions to make while copying:**

| Old pattern | New pattern |
|-------------|-------------|
| `import server as _srv` (local) | `import core.loaders` / `import core.copilot` as needed |
| `monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", x)` | `monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", x)` |
| `monkeypatch.setattr(_srv, "load_copilot_sessions", fn)` | `monkeypatch.setattr(core.loaders, "load_copilot_sessions", fn)` |
| `monkeypatch.setattr(_srv, "load_copilot_session_events", fn)` | `monkeypatch.setattr(core.loaders, "load_copilot_session_events", fn)` |
| `monkeypatch.setattr(_srv, "_analyze_session_events", fn)` | `monkeypatch.setattr(core.analysis, "_analyze_session_events", fn)` |
| `monkeypatch.setattr(_srv._config, "CONFIG_PATH", x)` | `monkeypatch.setattr(_cfg, "CONFIG_PATH", x)` (same — `_srv._config` IS `config`) |
| `with patch("server.date", FakeDate):` | `with patch("core.copilot.date", FakeDate):` |

For `test_copilot_premium_usage_success` and `test_copilot_premium_usage_overage_budget`, `import server as _srv` is used only for `_srv._config`. Replace with `import config as _cfg` and use `_cfg` directly.

- [ ] **Step 2: Run new test file**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_copilot.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_copilot.py
git commit -m "refactor: add test_copilot.py"
```

---

### Task 12: Delete test_server.py and Verify

**Files:**
- Delete: `tests/test_server.py`

- [ ] **Step 1: Delete `tests/test_server.py`**

```bash
rm tests/test_server.py
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -v
```

Expected: all tests pass. Count should be **identical** to the original count from Task 1 (every test has been migrated — no tests lost, no tests added except the loaders file which is net-new).

If any tests are missing, compare against the original test file sections and add them to the appropriate new file.

- [ ] **Step 3: Remove now-unused re-exports from `server.py`**

Now that `test_server.py` is deleted, nothing imports from `server` except through `mcp` (the integration tests) and `from server import mcp` in conftest. Remove the re-export imports from `server.py` that are no longer needed — keep only what `server.py` actually uses internally.

Specifically, remove from `server.py`'s import block:
- `fmt` (not used in server.py directly)
- `_find_active_session_id` (not used in server.py directly)
- `_WORKFLOW_TOOLS` (not used in server.py after analysis extraction)
- `_analyze_session_events`, `_format_session_analysis` (called only from core.copilot)
- `_matches_tool` (called only from core.claude)

Keep in `server.py` imports (because `@mcp.tool` wrappers call these):
- `load_copilot_sessions` — if any `@mcp.tool` wrapper uses it directly
- `_session_report`, `_monthly_summary`, `_calibrate`, `_tool_impact`
- `_copilot_tool_impact`, `_copilot_session_report`, `_copilot_monthly_summary`
- `_configure_subscription`, `_analyze_copilot_session`, `_copilot_behavior_report`
- `_copilot_budget_forecast`, `_record_copilot_spend`, `_copilot_premium_usage`

To determine exactly which imports server.py needs, scan lines 977–1199 (the `@mcp.tool` wrapper bodies) for function calls, and keep only those imports.

- [ ] **Step 4: Run tests again to confirm cleanup didn't break anything**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "refactor: split server.py into core/ package, split tests to match

- core/loaders.py: path constants, CSV + Copilot session/event loading
- core/analysis.py: session event analysis engine and formatter
- core/claude.py: Claude Code tool implementations
- core/copilot.py: Copilot CLI tool implementations + GitHub API helpers
- server.py: now ~300 lines, thin MCP entry point only
- tests split into test_loaders, test_analysis, test_claude, test_copilot
- conftest.py: shared client/fake_csv fixtures moved here

No behavior changes. All tests pass.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Verification Checklist

Before declaring this complete:

- [ ] `server.py` is ≤ 350 lines
- [ ] `core/loaders.py` ≤ 280 lines
- [ ] `core/analysis.py` ≤ 700 lines
- [ ] `core/claude.py` ≤ 450 lines
- [ ] `core/copilot.py` ≤ 1100 lines
- [ ] `tests/test_server.py` does not exist
- [ ] All four test files exist and import from `core.*`
- [ ] `uv run --with pytest --with pytest-asyncio python -m pytest tests/ -q` passes with the same count as Task 1
- [ ] `uv run python server.py` starts without error (MCP server runs)
