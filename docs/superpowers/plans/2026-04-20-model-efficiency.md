# Model Efficiency Analysis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-turn model efficiency scoring to nudge-mcp: a new `core/model_analysis.py` engine, a new `copilot_model_efficiency` MCP tool, and a "Model Usage" section in `analyze_copilot_session`.

**Architecture:** A shared `core/model_analysis.py` module scores each turn's complexity (0–8), classifies the active model as over/fit/under, and estimates savings. `_analyze_session_events()` in `core/analysis.py` calls this engine and stores results in its return dict. A new `_copilot_model_efficiency()` in `core/copilot.py` does the same across multiple sessions. Both are exposed via `server.py`.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio (test runner: `uv run --with pytest --with pytest-asyncio python -m pytest`), FastMCP, existing `core/loaders.py` + `pricing.py` (no changes to either).

---

## File Map

| Action | File | What changes |
|---|---|---|
| Create | `core/model_analysis.py` | New scoring engine |
| Create | `tests/test_model_analysis.py` | Unit tests for the engine |
| Modify | `core/analysis.py` | Add model data to `_analyze_session_events` return dict; add `### Model Usage` section to `_format_session_analysis` |
| Modify | `tests/test_analysis.py` | Two new tests for model data in analysis output |
| Modify | `core/copilot.py` | New `_copilot_model_efficiency()` function |
| Modify | `tests/test_copilot.py` | Tests for new function |
| Modify | `server.py` | Register `copilot_model_efficiency` MCP tool |

---

## Task 1: Core scoring engine — `core/model_analysis.py`

**Files:**
- Create: `core/model_analysis.py`
- Test: `tests/test_model_analysis.py`

### Step 1.1 — Write failing tests

Create `tests/test_model_analysis.py` with the full test suite below. Every import will fail until Task 1.2.

