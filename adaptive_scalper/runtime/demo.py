"""MT5 DEMO runtime cycles (directive sections 14-16, 36, 115).

`DemoRuntime.position_cycle()` (every ~1s, priority 0):
    account/terminal health -> reconciliation (repairing, journaled only
    when not CLEAN) -> apply UNKNOWN resolutions on broker proof -> review
    every local open position (continuous expectancy, adaptive exit, safe
    stop advance / safe close). Never scans for new trades.

`DemoRuntime.entry_cycle()` (every ~4s, priority 1), decides once per
newly CLOSED bar per symbol -- the same semantics the backtest/PAPER
engines are validated with:
    global short-circuit (kill switch, DEMO/terminal/broker permission,
    dangerous UNKNOWN, reconciliation, news outage, daily-loss/drawdown)
    -> closed bars -> features -> persisted regime tracker -> six active
    strategies -> journal SIGNAL_CREATED -> advisory evidence (RAG / ML
    observer / OKF: journaled, zero authority) -> selector over the LIVE
    cost estimate -> safe volume -> `execution.service.submit_new_entry()`,
    whose `fetch_fresh_evidence` rebuilds the complete final-permission
    input from fresh broker/DB truth each time it is called (twice).

Nothing here clears or bootstraps the kill switch, and nothing here calls
`order_send` directly: entries go through `execution.service`, exits and
stop moves through `position_management.manager` (which uses the safe
close / stop-modification services).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.core.final_permission import FinalPermissionInput
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.costs.model import CostEstimate, estimate_cost_from_evidence, price_equivalent_of_monetary_cost
from adaptive_scalper.execution.reconciliation import (
    CLEAN,
    get_open_positions,
    has_dangerous_unresolved_unknown,
    PROTECTIVE_CLOSE_IN_PROGRESS,
    reconcile_pending_orders,
    reconcile_positions,
    run_reconciliation,
    BrokerPositionSnapshot,
)
from adaptive_scalper.execution.close_requests import has_unresolved_close, resolve_unresolved_closes
from adaptive_scalper.execution.recovery import apply_unknown_resolutions
from adaptive_scalper.costs.observations import record_entry_observation
from adaptive_scalper.execution.service import FILLED, PARTIAL, FreshEvidence, submit_new_entry
from adaptive_scalper.execution.store import get_active_orders
from adaptive_scalper.features.bar_features import compute_bar_features, numeric_feature_vector
from adaptive_scalper.gateway.demo_gate import verify_demo_before_order
from adaptive_scalper.gateway.mt5_gateway import Mt5QueryError
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.symbol_validation import (
    DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
    validate_direction_for_new_exposure,
    validate_execution_quote,
    validate_resolved_symbol,
)
from adaptive_scalper.gateway.types import SymbolSpec, Tick
from adaptive_scalper.journal.events import append_event
from adaptive_scalper.portfolio.correlation import (
    CorrelationResult,
    compute_correlation_matrix,
    describe_correlation_pairs,
)
from adaptive_scalper.portfolio.exposure import PositionExposure, portfolio_risk_limits_from_risk_limits
from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.position_management.manager import PositionReviewInput, review_position_once
from adaptive_scalper.position_management.re_entry import ReentryParams, evaluate_reentry
from adaptive_scalper.regimes.classifier import RegimeClassification, RegimeTracker, classify_regime
from adaptive_scalper.risk.governor import RiskGateInput, calculate_safe_volume, risk_limits_from_config
from adaptive_scalper.runtime.advisory import AdvisoryPanel
from adaptive_scalper.runtime.market_data import closed_bars, log_returns
from adaptive_scalper.runtime.news_monitor import NewsMonitor
from adaptive_scalper.runtime.state import (
    get_position_entry_context,
    get_state,
    put_state,
    record_entry_decision,
    record_event,
    record_position_entry_context,
)
from adaptive_scalper.selector.selector import select_and_journal_proposal
from adaptive_scalper.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

MODE = "DEMO"
FEATURE_LOOKBACK = 20
BLOCK_KILL_SWITCH = "BLOCK_KILL_SWITCH"
BLOCK_UNKNOWN_ORDER = "BLOCK_UNKNOWN_ORDER"
BLOCK_RECONCILIATION = "BLOCK_RECONCILIATION"
BLOCK_RISK = "BLOCK_RISK"
BLOCK_COST = "BLOCK_COST"
# Persisted when run_reconciliation itself fails for a reason other than
# unreadable broker truth.
RECONCILIATION_ERROR = "ERROR"
# Persisted when a broker-truth query failed (Mt5QueryError) anywhere in the
# position cycle: "we cannot currently know", never a stale CLEAN.
RECONCILIATION_BROKER_TRUTH_UNAVAILABLE = "BROKER_TRUTH_UNAVAILABLE"
# `broker_truth` runtime state (master prompt section 14). AVAILABLE only
# after a complete, successful position cycle (reconciliation, UNKNOWN and
# close resolution, position reviews); anything else blocks new exposure.
BROKER_TRUTH_AVAILABLE = "AVAILABLE"
BROKER_TRUTH_UNAVAILABLE = "UNAVAILABLE"
BROKER_TRUTH_CYCLE_FAILED = "CYCLE_FAILED"
# A CLEAN verdict older than this no longer admits new entries.
RECONCILIATION_MIN_MAX_AGE_SECONDS = 30

_OPEN_EXPOSURE_ORDER_STATES = ("SUBMITTED", "ACCEPTED", "PENDING", "RESTING", "PARTIAL", "UNKNOWN", "PENDING_RECONCILIATION")


@dataclass
class SymbolAnalysis:
    bar_time: int
    features: object
    raw_regime: RegimeClassification
    confirmed_regime: str
    feature_vector: dict


def live_cost_estimate(config: AppConfig, canonical_symbol: str, spec: SymbolSpec | None, tick: Tick | None) -> CostEstimate | None:
    """Conservative PRE-ENTRY round-trip cost estimate.

    SymbolCostConfig.slippage_price is measured per fill (the shipped
    config records p90 adverse slippage from individual market-order fills).
    A market entry followed by a market/stop exit therefore has TWO slippage
    opportunities. The previous implementation charged only one, while the
    simulator correctly charged slippage on every fill. That made the live
    selector/final-permission cost estimate systematically optimistic.

    The bid/ask spread is charged once for an immediate round trip: buy at ask
    and sell at bid loses one full spread. Commission is already configured
    as a round-trip amount. Any unknown required component still fails closed.
    """
    if spec is None or tick is None or tick.ask <= 0 or tick.bid <= 0:
        return None
    costs = config.cost_for(canonical_symbol)
    commission = (
        price_equivalent_of_monetary_cost(costs.commission_per_lot_round_trip, spec.trade_tick_size, spec.trade_tick_value)
        if costs.commission_per_lot_round_trip is not None else None
    )
    swap = price_equivalent_of_monetary_cost(costs.swap_per_lot_per_day, spec.trade_tick_size, spec.trade_tick_value)
    round_trip_slippage = None if costs.slippage_price is None else 2.0 * costs.slippage_price
    return estimate_cost_from_evidence(
        spread_price=tick.ask - tick.bid, commission_price_equivalent=commission,
        expected_slippage_price=round_trip_slippage, swap_price_equivalent=swap,
    )


def live_remaining_exit_cost_estimate(
    config: AppConfig, canonical_symbol: str, spec: SymbolSpec | None, tick: Tick | None
) -> CostEstimate | None:
    """Incremental cost still payable for an ALREADY OPEN position.

    Entry spread/slippage and the entry-side commission are sunk and must not
    be charged again when asking whether the position is worth continuing to
    hold. _review marks a long at bid and a short at ask, i.e. at the
    executable closing side of the quote, so the current spread is already in
    the mark. Remaining friction is one expected exit slippage, one half of
    the configured round-trip commission, plus the conservative configured
    swap allowance.

    Keeping this separate from live_cost_estimate prevents the same cost
    object from meaning full trade lifecycle at entry and remaining cost
    after entry.
    """
    if spec is None or tick is None or tick.ask <= 0 or tick.bid <= 0:
        return None
    costs = config.cost_for(canonical_symbol)
    commission = (
        price_equivalent_of_monetary_cost(
            costs.commission_per_lot_round_trip / 2.0, spec.trade_tick_size, spec.trade_tick_value
        )
        if costs.commission_per_lot_round_trip is not None else None
    )
    swap = price_equivalent_of_monetary_cost(costs.swap_per_lot_per_day, spec.trade_tick_size, spec.trade_tick_value)
    return estimate_cost_from_evidence(
        spread_price=0.0, commission_price_equivalent=commission,
        expected_slippage_price=costs.slippage_price, swap_price_equivalent=swap,
    )


@dataclass
class DemoRuntime:
    conn: sqlite3.Connection
    gateway: Gateway
    config: AppConfig
    symbols: dict[str, str]                      # canonical -> broker symbol (resolved + validated)
    registry: object
    news: NewsMonitor
    advisory: AdvisoryPanel
    clock: Callable[[], float] = time.time
    exit_params: AdaptiveExitParams = field(default_factory=AdaptiveExitParams)
    reentry_params: ReentryParams = field(default_factory=ReentryParams)
    latest: dict[str, SymbolAnalysis] = field(default_factory=dict)
    correlation: dict[tuple[str, str], CorrelationResult] = field(default_factory=dict)
    returns: dict[str, dict[int, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.risk_limits = risk_limits_from_config(self.config.risk)
        self.portfolio_limits = portfolio_risk_limits_from_risk_limits(self.risk_limits.max_total_open_risk_pct)
        self.resolution = self.config.runtime.entry_resolution

    def now(self) -> int:
        return int(self.clock())

    # ------------------------------------------------------------------
    # shared broker/DB truth
    # ------------------------------------------------------------------

    def _daily_realized_pnl(self, now: int) -> float:
        day_start = now - now % 86400
        deals = self.gateway.history_deals_get(day_start, now + 60)
        return sum(d.profit + d.commission + d.swap + d.fee for d in deals)

    def _peak_equity(self, equity: float, now: int) -> float:
        peak = max(float(get_state(self.conn, "peak_equity", equity)), equity)
        put_state(self.conn, "peak_equity", peak, now_utc=now)
        return peak

    def _exposures(self) -> tuple[list[PositionExposure], list[PositionExposure]]:
        open_positions = [
            PositionExposure(p.canonical_symbol, p.direction, max(0.0, p.initial_monetary_risk or 0.0))
            for p in get_open_positions(self.conn)
        ]
        pending = []
        for order in get_active_orders(self.conn):
            if order.state.value not in _OPEN_EXPOSURE_ORDER_STATES:
                continue
            risk = order.remaining_pending_monetary_risk
            if risk is None:
                risk = order.requested_monetary_risk or 0.0
            if risk > 0:
                pending.append(PositionExposure(order.canonical_symbol, order.direction, risk))
        return open_positions, pending

    def readonly_reconciliation_status(self) -> str:
        """Fresh broker truth vs local state WITHOUT repairing or
        journaling -- used inside the pre-send evidence builder."""
        broker = [BrokerPositionSnapshot(p.broker_position_id, p.symbol, p.direction, p.volume)
                  for p in self.gateway.positions_get()]
        local_open = get_open_positions(self.conn)
        findings = reconcile_positions(local_open, broker)
        findings += reconcile_pending_orders(
            get_active_orders(self.conn), self.gateway.orders_get(),
            local_open_positions={p.broker_position_id: p for p in local_open}, now_utc=self.now(),
            own_magic=self.config.runtime.magic,
        )
        # ASN-022: a broker SL/TP execution of our own position is informational, never a block
        blocking = [f for f in findings if f.finding_type != PROTECTIVE_CLOSE_IN_PROGRESS]
        return CLEAN if not blocking else "BLOCKING_MISMATCH"

    def _reconciliation_max_age_seconds(self) -> int:
        return int(max(RECONCILIATION_MIN_MAX_AGE_SECONDS, 10 * self.config.runtime.position_cycle_seconds))

    def global_entry_block(self, now: int) -> tuple[str, str] | None:
        """Directive section 45: when new entries are globally blocked,
        don't burn cycles evaluating strategies."""
        kill = get_kill_switch_state(self.conn)
        if kill.blocks_new_entries:
            return BLOCK_KILL_SWITCH, f"kill switch {kill.status.value}"
        demo = verify_demo_before_order(self.gateway)
        if not demo.allowed:
            return demo.block_reason, demo.detail
        if has_dangerous_unresolved_unknown(self.conn):
            return BLOCK_UNKNOWN_ORDER, "a dangerous UNKNOWN order is unresolved"
        if has_unresolved_close(self.conn):
            return BLOCK_UNKNOWN_ORDER, "a close request's outcome is unresolved"
        recon = get_state(self.conn, "reconciliation", {})
        last_recon = recon.get("status")
        if last_recon != CLEAN:
            return BLOCK_RECONCILIATION, f"last reconciliation status={last_recon}"
        recon_age = now - int(recon.get("at") or 0)
        if recon_age > self._reconciliation_max_age_seconds():
            return BLOCK_RECONCILIATION, f"last CLEAN reconciliation is {recon_age}s old (stale)"
        truth = get_state(self.conn, "broker_truth", {}) or {}
        if truth.get("status") != BROKER_TRUTH_AVAILABLE:
            return BLOCK_RECONCILIATION, f"broker truth {truth.get('status')}: {truth.get('error')}"
        truth_age = now - int(truth.get("at") or 0)
        if truth_age > self._reconciliation_max_age_seconds():
            return BLOCK_RECONCILIATION, f"last complete position cycle is {truth_age}s old (stale)"
        news_block = self.news.global_block(now)
        if news_block is not None:
            return news_block
        account = self.gateway.account_info()
        equity = account.equity
        daily = self._daily_realized_pnl(now)
        if daily < 0 and abs(daily) / equity * 100 >= self.risk_limits.max_daily_loss_pct:
            return BLOCK_RISK, f"daily loss {abs(daily) / equity * 100:.2f}% reached the limit"
        peak = self._peak_equity(equity, now)
        if peak > 0 and (peak - equity) / peak * 100 >= self.risk_limits.max_drawdown_pct:
            return BLOCK_RISK, f"drawdown {(peak - equity) / peak * 100:.2f}% reached the limit"
        return None

    # ------------------------------------------------------------------
    # analysis (bar-close)
    # ------------------------------------------------------------------

    def analyze(self, canonical: str, now: int) -> SymbolAnalysis | None:
        broker = self.symbols[canonical]
        tick = self.gateway.symbol_info_tick(broker)
        spec = self.gateway.symbol_info(broker)
        if spec is None:
            return None
        bars = closed_bars(self.gateway, broker, self.resolution, now_utc=now,
                           count=self.config.runtime.bar_history_count, tick=tick)
        if len(bars) < FEATURE_LOOKBACK + 2:
            return None
        features = compute_bar_features(canonical, self.resolution, bars, lookback=FEATURE_LOOKBACK,
                                        point_size=spec.point, now=bars[-1].time)
        raw = classify_regime(features)
        key = f"regime:{canonical}"
        saved = get_state(self.conn, key)
        if saved is not None and saved["bar_time"] >= bars[-1].time:
            confirmed = saved["confirmed"]
        else:
            tracker = RegimeTracker(
                initial_regime=saved["confirmed"], initial_candidate=saved["candidate"],
                initial_candidate_count=saved["candidate_count"],
            ) if saved is not None else RegimeTracker()
            confirmed = tracker.update(raw)
            c, cand, count = tracker.state
            put_state(self.conn, key, {"confirmed": c, "candidate": cand, "candidate_count": count,
                                       "bar_time": bars[-1].time}, now_utc=now)
        analysis = SymbolAnalysis(bars[-1].time, features, raw, confirmed, numeric_feature_vector(features))
        self.latest[canonical] = analysis
        self.returns[canonical] = log_returns(bars)
        return analysis

    # ------------------------------------------------------------------
    # position cycle
    # ------------------------------------------------------------------

    def position_cycle(self) -> None:
        """One protective cycle. Its outcome is published as `broker_truth`:
        AVAILABLE only when every step completed; a broker query failure
        anywhere is UNAVAILABLE (and reconciliation BROKER_TRUTH_UNAVAILABLE),
        any other failure CYCLE_FAILED. Both block new exposure until a later
        cycle completes -- never cleared merely because time passed."""
        now = self.now()
        verdict_before = get_state(self.conn, "reconciliation", {}) or {}
        try:
            self._position_cycle(now)
        except Exception as exc:
            self._publish_broker_truth_failure(now, exc, verdict_before)
            raise
        self._publish_broker_truth_available(now)

    def _publish_broker_truth_failure(self, now: int, exc: BaseException, verdict_before: dict) -> None:
        unavailable = isinstance(exc, Mt5QueryError)
        error = f"{type(exc).__name__}: {exc}"
        previous = get_state(self.conn, "broker_truth", {}) or {}
        degraded_before = previous.get("status") not in (None, BROKER_TRUTH_AVAILABLE)
        put_state(self.conn, "broker_truth", {
            "status": BROKER_TRUTH_UNAVAILABLE if unavailable else BROKER_TRUTH_CYCLE_FAILED, "at": now,
            "since": previous.get("since") if degraded_before else now,
            "error": error, "last_available_at": previous.get("last_available_at"),
        }, now_utc=now)
        if unavailable:
            # Keep the last verdict that was actually proven (taken before this
            # cycle overwrote anything), labelled as such -- never shown as current.
            recon = get_state(self.conn, "reconciliation", {}) or {}
            if recon.get("status") not in (None, RECONCILIATION_ERROR, RECONCILIATION_BROKER_TRUTH_UNAVAILABLE):
                last_known = recon  # this cycle's own reconciliation succeeded before the failure
            elif verdict_before.get("status") == RECONCILIATION_BROKER_TRUTH_UNAVAILABLE:
                last_known = verdict_before.get("last_known")
            else:
                last_known = verdict_before or None
            put_state(self.conn, "reconciliation", {"status": RECONCILIATION_BROKER_TRUTH_UNAVAILABLE, "at": now,
                                                    "error": error, "last_known": last_known}, now_utc=now)
        record_event(self.conn, "BLOCKED", "broker_truth",
                     "BROKER_TRUTH_UNAVAILABLE" if unavailable else "POSITION_CYCLE_FAILED",
                     f"{error}; new exposure blocked until a full position cycle succeeds",
                     dedup_key="broker_truth:unavailable", now_utc=now)

    def _publish_broker_truth_available(self, now: int) -> None:
        from adaptive_scalper.runtime.state import clear_event
        previous = get_state(self.conn, "broker_truth", {}) or {}
        put_state(self.conn, "broker_truth", {"status": BROKER_TRUTH_AVAILABLE, "at": now, "since": None,
                                              "error": None, "last_available_at": now}, now_utc=now)
        if clear_event(self.conn, "broker_truth:unavailable", now_utc=now):
            record_event(self.conn, "INFO", "broker_truth", "BROKER_TRUTH_RESTORED",
                         f"full position cycle succeeded (degraded since {previous.get('since')})", now_utc=now)

    def _position_cycle(self, now: int) -> None:
        try:
            report = run_reconciliation(self.conn, self.gateway, f"reconcile:{now // 86400}", now_utc=now,
                                        journal_clean=False, own_magic=self.config.runtime.magic)
        except Exception as exc:
            # A failed broker snapshot is never "still CLEAN": persist ERROR
            # (position_cycle relabels a broker query failure
            # BROKER_TRUTH_UNAVAILABLE) so global_entry_block fails closed.
            put_state(self.conn, "reconciliation", {"status": RECONCILIATION_ERROR, "at": now,
                                                    "error": f"{type(exc).__name__}: {exc}"}, now_utc=now)
            record_event(self.conn, "BLOCKED", "reconciliation", "RECONCILIATION_FAILED",
                         f"{type(exc).__name__}: {exc}", dedup_key="reconciliation:failed", now_utc=now)
            raise
        from adaptive_scalper.runtime.state import clear_event
        clear_event(self.conn, "reconciliation:failed", now_utc=now)
        put_state(self.conn, "reconciliation", {"status": report.status, "at": now,
                                                "unrepaired_positions": report.unrepaired_position_ids,
                                                "unrepaired_orders": report.unrepaired_order_ids}, now_utc=now)
        if report.status != CLEAN:
            record_event(self.conn, "BLOCKED", "reconciliation", "RECONCILIATION_MISMATCH",
                         f"status={report.status}", dedup_key="reconciliation:mismatch", now_utc=now)
        else:
            clear_event(self.conn, "reconciliation:mismatch", now_utc=now)

        for outcome in apply_unknown_resolutions(self.conn, self.gateway, now_utc=now):
            record_event(self.conn, "INFO" if outcome.resolved else "BLOCKED", "execution", "UNKNOWN_RESOLUTION",
                         outcome.detail, dedup_key=f"unknown:{outcome.order_id}", now_utc=now)

        for resolution in resolve_unresolved_closes(self.conn, self.gateway, now_utc=now):
            if resolution.resolved:
                clear_event(self.conn, f"close_unresolved:{resolution.broker_position_id}", now_utc=now)
                record_event(self.conn, "INFO", "execution", "CLOSE_RESOLUTION",
                             f"{resolution.status}: {resolution.detail}", now_utc=now)
            else:
                record_event(self.conn, "BLOCKED", "execution", "CLOSE_UNRESOLVED", resolution.detail,
                             dedup_key=f"close_unresolved:{resolution.broker_position_id}", now_utc=now)

        broker_positions = {p.broker_position_id: p for p in self.gateway.positions_get()}
        truth_failure: Exception | None = None
        for local in get_open_positions(self.conn):
            live = broker_positions.get(local.broker_position_id)
            if live is None:
                continue  # reconciliation owns vanished positions
            if has_unresolved_close(self.conn, local.broker_position_id):
                continue  # an unproven close is never followed by another decision on this position
            try:
                self._review(local, live, now)
            except Exception as exc:  # one position's failure must not stop the others
                logger.exception("position review failed for %s", local.broker_position_id)
                record_event(self.conn, "ERROR", "position_manager", "REVIEW_FAILED", f"{type(exc).__name__}: {exc}",
                             canonical_symbol=local.canonical_symbol,
                             dedup_key=f"review_failed:{local.broker_position_id}", now_utc=now)
                if isinstance(exc, Mt5QueryError) and truth_failure is None:
                    truth_failure = exc
        if truth_failure is not None:
            raise truth_failure  # after every position was reviewed: this cycle did not see full broker truth

    def _entry_context(self, local) -> sqlite3.Row | dict | None:
        row = get_position_entry_context(self.conn, local.broker_position_id)
        if row is not None:
            return row
        # A position recovered from an UNKNOWN: rebuild its decision-time
        # context from the SIGNAL_CREATED event of its own decision chain.
        order = self.conn.execute("SELECT chain_key FROM orders WHERE id = ?", (local.entry_order_id,)).fetchone()
        if order is None or not order["chain_key"]:
            return None
        event = self.conn.execute(
            "SELECT je.payload_json, je.strategy_key FROM journal_events je JOIN decision_chains dc ON dc.id = je.chain_id "
            "WHERE dc.chain_key = ? AND je.event_type = 'SIGNAL_CREATED' ORDER BY je.sequence_in_chain LIMIT 1",
            (order["chain_key"],),
        ).fetchone()
        if event is None:
            return None
        p = json.loads(event["payload_json"])
        record_position_entry_context(
            self.conn, broker_position_id=local.broker_position_id, canonical_symbol=local.canonical_symbol,
            strategy_key=event["strategy_key"], strategy_version=p.get("strategy_version"), direction=local.direction,
            entry_regime=p.get("regime", "UNKNOWN"), raw_confidence=p.get("raw_confidence"),
            stop_distance_price=p["stop_distance"], target_distance_price=p["target_distance"],
            signal_bar_time_utc=p.get("bar_time_utc"), chain_key=order["chain_key"],
        )
        return get_position_entry_context(self.conn, local.broker_position_id)

    def _review(self, local, live, now: int) -> None:
        canonical = local.canonical_symbol
        context = self._entry_context(local)
        if context is None:
            record_event(self.conn, "WARNING", "position_manager", "UNMANAGEABLE_POSITION",
                         f"position {local.broker_position_id} has no decision-time context; broker SL/TP remain its "
                         f"only protection", canonical_symbol=canonical,
                         dedup_key=f"unmanageable:{local.broker_position_id}", now_utc=now)
            return
        analysis = self.latest.get(canonical) or self.analyze(canonical, now)
        if analysis is None:
            return
        broker = self.symbols[canonical]
        tick = self.gateway.symbol_info_tick(broker)
        spec = self.gateway.symbol_info(broker)
        price = (tick.bid if local.direction == "BUY" else tick.ask) if tick is not None else None
        strategy = self.registry.get(context["strategy_key"])
        setup_valid = False
        if strategy is not None:
            fresh = strategy.evaluate(analysis.features, RegimeClassification(analysis.confirmed_regime, 1.0, 1, "re-evaluation"))
            setup_valid = fresh is not None and fresh.direction == local.direction
        cost = live_remaining_exit_cost_estimate(self.config, canonical, spec, tick)
        target = live.take_profit or (
            local.entry_price + context["target_distance_price"] if local.direction == "BUY"
            else local.entry_price - context["target_distance_price"]
        )
        net_edge = None
        if cost is not None and price is not None:
            net_edge = (target - price if local.direction == "BUY" else price - target) - cost.total_cost
        inp = PositionReviewInput(
            position_id=local.id, broker_position_id=local.broker_position_id, canonical_symbol=canonical,
            broker_symbol=broker, direction=local.direction, volume=live.volume, entry_price=local.entry_price,
            initial_stop_distance_price=context["stop_distance_price"],
            initial_monetary_risk=local.initial_monetary_risk, unrealized_pnl=live.profit,
            entry_regime=context["entry_regime"], current_regime=analysis.confirmed_regime,
            strategy_setup_still_valid=setup_valid, current_net_edge_price=net_edge, min_required_edge_price=0.0,
            holding_seconds=max(0, now - local.opened_at_utc), strategy_key=context["strategy_key"],
            current_price_at_review=price,
            close_magic=self.config.runtime.magic, close_comment="ASN exit",
        )
        result = review_position_once(self.conn, self.gateway, inp, params=self.exit_params, now_utc=now,
                                      clock=self.clock)
        close = result.close_outcome
        if close is not None and close.broker_truth_error is not None:
            # The close request is durable and UNRESOLVED; surface the
            # unreadable broker truth as a cycle failure (degraded state).
            raise Mt5QueryError(f"broker truth unavailable after closing {local.broker_position_id}: "
                                f"{close.broker_truth_error}")

    # ------------------------------------------------------------------
    # entry cycle
    # ------------------------------------------------------------------

    def entry_cycle(self) -> dict:
        now = self.now()
        snapshot = {"at": now, "mode": MODE, "global_block": None, "symbols": {}}
        block = self.global_entry_block(now)
        if block is not None:
            code, reason = block
            snapshot["global_block"] = {"decision": code, "reason": reason}
            record_event(self.conn, "BLOCKED", "entry", "NEW_ENTRIES_BLOCKED", f"{code}: {reason}",
                         dedup_key="entry:global_block", now_utc=now)
            record_entry_decision(self.conn, mode=MODE, stage="GLOBAL", decision=code, reason=reason, now_utc=now)
            # Keep bar-close analysis fresh for position reviews anyway.
            for canonical in self.symbols:
                self.analyze(canonical, now)
            self._refresh_correlation()
            put_state(self.conn, "why_no_trade", snapshot, now_utc=now)
            self._publish_readiness(now)
            return snapshot
        from adaptive_scalper.runtime.state import clear_event
        clear_event(self.conn, "entry:global_block", now_utc=now)

        analyses = {c: self.analyze(c, now) for c in self.symbols}
        self._refresh_correlation()
        for canonical, analysis in analyses.items():
            try:
                snapshot["symbols"][canonical] = self._decide(canonical, analysis, now)
            except Exception as exc:
                logger.exception("entry decision failed for %s", canonical)
                snapshot["symbols"][canonical] = {"stage": "ERROR", "decision": "ERROR", "reason": str(exc)}
                record_event(self.conn, "ERROR", "entry", "ENTRY_DECISION_FAILED", f"{type(exc).__name__}: {exc}",
                             canonical_symbol=canonical, dedup_key=f"entry_failed:{canonical}", now_utc=now)
        put_state(self.conn, "why_no_trade", snapshot, now_utc=now)
        self._publish_readiness(now)
        return snapshot

    def _refresh_correlation(self) -> None:
        self.correlation = compute_correlation_matrix(
            {c: r for c, r in self.returns.items() if c in self.symbols},
            min_sample_size=self.config.runtime.correlation_min_samples,
        )

    def _publish_readiness(self, now: int) -> None:
        """Observer-only diagnostics for the dashboard's multi-position
        readiness view. Local computation over state this cycle already
        holds (no extra broker call); nothing here feeds a decision. A
        failure is logged and never interrupts the entry cycle."""
        try:
            open_positions, pending = self._exposures()
            busy = sorted({p.canonical_symbol for p in open_positions + pending})
            cost_evidence = {}
            for canonical in self.symbols:
                costs = self.config.cost_for(canonical)
                unknown = [name for name, value in (("commission", costs.commission_per_lot_round_trip),
                                                    ("slippage", costs.slippage_price)) if value is None]
                cost_evidence[canonical] = {"eligible": not unknown, "unknown_components": unknown,
                                            "provenance": costs.provenance}
            news = {}
            for canonical in self.symbols:
                result = self.news.block_for(canonical, now)
                news[canonical] = {"decision": result.decision, "reason": result.reason}
            put_state(self.conn, "multi_position_readiness", {
                "at": now, "open_or_pending_symbols": busy,
                "correlation": describe_correlation_pairs(list(self.symbols), self.correlation, busy),
                "correlation_min_samples": self.config.runtime.correlation_min_samples,
                "cost_evidence": cost_evidence, "news": news,
            }, now_utc=now)
        except Exception as exc:
            logger.warning("multi-position readiness snapshot failed: %s", exc)

    def _record(self, canonical, bar_time, stage, decision, reason, **kwargs) -> dict:
        record_entry_decision(self.conn, mode=MODE, stage=stage, decision=decision, reason=reason,
                              canonical_symbol=canonical, bar_time_utc=bar_time, now_utc=self.now(), **kwargs)
        return {"stage": stage, "decision": decision, "reason": reason, "bar_time_utc": bar_time}

    def _decide(self, canonical: str, analysis: SymbolAnalysis | None, now: int) -> dict:
        if analysis is None:
            return self._record(canonical, None, "DATA", "BLOCK_DATA_QUALITY", "not enough closed bars / no symbol info")
        last = get_state(self.conn, f"last_decided_bar:{canonical}", 0)
        if analysis.bar_time <= last:
            return {"stage": "WAIT", "decision": "WAITING_FOR_BAR_CLOSE", "reason": "no new closed bar",
                    "bar_time_utc": analysis.bar_time}
        put_state(self.conn, f"last_decided_bar:{canonical}", analysis.bar_time, now_utc=now)

        open_symbols = {p.canonical_symbol for p in get_open_positions(self.conn)}
        open_symbols |= {o.canonical_symbol for o in get_active_orders(self.conn)}
        if canonical in open_symbols:
            return self._record(canonical, analysis.bar_time, "SIGNAL", "FLAT", "position or order already open on this symbol")

        regime = RegimeClassification(analysis.confirmed_regime, analysis.raw_regime.confidence,
                                      analysis.raw_regime.version, analysis.raw_regime.reason)
        signals = [s for s in (st.evaluate(analysis.features, regime) for st in self.registry.all_active()) if s is not None]
        if not signals:
            return self._record(canonical, analysis.bar_time, "SIGNAL", "FLAT", f"no strategy signal (regime {analysis.confirmed_regime})")

        chains = [f"entry:{canonical}:{analysis.bar_time}:{s.strategy_key}" for s in signals]
        for chain, signal in zip(chains, signals):
            append_event(self.conn, chain, "SIGNAL_CREATED", now, canonical, {
                "strategy_version": signal.strategy_version, "direction": signal.direction,
                "raw_confidence": signal.raw_confidence, "stop_distance": signal.stop_distance,
                "target_distance": signal.target_distance, "regime": analysis.confirmed_regime,
                "resolutions": [self.resolution], "bar_time_utc": analysis.bar_time, "features": analysis.feature_vector,
            }, strategy_key=signal.strategy_key, broker_symbol=self.symbols[canonical])
            for evidence in self.advisory.gather(self.conn, canonical_symbol=canonical, strategy_key=signal.strategy_key,
                                                 direction=signal.direction, regime=analysis.confirmed_regime,
                                                 features=analysis.feature_vector):
                event_type = "MODEL_USED" if evidence.source == "ML_OBSERVER" else "RAG_USED"
                append_event(self.conn, chain, event_type, now, canonical,
                             {"source": evidence.source, "status": evidence.status, "evidence": evidence.summary,
                              "authority": "NONE (advisory evidence only)"}, strategy_key=signal.strategy_key)

        broker = self.symbols[canonical]
        tick = self.gateway.symbol_info_tick(broker)
        spec = self.gateway.symbol_info(broker)
        cost = live_cost_estimate(self.config, canonical, spec, tick)
        selection = select_and_journal_proposal(self.conn, signals, chains, {canonical: cost}, now_utc=now)
        if selection.selected is None:
            decision = BLOCK_COST if cost is None else "FLAT"
            return self._record(canonical, analysis.bar_time, "SELECTOR", decision, selection.reason)
        signal = selection.selected
        chain = chains[signals.index(signal)]

        account = self.gateway.account_info()
        sizing = calculate_safe_volume(equity=account.equity, risk_per_trade_pct=self.config.risk.risk_per_trade_pct,
                                       stop_distance_price=signal.stop_distance, symbol_spec=spec)
        if not sizing.approved:
            return self._record(canonical, analysis.bar_time, "SIZING", BLOCK_RISK, sizing.reason,
                                strategy_key=signal.strategy_key, direction=signal.direction, chain_key=chain)

        price = tick.ask if signal.direction == "BUY" else tick.bid
        sign = 1.0 if signal.direction == "BUY" else -1.0
        outcome = submit_new_entry(
            self.conn, self.gateway, chain_key=chain,
            client_request_id=f"{canonical}:{analysis.bar_time}:{signal.strategy_key}:{signal.direction}",
            canonical_symbol=canonical, broker_symbol=broker, direction=signal.direction, volume=sizing.volume,
            stop_loss=round(price - sign * signal.stop_distance, spec.digits),
            take_profit=round(price + sign * signal.target_distance, spec.digits),
            fetch_fresh_evidence=lambda: self.fresh_evidence(canonical, signal, sizing.monetary_risk),
            magic=self.config.runtime.magic, comment="ASN", now_utc=now, clock=self.clock,
        )
        if not outcome.status.startswith("BLOCK"):
            self._observe_execution_cost(outcome, chain, canonical, broker, signal.direction, tick, price,
                                         sizing.volume, cost, analysis.feature_vector, now)
        if outcome.status in (FILLED, PARTIAL):
            row = self.conn.execute("SELECT broker_position_id FROM positions WHERE entry_order_id = ?",
                                    (outcome.order.id,)).fetchone()
            if row is not None:
                record_position_entry_context(
                    self.conn, broker_position_id=row["broker_position_id"], canonical_symbol=canonical,
                    strategy_key=signal.strategy_key, strategy_version=signal.strategy_version,
                    direction=signal.direction, entry_regime=analysis.confirmed_regime,
                    raw_confidence=signal.raw_confidence, stop_distance_price=signal.stop_distance,
                    target_distance_price=signal.target_distance, signal_bar_time_utc=analysis.bar_time,
                    chain_key=chain, now_utc=now,
                )
        return self._record(canonical, analysis.bar_time, "EXECUTION", outcome.status, outcome.detail,
                            strategy_key=signal.strategy_key, direction=signal.direction, chain_key=chain)

    def _observe_execution_cost(self, outcome, chain, canonical, broker, direction, tick, price, volume, cost,
                                features, now) -> None:
        """Phase 7 evidence, recorded after the broker call returned. A
        failure here is logged and swallowed: it can never change what
        happened to the order."""
        try:
            record_entry_observation(
                self.conn, order_id=outcome.order.id, chain_key=chain, canonical_symbol=canonical,
                broker_symbol=broker, direction=direction, outcome_status=outcome.status, decided_at_utc=now,
                tick=tick, requested_price=price, requested_volume=volume, estimate=cost, features=features,
                news_windows=self.news.windows_for(canonical), now_utc=now,
            )
        except Exception as exc:
            logger.warning("execution cost observation failed: %s", exc)
            record_event(self.conn, "WARNING", "costs", "COST_OBSERVATION_FAILED", f"{type(exc).__name__}: {exc}",
                         canonical_symbol=canonical, dedup_key=f"cost_observation:{canonical}", now_utc=now)

    # ------------------------------------------------------------------
    # the pre-send evidence builder (called twice by submit_new_entry)
    # ------------------------------------------------------------------

    def fresh_evidence(self, canonical: str, signal: StrategySignal, proposed_risk: float) -> FreshEvidence:
        now = self.now()
        broker = self.symbols[canonical]
        spec = self.gateway.symbol_info(broker)
        tick = self.gateway.symbol_info_tick(broker)
        account = self.gateway.account_info()
        open_positions, pending = self._exposures()
        same_symbol = [p for p in open_positions if p.canonical_symbol == canonical]
        # A working order on a symbol with no open position is a position the
        # broker can open at any moment, so it takes a max_open_positions slot.
        # A PARTIAL order's remainder belongs to its own open position: counted once.
        open_symbols = {p.canonical_symbol for p in open_positions}
        pending_only = [o for o in get_active_orders(self.conn)
                        if o.state.value in _OPEN_EXPOSURE_ORDER_STATES and o.canonical_symbol not in open_symbols]
        reentry = self._reentry_check(canonical, signal, now)
        permission = FinalPermissionInput(
            mode=MODE, signal=signal, kill_switch_state=get_kill_switch_state(self.conn),
            demo_verification=verify_demo_before_order(self.gateway),
            reconciliation_status=self.readonly_reconciliation_status(),
            has_dangerous_unknown_order=has_dangerous_unresolved_unknown(self.conn),
            duplicate_active_order=any(o.canonical_symbol == canonical for o in get_active_orders(self.conn)),
            asset_identity=validate_resolved_symbol(self.gateway, canonical, broker, now=self.clock()),
            direction_check=validate_direction_for_new_exposure(spec.trade_mode, signal.direction),
            execution_quote=validate_execution_quote(tick, max_quote_age_seconds=DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
                                                     now=self.clock()),
            news_result=self.news.block_for(canonical, now),
            cost_estimate=live_cost_estimate(self.config, canonical, spec, tick),
            open_or_pending_symbols=sorted({p.canonical_symbol for p in open_positions + pending}),
            correlation_matrix=self.correlation,
            risk_gate_input=RiskGateInput(
                proposed_symbol=canonical, proposed_monetary_risk=proposed_risk, equity=account.equity,
                current_total_open_risk=sum(p.monetary_risk for p in open_positions),
                current_total_pending_risk=sum(p.monetary_risk for p in pending),
                current_positions_count=len(open_positions) + len(pending_only),
                current_positions_for_symbol=len(same_symbol),
                daily_realized_pnl=self._daily_realized_pnl(now), peak_equity=self._peak_equity(account.equity, now),
            ),
            risk_limits=self.risk_limits, open_positions=open_positions, pending_positions=pending,
            portfolio_risk_limits=self.portfolio_limits, reentry_check=reentry,
        )
        return FreshEvidence(permission_input=permission, symbol_spec=spec, available_margin_free=account.margin_free)

    def _reentry_check(self, canonical: str, signal: StrategySignal, now: int) -> tuple[str, str] | None:
        row = self.conn.execute(
            "SELECT p.direction, p.closed_at_utc, c.raw_confidence FROM positions p "
            "LEFT JOIN position_entry_context c ON c.broker_position_id = p.broker_position_id "
            "WHERE p.canonical_symbol = ? AND p.status = 'CLOSED' AND p.closed_at_utc IS NOT NULL "
            "ORDER BY p.closed_at_utc DESC LIMIT 1", (canonical,),
        ).fetchone()
        if row is None:
            return None
        return evaluate_reentry(
            proposed_direction=signal.direction, proposed_raw_confidence=signal.raw_confidence,
            original_raw_confidence=row["raw_confidence"] if row["raw_confidence"] is not None else 1.0,
            last_exit_utc=row["closed_at_utc"], last_exit_direction=row["direction"], now_utc=now,
            params=self.reentry_params,
        )
