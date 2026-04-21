"""
Copilot CLI session analysis engine.

Responsibilities:
  - Analyse session events for inefficiencies (vague prompts, batching, bash overuse, etc.)
  - Format analysis results as human-readable strings
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from core.loaders import fmt
from core.model_analysis import analyze_session_model_usage, estimate_savings as _estimate_savings

# Metadata for known agentic workflow systems (skills, subagent orchestrators, spec-driven tools).
# These are not just prompts — they bring tools, subagents, and structured multi-step workflows
# that reduce context overhead and prevent context rot across long sessions.
_WORKFLOW_TOOLS: dict[str, dict] = {
    "superpowers": {
        "label": "Superpowers",
        "url": "https://github.com/obra/superpowers",
        "benefit": (
            "Subagent-driven development isolates context per task so each sub-task starts fresh. "
            "Enforces TDD (RED-GREEN-REFACTOR) and systematic debugging — prevents context rot across tasks."
        ),
        "best_for": "multi-task implementation, long sessions, TDD discipline",
    },
    "get-shit-done": {
        "label": "Get Shit Done (GSD)",
        "url": "https://github.com/gsd-build/get-shit-done",
        "benefit": (
            "Context engineering layer explicitly solves context rot — the quality degradation as the context window fills. "
            "Subagent orchestration with state management keeps each planning/implementation phase clean."
        ),
        "best_for": "long sessions, new projects, preventing quality degradation",
    },
    "spec-kit": {
        "label": "Spec-Kit",
        "url": "https://github.github.com/spec-kit/",
        "benefit": (
            "Spec-Driven Development makes specifications executable before coding begins. "
            "Multi-step refinement reduces rework turns vs one-shot generation from prompts."
        ),
        "best_for": "greenfield projects, spec-first development, org/enterprise workflows",
    },
}


def _analyze_session_events(events: list[dict], session_id: str = "") -> dict:
    """Analyse a session's events and return a structured findings dict."""
    user_messages      = [e for e in events if e.get("type") == "user.message"]
    assistant_messages = [e for e in events if e.get("type") == "assistant.message"]
    tool_starts        = [e for e in events if e.get("type") == "tool.execution_start"]

    # --- Prompt quality ---
    _CONTINUATION = {"yes", "no", "ok", "sure", "proceed", "continue", "go ahead",
                     "do it", "yes please", "sounds good", "perfect", "great"}
    _VAGUE_PREFIXES = ("fix ", "update ", "change ", "do ", "make ", "add ", "remove ")
    vague_prompts: list[dict] = []
    for e in user_messages:
        content = e.get("data", {}).get("content", "").strip()
        if not content:
            continue
        low = content.lower()
        if low in _CONTINUATION:
            vague_prompts.append({"content": content, "reason": "continuation with no context"})
        elif len(content) < 20 and not any(c.isalpha() for c in content[10:]):
            vague_prompts.append({"content": content, "reason": "very short"})
        elif len(content) < 45 and any(low.startswith(p) for p in _VAGUE_PREFIXES) and \
                not any(ch in content for ch in ("/", ".", "_", "`")):
            vague_prompts.append({"content": content, "reason": "vague imperative (no file/symbol)"})

    # --- Tool batching (parallelism) ---
    single_tool_turns = 0
    multi_tool_turns  = 0
    for msg in assistant_messages:
        n = len(msg.get("data", {}).get("toolRequests", []))
        if n == 1:
            single_tool_turns += 1
        elif n > 1:
            multi_tool_turns += 1
    total_tool_turns = single_tool_turns + multi_tool_turns
    batching_pct = (multi_tool_turns / total_tool_turns * 100) if total_tool_turns else 0.0

    # --- Tool diversity ---
    tool_name_counts: dict[str, int] = {}
    for e in tool_starts:
        name = e.get("data", {}).get("toolName", "unknown")
        tool_name_counts[name] = tool_name_counts.get(name, 0) + 1
    total_tools = sum(tool_name_counts.values())
    bash_count  = tool_name_counts.get("bash", 0)
    bash_pct    = (bash_count / total_tools * 100) if total_tools else 0.0

    # Bash commands that could use a specialised tool instead
    bash_antipatterns: list[str] = []
    seen_ap: set[str] = set()
    for e in tool_starts:
        if e.get("data", {}).get("toolName") != "bash":
            continue
        cmd = e.get("data", {}).get("arguments", {}).get("command", "")
        checks = [
            (r"\bgrep\b|\brg\b",           "grep/rg in bash → use the grep tool"),
            (r"\bfind\b|\bls\b",           "find/ls in bash → use the glob tool"),
            (r"\bcat\b|\bhead\b|\btail\b", "cat/head/tail in bash → use the view tool"),
        ]
        for pattern, msg in checks:
            if msg not in seen_ap and re.search(pattern, cmd):
                bash_antipatterns.append(msg)
                seen_ap.add(msg)

    # --- Smart code intelligence tools ---
    # Detect serena (LSP symbol lookup), ck (semantic search), ast-grep (structural search).
    # Both "ast-grep" (hyphen) and "ast_grep" (underscore) are checked since MCP servers
    # may register the tool name with either convention.
    _SMART_TOOLS = {
        "serena":   "Serena (LSP symbol lookup)",
        "ck":       "ck (semantic search)",
        "ast-grep": "ast-grep (structural search)",
        "ast_grep": "ast-grep (structural search)",
    }
    smart_tools_used = list({
        label for key, label in _SMART_TOOLS.items()
        if any(key in (e.get("data", {}).get("toolName", "") or "").lower()
               for e in tool_starts)
    })

    # --- Memory utilisation ---
    memory_events = [
        e for e in tool_starts
        if e.get("data", {}).get("toolName") == "store_memory"
    ]
    memory_used = bool(memory_events)
    memory_subjects = [
        e.get("data", {}).get("arguments", {}).get("subject", "")
        for e in memory_events
    ]

    # --- System prompt (always-on) overhead ---
    # system.message events are injected every turn and include the base system prompt,
    # environment context, memories, and MCP tool descriptions. Tool *schemas* (inputSchema)
    # are sent as a separate `tools` API parameter not logged in events, but they contribute
    # to the size variation visible here. This is the best measurable proxy for always-on overhead.
    system_messages = [e for e in events if e.get("type") == "system.message"]
    system_msg_sizes = [len(e.get("data", {}).get("content", "")) for e in system_messages]
    system_prompt_turns = len(system_msg_sizes)
    system_prompt_avg_kb = round((sum(system_msg_sizes) / max(system_prompt_turns, 1)) / 1024, 1)
    system_prompt_total_kb = round(sum(system_msg_sizes) / 1024, 1)
    system_prompt_min_kb = round(min(system_msg_sizes) / 1024, 1) if system_msg_sizes else 0.0
    system_prompt_max_kb = round(max(system_msg_sizes) / 1024, 1) if system_msg_sizes else 0.0
    # Growth from min to max suggests additions mid-session (new MCP tools, expanded memories, etc.)
    system_prompt_growth_kb = round(system_prompt_max_kb - system_prompt_min_kb, 1)

    # --- MCP tool context cost ---
    # Build a lookup from toolCallId -> result payload size using execution_complete events
    tool_result_sizes: dict[str, int] = {}  # toolName -> total chars returned
    tool_result_counts: dict[str, int] = {}
    exec_complete = [e for e in events if e.get("type") == "tool.execution_complete"]
    # Map toolCallId -> toolName from tool_starts
    call_id_to_name: dict[str, str] = {
        e.get("data", {}).get("toolCallId", ""): e.get("data", {}).get("toolName", "")
        for e in tool_starts
    }
    for e in exec_complete:
        data = e.get("data", {})
        call_id = data.get("toolCallId", "")
        tool_name = call_id_to_name.get(call_id, "")
        if not tool_name:
            continue
        result = data.get("result", {})
        # Use the 'content' field (what the model sees) if available
        if isinstance(result, dict):
            content = result.get("content", "") or result.get("detailedContent", "")
        else:
            content = str(result)
        size = len(str(content))
        tool_result_sizes[tool_name] = tool_result_sizes.get(tool_name, 0) + size
        tool_result_counts[tool_name] = tool_result_counts.get(tool_name, 0) + 1

    # Average result size per tool call (chars); flag tools averaging > 2 KB
    _CONTEXT_WARN_CHARS = 2_000
    heavy_context_tools: list[dict] = []
    for name, total_size in tool_result_sizes.items():
        count = tool_result_counts.get(name, 1)
        avg = total_size // count
        if avg > _CONTEXT_WARN_CHARS:
            heavy_context_tools.append({
                "name":   name,
                "calls":  count,
                "avg_kb": round(avg / 1024, 1),
                "total_kb": round(total_size / 1024, 1),
            })

    # --- Total context volume and context rot detection ---
    total_context_kb = round(sum(tool_result_sizes.values()) / 1024, 1)

    # Repeated view reads of the same file path — strongest detectable context rot signal.
    # When the model re-reads a file it already saw, earlier content has likely been evicted
    # or crowded out, forcing the re-read to recover lost context.
    view_paths: list[str] = []
    for e in tool_starts:
        if e.get("data", {}).get("toolName") == "view":
            path = e.get("data", {}).get("arguments", {}).get("path", "")
            if path:
                view_paths.append(path)
    view_path_counts = Counter(view_paths)
    # Files viewed 2+ times (path → count)
    repeated_view_paths = {p: c for p, c in view_path_counts.items() if c >= 2}
    # Number of redundant reads (each re-read beyond the first)
    redundant_reads = sum(c - 1 for c in repeated_view_paths.values())
    total_view_reads = len(view_paths)
    repeat_view_pct = round(redundant_reads / total_view_reads * 100) if total_view_reads else 0

    # --- MCP tool budget analysis ---
    # Built-in tools have simple lowercase names with no hyphens or MCP prefixes
    _BUILTIN_TOOLS = {
        "bash", "view", "edit", "create", "grep", "glob", "report_intent",
        "store_memory", "ask_user", "sql", "task", "read_bash", "write_bash",
        "stop_bash", "list_bash", "read_agent", "list_agents", "write_agent",
        "ide-get_selection", "ide-get_diagnostics", "fetch_copilot_cli_documentation",
    }
    mcp_tool_analysis: list[dict] = []
    for name, total_size in tool_result_sizes.items():
        if name in _BUILTIN_TOOLS:
            continue
        # Treat any non-builtin as an MCP tool
        calls = tool_result_counts.get(name, 1)
        avg_kb = round(total_size / calls / 1024, 1)
        total_kb = round(total_size / 1024, 1)
        # Recommendation logic
        if avg_kb > 5 and calls < 3:
            verdict = "⚠️  Low value, high cost — consider disabling"
        elif avg_kb > 5:
            verdict = "⚠️  High cost — scope queries more narrowly"
        elif avg_kb > 2:
            verdict = "💡 Moderate cost — monitor usage"
        else:
            verdict = "✅ Low cost"
        mcp_tool_analysis.append({
            "name":     name,
            "calls":    calls,
            "avg_kb":   avg_kb,
            "total_kb": total_kb,
            "verdict":  verdict,
        })
    mcp_tool_analysis.sort(key=lambda x: -x["avg_kb"])

    # --- Agentic workflow tools (skills, subagent systems, spec-driven tools) ---
    # Alias → canonical name mapping
    _WORKFLOW_ALIASES: dict[str, str] = {
        "superpowers": "superpowers",
        "get-shit-done": "get-shit-done",
        "get_shit_done": "get-shit-done",
        "spec-kit": "spec-kit",
        "speckit": "spec-kit",
        "speckit-constitution": "spec-kit",
        "speckit.constitution": "spec-kit",
        "blog-writing-specialist": "blog-writing-specialist",
        "find-skills": "find-skills",
        "customize-cloud-agent": "customize-cloud-agent",
    }
    _SKILL_NAMES = set(_WORKFLOW_ALIASES.keys())
    skill_tools_used: list[dict] = []
    for e in tool_starts:
        tool_name = e.get("data", {}).get("toolName", "")
        args = e.get("data", {}).get("arguments", {})
        if tool_name == "skill":
            raw = args.get("skill", "")
            if raw:
                canonical = _WORKFLOW_ALIASES.get(raw, raw)
                existing = next((s for s in skill_tools_used if s["name"] == canonical), None)
                if existing:
                    existing["calls"] += 1
                else:
                    skill_tools_used.append({"name": canonical, "calls": 1, "via": "skill tool"})
        else:
            for alias, canonical in _WORKFLOW_ALIASES.items():
                if alias in tool_name.lower():
                    existing = next((x for x in skill_tools_used if x["name"] == canonical), None)
                    if existing:
                        existing["calls"] += 1
                    else:
                        skill_tools_used.append({"name": canonical, "calls": 1, "via": "direct"})
                    break

    # --- Session metadata ---
    start_event = next((e for e in events if e.get("type") == "session.start"), None)
    cwd = start_event.get("data", {}).get("context", {}).get("cwd", "") if start_event else ""
    timestamps = sorted(
        datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        for e in events if "timestamp" in e
    )
    duration_min = (
        round((timestamps[-1] - timestamps[0]).total_seconds() / 60, 1)
        if len(timestamps) >= 2 else 0.0
    )
    output_tokens = sum(
        e.get("data", {}).get("outputTokens", 0)
        for e in events if e.get("type") == "assistant.message"
    )

    # --- Session health: stagnation and over-duration signals ---
    # Four measurable signals that indicate the session context is degrading.
    # Each signal adds 1 to the score; score drives the health verdict.
    _session_health_signals: list[str] = []
    if duration_min > 90:
        _session_health_signals.append(f"Long duration: {duration_min:.0f} min")
    if len(user_messages) > 50:
        _session_health_signals.append(f"High turn count: {len(user_messages)} turns")
    if total_context_kb >= 200:
        _session_health_signals.append(f"Heavy context load: {total_context_kb} KB loaded")
    if redundant_reads >= 3 and repeat_view_pct >= 15:
        _session_health_signals.append(
            f"Context rot: {redundant_reads} redundant file reads ({repeat_view_pct}%)"
        )
    _session_health_score = len(_session_health_signals)

    # --- Model efficiency ---
    _session_model = ""
    for _e in events:
        if _e.get("type") == "session.model_change":
            _session_model = _e.get("data", {}).get("newModel", "")
            break  # first change = session start model
    _model_turns   = analyze_session_model_usage(events, _session_model)
    _model_savings = _estimate_savings(_model_turns)
    _model_total   = _model_savings["total_turns"]
    _model_over    = _model_savings["over_powered_turns"]
    _model_eff     = int(100 * (1 - _model_over / _model_total)) if _model_total else 100

    return {
        "session_id":        session_id[:8] if session_id else "",
        "project":           Path(cwd).name if cwd else "unknown",
        "turns":             len(user_messages),
        "duration_min":      duration_min,
        "output_tokens":     output_tokens,
        "vague_prompts":     vague_prompts,
        "single_tool_turns": single_tool_turns,
        "multi_tool_turns":  multi_tool_turns,
        "batching_pct":      batching_pct,
        "tool_name_counts":  tool_name_counts,
        "total_tools":       total_tools,
        "bash_count":        bash_count,
        "bash_pct":          bash_pct,
        "bash_antipatterns": bash_antipatterns,
        "memory_used":       memory_used,
        "memory_subjects":   memory_subjects,
        "heavy_context_tools": heavy_context_tools,
        "tool_result_sizes": tool_result_sizes,
        "tool_result_counts": tool_result_counts,
        "system_prompt_turns":    system_prompt_turns,
        "system_prompt_avg_kb":   system_prompt_avg_kb,
        "system_prompt_total_kb": system_prompt_total_kb,
        "system_prompt_min_kb":   system_prompt_min_kb,
        "system_prompt_max_kb":   system_prompt_max_kb,
        "system_prompt_growth_kb": system_prompt_growth_kb,
        "total_context_kb":  total_context_kb,
        "repeated_view_paths": repeated_view_paths,
        "redundant_reads":   redundant_reads,
        "total_view_reads":  total_view_reads,
        "repeat_view_pct":   repeat_view_pct,
        "smart_tools_used":  smart_tools_used,
        "mcp_tool_analysis": mcp_tool_analysis,
        "skill_tools_used":  skill_tools_used,
        "session_health_signals": _session_health_signals,
        "session_health_score":   _session_health_score,
        "model_turns":            _model_turns,
        "model_over_count":       _model_over,
        "model_total_turns":      _model_total,
        "model_savings_usd":      _model_savings["savings_usd"],
        "model_efficiency_score": _model_eff,
    }


