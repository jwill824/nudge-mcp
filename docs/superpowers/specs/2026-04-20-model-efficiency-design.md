# Model Efficiency Analysis — Design Spec

**Date:** 2026-04-20
**Status:** Approved

## Problem

Every Copilot CLI session currently uses `claude-sonnet-4.6` regardless of task complexity. Many turns — short lookups, single-question confirmations, quick clarifications — don't need a premium model. There is no tooling to identify which sessions or turns are over-powered, estimate the cost waste, or guide model-switching decisions.

## Proposed Approach

Option C: a new `copilot_model_efficiency` MCP tool for cross-session summary, plus a new "Model Usage" section added to the existing `analyze_copilot_session` tool for per-turn detail. A shared `core/model_analysis.py` module provides the scoring engine used by both.

---

## Architecture

### New file: `core/model_analysis.py`

Shared engine with three public functions:

```
score_turn_complexity(events, interaction_id) → int    # 0–8 composite
classify_model_fit(complexity_score, model) → "over" | "fit" | "under"
estimate_savings(turns: list[dict], model_prices: dict) → dict
```

### New MCP tool: `copilot_model_efficiency`

Registered in `server.py`. Delegates to `core/copilot.py` → `_copilot_model_efficiency()`. Calls `model_analysis` functions on sessions loaded via `core/loaders.py`.

Parameters: `last: int = 10`, `month: Optional[str] = None`

### Extended tool: `analyze_copilot_session`

Existing implementation in `core/analysis.py` gains one new section: "Model Usage". Calls the same `model_analysis` functions. No changes to the tool's signature or other sections.

### Dependencies

- `core/loaders.py` — existing session and event loading (unchanged)
- `pricing.py` — existing model price table (unchanged)
- `server.py` — new `@mcp.tool` registration

---

## Complexity Scoring

Each turn is identified by `interactionId` (present on `user.message`, `assistant.message`, and `tool.execution_complete` events). Four signals are scored 0–2 each, summed to a composite 0–8:

| Signal | Score 0 | Score 1 | Score 2 | Source |
|---|---|---|---|---|
| Prompt length | < 50 chars | 50–300 | 300+ | `user.message` content |
| Output tokens | < 500 | 500–2 000 | 2 000+ | `assistant.message` outputTokens |
| Tool call count | 0 | 1–4 | 5+ | `tool.execution_start` count |
| Unique tools used | 0–1 | 2–3 | 4+ | distinct `toolName` values |

**Classification:**

| Score | Class | Interpretation |
|---|---|---|
| 0–2 | Simple | Quick lookups, confirmations, short answers |
| 3–5 | Moderate | Some tool use, medium output — either model acceptable |
| 6–8 | Complex | Multi-tool, heavy output — Sonnet warranted |

**Model fit verdict:**

- Sonnet/Opus + Simple → `over` (downgrade candidate)
- Haiku + Complex → `under` (quality risk flag)
- All other combinations → `fit`

---

## Cost Savings Estimation

For each `over`-powered turn:

```
savings += output_tokens × (sonnet_output_rate − haiku_output_rate) / 1_000_000
```

Using list prices: Sonnet $15/MTok output, Haiku $4/MTok output → **$11/MTok delta**.

Estimates are conservative: Copilot CLI exposes only output tokens, not input or cache tokens.

---

## Output Format

### `copilot_model_efficiency`

```
## Copilot Model Efficiency — Last N Sessions

Overall efficiency score:    62 / 100  ⚠️
Over-powered turns:          187 / 839  (22%)
Est. savings if right-sized: ~$1.47 this month

────────────────────────────────────────────────────────────────
Date        Project     Model       Turns  Simple  Score  Flag
────────────────────────────────────────────────────────────────
2026-04-19  scrooge     sonnet-4.6      7    6/7    28    ⚠️ over
...

### Downgrade Candidates
  ⚡ Apr 19 scrooge (7 turns): 6 simple turns on Sonnet — try Haiku (~$0.04 savings)

### Recommendations
  🔽 Switch to Haiku for sessions under 10 turns with no multi-tool work
  💡 Run analyze_copilot_session(<id>) for per-turn breakdown
```

**Efficiency score** = `100 × (1 − over_powered_turns / total_turns)`, capped 0–100.

### `analyze_copilot_session` — new "Model Usage" section

```
### Model Usage
  Model used:         claude-sonnet-4.6 (entire session)
  Over-powered turns: 6 / 7  (86%)  ████████████████████░░░
  Est. savings:       ~$0.04

  Turn  Prompt preview               Complexity  Score  Verdict
  ────  ───────────────────────────  ──────────  ─────  ───────
     1  "Forecast my Copilot budg…"  simple         1   ⚠️ over
     2  "So my current spend is $…"  moderate       4   ✅ fit
     ...
```

Prompt preview is truncated to 30 chars with `…`.

---

## Edge Cases

- **Events without `interactionId`** (older sessions): grouped into a fallback bucket, excluded from per-turn scoring, reported as "unattributed turns: N".
- **Single `session.model_change` at session start** (common case): model applied uniformly to all turns.
- **Mid-session model switch**: each turn inherits the model active at the time of its `user.message` timestamp.
- **Continuation messages** ("yes", "ok", "proceed", etc.): prompt_chars treated as 0; composite score capped at 1; never flagged as downgrade candidates.
- **Model name normalization**: event data uses dot notation (`claude-sonnet-4.6`) while `pricing.py` uses dash notation (`claude-sonnet-4-6`). `model_analysis.py` normalizes by replacing `.` with `-` before any pricing lookup.
- **No sessions found**: return a clear "No session data found" message, same pattern as other tools.

---

## Testing

### Unit tests (`tests/`)

- `score_turn_complexity` — fixture event sets covering all score boundaries (0, 2, 3, 5, 6, 8)
- `classify_model_fit` — all 9 combinations (3 complexity × 3 model tiers)
- `estimate_savings` — known token counts × known prices → expected dollar amounts
- Continuation message detection — assert score capped at 1 for "yes", "ok", etc.

### Integration tests

- Load a 2-session fixture → assert `copilot_model_efficiency` output contains score, savings line, and downgrade candidates section
- Load a single-session fixture → assert `analyze_copilot_session` output contains "### Model Usage" section with per-turn table

---

## Files Changed

| File | Change |
|---|---|
| `core/model_analysis.py` | **New** — scoring engine |
| `core/copilot.py` | Add `_copilot_model_efficiency()` |
| `core/analysis.py` | Add model usage section to `_analyze_session_events()` |
| `server.py` | Register new `copilot_model_efficiency` MCP tool |
| `tests/test_model_analysis.py` | **New** — unit + integration tests |
