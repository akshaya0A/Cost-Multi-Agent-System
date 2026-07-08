"""CacheStrategist — finds prompt-caching opportunities.

Caching is a prefix match: a stable prefix (system prompt, tools, shared docs)
re-sent on every call bills at full input price, but would bill at ~0.1x once
cached (writes cost 1.25x). Repeated prompt_prefix_hash values with near-zero
cache_read_tokens are money left on the table.
"""

from .base import Agent, Finding
from ..store import UsageStore
from ..pricing import CATALOG, CACHE_READ_MULTIPLIER, CACHE_WRITE_MULTIPLIER

MIN_CACHEABLE_PREFIX = 1024  # tokens — shorter prefixes silently won't cache
HIT_ASSUMPTION = 0.90        # assume 90% of repeat calls land within the cache TTL


class CacheStrategist(Agent):
    name = "cache-strategist"
    mission = "Find repeated prompt prefixes that should be served from cache."

    def analyze(self, store: UsageStore) -> list[Finding]:
        rows = store.query(
            """SELECT app, model, prompt_prefix_hash, prefix_tokens,
                      COUNT(*) AS calls, SUM(cache_read_tokens) AS creads
               FROM api_calls WHERE retry_of IS NULL
               GROUP BY app, prompt_prefix_hash HAVING calls > 50"""
        )
        days = store.query("SELECT COUNT(DISTINCT DATE(ts)) AS d FROM api_calls")[0]["d"]

        findings = []
        for r in rows:
            if r["prefix_tokens"] < MIN_CACHEABLE_PREFIX or r["creads"] > 0:
                continue
            price = CATALOG[r["model"]].input_per_mtok
            prefix_cost_now = r["calls"] * r["prefix_tokens"] * price / 1e6
            hits = r["calls"] * HIT_ASSUMPTION
            writes = r["calls"] - hits
            prefix_cost_cached = (
                hits * r["prefix_tokens"] * price * CACHE_READ_MULTIPLIER
                + writes * r["prefix_tokens"] * price * CACHE_WRITE_MULTIPLIER
            ) / 1e6
            savings = (prefix_cost_now - prefix_cost_cached) / days * 30
            if savings < 1:
                continue

            findings.append(Finding(
                agent=self.name,
                title=f"Uncached repeated prefix: {r['app']}",
                severity="warning" if savings < 500 else "critical",
                est_monthly_savings_usd=savings,
                detail=(
                    f"{r['app']} re-sends a ~{r['prefix_tokens']:,}-token stable prefix on "
                    f"{r['calls']:,} calls with zero cache reads. At ${price}/MTok input, "
                    f"that prefix alone costs ${prefix_cost_now:,.2f} uncached vs "
                    f"${prefix_cost_cached:,.2f} with a cache_control breakpoint."
                ),
                recommendation=(
                    "Add cache_control: {type: 'ephemeral'} on the last stable block "
                    "(system prompt / shared docs) and keep volatile content after it. "
                    "Verify with usage.cache_read_input_tokens > 0."
                ),
                data={"app": r["app"], "prefix_tokens": r["prefix_tokens"],
                      "calls": r["calls"], "assumed_hit_rate": HIT_ASSUMPTION},
            ))
        return findings
