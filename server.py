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

import json
from typing import Literal, Optional

from fastmcp import FastMCP

import config as _config

from core.claude import (
    _session_report,
    _monthly_summary,
    _calibrate,
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

mcp = FastMCP("nudge-mcp")

# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource(
    "nudge://config",
    name="Nudge Config",
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
    "nudge://pricing",
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


if __name__ == "__main__":
    mcp.run()


