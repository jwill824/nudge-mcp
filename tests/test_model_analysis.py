"""Tests for core/model_analysis.py."""

from core.model_analysis import (
    group_turns,
    score_turn_complexity,
    classify_model_fit,
    estimate_savings,
    analyze_session_model_usage,
)


def _evt(type_, data=None, timestamp="2026-04-01T10:00:00.000Z"):
    return {"type": type_, "data": data or {}, "timestamp": timestamp}


# ---------------------------------------------------------------------------
# group_turns
# ---------------------------------------------------------------------------

class TestGroupTurns:
    def test_empty_events(self):
        assert group_turns([]) == []

    def test_single_turn(self):
        events = [
            _evt("user.message", {"content": "hello world"}),
            _evt("assistant.message", {"outputTokens": 500}),
        ]
        turns = group_turns(events)
        assert len(turns) == 1
        assert turns[0]["prompt_content"] == "hello world"
        assert turns[0]["output_tokens"] == 500
        assert turns[0]["model"] == ""

    def test_multiple_turns(self):
        events = [
            _evt("user.message", {"content": "first"}),
            _evt("assistant.message", {"outputTokens": 100}),
            _evt("user.message", {"content": "second"}),
            _evt("assistant.message", {"outputTokens": 200}),
        ]
        turns = group_turns(events)
        assert len(turns) == 2
        assert turns[0]["prompt_content"] == "first"
        assert turns[0]["output_tokens"] == 100
        assert turns[1]["prompt_content"] == "second"
        assert turns[1]["output_tokens"] == 200

    def test_tool_calls_accumulate(self):
        events = [
            _evt("user.message", {"content": "do work"}),
            _evt("tool.execution_start", {"toolName": "bash"}),
            _evt("tool.execution_start", {"toolName": "view"}),
            _evt("tool.execution_start", {"toolName": "view"}),  # duplicate tool name
        ]
        turns = group_turns(events)
        assert turns[0]["tool_call_count"] == 3
        assert set(turns[0]["unique_tools"]) == {"bash", "view"}

    def test_model_change_before_first_turn(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "hello"}),
        ]
        turns = group_turns(events)
        assert turns[0]["model"] == "claude-sonnet-4.6"

    def test_model_change_between_turns(self):
        events = [
            _evt("user.message", {"content": "first"}),
            _evt("session.model_change", {"newModel": "claude-haiku-4.5"}),
            _evt("user.message", {"content": "second"}),
        ]
        turns = group_turns(events)
        assert turns[0]["model"] == ""
        assert turns[1]["model"] == "claude-haiku-4.5"

    def test_no_user_messages_returns_empty(self):
        events = [
            _evt("session.start", {}),
            _evt("assistant.message", {"outputTokens": 100}),
        ]
        assert group_turns(events) == []

    def test_default_model_applied_before_first_change(self):
        events = [
            _evt("user.message", {"content": "hi"}),
        ]
        turns = group_turns(events, default_model="claude-sonnet-4.6")
        assert turns[0]["model"] == "claude-sonnet-4.6"


# ---------------------------------------------------------------------------
# score_turn_complexity
# ---------------------------------------------------------------------------

class TestScoreTurnComplexity:
    def _turn(self, prompt="", output_tokens=0, tool_call_count=0, unique_tools=None):
        return {
            "prompt_content": prompt,
            "output_tokens": output_tokens,
            "tool_call_count": tool_call_count,
            "unique_tools": unique_tools or [],
        }

    def test_all_zeros(self):
        assert score_turn_complexity(self._turn()) == 0

    def test_short_prompt_scores_zero(self):
        assert score_turn_complexity(self._turn(prompt="short")) == 0

    def test_medium_prompt_scores_one(self):
        assert score_turn_complexity(self._turn(prompt="x" * 50)) == 1
        assert score_turn_complexity(self._turn(prompt="x" * 299)) == 1

    def test_long_prompt_scores_two(self):
        assert score_turn_complexity(self._turn(prompt="x" * 300)) == 2

    def test_medium_output_tokens_scores_one(self):
        assert score_turn_complexity(self._turn(output_tokens=500)) == 1
        assert score_turn_complexity(self._turn(output_tokens=1999)) == 1

    def test_high_output_tokens_scores_two(self):
        assert score_turn_complexity(self._turn(output_tokens=2000)) == 2

    def test_single_tool_call_scores_one(self):
        assert score_turn_complexity(self._turn(tool_call_count=1)) == 1
        assert score_turn_complexity(self._turn(tool_call_count=4)) == 1

    def test_many_tool_calls_scores_two(self):
        assert score_turn_complexity(self._turn(tool_call_count=5)) == 2

    def test_two_to_three_unique_tools_scores_one(self):
        assert score_turn_complexity(self._turn(unique_tools=["bash", "view"])) == 1
        assert score_turn_complexity(self._turn(unique_tools=["bash", "view", "grep"])) == 1

    def test_four_plus_unique_tools_scores_two(self):
        assert score_turn_complexity(
            self._turn(unique_tools=["bash", "view", "grep", "glob"])
        ) == 2

    def test_max_score_is_eight(self):
        score = score_turn_complexity(self._turn(
            prompt="x" * 300, output_tokens=2000, tool_call_count=5,
            unique_tools=["bash", "view", "grep", "glob"],
        ))
        assert score == 8

    def test_continuation_capped_at_one_even_with_heavy_activity(self):
        score = score_turn_complexity({
            "prompt_content": "yes",
            "output_tokens": 5000,
            "tool_call_count": 10,
            "unique_tools": ["bash", "view", "grep", "glob", "create"],
        })
        assert score == 1

    def test_continuation_with_zero_activity_scores_zero(self):
        assert score_turn_complexity(self._turn(prompt="ok")) == 0

    def test_all_continuation_phrases_are_capped(self):
        continuations = ["yes", "no", "ok", "sure", "proceed", "continue",
                         "go ahead", "do it", "yes please", "sounds good",
                         "perfect", "great"]
        for phrase in continuations:
            score = score_turn_complexity({
                "prompt_content": phrase,
                "output_tokens": 5000,
                "tool_call_count": 10,
                "unique_tools": ["bash", "view", "grep", "glob"],
            })
            assert score <= 1, f"'{phrase}' scored {score}, expected <= 1"


