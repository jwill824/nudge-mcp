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


# ---------------------------------------------------------------------------
# copilot_session_report
# ---------------------------------------------------------------------------

async def test_copilot_session_report_today(client):
    result = await client.call_tool("copilot_session_report", {"today": True})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_copilot_session_report_april_has_sessions(client, fake_copilot_sessions):
    result = await client.call_tool("copilot_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "Sessions:" in text or "2026-04" in text


async def test_copilot_session_report_output_tokens_nonzero(client, fake_copilot_sessions):
    """April data exists and output tokens should now be parsed correctly."""
    result = await client.call_tool("copilot_session_report", {"month": "2026-04"})
    text = _result_text(result)
    # Should contain at least one non-zero formatted token count (e.g. "87k")
    assert any(c.isdigit() for c in text)
    assert "0     0     0     0" not in text  # no all-zero rows


async def test_copilot_session_report_last(client):
    result = await client.call_tool("copilot_session_report", {"last": 3})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_copilot_session_report_unknown_month(client):
    result = await client.call_tool("copilot_session_report", {"month": "1999-01"})
    text = _result_text(result)
    assert "No Copilot" in text or "1999-01" in text or "No sessions" in text


# ---------------------------------------------------------------------------
# copilot_monthly_summary
# ---------------------------------------------------------------------------

async def test_copilot_monthly_summary_april(client, fake_copilot_sessions):
    result = await client.call_tool("copilot_monthly_summary", {"month": "2026-04"})
    text = _result_text(result)
    assert "2026-04" in text
    assert "Sessions:" in text


async def test_copilot_monthly_summary_unknown_month(client):
    result = await client.call_tool("copilot_monthly_summary", {"month": "1999-01"})
    text = _result_text(result)
    assert "1999-01" in text


# ---------------------------------------------------------------------------
# configure_subscription
# ---------------------------------------------------------------------------

async def test_configure_subscription_invalid_service(client):
    with pytest.raises(Exception):
        await client.call_tool("configure_subscription", {"service": "invalid"})


async def test_configure_subscription_missing_plan_and_budget(client):
    result = await client.call_tool("configure_subscription", {"service": "claude"})
    text = _result_text(result)
    assert "Provide" in text or "plan" in text.lower()


async def test_configure_subscription_invalid_plan(client):
    result = await client.call_tool("configure_subscription", {
        "service": "claude",
        "plan": "not_a_real_plan",
    })
    text = _result_text(result)
    assert "Unknown plan" in text or "not_a_real_plan" in text


async def test_configure_subscription_valid_claude_plan(client):
    result = await client.call_tool("configure_subscription", {
        "service": "claude",
        "plan": "claude_max_400",
    })
    text = _result_text(result)
    assert "400" in text or "Claude" in text


async def test_configure_subscription_valid_copilot_plan(client):
    result = await client.call_tool("configure_subscription", {
        "service": "copilot",
        "plan": "copilot_pro",
    })
    text = _result_text(result)
    assert "Copilot" in text or "10" in text


async def test_configure_subscription_custom_budget(client):
    result = await client.call_tool("configure_subscription", {
        "service": "copilot",
        "monthly_budget": 39.0,
    })
    text = _result_text(result)
    assert "39" in text or "Copilot" in text


async def test_configure_subscription_plan_and_budget_override(client):
    """Plan + custom budget — budget should win."""
    result = await client.call_tool("configure_subscription", {
        "service": "claude",
        "plan": "claude_max_100",
        "monthly_budget": 150.0,
    })
    text = _result_text(result)
    assert "150" in text


# ---------------------------------------------------------------------------
# analyze_copilot_session — MCP tool (integration)
# ---------------------------------------------------------------------------

async def test_analyze_copilot_session_no_args(client):
    result = await client.call_tool("analyze_copilot_session", {})
    text = _result_text(result)
    assert isinstance(text, str)
    assert len(text) > 0


async def test_analyze_copilot_session_invalid_prefix(client):
    result = await client.call_tool("analyze_copilot_session", {"session_id": "zzzzzzz"})
    text = _result_text(result)
    assert "No Copilot" in text or "No session" in text or "zzzzzzz" in text


async def test_analyze_copilot_session_output_has_sections(client):
    result = await client.call_tool("analyze_copilot_session", {})
    text = _result_text(result)
    # Should have at least the analysis sections or a "no sessions" message
    has_sections = all(s in text for s in ["Prompt Quality", "Tool Batching", "Memory"])
    has_no_data  = "No Copilot" in text or "No sessions" in text or "No events" in text
    assert has_sections or has_no_data


# ---------------------------------------------------------------------------
# copilot_behavior_report — MCP tool (integration)
# ---------------------------------------------------------------------------

async def test_copilot_behavior_report_default(client):
    result = await client.call_tool("copilot_behavior_report", {})
    text = _result_text(result)
    assert isinstance(text, str)
    assert len(text) > 0


async def test_copilot_behavior_report_unknown_month(client):
    result = await client.call_tool("copilot_behavior_report", {"month": "1999-01"})
    text = _result_text(result)
    assert "No Copilot" in text or "No sessions" in text or "1999-01" in text


async def test_copilot_behavior_report_has_sections(client):
    result = await client.call_tool("copilot_behavior_report", {"last": 5})
    text = _result_text(result)
    has_report    = "Behaviour Report" in text and "Recommendations" in text
    has_no_data   = "No sessions" in text or "No Copilot" in text
    assert has_report or has_no_data


def test_copilot_behavior_report_low_sample_disclaimer(tmp_path, monkeypatch):
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)

    # Create 3 sessions (below threshold of 10)
    for i in range(3):
        session_dir = tmp_path / f"sess{i:07d}"
        session_dir.mkdir()
        events = [
            json.dumps({"type": "session.start", "timestamp": f"2026-04-0{i+1}T10:00:00Z"}),
            json.dumps({"type": "user.message", "data": {"content": "Do the thing"}}),
            json.dumps({"type": "tool.execution_start", "data": {"toolName": "bash", "arguments": {}}}),
        ]
        (session_dir / "events.jsonl").write_text("\n".join(events) + "\n")

    result = _copilot_behavior_report({"last": 10})
    assert "⚠️  Low sample size" in result


