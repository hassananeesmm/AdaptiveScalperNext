"""PAPER runtime cycle (directive section 132): real MT5 market data,
simulated fills, NEVER an order_check/order_send.

Once per entry cadence, for every resolved symbol, the newest CLOSED bars
are fed to `paper.engine.run_paper_cycle()` -- the same causal decision
cores the backtest is validated with. Cross-symbol policy is applied
exactly as DEMO applies it: the other symbols' open simulated positions
are passed as `external_open_positions` (max positions, total risk,
portfolio heat) with a correlation matrix computed from the same closed
bars (N/A blocks). News windows come from the live calendar monitor; a
calendar outage, a provider conflict or a kill switch that is not
DISENGAGED blocks NEW simulated entries while open simulated positions
keep being managed.

Honest, named limits: each symbol session carries its own simulated
equity (no pooled portfolio equity), and costs that are not configured
simulate as 0.0 with every result labeled UNVERIFIED_ASSUMPTION.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.paper.engine import PaperSessionConfigMismatchError, run_paper_cycle
from adaptive_scalper.paper.state import get_session
from adaptive_scalper.portfolio.correlation import compute_correlation_matrix
from adaptive_scalper.portfolio.exposure import PositionExposure
from adaptive_scalper.risk.governor import risk_limits_from_config
from adaptive_scalper.runtime.market_data import closed_bars, log_returns
from adaptive_scalper.runtime.news_monitor import NewsMonitor
from adaptive_scalper.runtime.state import put_state, record_entry_decision, record_event
from adaptive_scalper.costs.edge_evidence import (
    NO_VALIDATED_EDGE_EVIDENCE,
    EdgeEvidenceProvider,
    require_executable_provider,
)
from adaptive_scalper.simulation.fill_model import COST_UNVERIFIED_ASSUMPTION, FillAssumptions

logger = logging.getLogger(__name__)

MODE = "PAPER"
BLOCK_KILL_SWITCH = "BLOCK_KILL_SWITCH"
BLOCK_COST = "BLOCK_COST"


def paper_config(
    config: AppConfig, canonical_symbol: str, news_windows: tuple[tuple[int, int], ...], *,
    edge_provider: EdgeEvidenceProvider | None = None,
) -> BacktestConfig:
    costs = config.cost_for(canonical_symbol)
    provenance = costs.provenance if costs.fully_known else COST_UNVERIFIED_ASSUMPTION
    return BacktestConfig(
        initial_equity=config.runtime.paper_initial_equity,
        risk_per_trade_pct=config.risk.risk_per_trade_pct,
        risk_limits=risk_limits_from_config(config.risk),
        fill_assumptions=FillAssumptions(
            slippage_price=costs.slippage_price or 0.0,
            commission_monetary_per_lot=costs.commission_per_lot_round_trip or 0.0,
            swap_monetary_per_lot_per_day=costs.swap_per_lot_per_day, provenance=provenance,
        ),
        news_windows=news_windows,
        server_time_rule=config.mt5.server_time_rule,
        edge_provider=edge_provider,
    )


def session_key(config: AppConfig, canonical_symbol: str) -> str:
    return f"PAPER:{canonical_symbol}:{config.runtime.entry_resolution}:{config.runtime.paper_session_tag}"


@dataclass
class PaperRuntime:
    conn: sqlite3.Connection
    gateway: Gateway
    config: AppConfig
    symbols: dict[str, str]
    news: NewsMonitor
    clock: Callable[[], float] = time.time
    halted_symbols: set[str] = field(default_factory=set)
    # Expected-edge evidence (issue #6): the default has none, so PAPER
    # proposes nothing (FLAT); the legacy V1 replay provider is refused.
    edge_evidence: EdgeEvidenceProvider = NO_VALIDATED_EDGE_EVIDENCE

    def __post_init__(self) -> None:
        require_executable_provider(self.edge_evidence, "PAPER runtime")

    def global_entry_block(self, now: int) -> tuple[str, str] | None:
        kill = get_kill_switch_state(self.conn)
        if kill.blocks_new_entries:
            return BLOCK_KILL_SWITCH, f"kill switch {kill.status.value}"
        return self.news.global_block(now)

    def _external(self, canonical: str) -> tuple[PositionExposure, ...]:
        out = []
        for other in self.symbols:
            if other == canonical:
                continue
            session = get_session(self.conn, session_key(self.config, other))
            if session is not None and session.open_position is not None:
                pos = session.open_position
                out.append(PositionExposure(other, pos.direction, pos.initial_monetary_risk))
        return tuple(out)

    def cycle(self) -> dict:
        now = int(self.clock())
        block = self.global_entry_block(now)
        snapshot = {"at": now, "mode": MODE, "global_block": None, "symbols": {}}
        if block is not None:
            snapshot["global_block"] = {"decision": block[0], "reason": block[1]}
            record_event(self.conn, "BLOCKED", "entry", "NEW_ENTRIES_BLOCKED", f"{block[0]}: {block[1]}",
                         dedup_key="paper:global_block", now_utc=now)
        else:
            from adaptive_scalper.runtime.state import clear_event
            clear_event(self.conn, "paper:global_block", now_utc=now)

        bars_by_symbol, specs = {}, {}
        for canonical, broker in self.symbols.items():
            spec = self.gateway.symbol_info(broker)
            if spec is None:
                continue
            tick = self.gateway.symbol_info_tick(broker)
            bars_by_symbol[canonical] = closed_bars(
                self.gateway, broker, self.config.runtime.entry_resolution, now_utc=now,
                count=self.config.runtime.bar_history_count, tick=tick,
            )
            specs[canonical] = spec
        correlation = compute_correlation_matrix(
            {c: log_returns(b) for c, b in bars_by_symbol.items()},
            min_sample_size=self.config.runtime.correlation_min_samples,
        )

        for canonical, bars in bars_by_symbol.items():
            if canonical in self.halted_symbols:
                snapshot["symbols"][canonical] = {"decision": "SESSION_HALTED", "reason": "configuration mismatch"}
                continue
            try:
                snapshot["symbols"][canonical] = self._symbol_cycle(canonical, bars, specs[canonical], correlation,
                                                                     block, now)
            except PaperSessionConfigMismatchError as exc:
                self.halted_symbols.add(canonical)
                record_event(self.conn, "CRITICAL", "paper", "PAPER_SESSION_CONFIG_MISMATCH",
                             f"{exc} -- set [runtime] paper_session_tag to start a new session",
                             canonical_symbol=canonical, dedup_key=f"paper:mismatch:{canonical}", now_utc=now)
                snapshot["symbols"][canonical] = {"decision": "SESSION_HALTED", "reason": str(exc)}
            except Exception as exc:
                logger.exception("PAPER cycle failed for %s", canonical)
                record_event(self.conn, "ERROR", "paper", "PAPER_CYCLE_FAILED", f"{type(exc).__name__}: {exc}",
                             canonical_symbol=canonical, dedup_key=f"paper:failed:{canonical}", now_utc=now)
                snapshot["symbols"][canonical] = {"decision": "ERROR", "reason": str(exc)}
        put_state(self.conn, "why_no_trade", snapshot, now_utc=now)
        return snapshot

    def _symbol_cycle(self, canonical, bars, spec, correlation, block, now) -> dict:
        config = paper_config(self.config, canonical, self.news.windows_for(canonical),
                              edge_provider=self.edge_evidence)
        if len(bars) < config.feature_lookback + 2:
            return {"decision": "BLOCK_DATA_QUALITY", "reason": f"only {len(bars)} closed bars"}
        # Unknown commission/slippage never becomes a free fill: new PAPER
        # entries block exactly as DEMO's do (BLOCK_COST); an already-open
        # simulated position is still managed to its exit.
        cost_block = None if self.config.cost_for(canonical).fully_known else BLOCK_COST
        result = run_paper_cycle(
            self.conn, bars, canonical, self.config.runtime.entry_resolution, spec, config=config,
            session_key=session_key(self.config, canonical), now_utc=now,
            external_open_positions=self._external(canonical), correlation_matrix=correlation,
            entry_block_reason=block[0] if block is not None else cost_block,
        )
        if not result.ran:
            return {"decision": "WAITING_FOR_BAR_CLOSE", "reason": "no new closed bar"}
        for rejection in result.entry_rejections:
            record_entry_decision(
                self.conn, mode=MODE, stage="PAPER_FILL", decision=rejection.reason_code, reason=rejection.detail,
                canonical_symbol=canonical, bar_time_utc=rejection.signal_time_utc,
                strategy_key=rejection.strategy_key, direction=rejection.direction, now_utc=now,
            )
        for trade in result.new_trades:
            record_entry_decision(
                self.conn, mode=MODE, stage="PAPER_TRADE", decision="CLOSED", reason=trade.exit_reason or "",
                canonical_symbol=canonical, bar_time_utc=trade.signal_time_utc, strategy_key=trade.strategy_key,
                direction=trade.direction, detail={"realized_r": trade.realized_r, "realized_pnl": trade.realized_pnl},
                now_utc=now,
            )
        state = {
            "decision": "OPEN_POSITION" if result.open_position is not None else "FLAT",
            "reason": f"{len(result.new_trades)} simulated trade(s) closed this cycle",
            "equity": result.equity, "last_bar_utc": result.last_processed_bar_time_utc,
            "entry_rejections": [r.reason_code for r in result.entry_rejections],
        }
        if block is not None:
            state["global_block"] = block[0]
        return state
