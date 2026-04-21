"""
Tests for core/claude.py and MCP server tool/resource discovery.

Uses the `client` and `fake_claude_sessions` fixtures from conftest.py.
"""

import json
import pytest

from conftest import _result_text

from core.loaders import fmt
from core.claude import _matches_tool, _avg, _tok_per_turn, tool_impact


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
    assert len(tools) == 14


async def test_list_tools_names(client):
    tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "claude_session_report",
        "claude_monthly_summary",
        "claude_calibrate_pricing",
        "claude_tool_impact",
        "copilot_session_report",
        "copilot_monthly_summary",
        "configure_subscription",
        "analyze_copilot_session",
        "copilot_behavior_report",
        "copilot_premium_usage",
        "record_copilot_spend",
        "copilot_budget_forecast",
        "copilot_tool_impact",
        "copilot_model_efficiency",
    }


# ---------------------------------------------------------------------------
# Resource discovery
# ---------------------------------------------------------------------------

async def test_list_resources(client):
    resources = await client.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "nudge://config" in uris
    assert "nudge://pricing" in uris


async def test_config_resource_is_valid_json(client):
    result = await client.read_resource("nudge://config")
    text = result[0].text if hasattr(result[0], "text") else result[0].content
    parsed = json.loads(text)
    assert "discount_factor" in parsed
    assert "available_claude_plans" in parsed
    assert "available_copilot_plans" in parsed


async def test_pricing_resource_is_valid_json(client):
    result = await client.read_resource("nudge://pricing")
    text = result[0].text if hasattr(result[0], "text") else result[0].content
    parsed = json.loads(text)
    assert "list_prices_per_mtok" in parsed
    assert "claude_plans" in parsed
    assert "copilot_plans" in parsed
    assert "discount_factor" in parsed


async def test_pricing_resource_has_sonnet(client):
    result = await client.read_resource("nudge://pricing")
    text = result[0].text if hasattr(result[0], "text") else result[0].content
    parsed = json.loads(text)
    assert any("sonnet" in model for model in parsed["list_prices_per_mtok"])


# ---------------------------------------------------------------------------
# session_report — no data
# ---------------------------------------------------------------------------

async def test_session_report_no_data_returns_message(client):
    result = await client.call_tool("claude_session_report", {})
    text = _result_text(result)
    assert isinstance(text, str)
    assert len(text) > 0


# ---------------------------------------------------------------------------
# session_report — with synthetic CSV data
# ---------------------------------------------------------------------------

async def test_session_report_with_data_shows_rows(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {})
    text = _result_text(result)
    assert "proj-alpha" in text
    assert "proj-beta" in text