```python
"""Tests for core/model_analysis.py."""

from core.model_analysis import (
    group_turns,
    score_turn_complexity,
    classify_model_fit,
    estimate_savings,
    analyze_session_model_usage,
)


def _evt(type_, data=None, timestamp="2026-04-01T10:00:00.000Z"):
    return {"type": type_, "data": data or {}, "timestamp": timestamp}


# ---------------------------------------------------------------------------
# group_turns
# ---------------------------------------------------------------------------

class TestGroupTurns:
    def test_empty_events(self):
        assert group_turns([]) == []

    def test_single_turn(self):
        events = [
            _evt("user.message", {"content": "hello world"}),
            _evt("assistant.message", {"outputTokens": 500}),
        ]
        turns = group_turns(events)
        assert len(turns) == 1
        assert turns[0]["prompt_content"] == "hello world"
        assert turns[0]["output_tokens"] == 500
        assert turns[0]["model"] == ""

    def test_multiple_turns(self):
        events = [
            _evt("user.message", {"content": "first"}),
            _evt("assistant.message", {"outputTokens": 100}),
            _evt("user.message", {"content": "second"}),
            _evt("assistant.message", {"outputTokens": 200}),
        ]
        turns = group_turns(events)
        assert len(turns) == 2
        assert turns[0]["prompt_content"] == "first"
        assert turns[0]["output_tokens"] == 100
        assert turns[1]["prompt_content"] == "second"
        assert turns[1]["output_tokens"] == 200

    def test_tool_calls_accumulate(self):
        events = [
            _evt("user.message", {"content": "do work"}),
            _evt("tool.execution_start", {"toolName": "bash"}),
            _evt("tool.execution_start", {"toolName": "view"}),
            _evt("tool.execution_start", {"toolName": "view"}),  # duplicate tool name
        ]
        turns = group_turns(events)
        assert turns[0]["tool_call_count"] == 3
        assert set(turns[0]["unique_tools"]) == {"bash", "view"}

    def test_model_change_before_first_turn(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "hello"}),
        ]
        turns = group_turns(events)
        assert turns[0]["model"] == "claude-sonnet-4.6"

    def test_model_change_between_turns(self):
        events = [
            _evt("user.message", {"content": "first"}),
            _evt("session.model_change", {"newModel": "claude-haiku-4.5"}),
            _evt("user.message", {"content": "second"}),
        ]
        turns = group_turns(events)
        assert turns[0]["model"] == ""
        assert turns[1]["model"] == "claude-haiku-4.5"

    def test_no_user_messages_returns_empty(self):
        events = [
            _evt("session.start", {}),
            _evt("assistant.message", {"outputTokens": 100}),
        ]
        assert group_turns(events) == []

    def test_default_model_applied_before_first_change(self):
        events = [
            _evt("user.message", {"content": "hi"}),
        ]
        turns = group_turns(events, default_model="claude-sonnet-4.6")
        assert turns[0]["model"] == "claude-sonnet-4.6"


# ---------------------------------------------------------------------------
# score_turn_complexity
# ---------------------------------------------------------------------------

class TestScoreTurnComplexity:
    def _turn(self, prompt="", output_tokens=0, tool_call_count=0, unique_tools=None):
        return {
            "prompt_content": prompt,
            "output_tokens": output_tokens,
            "tool_call_count": tool_call_count,
            "unique_tools": unique_tools or [],
        }

    def test_all_zeros(self):
        assert score_turn_complexity(self._turn()) == 0

    def test_short_prompt_scores_zero(self):
        # Under 50 chars
        assert score_turn_complexity(self._turn(prompt="short")) == 0

    def test_medium_prompt_scores_one(self):
        # 50–299 chars
        assert score_turn_complexity(self._turn(prompt="x" * 50)) == 1
        assert score_turn_complexity(self._turn(prompt="x" * 299)) == 1

    def test_long_prompt_scores_two(self):
        # 300+ chars
        assert score_turn_complexity(self._turn(prompt="x" * 300)) == 2

    def test_medium_output_tokens_scores_one(self):
        assert score_turn_complexity(self._turn(output_tokens=500)) == 1
        assert score_turn_complexity(self._turn(output_tokens=1999)) == 1

    def test_high_output_tokens_scores_two(self):
        assert score_turn_complexity(self._turn(output_tokens=2000)) == 2

    def test_single_tool_call_scores_one(self):
        assert score_turn_complexity(self._turn(tool_call_count=1)) == 1
        assert score_turn_complexity(self._turn(tool_call_count=4)) == 1

    def test_many_tool_calls_scores_two(self):
        assert score_turn_complexity(self._turn(tool_call_count=5)) == 2

    def test_two_to_three_unique_tools_scores_one(self):
        assert score_turn_complexity(self._turn(unique_tools=["bash", "view"])) == 1
        assert score_turn_complexity(self._turn(unique_tools=["bash", "view", "grep"])) == 1

    def test_four_plus_unique_tools_scores_two(self):
        assert score_turn_complexity(
            self._turn(unique_tools=["bash", "view", "grep", "glob"])
        ) == 2

    def test_max_score_is_eight(self):
        score = score_turn_complexity(self._turn(
            prompt="x" * 300, output_tokens=2000, tool_call_count=5,
            unique_tools=["bash", "view", "grep", "glob"],
        ))
        assert score == 8

    def test_continuation_capped_at_one_even_with_heavy_activity(self):
        # "yes" is a continuation — heavy output/tools should be capped at 1
        score = score_turn_complexity({
            "prompt_content": "yes",
            "output_tokens": 5000,
            "tool_call_count": 10,
            "unique_tools": ["bash", "view", "grep", "glob", "create"],
        })
        assert score == 1

    def test_continuation_with_zero_activity_scores_zero(self):
        # Short continuation with no output/tools → 0
        assert score_turn_complexity(self._turn(prompt="ok")) == 0

    def test_all_continuation_phrases_are_capped(self):
        continuations = ["yes", "no", "ok", "sure", "proceed", "continue",
                         "go ahead", "do it", "yes please", "sounds good",
                         "perfect", "great"]
        for phrase in continuations:
            score = score_turn_complexity({
                "prompt_content": phrase,
                "output_tokens": 5000,
                "tool_call_count": 10,
                "unique_tools": ["bash", "view", "grep", "glob"],
            })
            assert score <= 1, f"'{phrase}' scored {score}, expected <= 1"


# ---------------------------------------------------------------------------
# classify_model_fit
# ---------------------------------------------------------------------------

class TestClassifyModelFit:
    def test_sonnet_simple_is_over(self):
        assert classify_model_fit(0, "claude-sonnet-4.6") == "over"
        assert classify_model_fit(2, "claude-sonnet-4.6") == "over"

    def test_sonnet_moderate_is_fit(self):
        assert classify_model_fit(3, "claude-sonnet-4.6") == "fit"
        assert classify_model_fit(5, "claude-sonnet-4.6") == "fit"

    def test_sonnet_complex_is_fit(self):
        assert classify_model_fit(6, "claude-sonnet-4.6") == "fit"
        assert classify_model_fit(8, "claude-sonnet-4.6") == "fit"

    def test_opus_simple_is_over(self):
        assert classify_model_fit(0, "claude-opus-4.6") == "over"

    def test_haiku_simple_is_fit(self):
        assert classify_model_fit(0, "claude-haiku-4.5") == "fit"
        assert classify_model_fit(5, "claude-haiku-4.5") == "fit"

    def test_haiku_complex_is_under(self):
        assert classify_model_fit(6, "claude-haiku-4.5") == "under"
        assert classify_model_fit(8, "claude-haiku-4.5") == "under"

    def test_unknown_model_is_fit(self):
        assert classify_model_fit(0, "") == "fit"
        assert classify_model_fit(8, "some-unknown-model") == "fit"

    def test_model_name_with_dots_normalised(self):
        # Dots normalised to dashes internally — both forms must work
        assert classify_model_fit(0, "claude-sonnet-4.6") == "over"
        assert classify_model_fit(0, "claude-sonnet-4-6") == "over"


# ---------------------------------------------------------------------------
# estimate_savings
# ---------------------------------------------------------------------------

class TestEstimateSavings:
    def _turn(self, verdict, output_tokens=1000):
        return {"verdict": verdict, "output_tokens": output_tokens}

    def test_no_over_turns_gives_zero_savings(self):
        result = estimate_savings([self._turn("fit"), self._turn("under")])
        assert result["savings_usd"] == 0.0
        assert result["over_powered_turns"] == 0
        assert result["total_turns"] == 2

    def test_one_million_over_tokens_saves_eleven_dollars(self):
        # ($15 - $4) / 1M × 1_000_000 = $11.00
        result = estimate_savings([self._turn("over", 1_000_000)])
        assert abs(result["savings_usd"] - 11.0) < 0.01

    def test_mixed_turns_only_counts_over(self):
        turns = [self._turn("over", 1_000_000), self._turn("fit", 500_000)]
        result = estimate_savings(turns)
        assert result["over_powered_turns"] == 1
        assert result["total_turns"] == 2
        assert abs(result["savings_usd"] - 11.0) < 0.01

    def test_empty_turns(self):
        result = estimate_savings([])
        assert result["savings_usd"] == 0.0
        assert result["over_powered_turns"] == 0
        assert result["total_turns"] == 0


# ---------------------------------------------------------------------------
# analyze_session_model_usage
# ---------------------------------------------------------------------------

class TestAnalyzeSessionModelUsage:
    def test_adds_complexity_and_verdict_fields(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "hello"}),
            _evt("assistant.message", {"outputTokens": 100}),
        ]
        turns = analyze_session_model_usage(events)
        assert len(turns) == 1
        assert "complexity" in turns[0]
        assert "verdict" in turns[0]

    def test_simple_sonnet_turn_is_over(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "hi"}),
            _evt("assistant.message", {"outputTokens": 100}),
        ]
        turns = analyze_session_model_usage(events)
        assert turns[0]["verdict"] == "over"

    def test_complex_sonnet_turn_is_fit(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "x" * 300}),
            _evt("assistant.message", {"outputTokens": 3000}),
            _evt("tool.execution_start", {"toolName": "bash"}),
            _evt("tool.execution_start", {"toolName": "view"}),
            _evt("tool.execution_start", {"toolName": "grep"}),
            _evt("tool.execution_start", {"toolName": "glob"}),
            _evt("tool.execution_start", {"toolName": "edit"}),
        ]
        turns = analyze_session_model_usage(events)
        assert turns[0]["verdict"] == "fit"

    def test_no_model_change_uses_default(self):
        events = [
            _evt("user.message", {"content": "hi"}),
        ]
        turns = analyze_session_model_usage(events, default_model="claude-sonnet-4.6")
        assert turns[0]["model"] == "claude-sonnet-4.6"
        assert turns[0]["verdict"] == "over"
```

