# nudge-mcp

<!-- mcp-name: io.github.jwill824/nudge-mcp -->

MCP server that exposes Claude Code and GitHub Copilot CLI session token usage and cost data as tools, so your AI assistant can query its own usage mid-conversation.

Also includes a Stop hook that automatically logs each session to a CSV, and CLI tools for reporting and pricing calibration.

---

## Prerequisites

- [Claude Code](https://claude.ai/code) and/or [GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli) installed
- Python 3.13+ via [uv](https://docs.astral.sh/uv/)

Check that Python 3.13 is available:

```bash
uv python install 3.13
uv run python --version
```

---

## Installation

### 1. Install dependencies

```bash
cd ~/Developer/personal/nudge-mcp
uv pip install -e .
```

### 2. Register the MCP server

#### Claude Code

Add to `~/.claude/settings.json` under `mcpServers`:

```json
"mcpServers": {
  "nudge-mcp": {
    "command": "/opt/homebrew/bin/uv",
    "args": ["run", "--project", "/Users/YOUR_USERNAME/Developer/personal/nudge-mcp", "python", "/Users/YOUR_USERNAME/Developer/personal/nudge-mcp/server.py"]
  }
}
```

Replace `YOUR_USERNAME` with your macOS username (`whoami`).

#### GitHub Copilot CLI

Create (or update) `.mcp.json` in the repo root:

```json
{
  "mcpServers": {
    "nudge-mcp": {
      "type": "stdio",
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--project", "/Users/YOUR_USERNAME/Developer/personal/nudge-mcp", "python", "/Users/YOUR_USERNAME/Developer/personal/nudge-mcp/server.py"]
    }
  }
}
```

> **Note:** `.mcp.json` is gitignored because paths are machine-specific. Each contributor creates their own locally.

### 3. Wire up the Stop hook

Add to `~/.claude/settings.json` under `hooks`:

```json
"hooks": {
  "Stop": [{
    "hooks": [{
      "type": "command",
      "command": "/opt/homebrew/bin/uv run --project /Users/YOUR_USERNAME/Developer/personal/nudge-mcp python /Users/YOUR_USERNAME/Developer/personal/nudge-mcp/log.py 2>/dev/null || true"
    }]
  }]
}
```

This automatically logs token usage to `~/.config/nudge/sessions.csv` every time a Claude Code session ends.

### 4. Restart Claude Code

The MCP server connects on startup. You should see `nudge-mcp` listed when you run `/mcp` in Claude Code.

---

## MCP Tools

Once installed, ask Claude naturally — it will call the tools automatically.

### `claude_session_report`

Recent sessions with per-session cost and efficiency metrics.

```
"Show my session report for today"
"Show the last 10 sessions"
"Show sessions for April 2026"
```

| Input | Type | Description |
|-------|------|-------------|
| `last` | integer | Show last N sessions (default: 20) |
| `today` | boolean | Today's sessions only |
| `month` | string | Filter by month, e.g. `"2026-04"` |

### `claude_monthly_summary`

Total token usage and estimated cost for a Claude Code billing month. Shows spend vs your configured monthly budget with remaining runway.

```
"What's my estimated spend for this month?"
"Show my April 2026 monthly summary"
```

| Input | Type | Description |
|-------|------|-------------|
| `month` | string | Month in `YYYY-MM` format (default: current month) |

### `copilot_monthly_summary`

Monthly output token summary for GitHub Copilot CLI sessions. Shows total output tokens, top projects, and equivalent API cost vs your flat subscription to highlight savings.

```
"What's my Copilot usage this month?"
"Show Copilot summary for April 2026"
```

| Input | Type | Description |
|-------|------|-------------|
| `month` | string | Month in `YYYY-MM` format (default: current month) |

### `configure_subscription`

Update the active Claude Code or GitHub Copilot plan and monthly budget.

```
"Set my Claude plan to claude_max_400"
"My Copilot budget changed to $39 — update it"
"Set my Claude budget to 350"
```

| Input | Type | Description |
|-------|------|-------------|
| `service` | string | **Required.** `"claude"` or `"copilot"` |
| `plan` | string | Named plan key (see below) |
| `monthly_budget` | number | Custom budget in USD — overrides the plan default |

**Claude plans:** `claude_pro` ($20), `claude_max_100` ($100), `claude_max_200` ($200), `claude_max_400` ($400), `api` ($0)

**Copilot plans:** `copilot_free` ($0), `copilot_pro` ($10), `copilot_pro_plus` ($39), `copilot_business` ($19/seat), `copilot_enterprise` ($39/seat)

### `claude_tool_impact`

Compare Claude Code session efficiency between sessions that used a specific tool vs those that didn't.

```
"What impact does Serena have on my session efficiency?"
"Compare sessions that used ck vs those that didn't"
"Show the impact of Read calls in April 2026"
```

| Input | Type | Description |
|-------|------|-------------|
| `tool` | string | **Required.** Tool name — e.g. `"serena"`, `"ck"`, `"ast-grep"`, `"Read"`, `"Grep"`, `"Bash"` |
| `month` | string | Limit to a specific month, e.g. `"2026-04"`. Defaults to all history. |

### `copilot_session_report`

Show GitHub Copilot CLI session output token usage and efficiency metrics.

```
"Show my Copilot CLI session report"
"Show Copilot sessions for April 2026"
"Show today's Copilot sessions"
```

| Input | Type | Description |
|-------|------|-------------|
| `last` | integer | Show last N sessions (default: 20) |
| `today` | boolean | Today's sessions only |
| `month` | string | Filter by month, e.g. `"2026-04"` |

> **Note:** Only `output_tokens` are tracked. The Copilot CLI does not expose input or cache token counts.

### `claude_calibrate_pricing`

Update the discount factor from your actual Claude Code billing statement.

```
"Calibrate pricing — I was billed $185.50 last month"
```

| Input | Type | Description |
|-------|------|-------------|
| `actual_billed` | number | **Required.** Amount shown on your billing page (USD) |
| `month` | string | Month being calibrated, e.g. `"2026-04"` (default: previous month) |

### `copilot_behavior_report`

Cross-session pattern analysis. Aggregates behavioural signals across recent sessions to surface recurring inefficiencies with actionable recommendations. Includes aggregate context cost (heavy tool results), `store_memory` utilisation, and a smart tool recommendation when heavy `view` usage is detected without Serena, ck, or ast-grep.

```
"Analyse my last 10 Copilot sessions for bad habits"
"Show behaviour report for April 2026"
```

| Input | Type | Description |
|-------|------|-------------|
| `last` | integer | Number of recent sessions to analyse (default: 10) |
| `month` | string | Limit to a specific month, e.g. `"2026-04"` |

### `analyze_copilot_session`

Deep-dive analysis of a single Copilot CLI session. Checks prompt quality, tool batching (parallelism), bash overuse vs specialised tools, memory utilisation, context cost (heavy tool results), smart code intelligence tool usage (Serena, ck, ast-grep), MCP tool budget (avg KB/call vs call frequency with disable recommendations), and agentic workflow tools — structured systems like Superpowers, Get Shit Done (GSD), and Spec-Kit that bring tools, subagents, and multi-step workflows to reduce context overhead and prevent context rot. When no workflow tools are detected, the analysis gives personalized recommendations based on session characteristics.

```
"Analyse my current session"
"Analyse session abc123 for inefficiencies"
```

| Input | Type | Description |
|-------|------|-------------|
| `session_id` | string | Full or partial session UUID. Omit to use the active or most recent session. |

### `copilot_premium_usage`

Fetch live Copilot premium request usage from the GitHub API. Shows requests used and cost broken down by model, with progress against your configured overage budget. Uses the `gh` CLI token automatically — no extra setup needed if you're already authenticated with `gh`.

Requires a fine-grained PAT (or `gh auth login`) with **Plan (read)** permission. Only returns data for individual Copilot plans, not org/enterprise-managed licenses.

```
"Show my Copilot premium request usage for this month"
"How many premium requests have I used in April 2026?"
```

| Input | Type | Description |
|-------|------|-------------|
| `month` | string | Month in YYYY-MM format. Defaults to current month. |

---

### `copilot_tool_impact`

Analyze how a specific tool affects Copilot CLI session efficiency — comparing output tokens/turn and estimated cost between sessions that used the tool and those that didn't. The Copilot equivalent of `tool_impact`. Useful for measuring the real-world impact of tools like Serena, ck, ast-grep, or any MCP tool once you start using them.

```
"Show the impact of serena on my Copilot sessions"
"How much does ck reduce my output tokens per turn?"
"Compare bash-heavy vs non-bash-heavy sessions"
```

| Input | Type | Description |
|-------|------|-------------|
| `tool` | string | Tool name to analyze. Case-insensitive, substring match. |
| `month` | string | Month in YYYY-MM format. Defaults to all history. |

---

### `record_copilot_spend`

Manually record your actual Copilot overage spend for a month (from the GitHub billing UI). Used when the live API is inaccessible (org/enterprise-managed plans). The recorded amount is displayed in `copilot_monthly_summary` and used for budget forecasting.

```
"Record my Copilot spend of $18.00 for April 2026"
```

| Input | Type | Description |
|-------|------|-------------|
| `amount` | float | Actual overage spend in USD. |
| `month` | string | Month in YYYY-MM format. Defaults to current month. |

---

### `copilot_budget_forecast`

Forecast end-of-month Copilot overage spend using your current burn rate, and estimate how much you could save by fixing behavioural inefficiencies (low batching, bash overuse, vague prompts).

Requires `record_copilot_spend` and `configure_subscription` (with `overage_budget`) to be set first.

```
"Forecast my Copilot budget for this month"
"How much will I spend by end of April at this rate?"
"How much would I save if I fixed my batching habits?"
```

| Input | Type | Description |
|-------|------|-------------|
| `month` | string | Month in YYYY-MM format. Defaults to current month. |

---

## CLI Tools

### Session report

```bash
~/Developer/personal/nudge-mcp/nudge             # All Claude sessions
~/Developer/personal/nudge-mcp/nudge --last 20   # Last N sessions
~/Developer/personal/nudge-mcp/nudge --today     # Today only
~/Developer/personal/nudge-mcp/nudge --month 2026-04
~/Developer/personal/nudge-mcp/nudge --session abc123  # By session ID prefix

~/Developer/personal/nudge-mcp/nudge --copilot             # Copilot CLI sessions
~/Developer/personal/nudge-mcp/nudge --copilot --last 10
~/Developer/personal/nudge-mcp/nudge --copilot --month 2026-04
```

Add the directory to your PATH for shorter invocations:

```bash
# In ~/.zshrc
export PATH="$HOME/Developer/personal/nudge-mcp:$PATH"
```

### Recalibrate pricing

```bash
uv run python ~/Developer/personal/nudge-mcp/calibrate.py 185.50
# Specify a past month:
uv run python ~/Developer/personal/nudge-mcp/calibrate.py 198.36 --month 2026-04
```

---

## Pricing Model

| Token type | List price | Notes |
|-----------|-----------|-------|
| Input | $3.00/MTok | |
| Output | $15.00/MTok | |
| Cache read | $0.30/MTok | Cheapest — high cache hit % = efficiency |
| Cache creation | $3.75/MTok | |

The `discount_factor` (default `0.5868` ≈ 41% off list) is applied uniformly. Recalibrate monthly for accuracy.

---

## Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector \
  /opt/homebrew/bin/uv \
  run --project ~/Developer/personal/nudge-mcp python ~/Developer/personal/nudge-mcp/server.py
```

---

## Interpreting the Stats

### Cache hit %

| Range | Meaning |
|-------|---------|
| **>80%** | Excellent — system prompts and agent frontmatter are stable |
| **60–80%** | Good — some prompt churn |
| **<60%** | Prompts are unstable or sessions are very short |

### tokens/turn

| Range | Meaning |
|-------|---------|
| **~40–60k** | Efficient — targeted lookups |
| **~80–120k** | Moderate — some broad file reads |
| **>150k** | High — likely speculative reads or large context |

---

## Data

Sessions are stored at `~/.config/nudge/sessions.csv`.

CSV columns: `date`, `session_id`, `project`, `branch`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_create_tokens`, `total_tokens`, `cache_hit_pct`, `est_cost_usd`, `duration_min`, `turns`

---

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow and project structure.