async def test_session_report_month_filter_april(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "proj-alpha" in text
    assert "proj-beta" in text
    assert "proj-gamma" not in text  # March session excluded


async def test_session_report_month_filter_march(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {"month": "2026-03"})
    text = _result_text(result)
    assert "proj-gamma" in text
    assert "proj-alpha" not in text


async def test_session_report_last_limits_results(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {"last": 1})
    text = _result_text(result)
    # With last=1 only the final row (proj-gamma is 3rd chronologically)
    # CSV has 3 rows; last=1 returns only the last
    assert text.count("proj-") == 1


async def test_session_report_today_returns_no_data_for_old_csv(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {"today": True})
    text = _result_text(result)
    # Sample data is from 2026-04-01/02 and 2026-03-15, not today
    assert "No sessions found" in text or "proj-alpha" not in text


async def test_session_report_shows_cost(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "$" in text


async def test_session_report_shows_summary_line(client, fake_claude_sessions):
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "Sessions:" in text
    assert "Total est.:" in text


# ---------------------------------------------------------------------------
# Model detection
# ---------------------------------------------------------------------------

def test_load_claude_sessions_detects_model(fake_claude_sessions):
    """Each session should have a 'model' field derived from the JSONL message.model."""
    from core.loaders import load_claude_sessions
    sessions = load_claude_sessions()
    by_project = {s["project"]: s for s in sessions}
    assert by_project["proj-alpha"]["model"] == "claude-sonnet-4-6"
    assert by_project["proj-beta"]["model"] == "claude-opus-4-6"
    assert by_project["proj-gamma"]["model"] == "claude-sonnet-4-6"


def test_load_claude_sessions_models_list(fake_claude_sessions):
    """Single-model sessions have a one-element models list."""
    from core.loaders import load_claude_sessions
    sessions = load_claude_sessions()
    for s in sessions:
        assert isinstance(s["models"], list)
        assert len(s["models"]) >= 1


def test_load_claude_sessions_opus_costs_more_than_sonnet(fake_claude_sessions):
    """Opus sessions should be priced at higher rates than equivalent-size Sonnet sessions."""
    from core.loaders import load_claude_sessions
    sessions = load_claude_sessions()
    by_project = {s["project"]: s for s in sessions}
    # proj-beta (opus) has fewer tokens than proj-alpha (sonnet) but should still cost more per token
    opus_cost = by_project["proj-beta"]["est_cost_usd"]
    sonnet_cost = by_project["proj-alpha"]["est_cost_usd"]
    # Opus output is 5x more expensive — even with fewer total tokens it should be higher per output token
    opus_out = by_project["proj-beta"]["output_tokens"]
    sonnet_out = by_project["proj-alpha"]["output_tokens"]
    assert opus_cost / opus_out > sonnet_cost / sonnet_out


def test_load_claude_sessions_skips_synthetic_model(tmp_path, monkeypatch):
    """Entries with model '<synthetic>' should be excluded from token/cost counts."""
    import json
    import core.loaders

    synthetic_jsonl = [
        {"type": "user", "timestamp": "2026-04-10T10:00:00.000Z", "cwd": "/home/user/proj-synth"},
        {"type": "assistant", "timestamp": "2026-04-10T10:01:00.000Z", "message": {
            "model": "<synthetic>",
            "usage": {"input_tokens": 9999, "output_tokens": 9999},
            "content": [],
        }},
        {"type": "assistant", "timestamp": "2026-04-10T10:02:00.000Z", "message": {
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "content": [],
        }},
    ]
    path = tmp_path / "proj-synth" / "dddddddd-0000-0000-0000-000000000004.jsonl"
    path.parent.mkdir(parents=True)
    with open(path, "w") as f:
        for entry in synthetic_jsonl:
            f.write(json.dumps(entry) + "\n")

    monkeypatch.setattr(core.loaders, "CLAUDE_PROJECTS_PATH", tmp_path)
    sessions = core.loaders.load_claude_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    # Only the real sonnet entry counts — synthetic tokens excluded
    assert s["input_tokens"] == 100
    assert s["output_tokens"] == 50
    assert s["model"] == "claude-sonnet-4-6"


async def test_session_report_shows_model_column(client, fake_claude_sessions):
    """Session report table should include the model name."""
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "sonnet-4-6" in text
    assert "opus-4-6" in text


# ---------------------------------------------------------------------------
# monthly_summary
# ---------------------------------------------------------------------------

async def test_monthly_summary_default(client):
    result = await client.call_tool("claude_monthly_summary", {})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_monthly_summary_specific_month(client):
    result = await client.call_tool("claude_monthly_summary", {"month": "2026-04"})
    text = _result_text(result)
    assert isinstance(text, str)


async def test_monthly_summary_unknown_month_returns_message(client):
    result = await client.call_tool("claude_monthly_summary", {"month": "1999-01"})
    text = _result_text(result)
    assert "1999-01" in text


# ---------------------------------------------------------------------------
# tool_impact
# ---------------------------------------------------------------------------

async def test_tool_impact_unknown_tool(client):
    result = await client.call_tool("claude_tool_impact", {"tool": "nonexistent_tool_xyz"})
    text = _result_text(result)
    assert "nonexistent_tool_xyz" in text or "No session" in text


async def test_tool_impact_empty_string(client):
    result = await client.call_tool("claude_tool_impact", {"tool": ""})
    text = _result_text(result)
    assert "provide" in text.lower()


async def test_tool_impact_with_data_and_known_tool(client, fake_claude_sessions):
    result = await client.call_tool("claude_tool_impact", {"tool": "Read"})
    text = _result_text(result)
    # "Read" appears in two of the three sample sessions
    assert "Read" in text or "Tool Impact" in text


def test_tool_impact_low_sample_disclaimer(fake_claude_sessions):
    # 3 sessions; "Read" appears in 2 — below threshold of 10
    result = tool_impact({"tool": "Read"})
    assert "⚠️  Low sample size" in result


def test_tool_impact_no_disclaimer_when_sufficient(monkeypatch):
    # Build 10 sessions all using "Read"
    sessions = [
        {
            "date": f"2026-04-{i+1:02d} 10:00", "session_id": f"s{i:07d}",
            "project": "proj", "branch": "main",
            "input_tokens": 5000, "output_tokens": 1000,
            "cache_read_tokens": 3000, "cache_create_tokens": 500,
            "total_tokens": 9500, "turns": 5, "duration_min": 10.0,
            "est_cost_usd": 0.05, "cache_hit_pct": 60.0,
            "tools": "Read", "jsonl_path": "",
        }
        for i in range(10)
    ]
    import core.loaders
    monkeypatch.setattr(core.loaders, "load_claude_sessions", lambda: sessions)
    result = tool_impact({"tool": "Read"})
    assert "⚠️  Low sample size" not in result
