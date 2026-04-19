"""
Tests for the Scrooge MCP server using the FastMCP in-process client.
"""

import csv
import json
from datetime import datetime
import pytest
from pathlib import Path
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport

from server import (
    mcp, fmt, _matches_tool, _avg, _tok_per_turn,
    load_copilot_sessions, load_copilot_session_events,
    _analyze_session_events, _format_session_analysis, _find_active_session_id,
    _get_gh_token, _get_gh_username, _copilot_premium_usage,
    _record_copilot_spend, _copilot_budget_forecast, _copilot_tool_impact,
    _tool_impact, _copilot_behavior_report,
)
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
    import core.loaders
    csv_file = tmp_path / "sessions.csv"
    if SAMPLE_CSV_ROWS:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SAMPLE_CSV_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(SAMPLE_CSV_ROWS)
    monkeypatch.setattr(core.loaders, "CSV_PATH", csv_file)
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
    assert len(tools) == 13


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
    result = await client.call_tool("claude_session_report", {})
    text = _result_text(result)
    assert isinstance(text, str)
    assert len(text) > 0


# ---------------------------------------------------------------------------
# session_report — with synthetic CSV data
# ---------------------------------------------------------------------------

async def test_session_report_with_data_shows_rows(client, fake_csv):
    result = await client.call_tool("claude_session_report", {})
    text = _result_text(result)
    assert "proj-alpha" in text
    assert "proj-beta" in text


async def test_session_report_month_filter_april(client, fake_csv):
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "proj-alpha" in text
    assert "proj-beta" in text
    assert "proj-gamma" not in text  # March session excluded


async def test_session_report_month_filter_march(client, fake_csv):
    result = await client.call_tool("claude_session_report", {"month": "2026-03"})
    text = _result_text(result)
    assert "proj-gamma" in text
    assert "proj-alpha" not in text


async def test_session_report_last_limits_results(client, fake_csv):
    result = await client.call_tool("claude_session_report", {"last": 1})
    text = _result_text(result)
    # With last=1 only the final row (proj-gamma is 3rd chronologically)
    # CSV has 3 rows; last=1 returns only the last
    assert text.count("proj-") == 1


async def test_session_report_today_returns_no_data_for_old_csv(client, fake_csv):
    result = await client.call_tool("claude_session_report", {"today": True})
    text = _result_text(result)
    # Sample data is from 2026-04-01/02 and 2026-03-15, not today
    assert "No sessions found" in text or "proj-alpha" not in text


async def test_session_report_shows_cost(client, fake_csv):
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "$" in text


async def test_session_report_shows_summary_line(client, fake_csv):
    result = await client.call_tool("claude_session_report", {"month": "2026-04"})
    text = _result_text(result)
    assert "Sessions:" in text
    assert "Total est.:" in text


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


async def test_tool_impact_with_data_and_known_tool(client, fake_csv):
    result = await client.call_tool("claude_tool_impact", {"tool": "Read"})
    text = _result_text(result)
    # "Read" appears in two of the three sample sessions
    assert "Read" in text or "Tool Impact" in text


def test_tool_impact_low_sample_disclaimer(fake_csv):
    # fake_csv has only 3 sessions; "Read" appears in 2 — below threshold of 10
    result = _tool_impact({"tool": "Read"})
    assert "⚠️  Low sample size" in result


def test_tool_impact_no_disclaimer_when_sufficient(monkeypatch):
    import server as _srv
    # Build 10 sessions all using "Read"
    rows = [
        {
            "date": f"2026-04-{i+1:02d} 10:00", "session_id": f"s{i:07d}",
            "project": "proj", "input_tokens": "5000", "output_tokens": "1000",
            "cache_read_tokens": "3000", "cache_creation_tokens": "500",
            "total_tokens": "9500", "turns": "5", "duration_min": "10",
            "est_cost_usd": "0.05", "cache_hit_pct": "60.0",
            "tool_calls": "Read,Read", "model": "claude-sonnet-4.6",
        }
        for i in range(10)
    ]
    monkeypatch.setattr(_srv, "load_csv", lambda: rows)
    result = _tool_impact({"tool": "Read"})
    assert "⚠️  Low sample size" not in result


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


# ---------------------------------------------------------------------------
# _analyze_session_events() — unit tests with synthetic event data
# ---------------------------------------------------------------------------

