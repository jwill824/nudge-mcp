import sys
import os

# Ensure lib/ (vendored dependencies) is on the path for all tests
_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
sys.path.insert(0, _ROOT)

import csv
import json
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
import core.loaders

from server import mcp


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


SAMPLE_COPILOT_EVENTS = [
    {
        "type": "session.start",
        "timestamp": "2026-04-01T10:00:00.000Z",
        "data": {
            "sessionId": "abc12345-0000-0000-0000-000000000001",
            "context": {"cwd": "/home/user/scrooge", "branch": "main"},
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


@pytest.fixture
def fake_csv(tmp_path, monkeypatch):
    """Write sample CSV rows to a temp file and patch CSV_PATH in core.loaders."""
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