- [ ] **Step 1.1: Run the tests to confirm they all FAIL**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_model_analysis.py -v 2>&1 | head -30
```

Expected: `ImportError: No module named 'core.model_analysis'`

### Step 1.2 — Implement `core/model_analysis.py`

Create `core/model_analysis.py`:

```python
"""
Per-turn model efficiency scoring engine.

Responsibilities:
  - Group session events into per-turn dicts
  - Score each turn's complexity (0–8 composite)
  - Classify model fit: over / fit / under
  - Estimate output-token savings from right-sizing
"""

from __future__ import annotations

_CONTINUATION: set[str] = {
    "yes", "no", "ok", "sure", "proceed", "continue", "go ahead",
    "do it", "yes please", "sounds good", "perfect", "great",
}

_PREMIUM_KEYWORDS = ("sonnet", "opus")
_BUDGET_KEYWORDS  = ("haiku",)


def _normalize_model(model: str) -> str:
    """Normalise model name to lowercase-dashed form (e.g. claude-sonnet-4-6)."""
    return model.replace(".", "-").lower()


def group_turns(events: list[dict], default_model: str = "") -> list[dict]:
    """Group a session's events into per-turn dicts.

    Each turn starts at a user.message event. Accumulated fields per turn:
      prompt_content    — content of the user.message
      output_tokens     — sum of assistant.message outputTokens
      tool_call_count   — count of tool.execution_start events
      unique_tools      — sorted list of distinct toolName values
      model             — model active at turn start (updated by session.model_change)
      timestamp         — ISO timestamp of the user.message
    """
    current_model = default_model
    turns: list[dict] = []
    current: dict | None = None

    for e in events:
        etype = e.get("type", "")

        if etype == "session.model_change":
            current_model = e.get("data", {}).get("newModel", current_model)
            continue

        if etype == "user.message":
            if current is not None:
                current["unique_tools"] = sorted(current.pop("_unique_tools"))
                turns.append(current)
            current = {
                "prompt_content": e.get("data", {}).get("content", ""),
                "output_tokens": 0,
                "tool_call_count": 0,
                "_unique_tools": set(),
                "unique_tools": [],
                "model": current_model,
                "timestamp": e.get("timestamp", ""),
            }
        elif current is not None:
            if etype == "assistant.message":
                current["output_tokens"] += e.get("data", {}).get("outputTokens", 0)
            elif etype == "tool.execution_start":
                name = e.get("data", {}).get("toolName", "")
                if name:
                    current["tool_call_count"] += 1
                    current["_unique_tools"].add(name)

    if current is not None:
        current["unique_tools"] = sorted(current.pop("_unique_tools"))
        turns.append(current)

    return turns


