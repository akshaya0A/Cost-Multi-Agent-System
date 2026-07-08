"""PromptOptimizer — flags prompt bloat.

Workloads where input dwarfs output (beyond what the task kind justifies)
usually carry dead weight: over-stuffed retrieval context, redundant few-shot
examples, or verbose instructions. Trimming input tokens is the cheapest
optimization there is — no model change, no quality gate.
"""

from .base import Agent, Finding
from ..store import UsageStore
from ..pricing import CATALOG

# Expected input:output ratios by task; beyond ~2x expected = bloat suspect.
EXPECTED_RATIO = {"classification": 40, "rag_qa": 25, "summarization": 15,
                  "codegen": 4, "evaluation": 20}
TRIM_ESTIMATE = 0.30  # conservative: 30% of bloated input is trimmable


class PromptOptimizer(Agent):
    name = "prompt-optimizer"
    mission = "Detect over-stuffed prompts and estimate trim savings."

    def analyze(self, store: UsageStore) -> list[Finding]:
        rows = store.query(
            """SELECT app, task_kind, model, COUNT(*) AS calls,
                      SUM(input_tokens) AS itok, SUM(output_tokens) AS otok
               FROM api_calls WHERE retry_of IS NULL GROUP BY app"""
        )
        days = store.query("SELECT COUNT(DISTINCT DATE(ts)) AS d FROM api_calls")[0]["d"]

        findings = []
        for r in rows:
            expected = EXPECTED_RATIO.get(r["task_kind"], 10)
            ratio = r["itok"] / max(1, r["otok"])
            if ratio < expected * 2:
                continue
            price = CATALOG[r["model"]].input_per_mtok
            savings = r["itok"] * TRIM_ESTIMATE * price / 1e6 / days * 30
            if savings < 1:
                continue
            findings.append(Finding(
                agent=self.name,
                title=f"Prompt bloat: {r['app']}",
                severity="warning",
                est_monthly_savings_usd=savings,
                detail=(
                    f"{r['app']} averages a {ratio:.0f}:1 input:output token ratio "
                    f"({expected}:1 is typical for {r['task_kind']}). "
                    f"Trimming ~{TRIM_ESTIMATE:.0%} of input tokens saves ≈${savings:,.0f}/month."
                ),
                recommendation=(
                    "Audit the prompt template: cut redundant few-shot examples, tighten "
                    "retrieval top-k, and drop boilerplate instructions the model ignores. "
                    "Use the count_tokens endpoint to measure before/after."
                ),
                data={"app": r["app"], "ratio": round(ratio, 1),
                      "expected_ratio": expected, "input_tokens": r["itok"]},
            ))
        return findings
