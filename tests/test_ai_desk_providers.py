"""AI desk provider adapters against a LOCAL stub HTTP server (127.0.0.1).

No real provider is contacted and no paid call is made: every adapter is
pointed at an in-process server that scripts each response (status, headers,
body, delay, abrupt disconnect). Covers success for both wire formats, rate
limits with Retry-After, server errors and the circuit breaker (including
half-open), timeouts, malformed / oversized / wrongly-shaped bodies, invalid
model output, a missing key, token and cost budgets, the per-cycle deadline,
adapter bugs, and that the API key never reaches the journal.

Also covers the market-data freshness classification and the launcher /
`serve` refusals added during verification.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from ai_desk_fixtures import NOW, build_trading_db

from adaptive_scalper.ai_desk.agents.market_analyst import freshness_report
from adaptive_scalper.ai_desk.configuration import AiDeskConfig, ProviderConfig
from adaptive_scalper.ai_desk.journal import Journal
from adaptive_scalper.ai_desk.orchestration import DeskOrchestrator
from adaptive_scalper.ai_desk.providers.base import (
    MAX_RETRY_AFTER_SECONDS,
    Provider,
    ProviderRouter,
)
from adaptive_scalper.ai_desk.providers.http import MAX_RESPONSE_BYTES
from adaptive_scalper.config.loader import AppConfig

KEY_ENV = "AI_DESK_STUB_KEY"
KEY = "sk-ant-STUBSECRET0123456789abcdef"
GOOD = json.dumps({"narrative": "Evidence is incomplete; no edge is supported.", "cited_evidence_ids": [],
                   "counterarguments": ["small samples"]})


# ------------------------------------------------------------------ stub server
class Stub:
    """Scripted responses: each item is a dict(status, body, headers, delay, drop)."""

    def __init__(self) -> None:
        self.script: list[dict] = []
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                stub.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": json.loads(self.rfile.read(length) or b"{}")})
                item = stub.script.pop(0) if stub.script else {"status": 500, "body": "{}"}
                if item.get("delay"):
                    time.sleep(item["delay"])
                if item.get("drop"):
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n{\"trunc")
                    self.wfile.flush()
                    self.close_connection = True
                    return
                body = item.get("body", "{}")
                data = body if isinstance(body, bytes) else body.encode()
                self.send_response(item.get("status", 200))
                for k, v in (item.get("headers") or {}).items():
                    self.send_header(k, v)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def openai_body(text: str, prompt=120, completion=40) -> str:
    return json.dumps({"model": "stub-chat", "choices": [{"message": {"content": text}}],
                       "usage": {"prompt_tokens": prompt, "completion_tokens": completion}})


def anthropic_body(text: str, inp=110, out=30) -> str:
    return json.dumps({"model": "stub-claude", "content": [{"type": "text", "text": text}],
                       "usage": {"input_tokens": inp, "output_tokens": out}})


def make(tmp_path: Path, stub: Stub, *, kind="openai_compatible", tokens=100_000, usd=1.0, retries=2,
         timeout=2.0, breaker=3, cooldown=600.0, in_cost=0.0, out_cost=0.0):
    base = stub.url + ("/v1/messages" if kind == "anthropic" else "/v1")
    cfg = AiDeskConfig(
        journal_path=str(tmp_path / "j.sqlite3"), provider="ext", fallback_providers=["deterministic"],
        llm_agents=["coordinator"], allowed_external_data=["risk_summary"],
        budget={"daily_token_budget": tokens, "daily_cost_budget_usd": usd},
        providers=[ProviderConfig(name="ext", kind=kind, model="stub-model", base_url=base, api_key_env=KEY_ENV,
                                  timeout_seconds=timeout, max_retries=retries, backoff_seconds=0.01,
                                  min_seconds_between_requests=0, circuit_breaker_failures=breaker,
                                  circuit_breaker_cooldown_seconds=cooldown,
                                  input_cost_per_1k_tokens_usd=in_cost, output_cost_per_1k_tokens_usd=out_cost),
                   ProviderConfig()])
    journal = Journal(cfg.journal_path)
    return cfg, journal


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def router_for(cfg, journal, clock: Clock | None = None) -> ProviderRouter:
    if clock is None:
        return ProviderRouter(cfg, journal, sleep=lambda s: None)
    return ProviderRouter(cfg, journal, sleep=clock.sleep, clock=clock)


def ask(router: ProviderRouter, n=0, **kw):
    return router.narrate(agent="coordinator", category="risk_summary", digest={"n": n},
                          allowed_evidence_ids=frozenset(), **kw)


def calls(journal) -> list[tuple]:
    return [tuple(r) for r in journal.conn.execute(
        "SELECT status, input_tokens, output_tokens, error FROM provider_calls ORDER BY id").fetchall()]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv(KEY_ENV, KEY)


# ---------------------------------------------------------------- success paths
def test_openai_compatible_success_sends_bearer_and_logs_usage(tmp_path, stub):
    cfg, j = make(tmp_path, stub)
    stub.script = [{"body": openai_body(GOOD)}]
    out = ask(router_for(cfg, j))
    assert out.status == "OK" and out.narrative["narrative"].startswith("Evidence is incomplete")
    req = stub.requests[0]
    assert req["path"] == "/v1/chat/completions" and req["headers"]["authorization"] == f"Bearer {KEY}"
    assert req["body"]["temperature"] == 0 and req["body"]["max_tokens"] == 600
    assert calls(j) == [("OK", 120, 40, None)]
    j.close()


def test_anthropic_success_uses_the_messages_wire_format(tmp_path, stub):
    cfg, j = make(tmp_path, stub, kind="anthropic")
    stub.script = [{"body": anthropic_body(GOOD)}]
    out = ask(router_for(cfg, j))
    assert out.status == "OK"
    req = stub.requests[0]
    assert req["headers"]["x-api-key"] == KEY and req["headers"]["anthropic-version"]
    assert req["body"]["system"] and all(m["role"] != "system" for m in req["body"]["messages"])
    assert calls(j) == [("OK", 110, 30, None)]
    j.close()


def test_a_cached_narrative_makes_no_second_request(tmp_path, stub):
    cfg, j = make(tmp_path, stub)
    stub.script = [{"body": openai_body(GOOD)}]
    r = router_for(cfg, j)
    assert ask(r).status == "OK" and ask(r).status == "CACHED"
    assert len(stub.requests) == 1
    j.close()


# --------------------------------------------------------------- rate limiting
def test_429_retry_after_is_honoured_then_succeeds(tmp_path, stub):
    cfg, j = make(tmp_path, stub)
    clock = Clock()
    stub.script = [{"status": 429, "headers": {"Retry-After": "7"}}, {"body": openai_body(GOOD)}]
    out = ask(router_for(cfg, j, clock))
    assert out.status == "OK" and len(stub.requests) == 2
    assert 7.0 in clock.slept
    assert [c[0] for c in calls(j)] == ["ERROR", "OK"] and "HTTP 429" in calls(j)[0][3]
    j.close()


def test_retry_after_is_capped(tmp_path, stub):
    cfg, j = make(tmp_path, stub)
    clock = Clock()
    stub.script = [{"status": 429, "headers": {"Retry-After": "3600"}}, {"body": openai_body(GOOD)}]
    assert ask(router_for(cfg, j, clock)).status == "OK"
    assert max(clock.slept) == MAX_RETRY_AFTER_SECONDS
    j.close()


def test_min_interval_between_requests_is_enforced(tmp_path, stub):
    cfg, j = make(tmp_path, stub)
    cfg.providers[0] = cfg.providers[0].model_copy(update={"min_seconds_between_requests": 5.0})
    clock = Clock()
    stub.script = [{"body": openai_body(GOOD)}, {"body": openai_body(GOOD)}]
    r = router_for(cfg, j, clock)
    assert ask(r, 1).status == "OK" and ask(r, 2).status == "OK"
    assert 5.0 in clock.slept
    j.close()


# ------------------------------------------------- server errors and the breaker
def test_persistent_500_fails_over_and_opens_the_circuit(tmp_path, stub):
    cfg, j = make(tmp_path, stub, retries=1, breaker=2)
    stub.script = [{"status": 500}] * 10
    r = router_for(cfg, j)
    out = ask(r, 1)
    assert out.narrative is None and out.attempts[-1]["status"] == "DETERMINISTIC_FALLBACK"
    assert len(stub.requests) == 2 and r.status()["ext"]["circuit"] == "OPEN"
    out2 = ask(r, 2)
    assert out2.attempts[0]["status"] == "CIRCUIT_OPEN" and len(stub.requests) == 2   # no new request
    j.close()


def test_half_open_circuit_reopens_after_one_failure(tmp_path, stub):
    cfg, j = make(tmp_path, stub, retries=0, breaker=2, cooldown=60)
    clock = Clock()
    stub.script = [{"status": 503}] * 10
    r = router_for(cfg, j, clock)
    ask(r, 1)
    ask(r, 2)
    assert r.status()["ext"]["circuit"] == "OPEN" and len(stub.requests) == 2
    clock.t += 61                                  # cooldown elapsed -> one trial call allowed
    ask(r, 3)
    assert len(stub.requests) == 3 and r.status()["ext"]["circuit"] == "OPEN"     # one failure re-opened it
    ask(r, 4)
    assert len(stub.requests) == 3
    j.close()


def test_a_4xx_client_error_is_not_retried(tmp_path, stub):
    cfg, j = make(tmp_path, stub, retries=3)
    stub.script = [{"status": 400}] * 5
    assert ask(router_for(cfg, j)).narrative is None
    assert len(stub.requests) == 1
    j.close()


# ------------------------------------------------------------------- timeouts
def test_a_slow_server_times_out_and_the_call_is_bounded(tmp_path, stub):
    cfg, j = make(tmp_path, stub, retries=1, timeout=0.3)
    stub.script = [{"delay": 1.0, "body": openai_body(GOOD)}] * 2
    started = time.monotonic()
    out = ask(router_for(cfg, j))
    elapsed = time.monotonic() - started
    assert out.narrative is None and out.attempts[0]["status"] == "FAILED"
    assert elapsed < 3.0                              # 2 attempts x 0.3 s timeout, not 2 x 1 s
    assert all(c[0] == "ERROR" and "timeout" in c[3] for c in calls(j))
    j.close()


def test_the_cycle_deadline_stops_new_calls(tmp_path, stub):
    cfg, j = make(tmp_path, stub)
    clock = Clock()
    stub.script = [{"body": openai_body(GOOD)}]
    out = ask(router_for(cfg, j, clock), deadline=clock.t - 1)
    assert out.status == "DEADLINE_EXCEEDED" and stub.requests == []
    j.close()


def test_a_retry_that_would_cross_the_deadline_is_not_attempted(tmp_path, stub):
    cfg, j = make(tmp_path, stub, retries=3)
    clock = Clock()
    stub.script = [{"status": 429, "headers": {"Retry-After": "30"}}] * 4
    out = ask(router_for(cfg, j, clock), deadline=clock.t + 10)
    assert out.status == "DEADLINE_EXCEEDED" and len(stub.requests) == 1
    j.close()


# ------------------------------------------------------------------- bad bodies
@pytest.mark.parametrize("item", [
    {"body": "this is not json"},
    {"body": json.dumps({"unexpected": "shape"})},
    {"body": b"{" + b" " * (MAX_RESPONSE_BYTES + 10) + b"}"},
    {"drop": True},
], ids=["malformed-json", "wrong-shape", "oversized", "connection-dropped"])
def test_bad_bodies_fail_cleanly_without_raising(tmp_path, stub, item):
    cfg, j = make(tmp_path, stub, retries=0)
    stub.script = [item]
    out = ask(router_for(cfg, j))
    assert out.narrative is None and out.attempts[0]["status"] == "FAILED"
    assert calls(j)[0][0] == "ERROR"
    j.close()


def test_invalid_model_output_is_rejected_and_counts_toward_the_breaker(tmp_path, stub):
    cfg, j = make(tmp_path, stub, breaker=2)
    bad = json.dumps({"narrative": "Buy XAUUSD now at market.", "cited_evidence_ids": [], "counterarguments": []})
    stub.script = [{"body": openai_body(bad)}] * 3
    r = router_for(cfg, j)
    assert ask(r, 1).attempts[0]["status"] == "REJECTED"
    assert ask(r, 2).attempts[0]["status"] == "REJECTED"
    assert r.status()["ext"]["circuit"] == "OPEN"
    assert ask(r, 3).attempts[0]["status"] == "CIRCUIT_OPEN" and len(stub.requests) == 2
    j.close()


def test_a_missing_key_never_sends_a_request(tmp_path, stub, monkeypatch):
    monkeypatch.delenv(KEY_ENV)
    cfg, j = make(tmp_path, stub, kind="anthropic")
    out = ask(router_for(cfg, j))
    assert out.narrative is None and stub.requests == []
    assert "is not set" in calls(j)[0][3]
    j.close()


def test_an_adapter_bug_never_escapes_the_router(tmp_path, stub):
    cfg, j = make(tmp_path, stub)

    class Broken(Provider):
        kind = "openai_compatible"

        def complete(self, messages, *, max_tokens, timeout):
            raise RuntimeError("bug")

    r = ProviderRouter(cfg, j, providers={"ext": Broken(cfg.provider_config("ext")),
                                          "deterministic": Provider(cfg.provider_config("deterministic"))},
                       sleep=lambda s: None)
    out = ask(r)
    assert out.narrative is None and "adapter error: RuntimeError" in calls(j)[0][3]
    j.close()


# ---------------------------------------------------------------------- budgets
def test_actual_token_usage_exhausts_the_daily_budget(tmp_path, stub):
    cfg, j = make(tmp_path, stub, tokens=1500)
    stub.script = [{"body": openai_body(GOOD, prompt=700, completion=100)}] * 3
    r = router_for(cfg, j)
    assert ask(r, 1).status == "OK"                     # 800 used
    out = ask(r, 2)                                      # 800 + estimate (> 600 output) > 1500
    assert out.attempts[0]["status"] == "BUDGET_EXHAUSTED" and len(stub.requests) == 1
    j.close()


def test_the_cost_budget_blocks_paid_calls(tmp_path, stub):
    cfg, j = make(tmp_path, stub, usd=0.05, in_cost=0.03, out_cost=0.06)
    stub.script = [{"body": openai_body(GOOD, prompt=1000, completion=500)}] * 3
    r = router_for(cfg, j)
    assert ask(r, 1).status == "OK"                     # 0.03 + 0.03 = 0.06 USD spent
    assert ask(r, 2).attempts[0]["status"] == "BUDGET_EXHAUSTED"
    assert len(stub.requests) == 1
    assert j.usage_since(0)["cost_usd"] == pytest.approx(0.06)
    j.close()


# --------------------------------------------------------------------- secrets
def test_the_api_key_never_reaches_the_journal(tmp_path, stub):
    cfg, j = make(tmp_path, stub, retries=0)
    stub.script = [{"status": 500}, {"body": openai_body(GOOD)}]
    r = router_for(cfg, j)
    ask(r, 1)
    ask(r, 2)
    dump = "\n".join(j.conn.iterdump())
    assert KEY not in dump and "STUBSECRET" not in dump
    sent = json.dumps([req["body"] for req in stub.requests])
    assert KEY not in sent                                # the key is a header, never prompt content
    j.close()


# ------------------------------------------------- the cycle with a real adapter
def test_a_full_cycle_against_the_stub_stays_deterministic(tmp_path, stub):
    db = build_trading_db(tmp_path / "trading.sqlite3")
    cfg, j = make(tmp_path, stub, retries=0)
    stub.script = [{"status": 503}] * 20
    orch = DeskOrchestrator(cfg, trading_db_path=db, journal=j, trading_config=AppConfig())
    report = orch.run_cycle(as_of_utc=NOW)
    assert report.status == "COMPLETED"
    coord = next(r for r in report.results if r.agent == "coordinator")
    assert coord.llm_narrative is None and coord.model_provider == "deterministic"
    j.close()


# ------------------------------------------------------------- data freshness
def test_freshness_states():
    assert freshness_report(30, None, 5, 1800, True)["state"] == "FRESH_BARS"
    obs = freshness_report(10 * 86400, 60, 5, 1800, True)
    assert obs["state"] == "RUNTIME_OBSERVER_ONLY" and obs["remediation"]
    assert freshness_report(10 * 86400, None, 5, 1800, True)["state"] == "RUNTIME_LIVE_NO_OBSERVER"
    down = freshness_report(10 * 86400, 60, 5, 1800, False)
    assert down["state"] == "RUNTIME_DOWN" and "never opens MT5" in down["remediation"]


def test_stale_market_view_uses_the_newest_source(tmp_path):
    db = build_trading_db(tmp_path / "trading.sqlite3", runtime_live=False, bars_age_seconds=10 * 86400)
    cfg = AiDeskConfig(journal_path=str(tmp_path / "ai.sqlite3"))
    j = Journal(cfg.journal_path, trading_db_path=db)
    report = DeskOrchestrator(cfg, trading_db_path=db, journal=j, trading_config=AppConfig()).run_cycle(
        as_of_utc=NOW, agents={"market_analyst"})
    xau = next(r for r in report.results if r.agent == "market_analyst" and r.symbol == "XAUUSD")
    assert xau.status == "STALE"
    f = xau.findings
    assert f["data_freshness"]["state"] == "RUNTIME_DOWN" and f["data_freshness"]["quote_age_seconds"] > 1800
    assert f["last_known_source"] == "runtime_observer"            # observer (330 s) is newer than bars (10 d)
    assert f["regime"] == "RANGE" and f["vol_bucket"] is None      # nothing taken from the 10-day-old bars
    claim = next(c for c in xau.claims if c.claim_id == "market:XAUUSD:condition")
    source_of = dict(j.conn.execute("SELECT evidence_id, source FROM evidence WHERE snapshot_id=?",
                                    (report.snapshot_id,)).fetchall())
    assert claim.evidence_ids and all(source_of[e].startswith("runtime_state:") for e in claim.evidence_ids)
    j.close()


def test_fresh_bars_are_used_when_present(tmp_path):
    db = build_trading_db(tmp_path / "trading.sqlite3")
    cfg = AiDeskConfig(journal_path=str(tmp_path / "ai.sqlite3"))
    j = Journal(cfg.journal_path, trading_db_path=db)
    report = DeskOrchestrator(cfg, trading_db_path=db, journal=j, trading_config=AppConfig()).run_cycle(
        as_of_utc=NOW, agents={"market_analyst"})
    xau = next(r for r in report.results if r.agent == "market_analyst" and r.symbol == "XAUUSD")
    assert xau.findings["data_freshness"]["state"] == "FRESH_BARS" and xau.findings["basis"] == "closed_bars"
    j.close()


# ------------------------------------------------------------ launcher / serve
def test_launcher_stops_when_the_self_check_fails():
    text = (Path(__file__).resolve().parents[1] / "START AI DESK.bat").read_text(encoding="utf-8")
    doctor, guard, serve = text.index("ai-desk doctor"), text.index("if errorlevel 1"), text.index("ai-desk serve")
    assert doctor < guard < serve
    assert "exit /b 1" in text[guard:serve]


def test_serve_refuses_before_announcing_itself(tmp_path, capsys):
    from adaptive_scalper.cli import main

    db = build_trading_db(tmp_path / "trading.sqlite3")
    trading = tmp_path / "trading.toml"
    trading.write_text(f'mode = "PAPER"\n[database]\npath = "{Path(db).as_posix()}"\n', encoding="utf-8")
    desk = tmp_path / "desk.toml"
    desk.write_text(f'journal_path = "{(tmp_path / "ai.sqlite3").as_posix()}"\n', encoding="utf-8")
    assert main(["--config", str(trading), "ai-desk", "--ai-config", str(desk), "serve", "--max-ticks", "1"]) == 1
    captured = capsys.readouterr()
    assert "Polling" not in captured.out and "disabled" in captured.err
    assert not (tmp_path / "ai.sqlite3").exists()          # refused before even opening the journal
