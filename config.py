"""
Session stats configuration.

Single source of truth: ~/.config/scrooge/config.json

Priority (highest to lowest):
  1. Environment variables  MCP_CLAUDE_BUDGET, MCP_COPILOT_BUDGET,
                            MCP_DISCOUNT_FACTOR, MCP_CLAUDE_PLAN, MCP_COPILOT_PLAN
  2. ~/.config/scrooge/config.json
  3. Built-in defaults

Use load() to read, update(**kwargs) to write specific keys.
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "scrooge" / "config.json"

DEFAULTS: dict = {
    "discount_factor":         0.5868,
    "claude_plan":             "claude_max_400",
    "claude_monthly_budget":   400.0,
    "copilot_plan":            "copilot_pro",
    "copilot_monthly_budget":  10.0,
    "copilot_overage_budget":  0.0,
    "calibration_history":     [],
    "copilot_spend_history":   {},
}

_ENV_MAP = [
    # (env var,                       config key,                cast)
    ("MCP_DISCOUNT_FACTOR",           "discount_factor",         float),
    ("MCP_CLAUDE_BUDGET",             "claude_monthly_budget",   float),
    ("MCP_COPILOT_BUDGET",            "copilot_monthly_budget",  float),
    ("MCP_COPILOT_OVERAGE_BUDGET",    "copilot_overage_budget",  float),
    ("MCP_CLAUDE_PLAN",               "claude_plan",             str),
    ("MCP_COPILOT_PLAN",              "copilot_plan",            str),
]


def load() -> dict:
    """Return merged config: defaults → config.json → env vars."""
    cfg = {**DEFAULTS}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for env_var, key, cast in _ENV_MAP:
        val = os.environ.get(env_var)
        if val is not None:
            try:
                cfg[key] = cast(val)
            except ValueError:
                pass
    return cfg


def save(cfg: dict) -> None:
    """Persist cfg to config.json (env var values are not written back)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def update(**kwargs) -> dict:
    """Update specific keys and persist. Returns the full updated config."""
    cfg = load()
    cfg.update(kwargs)
    # Don't persist env-var-sourced keys — re-apply after save
    save(cfg)
    return load()  # re-load so env overrides are still applied