def score_turn_complexity(turn: dict) -> int:
    """Return a complexity score 0–8 for a single turn.

    Signals (each 0–2, summed):
      - Prompt length (chars): <50=0, 50–299=1, 300+=2
      - Output tokens:         <500=0, 500–1999=1, 2000+=2
      - Tool call count:       0=0, 1–4=1, 5+=2
      - Unique tools:          0–1=0, 2–3=1, 4+=2

    Continuation messages (single-word/phrase acknowledgements) are treated as
    having 0 prompt chars; the composite score is then capped at 1.
    """
    prompt = turn.get("prompt_content", "")
    low = prompt.strip().lower()
    is_continuation = low in _CONTINUATION

    if is_continuation:
        prompt_signal = 0
    elif len(prompt) >= 300:
        prompt_signal = 2
    elif len(prompt) >= 50:
        prompt_signal = 1
    else:
        prompt_signal = 0

    output_tokens = turn.get("output_tokens", 0)
    if output_tokens >= 2000:
        output_signal = 2
    elif output_tokens >= 500:
        output_signal = 1
    else:
        output_signal = 0

    tool_count = turn.get("tool_call_count", 0)
    if tool_count >= 5:
        tool_signal = 2
    elif tool_count >= 1:
        tool_signal = 1
    else:
        tool_signal = 0

    unique_count = len(turn.get("unique_tools", []))
    if unique_count >= 4:
        unique_signal = 2
    elif unique_count >= 2:
        unique_signal = 1
    else:
        unique_signal = 0

    total = prompt_signal + output_signal + tool_signal + unique_signal
    return min(1, total) if is_continuation else total