def test_copilot_behavior_report_no_disclaimer_when_sufficient(tmp_path, monkeypatch):
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)

    # Create 10 sessions (at threshold)
    for i in range(10):
        session_dir = tmp_path / f"sess{i:07d}"
        session_dir.mkdir()
        events = [
            json.dumps({"type": "session.start", "timestamp": f"2026-04-{i+1:02d}T10:00:00Z"}),
            json.dumps({"type": "user.message", "data": {"content": "Do the thing"}}),
            json.dumps({"type": "tool.execution_start", "data": {"toolName": "bash", "arguments": {}}}),
        ]
        (session_dir / "events.jsonl").write_text("\n".join(events) + "\n")

    result = _copilot_behavior_report({"last": 10})
    assert "⚠️  Low sample size" not in result


# ---------------------------------------------------------------------------
# _copilot_premium_usage unit tests
# ---------------------------------------------------------------------------

def _fake_usage_response(items: list[dict]) -> bytes:
    return json.dumps({
        "timePeriod": {"year": 2026, "month": 4},
        "usageItems": items,
    }).encode()


def test_copilot_premium_usage_no_token(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": ""})())
    result = _copilot_premium_usage({"month": "2026-04"})
    assert "No GitHub token" in result


def test_copilot_premium_usage_invalid_month():
    result = _copilot_premium_usage({"month": "April-2026"})
    assert "Invalid month" in result


def test_copilot_premium_usage_success(monkeypatch):
    import urllib.request

    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_USER", "testuser")

    items = [
        {
            "product": "Copilot", "sku": "Copilot Premium Request",
            "model": "claude-sonnet-4-6", "unitType": "requests",
            "pricePerUnit": 0.04, "grossQuantity": 50, "grossAmount": 2.0,
            "discountQuantity": 0, "discountAmount": 0.0,
            "netQuantity": 50, "netAmount": 2.0,
        },
        {
            "product": "Copilot", "sku": "Copilot Premium Request",
            "model": "gpt-5", "unitType": "requests",
            "pricePerUnit": 0.04, "grossQuantity": 25, "grossAmount": 1.0,
            "discountQuantity": 0, "discountAmount": 0.0,
            "netQuantity": 25, "netAmount": 1.0,
        },
    ]

    class FakeResponse:
        def read(self): return _fake_usage_response(items)
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    result = _copilot_premium_usage({"month": "2026-04"})
    assert "testuser" in result
    assert "2026-04" in result
    assert "75" in result        # total requests
    assert "3.00" in result      # total cost
    assert "claude-sonnet-4-6" in result
    assert "gpt-5" in result


def test_copilot_premium_usage_empty_items(monkeypatch):
    import urllib.request
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_USER", "testuser")

    class FakeResponse:
        def read(self): return _fake_usage_response([])
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    result = _copilot_premium_usage({"month": "2026-04"})
    assert "No premium request usage" in result


def test_copilot_premium_usage_403(monkeypatch):
    import urllib.request, urllib.error
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_USER", "testuser")

    def raise_403(*a, **kw):
        raise urllib.error.HTTPError(None, 403, "Forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", raise_403)
    result = _copilot_premium_usage({"month": "2026-04"})
    assert "Access denied" in result


def test_copilot_premium_usage_404(monkeypatch):
    import urllib.request, urllib.error
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_USER", "testuser")

    def raise_404(*a, **kw):
        raise urllib.error.HTTPError(None, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", raise_404)
    result = _copilot_premium_usage({"month": "2026-04"})
    assert "No premium request data" in result


def test_copilot_premium_usage_overage_budget(monkeypatch):
    import urllib.request
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_USER", "testuser")
    monkeypatch.setattr(_cfg, "load", lambda: {
        "copilot_overage_budget": 10.0,
        "copilot_monthly_budget": 10.0,
    })

    items = [{
        "model": "claude-sonnet-4-6", "grossQuantity": 100, "grossAmount": 4.0,
        "discountQuantity": 0, "discountAmount": 0.0, "netQuantity": 100, "netAmount": 4.0,
    }]

    class FakeResponse:
        def read(self): return _fake_usage_response(items)
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())

    result = _copilot_premium_usage({"month": "2026-04"})
    assert "40.0% used" in result
    assert "10.00" in result


# ---------------------------------------------------------------------------
# _record_copilot_spend unit tests
# ---------------------------------------------------------------------------

def test_record_copilot_spend_invalid_amount():
    result = _record_copilot_spend({"amount": -5.0, "month": "2026-04"})
    assert "Invalid amount" in result


def test_record_copilot_spend_invalid_month():
    result = _record_copilot_spend({"amount": 10.0, "month": "April-2026"})
    assert "Invalid month" in result


def test_record_copilot_spend_persists(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)

    result = _record_copilot_spend({"amount": 17.72, "month": "2026-04"})
    assert "17.72" in result
    assert "2026-04" in result

    cfg = _cfg.load()
    assert cfg["copilot_spend_history"]["2026-04"] == 17.72


def test_record_copilot_spend_shows_budget_bar(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_overage_budget": 25.0})

    result = _record_copilot_spend({"amount": 17.72, "month": "2026-04"})
    assert "█" in result
    assert "Remaining" in result


def test_record_copilot_spend_defaults_to_current_month(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)

    result = _record_copilot_spend({"amount": 5.0})
    assert date.today().strftime("%Y-%m") in result


# ---------------------------------------------------------------------------
# _copilot_budget_forecast unit tests
# ---------------------------------------------------------------------------

def test_copilot_budget_forecast_no_recorded_spend(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_overage_budget": 25.0})

    # Patch load_copilot_sessions to return something
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 5000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 5},
    ])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "record_copilot_spend" in result