def _make_events(
    user_prompts: list[str] | None = None,
    tool_calls_per_turn: list[list[str]] | None = None,
    store_memory: bool = False,
    bash_commands: list[str] | None = None,
) -> list[dict]:
    """Build a minimal synthetic events.jsonl list for testing."""
    from datetime import timezone as _tz
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=_tz.utc)

    events: list[dict] = [
        {
            "type": "session.start",
            "data": {"sessionId": "aaaa-bbbb", "context": {"cwd": "/home/user/myproject"}},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        }
    ]

    for i, prompt in enumerate(user_prompts or ["hello world"]):
        events.append({
            "type": "user.message",
            "data": {"content": prompt},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })
        tool_names = (tool_calls_per_turn or [[]])[i] if tool_calls_per_turn and i < len(tool_calls_per_turn) else []
        tool_requests = [{"toolCallId": f"tc{i}{j}", "name": t, "arguments": {}, "type": "function"}
                         for j, t in enumerate(tool_names)]
        events.append({
            "type": "assistant.message",
            "data": {"messageId": f"m{i}", "content": "", "toolRequests": tool_requests, "outputTokens": 500},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })
        for j, t in enumerate(tool_names):
            events.append({
                "type": "tool.execution_start",
                "data": {"toolCallId": f"tc{i}{j}", "toolName": t, "arguments": {}},
                "timestamp": now.isoformat().replace("+00:00", "Z"),
            })

    if store_memory:
        events.append({
            "type": "tool.execution_start",
            "data": {"toolCallId": "mem1", "toolName": "store_memory", "arguments": {"fact": "x"}},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })

    for cmd in (bash_commands or []):
        events.append({
            "type": "tool.execution_start",
            "data": {"toolCallId": "b1", "toolName": "bash", "arguments": {"command": cmd}},
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })

    return events


def test_analyze_session_events_basic_structure():
    events = _make_events(["tell me about the project", "show me server.py"])
    result = _analyze_session_events(events, "test-session-id")
    assert result["session_id"] == "test-ses"
    assert result["project"] == "myproject"
    assert result["turns"] == 2
    assert isinstance(result["vague_prompts"], list)
    assert isinstance(result["tool_name_counts"], dict)


def test_analyze_session_events_detects_continuation_prompt():
    events = _make_events(["yes please"])
    result = _analyze_session_events(events)
    assert any(p["reason"] == "continuation with no context" for p in result["vague_prompts"])


def test_analyze_session_events_specific_prompt_not_flagged():
    events = _make_events(["Can you look at src/server.py and find why the import fails?"])
    result = _analyze_session_events(events)
    assert result["vague_prompts"] == []


def test_analyze_session_events_memory_detected():
    events = _make_events(store_memory=True)
    result = _analyze_session_events(events)
    assert result["memory_used"] is True


def test_analyze_session_events_memory_not_detected():
    events = _make_events()
    result = _analyze_session_events(events)
    assert result["memory_used"] is False


def test_analyze_session_events_batching_multi_tool():
    events = _make_events(
        user_prompts=["read two files"],
        tool_calls_per_turn=[["view", "grep"]],
    )
    result = _analyze_session_events(events)
    assert result["multi_tool_turns"] == 1
    assert result["single_tool_turns"] == 0
    assert result["batching_pct"] == pytest.approx(100.0)


def test_analyze_session_events_batching_single_tool():
    events = _make_events(
        user_prompts=["read one file"],
        tool_calls_per_turn=[["view"]],
    )
    result = _analyze_session_events(events)
    assert result["single_tool_turns"] == 1
    assert result["multi_tool_turns"] == 0
    assert result["batching_pct"] == pytest.approx(0.0)


def test_analyze_session_events_bash_antipattern_grep():
    events = _make_events(bash_commands=["grep 'foo' src/"])
    result = _analyze_session_events(events)
    assert any("grep" in ap for ap in result["bash_antipatterns"])


def test_analyze_session_events_bash_antipattern_find():
    events = _make_events(bash_commands=["find . -name '*.py'"])
    result = _analyze_session_events(events)
    assert any("find" in ap for ap in result["bash_antipatterns"])


def test_analyze_session_events_no_bash_antipattern_for_git():
    events = _make_events(bash_commands=["git status && git diff"])
    result = _analyze_session_events(events)
    assert result["bash_antipatterns"] == []


