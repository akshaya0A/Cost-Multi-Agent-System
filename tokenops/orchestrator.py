"""Orchestrator — runs the agent team and aggregates results.

Two layers:
  1. Deterministic: every agent computes findings from the usage store.
     Runs with zero API dependencies — this is the auditable, always-on core.
  2. LLM synthesis (optional): Claude Opus 4.8 turns raw findings into an
     executive report, and answers ad-hoc questions with tool access to the
     agents' data. Activates only when Anthropic credentials are available.
"""

import json
from dataclasses import dataclass, field

from .store import UsageStore
from .agents.base import Agent, Finding
from .agents.cost_analyst import CostAnalyst
from .agents.usage_monitor import UsageMonitor
from .agents.model_advisor import ModelAdvisor
from .agents.cache_strategist import CacheStrategist
from .agents.prompt_optimizer import PromptOptimizer
from .agents.waste_detector import WasteDetector
from .agents.forecaster import Forecaster
from .agents.policy_engine import PolicyEngine

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class RunResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def total_identified_savings(self) -> float:
        return sum(f.est_monthly_savings_usd for f in self.findings)

    def to_json(self) -> str:
        return json.dumps([f.to_dict() for f in self.findings], indent=2)


class Orchestrator:
    def __init__(self, store: UsageStore, monthly_budget_usd: float | None = None):
        self.store = store
        self.policy = PolicyEngine()
        self.analysts: list[Agent] = [
            CostAnalyst(),
            UsageMonitor(),
            ModelAdvisor(),
            CacheStrategist(),
            PromptOptimizer(),
            WasteDetector(),
            Forecaster(monthly_budget_usd),
        ]

    def run(self) -> RunResult:
        result = RunResult()
        for agent in self.analysts:
            findings = agent.analyze(self.store)
            result.findings.extend(findings)
            self.policy.submit(findings)
        # policy engine runs last — it consumes everyone else's output
        result.findings.extend(self.policy.analyze(self.store))
        result.findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.est_monthly_savings_usd))
        return result

    # ------------------------------------------------------------ reporting

    def render_report(self, result: RunResult) -> str:
        lines = ["=" * 78, "TOKENOPS — API COST OPTIMIZATION REPORT", "=" * 78]
        icons = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
        for f in result.findings:
            lines.append(f"\n{icons[f.severity]} [{f.agent}] {f.title}")
            if f.est_monthly_savings_usd > 0:
                lines.append(f"   💰 est. savings: ${f.est_monthly_savings_usd:,.2f}/month")
            lines.append(f"   {f.detail}")
            if f.recommendation:
                lines.append(f"   → {f.recommendation}")
        lines.append("\n" + "=" * 78)
        lines.append(f"TOTAL IDENTIFIED SAVINGS: ${result.total_identified_savings:,.2f}/month")
        lines.append("=" * 78)
        return "\n".join(lines)