def classify_model_fit(complexity: int, model: str) -> str:
    """Classify whether a model is over/under/fit for the given turn complexity.

    Returns:
      "over"  — premium model (sonnet/opus) on a simple turn (complexity <= 2)
      "under" — budget model (haiku) on a complex turn (complexity >= 6)
      "fit"   — model is appropriate for the complexity
    """
    norm = _normalize_model(model)
    is_premium = any(kw in norm for kw in _PREMIUM_KEYWORDS)
    is_budget  = any(kw in norm for kw in _BUDGET_KEYWORDS)

    if is_premium and complexity <= 2:
        return "over"
    if is_budget and complexity >= 6:
        return "under"
    return "fit"


def estimate_savings(turns: list[dict]) -> dict:
    """Estimate output-token savings from right-sizing over-powered turns.

    Conservative estimate using list prices (no discount), output tokens only.
    Savings = over_output_tokens × ($15.00 − $4.00) / 1_000_000

    Each turn dict must have 'verdict' and 'output_tokens' keys.
    Returns dict with keys: savings_usd, over_powered_turns, total_turns.
    """
    from pricing import LIST_PRICES

    sonnet_rate = LIST_PRICES["claude-sonnet-4-6"]["output"] / 1_000_000
    haiku_rate  = LIST_PRICES["claude-haiku-4-5"]["output"]  / 1_000_000
    delta = sonnet_rate - haiku_rate

    over_turns = [t for t in turns if t.get("verdict") == "over"]
    savings_usd = sum(t.get("output_tokens", 0) * delta for t in over_turns)

    return {
        "savings_usd":        round(savings_usd, 4),
        "over_powered_turns": len(over_turns),
        "total_turns":        len(turns),
    }


def analyze_session_model_usage(events: list[dict], default_model: str = "") -> list[dict]:
    """Score and classify every turn in a session.

    Calls group_turns, then for each turn adds:
      complexity  — int 0–8
      verdict     — "over" | "fit" | "under"

    Returns the enriched list of turn dicts.
    """
    turns = group_turns(events, default_model)
    for turn in turns:
        complexity = score_turn_complexity(turn)
        turn["complexity"] = complexity
        turn["verdict"]    = classify_model_fit(complexity, turn.get("model", ""))
    return turns
```

- [ ] **Step 1.3: Run the tests to confirm they all PASS**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_model_analysis.py -v
```

Expected: all tests PASS.

- [ ] **Step 1.4: Commit**

```bash
git add core/model_analysis.py tests/test_model_analysis.py
git commit -m "feat: add core/model_analysis.py scoring engine with full test suite

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Extend `_analyze_session_events` and `_format_session_analysis`

**Files:**
- Modify: `core/analysis.py` (return dict at line ~318, format function at line ~357)
- Modify: `tests/test_analysis.py`

### Step 2.1 — Write failing tests

Add these two tests to the bottom of `tests/test_analysis.py`:

```python
def test_analyze_session_events_includes_model_data():
    events = _make_events(["hello world"])
    result = _analyze_session_events(events)
    assert "model_turns" in result
    assert "model_over_count" in result
    assert "model_total_turns" in result
    assert "model_savings_usd" in result
    assert "model_efficiency_score" in result
    assert isinstance(result["model_turns"], list)
    assert isinstance(result["model_efficiency_score"], int)


def test_format_session_analysis_includes_model_usage_section():
    events = _make_events(["hello world"])
    analysis = _analyze_session_events(events)
    formatted = _format_session_analysis(analysis)
    assert "### Model Usage" in formatted
```

- [ ] **Step 2.1: Run the new tests to confirm they FAIL**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_analysis.py::test_analyze_session_events_includes_model_data tests/test_analysis.py::test_format_session_analysis_includes_model_usage_section -v
```

Expected: `KeyError` or `AssertionError` — keys not yet in return dict.

### Step 2.2 — Add model data to `_analyze_session_events`

In `core/analysis.py`, add this import near the top of the file (after the existing imports):

