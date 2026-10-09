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
import dataclasses
from dataclasses import dataclass, field
from typing import Callable

from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.core.final_permission import FinalPermissionInput
from adaptive_scalper.core.kill_switch import KillSwitchStatus
from adaptive_scalper.core.kill_switch import engage as engage_kill_switch
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.costs.model import (
    HORIZON_REMAINING_EXIT,
    CostEstimate,
    price_equivalent_of_monetary_cost,
    remaining_exit_cost_from_evidence,
    require_horizon,
    round_trip_cost_from_evidence,
)
from adaptive_scalper.costs.edge import BLOCK_EDGE_UNVALIDATED
from adaptive_scalper.costs.edge_evidence import (
    NO_VALIDATED_EDGE_EVIDENCE,
    EdgeEvidenceProvider,
    executable_evidence,
    require_executable_provider,
)
from adaptive_scalper.costs.swap_horizon import swap_price_for_horizon
from adaptive_scalper.validation.certificate import load_certificate_public_key
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
from adaptive_scalper.execution.close import close_position_safely
from adaptive_scalper.execution.close_requests import has_unresolved_close, resolve_unresolved_closes
from adaptive_scalper.execution.recovery import apply_unknown_resolutions
from adaptive_scalper.costs.observations import record_entry_observation
from adaptive_scalper.execution.service import FILLED, PARTIAL, FreshEvidence, submit_new_entry
from adaptive_scalper.execution.store import get_active_orders
from adaptive_scalper.features.adx import wilder_adx
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
from adaptive_scalper.risk.entry_regime import adx_entry_block
from adaptive_scalper.risk.entry_window import entry_window_block
from adaptive_scalper.risk.governor import (
    RiskGateInput,
    calculate_safe_volume,
    effective_daily_loss_pct,
    risk_limits_from_config,
)
from adaptive_scalper.risk.hard_stop import adverse_move_pct, breaches_hard_stop
from adaptive_scalper.runtime.advisory import AdvisoryPanel
from adaptive_scalper.runtime.market_data import closed_bars, log_returns
from adaptive_scalper.runtime.microstructure import publish_vwap_bands
from adaptive_scalper.runtime.news_monitor import NewsMonitor
from adaptive_scalper.runtime.state import (
    get_position_entry_context,
    get_state,
    put_state,
    record_entry_decision,
    record_event,
    record_position_entry_context,
)
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.selector.selector import REJECTED_EDGE_UNVALIDATED, select_and_journal_proposal, select_proposal
from adaptive_scalper.shadow.observer import LOST_TO_HIGHER_EDGE as SHADOW_LOST
from adaptive_scalper.shadow.observer import NOT_EVALUATED as SHADOW_NOT_EVALUATED
from adaptive_scalper.shadow.observer import REJECTED as SHADOW_REJECTED
from adaptive_scalper.shadow.observer import SELECTED as SHADOW_SELECTED
from adaptive_scalper.runtime.paper import paper_config
from adaptive_scalper.shadow.lifecycle_counterfactual import resolve_lifecycles
from adaptive_scalper.shadow.observer import ShadowCandidate, record_candidates, resolve_due
from adaptive_scalper.selector.suspension import REJECTED_ENTRY_SUSPENDED, partition_suspended
from adaptive_scalper.strategies.base import StrategySignal

logger = logging.getLogger(__name__)

MODE = "DEMO"
FEATURE_LOOKBACK = 20
BLOCK_KILL_SWITCH = "BLOCK_KILL_SWITCH"
BLOCK_UNKNOWN_ORDER = "BLOCK_UNKNOWN_ORDER"
BLOCK_RECONCILIATION = "BLOCK_RECONCILIATION"
BLOCK_RISK = "BLOCK_RISK"
BLOCK_COST = "BLOCK_COST"
DAILY_LOSS_LOCK_STATE_KEY = "daily_loss_lock"
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
    adx: float | None = None  # Wilder ADX of the same closed bars ([entry_regime] gate)


# Allowance between "max hold reached" and the closing fill: one position
# cycle plus broker round trip, generously rounded up.
CLOSE_LATENCY_ALLOWANCE_SECONDS = 60