def _format_session_analysis(analysis: dict, is_active: bool = False) -> str:
    label = " (ACTIVE)" if is_active else ""
    lines = [
        f"## Session Analysis — {analysis['project']} [{analysis['session_id']}]{label}",
        f"Turns: {analysis['turns']}  |  Duration: {analysis['duration_min']} min"
        f"  |  Output tokens: {fmt(analysis['output_tokens'])}",
        "",
    ]
    suggestions: list[str] = []

    # 1. Prompt quality
    lines.append("### Prompt Quality")
    vague = analysis["vague_prompts"]
    if vague:
        lines.append(f"⚠️  {len(vague)} potentially vague prompt(s):")
        for p in vague[:5]:
            snippet = p["content"][:60].replace("\n", " ")
            lines.append(f'   • "{snippet}"  ({p["reason"]})')
        suggestions.append(
            "Add context to short/vague prompts — reference specific files, errors, or goals."
        )
    else:
        lines.append("✅  Prompts appear specific and contextual.")
    lines.append("")

    # 2. Tool batching
    lines.append("### Tool Batching (Parallelism)")
    total_tt = analysis["single_tool_turns"] + analysis["multi_tool_turns"]
    if total_tt:
        lines.append(f"   Single-tool turns:  {analysis['single_tool_turns']:>3}  ({100 - analysis['batching_pct']:.0f}%)")
        lines.append(f"   Multi-tool turns:   {analysis['multi_tool_turns']:>3}  ({analysis['batching_pct']:.0f}%)")
        if analysis["batching_pct"] < 30 and total_tt >= 3:
            lines.append(f"⚠️  Low parallelism — only {analysis['batching_pct']:.0f}% of tool turns batched multiple calls.")
            suggestions.append(
                "Ask Copilot to read multiple files or run multiple searches in a single turn."
            )
        else:
            lines.append(f"✅  Good batching ratio ({analysis['batching_pct']:.0f}% multi-tool turns).")
    else:
        lines.append("   No tool turns recorded.")
    lines.append("")

    # 3. Tool diversity
    lines.append("### Tool Usage")
    if analysis["total_tools"]:
        for name, count in sorted(analysis["tool_name_counts"].items(), key=lambda x: -x[1])[:8]:
            pct = count / analysis["total_tools"] * 100
            bar = "█" * max(1, int(pct / 5))
            lines.append(f"   {name:<30} {count:>4}  ({pct:>4.0f}%)  {bar}")
        lines.append("")
        if analysis["bash_pct"] > 50 and analysis["bash_count"] >= 3:
            lines.append(f"⚠️  Bash-heavy session ({analysis['bash_pct']:.0f}% of tool calls).")
            suggestions.append(
                "Use specialised tools instead of bash: grep (search), glob (find files), view (read files)."
            )
        if analysis["bash_antipatterns"]:
            lines.append("⚠️  Bash used where a specialised tool would be better:")
            for ap in analysis["bash_antipatterns"]:
                lines.append(f"   • {ap}")
            if not any("specialised" in s for s in suggestions):
                suggestions.append(
                    "Use specialised tools instead of bash: grep (search), glob (find files), view (read files)."
                )
    else:
        lines.append("   No tool calls recorded.")
    lines.append("")

    # 4. Memory
    lines.append("### Memory Utilisation")
    if analysis["memory_used"]:
        subjects = [s for s in analysis["memory_subjects"] if s]
        count = len(analysis["memory_subjects"])
        lines.append(f"✅  store_memory called {count}x — facts persisted for future sessions.")
        if subjects:
            lines.append(f"   Stored: {', '.join(subjects)}")
    else:
        lines.append("⚠️  store_memory not called. Use it to persist key project facts across sessions.")
        suggestions.append(
            "Call store_memory during sessions to persist project conventions and reduce repetitive context."
        )
    lines.append("")

    # 5. MCP / tool context cost + volume + rot
    heavy = analysis.get("heavy_context_tools", [])
    total_kb = analysis.get("total_context_kb", 0.0)
    redundant = analysis.get("redundant_reads", 0)
    total_views = analysis.get("total_view_reads", 0)  # noqa: F841
    repeat_pct = analysis.get("repeat_view_pct", 0)
    repeated = analysis.get("repeated_view_paths", {})

    lines.append("### Context Volume & Rot")

    # Total volume
    if total_kb >= 500:
        lines.append(f"🔴 {total_kb} KB of tool results loaded — very high context load.")
        lines.append(
            "   Use /compact, start a new session, or delegate remaining work to a subagent "
            "(Superpowers / GSD) to avoid severe quality degradation."
        )
        suggestions.append(
            f"Very high context load ({total_kb} KB) — use /compact or start a new session to prevent context rot."
        )
    elif total_kb >= 200:
        lines.append(f"⚠️  {total_kb} KB of tool results loaded — context window filling up.")
        lines.append(
            "   Scope tool queries more narrowly, summarise large results, "
            "or use Serena/ck to replace whole-file reads."
        )
        suggestions.append(
            f"High context load ({total_kb} KB) — scope queries more narrowly and avoid re-reading files."
        )
    elif total_kb > 0:
        lines.append(f"✅  {total_kb} KB of tool results loaded — within healthy range.")

    # Per-tool breakdown (heavy tools)
    if heavy:
        lines.append("   Tool results > 2 KB average:")
        for t in sorted(heavy, key=lambda x: -x["avg_kb"]):
            lines.append(
                f"   • {t['name']:<35} {t['calls']}x  avg {t['avg_kb']} KB  total {t['total_kb']} KB"
            )
        if total_kb < 200:
            suggestions.append(
                "Large tool responses consume context — consider scoping queries or summarising results."
            )

    # Context rot: repeated view reads
    if redundant >= 3 and repeat_pct >= 15:
        top = sorted(repeated.items(), key=lambda x: -x[1])[:5]
        top_str = ", ".join(f"{Path(p).name} ×{c}" for p, c in top)
        lines.append(
            f"⚠️  Context rot signal: {redundant} redundant file read(s) "
            f"({repeat_pct}% of view calls re-read already-seen files)."
        )
        lines.append(f"   Top offenders: {top_str}")
        lines.append(
            "   Re-reads indicate the model lost earlier context. Use Serena (symbol lookup), "
            "ck (semantic search), or /compact to recover."
        )
        suggestions.append(
            f"Context rot detected — {redundant} redundant file reads ({repeat_pct}%). "
            "Use Serena or ck instead of re-reading whole files, or run /compact."
        )
    elif redundant > 0:
        top = sorted(repeated.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{Path(p).name} ×{c}" for p, c in top)
        lines.append(f"   {redundant} file(s) re-read: {top_str}")

    if not heavy and total_kb < 200 and redundant < 3:
        lines.append("   No context rot signals detected.")

    # System prompt (always-on) overhead
    sp_avg  = analysis.get("system_prompt_avg_kb", 0.0)
    sp_total = analysis.get("system_prompt_total_kb", 0.0)
    sp_turns = analysis.get("system_prompt_turns", 0)
    sp_growth = analysis.get("system_prompt_growth_kb", 0.0)
    sp_min  = analysis.get("system_prompt_min_kb", 0.0)
    sp_max  = analysis.get("system_prompt_max_kb", 0.0)
    if sp_turns > 0:
        lines.append(f"   System prompt: {sp_avg} KB/turn × {sp_turns} turns = {sp_total} KB total always-on overhead.")
        if sp_growth >= 2.0:
            lines.append(
                f"   ⚠️  System prompt grew {sp_growth} KB during session ({sp_min}→{sp_max} KB). "
                "Possible causes: new MCP tools connected, additional memories stored."
            )
            suggestions.append(
                f"System prompt grew {sp_growth} KB mid-session — "
                "review which MCP servers are connected and whether all are needed."
            )
        else:
            lines.append(
                "   Note: MCP tool schemas are sent alongside this but are not separately logged. "
                "Reduce always-on overhead by disabling unused MCP servers."
            )

    lines.append("")

    # 6. Smart code intelligence tools
    view_heavy = any(t["name"] == "view" for t in heavy)
    context_rot = redundant >= 3 and repeat_pct >= 15
    smart = analysis.get("smart_tools_used", [])
    lines.append("### Code Intelligence Tools")
    if smart:
        lines.append(f"✅  Smart tools active: {', '.join(smart)}")
    elif view_heavy or context_rot:
        lines.append("⚠️  Heavy `view` usage detected but no smart code tools used.")
        lines.append("   Consider: Serena (LSP symbol lookup), ck (semantic search), ast-grep (structural search)")
        if not any("Serena" in s for s in suggestions):
            suggestions.append(
                "Replace whole-file view calls with Serena (symbol lookup) or ck (semantic search) "
                "to cut context cost per read."
            )
    else:
        lines.append("   No smart tool usage detected (Serena / ck / ast-grep).")
    lines.append("")

    # 7. MCP tool budget
    mcp_analysis = analysis.get("mcp_tool_analysis", [])
    if mcp_analysis:
        lines.append("### MCP Tool Budget")
        lines.append(f"   {'Tool':<40} {'Calls':>5}  {'Avg KB':>6}  {'Total KB':>8}  Verdict")
        lines.append(f"   {'─'*40} {'─'*5}  {'─'*6}  {'─'*8}  {'─'*30}")
        for t in mcp_analysis:
            lines.append(
                f"   {t['name']:<40} {t['calls']:>5}  {t['avg_kb']:>6}  {t['total_kb']:>8}  {t['verdict']}"
            )
        disable_candidates = [t for t in mcp_analysis if "consider disabling" in t["verdict"]]
        if disable_candidates:
            names = ", ".join(t["name"] for t in disable_candidates)
            suggestions.append(
                f"MCP tools with high cost and low usage: {names}. "
                "Disable in .mcp.json when not needed to reduce context overhead."
            )
        lines.append("")

    # 8. Agentic workflow tools
    workflows = analysis.get("skill_tools_used", [])
    lines.append("### Agentic Workflow Tools")
    if workflows:
        lines.append(
            "   These structured systems reduce context overhead with tools, subagents, and multi-step workflows:"
        )
        for w in workflows:
            meta = _WORKFLOW_TOOLS.get(w["name"])
            if meta:
                lines.append(f"   ✅ {meta['label']}  ({w['calls']}x)")
                lines.append(f"      {meta['benefit']}")
            else:
                lines.append(f"   ✅ {w['name']}  ({w['calls']}x)")
    else:
        lines.append("⚠️  No agentic workflow tools detected.")
        lines.append(
            "   These go beyond individual skills — they bring tools, subagents, and structured\n"
            "   multi-step workflows that reduce context overhead and prevent context rot."
        )
        turns = analysis.get("turns", 0)
        duration_min = analysis.get("duration_min", 0)
        view_count = analysis.get("tool_name_counts", {}).get("view", 0)
        recs: list[str] = []
        if turns > 30 or duration_min > 60:
            meta = _WORKFLOW_TOOLS["get-shit-done"]
            recs.append(
                f"   • {meta['label']} ({meta['url']})\n"
                f"     Why: {turns} turns / {duration_min} min — context rot likely\n"
                f"     Best for: {meta['best_for']}"
            )
        if view_count > 20:
            meta = _WORKFLOW_TOOLS["superpowers"]
            recs.append(
                f"   • {meta['label']} ({meta['url']})\n"
                f"     Why: {view_count} view calls — subagent isolation keeps per-task context lean\n"
                f"     Best for: {meta['best_for']}"
            )
        if turns > 15:
            meta = _WORKFLOW_TOOLS["spec-kit"]
            recs.append(
                f"   • {meta['label']} ({meta['url']})\n"
                f"     Why: complex session — spec-first reduces implementation rework turns\n"
                f"     Best for: {meta['best_for']}"
            )
        if recs:
            lines.append("   Based on this session:")
            lines.extend(recs)
        else:
            lines.append(
                "   Consider: Superpowers, Get Shit Done (GSD), or Spec-Kit\n"
                "   Run /find-skills to discover what's available in your environment."
            )
        if turns > 10 or duration_min > 30:
            suggestions.append(
                "No agentic workflow tools used — Superpowers, GSD, or Spec-Kit could reduce context "
                "overhead and prevent quality degradation in sessions like this."
            )
    lines.append("")

    # 9. Session health: stagnation and over-duration
    health_score = analysis.get("session_health_score", 0)
    health_signals = analysis.get("session_health_signals", [])
    lines.append("### Session Health")
    if health_score == 0:
        lines.append("✅  Session healthy — no stagnation or over-duration signals detected.")
    elif health_score == 1:
        lines.append("💡 Session getting long — monitor for quality degradation.")
        lines.append(f"   Signal: {health_signals[0]}")
        lines.append(
            "   Start a new session if switching to an unrelated topic or feature.\n"
            "   Stay if continuing the same task thread — accumulated context helps."
        )
    else:
        emoji = "⚠️ " if health_score == 2 else "🔴"
        lines.append(f"{emoji} Context stagnation risk — {health_score}/4 degradation signals active:")
        for sig in health_signals:
            lines.append(f"   • {sig}")
        lines.append("")
        lines.append("   Start a new session when:")
        lines.append("   • Switching to a different feature or unrelated task")
        lines.append("   • After completing a major milestone")
        lines.append("   • Responses feel repetitive, miss prior context, or re-ask clarifying questions")
        lines.append("   Stay in this session when:")
        lines.append("   • Continuing the same task thread (accumulated context is an advantage)")
        lines.append("   • Mid-way through a multi-step implementation")
        lines.append("")
        lines.append("   How agentic tools help avoid forced restarts:")
        lines.append(
            "   • Superpowers: dispatches each task to a fresh subagent — parent session stays lean\n"
            "     → https://github.com/obra/superpowers"
        )
        lines.append(
            "   • GSD: context engineering prevents rot; /gsd-new-project re-loads state in a fresh session\n"
            "     → https://github.com/gsd-build/get-shit-done"
        )
        lines.append(
            "   • Spec-Kit: specs encode all intent — /speckit.constitution bootstraps a new session cheaply\n"
            "     → https://github.github.com/spec-kit/"
        )
        if health_score >= 3:
            suggestions.append(
                f"High stagnation risk ({health_score}/4 signals) — start a fresh session for new work. "
                "Use Superpowers or GSD to preserve state across session boundaries."
            )

    lines.append("")

    # 8. Model Usage
    lines.append("### Model Usage")
    _mt = analysis.get("model_total_turns", 0)
    _mo = analysis.get("model_over_count", 0)
    _eff = analysis.get("model_efficiency_score", 100)
    _sav = analysis.get("model_savings_usd", 0.0)
    if _mt:
        _eff_icon = "✅" if _eff >= 80 else "⚠️"
        lines.append(f"{_eff_icon}  Efficiency score: {_eff} / 100")
        lines.append(f"   Over-powered turns: {_mo} / {_mt}  ({100 * _mo // _mt}%)")
        if _sav > 0.001:
            lines.append(f"   Est. savings if right-sized: ~${_sav:.2f}")
        if _mo > 0:
            suggestions.append(
                f"Model over-powered for {_mo}/{_mt} turns — consider switching to Haiku "
                "for simple lookups, confirmations, and short Q&A turns."
            )
    else:
        lines.append("   No model turn data available.")
    lines.append("")

    # Summary
    lines.append("### Summary")
    if suggestions:
        lines.append(f"Issues found: {len(suggestions)}")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")
    else:
        lines.append("✅  No major inefficiencies detected.")

    return "\n".join(lines)
