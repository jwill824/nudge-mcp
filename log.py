#!/usr/bin/env python3
"""
Stop hook: log Claude Code session costs to CSV (Scrooge).

Runs automatically when a session ends via the Stop hook in ~/.claude/settings.json.
Reads session JSONL, sums token usage, and appends one row to sessions.csv.
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
CSV_PATH = Path.home() / ".config" / "scrooge" / "sessions.csv"
CSV_HEADERS = [
    "date", "session_id", "project", "branch",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens",
    "total_tokens", "cache_hit_pct", "est_cost_usd", "duration_min", "turns",
    "tools",
]


def find_jsonl(session_id: str, transcript_path: str) -> Path | None:
    if transcript_path:
        p = Path(transcript_path)
        if p.exists():
            return p
    for p in Path.home().glob(".claude/projects/**/*.jsonl"):
        if session_id in p.stem:
            return p
    return None


def parse_jsonl(path: Path) -> dict:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                pass

    assistant_msgs = [e for e in entries if e.get("type") == "assistant"]

    input_tok = output_tok = cache_read_tok = cache_create_tok = 0
    tools_used = set()
    for e in assistant_msgs:
        usage = e.get("message", {}).get("usage", {})
        input_tok        += usage.get("input_tokens", 0)
        output_tok       += usage.get("output_tokens", 0)
        cache_read_tok   += usage.get("cache_read_input_tokens", 0)
        cache_create_tok += usage.get("cache_creation_input_tokens", 0)
        for block in e.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                tools_used.add(block.get("name", ""))

    # Pull cwd and branch from the first entry that has them
    cwd = branch = ""
    for e in entries:
        if not cwd and e.get("cwd"):
            cwd = e["cwd"]
        if not branch and e.get("gitBranch"):
            branch = e["gitBranch"]

    project = Path(cwd).name if cwd else "unknown"

    # Duration and date from timestamps
    timestamps = sorted(
        datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        for e in entries if "timestamp" in e
    )
    date_str = timestamps[0].strftime("%Y-%m-%d %H:%M") if timestamps else ""
    duration_min = (
        round((timestamps[-1] - timestamps[0]).total_seconds() / 60, 1)
        if len(timestamps) >= 2 else 0.0
    )

    total_tok = input_tok + output_tok + cache_read_tok + cache_create_tok
    denom = cache_read_tok + input_tok
    cache_hit_pct = round(cache_read_tok / denom * 100, 1) if denom > 0 else 0.0

    return {
        "date": date_str,
        "project": project[:12],
        "branch": branch,
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "cache_read_tokens": cache_read_tok,
        "cache_create_tokens": cache_create_tok,
        "total_tokens": total_tok,
        "cache_hit_pct": cache_hit_pct,
        "turns": len(assistant_msgs),
        "duration_min": duration_min,
        "tools": " ".join(sorted(tools_used)),
    }


def estimate_cost(data: dict) -> float:
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from pricing import estimate_cost as _ec
        return round(_ec({
            "input":        data["input_tokens"],
            "output":       data["output_tokens"],
            "cache_read":   data["cache_read_tokens"],
            "cache_create": data["cache_create_tokens"],
        }), 4)
    except Exception:
        # Fallback: hardcoded list prices with April 2026 discount factor
        factor = 0.5868
        LIST = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75}
        return round((
            data["input_tokens"]        * LIST["input"] +
            data["output_tokens"]       * LIST["output"] +
            data["cache_read_tokens"]   * LIST["cache_read"] +
            data["cache_create_tokens"] * LIST["cache_create"]
        ) * factor / 1_000_000, 4)


def main():
    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        event = {}

    session_id = event.get("session_id", "")
    transcript_path = event.get("transcript_path")

    if not session_id:
        sys.exit(0)

    jsonl = find_jsonl(session_id, transcript_path)
    if not jsonl:
        sys.exit(0)

    data = parse_jsonl(jsonl)

    if data["turns"] == 0:
        sys.exit(0)

    cost = estimate_cost(data)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not CSV_PATH.exists()

    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "date":               data["date"],
            "session_id":         session_id[:8],
            "project":            data["project"],
            "branch":             data["branch"],
            "input_tokens":       data["input_tokens"],
            "output_tokens":      data["output_tokens"],
            "cache_read_tokens":  data["cache_read_tokens"],
            "cache_create_tokens": data["cache_create_tokens"],
            "total_tokens":       data["total_tokens"],
            "cache_hit_pct":      data["cache_hit_pct"],
            "est_cost_usd":       cost,
            "duration_min":       data["duration_min"],
            "turns":              data["turns"],
            "tools":              data["tools"],
        })


if __name__ == "__main__":
    main()
