#!/usr/bin/env python3
"""
MCP server: Scrooge — Claude Code and GitHub Copilot CLI cost tracker.

Exposes session token usage and cost data as MCP tools so Claude can
query its own usage mid-conversation.

Tools:
  claude_session_report       — Recent Claude Code sessions with cost and efficiency metrics
  claude_monthly_summary      — Total cost and token breakdown for a Claude Code billing month
  claude_calibrate_pricing    — Update the discount factor from actual billing
  claude_tool_impact          — Compare efficiency metrics for sessions that used a specific tool vs those that didn't
  copilot_tool_impact         — Same as tool_impact but for Copilot CLI sessions (output tokens/turn + est. cost)
  copilot_session_report      — Recent Copilot CLI sessions with output token metrics
  copilot_monthly_summary     — Monthly output token summary for Copilot CLI
  configure_subscription      — Update active Claude or Copilot plan and monthly budget
  analyze_copilot_session     — Analyze a session for inefficient prompts, poor tool batching, bash overuse, missing memory
  copilot_behavior_report     — Cross-session pattern analysis: recurring inefficiencies and actionable recommendations
  copilot_premium_usage       — Fetch live premium request usage from the GitHub API for the current month
  record_copilot_spend        — Manually record actual Copilot overage spend for a month
  copilot_budget_forecast     — Forecast end-of-month spend and show waste savings from fixing inefficiencies
"""

import sys
import os

# Add the lib directory to the path (uv-installed dependencies)
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "lib"))
# Add this directory for pricing.py
sys.path.insert(0, _HERE)

import csv
import json
import re
import subprocess
import urllib.error
import urllib.request
import calendar
from datetime import date, datetime, timedelta, timezone
from glob import glob
from pathlib import Path
from typing import Literal, Optional

from fastmcp import FastMCP

import config as _config

from core.loaders import (
    CSV_PATH,
    COPILOT_SESSIONS_PATH,
    fmt,
    load_csv,
    load_copilot_sessions,
    _find_active_session_id,
    load_copilot_session_events,
)

from core.analysis import (
    _analyze_session_events,
    _format_session_analysis,
)

mcp = FastMCP("scrooge")

# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource(
    "scrooge://config",
    name="Scrooge Config",
    description=(
        "Current configuration: active subscription plans, monthly budgets, "
        "discount factor, and calibration history. "
        "Read this before checking monthly spend or adjusting budgets."
    ),
    mime_type="application/json",
)
def config_resource() -> str:
    from pricing import CLAUDE_PLANS, COPILOT_PLANS
    cfg = _config.load()
    enriched = {
        **cfg,
        "claude_plan_label":  CLAUDE_PLANS.get(cfg.get("claude_plan", ""), {}).get("label", ""),
        "copilot_plan_label": COPILOT_PLANS.get(cfg.get("copilot_plan", ""), {}).get("label", ""),
        "available_claude_plans":  {k: v["label"] for k, v in CLAUDE_PLANS.items()},
        "available_copilot_plans": {k: v["label"] for k, v in COPILOT_PLANS.items()},
        "_env_overrides_supported": {
            "MCP_DISCOUNT_FACTOR":  "float — override discount_factor",
            "MCP_CLAUDE_BUDGET":    "float — override claude_monthly_budget",
            "MCP_COPILOT_BUDGET":   "float — override copilot_monthly_budget",
            "MCP_CLAUDE_PLAN":      "str   — override claude_plan",
            "MCP_COPILOT_PLAN":     "str   — override copilot_plan",
        },
        "_config_path": str(_config.CONFIG_PATH),
    }
    return json.dumps(enriched, indent=2)


@mcp.resource(
    "scrooge://pricing",
    name="API Pricing Tables",
    description=(
        "Anthropic API list prices per model and available subscription plan definitions. "
        "Useful for understanding cost calculations."
    ),
    mime_type="application/json",
)
def pricing_resource() -> str:
    from pricing import LIST_PRICES, CLAUDE_PLANS, COPILOT_PLANS
    cfg = _config.load()
    discount = cfg.get("discount_factor", 0.5868)
    discounted = {
        model: {k: round(v * discount / 1_000_000, 10) for k, v in prices.items()}
        for model, prices in LIST_PRICES.items()
    }
    return json.dumps({
        "list_prices_per_mtok":       LIST_PRICES,
        "effective_prices_per_token": discounted,
        "discount_factor":            discount,
        "claude_plans":               CLAUDE_PLANS,
        "copilot_plans":              COPILOT_PLANS,
    }, indent=2)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool
def claude_session_report(
    last: int = 20,
    today: bool = False,
    month: Optional[str] = None,
) -> str:
    """Show Claude Code session token usage and cost estimates.

    Filter by last N sessions, today, a specific month (YYYY-MM), or all time.
    Returns per-session metrics: cache hit %, estimated cost, tokens/turn, duration.

    Args:
        last: Show last N sessions (default: 20)
        today: Show today's sessions only
        month: Filter to a specific month, e.g. '2026-04'
    """
    return _session_report({"last": last, "today": today, "month": month})


@mcp.tool
def claude_monthly_summary(month: Optional[str] = None) -> str:
    """Return total token usage and estimated cost for a Claude Code billing month.

    Shows spend vs your configured monthly budget and remaining runway.
    Defaults to the current month.

    Args:
        month: Month in YYYY-MM format. Defaults to current month.
    """
    return _monthly_summary({"month": month})


@mcp.tool
def claude_calibrate_pricing(actual_billed: float, month: Optional[str] = None) -> str:
    """Update the pricing discount factor from actual Claude Code billing.

    Run this each month after your billing statement resets.
    Provide the actual_billed amount shown on your subscription page.

    Args:
        actual_billed: Actual amount billed shown in Claude Code subscription (USD)
        month: Month being calibrated (YYYY-MM). Defaults to previous month.
    """
    return _calibrate({"actual_billed": actual_billed, "month": month})


@mcp.tool
def claude_tool_impact(tool: str, month: Optional[str] = None) -> str:
    """Analyze how a specific tool affects Claude Code session efficiency.

    Scans session history to compare tokens/turn, cache hit %, and cost
    between sessions that used the tool and those that didn't.
    Useful for measuring the real-world impact of tools like 'serena', 'ck',
    'ast-grep', 'Read', 'Grep', or any MCP/built-in tool.
    Provide the tool name as you'd refer to it naturally, e.g. 'serena', 'ck', 'ast-grep'.

    Args:
        tool: Tool name to analyze. Examples: 'serena', 'ck', 'ast-grep', 'Read', 'Grep', 'Glob', 'Bash'. Case-insensitive.
        month: Limit analysis to a specific month, e.g. '2026-04'. Defaults to all history.
    """
    return _tool_impact({"tool": tool, "month": month})


@mcp.tool
def copilot_tool_impact(tool: str, month: Optional[str] = None) -> str:
    """Analyze how a specific tool affects Copilot CLI session efficiency.

    Scans Copilot CLI session history to compare output tokens/turn and estimated cost
    between sessions that used the tool and those that didn't.
    Useful for measuring the real-world impact of tools like 'serena', 'ck',
    'ast-grep', 'view', 'bash', or any MCP tool.
    Provide the tool name as you'd refer to it naturally, e.g. 'serena', 'ck', 'bash'.

    Args:
        tool: Tool name to analyze. Case-insensitive substring match against MCP tool names.
        month: Limit analysis to a specific month, e.g. '2026-04'. Defaults to all history.
    """
    return _copilot_tool_impact({"tool": tool, "month": month})


