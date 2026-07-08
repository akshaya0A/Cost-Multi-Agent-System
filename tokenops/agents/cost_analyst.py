"""CostAnalyst — spend attribution: who is spending what, where, and on which model."""

from .base import Agent, Finding
from ..store import UsageStore


class CostAnalyst(Agent):
    name = "cost-analyst"
    mission = "Attribute every dollar of spend to an app, model, and task kind."

    def analyze(self, store: UsageStore) -> list[Finding]:
        total = store.query("SELECT SUM(cost_usd) AS c, COUNT(*) AS n FROM api_calls")[0]
        by_app = store.query(
            """SELECT app, model, SUM(cost_usd) AS cost, COUNT(*) AS calls,
                      SUM(input_tokens) AS itok, SUM(output_tokens) AS otok
               FROM api_calls GROUP BY app, model ORDER BY cost DESC"""
        )
        days = store.query(
            "SELECT COUNT(DISTINCT DATE(ts)) AS d FROM api_calls")[0]["d"]

        breakdown = [
            {
                "app": r["app"], "model": r["model"],
                "cost_usd": round(r["cost"], 2), "calls": r["calls"],
                "input_tokens": r["itok"], "output_tokens": r["otok"],
                "share_pct": round(100 * r["cost"] / total["c"], 1),
            }
            for r in by_app
        ]
        top = breakdown[0]
        monthly_run_rate = total["c"] / days * 30

        return [Finding(
            agent=self.name,
            title=f"Spend attribution across {len(breakdown)} app/model pairs",
            severity="info",
            detail=(
                f"${total['c']:,.2f} across {total['n']:,} calls over {days} days "
                f"(≈${monthly_run_rate:,.0f}/month run rate). Top driver: "
                f"{top['app']} on {top['model']} at ${top['cost_usd']:,.2f} "
                f"({top['share_pct']}% of spend)."
            ),
            recommendation="Review the top two spend drivers first — optimizations there compound.",
            data={"total_usd": round(total["c"], 2),
                  "monthly_run_rate_usd": round(monthly_run_rate, 2),
                  "days": days, "breakdown": breakdown},
        )]
