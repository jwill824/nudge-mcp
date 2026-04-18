"""
Tests for the Scrooge MCP server using the FastMCP in-process client.
"""

import csv
import json
import pytest
from pathlib import Path
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport

from server import mcp, fmt, _matches_tool, _avg, _tok_per_turn, load_copilot_sessions
import server as _server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Write sample CSV rows to a temp file and patch CSV_PATH."""
    csv_file = tmp_path / "sessions.csv"
    if SAMPLE_CSV_ROWS:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SAMPLE_CSV_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(SAMPLE_CSV_ROWS)
    monkeypatch.setattr(_server, "CSV_PATH", csv_file)
    return csv_file


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


# ---------------------------------------------------------------------------
# Unit tests — fmt()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n, expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1k"),
    (1500, "2k"),
    (999_999, "1000k"),
    (1_000_000, "1.0M"),
    (2_500_000, "2.5M"),
])
def test_fmt(n, expected):
    assert fmt(n) == expected


# ---------------------------------------------------------------------------
# Unit tests — _matches_tool()
# ---------------------------------------------------------------------------

def test_matches_tool_exact_builtin():
    assert _matches_tool("bash", "bash", {}) is True
    assert _matches_tool("grep", "grep", {}) is True


def test_matches_tool_case_insensitive_builtin():
    assert _matches_tool("read", "Read", {}) is True


def test_matches_tool_substring_mcp():
    assert _matches_tool("serena", "mcp__plugin_serena_serena__list_dir", {}) is True


def test_matches_tool_known_prefix_serena():
    assert _matches_tool("serena", "mcp__plugin_serena_serena__search_files", {}) is True


def test_matches_tool_no_match():
    assert _matches_tool("serena", "Read", {}) is False
    assert _matches_tool("grep", "bash", {}) is False


def test_matches_tool_bash_command_content():
    assert _matches_tool("rg", "bash", {"command": "rg 'pattern' src/"}) is True
    assert _matches_tool("pytest", "bash", {"command": "python3.12 -m pytest"}) is True


def test_matches_tool_bash_command_no_false_positive():
    # "cat" should not match "concatenate" as a full word
    assert _matches_tool("cat", "bash", {"command": "concatenate files"}) is False


# ---------------------------------------------------------------------------
# Unit tests — _avg() and _tok_per_turn()
# ---------------------------------------------------------------------------

def test_avg_basic():
    rows = [{"score": "10"}, {"score": "20"}, {"score": "30"}]
    assert _avg(rows, "score") == pytest.approx(20.0)


def test_avg_empty():
    assert _avg([], "score") == 0.0


def test_avg_missing_key():
    rows = [{"score": ""}, {"score": None}, {"score": "15"}]
    assert _avg(rows, "score") == pytest.approx(15.0)


def test_tok_per_turn_basic():
    row = {"total_tokens": "1000", "turns": "10"}
    assert _tok_per_turn(row) == pytest.approx(100.0)


def test_tok_per_turn_zero_turns_fallback():
    row = {"total_tokens": "1000", "turns": "0"}
    assert _tok_per_turn(row) == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

async def test_list_tools_count(client):
    tools = await client.list_tools()
    assert len(tools) == 7


async def test_list_tools_names(client):
    tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "session_report",
        "monthly_summary",
        "calibrate_pricing",
        "tool_impact",
        "copilot_session_report",
        "copilot_monthly_summary",
        "configure_subscription",
    }


# ---------------------------------------------------------------------------
# Resource discovery
# ---------------------------------------------------------------------------

async def test_list_resources(client):
    resources = await client.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "scrooge://config" in uris
    assert "scrooge://pricing" in uris


async def test_config_resource_is_valid_json(client):
    result = await client.read_resource("scrooge://config")
    text = result[0].text if hasattr(result[0], "text") else result[0].content
    parsed = json.loads(text)
    assert "discount_factor" in parsed
    assert "available_claude_plans" in parsed
    assert "available_copilot_plans" in parsed


async def test_pricing_resource_is_valid_json(client):
    result = await client.read_resource("scrooge://pricing")
    text = result[0].text if hasattr(result[0], "text") else result[0].content
    parsed = json.loads(text)
    assert "list_prices_per_mtok" in parsed
    assert "claude_plans" in parsed
    assert "copilot_plans" in parsed
    assert "discount_factor" in parsed


async def test_pricing_resource_has_sonnet(client):
    result = await client.read_resource("scrooge://pricing")
    text = result[0].text if hasattr(result[0], "text") else result[0].content
    parsed = json.loads(text)
    assert any("sonnet" in model for model in parsed["list_prices_per_mtok"])


# ---------------------------------------------------------------------------
# session_report — no data
# ---------------------------------------------------------------------------

async def test_session_report_no_data_returns_message(client):
    result = await client.call_tool("session_report", {})
    text = _result_text(result)
    assert isinstance(text, str)
    assert len(text) > 0


# ---------------------------------------------------------------------------
# session_report — with synthetic CSV data
# ---------------------------------------------------------------------------

async def test_session_report_with_data_shows_rows(client, fake_csv):
    result = await client.call_tool("session_report", {})
    text = _result_text(result)
    assert "proj-alpha" in text
    assert "proj-beta" in text


async def test_session_report_month_filter_april(client, fake_csv):
    result = await client.call_tool("session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "proj-alpha" in text
    assert "proj-beta" in text
    assert "proj-gamma" not in text  # March session excluded


async def test_session_report_month_filter_march(client, fake_csv):
    result = await client.call_tool("session_report", {"month": "2026-03"})
    text = _result_text(result)
    assert "proj-gamma" in text
    assert "proj-alpha" not in text


async def test_session_report_last_limits_results(client, fake_csv):
    result = await client.call_tool("session_report", {"last": 1})
    text = _result_text(result)
    # With last=1 only the final row (proj-gamma is 3rd chronologically)
    # CSV has 3 rows; last=1 returns only the last
    assert text.count("proj-") == 1


async def test_session_report_today_returns_no_data_for_old_csv(client, fake_csv):
    result = await client.call_tool("session_report", {"today": True})
    text = _result_text(result)
    # Sample data is from 2026-04-01/02 and 2026-03-15, not today
    assert "No sessions found" in text or "proj-alpha" not in text


async def test_session_report_shows_cost(client, fake_csv):
    result = await client.call_tool("session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "$" in text


async def test_session_report_shows_summary_line(client, fake_csv):
    result = await client.call_tool("session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "Sessions:" in text
    assert "Total est.:" in text


# ---------------------------------------------------------------------------
# monthly_summary
# ---------------------------------------------------------------------------

async def test_monthly_summary_default(client):
    result = await client.call_tool("monthly_summary", {})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_monthly_summary_specific_month(client):
    result = await client.call_tool("monthly_summary", {"month": "2026-04"})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_monthly_summary_unknown_month_returns_message(client):
    result = await client.call_tool("monthly_summary", {"month": "1999-01"})
    text = _result_text(result)
    assert "1999-01" in text


# ---------------------------------------------------------------------------
# tool_impact
# ---------------------------------------------------------------------------

async def test_tool_impact_unknown_tool(client):
    result = await client.call_tool("tool_impact", {"tool": "nonexistent_tool_xyz"})
    text = _result_text(result)
    assert "nonexistent_tool_xyz" in text or "No session" in text


async def test_tool_impact_empty_string(client):
    result = await client.call_tool("tool_impact", {"tool": ""})
    text = _result_text(result)
    assert "provide" in text.lower()


async def test_tool_impact_with_data_and_known_tool(client, fake_csv):
    result = await client.call_tool("tool_impact", {"tool": "Read"})
    text = _result_text(result)
    # "Read" appears in two of the three sample sessions
    assert "Read" in text or "Tool Impact" in text


# ---------------------------------------------------------------------------
# copilot_session_report
# ---------------------------------------------------------------------------

async def test_copilot_session_report_today(client):
    result = await client.call_tool("copilot_session_report", {"today": True})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_copilot_session_report_april_has_sessions(client):
    result = await client.call_tool("copilot_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "Sessions:" in text or "2026-04" in text


async def test_copilot_session_report_output_tokens_nonzero(client):
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
# load_copilot_sessions() unit test
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
# copilot_monthly_summary
# ---------------------------------------------------------------------------

async def test_copilot_monthly_summary_april(client):
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