@mcp.tool
def copilot_session_report(
    last: int = 20,
    today: bool = False,
    month: Optional[str] = None,
) -> str:
    """Show GitHub Copilot CLI session output token usage and efficiency metrics.

    Reads directly from ~/.copilot/session-state.
    Filter by last N sessions, today, or a specific month (YYYY-MM).
    Returns per-session: output tokens, turns, duration, tokens/turn, tools used, model.
    Note: only output tokens are tracked (Copilot CLI does not expose input/cache token counts).

    Args:
        last: Show last N sessions (default: 20)
        today: Show today's sessions only
        month: Filter to a specific month, e.g. '2026-04'
    """
    return _copilot_session_report({"last": last, "today": today, "month": month})


@mcp.tool
def copilot_monthly_summary(month: Optional[str] = None) -> str:
    """Monthly output token summary for GitHub Copilot CLI sessions.

    Shows total output tokens, session count, top projects, and at-API-rate
    equivalent cost vs your flat subscription budget.
    Defaults to the current month.

    Args:
        month: Month in YYYY-MM format. Defaults to current month.
    """
    return _copilot_monthly_summary({"month": month})


@mcp.tool
def configure_subscription(
    service: Literal["claude", "copilot"],
    plan: Optional[str] = None,
    monthly_budget: Optional[float] = None,
    overage_budget: Optional[float] = None,
) -> str:
    """Update the active Claude Code or GitHub Copilot subscription plan and monthly budget.

    Use this when you change plans or when your admin adjusts your budget.
    Provide a named plan key and/or a custom monthly_budget override.
    Claude plans: claude_pro ($20), claude_max_100 ($100), claude_max_200 ($200), claude_max_400 ($400), api ($0).
    Copilot plans: copilot_free ($0), copilot_pro ($10), copilot_pro_plus ($39), copilot_business ($19/seat), copilot_enterprise ($39/seat).

    For Copilot Pro, you can also set overage_budget to track the additional premium
    request budget you've configured on top of your base plan (e.g. overage_budget=25.0
    means $10 base + $25 overage cap = $35 total).

    Args:
        service: Which service to configure: 'claude' or 'copilot'
        plan: Named plan key. Claude: claude_pro, claude_max_100, claude_max_200, claude_max_400, api. Copilot: copilot_free, copilot_pro, copilot_pro_plus, copilot_business, copilot_enterprise.
        monthly_budget: Custom monthly budget in USD. Overrides the plan default.
        overage_budget: Copilot only. Additional premium request budget cap in USD (e.g. 25.0).
    """
    return _configure_subscription({"service": service, "plan": plan, "monthly_budget": monthly_budget, "overage_budget": overage_budget})


@mcp.tool
def analyze_copilot_session(session_id: Optional[str] = None) -> str:
    """Analyse a Copilot CLI session for inefficiencies and behavioural anti-patterns.

    Checks prompt quality (vague/short prompts), tool batching (parallelism),
    bash overuse vs specialised tools, and memory utilisation.

    Defaults to the currently active session (inuse lock detected), then falls
    back to the most recently modified session.

    Args:
        session_id: Full or partial session UUID. Omit to use the active/latest session.
    """
    return _analyze_copilot_session({"session_id": session_id})


@mcp.tool
def copilot_behavior_report(last: int = 10, month: Optional[str] = None) -> str:
    """Cross-session pattern analysis for GitHub Copilot CLI sessions.

    Aggregates behavioural signals across recent sessions to surface recurring
    inefficiencies: vague prompts, low tool parallelism, bash overuse, missing
    memory calls. Returns a scored summary with actionable recommendations.

    Args:
        last: Number of recent sessions to analyse (default: 10)
        month: Limit to a specific month, e.g. '2026-04'
    """
    return _copilot_behavior_report({"last": last, "month": month})


@mcp.tool
def copilot_premium_usage(month: Optional[str] = None) -> str:
    """Fetch live Copilot premium request usage from the GitHub API.

    Calls GET /users/{username}/settings/billing/premium_request/usage and
    shows requests used, cost by model, and progress against your configured
    overage budget. Uses the gh CLI token automatically; falls back to the
    GH_TOKEN or GITHUB_TOKEN environment variables.

    Requires a fine-grained PAT (or gh CLI login) with 'Plan' (read) permission.
    Only works for individual Copilot plans — not org/enterprise-managed licenses.

    Args:
        month: Month in YYYY-MM format. Defaults to current month.
    """
    return _copilot_premium_usage({"month": month})


@mcp.tool
def record_copilot_spend(amount: float, month: Optional[str] = None) -> str:
    """Record actual Copilot overage spend for a month.

    Use this when you can see your real spend in the GitHub billing UI but
    the API isn't accessible (e.g. org/enterprise-managed plans).
    The recorded amount is shown in copilot_monthly_summary alongside
    your overage budget cap.

    Args:
        amount: Actual overage spend in USD (e.g. 17.72).
        month: Month in YYYY-MM format. Defaults to current month.
    """
    return _record_copilot_spend({"amount": amount, "month": month})


@mcp.tool
def copilot_budget_forecast(month: Optional[str] = None) -> str:
    """Forecast end-of-month Copilot spend and estimate savings from fixing inefficiencies.

    Uses burn rate from recorded spend + session history to project end-of-month cost.
    Analyses behavioral waste (low batching, bash overuse, vague prompts) and shows how
    fixing each issue would offset the projected spend.

    Requires record_copilot_spend and configure_subscription (overage_budget) to be set.

    Args:
        month: Month in YYYY-MM format. Defaults to current month.
    """
    return _copilot_budget_forecast({"month": month})


