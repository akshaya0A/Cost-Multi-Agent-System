"""Usage store: SQLite-backed log of LLM API calls, plus a synthetic-data generator.

In production this table would be fed by an SDK middleware / gateway proxy that
records every LLM call an org makes. For the demo we generate 30 days of
realistic traffic for five internal apps, with deliberate inefficiencies baked
in so each agent has something real to find:

  * support-bot        — simple classification/routing running on Opus (oversized model)
  * rag-search         — huge repeated context prefix, never cached
  * summarizer         — batch-eligible offline traffic running on the live API
  * code-assistant     — healthy workload (control group), plus a retry storm one day
  * eval-pipeline      — bloated prompts, truncated outputs, and a cost spike
"""

import hashlib
import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .pricing import call_cost

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    app TEXT NOT NULL,
    task_kind TEXT NOT NULL,          -- classification | rag_qa | summarization | codegen | evaluation
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    retry_of INTEGER,                 -- id of the call this retried, if any
    prompt_prefix_hash TEXT,          -- hash of the stable prompt prefix (system + tools)
    prefix_tokens INTEGER NOT NULL DEFAULT 0,
    max_tokens_requested INTEGER NOT NULL,
    stop_reason TEXT NOT NULL,        -- end_turn | max_tokens | refusal
    batched INTEGER NOT NULL DEFAULT 0,
    latency_sensitive INTEGER NOT NULL DEFAULT 1,
    quality_sensitive INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON api_calls (ts);
CREATE INDEX IF NOT EXISTS idx_calls_app ON api_calls (app);
"""


@dataclass
class AppProfile:
    name: str
    task_kind: str
    model: str
    calls_per_day: int
    input_mean: int
    output_mean: int
    prefix_tokens: int          # stable shared prefix (system prompt, tools, docs)
    latency_sensitive: bool
    quality_sensitive: bool


PROFILES = [
    AppProfile("support-bot", "classification", "claude-opus-4-8", 2400, 900, 12, 700, True, False),
    AppProfile("rag-search", "rag_qa", "claude-sonnet-5", 1800, 14000, 350, 12000, True, True),
    AppProfile("summarizer", "summarization", "claude-sonnet-5", 900, 6000, 500, 400, False, False),
    AppProfile("code-assistant", "codegen", "claude-opus-4-8", 700, 5000, 1800, 2500, True, True),
    AppProfile("eval-pipeline", "evaluation", "claude-sonnet-5", 1200, 9000, 200, 300, False, False),
]


class UsageStore:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, params).fetchall()

    def count(self) -> int:
        return self.query("SELECT COUNT(*) AS n FROM api_calls")[0]["n"]

    # ------------------------------------------------------------------ seed

    def seed_synthetic(self, days: int = 30, seed: int = 7, scale: float = 1.0) -> None:
        """Generate `days` of traffic (~195k calls at scale=1.0; seeds in <1s).
        Lower `scale` proportionally shrinks per-day volume."""
        rng = random.Random(seed)
        start = datetime(2026, 6, 7)
        rows = []

        for day in range(days):
            date = start + timedelta(days=day)
            weekday_factor = 0.55 if date.weekday() >= 5 else 1.0

            for prof in PROFILES:
                n_calls = max(4, int(prof.calls_per_day * scale * weekday_factor * rng.uniform(0.85, 1.15)))
                # eval-pipeline cost spike: a misconfigured sweep on days 24-25
                if prof.name == "eval-pipeline" and day in (24, 25):
                    n_calls *= 5
                # code-assistant retry storm on day 18 (flaky downstream tool)
                retry_storm = prof.name == "code-assistant" and day == 18

                prefix_hash = hashlib.sha1(f"{prof.name}-v3-prompt".encode()).hexdigest()[:12]

                for i in range(n_calls):
                    ts = date + timedelta(seconds=rng.randint(0, 86_399))
                    inp = max(50, int(rng.gauss(prof.input_mean, prof.input_mean * 0.25)))
                    out = max(1, int(rng.gauss(prof.output_mean, prof.output_mean * 0.35)))
                    # eval-pipeline requests undersized max_tokens -> truncation waste
                    if prof.name == "eval-pipeline":
                        max_req = 256
                        truncated = rng.random() < 0.22
                    else:
                        max_req = 16000
                        truncated = False
                    stop = "max_tokens" if truncated else "end_turn"
                    success = 0 if rng.random() < 0.015 else 1
                    latency = int(200 + inp * 0.02 + out * 3.5 * rng.uniform(0.8, 1.3))

                    rows.append(self._row(ts, prof, inp, out, latency, success, None,
                                          prefix_hash, max_req, stop))

                    # retries: normally rare; storm day retries ~35% of calls up to 3x
                    p_retry = 0.35 if retry_storm else 0.01
                    if rng.random() < p_retry:
                        for r in range(rng.randint(1, 3)):
                            rows.append(self._row(
                                ts + timedelta(seconds=2 + r), prof, inp, out,
                                latency, 1 if r > 0 else 0, -1,  # placeholder retry marker
                                prefix_hash, max_req, stop))

        self.db.executemany(
            """INSERT INTO api_calls
               (ts, app, task_kind, model, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, latency_ms, success, retry_of,
                prompt_prefix_hash, prefix_tokens, max_tokens_requested, stop_reason,
                batched, latency_sensitive, quality_sensitive, cost_usd)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.db.commit()

    def _row(self, ts, prof: AppProfile, inp, out, latency, success, retry_of,
             prefix_hash, max_req, stop):
        cost = call_cost(prof.model, inp, out)
        return (
            ts.isoformat(), prof.name, prof.task_kind, prof.model, inp, out,
            0, 0,  # nothing is cached in the seeded data — that's the point
            latency, success, retry_of, prefix_hash, prof.prefix_tokens,
            max_req, stop, 0,
            int(prof.latency_sensitive), int(prof.quality_sensitive), cost,
        )
