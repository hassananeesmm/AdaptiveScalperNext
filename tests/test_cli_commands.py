"""The complete operator CLI (completion directive Phase 8), driven end to
end against a temporary config + SQLite database seeded with stored bars
and a stored symbol spec -- exactly what `history bootstrap` and `symbols`
leave behind on the laptop. Broker-facing commands are verified to FAIL
cleanly without MetaTrader5 (the cloud has none); their live behaviour is
a LOCAL_MT5_HANDOFF.md item.
"""

from __future__ import annotations

import json
import sys

import pytest

from adaptive_scalper.cli import build_parser, main
from adaptive_scalper.gateway.spec_store import load_symbol_spec, save_symbol_spec
from adaptive_scalper.history.store import insert_bars
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.runtime.state import put_state
from sim_helpers import random_walk_bars, spec

START = 1_700_000_000
N_BARS = 3000


@pytest.fixture()
def env(tmp_path):
    db_path = tmp_path / "cli.sqlite3"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        f'mode = "PAPER"\n[market]\nsymbols = ["XAUUSD", "GBPJPY", "BTCUSD"]\n'
        f'[strategies]\nretired = ["failed_breakout_fade", "support_resistance_reaction"]\n'
        f'[database]\npath = "{db_path.as_posix()}"\n'
        f'[runtime]\nlog_dir = "{(tmp_path / "logs").as_posix()}"\n',
        encoding="utf-8",
    )
    conn = connect(db_path)
    migrate(conn)
    insert_bars(conn, "XAUUSD", "M5", random_walk_bars(N_BARS, seed=11, start=START))
    save_symbol_spec(conn, "XAUUSD", spec(), now_utc=START)
    conn.close()
    return str(cfg_path), db_path


def run(env, *args) -> int:
    cfg, _ = env
    code = main(["--config", cfg, *args])
    return code


def out_json(capsys):
    return json.loads(capsys.readouterr().out)


def _range(i0: int, i1: int) -> list[str]:
    return ["--start", str(START + i0 * 300), "--end", str(START + i1 * 300)]


# ---------------------------------------------------------------------------
# every listed command exists and is backed by code
# ---------------------------------------------------------------------------

EXPECTED = {
    "doctor", "status", "health", "symbols", "strategies", "why-no-trade", "journal", "costs", "kill-switch",
    "history", "broker-history", "paper", "demo", "scan", "analyse", "reconcile", "news", "backtest",
    "walk-forward", "oos", "path-stress", "purged-validation", "models", "learning", "model-walk-forward", "rag",
    "okf", "dashboard", "order-check-probe",
}


def test_every_directive_command_is_registered():
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    assert set(sub.choices) == EXPECTED
    for name, parser in sub.choices.items():
        nested = [a for a in parser._actions if getattr(a, "choices", None) and a.dest.endswith("command")]
        leaves = [nested[0].choices[k] for k in nested[0].choices] if nested else [parser]
        for leaf in leaves:
            assert callable(leaf.get_default("func")), name