def _session_report(args: dict) -> str:
    rows = load_csv()
    if not rows:
        return "No session data found. Sessions are logged automatically when Claude Code stops."

    today_str = date.today().isoformat()
    month = args.get("month")
    today = args.get("today", False)
    last = args.get("last", 20)

    if today:
        rows = [r for r in rows if r["date"].startswith(today_str)]
    elif month:
        rows = [r for r in rows if r["date"].startswith(month)]

    if last and not today and not month:
        rows = rows[-last:]
    elif last:
        rows = rows[-last:]

    if not rows:
        return "No sessions found for the given filter."

    lines = []
    header = (
        f"{'Date':<17} {'Project':<12} {'Cache%':>7} {'$/sess':>7} "
        f"{'Total':>8} {'Out':>7} {'Min':>5} {'Turns':>5} {'Tok/T':>7}"
    )
    div = "─" * len(header)
    lines.extend([div, header, div])

    total_cost = 0.0
    for r in rows:
        cost = float(r.get("est_cost_usd", 0))
        total_cost += cost
        turns = int(r.get("turns", 1)) or 1
        tok_per_turn = fmt(int(r["total_tokens"]) // turns)
        lines.append(
            f"{r['date']:<17} {r['project']:<12} "
            f"{r['cache_hit_pct']:>6}% ${cost:>6.4f} "
            f"{fmt(r['total_tokens']):>8} {fmt(r['output_tokens']):>7} "
            f"{r['duration_min']:>5} {r['turns']:>5} {tok_per_turn:>7}"
        )

    lines.append(div)

    if len(rows) > 1:
        avg_cache = sum(float(r["cache_hit_pct"]) for r in rows) / len(rows)
        avg_cost = total_cost / len(rows)
        lines.append(
            f"\nSessions: {len(rows)}  |  Total est.: ${total_cost:.4f}  |  "
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
    rows = load_csv()
    month = args.get("month") or date.today().strftime("%Y-%m")
    rows = [r for r in rows if r["date"].startswith(month)]

    if not rows:
        return f"No session data found for {month}."

    try:
        from pricing import estimate_cost, CLAUDE_PLANS, LIST_PRICES
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
                f"",
                f"Plan:              {plan_label}",
                f"Monthly budget:    ${claude_budget:.2f}",
                f"Estimated spend:   ${cost:.2f}",
                f"Remaining:         ${remaining:.2f}  ({pct_used:.1f}% used)",
                f"                   [{bar}]",
            ]
        else:
            budget_lines = [
                f"",
                f"Plan:              {plan_label}  (no monthly budget cap)",
                f"Estimated spend:   ${cost:.2f}",
            ]

        lines = [
            f"## Monthly Summary — {month}",
            f"",
            f"Token breakdown:",
            f"  Input:         {totals['input']:>15,}",
            f"  Output:        {totals['output']:>15,}",
            f"  Cache reads:   {totals['cache_read']:>15,}  ({fmt(totals['cache_read'])})",
            f"  Cache creates: {totals['cache_create']:>15,}",
            f"",
            f"At API list prices:                 ${list_cost:.2f}",
            f"Discount factor:                    {discount} ({(1-discount)*100:.1f}% off list)",
        ] + budget_lines + [
            f"",
            f"Sessions tracked: {len(rows)}",
            f"",
            f"Note: If this is the current month, compare to your Claude Code subscription",
            f"usage counter. Cross-month sessions may cause ~$20-50 discrepancy.",
        ]
        return "\n".join(lines)

    except ImportError:
        cost = sum(float(r["est_cost_usd"]) for r in rows)
        return f"Month: {month}\nSessions: {len(rows)}\nEstimated cost: ${cost:.4f}"


def _calibrate(args: dict) -> str:
    import subprocess, sys as _sys
    actual = args["actual_billed"]
    month = args.get("month")

    calibrate_script = Path(__file__).parent / "calibrate.py"
    cmd = [_sys.executable, str(calibrate_script), str(actual)]
    if month:
        cmd += ["--month", month]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout or result.stderr or "Calibration complete."
    except Exception as e:
        return f"Calibration failed: {e}"


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


def _scan_sessions_for_tool(query: str, csv_rows: list[dict]) -> tuple[list[tuple], list[dict]]:
    """
    Split sessions into those that used the tool vs those that didn't.
    Uses the 'tools' column from CSV when available; falls back to scanning JSONL files.
    Returns (sessions_with, sessions_without).
    sessions_with: list of (csv_row, call_count)
    sessions_without: list of csv_row
    """
    sessions_with = []
    sessions_without = []

    # Deduplicate rows by session_id (keep last, which has the most turns)
    seen_ids: dict[str, dict] = {}
    for r in csv_rows:
        seen_ids[r["session_id"]] = r
    deduped = list(seen_ids.values())

    # Split into rows with and without the 'tools' column populated
    csv_indexed: dict[str, dict] = {}
    for r in deduped:
        tools_col = r.get("tools", "")
        if tools_col:
            # Fast path: match directly against recorded tool names
            tool_names = tools_col.split()
            matched = any(_matches_tool(query, t, {}) for t in tool_names)
            if matched:
                sessions_with.append((r, sum(1 for t in tool_names if _matches_tool(query, t, {}))))
            else:
                sessions_without.append(r)
        else:
            # No tools column — queue for JSONL scan
            csv_indexed[r["session_id"]] = r

    # Fall back to JSONL scanning for sessions without a tools column
    if csv_indexed:
        seen_jsonl = set()
        for jsonl_path in glob(str(Path.home() / ".claude/projects/**/*.jsonl"), recursive=True):
            session_uuid = Path(jsonl_path).stem
            session_id_8 = session_uuid[:8]

            if session_id_8 not in csv_indexed or session_id_8 in seen_jsonl:
                continue
            seen_jsonl.add(session_id_8)

            csv_row = csv_indexed[session_id_8]
            call_count = 0

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
                continue

            if call_count > 0:
                sessions_with.append((csv_row, call_count))
            else:
                sessions_without.append(csv_row)

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

    rows = load_csv()
    if not rows:
        return "No session data found."

    if month:
        rows = [r for r in rows if r["date"].startswith(month)]
        if not rows:
            return f"No session data found for {month}."

    sessions_with, sessions_without = _scan_sessions_for_tool(query, rows)

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
        f"",
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
        lines.append(row_line("Avg tokens/turn", wa["tok_turn"], wo["tok_turn"], lambda v: fmt(int(v)),  lower_is_better=True))
        lines.append(row_line("Avg cache hit %", wa["cache_pct"], wo["cache_pct"], lambda v: f"{v:.1f}%", lower_is_better=False))
        lines.append(row_line("Avg cost/session", wa["cost"],   wo["cost"],     lambda v: f"${v:.4f}",  lower_is_better=True))
        lines.append(row_line("Avg turns",    wa["turns"],    wo["turns"],    lambda v: f"{v:.0f}",    lower_is_better=False))
        lines.append(row_line("Avg duration (min)", wa["duration"], wo["duration"], lambda v: f"{v:.1f}", lower_is_better=False))
    else:
        lines.append(f"{'Sessions':<22} {str(wa['n']):>16} {'0':>16} {'n/a':>12}")
        lines.append(f"{'Avg tokens/turn':<22} {fmt(int(wa['tok_turn'])):>16} {'n/a':>16} {'n/a':>12}")
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
            f"{fmt(int(_tok_per_turn(r))):>7}  {float(r['cache_hit_pct']):>6.1f}%  ${float(r['est_cost_usd']):>7.4f}"
        )

    lines.append("")
    lines.append("▼ = improvement (lower tokens/cost)  |  ▲ = regression  |  Delta = % change vs sessions without")

    return "\n".join(lines)