```python
from core.model_analysis import analyze_session_model_usage, estimate_savings as _estimate_savings
```

Then, in `_analyze_session_events`, find the `return {` block (around line 318) and add these five keys before the closing `}`:

```python
        # --- Model efficiency ---
        _session_model = ""
        for _e in events:
            if _e.get("type") == "session.model_change":
                _session_model = _e.get("data", {}).get("newModel", _session_model)
        _model_turns   = analyze_session_model_usage(events, _session_model)
        _model_savings = _estimate_savings(_model_turns)
        _model_total   = _model_savings["total_turns"]
        _model_over    = _model_savings["over_powered_turns"]
        _model_eff     = int(100 * (1 - _model_over / _model_total)) if _model_total else 100
```

And add to the return dict (just before the closing `}`):

```python
        "model_turns":           _model_turns,
        "model_over_count":      _model_over,
        "model_total_turns":     _model_total,
        "model_savings_usd":     _model_savings["savings_usd"],
        "model_efficiency_score": _model_eff,
```

The full change to the return block — locate the exact closing lines of the `return {` dict (ends with `"session_health_score": _session_health_score,`) and insert just before the `}`:

```python
        "model_turns":            _model_turns,
        "model_over_count":       _model_over,
        "model_total_turns":      _model_total,
        "model_savings_usd":      _model_savings["savings_usd"],
        "model_efficiency_score": _model_eff,
    }
```

The five `_model_*` local variables must be computed before the `return` statement. Insert them immediately before the `return {` line:

```python
    # --- Model efficiency ---
    _session_model = ""
    for _e in events:
        if _e.get("type") == "session.model_change":
            _session_model = _e.get("data", {}).get("newModel", _session_model)
    _model_turns   = analyze_session_model_usage(events, _session_model)
    _model_savings = _estimate_savings(_model_turns)
    _model_total   = _model_savings["total_turns"]
    _model_over    = _model_savings["over_powered_turns"]
    _model_eff     = int(100 * (1 - _model_over / _model_total)) if _model_total else 100

    return {
        ...existing keys...
        "model_turns":            _model_turns,
        "model_over_count":       _model_over,
        "model_total_turns":      _model_total,
        "model_savings_usd":      _model_savings["savings_usd"],
        "model_efficiency_score": _model_eff,
    }
```

### Step 2.3 — Add `### Model Usage` section to `_format_session_analysis`

In `core/analysis.py`, find the line `lines.append("")` just before `# Summary` (around line 678). Insert the new section immediately before it:

```python
    # 8. Model Usage
    lines.append("### Model Usage")
    _mt = analysis.get("model_total_turns", 0)
    _mo = analysis.get("model_over_count", 0)
    _eff = analysis.get("model_efficiency_score", 100)
    _sav = analysis.get("model_savings_usd", 0.0)
    if _mt:
        _eff_icon = "✅" if _eff >= 80 else "⚠️"
        lines.append(f"{_eff_icon}  Efficiency score: {_eff} / 100")
        lines.append(f"   Over-powered turns: {_mo} / {_mt}  ({100 * _mo // _mt}%)")
        if _sav > 0.001:
            lines.append(f"   Est. savings if right-sized: ~${_sav:.2f}")
        if _mo > 0:
            suggestions.append(
                f"Model over-powered for {_mo}/{_mt} turns — consider switching to Haiku "
                "for simple lookups, confirmations, and short Q&A turns."
            )
    else:
        lines.append("   No model turn data available.")
    lines.append("")
```

- [ ] **Step 2.4: Run the new tests to confirm they PASS**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_analysis.py::test_analyze_session_events_includes_model_data tests/test_analysis.py::test_format_session_analysis_includes_model_usage_section -v
```

Expected: both PASS.

- [ ] **Step 2.5: Run the full test suite to confirm no regressions**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -v
```

Expected: all previously passing tests still PASS.

- [ ] **Step 2.6: Commit**

```bash
git add core/analysis.py tests/test_analysis.py
git commit -m "feat: add Model Usage section to analyze_copilot_session output

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: New `_copilot_model_efficiency` cross-session tool

**Files:**
- Modify: `core/copilot.py` (add new function at end of file)
- Modify: `tests/test_copilot.py` (add new tests)
- Modify: `server.py` (register MCP tool)

### Step 3.1 — Write failing tests

Add these tests to `tests/test_copilot.py`:

```python
# ---------------------------------------------------------------------------
# copilot_model_efficiency
# ---------------------------------------------------------------------------