def test_format_session_analysis_returns_string():
    events = _make_events(["yes please"], store_memory=False)
    analysis = _analyze_session_events(events, "abc123")
    output = _format_session_analysis(analysis, is_active=True)
    assert isinstance(output, str)
    assert "ACTIVE" in output
    assert "### Summary" in output


def test_format_session_analysis_flags_no_memory():
    events = _make_events()
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "store_memory" in output


def test_format_session_analysis_no_issues_when_clean():
    events = _make_events(
        user_prompts=["Please analyse src/server.py and tell me about the tool definitions"],
        tool_calls_per_turn=[["view", "grep"]],
        store_memory=True,
    )
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "No major inefficiencies" in output


def test_analyze_session_events_detects_mcp_tool():
    events = _make_events(tool_calls_per_turn=[["github-mcp-server-list_issues"]])
    # Inject a fake completion event with a large result
    events.append({
        "type": "tool.execution_complete",
        "data": {
            "toolCallId": "tc00",
            "result": {"content": "x" * 10_000},
        },
        "timestamp": events[-1]["timestamp"],
    })
    result = _analyze_session_events(events)
    assert "mcp_tool_analysis" in result
    mcp_tools = {t["name"] for t in result["mcp_tool_analysis"]}
    assert "github-mcp-server-list_issues" in mcp_tools


def test_analyze_session_events_mcp_disable_verdict():
    """High avg KB (<3 calls) → 'consider disabling' verdict."""
    events = _make_events(tool_calls_per_turn=[["github-mcp-server-list_issues"]])
    events.append({
        "type": "tool.execution_complete",
        "data": {"toolCallId": "tc00", "result": {"content": "x" * 10_000}},
        "timestamp": events[-1]["timestamp"],
    })
    result = _analyze_session_events(events)
    entry = next(t for t in result["mcp_tool_analysis"] if t["name"] == "github-mcp-server-list_issues")
    assert "consider disabling" in entry["verdict"]


def test_analyze_session_events_detects_skill_via_skill_tool():
    events = _make_events()
    events.append({
        "type": "tool.execution_start",
        "data": {"toolCallId": "sk1", "toolName": "skill", "arguments": {"skill": "superpowers"}},
        "timestamp": events[-1]["timestamp"],
    })
    result = _analyze_session_events(events)
    names = [s["name"] for s in result["skill_tools_used"]]
    assert "superpowers" in names


def test_analyze_session_events_detects_skill_by_tool_name():
    events = _make_events(tool_calls_per_turn=[["spec-kit"]])
    result = _analyze_session_events(events)
    names = [s["name"] for s in result["skill_tools_used"]]
    assert "spec-kit" in names


def test_format_session_analysis_shows_mcp_budget_section():
    events = _make_events(tool_calls_per_turn=[["github-mcp-server-list_issues"]])
    events.append({
        "type": "tool.execution_complete",
        "data": {"toolCallId": "tc00", "result": {"content": "x" * 10_000}},
        "timestamp": events[-1]["timestamp"],
    })
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "MCP Tool Budget" in output
    assert "github-mcp-server-list_issues" in output


def test_format_session_analysis_shows_skill_section():
    events = _make_events()
    events.append({
        "type": "tool.execution_start",
        "data": {"toolCallId": "sk1", "toolName": "skill", "arguments": {"skill": "superpowers"}},
        "timestamp": events[-1]["timestamp"],
    })
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "Agentic Workflow Tools" in output
    assert "Superpowers" in output


def test_format_session_analysis_shows_no_skills_message():
    events = _make_events()
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "Agentic Workflow Tools" in output
    assert "No agentic workflow tools detected" in output


def test_analyze_session_events_tracks_total_context_kb():
    events = _make_events(tool_calls_per_turn=[["view"]])
    events.append({
        "type": "tool.execution_complete",
        "data": {"toolCallId": "tc00", "result": {"content": "x" * 10_000}},
        "timestamp": events[-1]["timestamp"],
    })
    result = _analyze_session_events(events)
    assert result["total_context_kb"] > 0


def test_analyze_session_events_detects_repeated_view_reads():
    events = _make_events(
        tool_calls_per_turn=[["view"], ["view"], ["view"]],
        user_prompts=["turn 1", "turn 2", "turn 3"],
    )
    # Inject path argument into each view start event
    for e in events:
        if e.get("type") == "tool.execution_start" and e["data"].get("toolName") == "view":
            e["data"]["arguments"] = {"path": "/project/server.py"}
    result = _analyze_session_events(events)
    assert "/project/server.py" in result["repeated_view_paths"]
    assert result["redundant_reads"] == 2  # 3 reads, 2 are redundant
    assert result["repeat_view_pct"] == 67  # 2/3 = 66.7% → 67%


