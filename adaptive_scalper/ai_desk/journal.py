"""Persistent AI research journal: `data/ai_desk.sqlite3`.

A separate SQLite database (WAL, its own ordered migrations), so the desk
never becomes a second writer of the trading database. Writes are grouped in
`BEGIN IMMEDIATE` transactions (crash-safe: a run either records all of an
agent result or none of it). Idempotency comes from deterministic keys:
`analysis_id` (same snapshot + agent + config -> same id, `INSERT OR IGNORE`),
`processed_events.event_key`, and the disagreement uniqueness constraint, so a
retry or a restart never duplicates research records. `agent_results` is
append-only (an UPDATE trigger aborts). Retention deletes rows older than the
configured number of days.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from adaptive_scalper.ai_desk import DESK_VERSION
from adaptive_scalper.ai_desk.contracts import (
    AgentResult,
    Disagreement,
    Snapshot,
    canonical_json,
    sha,
)
from adaptive_scalper.persistence.database import _split_statements, connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DIGEST_KINDS = frozenset({"MARKET_BARS", "MARKET_BARS_M1", "DEMO_TRADES", "SHADOW_CANDIDATES", "NEWS_HISTORY"})


class JournalError(Exception):
    pass


def _now() -> int:
    return int(time.time())


def migrate_journal(conn: sqlite3.Connection) -> list[int]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                 "applied_at_utc INTEGER NOT NULL)")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.stem.split("_", 1)[0])
        if version in done:
            continue
        try:
            conn.execute("BEGIN")
            for stmt in _split_statements(path.read_text(encoding="utf-8")):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (version, path.stem, _now()))
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise JournalError(f"journal migration {path.name} failed: {exc}") from exc
        applied.append(version)
    return applied


class Journal:
    def __init__(self, path: str | Path, *, trading_db_path: str | Path | None = None) -> None:
        if trading_db_path is not None and Path(path).resolve() == Path(trading_db_path).resolve():
            raise JournalError("the AI desk journal must not be the trading database")
        self.path = str(path)
        self.conn = connect(path)
        self.conn.execute("PRAGMA busy_timeout = 5000")
        migrate_journal(self.conn)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ----------------------------------------------------------------- runs
    def start_run(self, *, trigger: str, mode: str, config_fingerprint: str, agents: list[str],
                  now: int | None = None) -> str:
        started = now or _now()
        run_id = "run-" + sha(f"{trigger}|{mode}|{started}|{time.perf_counter_ns()}", 16)
        with self.tx() as c:
            c.execute("INSERT INTO desk_runs (run_id, trigger, mode, started_at_utc, status, config_fingerprint, "
                      "desk_version, agents_json) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?)",
                      (run_id, trigger[:200], mode, started, config_fingerprint, DESK_VERSION, json.dumps(agents)))
        return run_id

    def finish_run(self, run_id: str, *, status: str, snapshot_id: str | None, error: str | None = None) -> None:
        with self.tx() as c:
            c.execute("UPDATE desk_runs SET status=?, snapshot_id=COALESCE(?, snapshot_id), finished_at_utc=?, "
                      "error=? WHERE run_id=?", (status, snapshot_id, _now(), (error or None) and error[:1000], run_id))

    def recover_incomplete_runs(self) -> int:
        """Startup recovery: a run left RUNNING by a crash is ABANDONED (its
        already-committed results stay; nothing is half-written)."""
        with self.tx() as c:
            return c.execute("UPDATE desk_runs SET status='ABANDONED', finished_at_utc=?, "
                             "error='process ended before the run finished' WHERE status='RUNNING'",
                             (_now(),)).rowcount

    # ------------------------------------------------------------ snapshots
    def record_snapshot(self, snapshot: Snapshot) -> bool:
        with self.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (snapshot.snapshot_id, snapshot.as_of_utc, snapshot.mode, int(snapshot.runtime_live),
                             json.dumps(list(snapshot.limitations)), len(snapshot.evidence), _now()))
            if cur.rowcount == 0:
                return False
            for e in snapshot.evidence:
                if e.kind in DIGEST_KINDS:
                    storage = "DIGEST"
                    payload = canonical_json({"sha256": sha(e.payload_json, 64), "bytes": len(e.payload_json),
                                              "reconstruct": "rebuild from the trading DB with the snapshot as-of"})
                else:
                    storage, payload = "FULL", e.payload_json
                c.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (snapshot.snapshot_id, e.evidence_id, e.source, e.kind, e.symbol, e.data_timestamp_utc,
                           storage, payload))
        return True

    # -------------------------------------------------------------- results
    def record_result(self, run_id: str, result: AgentResult, *, as_of_epoch: int,
                      validation: str = "VALID") -> bool:
        """Returns False when this exact analysis was already journaled."""
        with self.tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO agent_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.analysis_id, run_id, result.agent, result.agent_version, result.symbol, result.snapshot_id,
                 as_of_epoch, result.status, result.assessment, result.confidence, result.model_provider,
                 result.model_id, result.duration_ms, result.config_fingerprint, validation,
                 result.model_dump_json(), _now()))
            inserted = cur.rowcount == 1
            if inserted:
                for d in result.disagreements:
                    self._disagreement(c, run_id, d)
                fc = result.findings.get("forward_check")
                if fc:
                    due = int(fc["from_bar_time_utc"]) + int(fc["horizon_bars"]) * int(fc.get("bar_seconds", 300))
                    c.execute("INSERT OR IGNORE INTO forward_checks VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, "
                              "NULL, NULL, ?)",
                              ("fc-" + result.analysis_id, result.analysis_id, result.agent, result.symbol,
                               fc["metric"], fc["expected"], canonical_json(fc), due, _now()))
        return inserted

    def record_disagreements(self, run_id: str, items: list[Disagreement]) -> int:
        with self.tx() as c:
            return sum(self._disagreement(c, run_id, d) for d in items)

    @staticmethod
    def _disagreement(c, run_id: str, d: Disagreement) -> int:
        return c.execute("INSERT OR IGNORE INTO disagreements (run_id, topic, parties_json, positions_json, severity, "
                         "resolution, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (run_id, d.topic, canonical_json(d.parties), canonical_json(d.positions), d.severity,
                          d.resolution, _now())).rowcount

    def latest_results(self, agent: str | None = None, *, limit: int = 50) -> list[dict]:
        q = "SELECT result_json, created_at_utc FROM agent_results"
        args: tuple = ()
        if agent:
            q += " WHERE agent=?"
            args = (agent,)
        q += " ORDER BY as_of_utc DESC, created_at_utc DESC LIMIT ?"
        return [json.loads(r[0]) for r in self.conn.execute(q, (*args, limit))]

    def prior_market_assessments(self, before_utc: int) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.conn.execute(
                "SELECT symbol, as_of_utc, result_json FROM agent_results WHERE agent='market_analyst' "
                "AND as_of_utc < ? AND status IN ('COMPLETE', 'INCOMPLETE') ORDER BY as_of_utc DESC", (before_utc,)):
            if r[0] in out:
                continue
            f = json.loads(r[2])["findings"]
            out[r[0]] = {"condition": f.get("condition"), "regime": f.get("regime"), "trend": f.get("trend"),
                         "vol_bucket": f.get("vol_bucket"), "as_of": json.loads(r[2])["as_of_utc"],
                         "as_of_epoch": r[1]}
        return out

    # ------------------------------------------------------------- providers
    def record_provider_call(self, *, run_id: str | None, provider: str, model: str, agent: str | None, status: str,
                             latency_ms: int, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0,
                             error: str | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO provider_calls (run_id, provider, model, agent, status, latency_ms, input_tokens, "
                      "output_tokens, cost_usd, error, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (run_id, provider, model, agent, status, latency_ms, input_tokens, output_tokens, cost_usd,
                       (error or None) and error[:500], _now()))

    def usage_since(self, since_utc: int) -> dict:
        r = self.conn.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0), COALESCE(SUM(cost_usd), 0), "
                              "COUNT(*) FROM provider_calls WHERE created_at_utc >= ?", (since_utc,)).fetchone()
        return {"tokens": int(r[0]), "cost_usd": float(r[1]), "calls": int(r[2])}

    def cache_get(self, key: str, ttl_seconds: int) -> dict | None:
        r = self.conn.execute("SELECT response_json, created_at_utc FROM narrative_cache WHERE cache_key=?",
                              (key,)).fetchone()
        if r is None or _now() - r[1] > ttl_seconds:
            return None
        return json.loads(r[0])

    def cache_put(self, key: str, value: dict) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO narrative_cache VALUES (?, ?, ?)", (key, canonical_json(value), _now()))

    # --------------------------------------------------------------- events
    def mark_event(self, event_key: str, event_type: str, run_id: str | None = None) -> bool:
        """True if the event is new (first time seen); False for a duplicate."""
        with self.tx() as c:
            return c.execute("INSERT OR IGNORE INTO processed_events VALUES (?, ?, ?, ?)",
                             (event_key, event_type, _now(), run_id)).rowcount == 1

    def get_watermark(self, name: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM watermarks WHERE name=?", (name,)).fetchone()
        return default if r is None else r[0]

    def set_watermark(self, name: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO watermarks VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, "
                      "updated_at_utc=excluded.updated_at_utc", (name, str(value), _now()))

    # -------------------------------------------------------- forward checks
    def pending_forward_checks(self, due_before_utc: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM forward_checks WHERE status='PENDING' AND due_at_utc <= ? "
                                 "ORDER BY due_at_utc", (due_before_utc,)).fetchall()
        return [dict(r) for r in rows]

    def score_forward_check(self, check_id: str, *, status: str, outcome: dict, hit: bool | None) -> None:
        with self.tx() as c:
            c.execute("UPDATE forward_checks SET status=?, outcome_json=?, hit=?, scored_at_utc=? "
                      "WHERE check_id=? AND status='PENDING'",
                      (status, canonical_json(outcome), None if hit is None else int(hit), _now(), check_id))

    def forward_check_rows(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM forward_checks ORDER BY due_at_utc")]

    # ------------------------------------------------------------ evaluations
    def record_evaluation(self, *, kind: str, protocol_version: str, protocol_hash: str, data_range: dict,
                          result: dict) -> tuple[str, int]:
        with self.tx() as c:
            trial = c.execute("SELECT COUNT(*) FROM evaluations WHERE kind=?", (kind,)).fetchone()[0] + 1
            eid = "eval-" + sha(f"{kind}|{protocol_hash}|{canonical_json(data_range)}|{trial}|{_now()}", 16)
            c.execute("INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (eid, kind, protocol_version, protocol_hash, canonical_json(data_range), canonical_json(result),
                       trial, _now()))
        return eid, trial

    def evaluations(self, kind: str | None = None) -> list[dict]:
        q = "SELECT * FROM evaluations" + (" WHERE kind=?" if kind else "") + " ORDER BY created_at_utc"
        return [dict(r) for r in self.conn.execute(q, (kind,) if kind else ())]

    def prior_evaluations_on_range(self, kind: str, data_range: dict) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM evaluations WHERE kind=? AND data_range_json=?",
                                 (kind, canonical_json(data_range))).fetchone()[0]

    # ---------------------------------------------------------- service state
    def put_state(self, key: str, value) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO service_state VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                      "value_json=excluded.value_json, updated_at_utc=excluded.updated_at_utc",
                      (key, canonical_json(value), _now()))

    def get_state(self, key: str, default=None):
        r = self.conn.execute("SELECT value_json, updated_at_utc FROM service_state WHERE key=?", (key,)).fetchone()
        return default if r is None else {**json.loads(r[0]), "_updated_at_utc": r[1]} \
            if isinstance(json.loads(r[0]), dict) else json.loads(r[0])

    # -------------------------------------------------------------- retention
    def prune(self, retention_days: int, *, now: int | None = None) -> dict:
        cutoff = (now or _now()) - retention_days * 86400
        with self.tx() as c:
            out = {
                "agent_results": c.execute("DELETE FROM agent_results WHERE created_at_utc < ?", (cutoff,)).rowcount,
                "evidence": c.execute("DELETE FROM evidence WHERE snapshot_id IN (SELECT snapshot_id FROM snapshots "
                                      "WHERE created_at_utc < ?)", (cutoff,)).rowcount,
                "snapshots": c.execute("DELETE FROM snapshots WHERE created_at_utc < ?", (cutoff,)).rowcount,
                "disagreements": c.execute("DELETE FROM disagreements WHERE created_at_utc < ?", (cutoff,)).rowcount,
                "provider_calls": c.execute("DELETE FROM provider_calls WHERE created_at_utc < ?", (cutoff,)).rowcount,
                "processed_events": c.execute("DELETE FROM processed_events WHERE first_seen_utc < ?",
                                              (cutoff,)).rowcount,
                "desk_runs": c.execute("DELETE FROM desk_runs WHERE started_at_utc < ?", (cutoff,)).rowcount,
                "narrative_cache": c.execute("DELETE FROM narrative_cache WHERE created_at_utc < ?", (cutoff,)).rowcount,
            }
        return out