from core.copilot import _copilot_model_efficiency
import json


def test_copilot_model_efficiency_no_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)
    result = _copilot_model_efficiency({})
    assert "No Copilot" in result


def test_copilot_model_efficiency_returns_string(fake_copilot_sessions):
    result = _copilot_model_efficiency({})
    assert isinstance(result, str)
    assert len(result) > 0


def test_copilot_model_efficiency_contains_header(fake_copilot_sessions):
    result = _copilot_model_efficiency({})
    assert "Model Efficiency" in result


def test_copilot_model_efficiency_contains_efficiency_score(fake_copilot_sessions):
    result = _copilot_model_efficiency({})
    assert "/ 100" in result


def test_copilot_model_efficiency_month_filter_no_match(fake_copilot_sessions):
    result = _copilot_model_efficiency({"month": "1999-01"})
    assert "No" in result


async def test_copilot_model_efficiency_mcp_tool(client, fake_copilot_sessions):
    result = await client.call_tool("copilot_model_efficiency", {"last": 5})
    text = _result_text(result)
    assert isinstance(text, str)
```

- [ ] **Step 3.1: Run the new tests to confirm they FAIL**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_copilot.py::test_copilot_model_efficiency_no_sessions tests/test_copilot.py::test_copilot_model_efficiency_returns_string tests/test_copilot.py::test_copilot_model_efficiency_contains_header tests/test_copilot.py::test_copilot_model_efficiency_contains_efficiency_score tests/test_copilot.py::test_copilot_model_efficiency_month_filter_no_match tests/test_copilot.py::test_copilot_model_efficiency_mcp_tool -v
```

Expected: `ImportError` — `_copilot_model_efficiency` not yet defined.

### Step 3.2 — Implement `_copilot_model_efficiency` in `core/copilot.py`

Add this import at the top of `core/copilot.py` (with the existing imports):

```python
from core import model_analysis as _model_analysis
```

Add this function at the end of `core/copilot.py`:

```python
def _copilot_model_efficiency(args: dict) -> str:
    """Cross-session model efficiency report."""
    last  = int(args.get("last", 10))
    month = args.get("month")

    if not _loaders.COPILOT_SESSIONS_PATH.exists():
        return "No Copilot session data found at ~/.copilot/session-state/."

    # Gather session directories sorted by most-recently-modified
    candidates = sorted(
        (d for d in _loaders.COPILOT_SESSIONS_PATH.iterdir() if (d / "events.jsonl").exists()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    session_rows: list[dict] = []
    total_turns = 0
    total_over  = 0
    total_savings = 0.0

    for session_dir in candidates:
        if not month and len(session_rows) >= last:
            break

        events = _loaders.load_copilot_session_events(session_dir.name)
        if not events:
            continue

        # Get session date for month filter
        start_evt = next((e for e in events if e.get("type") == "session.start"), None)
        ts = (start_evt or {}).get("timestamp", "")
        date_str = ts[:10] if ts else ""

        if month and not date_str.startswith(month):
            continue

        # Derive default model from session.model_change events
        session_model = ""
        for e in events:
            if e.get("type") == "session.model_change":
                session_model = e.get("data", {}).get("newModel", session_model)

        analyzed = _model_analysis.analyze_session_model_usage(events, session_model)
        savings   = _model_analysis.estimate_savings(analyzed)

        n_turns   = savings["total_turns"]
        n_over    = savings["over_powered_turns"]
        n_simple  = sum(1 for t in analyzed if t["complexity"] <= 2)
        eff_score = int(100 * (1 - n_over / n_turns)) if n_turns else 100

        # Derive project name from session start cwd
        cwd = (start_evt or {}).get("data", {}).get("context", {}).get("cwd", "")
        project = (cwd.split("/")[-1] or session_dir.name[:8]) if cwd else session_dir.name[:8]

        flag = "⚠️ over" if n_over > 0 else "✅ fit"

        session_rows.append({
            "date":      date_str or session_dir.name[:10],
            "project":   project[:10],
            "model":     session_model[:18] or "unknown",
            "turns":     n_turns,
            "simple":    n_simple,
            "score":     eff_score,
            "flag":      flag,
            "savings":   savings["savings_usd"],
            "over":      n_over,
        })

        total_turns   += n_turns
        total_over    += n_over
        total_savings += savings["savings_usd"]

    if not session_rows:
        period = f" for {month}" if month else ""
        return f"No Copilot CLI sessions found{period}."

    overall_eff = int(100 * (1 - total_over / total_turns)) if total_turns else 100
    eff_icon = "✅" if overall_eff >= 80 else "⚠️"

    div = "─" * 78
    period_label = f" ({month})" if month else f" (last {len(session_rows)})"
    lines = [
        f"## Copilot Model Efficiency{period_label}",
        "",
        f"Overall efficiency score:    {overall_eff} / 100  {eff_icon}",
        f"Over-powered turns:          {total_over} / {total_turns}"
        + (f"  ({100 * total_over // total_turns}%)" if total_turns else ""),
        f"Est. savings if right-sized: ~${total_savings:.2f} (output tokens only)",
        "",
    ]

    header = (
        f"{'Date':<12} {'Project':<11} {'Model':<19} "
        f"{'Turns':>5} {'Simple':>6} {'Score':>5}  {'Flag'}"
    )
    lines += [div, header, div]

    for row in session_rows:
        lines.append(
            f"{row['date']:<12} {row['project']:<11} {row['model']:<19} "
            f"{row['turns']:>5} {row['simple']:>6} {row['score']:>5}  {row['flag']}"
        )

    lines.append(div)
    lines.append("")
    lines.append(
        "Score = 100 × (1 − over-powered turns / total turns). "
        "Simple = turns with complexity ≤ 2. "
        "Savings use list prices, output tokens only (conservative)."
    )

    return "\n".join(lines)
```