def test_the_module_entry_point_works(env):
    import subprocess

    cfg, _ = env
    result = subprocess.run([sys.executable, "-m", "adaptive_scalper.cli", "--config", cfg, "strategies"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "momentum_continuation" in result.stdout


# ---------------------------------------------------------------------------
# operator kill switch
# ---------------------------------------------------------------------------

def test_kill_switch_bootstrap_is_an_explicit_operator_command(env, capsys):
    assert run(env, "kill-switch", "status") == 0
    assert out_json(capsys)["status"] == "UNINITIALIZED"
    assert run(env, "kill-switch", "bootstrap", "--operator-id", "hassan", "--reason", "first start") == 0
    assert "DISENGAGED" in capsys.readouterr().out
    assert run(env, "kill-switch", "bootstrap", "--operator-id", "hassan", "--reason", "again") == 0
    assert "already DISENGAGED" in capsys.readouterr().out
    run(env, "kill-switch", "engage", "--reason", "stop trading")
    assert "NOT closed" in capsys.readouterr().out
    assert run(env, "kill-switch", "bootstrap", "--operator-id", "hassan", "--reason", "sneak") == 1
    assert "already ENGAGED" in capsys.readouterr().out  # bootstrap can never clear an engaged switch
    run(env, "kill-switch", "status")
    assert out_json(capsys)["status"] == "ENGAGED"


def test_bootstrap_requires_operator_and_reason(env):
    with pytest.raises(SystemExit):
        run(env, "kill-switch", "bootstrap", "--reason", "x")
    with pytest.raises(SystemExit):
        run(env, "kill-switch", "bootstrap", "--operator-id", "x")


# ---------------------------------------------------------------------------
# observability
# ---------------------------------------------------------------------------

def test_status_strategies_and_why_no_trade(env, capsys):
    assert run(env, "status") == 0
    body = out_json(capsys)
    assert body["real_money_execution"] == "DISABLED" and body["engine"] is None
    assert run(env, "strategies") == 0
    body = out_json(capsys)
    assert len(body["active"]) == 6 and body["retired_permanently"] == ["failed_breakout_fade",
                                                                          "support_resistance_reaction"]
    assert run(env, "why-no-trade") == 0
    body = out_json(capsys)
    assert body["kill_switch"]["blocks_new_entries"] is True and "no runtime cycle" in body["note"]


def test_journal_recent_and_costs_observed(env, capsys):
    from adaptive_scalper.journal.events import append_event

    _, db = env
    conn = connect(db)
    append_event(conn, "c1", "ENTRY_BLOCKED", START, "XAUUSD", {"decision": "BLOCK_NEWS", "reason": "CPI"})
    conn.close()
    assert run(env, "journal", "recent", "--symbol", "XAUUSD") == 0
    (event,) = out_json(capsys)
    assert event["event_type"] == "ENTRY_BLOCKED" and event["payload"]["reason"] == "CPI"
    assert run(env, "costs", "observed", "--symbol", "XAUUSD") == 0
    body = out_json(capsys)
    assert body["observations"]["XAUUSD"]["sufficient"] is False and "nothing is changed" in body["note"]


def test_news_status_and_upcoming_read_the_cache(env, capsys):
    assert run(env, "news", "status") == 0
    assert out_json(capsys)["cached_events"] == 0
    assert run(env, "news", "upcoming") == 0
    assert out_json(capsys) == []


# ---------------------------------------------------------------------------
# research over stored history
# ---------------------------------------------------------------------------

def test_backtest_walk_forward_path_stress_and_purged_validation(env, capsys):
    assert run(env, "backtest", "--symbol", "XAUUSD", *_range(0, 1999)) == 0
    bt = out_json(capsys)
    assert bt["trades"] > 10 and bt["cost_provenance"] == "UNVERIFIED_ASSUMPTION"
    assert run(env, "walk-forward", "--symbol", "XAUUSD", *_range(0, 1999), "--folds", "3") == 0
    wf = out_json(capsys)
    assert len(wf["folds"]) == 3 and "nothing is re-fit" in wf["note"]
    assert run(env, "path-stress", "--symbol", "XAUUSD", "--simulations", "200") == 0
    ps = out_json(capsys)
    assert ps["run_id"] == bt["run_id"] and ps["n_simulations"] == 200
    assert run(env, "purged-validation", "--symbol", "XAUUSD", "--folds", "4") == 0
    pv = out_json(capsys)
    assert len(pv["folds"]) == 4 and 0.0 <= pv["psr_vs_zero"] <= 1.0


def test_oos_is_one_shot_and_refuses_design_data(env, capsys):
    assert run(env, "backtest", "--symbol", "XAUUSD", *_range(0, 1999)) == 0
    capsys.readouterr()
    assert run(env, "oos", "--symbol", "XAUUSD", *_range(1500, 2500)) == 1   # overlaps VALIDATION data
    assert "refused" in capsys.readouterr().err
    assert run(env, "oos", "--symbol", "XAUUSD", *_range(2100, 2999)) == 0
    assert "now spent" in out_json(capsys)["note"]
    assert run(env, "oos", "--symbol", "XAUUSD", *_range(2100, 2999)) == 1   # spent
    capsys.readouterr()
    assert run(env, "oos", "--symbol", "XAUUSD", *_range(2100, 2999), "--analysis-reuse") == 0
    assert "not untouched" in out_json(capsys)["note"]


def test_research_without_a_stored_spec_fails_clearly(env, capsys):
    assert run(env, "backtest", "--symbol", "GBPJPY", *_range(0, 100)) == 1
    assert "no stored symbol spec" in capsys.readouterr().err


def test_scan_and_analyse_from_stored_history(env, capsys):
    assert run(env, "analyse", "--symbol", "XAUUSD", "--source", "db", "--with-knowledge") == 0
    body = out_json(capsys)
    assert body["status"] == "OK" and len(body["signals"]) == 6 and "no order" in body["note"]
    assert run(env, "scan", "--source", "db") == 0
    body = out_json(capsys)
    assert body["XAUUSD"]["status"] == "OK" and body["GBPJPY"]["status"] == "UNAVAILABLE"
    _, db = env
    conn = connect(db)  # analysis never journals or persists regime state
    assert conn.execute("SELECT COUNT(*) FROM journal_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# learning, RAG, OKF
# ---------------------------------------------------------------------------

def test_learning_train_models_and_model_walk_forward(env, capsys):
    run(env, "backtest", "--symbol", "XAUUSD", *_range(0, 2999))
    capsys.readouterr()
    code = run(env, "learning", "train", "--symbol", "XAUUSD", "--min-samples", "20", "--folds", "3")
    body = out_json(capsys)
    assert body["lifecycle_state"] in ("BASELINE", "INSUFFICIENT_DATA")
    assert body["promotion_gate"]["action_taken"].startswith("NONE")
    assert code == (0 if body["trained"] else 1)
    assert run(env, "models") == 0
    assert out_json(capsys)["models"][0]["model_key"] == "entry_model:XAUUSD"
    assert run(env, "learning", "status") == 0
    assert "CURRENT" not in out_json(capsys)["models_by_state"]
    assert run(env, "model-walk-forward", "--symbol", "XAUUSD", "--folds", "3", "--min-train-rows", "10") == 0
    assert out_json(capsys)["promotion_ready"] is False
    assert run(env, "learning", "scores") == 0


def test_rag_commands(env, capsys):
    from adaptive_scalper.journal.events import append_event

    _, db = env
    conn = connect(db)
    append_event(conn, "c1", "ENTRY_BLOCKED", START, "XAUUSD",
                 {"decision": "BLOCK_NEWS", "reason": "nonfarm payrolls"}, strategy_key="range_breakout")
    conn.close()
    assert run(env, "rag", "ingest") == 0
    assert out_json(capsys)["inserted"] == {"REJECTION": 1}
    assert run(env, "rag", "stats") == 0
    assert out_json(capsys) == [{"memory_type": "REJECTION", "origin": "DECISION", "n": 1}]
    assert run(env, "rag", "similar", "XAUUSD nonfarm payrolls") == 0
    assert out_json(capsys)["matches"][0]["type"] == "REJECTION"
    assert run(env, "rag", "verify-index") == 0
    assert out_json(capsys)["ok"] is True
    assert run(env, "rag", "status") == 0 and out_json(capsys)["total_memories"] == 1
    assert run(env, "rag", "rebuild-index") == 0


def test_rag_verify_index_flags_dangling_sources(env, capsys):
    from adaptive_scalper.rag.store import store_memory

    _, db = env
    conn = connect(db)
    store_memory(conn, "SYSTEM_EVENT", "orphan", {}, source_key="journal:999", origin="SYSTEM")
    conn.close()
    assert run(env, "rag", "verify-index") == 1
    assert out_json(capsys)["dangling_source_keys"] == ["journal:999"]


def test_okf_commands(env, capsys):
    assert run(env, "okf", "status") == 0
    body = out_json(capsys)
    assert body["okf_version"] == "0.2" and body["errors"] == 0
    assert run(env, "okf", "validate") == 0
    assert out_json(capsys)["valid"] is True
    assert run(env, "okf", "search", "safety/kill-switch") == 0
    assert out_json(capsys)[0]["id"] == "safety/kill-switch"
    assert run(env, "okf", "search", "unknown order reconciliation") == 0
    assert out_json(capsys)
    assert run(env, "okf", "benchmark", "--rag-documents", "200") == 0
    assert "okf_direct" in capsys.readouterr().out


def test_okf_validate_fails_on_a_bad_bundle(env, tmp_path, capsys):
    bad = tmp_path / "bad_bundle"
    bad.mkdir()
    (bad / "x.md").write_text("---\ntitle: no type\n---\n")
    assert run(env, "okf", "--bundle", str(bad), "validate") == 1
    assert out_json(capsys)["valid"] is False


# ---------------------------------------------------------------------------
# broker-facing commands without MT5 (cloud) and runtime guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [["symbols"], ["reconcile"], ["history", "bootstrap"], ["broker-history", "status"],
                                  ["scan"], ["paper"], ["demo"], ["order-check-probe", "--symbol", "XAUUSD"]])
def test_broker_commands_fail_cleanly_without_metatrader5(env, capsys, args, monkeypatch):
    import adaptive_scalper.gateway.mt5_gateway as mt5_module

    if mt5_module.__dict__.get("mt5") is not None:
        pytest.skip("MetaTrader5 is installed here; covered by LOCAL_MT5_HANDOFF.md")
    assert run(env, *args) == 1
    err = capsys.readouterr().err
    assert "FAIL" in err and ("MetaTrader5" in err or "MT5" in err)


def test_doctor_reports_real_money_disabled(env, capsys):
    run(env, "doctor")
    out = capsys.readouterr().out
    assert "real-money execution: DISABLED" in out and "schema=" in out


def test_a_second_runtime_is_refused_while_the_first_heartbeat_is_fresh(env, capsys):
    import time

    _, db = env
    conn = connect(db)
    put_state(conn, "engine", {"state": "RUNNING", "mode": "PAPER"}, now_utc=int(time.time()))
    conn.close()
    assert run(env, "demo") == 1
    assert "already running" in capsys.readouterr().err


def test_the_dashboard_refuses_non_local_binds(env, capsys):
    assert run(env, "dashboard", "--host", "0.0.0.0") == 1
    assert "local-only" in capsys.readouterr().err


def test_symbol_spec_round_trip(env):
    _, db = env
    conn = connect(db)
    loaded, captured = load_symbol_spec(conn, "XAUUSD")
    assert loaded == spec() and captured == START
    assert load_symbol_spec(conn, "BTCUSD") is None
