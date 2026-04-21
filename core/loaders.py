"""
Data loading layer for Nudge.

Responsibilities:
  - Path constants for data directories
  - Loading Claude session data from JSONL
  - Loading Copilot CLI session-state from disk
  - Loading Copilot session events from JSONL
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

CLAUDE_PROJECTS_PATH = Path.home() / ".claude" / "projects"
COPILOT_SESSIONS_PATH = Path.home() / ".copilot" / "session-state"
COPILOT_CONFIG_PATH = Path.home() / ".copilot" / "config.json"


def fmt(n: int) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def load_claude_sessions() -> list[dict]:
    """Parse all Claude Code sessions from ~/.claude/projects/*/*.jsonl."""
    if not CLAUDE_PROJECTS_PATH.exists():
        return []

    try:
        import config as _cfg
        discount = _cfg.load().get("discount_factor", 0.5868)
    except Exception:
        discount = 0.5868

    from pricing import estimate_cost

    sessions = []
    for project_dir in CLAUDE_PROJECTS_PATH.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
            entries = []
            try:
                with open(jsonl_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass
            except Exception:
                continue

            assistant_entries = [e for e in entries if e.get("type") == "assistant"]
            if not assistant_entries:
                continue

            # Accumulate tokens per model for accurate per-model cost calculation
            tokens_by_model: dict[str, dict[str, int]] = {}
            tools_used: set[str] = set()
            for e in assistant_entries:
                msg = e.get("message", {})
                model = msg.get("model") or "claude-sonnet-4-6"
                if model == "<synthetic>":
                    continue
                usage = msg.get("usage", {})
                if model not in tokens_by_model:
                    tokens_by_model[model] = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
                tokens_by_model[model]["input"]        += usage.get("input_tokens", 0)
                tokens_by_model[model]["output"]       += usage.get("output_tokens", 0)
                tokens_by_model[model]["cache_read"]   += usage.get("cache_read_input_tokens", 0)
                tokens_by_model[model]["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        name = block.get("name", "")
                        if name:
                            tools_used.add(name)

            if not tokens_by_model:
                continue

            # Aggregate totals and cost across all models
            input_tok = sum(v["input"] for v in tokens_by_model.values())
            output_tok = sum(v["output"] for v in tokens_by_model.values())
            cache_read_tok = sum(v["cache_read"] for v in tokens_by_model.values())
            cache_create_tok = sum(v["cache_create"] for v in tokens_by_model.values())

            # Primary model = the one with the most output tokens
            primary_model = max(tokens_by_model, key=lambda m: tokens_by_model[m]["output"])
            models_used = sorted(tokens_by_model.keys())

            cwd = branch = ""
            for e in entries:
                if not cwd and e.get("cwd"):
                    cwd = e["cwd"]
                if not branch and e.get("gitBranch"):
                    branch = e["gitBranch"]

            project = Path(cwd).name[:12] if cwd else project_dir.name[:12]

            timestamps = sorted(
                datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                for e in entries if "timestamp" in e
            )
            if not timestamps:
                continue

            date_str = timestamps[0].strftime("%Y-%m-%d %H:%M")
            duration_min = (
                round((timestamps[-1] - timestamps[0]).total_seconds() / 60, 1)
                if len(timestamps) >= 2 else 0.0
            )

            total_tok = input_tok + output_tok + cache_read_tok + cache_create_tok
            denom = cache_read_tok + input_tok
            cache_hit_pct = round(cache_read_tok / denom * 100, 1) if denom > 0 else 0.0
            turns = len([e for e in assistant_entries if (e.get("message", {}).get("model") or "") != "<synthetic>"])

            try:
                cost = round(sum(
                    estimate_cost(toks, model=model, discount=discount)
                    for model, toks in tokens_by_model.items()
                ), 4)
            except Exception:
                cost = 0.0

            sessions.append({
                "date":                date_str,
                "session_id":          session_id[:8],
                "project":             project,
                "branch":              branch,
                "model":               primary_model,
                "models":              models_used,
                "input_tokens":        input_tok,
                "output_tokens":       output_tok,
                "cache_read_tokens":   cache_read_tok,
                "cache_create_tokens": cache_create_tok,
                "total_tokens":        total_tok,
                "cache_hit_pct":       cache_hit_pct,
                "est_cost_usd":        cost,
                "duration_min":        duration_min,
                "turns":               turns,
                "tools":               " ".join(sorted(tools_used)),
                "jsonl_path":          str(jsonl_file),
            })

    sessions.sort(key=lambda x: x["date"])
    return sessions


def _default_copilot_model() -> str:
    """Read the configured model from ~/.copilot/config.json."""
    try:
        with open(COPILOT_CONFIG_PATH) as f:
            return json.load(f).get("model", "claude-sonnet-4.6")
    except Exception:
        return "claude-sonnet-4.6"


def load_copilot_sessions() -> list[dict]:
    """Parse all Copilot CLI sessions from ~/.copilot/session-state/*/events.jsonl."""
    if not COPILOT_SESSIONS_PATH.exists():
        return []

    sessions = []
    for session_dir in COPILOT_SESSIONS_PATH.iterdir():
        events_file = session_dir / "events.jsonl"
        if not events_file.exists():
            continue

        events = []
        try:
            with open(events_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            continue

        if not events:
            continue

        session_start = next((e for e in events if e.get("type") == "session.start"), None)
        if not session_start:
            continue

        start_data = session_start.get("data", {})
        context = start_data.get("context", {})

        timestamps = sorted(
            datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
            for e in events if "timestamp" in e
        )
        if not timestamps:
            continue

        output_tokens = sum(
            e.get("data", {}).get("outputTokens", 0)
            for e in events if e.get("type") == "assistant.message"
        )
        turns = sum(1 for e in events if e.get("type") == "user.message")
        tools_used = {
            e.get("data", {}).get("toolName", "")
            for e in events if e.get("type") == "tool.execution_start"
        } - {""}

        # Pick up last model from session.model_change, else fall back to config
        model = _default_copilot_model()
        for e in events:
            if e.get("type") == "session.model_change":
                model = e.get("data", {}).get("newModel", model)

        duration_min = (
            round((timestamps[-1] - timestamps[0]).total_seconds() / 60, 1)
            if len(timestamps) >= 2 else 0.0
        )

        cwd = context.get("cwd", "")
        branch = context.get("branch", "")
        project = Path(cwd).name if cwd else "unknown"

        sessions.append({
            "date":          timestamps[0].strftime("%Y-%m-%d %H:%M"),
            "session_id":    start_data.get("sessionId", "")[:8],
            "project":       project[:12],
            "branch":        branch,
            "output_tokens": output_tokens,
            "turns":         turns,
            "duration_min":  duration_min,
            "tools":         " ".join(sorted(tools_used)),
            "model":         model,
        })

    sessions.sort(key=lambda x: x["date"])
    return sessions


def find_active_session_id() -> Optional[str]:
    """Return the best active session directory name.

    Priority (each tier sorted by updated_at descending, preferring sessions
    that actually have an events.jsonl):
    1. Active session whose cwd matches CWD and has events.jsonl.
    2. Active session whose cwd matches CWD (no events.jsonl yet).
    3. Any other active session with events.jsonl (most recently updated).
    4. Any active session (most recently updated).
    """
    if not COPILOT_SESSIONS_PATH.exists():
        return None

    import yaml

    cwd = str(Path.cwd())
    # buckets: (cwd_match_with_events, cwd_match_no_events, other_with_events, other)
    buckets: list[list[tuple[str, str]]] = [[], [], [], []]

    for session_dir in COPILOT_SESSIONS_PATH.iterdir():
        if not list(session_dir.glob("inuse.*.lock")):
            continue
        session_id = session_dir.name
        has_events = (session_dir / "events.jsonl").exists()
        updated = ""
        session_cwd = ""
        ws = session_dir / "workspace.yaml"
        if ws.exists():
            try:
                with open(ws) as f:
                    meta = yaml.safe_load(f)
                if meta:
                    session_cwd = meta.get("cwd", "")
                    updated = str(meta.get("updated_at", ""))
            except Exception:
                pass
        cwd_match = session_cwd == cwd
        if cwd_match and has_events:
            buckets[0].append((session_id, updated))
        elif cwd_match:
            buckets[1].append((session_id, updated))
        elif has_events:
            buckets[2].append((session_id, updated))
        else:
            buckets[3].append((session_id, updated))

    for bucket in buckets:
        if bucket:
            bucket.sort(key=lambda x: x[1], reverse=True)
            return bucket[0][0]
    return None


def load_copilot_session_events(session_id: str) -> list[dict]:
    """Load all events from a single Copilot session directory."""
    events_file = COPILOT_SESSIONS_PATH / session_id / "events.jsonl"
    if not events_file.exists():
        return []
    events: list[dict] = []
    try:
        with open(events_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return events
