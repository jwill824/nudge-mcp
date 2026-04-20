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

    Continuation messages are treated as having 0 prompt chars;
    the composite score is then capped at 1.
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
