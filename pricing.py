"""
Claude Code and GitHub Copilot pricing tables and cost functions.

Mutable config (discount_factor, monthly budgets, active plans) lives in
~/.config/scrooge/config.json and is managed by config.py.
"""

# ---------------------------------------------------------------------------
# Subscription plan tables  (static — update if new tiers are launched)
# ---------------------------------------------------------------------------
CLAUDE_PLANS: dict[str, dict] = {
    "claude_pro":     {"monthly_budget": 20.0,  "label": "Claude Pro"},
    "claude_max_100": {"monthly_budget": 100.0, "label": "Claude Max ($100/mo)"},
    "claude_max_200": {"monthly_budget": 200.0, "label": "Claude Max ($200/mo)"},
    "claude_max_400": {"monthly_budget": 400.0, "label": "Claude Max ($400/mo)"},
    "api":            {"monthly_budget": 0.0,   "label": "API (pay-as-you-go)"},
    "custom":         {"monthly_budget": 0.0,   "label": "Custom"},
}

COPILOT_PLANS: dict[str, dict] = {
    "copilot_free":       {"monthly_budget": 0.0,  "label": "GitHub Copilot Free"},
    "copilot_pro":        {"monthly_budget": 10.0, "label": "GitHub Copilot Pro"},
    "copilot_pro_plus":   {"monthly_budget": 39.0, "label": "GitHub Copilot Pro+"},
    "copilot_business":   {"monthly_budget": 19.0, "label": "GitHub Copilot Business (per seat)"},
    "copilot_enterprise": {"monthly_budget": 39.0, "label": "GitHub Copilot Enterprise (per seat)"},
    "custom":             {"monthly_budget": 0.0,  "label": "Custom"},
}

# ---------------------------------------------------------------------------
# API list prices per model (per MTok) — update when Anthropic reprices
# ---------------------------------------------------------------------------
LIST_PRICES: dict[str, dict] = {
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00,  "cache_read": 0.08, "cache_create": 1.00},
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_create": 18.75},
}
_DEFAULT_LIST = LIST_PRICES["claude-sonnet-4-6"]


_DEFAULT_DISCOUNT = 0.5868  # fallback if config unavailable


# ---------------------------------------------------------------------------
# Pricing functions
# ---------------------------------------------------------------------------
def get_prices(model: str = "claude-sonnet-4-6", discount: float | None = None) -> dict:
    """Return per-token prices (not per-MTok) for a given model, with discount applied."""
    if discount is None:
        try:
            import config as _c
            discount = _c.load().get("discount_factor", _DEFAULT_DISCOUNT)
        except Exception:
            discount = _DEFAULT_DISCOUNT
    list_p = LIST_PRICES.get(model, _DEFAULT_LIST)
    return {k: v * discount / 1_000_000 for k, v in list_p.items()}


def estimate_cost(tokens: dict, model: str = "claude-sonnet-4-6", discount: float | None = None) -> float:
    """
    Estimate session cost.

    tokens: dict with keys input, output, cache_read, cache_create
    discount: override discount factor (defaults to value from config)
    Returns cost in USD.
    """
    if discount is None:
        try:
            import config as _c
            discount = _c.load().get("discount_factor", _DEFAULT_DISCOUNT)
        except Exception:
            discount = _DEFAULT_DISCOUNT
    prices = get_prices(model, discount)
    return (
        tokens.get("input", 0)        * prices["input"] +
        tokens.get("output", 0)       * prices["output"] +
        tokens.get("cache_read", 0)   * prices["cache_read"] +
        tokens.get("cache_create", 0) * prices["cache_create"]
    )

