"""UsageMonitor — detects spend anomalies (spikes/dips) per app via z-scores on daily spend."""

import statistics

from .base import Agent, Finding
from ..store import UsageStore

Z_THRESHOLD = 3.0


class UsageMonitor(Agent):
    name = "usage-monitor"
    mission = "Watch daily usage per app and flag statistically abnormal days."

    def analyze(self, store: UsageStore) -> list[Finding]:
        rows = store.query(
            """SELECT app, DATE(ts) AS day, SUM(cost_usd) AS cost
               FROM api_calls GROUP BY app, day ORDER BY app, day"""
        )
        series: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            series.setdefault(r["app"], []).append((r["day"], r["cost"]))

        findings = []
        for app, points in series.items():
            costs = [c for _, c in points]
            if len(costs) < 7:
                continue
            mu, sigma = statistics.mean(costs), statistics.pstdev(costs)
            if sigma == 0:
                continue
            anomalies = [
                {"day": day, "cost_usd": round(c, 2), "z": round((c - mu) / sigma, 1)}
                for day, c in points
                if abs(c - mu) / sigma >= Z_THRESHOLD
            ]
            if anomalies:
                worst = max(anomalies, key=lambda a: a["z"])
                excess = sum(a["cost_usd"] - mu for a in anomalies if a["z"] > 0)
                findings.append(Finding(
                    agent=self.name,
                    title=f"Spend anomaly in {app}",
                    severity="critical" if worst["z"] >= 5 else "warning",
                    detail=(
                        f"{app} had {len(anomalies)} anomalous day(s); worst was {worst['day']} "
                        f"at ${worst['cost_usd']:,.2f} ({worst['z']}σ above its "
                        f"${mu:,.2f}/day baseline). Excess spend ≈ ${excess:,.2f}."
                    ),
                    recommendation=(
                        f"Investigate what changed in {app} on {worst['day']} "
                        "(deploys, config, input volume) and add a per-app daily budget alert."
                    ),
                    data={"app": app, "baseline_daily_usd": round(mu, 2), "anomalies": anomalies},
                ))
        return findings
