"""Durable backtest-run persistence (migration `0014_backtest`).

`run_backtest()` itself stays pure (no DB connection required, so it can
be called from a walk-forward fold, a Monte Carlo resample driver, or a
throwaway script without ever touching SQLite). Recording a run's dataset,
dataset-usage, and trades is a SEPARATE, explicit step a caller opts into
by passing a real connection here -- consistent with the rest of this
codebase's "never silently persist state a caller didn't ask for" style.
"""

from __future__ import annotations

import json
import sqlite3
import time

from adaptive_scalper.backtest.dataset import build_dataset_snapshot, record_dataset, record_dataset_usage
from adaptive_scalper.backtest.types import BacktestResult, SimulatedTrade
from adaptive_scalper.gateway.types import Bar

# Provenance/cost columns shared by `backtest_trades` and `paper_trades`
# (migration 0020), in insertion order.
TRADE_PROVENANCE_COLUMNS: tuple[str, ...] = (
    "strategy_version", "signal_time_utc", "entry_fill_reference", "exit_fill_reference",
    "exit_decision_time_utc", "entry_spread_cost", "entry_slippage_cost", "exit_spread_cost",
    "exit_slippage_cost", "commission_cost", "swap_cost", "fee_cost", "gross_pnl", "peak_r",
    "fill_model_version", "cost_provenance", "config_fingerprint", "evidence_json",
)


def trade_provenance_values(trade: SimulatedTrade) -> tuple:
    return (
        trade.strategy_version, trade.signal_time_utc, trade.entry_fill_reference, trade.exit_fill_reference,
        trade.exit_decision_time_utc, trade.entry_spread_cost, trade.entry_slippage_cost, trade.exit_spread_cost,
        trade.exit_slippage_cost, trade.commission_cost, trade.swap_cost, trade.fee_cost, trade.gross_pnl,
        trade.peak_r, trade.fill_model_version, trade.cost_provenance, trade.config_fingerprint,
        json.dumps(trade.entry_evidence, sort_keys=True) if trade.entry_evidence is not None else None,
    )


def record_backtest_run(
    conn: sqlite3.Connection,
    result: BacktestResult,
    bars: list[Bar],
    *,
    run_id: str,
    run_type: str,
    used_for: str,
    strategies: tuple[str, ...],
    feature_schema_version: int,
    now_utc: int | None = None,
) -> None:
    """Idempotent on `run_id`: a repeat call for the SAME run_id is a
    no-op, never a duplicate row or a silently-overwritten one -- a
    caller re-running the same fold after a crash gets back the original
    recorded result, not a second, possibly-different one.
    """
    now = now_utc if now_utc is not None else int(time.time())

    existing = conn.execute("SELECT id FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
    if existing is not None:
        return

    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=result.canonical_symbol, resolution=result.resolution,
        strategies=strategies, feature_schema_version=feature_schema_version,
        origin=result.origin, now_utc=now,
    )
    record_dataset(conn, snapshot)
    record_dataset_usage(conn, snapshot.dataset_id, run_id, used_for, now_utc=now)

    m = result.metrics
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, created_at_utc, canonical_symbol, resolution, dataset_id, run_type,
             range_start_utc, range_end_utc, config_json, trade_count, gross_pnl, net_pnl,
             win_rate, profit_factor, avg_r, max_drawdown, total_cost, metrics_json, origin,
             config_fingerprint, fill_model_version, cost_provenance)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, now, result.canonical_symbol, result.resolution, snapshot.dataset_id, run_type,
            result.range_start_utc, result.range_end_utc,
            json.dumps(_config_to_json(result.config)), m.trade_count, m.gross_pnl, m.net_pnl,
            m.win_rate, m.profit_factor, m.avg_r, m.max_drawdown, m.total_cost,
            json.dumps({
                "trade_count": m.trade_count, "closed_trade_count": m.closed_trade_count,
                "gross_pnl": m.gross_pnl, "net_pnl": m.net_pnl, "total_cost": m.total_cost,
                "win_rate": m.win_rate, "profit_factor": m.profit_factor, "avg_r": m.avg_r,
                "max_drawdown": m.max_drawdown, "final_equity": m.final_equity,
            }),
            result.origin.value, result.config_fingerprint, result.fill_model_version, result.cost_provenance,
        ),
    )

    base_columns = (
        "run_id", "canonical_symbol", "strategy_key", "direction", "entry_time_utc", "entry_price",
        "exit_time_utc", "exit_price", "exit_reason", "volume", "initial_monetary_risk", "realized_r",
        "realized_pnl", "total_cost", "entry_regime", "exit_regime",
    )
    columns = base_columns + TRADE_PROVENANCE_COLUMNS + ("entry_features_json",)
    sql = f"INSERT INTO backtest_trades ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    for trade in result.trades:
        conn.execute(sql, (
            run_id, result.canonical_symbol, trade.strategy_key, trade.direction,
            trade.entry_time_utc, trade.entry_price, trade.exit_time_utc, trade.exit_price,
            trade.exit_reason, trade.volume, trade.initial_monetary_risk, trade.realized_r,
            trade.realized_pnl, trade.total_cost, trade.entry_regime, trade.exit_regime,
        ) + trade_provenance_values(trade) + (
            json.dumps(trade.entry_features, sort_keys=True) if trade.entry_features is not None else None,
        ))
    conn.commit()


def _config_to_json(config) -> dict:
    if config is None:
        return {}
    return {
        "initial_equity": config.initial_equity, "risk_per_trade_pct": config.risk_per_trade_pct,
        "min_net_edge_price": config.min_net_edge_price, "min_raw_confidence": config.min_raw_confidence,
        "feature_lookback": config.feature_lookback, "regime_min_confirmations": config.regime_min_confirmations,
        "uncertainty_margin_pct": config.uncertainty_margin_pct,
        "risk_limits": dict(config.risk_limits.__dict__),
        "fill_assumptions": dict(config.fill_assumptions.__dict__),
        "max_entry_fill_delay_seconds": config.max_entry_fill_delay_seconds,
    }