# ---------------------------------------------------------------------------
# classify_model_fit
# ---------------------------------------------------------------------------

class TestClassifyModelFit:
    def test_sonnet_simple_is_over(self):
        assert classify_model_fit(0, "claude-sonnet-4.6") == "over"
        assert classify_model_fit(2, "claude-sonnet-4.6") == "over"

    def test_sonnet_moderate_is_fit(self):
        assert classify_model_fit(3, "claude-sonnet-4.6") == "fit"
        assert classify_model_fit(5, "claude-sonnet-4.6") == "fit"

    def test_sonnet_complex_is_fit(self):
        assert classify_model_fit(6, "claude-sonnet-4.6") == "fit"
        assert classify_model_fit(8, "claude-sonnet-4.6") == "fit"

    def test_opus_simple_is_over(self):
        assert classify_model_fit(0, "claude-opus-4.6") == "over"

    def test_haiku_simple_is_fit(self):
        assert classify_model_fit(0, "claude-haiku-4.5") == "fit"
        assert classify_model_fit(5, "claude-haiku-4.5") == "fit"

    def test_haiku_complex_is_under(self):
        assert classify_model_fit(6, "claude-haiku-4.5") == "under"
        assert classify_model_fit(8, "claude-haiku-4.5") == "under"

    def test_unknown_model_is_fit(self):
        assert classify_model_fit(0, "") == "fit"
        assert classify_model_fit(8, "some-unknown-model") == "fit"

    def test_model_name_with_dots_normalised(self):
        assert classify_model_fit(0, "claude-sonnet-4.6") == "over"
        assert classify_model_fit(0, "claude-sonnet-4-6") == "over"


# ---------------------------------------------------------------------------
# estimate_savings
# ---------------------------------------------------------------------------

class TestEstimateSavings:
    def _turn(self, verdict, output_tokens=1000):
        return {"verdict": verdict, "output_tokens": output_tokens}

    def test_no_over_turns_gives_zero_savings(self):
        result = estimate_savings([self._turn("fit"), self._turn("under")])
        assert result["savings_usd"] == 0.0
        assert result["over_powered_turns"] == 0
        assert result["total_turns"] == 2

    def test_one_million_over_tokens_saves_eleven_dollars(self):
        result = estimate_savings([self._turn("over", 1_000_000)])
        assert abs(result["savings_usd"] - 11.0) < 0.01

    def test_mixed_turns_only_counts_over(self):
        turns = [self._turn("over", 1_000_000), self._turn("fit", 500_000)]
        result = estimate_savings(turns)
        assert result["over_powered_turns"] == 1
        assert result["total_turns"] == 2
        assert abs(result["savings_usd"] - 11.0) < 0.01

    def test_empty_turns(self):
        result = estimate_savings([])
        assert result["savings_usd"] == 0.0
        assert result["over_powered_turns"] == 0
        assert result["total_turns"] == 0


# ---------------------------------------------------------------------------
# analyze_session_model_usage
# ---------------------------------------------------------------------------

class TestAnalyzeSessionModelUsage:
    def test_adds_complexity_and_verdict_fields(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "hello"}),
            _evt("assistant.message", {"outputTokens": 100}),
        ]
        turns = analyze_session_model_usage(events)
        assert len(turns) == 1
        assert "complexity" in turns[0]
        assert "verdict" in turns[0]

    def test_simple_sonnet_turn_is_over(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "hi"}),
            _evt("assistant.message", {"outputTokens": 100}),
        ]
        turns = analyze_session_model_usage(events)
        assert turns[0]["verdict"] == "over"

    def test_complex_sonnet_turn_is_fit(self):
        events = [
            _evt("session.model_change", {"newModel": "claude-sonnet-4.6"}),
            _evt("user.message", {"content": "x" * 300}),
            _evt("assistant.message", {"outputTokens": 3000}),
            _evt("tool.execution_start", {"toolName": "bash"}),
            _evt("tool.execution_start", {"toolName": "view"}),
            _evt("tool.execution_start", {"toolName": "grep"}),
            _evt("tool.execution_start", {"toolName": "glob"}),
            _evt("tool.execution_start", {"toolName": "edit"}),
        ]
        turns = analyze_session_model_usage(events)
        assert turns[0]["verdict"] == "fit"

    def test_no_model_change_uses_default(self):
        events = [
            _evt("user.message", {"content": "hi"}),
        ]
        turns = analyze_session_model_usage(events, default_model="claude-sonnet-4.6")
        assert turns[0]["model"] == "claude-sonnet-4.6"
        assert turns[0]["verdict"] == "over"
