"""
Scrooge core package.

Modules:
  loaders   — path constants, session loading
  analysis  — Copilot session analysis engine and formatter
  claude    — Claude Code tool implementations
  copilot   — Copilot CLI tool implementations + GitHub API helpers

Public API (called by server.py):
"""

from core.loaders import (
    CLAUDE_PROJECTS_PATH,
    COPILOT_SESSIONS_PATH,
    COPILOT_CONFIG_PATH,
    fmt,
    load_claude_sessions,
    load_copilot_sessions,
    find_active_session_id,
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
    "CLAUDE_PROJECTS_PATH", "COPILOT_SESSIONS_PATH", "COPILOT_CONFIG_PATH",
    "fmt", "load_claude_sessions", "load_copilot_sessions", "find_active_session_id",
    "load_copilot_session_events",
    "_WORKFLOW_TOOLS", "_analyze_session_events", "_format_session_analysis",
    "_session_report", "_monthly_summary", "_calibrate", "_matches_tool", "_tool_impact",
    "_copilot_tool_impact", "_copilot_session_report", "_copilot_monthly_summary",
    "_configure_subscription", "_analyze_copilot_session", "_copilot_behavior_report",
    "_copilot_budget_forecast", "_record_copilot_spend", "_copilot_premium_usage",
]
