"""Claude reasoning layer (optional).

Uses the Anthropic SDK with Claude Opus 4.8:
  * adaptive thinking + streaming (per current API guidance)
  * prompt caching: the stable system prompt carries a cache_control
    breakpoint; per-run findings go after it so repeat questions in a session
    hit the cache
  * tool use: Claude can query the usage store and re-run agents to answer
    ad-hoc questions ("why did spend spike on the 1st?")

Everything degrades gracefully: if the `anthropic` package or credentials are
missing, callers get a clear message and the deterministic report still works.
"""

import json

from .orchestrator import Orchestrator, RunResult
from .store import UsageStore

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are the lead analyst of TokenOps, an AI operations team that \
optimizes a company's LLM API spend. You receive structured findings from seven \
specialist agents (cost attribution, anomaly monitoring, model right-sizing, prompt \
caching, prompt optimization, waste detection, forecasting) plus a policy engine's \
ranked action plan.

Write for an engineering leader deciding what to do this week. Lead with the total \
savings opportunity and the 2-3 actions that matter most. Ground every claim in the \
findings data — never invent numbers. Flag risk honestly: model downgrades need eval \
gates; caching and batching are near-zero-risk. Keep it under 400 words."""

TOOLS = [
    {
        "name": "query_usage",
        "description": (
            "Run a read-only SQL query against the api_calls usage table. Columns: "
            "ts, app, task_kind, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, latency_ms, success, retry_of, prompt_prefix_hash, "
            "prefix_tokens, max_tokens_requested, stop_reason, batched, "
            "latency_sensitive, quality_sensitive, cost_usd. "
            "Call this when the findings summary lacks the detail needed to answer — "
            "e.g. per-day breakdowns, specific apps, or hour-level patterns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}},
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def _client():
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "The `anthropic` package is not installed. Run `pip install anthropic` "
            "to enable the Claude reasoning layer."
        )
    return anthropic.Anthropic()  # resolves API key / OAuth profile from environment


def _run_sql(store: UsageStore, sql: str) -> str:
    if not sql.lstrip().lower().startswith("select"):
        return "Error: only SELECT statements are allowed."
    try:
        rows = store.query(sql)
        return json.dumps([dict(r) for r in rows[:200]], default=str)
    except Exception as e:  # surface DB errors to the model so it can correct
        return f"Error: {e}"


def executive_summary(result: RunResult) -> str:
    """Stream an executive report from Claude over the run's findings."""
    client = _client()
    findings_json = result.to_json()

    parts = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # stable prefix — cache it
        }],
        messages=[{
            "role": "user",
            "content": f"Here are this run's findings:\n\n{findings_json}\n\nWrite the executive report.",
        }],
    ) as stream:
        for text in stream.text_stream:
            parts.append(text)
            print(text, end="", flush=True)
    print()
    return "".join(parts)


def ask(store: UsageStore, orch: Orchestrator, result: RunResult, question: str) -> str:
    """Answer an ad-hoc question with tool access to the usage store (manual tool loop)."""
    client = _client()
    messages = [{
        "role": "user",
        "content": (
            f"Current findings:\n{result.to_json()}\n\n"
            f"Question from the team: {question}"
        ),
    }]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            return next((b.text for b in response.content if b.type == "text"), "")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "query_usage":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _run_sql(store, block.input["sql"]),
                })
        messages.append({"role": "user", "content": tool_results})
