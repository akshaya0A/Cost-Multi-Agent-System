"""ModelAdvisor — right-sizes models per workload.

Heuristic: workloads with tiny outputs and simple task kinds (classification,
routing, extraction) rarely need frontier/Opus-tier models. Quality-sensitive
apps are never auto-downgraded — they get an eval-first recommendation instead.
"""

from .base import Agent, Finding
from ..store import UsageStore
from ..pricing import CATALOG, DOWNGRADE_LADDER, hypothetical_cost_on

SIMPLE_TASKS = {"classification", "summarization", "extraction", "routing"}


class ModelAdvisor(Agent):
    name = "model-advisor"
    mission = "Recommend the cheapest model that meets each workload's quality bar."

    def analyze(self, store: UsageStore) -> list[Finding]:
        rows = store.query(
            """SELECT app, task_kind, model, quality_sensitive,
                      COUNT(*) AS calls, SUM(cost_usd) AS cost,
                      SUM(input_tokens) AS itok, SUM(output_tokens) AS otok,
                      AVG(output_tokens) AS avg_out
               FROM api_calls WHERE retry_of IS NULL
               GROUP BY app, model"""
        )
        days = store.query("SELECT COUNT(DISTINCT DATE(ts)) AS d FROM api_calls")[0]["d"]

        findings = []
        for r in rows:
            target = DOWNGRADE_LADDER.get(r["model"])
            if not target:
                continue
            simple = r["task_kind"] in SIMPLE_TASKS and r["avg_out"] < 400
            if not simple:
                continue

            new_cost = hypothetical_cost_on(target, r["itok"], r["otok"])
            savings = (r["cost"] - new_cost) / days * 30
            if savings < 1:
                continue

            cur, tgt = CATALOG[r["model"]], CATALOG[target]
            if r["quality_sensitive"]:
                findings.append(Finding(
                    agent=self.name,
                    title=f"Eval-gated downgrade candidate: {r['app']}",
                    severity="info",
                    est_monthly_savings_usd=0.0,  # not claimable until evals pass
                    detail=(
                        f"{r['app']} runs {r['task_kind']} on {cur.display_name} but is marked "
                        f"quality-sensitive. A downgrade to {tgt.display_name} would save "
                        f"≈${savings:,.0f}/month if quality holds."
                    ),
                    recommendation=(
                        f"Run an offline A/B eval of {tgt.display_name} on a sampled week of "
                        f"{r['app']} traffic before switching."
                    ),
                    data={"app": r["app"], "from": r["model"], "to": target,
                          "gated_monthly_savings_usd": round(savings, 2)},
                ))
            else:
                findings.append(Finding(
                    agent=self.name,
                    title=f"Oversized model: {r['app']}",
                    severity="warning",
                    est_monthly_savings_usd=savings,
                    detail=(
                        f"{r['app']} runs {r['task_kind']} (avg {r['avg_out']:.0f} output tokens) "
                        f"on {cur.display_name} (${cur.input_per_mtok}/{cur.output_per_mtok} per MTok). "
                        f"{tgt.display_name} (${tgt.input_per_mtok}/{tgt.output_per_mtok}) handles this "
                        f"task class well."
                    ),
                    recommendation=f"Switch {r['app']} to {target}; validate on a 5% traffic canary for a week.",
                    data={"app": r["app"], "from": r["model"], "to": target,
                          "calls": r["calls"]},
                ))
        return findings