def test_format_session_analysis_shows_context_rot_warning():
    # Build a session with 5 reads of the same file → triggers rot threshold (>=3 redundant, >=15%)
    prompts = [f"turn {i}" for i in range(5)]
    events = _make_events(
        tool_calls_per_turn=[["view"]] * 5,
        user_prompts=prompts,
    )
    for e in events:
        if e.get("type") == "tool.execution_start" and e["data"].get("toolName") == "view":
            e["data"]["arguments"] = {"path": "/project/server.py"}
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "Context rot signal" in output
    assert "server.py" in output


def test_format_session_analysis_shows_high_volume_warning():
    events = _make_events(tool_calls_per_turn=[["view"]])
    # 250 KB result → should trigger high volume warning
    events.append({
        "type": "tool.execution_complete",
        "data": {"toolCallId": "tc00", "result": {"content": "x" * 250_000}},
        "timestamp": events[-1]["timestamp"],
    })
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "context window filling" in output or "very high context load" in output


def test_analyze_session_events_session_health_clean():
    events = _make_events()
    result = _analyze_session_events(events)
    assert result["session_health_score"] == 0
    assert result["session_health_signals"] == []


def test_analyze_session_events_system_prompt_overhead_present():
    """system_prompt_* fields present even with no system.message events."""
    events = _make_events()
    result = _analyze_session_events(events)
    assert "system_prompt_turns" in result
    assert "system_prompt_avg_kb" in result
    assert "system_prompt_total_kb" in result
    assert result["system_prompt_turns"] == 0
    assert result["system_prompt_total_kb"] == 0.0


def test_analyze_session_events_system_prompt_overhead_tracked():
    """system.message events are measured correctly."""
    from datetime import timezone as _tz
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=_tz.utc)
    events = _make_events()
    # Inject two system.message events of 10 KB each
    for _ in range(2):
        events.append({
            "type": "system.message",
            "data": {"content": "x" * 10_240},  # 10 KB
            "timestamp": now.isoformat().replace("+00:00", "Z"),
        })
    result = _analyze_session_events(events)
    assert result["system_prompt_turns"] == 2
    assert result["system_prompt_avg_kb"] == 10.0
    assert result["system_prompt_total_kb"] == 20.0


def test_format_session_analysis_shows_system_prompt_line():
    """System prompt overhead line appears in Context Volume & Rot section."""
    from datetime import timezone as _tz
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=_tz.utc)
    events = _make_events()
    events.append({
        "type": "system.message",
        "data": {"content": "x" * 10_240},
        "timestamp": now.isoformat().replace("+00:00", "Z"),
    })
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "System prompt" in output
    assert "always-on" in output


def test_format_session_analysis_growth_warning_when_large_delta():
    """Growth warning fires when max-min >= 2 KB."""
    events = _make_events()
    analysis = _analyze_session_events(events)
    # Simulate growth signal manually
    analysis["system_prompt_turns"] = 10
    analysis["system_prompt_avg_kb"] = 34.0
    analysis["system_prompt_total_kb"] = 340.0
    analysis["system_prompt_min_kb"] = 32.0
    analysis["system_prompt_max_kb"] = 36.0
    analysis["system_prompt_growth_kb"] = 4.0
    output = _format_session_analysis(analysis)
    assert "grew" in output.lower()
    assert "4.0 KB" in output



def test_analyze_session_events_session_health_long_duration():
    """duration_min > 90 triggers health signal."""
    from datetime import timezone as _tz
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=_tz.utc)
    old = datetime(2026, 4, 18, 9, 0, 0, tzinfo=_tz.utc)  # 3 hours earlier
    events = _make_events()
    events[0]["timestamp"] = old.isoformat().replace("+00:00", "Z")
    result = _analyze_session_events(events)
    assert result["session_health_score"] >= 1
    assert any("duration" in s.lower() for s in result["session_health_signals"])


def test_format_session_analysis_shows_health_section():
    events = _make_events()
    analysis = _analyze_session_events(events)
    output = _format_session_analysis(analysis)
    assert "Session Health" in output
    assert "healthy" in output.lower()