def max_hold_horizon_seconds(params: AdaptiveExitParams) -> int | None:
    """The longest a newly opened position can be held by design, or None
    when no maximum is enforced (then every swap question is "can cross")."""
    if not params.max_holding_enabled:
        return None
    return params.max_holding_seconds + CLOSE_LATENCY_ALLOWANCE_SECONDS


def _commission_price(costs, spec: SymbolSpec) -> float | None:
    if costs.commission_per_lot_round_trip is None:
        return None
    return price_equivalent_of_monetary_cost(costs.commission_per_lot_round_trip, spec.trade_tick_size,
                                             spec.trade_tick_value)


def live_cost_estimate(
    config: AppConfig, canonical_symbol: str, spec: SymbolSpec | None, tick: Tick | None, *,
    now_utc: int, max_hold_seconds: int | None,
) -> CostEstimate | None:
    """Conservative PRE-ENTRY cost of the whole trade (HORIZON_FULL_ROUND_TRIP).

    `slippage_price` is per fill, so a market entry plus a market/stop exit
    pays it twice (costs/model.py FILLS_PER_ROUND_TRIP); the full bid/ask
    spread is paid once across the two fills; commission is the configured
    round trip; swap is charged only if `now_utc + max_hold_seconds` can
    cross the broker's server-midnight rollover, and an unknown swap on such
    a horizon -- like any other unknown component -- returns None (BLOCK_COST).
    """
    if spec is None or tick is None or tick.ask <= 0 or tick.bid <= 0:
        return None
    costs = config.cost_for(canonical_symbol)
    swap = swap_price_for_horizon(
        server_time_rule=config.mt5.server_time_rule, start_utc=now_utc, max_hold_seconds=max_hold_seconds,
        swap_per_lot_per_day=costs.swap_per_lot_per_day, tick_size=spec.trade_tick_size,
        tick_value=spec.trade_tick_value,
    )
    return round_trip_cost_from_evidence(
        spread_price=tick.ask - tick.bid, per_fill_slippage_price=costs.slippage_price,
        round_trip_commission_price=_commission_price(costs, spec), swap_price_equivalent=swap,
    )


