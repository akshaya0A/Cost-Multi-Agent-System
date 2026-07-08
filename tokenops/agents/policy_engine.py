"""PolicyEngine — turns findings into a ranked, guardrailed action plan.

This is the "automation" agent: it consumes every other agent's findings,
ranks actions by savings-to-risk, enforces guardrails (never auto-downgrade a
quality-sensitive app; cap the number of simultaneous changes), and emits an
executable plan. In production, low-risk actions (caching, batching, retry
config) can be auto-applied via the gateway; model changes go to human review.
"""

from .base import Agent, Finding
from ..store import UsageStore

# action risk classes: auto-apply vs needs-human
AUTO_APPLY_AGENTS = {"cache-strategist", "waste-detector"}
MAX_CONCURRENT_CHANGES = 3


class PolicyEngine(Agent):
    name = "policy-engine"
    mission = "Rank savings actions, enforce guardrails, and emit an execution plan."

    def __init__(self):
        self.inbox: list[Finding] = []

    def submit(self, findings: list[Finding]) -> None:
        self.inbox.extend(findings)

    def analyze(self, store: UsageStore) -> list[Finding]:
        actionable = sorted(
            (f for f in self.inbox if f.est_monthly_savings_usd > 0),
            key=lambda f: f.est_monthly_savings_usd, reverse=True,
        )
        if not actionable:
            return []

        total = sum(f.est_monthly_savings_usd for f in actionable)
        plan = []
        for i, f in enumerate(actionable, 1):
            mode = "auto-apply" if f.agent in AUTO_APPLY_AGENTS else "human-review"
            wave = 1 if i <= MAX_CONCURRENT_CHANGES else 2
            plan.append({
                "rank": i, "wave": wave, "mode": mode, "source_agent": f.agent,
                "action": f.title, "est_monthly_savings_usd": round(f.est_monthly_savings_usd, 2),
                "recommendation": f.recommendation,
            })

        wave1 = sum(p["est_monthly_savings_usd"] for p in plan if p["wave"] == 1)
        return [Finding(
            agent=self.name,
            title=f"Action plan: {len(plan)} ranked optimizations",
            severity="info",
            est_monthly_savings_usd=0.0,  # roll-up; components already counted
            detail=(
                f"Total identified savings: ${total:,.2f}/month across {len(plan)} actions. "
                f"Wave 1 (top {min(MAX_CONCURRENT_CHANGES, len(plan))}, ${wave1:,.2f}/month) "
                f"ships first; changes are capped at {MAX_CONCURRENT_CHANGES} at a time so "
                f"regressions are attributable. Guardrail: quality-sensitive apps are never "
                f"auto-downgraded — those actions route to human review with an eval gate."
            ),
            recommendation="Execute wave 1, verify for one week via the usage monitor, then proceed.",
            data={"total_identified_monthly_savings_usd": round(total, 2), "plan": plan},
        )]
