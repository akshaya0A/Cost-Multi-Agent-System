"""Agent framework.

Each agent owns one dimension of cost optimization. `analyze()` runs
deterministic analytics against the usage store and returns Findings —
structured, auditable results with a dollar figure attached wherever
possible. The orchestrator aggregates findings across agents and can hand
them to Claude for synthesis and conversational Q&A.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..store import UsageStore


@dataclass
class Finding:
    agent: str
    title: str
    detail: str
    severity: str = "info"                # info | warning | critical
    est_monthly_savings_usd: float = 0.0  # 0 when not a savings-type finding
    recommendation: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "title": self.title,
            "severity": self.severity,
            "est_monthly_savings_usd": round(self.est_monthly_savings_usd, 2),
            "detail": self.detail,
            "recommendation": self.recommendation,
            "data": self.data,
        }


class Agent(ABC):
    name: str = "agent"
    mission: str = ""

    @abstractmethod
    def analyze(self, store: UsageStore) -> list[Finding]: ...