def live_remaining_exit_cost_estimate(
    config: AppConfig, canonical_symbol: str, spec: SymbolSpec | None, tick: Tick | None, *,
    now_utc: int, remaining_hold_seconds: int | None,
) -> CostEstimate | None:
    """Friction still payable on an ALREADY OPEN position (HORIZON_REMAINING_EXIT).

    Entry spread, entry slippage and the entry half of the commission are
    sunk and never charged again. _review marks a long at bid and a short
    at ask -- the executable closing side -- so the exit spread is already in
    the mark (exit_spread_price=0.0). Remaining: one exit slippage, the exit
    half of the commission, and swap only if the remaining hold can cross
    a rollover.
    """
    if spec is None or tick is None or tick.ask <= 0 or tick.bid <= 0:
        return None
    costs = config.cost_for(canonical_symbol)
    swap = swap_price_for_horizon(
        server_time_rule=config.mt5.server_time_rule, start_utc=now_utc, max_hold_seconds=remaining_hold_seconds,
        swap_per_lot_per_day=costs.swap_per_lot_per_day, tick_size=spec.trade_tick_size,
        tick_value=spec.trade_tick_value,
    )
    return remaining_exit_cost_from_evidence(
        exit_spread_price=0.0, per_fill_slippage_price=costs.slippage_price,
        round_trip_commission_price=_commission_price(costs, spec), swap_price_equivalent=swap,
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
    # Expected-edge evidence (issue #6). The default has none, so every
    # proposal is FLAT (BLOCK_EDGE_UNVALIDATED); the legacy V1 raw-score
    # replay provider is refused at construction.
    edge_evidence: EdgeEvidenceProvider = NO_VALIDATED_EDGE_EVIDENCE
    # Closed bars from the latest analysis, reused by the shadow observer so
    # it needs no extra broker read.
    shadow_bars: dict[str, list] = field(default_factory=dict)
    # Edge-certificate verification (validation/certificate.py): the Ed25519
    # PUBLIC key only. None -> read the file named by
    # ASN_EDGE_CERTIFICATE_PUBLIC_KEY_FILE; still none -> no certificate
    # verifies -> every proposal stays FLAT. The runtime never holds the
    # private signing seed and has no way to choose the validation protocol.
    public_key: bytes | None = None

    def __post_init__(self) -> None:
        require_executable_provider(self.edge_evidence, "DEMO runtime")
        if self.public_key is None:
            self.public_key = load_certificate_public_key()
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

    def _daily_loss(self, now: int, account) -> tuple[float, float, float, float]:
        """(effective daily loss %, realized today, floating now, day-start
        equity). Floating = broker equity - balance (every open position,
        swap included); day-start equity = balance - today's realized P&L
        (deposits/withdrawals today are not modelled; the denominator is
        still capped at current equity, risk.governor.effective_daily_loss_pct)."""
        realized = self._daily_realized_pnl(now)
        floating = account.equity - account.balance
        day_start = account.balance - realized
        pct = effective_daily_loss_pct(realized_pnl=realized, floating_pnl=floating,
                                       day_start_equity=day_start, equity=account.equity)
        return pct, realized, floating, day_start

    def _daily_loss_circuit_breaker(self, now: int, broker_positions: dict) -> bool:
        """Hard daily circuit breaker. When realized + floating loss reaches
        `max_daily_loss_pct`: engage the kill switch (new entries stay
        blocked until the OPERATOR clears it -- this never clears it) and
        close every locally tracked open position through the safe close
        service (durable close request, fresh-truth resolution). DEMO only
        sends market DEALs, so there are no resting entry orders to cancel;
        any working order seen with this runtime's magic is surfaced as an
        incident event. Returns True when the breaker is tripped (the cycle
        then skips normal exit reviews)."""
        account = self.gateway.account_info()
        if account is None:
            raise Mt5QueryError("account_info unavailable for the daily loss circuit breaker")
        pct, realized, floating, day_start = self._daily_loss(now, account)
        if pct < self.risk_limits.max_daily_loss_pct:
            return False
        reason = (f"daily loss circuit breaker: {pct:.2f}% >= max_daily_loss_pct "
                  f"{self.risk_limits.max_daily_loss_pct}% (realized {realized:.2f}, floating {floating:.2f}, "
                  f"day-start equity {day_start:.2f})")
        kill = get_kill_switch_state(self.conn)
        if kill.status != KillSwitchStatus.ENGAGED:
            engage_kill_switch(self.conn, reason, actor="risk_governor:daily_loss_circuit_breaker")
        record_event(self.conn, "BLOCKED", "risk", "DAILY_LOSS_CIRCUIT_BREAKER", reason,
                     dedup_key=f"daily_loss_breaker:{now // 86400}", now_utc=now)
        # Same-UTC-day lock: new entries stay blocked until the next UTC day even if the
        # operator clears the kill switch earlier (global_entry_block).
        put_state(self.conn, DAILY_LOSS_LOCK_STATE_KEY, {"utc_day": now // 86400, "reason": reason}, now_utc=now)
        truth_error: str | None = None
        for local in get_open_positions(self.conn):
            live = broker_positions.get(local.broker_position_id)
            if live is None or has_unresolved_close(self.conn, local.broker_position_id):
                continue  # reconciliation / close resolution own these
            outcome = close_position_safely(
                self.gateway, broker_position_id=local.broker_position_id, expected_direction=local.direction,
                expected_volume=live.volume, broker_symbol=live.symbol, magic=self.config.runtime.magic,
                comment="ASN daily-loss flatten", clock=self.clock, conn=self.conn,
                reconciliation_chain_key=f"daily-loss-breaker:{local.id}",
            )
            record_event(self.conn, "WARNING", "risk", "DAILY_LOSS_FLATTEN",
                         f"position {local.broker_position_id}: {outcome.status}: {outcome.detail}",
                         canonical_symbol=local.canonical_symbol,
                         dedup_key=f"daily_loss_flatten:{local.broker_position_id}:{outcome.status}", now_utc=now)
            if outcome.broker_truth_error is not None and truth_error is None:
                truth_error = outcome.broker_truth_error
        working = [o for o in self.gateway.orders_get() if o.magic == self.config.runtime.magic]
        if working:
            record_event(self.conn, "BLOCKED", "risk", "DAILY_LOSS_WORKING_ORDERS",
                         f"{len(working)} working order(s) with this runtime's magic while the daily loss breaker "
                         f"is tripped: {[o.broker_order_id for o in working]} -- the runtime has no cancel path; "
                         f"operator action required", dedup_key=f"daily_loss_orders:{now // 86400}", now_utc=now)
        if truth_error is not None:
            raise Mt5QueryError(f"broker truth unavailable after a daily-loss flatten: {truth_error}")
        return True

    def _hard_cut_loss(self, local, live, now: int) -> bool:
        """Hard cut-loss backstop (config `[hard_stop]`): close at market,
        through the safe close service, a position whose close-side price has
        moved STRICTLY more than `max_adverse_move_pct` against its broker
        fill price -- independent of the broker SL. Returns True when a close
        was attempted (the normal review is then skipped this cycle). A quote
        that is missing/stale/invalid is journaled and never treated as 'no
        loss'; the normal review (and the broker SL) still run."""
        max_pct = self.config.hard_stop.max_adverse_move_pct.get(local.canonical_symbol)
        if max_pct is None:
            return False
        tick = self.gateway.symbol_info_tick(live.symbol)
        quote = validate_execution_quote(tick, max_quote_age_seconds=DEFAULT_MAX_EXECUTION_QUOTE_AGE_SECONDS,
                                         now=self.clock())
        adverse = adverse_move_pct(live.direction, live.price_open, tick.bid, tick.ask) if quote.valid else None
        if adverse is None:
            record_event(self.conn, "WARNING", "risk", "HARD_CUT_LOSS_UNEVALUATED",
                         f"position {local.broker_position_id}: no usable quote/price to evaluate the "
                         f"{max_pct}% hard cut-loss ({quote.detail})", canonical_symbol=local.canonical_symbol,
                         dedup_key=f"hard_cut_loss_unevaluated:{local.broker_position_id}", now_utc=now)
            return False
        if not breaches_hard_stop(adverse, max_pct):
            return False
        reason = (f"hard cut-loss: {live.direction} {local.broker_position_id} moved {adverse:.3f}% against fill "
                  f"{live.price_open} (bid {tick.bid}, ask {tick.ask}) > {max_pct}%")
        record_event(self.conn, "BLOCKED", "risk", "HARD_CUT_LOSS_TRIGGERED", reason,
                     canonical_symbol=local.canonical_symbol,
                     dedup_key=f"hard_cut_loss:{local.broker_position_id}", now_utc=now)
        outcome = close_position_safely(
            self.gateway, broker_position_id=local.broker_position_id, expected_direction=local.direction,
            expected_volume=live.volume, broker_symbol=live.symbol, magic=self.config.runtime.magic,
            comment="ASN hard cut-loss", clock=self.clock, conn=self.conn,
            reconciliation_chain_key=f"hard-cut-loss:{local.id}",
        )
        record_event(self.conn, "WARNING", "risk", "HARD_CUT_LOSS_CLOSE",
                     f"position {local.broker_position_id}: {outcome.status}: {outcome.detail}",
                     canonical_symbol=local.canonical_symbol,
                     dedup_key=f"hard_cut_loss_close:{local.broker_position_id}:{outcome.status}", now_utc=now)
        if outcome.broker_truth_error is not None:
            raise Mt5QueryError(f"broker truth unavailable after a hard cut-loss close: {outcome.broker_truth_error}")
        return True

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
        lock = get_state(self.conn, DAILY_LOSS_LOCK_STATE_KEY) or {}
        if lock.get("utc_day") == now // 86400:
            return BLOCK_RISK, ("daily loss circuit breaker tripped today -- new entries locked until "
                                "00:00 UTC (" + str(lock.get("reason")) + ")")
        news_block = self.news.global_block(now)
        if news_block is not None:
            return news_block
        session_block = entry_window_block(now, self.config.entry_window.hours())
        if session_block is not None:
            return session_block
        account = self.gateway.account_info()
        equity = account.equity
        daily_pct, realized, floating, _ = self._daily_loss(now, account)
        if daily_pct >= self.risk_limits.max_daily_loss_pct:
            return BLOCK_RISK, (f"daily loss {daily_pct:.2f}% (realized {realized:.2f}, floating {floating:.2f}) "
                                f"reached the limit")
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
        analysis = SymbolAnalysis(bars[-1].time, features, raw, confirmed, numeric_feature_vector(features),
                                  adx=wilder_adx(bars, self.config.entry_regime.adx_period))
        self.latest[canonical] = analysis
        self.returns[canonical] = log_returns(bars)
        self.shadow_bars[canonical] = bars
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
        if self._daily_loss_circuit_breaker(now, broker_positions):
            return  # flattening this cycle; normal exit reviews resume once nothing is left to close
        truth_failure: Exception | None = None
        for local in get_open_positions(self.conn):
            live = broker_positions.get(local.broker_position_id)
            if live is None:
                continue  # reconciliation owns vanished positions
            if has_unresolved_close(self.conn, local.broker_position_id):
                continue  # an unproven close is never followed by another decision on this position
            try:
                if self._hard_cut_loss(local, live, now):
                    continue  # closed (or a close attempted) by the hard cut-loss backstop this cycle
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
        horizon = max_hold_horizon_seconds(self.exit_params)
        cost = live_remaining_exit_cost_estimate(
            self.config, canonical, spec, tick, now_utc=now,
            remaining_hold_seconds=None if horizon is None else max(0, horizon - (now - local.opened_at_utc)),
        )
        require_horizon(cost, HORIZON_REMAINING_EXIT, "DEMO position review")
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

    def _shadow_observe(self, canonical: str, analysis: SymbolAnalysis | None, now: int, *,
                        global_block: str | None) -> None:
        """Forward-only evidence (adaptive_scalper/shadow/observer.py): record
        every active strategy's candidate for each NEW closed bar -- including
        rejected and globally blocked ones -- and resolve outcomes whose
        horizon has elapsed. Pure observation: no order path, no journal
        decision, no state any gate reads. A failure here is journaled and
        swallowed; it can never change a trading decision."""
        try:
            resolve_due(self.conn, canonical, self.shadow_bars.get(canonical, []), now_utc=now)
            spec_now = self.gateway.symbol_info(self.symbols[canonical])
            if spec_now is not None:
                costs = self.config.cost_for(canonical)
                resolve_lifecycles(
                    self.conn, canonical, self.shadow_bars.get(canonical, []), now_utc=now, symbol_spec=spec_now,
                    config=dataclasses.replace(paper_config(self.config, canonical, ()),
                                               adaptive_exit_params=self.exit_params),
                    costs_known=costs.fully_known, cost_provenance=costs.provenance,
                    strategies={s.key: s for s in self.registry.all_active()},
                )
            if analysis is None:
                return
            cursor = f"shadow_last_bar:{canonical}"
            if analysis.bar_time <= get_state(self.conn, cursor, 0):
                return
            regime = RegimeClassification(analysis.confirmed_regime, analysis.raw_regime.confidence,
                                          analysis.raw_regime.version, analysis.raw_regime.reason)
            signals = [s for s in (st.evaluate(analysis.features, regime) for st in self.registry.all_active())
                       if s is not None]
            if signals:
                broker = self.symbols[canonical]
                tick = self.gateway.symbol_info_tick(broker)
                spec = self.gateway.symbol_info(broker)
                cost = live_cost_estimate(self.config, canonical, spec, tick, now_utc=now,
                                          max_hold_seconds=max_hold_horizon_seconds(self.exit_params))
                dispositions: dict[int, tuple[str, str | None]] = {}
                if global_block is not None:
                    dispositions = {id(s): (SHADOW_NOT_EVALUATED, global_block) for s in signals}
                else:
                    # Same order as _decide: entry-suspended strategies are removed before the selector
                    # (still observed here, recorded as REJECTED strategy_entry_suspended).
                    kept, dropped = partition_suspended(signals, self.config.strategies.entry_suspended)
                    for i in dropped:
                        dispositions[id(signals[i])] = (SHADOW_REJECTED, REJECTED_ENTRY_SUSPENDED)
                    selection = select_proposal([signals[i] for i in kept], {canonical: cost},
                                                edge_evidence=self.edge_evidence)
                    for e in selection.candidates:
                        if e.rejected:
                            dispositions[id(e.signal)] = (SHADOW_REJECTED, e.rejection_reason)
                        elif e.signal is selection.selected:
                            dispositions[id(e.signal)] = (SHADOW_SELECTED, None)
                        else:
                            dispositions[id(e.signal)] = (SHADOW_LOST, "lost_to_higher_expected_net_edge")
                news = self.news.block_for(canonical, now)
                lag = ((get_state(self.conn, "engine", {}) or {}).get("tasks", {}).get("entry_cycle", {})
                       .get("last_lag_seconds"))
                f = analysis.features
                costs = self.config.cost_for(canonical)
                record_candidates(self.conn, [ShadowCandidate(
                    canonical_symbol=canonical, resolution=self.resolution,
                    bar_seconds=resolution_seconds(self.resolution), decision_bar_time_utc=analysis.bar_time,
                    strategy_key=s.strategy_key, strategy_version=s.strategy_version, direction=s.direction,
                    raw_score=s.raw_confidence, stop_distance=s.stop_distance, target_distance=s.target_distance,
                    expected_duration_seconds=s.expected_duration_seconds,
                    raw_regime=analysis.raw_regime.regime, confirmed_regime=analysis.confirmed_regime,
                    regime_confidence=analysis.raw_regime.confidence, session=getattr(f, "session", None),
                    news_status=news.decision, news_detail=news.reason,
                    spread_points=getattr(f, "spread_current", None),
                    spread_percentile=getattr(f, "spread_percentile", None), atr=getattr(f, "atr", None),
                    realized_volatility=getattr(f, "realized_volatility", None),
                    movement_to_cost=getattr(f, "movement_to_cost", None),
                    estimated_round_trip_cost_price=cost.total_cost if cost is not None else None,
                    cost_horizon=cost.horizon if cost is not None else None, cost_provenance=costs.provenance,
                    edge_model=self.edge_evidence.model_id, scheduler_lag_seconds=lag,
                    selector_disposition=dispositions[id(s)][0], rejection_reason=dispositions[id(s)][1],
                    final_permission_result=("DECIDED_IN_ENTRY_DECISIONS" if dispositions[id(s)][0] == SHADOW_SELECTED
                                             else "NOT_REACHED"),
                    chain_key=f"entry:{canonical}:{analysis.bar_time}:{s.strategy_key}",
                    model_observer_score=None, features=analysis.feature_vector,
                ) for s in signals], mode=MODE, now_utc=now)
            put_state(self.conn, cursor, analysis.bar_time, now_utc=now)
        except Exception as exc:  # observation must never affect trading
            logger.exception("shadow observer failed for %s", canonical)
            record_event(self.conn, "WARNING", "shadow", "SHADOW_OBSERVER_FAILED", f"{type(exc).__name__}: {exc}",
                         canonical_symbol=canonical, dedup_key=f"shadow_failed:{canonical}", now_utc=now)

    def entry_cycle(self) -> dict:
        now = self.now()
        try:  # observer only: never blocks or causes a trade
            publish_vwap_bands(self.conn, self.gateway, self.config, self.symbols, now)
        except Exception:
            logger.exception("microstructure observer failed")
        snapshot = {"at": now, "mode": MODE, "global_block": None, "symbols": {}}
        block = self.global_entry_block(now)
        if block is not None:
            code, reason = block
            snapshot["global_block"] = {"decision": code, "reason": reason}
            record_event(self.conn, "BLOCKED", "entry", "NEW_ENTRIES_BLOCKED", f"{code}: {reason}",
                         dedup_key="entry:global_block", now_utc=now)
            record_entry_decision(self.conn, mode=MODE, stage="GLOBAL", decision=code, reason=reason, now_utc=now)
            # Keep bar-close analysis fresh for position reviews anyway, and
            # keep collecting shadow evidence (candidates NOT_EVALUATED).
            for canonical in self.symbols:
                self._shadow_observe(canonical, self.analyze(canonical, now), now, global_block=code)
            self._refresh_correlation()
            put_state(self.conn, "why_no_trade", snapshot, now_utc=now)
            self._publish_readiness(now)
            return snapshot
        from adaptive_scalper.runtime.state import clear_event
        clear_event(self.conn, "entry:global_block", now_utc=now)

        analyses = {c: self.analyze(c, now) for c in self.symbols}
        for canonical, analysis in analyses.items():
            self._shadow_observe(canonical, analysis, now, global_block=None)
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

        regime_block = adx_entry_block(analysis.adx, self.config.entry_regime.min_adx)
        if regime_block is not None:
            return self._record(canonical, analysis.bar_time, "REGIME", *regime_block)

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

        # Entry-suspended strategies: journaled as evidence, never offered to the (frozen V1) selector.
        kept, dropped = partition_suspended(signals, self.config.strategies.entry_suspended)
        for i in dropped:
            append_event(self.conn, chains[i], "SIGNAL_REJECTED", now, canonical, {
                "strategy_key": signals[i].strategy_key, "direction": signals[i].direction,
                "raw_confidence": signals[i].raw_confidence, "expected_net_edge": None,
                "reason": REJECTED_ENTRY_SUSPENDED,
            }, strategy_key=signals[i].strategy_key)
        signals, chains = [signals[i] for i in kept], [chains[i] for i in kept]
        if not signals:
            return self._record(canonical, analysis.bar_time, "SELECTOR", "FLAT",
                                f"every signal came from an entry-suspended strategy ({REJECTED_ENTRY_SUSPENDED})")

        broker = self.symbols[canonical]
        tick = self.gateway.symbol_info_tick(broker)
        spec = self.gateway.symbol_info(broker)
        cost = live_cost_estimate(self.config, canonical, spec, tick, now_utc=now,
                                  max_hold_seconds=max_hold_horizon_seconds(self.exit_params))
        selection = select_and_journal_proposal(self.conn, signals, chains, {canonical: cost}, now_utc=now,
                                                edge_evidence=self.edge_evidence)
        if selection.selected is None:
            if cost is None:
                decision = BLOCK_COST
            elif all(e.rejection_reason == REJECTED_EDGE_UNVALIDATED for e in selection.candidates):
                decision = BLOCK_EDGE_UNVALIDATED
            else:
                decision = "FLAT"
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
            max_spread_price=self.config.cost_for(canonical).max_spread_price,
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
        realized_today = self._daily_realized_pnl(now)
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
            cost_estimate=live_cost_estimate(self.config, canonical, spec, tick, now_utc=now,
                                             max_hold_seconds=max_hold_horizon_seconds(self.exit_params)),
            edge_evidence=executable_evidence(self.edge_evidence.for_signal(signal)),
            public_key=self.public_key, now_utc=now,
            proposal_features=(self.latest[canonical].feature_vector if canonical in self.latest else None),
            entry_suspended_strategy_keys=frozenset(self.config.strategies.entry_suspended),
            open_or_pending_symbols=sorted({p.canonical_symbol for p in open_positions + pending}),
            correlation_matrix=self.correlation,
            risk_gate_input=RiskGateInput(
                proposed_symbol=canonical, proposed_monetary_risk=proposed_risk, equity=account.equity,
                current_total_open_risk=sum(p.monetary_risk for p in open_positions),
                current_total_pending_risk=sum(p.monetary_risk for p in pending),
                current_positions_count=len(open_positions) + len(pending_only),
                current_positions_for_symbol=len(same_symbol),
                daily_realized_pnl=realized_today, peak_equity=self._peak_equity(account.equity, now),
                daily_floating_pnl=account.equity - account.balance,
                day_start_equity=account.balance - realized_today,
            ),
            risk_limits=self.risk_limits, open_positions=open_positions, pending_positions=pending,
            portfolio_risk_limits=self.portfolio_limits, reentry_check=reentry,
            entry_window_hours=self.config.entry_window.hours(),
            entry_adx=self.latest[canonical].adx if canonical in self.latest else None,
            min_entry_adx=self.config.entry_regime.min_adx,
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
