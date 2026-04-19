"""Tests for core/loaders.py."""

from core.loaders import (
    load_copilot_sessions,
    load_copilot_session_events,
    _find_active_session_id,
)


# ---------------------------------------------------------------------------
# load_copilot_sessions() unit tests
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
# _find_active_session_id() unit test
# ---------------------------------------------------------------------------

def test_find_active_session_id_returns_string_or_none():
    result = _find_active_session_id()
    assert result is None or isinstance(result, str)
