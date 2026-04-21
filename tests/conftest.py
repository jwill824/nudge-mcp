import sys
import os

# Ensure lib/ (vendored dependencies) is on the path for all tests
_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
sys.path.insert(0, _ROOT)

import json
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
import core.loaders

from server import mcp


def _result_text(result) -> str:
    return result.data if hasattr(result, "data") else str(result)


# ---------------------------------------------------------------------------
# Sample Claude session data — used by fake_claude_sessions fixture
# Each entry is a list of JSONL lines for one session file.
# Sorted in load order: proj-gamma (Mar), proj-alpha (Apr 1), proj-beta (Apr 2)
# ---------------------------------------------------------------------------

_ALPHA_JSONL = [
    {"type": "user",      "timestamp": "2026-04-01T10:00:00.000Z", "cwd": "/home/user/proj-alpha", "gitBranch": "main"},
    {"type": "assistant", "timestamp": "2026-04-01T10:15:00.000Z", "message": {
        "usage": {"input_tokens": 2000, "output_tokens": 1000, "cache_read_input_tokens": 8000, "cache_creation_input_tokens": 500},
        "content": [{"type": "tool_use", "name": "Read", "input": {}}, {"type": "tool_use", "name": "Grep", "input": {}}],
    }},
]

_BETA_JSONL = [
    {"type": "user",      "timestamp": "2026-04-02T11:00:00.000Z", "cwd": "/home/user/proj-beta", "gitBranch": "main"},
    {"type": "assistant", "timestamp": "2026-04-02T11:08:00.000Z", "message": {
        "usage": {"input_tokens": 4000, "output_tokens": 1000, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 500},
        "content": [{"type": "tool_use", "name": "Bash", "input": {}}],
    }},
]

_GAMMA_JSONL = [
    {"type": "user",      "timestamp": "2026-03-15T09:00:00.000Z", "cwd": "/home/user/proj-gamma", "gitBranch": "main"},
    {"type": "assistant", "timestamp": "2026-03-15T09:05:00.000Z", "message": {
        "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 200},
        "content": [{"type": "tool_use", "name": "Read", "input": {}}],
    }},
]


SAMPLE_COPILOT_EVENTS = [
    {
        "type": "session.start",
        "timestamp": "2026-04-01T10:00:00.000Z",
        "data": {
            "sessionId": "abc12345-0000-0000-0000-000000000001",
            "context": {"cwd": "/home/user/nudge-mcp", "branch": "main"},
        },
    },
    {"type": "user.message",      "timestamp": "2026-04-01T10:01:00.000Z", "data": {}},
    {"type": "tool.execution_start", "timestamp": "2026-04-01T10:01:10.000Z",
     "data": {"toolName": "view", "arguments": {}}},
    {"type": "assistant.message", "timestamp": "2026-04-01T10:01:30.000Z",
     "data": {"outputTokens": 1200}},
    {"type": "user.message",      "timestamp": "2026-04-01T10:02:00.000Z", "data": {}},
    {"type": "assistant.message", "timestamp": "2026-04-01T10:02:30.000Z",
     "data": {"outputTokens": 800}},
]


@pytest.fixture
def fake_copilot_sessions(tmp_path, monkeypatch):
    """Create a fake ~/.copilot/session-state/ with April 2026 data and patch the path."""
    session_dir = tmp_path / "abc12345-0000-0000-0000-000000000001"
    session_dir.mkdir()
    with open(session_dir / "events.jsonl", "w") as f:
        for event in SAMPLE_COPILOT_EVENTS:
            f.write(json.dumps(event) + "\n")
    monkeypatch.setattr(core.loaders, "COPILOT_SESSIONS_PATH", tmp_path)
    return tmp_path


def _write_jsonl(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for entry in lines:
            f.write(json.dumps(entry) + "\n")


@pytest.fixture
def fake_claude_sessions(tmp_path, monkeypatch):
    """Create fake ~/.claude/projects/ JSONL sessions and patch CLAUDE_PROJECTS_PATH.

    Three sessions:
      proj-alpha — 2026-04-01, tools: Read Grep
      proj-beta  — 2026-04-02, tool:  Bash
      proj-gamma — 2026-03-15, tool:  Read
    """
    _write_jsonl(tmp_path / "proj-alpha" / "aaaaaaaa-0000-0000-0000-000000000001.jsonl", _ALPHA_JSONL)
    _write_jsonl(tmp_path / "proj-beta"  / "bbbbbbbb-0000-0000-0000-000000000002.jsonl", _BETA_JSONL)
    _write_jsonl(tmp_path / "proj-gamma" / "cccccccc-0000-0000-0000-000000000003.jsonl", _GAMMA_JSONL)
    monkeypatch.setattr(core.loaders, "CLAUDE_PROJECTS_PATH", tmp_path)
    return tmp_path


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c