def test_format_session_analysis_stagnation_warning_when_high_score():
    """Manually craft an analysis dict with score=3 to check stagnation output."""
    events = _make_events()
    analysis = _analyze_session_events(events)
    # Override health fields to simulate a degraded session
    analysis["session_health_score"] = 3
    analysis["session_health_signals"] = [
        "Long duration: 200 min",
        "High turn count: 60 turns",
        "Heavy context load: 350 KB loaded",
    ]
    output = _format_session_analysis(analysis)
    assert "stagnation risk" in output.lower()
    assert "Start a new session" in output
    assert "Superpowers" in output
    assert "GSD" in output
    assert "Spec-Kit" in output


# ---------------------------------------------------------------------------
# _find_active_session_id() — unit test
# ---------------------------------------------------------------------------

def test_find_active_session_id_returns_string_or_none():
    result = _find_active_session_id()
    assert result is None or isinstance(result, str)


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
    assert "No session" in text or "zzzzzzz" in text


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
    assert "No sessions" in text or "1999-01" in text


async def test_copilot_behavior_report_has_sections(client):
    result = await client.call_tool("copilot_behavior_report", {"last": 5})
    text = _result_text(result)
    has_report    = "Behaviour Report" in text and "Recommendations" in text
    has_no_data   = "No sessions" in text or "No Copilot" in text
    assert has_report or has_no_data


def test_copilot_behavior_report_low_sample_disclaimer(tmp_path, monkeypatch):
    import server as _srv
    import core.loaders
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)
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
    import server as _srv
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)

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
    import server as _srv

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
    import server as _srv
    monkeypatch.setenv("GH_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_USER", "testuser")
    monkeypatch.setattr(_srv._config, "load", lambda: {
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
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)

    result = _record_copilot_spend({"amount": 17.72, "month": "2026-04"})
    assert "17.72" in result
    assert "2026-04" in result

    cfg = _cfg.load()
    assert cfg["copilot_spend_history"]["2026-04"] == 17.72


def test_record_copilot_spend_shows_budget_bar(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_overage_budget": 25.0})

    result = _record_copilot_spend({"amount": 17.72, "month": "2026-04"})
    assert "█" in result
    assert "Remaining" in result


def test_record_copilot_spend_defaults_to_current_month(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg
    from datetime import date

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)

    result = _record_copilot_spend({"amount": 5.0})
    assert date.today().strftime("%Y-%m") in result


# ---------------------------------------------------------------------------
# _copilot_budget_forecast unit tests
# ---------------------------------------------------------------------------

def test_copilot_budget_forecast_no_recorded_spend(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_overage_budget": 25.0})

    # Patch load_copilot_sessions to return something
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 5000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 5},
    ])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "record_copilot_spend" in result


def test_copilot_budget_forecast_no_overage_budget(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_spend_history": {"2026-04": 18.0}})

    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
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
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)

    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "No Copilot CLI session data" in result


def test_copilot_budget_forecast_shows_projection(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg
    from datetime import date

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 18.0},
    })

    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 40,
         "output_tokens": 100_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 30},
    ])
    # No events to analyze (sessions dir absent or empty) — waste section omitted
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path / "nonexistent")

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "Burn Rate" in result
    assert "18.00" in result
    assert "25.00" in result


def test_copilot_budget_forecast_waste_section(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 18.0},
    })

    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
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

    monkeypatch.setattr(_srv, "_analyze_session_events", fake_analyze)

    # Create a fake session dir with a dummy events.jsonl
    session_dir = tmp_path / "s1"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_text('{"type":"session.start"}\n')
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)
    monkeypatch.setattr(_srv, "load_copilot_session_events", lambda p: [{"type": "session.start"}])

    result = _copilot_budget_forecast({"month": "2026-04"})
    assert "Behavior Waste" in result
    assert "Total estimated waste" in result


def test_copilot_budget_forecast_low_days_disclaimer(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg
    from datetime import date
    from unittest.mock import patch

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 2.0},
    })

    # Simulate being on day 3 of the month
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
        {"session_id": "s1", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 10},
    ])
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path / "nonexistent")

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 4, 3)

    with patch("server.date", FakeDate):
        result = _copilot_budget_forecast({"month": "2026-04"})
    assert "⚠️  Low burn rate confidence" in result


