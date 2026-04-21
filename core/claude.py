"""
Claude Code tool implementations.

Responsibilities:
  - Session report: recent sessions with cost and efficiency metrics
  - Monthly summary: token/cost breakdown by billing month
  - Calibrate pricing: update discount factor from actual billing
  - Tool impact: compare sessions that used a specific tool vs those that didn't
"""

import json
import re
from datetime import date
from glob import glob
from pathlib import Path

import config as _config
from pricing import LIST_PRICES, CLAUDE_PLANS

from core import loaders as _loaders


def _session_report(args: dict) -> str:
    sessions = _loaders.load_claude_sessions()
    if not sessions:
        return "No session data found. Ensure Claude Code sessions exist in ~/.claude/projects/."

    today_str = date.today().isoformat()
    month = args.get("month")
    today = args.get("today", False)
    last = args.get("last", 20)

    if today:
        sessions = [s for s in sessions if s["date"].startswith(today_str)]
    elif month:
        sessions = [s for s in sessions if s["date"].startswith(month)]

    if last and not today and not month:
        sessions = sessions[-last:]
    elif last:
        sessions = sessions[-last:]

    if not sessions:
        return "No sessions found for the given filter."

    lines = []
    header = (
        f"{'Date':<17} {'Project':<12} {'Cache%':>7} {'$/sess':>7} "
        f"{'Total':>8} {'Out':>7} {'Min':>5} {'Turns':>5} {'Tok/T':>7}"
    )
    div = "─" * len(header)
    lines.extend([div, header, div])

    total_cost = 0.0
    for s in sessions:
        cost = s["est_cost_usd"]
        total_cost += cost
        turns = s["turns"] or 1
        tok_per_turn = _loaders.fmt(s["total_tokens"] // turns)
        lines.append(
            f"{s['date']:<17} {s['project']:<12} "
            f"{s['cache_hit_pct']:>6}% ${cost:>6.4f} "
            f"{_loaders.fmt(s['total_tokens']):>8} {_loaders.fmt(s['output_tokens']):>7} "
            f"{s['duration_min']:>5} {s['turns']:>5} {tok_per_turn:>7}"
        )

    lines.append(div)

    if len(sessions) > 1:
        avg_cache = sum(s["cache_hit_pct"] for s in sessions) / len(sessions)
        avg_cost = total_cost / len(sessions)
        lines.append(
            f"\nSessions: {len(sessions)}  |  Total est.: ${total_cost:.4f}  |  "
            f"Avg/session: ${avg_cost:.4f}  |  Avg cache hit: {avg_cache:.1f}%"
        )

    try:
        df = _config.load().get("discount_factor", 0.5868)
        discount_note = f"~{(1-df)*100:.0f}% off API list price (calibrated)"
    except Exception:
        discount_note = "calibrated internal pricing"
    lines.append(f"\nCache hit >80% = excellent  |  Costs {discount_note}")
    return "\n".join(lines)


def _monthly_summary(args: dict) -> str:
    month = args.get("month") or date.today().strftime("%Y-%m")
    month_sessions = [s for s in _loaders.load_claude_sessions() if s["date"].startswith(month)]

    try:
        from pricing import estimate_cost
        cfg = _config.load()
        discount      = cfg.get("discount_factor", 0.5868)
        claude_budget = cfg.get("claude_monthly_budget", 400.0)
        claude_plan   = cfg.get("claude_plan", "claude_max_400")
        _PRICE_INPUT  = LIST_PRICES.get("claude-sonnet-4-6", {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75})

        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
        for jsonl in glob(str(Path.home() / ".claude/projects/**/*.jsonl"), recursive=True):
            with open(jsonl) as f:
                for line in f:
                    try:
                        d = json.loads(line.strip())
                        if not d.get("timestamp", "").startswith(month):
                            continue
                        usage = d.get("message", {}).get("usage", {})
                        if usage:
                            totals["input"]        += usage.get("input_tokens", 0)
                            totals["output"]       += usage.get("output_tokens", 0)
                            totals["cache_read"]   += usage.get("cache_read_input_tokens", 0)
                            totals["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                    except Exception:
                        pass

        cost = estimate_cost(totals, discount=discount)
        list_cost = sum(totals[k] * _PRICE_INPUT[k] for k in totals) / 1_000_000

        plan_label = CLAUDE_PLANS.get(claude_plan, {}).get("label", claude_plan)
        budget_lines = []
        if claude_budget > 0:
            remaining = claude_budget - cost
            pct_used = cost / claude_budget * 100
            filled = int(pct_used / 5)  # 20-char bar
            bar = "█" * filled + "░" * (20 - filled)
            budget_lines = [
                "",
                f"Plan:              {plan_label}",
                f"Monthly budget:    ${claude_budget:.2f}",
                f"Estimated spend:   ${cost:.2f}",
                f"Remaining:         ${remaining:.2f}  ({pct_used:.1f}% used)",
                f"                   [{bar}]",
            ]
        else:
            budget_lines = [
                "",
                f"Plan:              {plan_label}  (no monthly budget cap)",
                f"Estimated spend:   ${cost:.2f}",
            ]

        lines = [
            f"## Monthly Summary — {month}",
            "",
            "Token breakdown:",
            f"  Input:         {totals['input']:>15,}",
            f"  Output:        {totals['output']:>15,}",
            f"  Cache reads:   {totals['cache_read']:>15,}  ({_loaders.fmt(totals['cache_read'])})",
            f"  Cache creates: {totals['cache_create']:>15,}",
            "",
            f"At API list prices:                 ${list_cost:.2f}",
            f"Discount factor:                    {discount} ({(1-discount)*100:.1f}% off list)",
        ] + budget_lines + [
            "",
            f"Sessions tracked: {len(month_sessions)}",
            "",
            "Note: If this is the current month, compare to your Claude Code subscription",
            "usage counter. Cross-month sessions may cause ~$20-50 discrepancy.",
        ]
        return "\n".join(lines)

    except Exception:
        return f"## Monthly Summary — {month}\n\nSessions tracked: {len(month_sessions)}\nError reading token data."


def _calibrate(args: dict) -> str:
    actual = args["actual_billed"]
    month = args.get("month") or date.today().strftime("%Y-%m")

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    for jsonl_path in glob(str(Path.home() / ".claude/projects/**/*.jsonl"), recursive=True):
        with open(jsonl_path) as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    if not d.get("timestamp", "").startswith(month):
                        continue
                    usage = d.get("message", {}).get("usage", {})
                    if usage:
                        totals["input"]        += usage.get("input_tokens", 0)
                        totals["output"]       += usage.get("output_tokens", 0)
                        totals["cache_read"]   += usage.get("cache_read_input_tokens", 0)
                        totals["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                except Exception:
                    pass

    if all(v == 0 for v in totals.values()):
        return f"No token data found for {month}. Check that sessions exist for this period."

    LIST = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75}
    list_cost = sum(totals[k] * LIST[k] for k in totals) / 1_000_000
    if list_cost == 0:
        return "Error: list_cost is 0, cannot compute discount."

    factor = actual / list_cost

    cfg = _config.load()
    history = cfg.get("calibration_history", [])
    history.append({
        "month":         month,
        "actual":        round(actual, 2),
        "list_estimate": round(list_cost, 2),
        "factor":        round(factor, 4),
    })
    _config.update(discount_factor=round(factor, 4), calibration_history=history)

    plan_label = CLAUDE_PLANS.get(cfg.get("claude_plan", ""), {}).get("label", cfg.get("claude_plan", ""))
    budget = cfg.get("claude_monthly_budget", 0)
    return (
        f"Calibrated {month}: actual=${actual:.2f}  list_est=${list_cost:.4f}  "
        f"factor={factor:.4f} ({(1-factor)*100:.1f}% off list)\n"
        f"Plan: {plan_label}  |  Monthly budget: ${budget:.2f}"
    )


# Known MCP tool prefixes for friendly short names
_MCP_PREFIXES = {
    "serena":   "mcp__plugin_serena_serena__",
    "ck":       "mcp__ck-search__",
    "context7": "mcp__plugin_context7_context7__",
}

# Built-in Claude Code tool names (exact match)
_BUILTIN_TOOLS = {"read", "write", "edit", "grep", "glob", "bash", "agent", "webfetch", "websearch"}


def _matches_tool(query: str, tool_name: str, tool_input: dict) -> bool:
    """Return True if this tool_use block matches the user's query."""
    q = query.lower()
    name_lower = tool_name.lower()

    # Known MCP prefix shorthand — try prefix first, then fall through to substring
    if q in _MCP_PREFIXES:
        if name_lower.startswith(_MCP_PREFIXES[q]):
            return True

    # Exact built-in match (e.g. "Read", "Grep")
    if q in _BUILTIN_TOOLS and q == name_lower:
        return True

    # Substring match on the tool name (catches partial MCP names, custom tools,
    # and MCP tools whose prefix doesn't match the known shorthand)
    if q in name_lower:
        return True

    # Bash command content — match as a whole word to avoid false positives
    if name_lower == "bash":
        cmd = tool_input.get("command", "")
        if re.search(r"\b" + re.escape(q) + r"\b", cmd, re.IGNORECASE):
            return True

    return False


def _scan_sessions_for_tool(query: str, sessions: list[dict]) -> tuple[list[tuple], list[dict]]:
    """
    Split sessions into those that used the tool vs those that didn't.
    Uses the 'tools' field for fast matching, then scans JSONL for exact call counts.
    Returns (sessions_with, sessions_without).
    sessions_with: list of (session_dict, call_count)
    sessions_without: list of session_dict
    """
    sessions_with = []
    sessions_without = []

    # Deduplicate by session_id (keep last occurrence)
    seen_ids: dict[str, dict] = {}
    for s in sessions:
        seen_ids[s["session_id"]] = s
    deduped = list(seen_ids.values())

    for s in deduped:
        tool_names = s.get("tools", "").split()
        if not any(_matches_tool(query, t, {}) for t in tool_names):
            sessions_without.append(s)
            continue

        # Count exact calls by rescanning the JSONL file
        call_count = 0
        jsonl_path = s.get("jsonl_path", "")
        if jsonl_path:
            try:
                with open(jsonl_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("type") != "assistant":
                                continue
                            for block in entry.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use":
                                    if _matches_tool(query, block.get("name", ""), block.get("input", {})):
                                        call_count += 1
                        except Exception:
                            pass
            except Exception:
                pass

        sessions_with.append((s, max(call_count, 1)))

    return sessions_with, sessions_without


def _avg(rows: list[dict], key: str) -> float:
    vals = [float(r[key]) for r in rows if r.get(key) not in (None, "")]
    return sum(vals) / len(vals) if vals else 0.0


def _tok_per_turn(row: dict) -> float:
    turns = int(row.get("turns", 1)) or 1
    return int(row.get("total_tokens", 0)) / turns


def _tool_impact(args: dict) -> str:
    query = args.get("tool", "").strip()
    month = args.get("month")

    if not query:
        return "Please provide a tool name to analyze."

    sessions = _loaders.load_claude_sessions()
    if not sessions:
        return "No session data found."

    if month:
        sessions = [s for s in sessions if s["date"].startswith(month)]
        if not sessions:
            return f"No session data found for {month}."

    sessions_with, sessions_without = _scan_sessions_for_tool(query, sessions)

    if not sessions_with:
        period = f" in {month}" if month else ""
        return (
            f"No sessions found that used '{query}'{period}.\n\n"
            f"Tip: Try the tool name as it appears in tool calls — e.g. 'serena', 'ck', "
            f"'Read', 'Grep', 'Bash', or a substring of an MCP tool name."
        )

    with_rows = [r for r, _ in sessions_with]
    total_calls = sum(c for _, c in sessions_with)

    # Compute averages
    def avgs(rs):
        if not rs:
            return None
        tpt = [_tok_per_turn(r) for r in rs]
        return {
            "n":         len(rs),
            "cache_pct": _avg(rs, "cache_hit_pct"),
            "cost":      _avg(rs, "est_cost_usd"),
            "tok_turn":  sum(tpt) / len(tpt),
            "turns":     _avg(rs, "turns"),
            "duration":  _avg(rs, "duration_min"),
        }

    wa = avgs(with_rows)
    assert wa is not None  # guaranteed: sessions_with is non-empty (checked above)
    wo = avgs(sessions_without)

    MIN_SESSIONS_FOR_RELIABLE_DATA = 10
    low_sample_warning = (
        f"⚠️  Low sample size: only {len(sessions_with)} session(s) used '{query}' "
        f"(recommend ≥{MIN_SESSIONS_FOR_RELIABLE_DATA} for reliable results). "
        f"Use the tool consistently across more sessions and re-run this report."
        if len(sessions_with) < MIN_SESSIONS_FOR_RELIABLE_DATA else ""
    )

    lines = [
        f"## Tool Impact Analysis — '{query}'",
        "",
        f"Scanned {len(sessions_with) + len(sessions_without)} sessions"
        + (f" in {month}" if month else ""),
    ]
    if low_sample_warning:
        lines += ["", low_sample_warning]
    lines.append("")

    # Comparison table
    h_metric  = f"{'Metric':<22}"
    h_with    = f"{'With ' + query:>16}"
    h_without = f"{'Without ' + query:>16}"
    h_delta   = f"{'Delta':>12}"
    div = "─" * (len(h_metric) + len(h_with) + len(h_without) + len(h_delta) + 3)

    lines += [div, f"{h_metric} {h_with} {h_without} {h_delta}", div]

    def row_line(label, key_with, key_without, fmt_fn, lower_is_better=True):
        v_with = key_with
        v_wo   = key_without
        if v_wo and v_wo != 0:
            delta = ((v_with - v_wo) / v_wo) * 100
            arrow = "▼" if (delta < 0) == lower_is_better else "▲"
            delta_str = f"{arrow} {abs(delta):.1f}%"
        else:
            delta_str = "n/a"
        return f"{label:<22} {fmt_fn(v_with):>16} {fmt_fn(v_wo) if v_wo is not None else 'n/a':>16} {delta_str:>12}"

    if wo:
        lines.append(row_line("Sessions",     wa["n"],        wo["n"],        lambda v: str(int(v)),   lower_is_better=False))
        lines.append(row_line("Avg tokens/turn", wa["tok_turn"], wo["tok_turn"], lambda v: _loaders.fmt(int(v)),  lower_is_better=True))
        lines.append(row_line("Avg cache hit %", wa["cache_pct"], wo["cache_pct"], lambda v: f"{v:.1f}%", lower_is_better=False))
        lines.append(row_line("Avg cost/session", wa["cost"],   wo["cost"],     lambda v: f"${v:.4f}",  lower_is_better=True))
        lines.append(row_line("Avg turns",    wa["turns"],    wo["turns"],    lambda v: f"{v:.0f}",    lower_is_better=False))
        lines.append(row_line("Avg duration (min)", wa["duration"], wo["duration"], lambda v: f"{v:.1f}", lower_is_better=False))
    else:
        lines.append(f"{'Sessions':<22} {str(wa['n']):>16} {'0':>16} {'n/a':>12}")
        lines.append(f"{'Avg tokens/turn':<22} {_loaders.fmt(int(wa['tok_turn'])):>16} {'n/a':>16} {'n/a':>12}")
        lines.append(f"{'Avg cache hit %':<22} {str(round(wa['cache_pct'], 1)) + '%':>16} {'n/a':>16} {'n/a':>12}")
        lines.append(f"{'Avg cost/session':<22} {'$' + str(round(wa['cost'], 4)):>16} {'n/a':>16} {'n/a':>12}")

    lines += [div, ""]

    # Call frequency
    avg_calls = total_calls / len(sessions_with)
    lines.append(f"Total '{query}' calls across {len(sessions_with)} sessions: {total_calls:,}  (avg {avg_calls:.1f}/session)")
    lines.append("")

    # Top sessions by call count
    top = sorted(sessions_with, key=lambda x: x[1], reverse=True)[:5]
    lines.append(f"Top sessions by '{query}' call count:")
    lines.append(f"  {'Date':<17} {'Project':<12} {'Calls':>6}  {'Tok/T':>7}  {'Cache%':>7}  {'Cost':>8}")
    for r, calls in top:
        lines.append(
            f"  {r['date']:<17} {r['project']:<12} {calls:>6}  "
            f"{_loaders.fmt(int(_tok_per_turn(r))):>7}  {float(r['cache_hit_pct']):>6.1f}%  ${float(r['est_cost_usd']):>7.4f}"
        )

    lines.append("")
    lines.append("▼ = improvement (lower tokens/cost)  |  ▲ = regression  |  Delta = % change vs sessions without")

    return "\n".join(lines)
