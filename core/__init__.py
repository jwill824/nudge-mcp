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
    session_report,
    monthly_summary,
    calibrate,
    _matches_tool,
    tool_impact,
)
from core.copilot import (
    copilot_tool_impact,
    copilot_session_report,
    copilot_monthly_summary,
    configure_subscription,
    analyze_copilot_session,
    copilot_behavior_report,
    copilot_budget_forecast,
    record_copilot_spend,
    copilot_premium_usage,
)

__all__ = [
    "CLAUDE_PROJECTS_PATH", "COPILOT_SESSIONS_PATH", "COPILOT_CONFIG_PATH",
    "fmt", "load_claude_sessions", "load_copilot_sessions", "find_active_session_id",
    "load_copilot_session_events",
    "_WORKFLOW_TOOLS", "_analyze_session_events", "_format_session_analysis",
    "session_report", "monthly_summary", "calibrate", "_matches_tool", "tool_impact",
    "copilot_tool_impact", "copilot_session_report", "copilot_monthly_summary",
    "configure_subscription", "analyze_copilot_session", "copilot_behavior_report",
    "copilot_budget_forecast", "record_copilot_spend", "copilot_premium_usage",
]