def test_copilot_budget_forecast_no_overage_budget(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_spend_history": {"2026-04": 18.0}})

    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 5000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 5},
    ])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "overage budget" in result.lower()


def test_copilot_budget_forecast_invalid_month():
    result = _copilot_budget_forecast({"month": "April-2026"})
    assert "Invalid month" in result


def test_copilot_budget_forecast_no_sessions(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)

    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "No Copilot CLI session data" in result


def test_copilot_budget_forecast_shows_projection(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 18.0},
    })

    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 40,
         "output_tokens": 100_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 30},
    ])
    # No events to analyze (sessions dir absent or empty) — waste section omitted
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path / "nonexistent")

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "Burn Rate" in result
    assert "18.00" in result
    assert "25.00" in result


def test_copilot_budget_forecast_waste_section(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 18.0},
    })

    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 40,
         "output_tokens": 100_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 30},
    ])

    # Stub _analyze_session_events to return a bash-heavy, low-batching session
    def fake_analyze(events):
        return {
            "vague_prompts": True,
            "batching_pct": 20.0,
            "single_tool_turns": 8,
            "multi_tool_turns": 2,
            "bash_pct": 60.0,
            "bash_count": 5,
            "memory_used": False,
            "memory_subjects": [],
            "tool_name_counts": {"bash": 5, "view": 3},
            "tool_result_sizes": {},
            "tool_result_counts": {},
            "heavy_context_tools": [],
            "smart_tools_used": set(),
        }

    monkeypatch.setattr(core.analysis, "_analyze_session_events", fake_analyze)

    # Create a fake session dir with a dummy events.jsonl
    session_dir = tmp_path / "s1"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_text('{"type":"session.start"}\n')
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)
    monkeypatch.setattr(core.loaders, "load_copilot_session_events", lambda p: [{"type": "session.start"}])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "Behavior Waste" in result
    assert "Total estimated waste" in result


