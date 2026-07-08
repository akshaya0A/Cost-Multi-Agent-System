"""Model catalog and cost math.

Prices are USD per million tokens (cached 2026-06, first-party Claude API).
Cache reads bill at ~0.1x input price, 5-minute-TTL cache writes at 1.25x,
and the Batches API discounts all token usage by 50%.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    model_id: str
    display_name: str
    input_per_mtok: float
    output_per_mtok: float
    tier: str  # frontier | opus | sonnet | haiku
    context_window: int


CATALOG: dict[str, ModelPrice] = {
    p.model_id: p
    for p in [
        ModelPrice("claude-fable-5", "Claude Fable 5", 10.00, 50.00, "frontier", 1_000_000),
        ModelPrice("claude-opus-4-8", "Claude Opus 4.8", 5.00, 25.00, "opus", 1_000_000),
        ModelPrice("claude-sonnet-5", "Claude Sonnet 5", 3.00, 15.00, "sonnet", 1_000_000),
        ModelPrice("claude-haiku-4-5", "Claude Haiku 4.5", 1.00, 5.00, "haiku", 200_000),
    ]
}

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25
BATCH_DISCOUNT = 0.50

# Cheaper-tier ladder used by the ModelAdvisor when right-sizing workloads.
DOWNGRADE_LADDER = {
    "claude-fable-5": "claude-opus-4-8",
    "claude-opus-4-8": "claude-sonnet-5",
    "claude-sonnet-5": "claude-haiku-4-5",
}


def call_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batched: bool = False,
) -> float:
    """Cost in USD for a single API call."""
    p = CATALOG[model_id]
    cost = (
        input_tokens * p.input_per_mtok
        + output_tokens * p.output_per_mtok
        + cache_read_tokens * p.input_per_mtok * CACHE_READ_MULTIPLIER
        + cache_write_tokens * p.input_per_mtok * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
    return cost * (1 - BATCH_DISCOUNT) if batched else cost


def hypothetical_cost_on(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """What the same token volume would cost on a different model (no caching)."""
    p = CATALOG[model_id]
    return (input_tokens * p.input_per_mtok + output_tokens * p.output_per_mtok) / 1_000_000
