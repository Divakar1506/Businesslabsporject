"""Model prices (USD per million tokens) and per-call cost calculation.

Source: Anthropic first-party API prices cached 2026-10-06. Re-check before
relying on them; override any value by writing data/prices.json with the same
shape as PRICES.
"""
import json
import os
from pathlib import Path

PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    # Haiku 5.5 prices double-tier: prompts above 100K tokens cost more.
    # cache_read is assumed at 0.1x input (not listed in the cached table) - verify.
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01,
                         "long_threshold": 100_000, "long_input": 0.50, "long_output": 2.50},
}
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute cache writes cost 1.25x the input price
CONTEXT_LIMIT = {"claude-opus-5-5": 1_000_000, "claude-sonnet-5-5": 1_000_000, "claude-haiku-5-5": 1_000_000}
CHEAPEST_MODEL = "claude-haiku-5-5"
FREE_PREFIXES = ("offline/", "ollama/")  # local / replayed answers cost $0

_override = Path(os.environ.get("PRICES_FILE", "data/prices.json"))
if _override.exists():
    PRICES.update(json.loads(_override.read_text(encoding="utf-8")))


def is_free(model: str) -> bool:
    return model.startswith(FREE_PREFIXES)


def known(model: str) -> bool:
    return model in PRICES or is_free(model)


def call_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
              cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Dollar cost of one call from its usage numbers. Unknown models cost 0 and are flagged by the caller."""
    p = PRICES.get(model)
    if p is None or is_free(model):
        return 0.0
    in_price, out_price = p["input"], p["output"]
    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    if "long_threshold" in p and prompt > p["long_threshold"]:
        in_price, out_price = p["long_input"], p["long_output"]
    usd = (input_tokens * in_price
           + output_tokens * out_price
           + cache_read_tokens * p["cache_read"]
           + cache_write_tokens * in_price * CACHE_WRITE_MULTIPLIER)
    return usd / 1_000_000


def context_pct(model: str, prompt_tokens: int) -> float:
    limit = CONTEXT_LIMIT.get(model)
    return round(100 * prompt_tokens / limit, 3) if limit else 0.0