def test_copilot_budget_forecast_low_days_disclaimer(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 2.0},
    })

    # Simulate being on day 3 of the month
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 10},
    ])
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path / "nonexistent")

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 4, 3)

    with patch("core.copilot.date", FakeDate):
        result = _copilot_budget_forecast({"month": "2026-04"})
    assert "⚠️  Low burn rate confidence" in result


def test_copilot_budget_forecast_no_disclaimer_after_7_days(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 10.0},
    })

    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": f"s{i}", "date": f"2026-04-{i+1:02d} 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 10}
        for i in range(7)
    ])
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path / "nonexistent")

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 4, 8)

    with patch("core.copilot.date", FakeDate):
        result = _copilot_budget_forecast({"month": "2026-04"})
    assert "⚠️  Low burn rate confidence" not in result


# ---------------------------------------------------------------------------
# _copilot_tool_impact unit tests
# ---------------------------------------------------------------------------

def test_copilot_tool_impact_no_tool():
    result = _copilot_tool_impact({"tool": ""})
    assert "Please provide" in result


def test_copilot_tool_impact_no_sessions(monkeypatch):
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [])
    result = _copilot_tool_impact({"tool": "serena"})
    assert "No Copilot CLI session data found" in result


def test_copilot_tool_impact_no_matching_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "abc12345", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 50_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 15},
    ])
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)
    result = _copilot_tool_impact({"tool": "serena"})
    assert "No Copilot CLI sessions found" in result
    assert "serena" in result


def test_copilot_tool_impact_with_matching_session(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_spend_history": {"2026-04": 18.0}})

    sessions = [
        {"session_id": "aaa00001", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj-a", "duration_min": 10},
        {"session_id": "bbb00002", "date": "2026-04-02 11:00", "turns": 20,
         "output_tokens": 80_000, "model": "claude-sonnet-4.6",
         "project": "proj-b", "duration_min": 20},
    ]
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: sessions)

    # Session aaa00001 uses serena; bbb00002 does not
    session_dir = tmp_path / "aaa00001"
    session_dir.mkdir()
    serena_event = json.dumps({
        "type": "tool.execution_start",
        "data": {"toolName": "mcp__serena__find_symbol", "arguments": {}, "toolCallId": "t1"},
    })
    (session_dir / "events.jsonl").write_text(serena_event + "\n")
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)

    result = _copilot_tool_impact({"tool": "serena", "month": "2026-04"})
    assert "Copilot Tool Impact" in result
    assert "serena" in result
    assert "Sessions" in result
    assert "⚠️  Low sample size" in result


def test_copilot_tool_impact_low_sample_disclaimer_absent_when_sufficient(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_spend_history": {"2026-04": 18.0}})

    # 10 sessions all using serena — should NOT show disclaimer
    sessions = [
        {"session_id": f"s{i:07d}", "date": f"2026-04-{i+1:02d} 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 10}
        for i in range(10)
    ]
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: sessions)

    for s in sessions:
        session_dir = tmp_path / s["session_id"]
        session_dir.mkdir()
        event = json.dumps({
            "type": "tool.execution_start",
            "data": {"toolName": "mcp__serena__find_symbol", "arguments": {}, "toolCallId": "t1"},
        })
        (session_dir / "events.jsonl").write_text(event + "\n")
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)

    result = _copilot_tool_impact({"tool": "serena", "month": "2026-04"})
    assert "⚠️  Low sample size" not in result


def test_copilot_tool_impact_invalid_month(monkeypatch):
    monkeypatch.setattr(core.loaders, "load_copilot_sessions", lambda: [
        {"session_id": "aaa00001", "date": "2026-04-01 10:00", "turns": 5,
         "output_tokens": 10_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 5},
    ])
    result = _copilot_tool_impact({"tool": "bash", "month": "2099-12"})
    assert "No Copilot CLI session data found for" in result
