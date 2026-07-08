"""WasteDetector — finds spend that bought nothing.

Three waste classes:
  1. Retry burn      — duplicate spend from retries (esp. retry storms)
  2. Truncation      — outputs cut off by undersized max_tokens; the tokens were
                       paid for but the result is unusable and usually re-run
  3. Missed batching — latency-insensitive traffic on the live API instead of
                       the Batches API (50% discount)
"""

from .base import Agent, Finding
from ..store import UsageStore
from ..pricing import BATCH_DISCOUNT


class WasteDetector(Agent):
    name = "waste-detector"
    mission = "Find spend that produced no usable output or paid an avoidable premium."

    def analyze(self, store: UsageStore) -> list[Finding]:
        days = store.query("SELECT COUNT(DISTINCT DATE(ts)) AS d FROM api_calls")[0]["d"]
        findings = []

        retry = store.query(
            """SELECT app, COUNT(*) AS n, SUM(cost_usd) AS cost
               FROM api_calls WHERE retry_of IS NOT NULL GROUP BY app
               HAVING cost > 0.5 ORDER BY cost DESC"""
        )
        for r in retry:
            monthly = r["cost"] / days * 30
            findings.append(Finding(
                agent=self.name,
                title=f"Retry burn: {r['app']}",
                severity="warning",
                est_monthly_savings_usd=monthly * 0.8,  # most retries are avoidable
                detail=(
                    f"{r['app']} spent ${r['cost']:,.2f} on {r['n']:,} retry calls "
                    f"(≈${monthly:,.0f}/month). Retries double-bill the full prompt."
                ),
                recommendation=(
                    "Add exponential backoff with jitter, honor retry-after headers, and "
                    "alert when an app's retry rate exceeds 2%. Fix the flaky dependency "
                    "behind the storm."
                ),
                data={"app": r["app"], "retry_calls": r["n"]},
            ))

        trunc = store.query(
            """SELECT app, COUNT(*) AS n, SUM(cost_usd) AS cost,
                      AVG(max_tokens_requested) AS avg_cap
               FROM api_calls WHERE stop_reason = 'max_tokens' GROUP BY app"""
        )
        for r in trunc:
            monthly = r["cost"] / days * 30
            if monthly < 1:
                continue
            findings.append(Finding(
                agent=self.name,
                title=f"Truncated outputs: {r['app']}",
                severity="warning",
                est_monthly_savings_usd=monthly,  # truncated calls get re-run
                detail=(
                    f"{r['app']} hit max_tokens on {r['n']:,} calls "
                    f"(cap: {r['avg_cap']:.0f} tokens) — ${r['cost']:,.2f} spent on outputs "
                    f"that were cut off mid-thought and typically re-run."
                ),
                recommendation=(
                    f"Raise max_tokens for {r['app']} and alert on stop_reason == 'max_tokens'. "
                    "A truncated call is the most expensive kind: full price, zero value."
                ),
                data={"app": r["app"], "truncated_calls": r["n"]},
            ))

        batch = store.query(
            """SELECT app, COUNT(*) AS n, SUM(cost_usd) AS cost
               FROM api_calls
               WHERE latency_sensitive = 0 AND batched = 0 AND retry_of IS NULL
               GROUP BY app"""
        )
        for r in batch:
            savings = r["cost"] * BATCH_DISCOUNT / days * 30
            if savings < 1:
                continue
            findings.append(Finding(
                agent=self.name,
                title=f"Missed batch discount: {r['app']}",
                severity="warning",
                est_monthly_savings_usd=savings,
                detail=(
                    f"{r['app']} is latency-insensitive but sent {r['n']:,} calls through the "
                    f"live API. The Batches API prices the same work at 50% off "
                    f"(≈${savings:,.0f}/month saved)."
                ),
                recommendation=(
                    f"Move {r['app']} to the Message Batches API — most batches complete "
                    "within an hour, well within an offline pipeline's SLA."
                ),
                data={"app": r["app"], "calls": r["n"]},
            ))

        return findings
