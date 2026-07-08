# TokenOps

A multi-agent system for LLM API cost management — the core engine for a SaaS
platform that acts like an intelligent operations team, not a dashboard. Eight
specialized agents continuously monitor usage, attribute spend, detect waste,
recommend cheaper models, find caching opportunities, forecast budgets, and
turn everything into a ranked, guardrailed action plan.

## Quick start

```bash
python3 -m tokenops demo                 # full agent run on 30 days of synthetic traffic
python3 -m tokenops demo --budget 4000   # adds budget-breach forecasting
python3 -m tokenops export findings.json # machine-readable findings

# Optional Claude reasoning layer (executive summaries + conversational Q&A):
pip install anthropic                    # then set ANTHROPIC_API_KEY or `ant auth login`
python3 -m tokenops demo --llm
python3 -m tokenops ask "Why is rag-search our top spend driver, and what do we do first?"
```

No dependencies are required for the core system — it runs on the Python
standard library. The `anthropic` package enables the LLM layer.

## The agent team

| Agent | Mission | Example finding |
|---|---|---|
| **cost-analyst** | Attribute every dollar to an app, model, and task | "rag-search is 40% of spend" |
| **usage-monitor** | Z-score anomaly detection on per-app daily spend | "eval-pipeline spiked 4σ on July 2" |
| **model-advisor** | Right-size models per workload | "support-bot runs 11-token classifications on Opus — move to Sonnet" |
| **cache-strategist** | Find repeated prompt prefixes billing at full price | "12k-token prefix re-sent 47k times uncached: $1,330/mo recoverable" |
| **prompt-optimizer** | Detect prompt bloat via input:output ratios | "45:1 ratio where 20:1 is typical — trim 30%" |
| **waste-detector** | Retry burn, truncated outputs, missed batch discounts | "40k latency-insensitive calls skipping the 50%-off Batches API" |
| **forecaster** | OLS trend projection + budget-breach alerts | "Projected $6,496 vs $4,000 budget — 62% over" |
| **policy-engine** | Rank actions by savings, enforce guardrails, emit plan | "Wave 1: top 3 actions, $2,254/mo; quality-sensitive apps never auto-downgraded" |

## Architecture

```
                       ┌──────────────────────────────────────────┐
   LLM API traffic ───▶│  UsageStore (SQLite)                     │
   (gateway/middleware │  api_calls: tokens, cache stats, cost,   │
    writes call logs)  │  latency, retries, stop_reason, ...      │
                       └───────────────┬──────────────────────────┘
                                       │
                       ┌───────────────▼──────────────────────────┐
                       │  Orchestrator                            │
                       │  runs 7 analyst agents → Findings        │
                       │  policy-engine consumes all findings     │
                       │  → ranked, guardrailed action plan       │
                       └───────┬──────────────────┬───────────────┘
                               │                  │
                     deterministic report   Claude Opus 4.8 layer (optional)
                     (JSON / CLI, no deps)  • streamed executive summaries
                                            • tool-use Q&A over the store
                                            • prompt caching on the system prompt
```

**Design principles**

- **Deterministic core, LLM on top.** Every dollar figure comes from auditable
  SQL + pricing math, never from a model's imagination. Claude reads the
  findings and the store; it doesn't compute the numbers.
- **Findings are structured.** Each carries severity, an estimated monthly
  savings figure, a concrete recommendation, and the underlying data — so they
  can drive dashboards, alerts, or automation equally well.
- **Guardrails before automation.** Low-risk actions (caching, batching, retry
  config) are marked auto-apply; model downgrades route to human review, and
  quality-sensitive apps additionally require an eval gate. Changes ship in
  capped waves so regressions stay attributable.
- **Graceful degradation.** Without credentials, everything except the LLM
  synthesis still works.

## Toward a SaaS platform

The pieces this scaffold is designed to grow into:

1. **Ingestion** — replace the synthetic seeder with a gateway proxy or SDK
   middleware that logs every LLM call (the `api_calls` schema already models
   what you'd capture), plus provider usage/cost API pollers for reconciliation.
2. **Multi-tenancy** — org/workspace scoping on the store; per-tenant budgets
   and policies.
3. **Continuous operation** — agents run on schedules (monitor: minutes;
   analyst/advisor: daily; forecaster: weekly) with findings deduplicated
   across runs and lifecycle-tracked (open → acknowledged → resolved).
4. **Actuation** — the policy engine's auto-apply actions become gateway config
   changes (routing rules, cache headers, batch redirection) behind feature
   flags; the eval gate becomes a real offline A/B harness.
5. **Surface** — web dashboard over the findings JSON, Slack/email alerts for
   critical findings, and the `ask` endpoint as an embedded copilot.
6. **Hosted agents** — the Claude layer can graduate from a single synthesis
   call to Anthropic Managed Agents for long-running investigation sessions.

## Project layout

```
tokenops/
├── pricing.py          # model catalog, cache/batch multipliers, cost math
├── store.py            # SQLite usage store + synthetic traffic generator
├── orchestrator.py     # runs the team, aggregates + renders findings
├── llm.py              # Claude Opus 4.8 layer: summaries, tool-use Q&A
├── cli.py              # demo / ask / export commands
└── agents/
    ├── base.py         # Agent + Finding abstractions
    ├── cost_analyst.py
    ├── usage_monitor.py
    ├── model_advisor.py
    ├── cache_strategist.py
    ├── prompt_optimizer.py
    ├── waste_detector.py
    ├── forecaster.py
    └── policy_engine.py
```
