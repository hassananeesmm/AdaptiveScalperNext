"""Strategy Lab additions for the research release: the INDEPENDENT RESEARCH
view (report loader + endpoint), rejected proposals in the DEMO funnel, the
readiness panel's last actual block / latest signals, and the research CLI's
refusal to touch the production database. Temp files only."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from adaptive_scalper.cli import research as research_cli
from adaptive_scalper.cli.common import CliError
from adaptive_scalper.dashboard import panels
from adaptive_scalper.dashboard import strategy_lab as sl
from adaptive_scalper.dashboard.app import create_app
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.persistence.database import connect, migrate

T0 = 1_790_000_000


def _report(symbol: str, created: int, net: float = -10.0) -> dict:
    summary = {"trades": 3, "wins": 1, "losses": 2, "breakevens": 0, "win_rate": 1 / 3, "gross_pnl": 2.0,
               "total_cost": 12.0, "net_pnl": net, "profit_factor": 0.5, "expectancy_per_trade": net / 3,
               "avg_gross_r": 0.01, "avg_cost_r": 0.2, "avg_net_r": -0.19, "avg_holding_seconds": 300.0,
               "loss_classes": {}, "gross_positive_share": 0.5, "cost_to_abs_gross": 6.0}
    return {
        "kind": "INDEPENDENT_STRATEGY_RESEARCH", "symbol": symbol, "created_at_utc": created, "range": [1, 2],
        "bars": 100, "folds": 2, "inputs_identical": True, "bars_checksum": "abc", "cost_provenance": "X",
        "initial_equity_per_session": 10000.0, "news_windows_applied": 0,
        "sessions": {"microstructure_acceleration": {"summary": summary, "statistics": {"psr_vs_zero": 0.1},
                                                     "dsr": {"dsr": 0.01}, "breakdown": {"exit_reason": {}}},
                     "range_breakout": {"error": "RuntimeError: x"}},
        "pbo": {"computable": False, "pbo": None, "reason": "r"},
        "selector_study": {"candidates": 5, "selected": 2, "per_strategy": {"microstructure_acceleration": {
            "signals": 5, "selected": 2, "selection_share": 1.0}}},
    }


def test_research_reports_returns_the_latest_report_per_symbol(tmp_path):
    (tmp_path / "independent_BTCUSD_a.json").write_text(json.dumps(_report("BTCUSD", 10, net=-1)), encoding="utf-8")
    (tmp_path / "independent_BTCUSD_b.json").write_text(json.dumps(_report("BTCUSD", 20, net=-2)), encoding="utf-8")
    (tmp_path / "independent_XAUUSD_a.json").write_text(json.dumps(_report("XAUUSD", 5)), encoding="utf-8")
    (tmp_path / "independent_broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "independent_other.json").write_text(json.dumps({"kind": "SOMETHING_ELSE"}), encoding="utf-8")
    out = sl.research_reports(tmp_path)
    assert out["status"] == "OK" and sorted(out["symbols"]) == ["BTCUSD", "XAUUSD"]
    btc = out["symbols"]["BTCUSD"]
    assert btc["file"] == "independent_BTCUSD_b.json"
    assert btc["sessions"]["microstructure_acceleration"]["net_pnl"] == -2
    assert btc["sessions"]["range_breakout"] == {"error": "RuntimeError: x"}  # failures stay visible
    assert len(out["errors"]) == 1 and "independent_broken.json" in out["errors"][0]
    assert "never" in out["note"] and "BACKTEST" in out["note"]


def test_research_reports_without_a_folder_or_reports_is_no_data(tmp_path):
    assert sl.research_reports(tmp_path / "missing")["status"] == "NO_DATA"
    assert sl.research_reports(tmp_path)["status"] == "NO_DATA"


def test_research_endpoint_reads_the_folder_next_to_the_database(tmp_path):
    db_path = tmp_path / "prod.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "independent_XAUUSD_r.json").write_text(json.dumps(_report("XAUUSD", 1)),
                                                                     encoding="utf-8")
    client = TestClient(create_app(db_path))
    body = client.get("/api/strategy-lab/research").json()
    assert body["status"] == "OK" and list(body["symbols"]) == ["XAUUSD"]
    assert client.post("/api/strategy-lab/research").status_code == 405  # read-only


def test_the_demo_funnel_counts_rejected_proposals(tmp_path):
    conn = connect(tmp_path / "f.sqlite3")
    migrate(conn)
    for i, (key, event) in enumerate((("statistical_reversion", "SIGNAL_CREATED"),
                                      ("statistical_reversion", "PROPOSAL_REJECTED"),
                                      ("microstructure_acceleration", "SIGNAL_CREATED"),
                                      ("microstructure_acceleration", "PROPOSAL_CREATED"),
                                      ("range_breakout", "SIGNAL_CREATED"),
                                      ("range_breakout", "SIGNAL_REJECTED"))):
        append_event(conn, f"entry:BTCUSD:{T0}:{key}", event, T0 + i, "BTCUSD", {}, strategy_key=key)
    f = sl.demo_funnel(conn, None, None)["by_strategy"]
    assert f["statistical_reversion"]["proposals_rejected"] == 1 and f["statistical_reversion"]["signals_rejected"] == 0
    assert f["range_breakout"]["signals_rejected"] == 1 and f["range_breakout"]["proposals_rejected"] == 0
    assert f["microstructure_acceleration"]["selected_proposals"] == 1
    conn.close()


def _decision(conn, symbol, stage, decision, reason, strategy="s", chain=None, at=T0):
    conn.execute("INSERT INTO entry_decisions (decided_at_utc, mode, canonical_symbol, stage, decision, reason, "
                 "strategy_key, chain_key) VALUES (?, 'DEMO', ?, ?, ?, ?, ?, ?)",
                 (at, symbol, stage, decision, reason, strategy, chain))
    conn.commit()


def test_last_actual_block_skips_no_signal_bars_and_fills(tmp_path):
    conn = connect(tmp_path / "p.sqlite3")
    migrate(conn)
    assert panels._last_block(conn, "XAUUSD") is None
    _decision(conn, "XAUUSD", "SIZING", "BLOCK_RISK_SIZING", "volume below minimum", at=T0)
    _decision(conn, "XAUUSD", "SIGNAL", "FLAT", "no strategy signal", at=T0 + 300)
    _decision(conn, "XAUUSD", "EXECUTION", "FILLED", "filled", at=T0 + 600)
    block = panels._last_block(conn, "XAUUSD")
    assert block["decided_at_utc"] == T0 and block["label"] == panels.BLOCKED_BY_RISK
    _decision(conn, "XAUUSD", "SELECTOR", "BLOCK_COST", "cost unknown", at=T0 + 900)
    assert panels._last_block(conn, "XAUUSD")["label"] == panels.BLOCKED_BY_COST
    conn.close()


def test_margin_rejection_has_its_own_label(tmp_path):
    conn = connect(tmp_path / "m.sqlite3")
    migrate(conn)
    row = {"stage": "EXECUTION", "decision": "BLOCKED_MARGIN", "reason": "order_check: not enough money",
           "chain_key": None}
    assert panels._decision_label(conn, row) == panels.BLOCKED_BY_MARGIN
    conn.close()


def test_latest_signals_lists_the_strategies_of_the_last_decided_bar(tmp_path):
    conn = connect(tmp_path / "s.sqlite3")
    migrate(conn)
    append_event(conn, f"entry:BTCUSD:{T0}:a", "SIGNAL_CREATED", T0, "BTCUSD",
                 {"direction": "BUY", "raw_confidence": 0.4}, strategy_key="momentum_continuation")
    append_event(conn, f"entry:BTCUSD:{T0 + 300}:a", "SIGNAL_CREATED", T0 + 300, "BTCUSD",
                 {"direction": "SELL", "raw_confidence": 1.0}, strategy_key="microstructure_acceleration")
    append_event(conn, f"entry:BTCUSD:{T0 + 300}:b", "SIGNAL_CREATED", T0 + 300, "BTCUSD",
                 {"direction": "SELL", "raw_confidence": 0.3}, strategy_key="statistical_reversion")
    out = panels._latest_signals(conn, "BTCUSD")
    assert out["at_utc"] == T0 + 300
    assert [s["strategy_key"] for s in out["signals"]] == ["microstructure_acceleration", "statistical_reversion"]
    assert panels._latest_signals(conn, "XAUUSD") == {"at_utc": None, "signals": []}
    conn.close()


def test_the_research_cli_refuses_the_production_database(tmp_path):
    prod = tmp_path / "prod.sqlite3"
    cfg_path = tmp_path / "cfg.toml"
    cfg_path.write_text(f'mode = "PAPER"\n[database]\npath = "{prod.as_posix()}"\n', encoding="utf-8")
    with pytest.raises(CliError, match="production database"):
        research_cli.open_research_db(str(cfg_path), str(prod))
    with pytest.raises(CliError, match="does not exist"):
        research_cli.open_research_db(str(cfg_path), str(tmp_path / "nope.sqlite3"))


def _v2_report(symbol: str, created: int) -> dict:
    session = {"r": {"n": 4, "gross_r": 0.05, "cost_r": 0.2, "net_r": -0.15, "net_win_rate": 0.25},
               "summary": {"profit_factor": 0.5, "net_pnl": -6.0, "avg_holding_seconds": 600.0},
               "statistics": {"psr_vs_zero": 0.1}, "positive_folds": 1, "folds": 2, "halted_folds": 0,
               "mae_r": {"median": 0.4}, "mfe_r": {"median": 0.3}}
    return {"kind": "RESEARCH_V2_STUDY", "symbol": symbol, "created_at_utc": created, "bars": 100, "folds": 2,
            "cost_provenance": "BROKER_DEMO_CONFIRMED", "protected_oos": "not read",
            "sessions": {"V1:momentum_continuation": session, "H1-DIR:momentum_continuation": {**session, "dsr": {"dsr": 0.0}},
                         "H3-M1:__selector__": {"r": {"n": 0}, "summary": {}, "statistics": {}},
                         "H2-K3:range_breakout": {"error": "RuntimeError: x"}},
            "family_pbo": {}, "raw_confidence_reliability": {"momentum_continuation": "UNKNOWN (too few trades)"}}


def test_research_v2_view_labels_baseline_and_candidates_separately(tmp_path):
    folder = tmp_path / "research"
    folder.mkdir()
    (folder / "v2_XAUUSD_old.json").write_text(json.dumps(_v2_report("XAUUSD", 1)), encoding="utf-8")
    (folder / "v2_XAUUSD_new.json").write_text(json.dumps(_v2_report("XAUUSD", 2)), encoding="utf-8")
    (folder / "v2_costs_20260929.json").write_text(json.dumps({"evidence": "BROKER_DEMO_CONFIRMED (entry side)",
                                                               "symbols": {}}), encoding="utf-8")
    (folder / "independent_XAUUSD_r.json").write_text(json.dumps(_report("XAUUSD", 3)), encoding="utf-8")
    body = sl.research_v2_reports(folder)
    x = body["symbols"]["XAUUSD"]
    assert x["file"] == "v2_XAUUSD_new.json"
    assert {r["evidence_label"] for r in x["variants"]["V1"]} == {"V1 BASELINE"}
    assert {r["evidence_label"] for v in ("H1-DIR", "H2-K3", "H3-M1") for r in x["variants"][v]} == {"V2 CANDIDATE"}
    assert x["variants"]["H2-K3"][0]["error"].startswith("RuntimeError")
    assert x["variants"]["H3-M1"][0]["trades"] == 0
    assert body["costs"]["_file"] == "v2_costs_20260929.json"
    assert sl.research_v2_reports(tmp_path / "missing")["status"] == "NO_DATA"


def test_research_v2_endpoint_is_read_only(tmp_path):
    db_path = tmp_path / "prod.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "v2_BTCUSD_t.json").write_text(json.dumps(_v2_report("BTCUSD", 1)), encoding="utf-8")
    client = TestClient(create_app(db_path))
    body = client.get("/api/strategy-lab/research-v2").json()
    assert body["status"] == "OK" and list(body["symbols"]) == ["BTCUSD"]
    assert client.post("/api/strategy-lab/research-v2").status_code == 405