### Step 3.3 — Register the tool in `server.py`

Add the import to the existing import block in `server.py` (around line 46):

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
    _copilot_model_efficiency,          # ← add this line
)
```

Add the MCP tool registration after the last `@mcp.tool` block in `server.py` (after `copilot_budget_forecast`):

```python
@mcp.tool
def copilot_model_efficiency(
    last: int = 10,
    month: Optional[str] = None,
) -> str:
    """Analyse how well the active model matched task complexity across Copilot CLI sessions.

    Scores each turn 0–8 (prompt length, output tokens, tool count, unique tools),
    classifies each as over-powered / fit / under-powered, and estimates savings
    from switching simple turns to a budget model.

    Args:
        last: Number of most-recent sessions to analyse (default: 10)
        month: Filter to a specific month, e.g. '2026-04'
    """
    return _copilot_model_efficiency({"last": last, "month": month})
```

Also update the docstring at the top of `server.py` to include the new tool. Find the `Tools:` block and add:

```
  copilot_model_efficiency    — Per-turn model fit analysis: efficiency score, over-powered turns, savings estimate
```

- [ ] **Step 3.4: Run the new tests to confirm they PASS**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/test_copilot.py::test_copilot_model_efficiency_no_sessions tests/test_copilot.py::test_copilot_model_efficiency_returns_string tests/test_copilot.py::test_copilot_model_efficiency_contains_header tests/test_copilot.py::test_copilot_model_efficiency_contains_efficiency_score tests/test_copilot.py::test_copilot_model_efficiency_month_filter_no_match tests/test_copilot.py::test_copilot_model_efficiency_mcp_tool -v
```

Expected: all 6 PASS.

- [ ] **Step 3.5: Run the full test suite to confirm no regressions**

```bash
uv run --with pytest --with pytest-asyncio python -m pytest tests/ -v
```

Expected: all previously passing tests still PASS; new tests add to the count.

- [ ] **Step 3.6: Commit**

```bash
git add core/copilot.py server.py tests/test_copilot.py
git commit -m "feat: add copilot_model_efficiency MCP tool

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Done

At this point:
- `core/model_analysis.py` provides the scoring engine (tested in isolation)
- `analyze_copilot_session` includes a `### Model Usage` section
- `copilot_model_efficiency` is a live MCP tool reporting cross-session efficiency scores
- All existing tests still pass
- Three focused commits, each containing working, testable software
