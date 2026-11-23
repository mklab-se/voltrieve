"""Prices for the estimates of what a run costs. Estimates for trends, not the bill.

USD per million tokens: input, cached input, output. List prices, Global Standard, Sweden Central,
short context, read on 2026-11-05. The source is docs/model-comparison.md (the section on cost) and
the same table in api/app/insights.py. Cache write charges are left out, so a figure is a little low.
A model that is not listed has no price: its tokens are printed and its cost is left out.
"""

PRICES_DATE = "2026-11-05"
PRICES_SOURCE = "docs/model-comparison.md and api/app/insights.py"
PRICES_USD_PER_MILLION = {
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
}
AGENT_MODEL = "gpt-6.1-terra"  # what all three backends run
PLANNING_MODEL = "gpt-5.4-mini"  # Search plans its queries with it
SEARCH_REASONING_USD_PER_MILLION = 0.022


def token_cost(prices, model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float | None:
    """USD for the tokens of one model, or None when there is no price for it."""
    if not prices or model not in prices:
        return None
    price_in, price_cached, price_out = prices[model]
    uncached = max(0, input_tokens - cached_tokens)
    return (uncached * price_in + cached_tokens * price_cached + output_tokens * price_out) / 1e6