def test_copilot_budget_forecast_no_disclaimer_after_7_days(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg
    from datetime import date
    from unittest.mock import patch

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({
        **_cfg.DEFAULTS,
        "copilot_overage_budget": 25.0,
        "copilot_spend_history": {"2026-04": 10.0},
    })

    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
        {"session_id": f"s{i}", "date": f"2026-04-{i+1:02d} 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 10}
        for i in range(7)
    ])
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path / "nonexistent")

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 4, 8)

    with patch("server.date", FakeDate):
        result = _copilot_budget_forecast({"month": "2026-04"})
    assert "⚠️  Low burn rate confidence" not in result


# ---------------------------------------------------------------------------
# _copilot_tool_impact unit tests
# ---------------------------------------------------------------------------

def test_copilot_tool_impact_no_tool():
    result = _copilot_tool_impact({"tool": ""})
    assert "Please provide" in result


def test_copilot_tool_impact_no_sessions(monkeypatch):
    import server as _srv
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [])
    result = _copilot_tool_impact({"tool": "serena"})
    assert "No Copilot CLI session data found" in result


def test_copilot_tool_impact_no_matching_sessions(tmp_path, monkeypatch):
    import server as _srv
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
        {"session_id": "abc12345", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 50_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 15},
    ])
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)
    result = _copilot_tool_impact({"tool": "serena"})
    assert "No Copilot CLI sessions found" in result
    assert "serena" in result


def test_copilot_tool_impact_with_matching_session(tmp_path, monkeypatch):
    import server as _srv
    import core.loaders
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_spend_history": {"2026-04": 18.0}})

    sessions = [
        {"session_id": "aaa00001", "date": "2026-04-01 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj-a", "duration_min": 10},
        {"session_id": "bbb00002", "date": "2026-04-02 11:00", "turns": 20,
         "output_tokens": 80_000, "model": "claude-sonnet-4.6",
         "project": "proj-b", "duration_min": 20},
    ]
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: sessions)

    # Session aaa00001 uses serena; bbb00002 does not
    session_dir = tmp_path / "aaa00001"
    session_dir.mkdir()
    serena_event = json.dumps({
        "type": "tool.execution_start",
        "data": {"toolName": "mcp__serena__find_symbol", "arguments": {}, "toolCallId": "t1"},
    })
    (session_dir / "events.jsonl").write_text(serena_event + "\n")
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)

    result = _copilot_tool_impact({"tool": "serena", "month": "2026-04"})
    assert "Copilot Tool Impact" in result
    assert "serena" in result
    assert "Sessions" in result
    assert "⚠️  Low sample size" in result


def test_copilot_tool_impact_low_sample_disclaimer_absent_when_sufficient(tmp_path, monkeypatch):
    import server as _srv
    import config as _cfg

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(_cfg, "CONFIG_PATH", config_file)
    monkeypatch.setattr(_srv._config, "CONFIG_PATH", config_file)
    _cfg.save({**_cfg.DEFAULTS, "copilot_spend_history": {"2026-04": 18.0}})

    # 10 sessions all using serena — should NOT show disclaimer
    sessions = [
        {"session_id": f"s{i:07d}", "date": f"2026-04-{i+1:02d} 10:00", "turns": 10,
         "output_tokens": 30_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 10}
        for i in range(10)
    ]
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: sessions)

    for s in sessions:
        session_dir = tmp_path / s["session_id"]
        session_dir.mkdir()
        event = json.dumps({
            "type": "tool.execution_start",
            "data": {"toolName": "mcp__serena__find_symbol", "arguments": {}, "toolCallId": "t1"},
        })
        (session_dir / "events.jsonl").write_text(event + "\n")
    monkeypatch.setattr(_srv, "COPILOT_SESSIONS_PATH", tmp_path)

    result = _copilot_tool_impact({"tool": "serena", "month": "2026-04"})
    assert "⚠️  Low sample size" not in result


def test_copilot_tool_impact_invalid_month(monkeypatch):
    import server as _srv
    monkeypatch.setattr(_srv, "load_copilot_sessions", lambda: [
        {"session_id": "aaa00001", "date": "2026-04-01 10:00", "turns": 5,
         "output_tokens": 10_000, "model": "claude-sonnet-4.6",
         "project": "proj", "duration_min": 5},
    ])
    result = _copilot_tool_impact({"tool": "bash", "month": "2099-12"})
    assert "No Copilot CLI session data found for" in result