def _copilot_tool_impact(args: dict) -> str:
    query = args.get("tool", "").strip()
    month = args.get("month")

    if not query:
        return "Please provide a tool name to analyze."

    sessions = load_copilot_sessions()
    if not sessions:
        return "No Copilot CLI session data found."

    if month:
        sessions = [s for s in sessions if s["date"].startswith(month)]
        if not sessions:
            return f"No Copilot CLI session data found for {month}."

    # For each session, reload its events to get precise tool call counts
    # (load_copilot_sessions only stores a set of tool names, not counts)
    sessions_with: list[tuple[dict, int]] = []
    sessions_without: list[dict] = []

    for s in sessions:
        sid = s["session_id"]
        # Find the full session dir by matching the 8-char prefix
        session_dir = None
        if COPILOT_SESSIONS_PATH.exists():
            for d in COPILOT_SESSIONS_PATH.iterdir():
                if d.name.startswith(sid) or d.name[:8] == sid:
                    session_dir = d
                    break

        tool_calls = 0
        if session_dir:
            events_file = session_dir / "events.jsonl"
            if events_file.exists():
                events = load_copilot_session_events(session_dir.name)
                tool_calls = sum(
                    1 for e in events
                    if e.get("type") == "tool.execution_start"
                    and _matches_tool(query, e.get("data", {}).get("toolName", ""),
                                     e.get("data", {}).get("arguments", {}))
                )

        if tool_calls > 0:
            sessions_with.append((s, tool_calls))
        else:
            sessions_without.append(s)

    if not sessions_with:
        period = f" in {month}" if month else ""
        return (
            f"No Copilot CLI sessions found that used '{query}'{period}.\n\n"
            f"Tip: The tool name must match how it appears in session events — "
            f"e.g. 'serena', 'ck', 'bash', 'view', or a substring of an MCP tool name."
        )

    with_sessions = [s for s, _ in sessions_with]
    total_calls = sum(c for _, c in sessions_with)

    def _avg_metric(sess_list: list[dict], key: str) -> float:
        vals = [s[key] for s in sess_list if s.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def _out_per_turn(s: dict) -> float:
        turns = s["turns"] or 1
        return s["output_tokens"] / turns

    def avgs(sess_list: list[dict]) -> Optional[dict]:
        if not sess_list:
            return None
        opt_vals = [_out_per_turn(s) for s in sess_list]
        return {
            "n":           len(sess_list),
            "out_per_turn": sum(opt_vals) / len(opt_vals),
            "turns":       _avg_metric(sess_list, "turns"),
            "duration":    _avg_metric(sess_list, "duration_min"),
            "total_output": sum(s["output_tokens"] for s in sess_list),
        }

    wa = avgs(with_sessions)
    wo = avgs(sessions_without)

    # Cost estimates: use recorded spend / total turns as cost-per-turn proxy
    cfg = _config.load()
    month_key = month or date.today().strftime("%Y-%m")
    recorded_spend = cfg.get("copilot_spend_history", {}).get(month_key)
    all_month_sessions = load_copilot_sessions()
    if month:
        all_month_sessions = [s for s in all_month_sessions if s["date"].startswith(month)]
    total_turns_month = sum(s["turns"] for s in all_month_sessions) or 1
    cost_per_turn = (recorded_spend / total_turns_month) if recorded_spend else None

    MIN_SESSIONS_FOR_RELIABLE_DATA = 10
    low_sample_warning = (
        f"⚠️  Low sample size: only {len(sessions_with)} session(s) used '{query}' "
        f"(recommend ≥{MIN_SESSIONS_FOR_RELIABLE_DATA} for reliable results). "
        f"Use the tool consistently across more sessions and re-run this report."
        if len(sessions_with) < MIN_SESSIONS_FOR_RELIABLE_DATA else ""
    )

    div = "─" * 68
    period_label = f" ({month})" if month else ""
    lines = [
        f"## Copilot Tool Impact — '{query}'{period_label}",
        "",
        f"Scanned {len(sessions_with) + len(sessions_without)} sessions"
        + (f" in {month}" if month else ""),
    ]
    if low_sample_warning:
        lines += ["", low_sample_warning]
    lines.append("")

    h_metric  = f"{'Metric':<26}"
    h_with    = f"{'With ' + query:>14}"
    h_without = f"{'Without ' + query:>14}"
    h_delta   = f"{'Delta':>12}"
    lines += [div, f"{h_metric} {h_with} {h_without} {h_delta}", div]

    def row_line(label, v_with, v_without, fmt_fn, lower_is_better=True):
        if v_without and v_without != 0:
            delta = ((v_with - v_without) / v_without) * 100
            arrow = "▼" if (delta < 0) == lower_is_better else "▲"
            delta_str = f"{arrow} {abs(delta):.1f}%"
        else:
            delta_str = "n/a"
        wo_str = fmt_fn(v_without) if v_without is not None else "n/a"
        return f"{label:<26} {fmt_fn(v_with):>14} {wo_str:>14} {delta_str:>12}"

    lines.append(row_line("Sessions",          wa["n"],            wo["n"] if wo else None,
                          lambda v: str(int(v)), lower_is_better=False))
    lines.append(row_line("Avg output/turn",   wa["out_per_turn"], wo["out_per_turn"] if wo else None,
                          lambda v: fmt(int(v)), lower_is_better=True))
    lines.append(row_line("Avg turns/session", wa["turns"],        wo["turns"] if wo else None,
                          lambda v: f"{v:.1f}",  lower_is_better=False))
    lines.append(row_line("Avg duration (min)", wa["duration"],    wo["duration"] if wo else None,
                          lambda v: f"{v:.1f}",  lower_is_better=False))

    if cost_per_turn and wo:
        avg_cost_with    = wa["turns"] * cost_per_turn
        avg_cost_without = wo["turns"] * cost_per_turn
        lines.append(row_line("Est. avg cost/session", avg_cost_with, avg_cost_without,
                              lambda v: f"${v:.3f}", lower_is_better=True))

    lines += [div, ""]

    avg_calls = total_calls / len(sessions_with)
    lines.append(f"Total '{query}' calls across {len(sessions_with)} sessions: {total_calls:,}  (avg {avg_calls:.1f}/session)")
    lines.append("")

    if cost_per_turn and wo and sessions_without:
        out_per_turn_delta = wo["out_per_turn"] - wa["out_per_turn"]
        if out_per_turn_delta > 0:
            # Project monthly savings: if all sessions used the tool
            total_sessions_month = len(all_month_sessions)
            avg_turns_wo = wo["turns"]
            savings_per_session = (out_per_turn_delta / wo["out_per_turn"]) * avg_turns_wo * cost_per_turn
            monthly_savings = savings_per_session * total_sessions_month
            lines.append(f"Est. monthly savings (if used in all sessions):  ~${monthly_savings:.2f}")
            lines.append("")

    top = sorted(sessions_with, key=lambda x: x[1], reverse=True)[:5]
    lines.append(f"Top sessions by '{query}' call count:")
    lines.append(f"  {'Date':<17} {'Project':<12} {'Calls':>6}  {'OutT/T':>7}  {'Turns':>5}")
    for s, calls in top:
        lines.append(
            f"  {s['date']:<17} {s['project']:<12} {calls:>6}  "
            f"{fmt(int(_out_per_turn(s))):>7}  {s['turns']:>5}"
        )

    lines.append("")
    lines.append("▼ = improvement (lower output/cost)  |  ▲ = regression  |  Delta = % change vs sessions without")
    lines.append("Note: Only output tokens tracked. Cost estimate uses recorded_spend / total turns as proxy.")

    return "\n".join(lines)


def _copilot_session_report(args: dict) -> str:
    sessions = load_copilot_sessions()
    if not sessions:
        return (
            "No Copilot CLI session data found. "
            "Sessions are stored in ~/.copilot/session-state/ after each conversation."
        )

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
        return "No Copilot CLI sessions found for the given filter."

    lines = []
    header = (
        f"{'Date':<17} {'Project':<12} {'Model':<22} "
        f"{'Output':>8} {'Turns':>5} {'Min':>5} {'OutT/T':>7}"
    )
    div = "─" * len(header)
    lines.extend([div, header, div])

    total_output = 0
    for s in sessions:
        turns = s["turns"] or 1
        out_per_turn = fmt(s["output_tokens"] // turns)
        total_output += s["output_tokens"]
        lines.append(
            f"{s['date']:<17} {s['project']:<12} {s['model']:<22} "
            f"{fmt(s['output_tokens']):>8} {s['turns']:>5} {s['duration_min']:>5} {out_per_turn:>7}"
        )

    lines.append(div)

    if len(sessions) > 1:
        avg_out = total_output / len(sessions)
        lines.append(
            f"\nSessions: {len(sessions)}  |  Total output tokens: {fmt(total_output)}  |  "
            f"Avg output/session: {fmt(int(avg_out))}"
        )

    lines.append(
        "\nNote: Only output tokens are tracked — Copilot CLI does not expose input/cache token counts."
    )
    return "\n".join(lines)


def _copilot_monthly_summary(args: dict) -> str:
    month = args.get("month") or date.today().strftime("%Y-%m")
    sessions = load_copilot_sessions()
    month_sessions = [s for s in sessions if s["date"].startswith(month)]

    if not month_sessions:
        return f"No Copilot CLI session data found for {month}."

    from pricing import COPILOT_PLANS, LIST_PRICES
    cfg = _config.load()
    copilot_budget  = cfg.get("copilot_monthly_budget", 10.0)
    overage_budget  = cfg.get("copilot_overage_budget", 0.0)
    total_budget    = copilot_budget + overage_budget
    copilot_plan    = cfg.get("copilot_plan", "copilot_pro")
    plan_label      = COPILOT_PLANS.get(copilot_plan, {}).get("label", copilot_plan)
    _default_list   = LIST_PRICES.get("claude-sonnet-4-6", {"output": 15.00})

    total_output = sum(s["output_tokens"] for s in month_sessions)

    # Equivalent API output cost (informational — Copilot is flat-rate)
    equiv_api_cost = sum(
        s["output_tokens"] * LIST_PRICES.get(s["model"], _default_list).get("output", 15.0) / 1_000_000
        for s in month_sessions
    )

    # Top projects by output tokens
    project_totals: dict[str, int] = {}
    for s in month_sessions:
        project_totals[s["project"]] = project_totals.get(s["project"], 0) + s["output_tokens"]
    top_projects = sorted(project_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    # Estimate premium requests: each assistant turn consumes one premium request.
    # This is a proxy — free-quota models don't count, but it's the best we can
    # derive from local session data without API access.
    total_turns = sum(s["turns"] for s in month_sessions)

    lines = [f"## Copilot CLI Monthly Summary — {month}", ""]

    if overage_budget > 0:
        lines += [
            f"Plan:              {plan_label}  (${copilot_budget:.2f}/mo base)",
            f"Overage budget:    ${overage_budget:.2f}  (premium request cap)",
            f"Total budget:      ${total_budget:.2f}/mo",
        ]
    else:
        lines += [
            f"Plan:              {plan_label}",
            f"Budget:            ${copilot_budget:.2f}/mo  (flat rate)",
        ]

    lines += [
        "",
        f"Sessions:          {len(month_sessions)}",
        f"Output tokens:     {total_output:>15,}  ({fmt(total_output)})",
        f"Est. premium requests used:  ~{total_turns:,}  (turns across {len(month_sessions)} sessions)",
        "",
        f"At API output rates (Sonnet 4.6):   ${equiv_api_cost:.2f}",
    ]

    recorded_spend = cfg.get("copilot_spend_history", {}).get(month)

    if total_budget > 0:
        savings = equiv_api_cost - total_budget
        lines.append(
            f"Subscription savings vs API:        ${savings:.2f}  "
            f"({'saves' if savings > 0 else 'costs'} vs pay-as-you-go)"
        )
        if overage_budget > 0:
            if recorded_spend is not None:
                # Use actual recorded spend for the overage bar
                pct = (recorded_spend / overage_budget * 100)
                filled = min(20, int(pct / 5))
                bar = "█" * filled + "░" * (20 - filled)
                remaining = max(0.0, overage_budget - recorded_spend)
                lines += [
                    "",
                    f"Actual overage spend:               ${recorded_spend:.2f} / ${overage_budget:.2f}  "
                    f"({pct:.1f}%)  [{bar}]",
                    f"Remaining overage budget:           ${remaining:.2f}",
                ]
            else:
                pct_used = min(equiv_api_cost / total_budget * 100, 999.9)
                filled = int(min(pct_used, 100) / 5)
                bar = "█" * filled + "░" * (20 - filled)
                lines.append(f"Budget utilization (API est.):      {pct_used:.1f}%  [{bar}]")

    lines += ["", "Top projects by output tokens:"]
    for proj, toks in top_projects:
        lines.append(f"  {proj:<15} {fmt(toks):>8}")

    lines += [
        "",
        "Note: Input/cache tokens not tracked. API rate comparison uses output-only pricing.",
        "      Premium request estimate = total turns (proxy; free-quota model turns not excluded).",
        "      Use record_copilot_spend to log actual overage spend from the GitHub billing UI.",
    ]
    return "\n".join(lines)


def _configure_subscription(args: dict) -> str:
    service = args.get("service", "").lower()
    plan = args.get("plan", "")
    custom_budget = args.get("monthly_budget")
    overage_budget = args.get("overage_budget")

    if service not in ("claude", "copilot"):
        return "Invalid service. Use 'claude' or 'copilot'."

    from pricing import CLAUDE_PLANS, COPILOT_PLANS
    plans      = CLAUDE_PLANS  if service == "claude"  else COPILOT_PLANS
    plan_key   = "claude_plan" if service == "claude"  else "copilot_plan"
    budget_key = "claude_monthly_budget" if service == "claude" else "copilot_monthly_budget"

    if plan and plan not in plans:
        valid = ", ".join(plans.keys())
        return f"Unknown plan '{plan}' for {service}. Valid options: {valid}"

    if custom_budget is not None:
        new_budget = float(custom_budget)
        new_plan   = plan if plan else "custom"
        plan_label = plans.get(new_plan, {}).get("label", "Custom")
    elif plan:
        new_budget = plans[plan]["monthly_budget"]
        new_plan   = plan
        plan_label = plans[plan]["label"]
    elif overage_budget is not None and service == "copilot":
        # Only overage_budget provided — keep existing plan/budget
        cfg = _config.load()
        new_budget = cfg.get("copilot_monthly_budget", 10.0)
        new_plan   = cfg.get("copilot_plan", "copilot_pro")
        plan_label = plans.get(new_plan, {}).get("label", new_plan)
    else:
        return "Provide a plan name, a monthly_budget, or both."

    updates = {plan_key: new_plan, budget_key: new_budget}
    if overage_budget is not None:
        if service != "copilot":
            return "overage_budget is only supported for the 'copilot' service."
        updates["copilot_overage_budget"] = float(overage_budget)

    _config.update(**updates)

    service_label = "Claude Code" if service == "claude" else "GitHub Copilot"
    lines = [
        f"Updated {service_label} subscription:",
        f"  Plan:   {plan_label}",
        f"  Budget: ${new_budget:.2f}/mo",
    ]
    if overage_budget is not None:
        total = new_budget + float(overage_budget)
        lines.append(f"  Overage budget: ${float(overage_budget):.2f}  (premium request cap)")
        lines.append(f"  Total budget:   ${total:.2f}/mo")
    lines.append(f"\nSaved to {_config.CONFIG_PATH}.")
    return "\n".join(lines)


def _analyze_copilot_session(args: dict) -> str:
    session_id = args.get("session_id") or ""

    if not COPILOT_SESSIONS_PATH.exists():
        return "No Copilot session data found at ~/.copilot/session-state/."

    # Resolve to a full session directory name
    if session_id and len(session_id) >= 36:
        resolved = session_id  # already full UUID
    elif session_id:
        # Match by prefix
        resolved = next(
            (d.name for d in COPILOT_SESSIONS_PATH.iterdir() if d.name.startswith(session_id)),
            None,
        )
        if not resolved:
            return f"No session found matching prefix '{session_id}'."
    else:
        # Active session first, then most-recently-modified
        resolved = _find_active_session_id()
        if not resolved:
            candidates = sorted(
                (d for d in COPILOT_SESSIONS_PATH.iterdir() if (d / "events.jsonl").exists()),
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
            resolved = candidates[0].name if candidates else None
        if not resolved:
            return "No Copilot sessions found."

    events = load_copilot_session_events(resolved)
    if not events:
        return f"No events recorded for session {resolved[:8]}."

    is_active = bool(list((COPILOT_SESSIONS_PATH / resolved).glob("inuse.*.lock")))
    analysis  = _analyze_session_events(events, resolved)
    return _format_session_analysis(analysis, is_active=is_active)


def _copilot_behavior_report(args: dict) -> str:
    last  = args.get("last", 10)
    month = args.get("month")

    if not COPILOT_SESSIONS_PATH.exists():
        return "No Copilot session data found at ~/.copilot/session-state/."

    # Gather session dirs sorted by most-recently-modified
    candidates = sorted(
        (d for d in COPILOT_SESSIONS_PATH.iterdir() if (d / "events.jsonl").exists()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    analyses: list[dict] = []
    for session_dir in candidates:
        if len(analyses) >= last:
            break
        events = load_copilot_session_events(session_dir.name)
        if not events:
            continue
        start = next((e for e in events if e.get("type") == "session.start"), None)
        if not start:
            continue
        if month and not start.get("timestamp", "").startswith(month):
            continue
        a = _analyze_session_events(events, session_dir.name)
        if a["turns"] == 0:
            continue
        analyses.append(a)

    if not analyses:
        period = f" for {month}" if month else ""
        return f"No sessions found{period}."

    n = len(analyses)
    vague_n      = sum(1 for a in analyses if a["vague_prompts"])
    low_batch_n  = sum(1 for a in analyses if a["batching_pct"] < 30 and
                       (a["single_tool_turns"] + a["multi_tool_turns"]) >= 3)
    bash_heavy_n = sum(1 for a in analyses if a["bash_pct"] > 50 and a["bash_count"] >= 3)
    no_memory_n  = sum(1 for a in analyses if not a["memory_used"])
    total_memory_calls = sum(len(a.get("memory_subjects", [])) for a in analyses)

    batch_vals   = [a["batching_pct"] for a in analyses
                    if (a["single_tool_turns"] + a["multi_tool_turns"]) > 0]
    avg_batching = sum(batch_vals) / len(batch_vals) if batch_vals else 0.0

    # Aggregate tool usage
    all_tool_counts: dict[str, int] = {}
    for a in analyses:
        for name, count in a["tool_name_counts"].items():
            all_tool_counts[name] = all_tool_counts.get(name, 0) + count
    total_tool_calls = sum(all_tool_counts.values())

    # Aggregate tool result sizes across sessions
    all_result_sizes: dict[str, int] = {}
    all_result_counts: dict[str, int] = {}
    for a in analyses:
        for name, size in a.get("tool_result_sizes", {}).items():
            all_result_sizes[name] = all_result_sizes.get(name, 0) + size
            all_result_counts[name] = all_result_counts.get(name, 0) + a.get("tool_result_counts", {}).get(name, 0)
    _CONTEXT_WARN_CHARS = 2_000
    agg_heavy_tools = [
        {
            "name":     name,
            "calls":    all_result_counts.get(name, 1),
            "avg_kb":   round(size / max(all_result_counts.get(name, 1), 1) / 1024, 1),
            "total_kb": round(size / 1024, 1),
        }
        for name, size in all_result_sizes.items()
        if size // max(all_result_counts.get(name, 1), 1) > _CONTEXT_WARN_CHARS
    ]

    def signal(bad: int, total: int, threshold_pct: float = 30.0) -> str:
        return "✅ Good" if (bad / total * 100) <= threshold_pct else "⚠️  Needs attention"

    MIN_SESSIONS_FOR_RELIABLE_BEHAVIOR = 10
    period_label = f" ({month})" if month else ""
    div = "─" * 65
    lines = [
        f"## Copilot Behaviour Report — Last {n} Sessions{period_label}",
        "",
    ]
    if n < MIN_SESSIONS_FOR_RELIABLE_BEHAVIOR:
        lines += [
            f"⚠️  Low sample size: only {n} session(s) analyzed "
            f"(recommend ≥{MIN_SESSIONS_FOR_RELIABLE_BEHAVIOR} for reliable behavior patterns). "
            f"Results may not reflect your typical habits.",
            "",
        ]
    lines += [
        f"{'Metric':<38} {'Value':>7}   Signal",
        div,
        f"{'Vague prompt sessions':<38} {vague_n:>3}/{n:<3}   {signal(vague_n, n)}",
        f"{'Low-batching sessions (<30% multi-tool)':<38} {low_batch_n:>3}/{n:<3}   {signal(low_batch_n, n)}",
        f"{'Bash-heavy sessions (>50% bash)':<38} {bash_heavy_n:>3}/{n:<3}   {signal(bash_heavy_n, n)}",
        f"{'Sessions without store_memory':<38} {no_memory_n:>3}/{n:<3}   {signal(no_memory_n, n, 50.0)}",
        f"{'Total store_memory calls':<38} {total_memory_calls:>7}",
        f"{'Avg tool batching %':<38} {avg_batching:>6.1f}%   {'✅ Good' if avg_batching >= 30 else '⚠️  Needs attention'}",
        div,
        "",
    ]

    if total_tool_calls:
        lines.append("### Tool Distribution (across all sessions)")
        for name, count in sorted(all_tool_counts.items(), key=lambda x: -x[1])[:10]:
            pct = count / total_tool_calls * 100
            bar = "█" * max(1, int(pct / 5))
            lines.append(f"  {name:<30} {count:>5}  ({pct:>4.0f}%)  {bar}")
        lines.append("")

    if agg_heavy_tools:
        lines.append("### Context Cost (Heavy Tools, avg > 2 KB per call)")
        lines.append(f"  {'Tool':<35} {'Calls':>5}  {'Avg KB':>6}  {'Total KB':>8}")
        for t in sorted(agg_heavy_tools, key=lambda x: -x["avg_kb"]):
            lines.append(f"  {t['name']:<35} {t['calls']:>5}  {t['avg_kb']:>6}  {t['total_kb']:>8}")
        lines.append("")

    # Recommendations
    recs: list[str] = []
    if vague_n > n * 0.3:
        recs.append(
            "📝 Prompts: Many sessions contain vague prompts. "
            "Include file names, error messages, or specific goals."
        )
    if low_batch_n > n * 0.4:
        recs.append(
            "⚡ Batching: Frequent low-parallelism sessions. "
            "Ask Copilot to read or search multiple things in a single turn."
        )
    if bash_heavy_n > n * 0.3:
        recs.append(
            "🔧 Tools: Frequent bash-heavy sessions. "
            "Prefer grep/glob/view tools over bash for file operations."
        )
    if no_memory_n == n:
        recs.append(
            "🧠 Memory: store_memory never called. "
            "Persist project conventions and preferences to cut repetitive context-setting."
        )
    elif no_memory_n > n * 0.5:
        recs.append(
            "🧠 Memory: store_memory called in fewer than half of sessions. "
            "Use it more consistently to reduce repeated context."
        )
    if agg_heavy_tools:
        top = agg_heavy_tools[0]["name"]
        recs.append(
            f"📦 Context: Heavy tool responses detected (e.g. {top}). "
            "Consider scoping queries or summarising large results to preserve context."
        )
    # Flag sessions with heavy view but no smart code tools
    sessions_view_heavy_no_smart = sum(
        1 for a in analyses
        if any(t["name"] == "view" for t in a.get("heavy_context_tools", []))
        and not a.get("smart_tools_used")
    )
    if sessions_view_heavy_no_smart > 0:
        recs.append(
            f"🔍 Smart Tools: {sessions_view_heavy_no_smart}/{n} sessions had heavy `view` usage "
            "without Serena, ck, or ast-grep. Install a code intelligence MCP to cut context cost."
        )

    if recs:
        lines.append("### Recommendations")
        for r in recs:
            lines.append(f"  {r}")
    else:
        lines.append("✅  No major behavioural patterns to flag.")

    return "\n".join(lines)


def _copilot_budget_forecast(args: dict) -> str:
    month = args.get("month") or date.today().strftime("%Y-%m")

    try:
        month_dt = datetime.strptime(month, "%Y-%m")
    except ValueError:
        return f"Invalid month format: '{month}'. Use YYYY-MM."

    sessions = load_copilot_sessions()
    month_sessions = [s for s in sessions if s["date"].startswith(month)]

    if not month_sessions:
        return f"No Copilot CLI session data found for {month}."

    cfg = _config.load()
    overage_budget = cfg.get("copilot_overage_budget", 0.0)
    recorded_spend = cfg.get("copilot_spend_history", {}).get(month)

    if recorded_spend is None:
        return (
            f"No recorded spend for {month}. "
            "Run record_copilot_spend first to enable budget forecasting."
        )
    if overage_budget <= 0:
        return (
            "No overage budget configured. "
            "Run configure_subscription with overage_budget to enable forecasting."
        )

    # Calendar math
    today = date.today()
    days_in_month = calendar.monthrange(month_dt.year, month_dt.month)[1]
    is_current_month = (month_dt.year == today.year and month_dt.month == today.month)
    days_elapsed = today.day if is_current_month else days_in_month
    days_remaining = (days_in_month - today.day) if is_current_month else 0

    total_turns = sum(s["turns"] for s in month_sessions)
    n_sessions = len(month_sessions)

    # Burn rate
    daily_spend = recorded_spend / days_elapsed if days_elapsed > 0 else 0.0
    daily_turns = total_turns / days_elapsed if days_elapsed > 0 else 0.0
    daily_sessions = n_sessions / days_elapsed if days_elapsed > 0 else 0.0

    # Linear projection to end of month
    projected_turns = total_turns + daily_turns * days_remaining
    projected_spend = recorded_spend + daily_spend * days_remaining

    # Budget exhaustion date
    if daily_spend > 0 and recorded_spend < overage_budget and is_current_month:
        remaining_budget = overage_budget - recorded_spend
        days_to_exhaustion = remaining_budget / daily_spend
        exhaustion_date = today + timedelta(days=days_to_exhaustion)
        if exhaustion_date.month > today.month or exhaustion_date.year > today.year:
            exhaustion_str = "within budget ✅"
        else:
            exhaustion_str = exhaustion_date.strftime("~%b %d")
    elif recorded_spend >= overage_budget:
        exhaustion_str = "already exceeded ⚠️"
    else:
        exhaustion_str = "n/a"

    # Behavior waste analysis — re-analyze each month session from events
    analyses = []
    session_ids = {s["session_id"] for s in month_sessions}
    if COPILOT_SESSIONS_PATH.exists():
        for session_dir in sorted(COPILOT_SESSIONS_PATH.iterdir()):
            if session_dir.name not in session_ids:
                continue
            events_file = session_dir / "events.jsonl"
            if not events_file.exists():
                continue
            events = load_copilot_session_events(events_file)
            if events:
                a = _analyze_session_events(events)
                if a:
                    analyses.append(a)

    TARGET_BATCHING = 60.0
    cost_per_turn = recorded_spend / total_turns if total_turns > 0 else 0.0

    waste_turns_batching = 0
    waste_turns_bash = 0
    waste_turns_vague = 0

    if analyses:
        n_analyzed = len(analyses)

        # Low batching: each % below 60% target → ~0.5% of turns are "extra" serial calls
        batch_vals = [
            a["batching_pct"] for a in analyses
            if (a["single_tool_turns"] + a["multi_tool_turns"]) > 0
        ]
        avg_batching = sum(batch_vals) / len(batch_vals) if batch_vals else TARGET_BATCHING
        if avg_batching < TARGET_BATCHING:
            batching_gap = TARGET_BATCHING - avg_batching
            waste_turns_batching = int(total_turns * batching_gap / 100 * 0.5)

        # Bash overuse: sessions >50% bash run ~10% longer due to context bloat
        bash_heavy_count = sum(
            1 for a in analyses if a["bash_pct"] > 50 and a["bash_count"] >= 3
        )
        if bash_heavy_count > 0:
            avg_turns_per_session = total_turns / n_analyzed
            waste_turns_bash = int(bash_heavy_count * avg_turns_per_session * 0.10)

        # Vague prompts: each vague session adds ~1.5 clarification turns
        vague_count = sum(1 for a in analyses if a["vague_prompts"])
        waste_turns_vague = int(vague_count * 1.5)

    total_waste_turns = waste_turns_batching + waste_turns_bash + waste_turns_vague
    waste_spend = total_waste_turns * cost_per_turn

    # Optimized projection: apply scaled waste savings over remaining days
    if days_elapsed > 0 and days_remaining > 0:
        daily_waste_spend = waste_spend / days_elapsed
        optimized_projected_spend = projected_spend - (daily_waste_spend * days_remaining)
    else:
        optimized_projected_spend = projected_spend

    # --- Format output ---
    pct_used = recorded_spend / overage_budget * 100
    filled = min(20, int(pct_used / 5))
    bar = "█" * filled + "░" * (20 - filled)
    over_under = projected_spend - overage_budget
    forecast_icon = "✅" if projected_spend <= overage_budget else "⚠️"

    MIN_DAYS_FOR_RELIABLE_FORECAST = 7
    low_days_warning = (
        f"⚠️  Low burn rate confidence: only {days_elapsed} day(s) of data "
        f"(recommend ≥{MIN_DAYS_FOR_RELIABLE_FORECAST} days for a reliable projection). "
        f"Early-month forecasts are highly sensitive to day-to-day variance."
        if is_current_month and days_elapsed < MIN_DAYS_FOR_RELIABLE_FORECAST else ""
    )

    lines = [
        f"## Copilot Budget Forecast — {month}",
        "",
        f"Current spend:      ${recorded_spend:.2f} / ${overage_budget:.2f}  "
        f"({pct_used:.0f}%)  [{bar}]",
        f"Days elapsed:       {days_elapsed} / {days_in_month}",
    ]
    if low_days_warning:
        lines += ["", low_days_warning]
    lines += [
        "",
        "### Burn Rate",
        f"  ${daily_spend:.2f}/day  |  {daily_turns:.0f} turns/day  "
        f"|  {daily_sessions:.1f} sessions/day",
        "",
    ]

    if is_current_month:
        lines += [
            "### Linear Projection (to end of month)",
            f"  Est. end-of-month turns:   ~{int(projected_turns):,}",
            f"  Est. end-of-month spend:   ~${projected_spend:.2f}  {forecast_icon}",
        ]
        if projected_spend > overage_budget:
            lines.append(f"  Over budget by:            ~${over_under:.2f}")
        else:
            lines.append(f"  Headroom remaining:        ~${-over_under:.2f}")
        lines.append(f"  Budget exhaustion:         {exhaustion_str}")
        lines.append("")

    if total_waste_turns > 0:
        div57 = "─" * 57
        lines += [
            "### Behavior Waste (estimated this month)",
            f"  {'Behavior':<35} {'Extra turns':>11}  {'Est. $':>8}",
            f"  {div57}",
        ]
        if waste_turns_batching > 0:
            lines.append(
                f"  {'Low batching (target 60%)':<35} "
                f"{waste_turns_batching:>+11}    "
                f"~${waste_turns_batching * cost_per_turn:.2f}"
            )
        if waste_turns_bash > 0:
            lines.append(
                f"  {'Bash overuse (>50% bash)':<35} "
                f"{waste_turns_bash:>+11}    "
                f"~${waste_turns_bash * cost_per_turn:.2f}"
            )
        if waste_turns_vague > 0:
            lines.append(
                f"  {'Vague prompts (+1.5 turns each)':<35} "
                f"{waste_turns_vague:>+11}    "
                f"~${waste_turns_vague * cost_per_turn:.2f}"
            )
        lines += [
            f"  {div57}",
            f"  {'Total estimated waste':<35} "
            f"{total_waste_turns:>+11}    ~${waste_spend:.2f}",
            "",
        ]

        if is_current_month and days_remaining > 0:
            opt_icon = "✅" if optimized_projected_spend <= overage_budget else "⚠️"
            opt_diff = optimized_projected_spend - overage_budget
            lines += [
                "### Optimized Projection (if issues fixed)",
                f"  Est. end-of-month spend:   ~${optimized_projected_spend:.2f}  {opt_icon}",
            ]
            if optimized_projected_spend <= overage_budget:
                lines.append(
                    f"  Would stay within budget   (${-opt_diff:.2f} headroom)"
                )
            else:
                lines.append(f"  Still over budget by:      ~${opt_diff:.2f}")
            lines.append("")
    else:
        lines.append("✅ No significant behavior waste detected this month.")
        lines.append("")

    lines.append(
        "Note: Waste models are approximate (batching gap × 0.5, bash-heavy × 10%,\n"
        "      vague prompts × 1.5 turns). Actual savings may vary."
    )

    return "\n".join(lines)


def _record_copilot_spend(args: dict) -> str:
    amount = args.get("amount")
    if amount is None or float(amount) < 0:
        return "Invalid amount. Provide a non-negative USD value, e.g. 17.72."
    amount = round(float(amount), 2)
    month = args.get("month") or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        return f"Invalid month format: '{month}'. Use YYYY-MM."

    cfg = _config.load()
    history: dict = cfg.get("copilot_spend_history", {})
    history[month] = amount
    _config.update(copilot_spend_history=history)

    overage_budget = cfg.get("copilot_overage_budget", 0.0)
    lines = [f"Recorded Copilot overage spend for {month}: ${amount:.2f}"]
    if overage_budget > 0:
        pct = amount / overage_budget * 100
        filled = min(20, int(pct / 5))
        bar = "█" * filled + "░" * (20 - filled)
        remaining = max(0.0, overage_budget - amount)
        lines.append(
            f"Overage budget: ${amount:.2f} / ${overage_budget:.2f}  "
            f"({pct:.1f}%)  [{bar}]"
        )
        lines.append(f"Remaining:      ${remaining:.2f}")
    return "\n".join(lines)


def _get_gh_token() -> str | None:
    """Return a GitHub token from env vars or the gh CLI keychain."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _get_gh_username(token: str) -> str | None:
    """Return the GitHub username from env var, config, gh CLI, or the API."""
    # GH_USER / GITHUB_USER often contain an email — only use if it looks like
    # a login (no @ sign).
    for env_var in ("GITHUB_USER", "GH_USER"):
        val = os.environ.get(env_var, "")
        if val and "@" not in val:
            return val
    username = _config.load().get("github_username")
    if username:
        return username
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("login")
    except Exception:
        pass
    return None


def _copilot_premium_usage(args: dict) -> str:
    month_str = args.get("month")
    if month_str:
        try:
            dt = datetime.strptime(month_str, "%Y-%m")
            year, month = dt.year, dt.month
        except ValueError:
            return f"Invalid month format: '{month_str}'. Use YYYY-MM."
    else:
        now = datetime.now()
        year, month = now.year, now.month

    token = _get_gh_token()
    if not token:
        return (
            "No GitHub token found. Either:\n"
            "  1. Run `gh auth login` to authenticate with the gh CLI\n"
            "  2. Set the GH_TOKEN or GITHUB_TOKEN environment variable"
        )

    username = _get_gh_username(token)
    if not username:
        return (
            "Could not determine GitHub username. "
            "Set GITHUB_USER env var or run `gh auth login`."
        )

    url = (
        f"https://api.github.com/users/{username}/settings/billing"
        f"/premium_request/usage?year={year}&month={month}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return (
                f"No premium request data found for {username} in "
                f"{year}-{month:02d}.\n"
                "This endpoint only works for individual Copilot plans "
                "(not org/enterprise-managed licenses)."
            )
        if e.code == 403:
            return "Access denied. Ensure your token has 'Plan' (read) permission."
        return f"GitHub API error {e.code}: {e.reason}"
    except Exception as e:
        return f"Failed to fetch premium request usage: {e}"

    usage_items = data.get("usageItems", [])
    period = f"{year}-{month:02d}"

    if not usage_items:
        return f"No premium request usage found for {username} in {period}."

    # Aggregate by model
    by_model: dict[str, dict] = {}
    total_requests = 0
    total_cost = 0.0
    for item in usage_items:
        model = item.get("model") or "unknown"
        qty = item.get("grossQuantity", 0)
        amt = item.get("grossAmount", 0.0)
        total_requests += qty
        total_cost += amt
        if model not in by_model:
            by_model[model] = {"requests": 0, "cost": 0.0}
        by_model[model]["requests"] += qty
        by_model[model]["cost"] += amt

    cfg = _config.load()
    overage_budget = cfg.get("copilot_overage_budget", 0.0)

    div = "─" * 58
    lines = [
        f"## Copilot Premium Request Usage — {username} ({period})",
        "",
        f"  Total requests used:  {total_requests:>6,}",
        f"  Total cost:           ${total_cost:>8.2f}",
    ]

    if overage_budget > 0:
        pct = (total_cost / overage_budget * 100) if overage_budget else 0.0
        filled = min(20, int(pct / 5))
        bar = "█" * filled + "░" * (20 - filled)
        lines.append(
            f"  Overage budget:       ${overage_budget:>8.2f}  "
            f"({pct:.1f}% used)  [{bar}]"
        )

    lines += [
        "",
        div,
        f"  {'Model':<42} {'Requests':>8}  {'Cost':>8}",
        div,
    ]
    for model, stats in sorted(by_model.items(), key=lambda x: -x[1]["requests"]):
        lines.append(
            f"  {model:<42} {stats['requests']:>8,}  ${stats['cost']:>7.2f}"
        )
    lines += [
        div,
        "",
        "  Note: Counters reset on the 1st of each month at 00:00 UTC.",
        "  Only individual Copilot plan usage is returned by this endpoint.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
