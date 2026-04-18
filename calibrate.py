#!/usr/bin/env python3
"""
Recalibrate the Claude Code pricing discount factor from actual billing.

Usage:
  python3 ~/.claude/tools/calibrate.py <actual_billed> [--month YYYY-MM]

Examples:
  python3 ~/.claude/tools/calibrate.py 198.36
  python3 ~/.claude/tools/calibrate.py 185.50 --month 2026-05

This script:
  1. Sums all token usage for the given month from session JSONL files
  2. Computes the list-price total
  3. Derives the discount factor = actual / list
  4. Updates discount_factor in ~/.config/scrooge/config.json
  5. Appends to calibration_history in config.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from glob import glob
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE / "lib"))

import config as _config

LIST = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75}


def sum_tokens_for_month(month_prefix: str) -> dict:
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    for jsonl in glob(str(Path.home() / ".claude/projects/**/*.jsonl"), recursive=True):
        with open(jsonl) as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    if not d.get("timestamp", "").startswith(month_prefix):
                        continue
                    usage = d.get("message", {}).get("usage", {})
                    if usage:
                        totals["input"]        += usage.get("input_tokens", 0)
                        totals["output"]       += usage.get("output_tokens", 0)
                        totals["cache_read"]   += usage.get("cache_read_input_tokens", 0)
                        totals["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                except Exception:
                    pass
    return totals


def main():
    parser = argparse.ArgumentParser(description="Calibrate Claude Code pricing discount factor")
    parser.add_argument("actual_billed", type=float, help="Actual amount billed for the month (USD)")
    parser.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"),
                        help="Month to analyze (YYYY-MM, default: current month)")
    args = parser.parse_args()

    print(f"Analyzing token usage for {args.month}...")
    tokens = sum_tokens_for_month(args.month)

    if all(v == 0 for v in tokens.values()):
        print(f"No token data found for {args.month}. Check that sessions exist for this period.")
        sys.exit(1)

    list_cost = sum(tokens[k] * LIST[k] for k in tokens) / 1_000_000

    print(f"\nToken totals for {args.month}:")
    for k, v in tokens.items():
        print(f"  {k+':':<20} {v:>15,}")
    print(f"\nList-price estimate:  ${list_cost:.4f}")
    print(f"Actual billed:        ${args.actual_billed:.2f}")

    if list_cost == 0:
        print("Error: list_cost is 0, cannot compute discount.")
        sys.exit(1)

    factor = args.actual_billed / list_cost
    print(f"Discount factor:      {factor:.4f}  ({(1-factor)*100:.1f}% off list)")

    # Append to calibration history and save new discount factor
    cfg = _config.load()
    history = cfg.get("calibration_history", [])
    history.append({
        "month":         args.month,
        "actual":        round(args.actual_billed, 2),
        "list_estimate": round(list_cost, 2),
        "factor":        round(factor, 4),
    })
    _config.update(discount_factor=round(factor, 4), calibration_history=history)

    print(f"\nUpdated {_config.CONFIG_PATH}")

    # Show current plan/budget for context
    cfg = _config.load()
    from pricing import CLAUDE_PLANS
    plan_label = CLAUDE_PLANS.get(cfg.get("claude_plan", ""), {}).get("label", cfg.get("claude_plan", ""))
    print(f"Plan: {plan_label}  |  Monthly budget: ${cfg.get('claude_monthly_budget', 0):.2f}")
    print("Run `scrooge` to see recalculated estimates.")


if __name__ == "__main__":
    main()