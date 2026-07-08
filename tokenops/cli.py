"""CLI entrypoints.

  python -m tokenops demo                      # seed synthetic data + full agent run
  python -m tokenops demo --budget 5000        # add a monthly budget for breach alerts
  python -m tokenops demo --llm                # append a Claude-written executive summary
  python -m tokenops ask "why did spend spike?"  # conversational Q&A (needs credentials)
  python -m tokenops export findings.json      # machine-readable findings
"""

import argparse
import sys

from .store import UsageStore
from .orchestrator import Orchestrator


def _build(budget: float | None):
    store = UsageStore()
    store.seed_synthetic()
    orch = Orchestrator(store, monthly_budget_usd=budget)
    return store, orch


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tokenops", description="Multi-agent LLM API cost optimization")
    sub = ap.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="Run the full agent team on synthetic usage data")
    demo.add_argument("--budget", type=float, default=None, help="Monthly budget in USD")
    demo.add_argument("--llm", action="store_true", help="Append a Claude-written executive summary")

    ask_p = sub.add_parser("ask", help="Ask the team a question (requires Anthropic credentials)")
    ask_p.add_argument("question")
    ask_p.add_argument("--budget", type=float, default=None)

    exp = sub.add_parser("export", help="Export findings as JSON")
    exp.add_argument("path")
    exp.add_argument("--budget", type=float, default=None)

    args = ap.parse_args(argv)
    store, orch = _build(args.budget)
    result = orch.run()

    if args.cmd == "demo":
        print(orch.render_report(result))
        if args.llm:
            print("\n--- Claude executive summary " + "-" * 48)
            from . import llm
            try:
                llm.executive_summary(result)
            except Exception as e:
                print(f"(LLM layer unavailable: {e})")
    elif args.cmd == "ask":
        from . import llm
        try:
            print(llm.ask(store, orch, result, args.question))
        except Exception as e:
            print(f"LLM layer unavailable: {e}", file=sys.stderr)
            return 1
    elif args.cmd == "export":
        with open(args.path, "w") as f:
            f.write(result.to_json())
        print(f"Wrote {len(result.findings)} findings to {args.path}")
    return 0
