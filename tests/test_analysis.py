"""Tests for core/analysis.py."""

import pytest
from datetime import datetime, timezone as _tz

from core.analysis import _analyze_session_events, _format_session_analysis


def _make_events(
    user_prompts: list[str] | None = None,
    tool_calls_per_turn: list[list[str]] | None = None,
    store_memory: bool = False,
    bash_commands: list[str] | None = None,
) -> list[dict]:
    """Build a minimal synthetic events.jsonl list for testing."""
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


def test_analyze_session_events_includes_model_data():
    events = _make_events(["hello world"])
    result = _analyze_session_events(events)
    assert "model_turns" in result
    assert "model_over_count" in result
    assert "model_total_turns" in result
    assert "model_savings_usd" in result
    assert "model_efficiency_score" in result
    assert isinstance(result["model_turns"], list)
    assert isinstance(result["model_efficiency_score"], int)


def test_format_session_analysis_includes_model_usage_section():
    events = _make_events(["hello world"])
    analysis = _analyze_session_events(events)
    formatted = _format_session_analysis(analysis)
    assert "### Model Usage" in formatted
