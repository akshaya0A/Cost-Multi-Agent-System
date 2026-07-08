"""Forecaster — projects spend forward and flags budget risk.

Ordinary least squares on daily spend (robust enough for a trend read),
projected 30 days out, with a budget-breach date when a budget is set.
"""

from .base import Agent, Finding
from ..store import UsageStore


class Forecaster(Agent):
    name = "forecaster"
    mission = "Project spend 30 days forward and warn before budget breaches."

    def __init__(self, monthly_budget_usd: float | None = None):
        self.budget = monthly_budget_usd

    def analyze(self, store: UsageStore) -> list[Finding]:
        rows = store.query(
            "SELECT DATE(ts) AS day, SUM(cost_usd) AS cost FROM api_calls GROUP BY day ORDER BY day"
        )
        costs = [r["cost"] for r in rows]
        n = len(costs)
        if n < 7:
            return []

        # OLS fit: cost = a + b * day_index
        xs = list(range(n))
        xbar, ybar = sum(xs) / n, sum(costs) / n
        b = sum((x - xbar) * (y - ybar) for x, y in zip(xs, costs)) / sum((x - xbar) ** 2 for x in xs)
        a = ybar - b * xbar

        next30 = sum(max(0.0, a + b * (n + i)) for i in range(30))
        trend = "rising" if b > ybar * 0.01 else ("falling" if b < -ybar * 0.01 else "flat")
        sev = "warning" if trend == "rising" else "info"

        detail = (
            f"Daily spend trend is {trend} ({'+' if b >= 0 else ''}{b:,.2f} USD/day slope, "
            f"${ybar:,.2f}/day mean). Projected next-30-day spend: ${next30:,.2f}."
        )
        rec = "Re-forecast weekly; the trend slope is the earliest budget signal you have."
        data = {"trend": trend, "slope_usd_per_day": round(b, 2),
                "mean_daily_usd": round(ybar, 2), "forecast_30d_usd": round(next30, 2)}

        if self.budget:
            data["monthly_budget_usd"] = self.budget
            if next30 > self.budget:
                sev = "critical"
                overage = next30 - self.budget
                detail += (f" That exceeds the ${self.budget:,.0f} monthly budget "
                           f"by ${overage:,.2f} ({100 * overage / self.budget:.0f}% over).")
                rec = ("Projected budget breach — apply the policy engine's top savings "
                       "actions now, or raise the budget deliberately rather than by drift.")

        return [Finding(agent=self.name, title="30-day spend forecast", severity=sev,
                        detail=detail, recommendation=rec, data=data)]
