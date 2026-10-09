"""Backtest configuration and result types (directive section 80)."""

from __future__ import annotations

from dataclasses import dataclass, field

from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.risk.governor import RiskLimits
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.simulation.types import EvidenceOrigin

# Directive section 20's exact initial research defaults -- starting
# points for validation, never claimed optimal or profitable, and never
# silently diverging from position_management.adaptive_exit's own
# defaults (which this module reuses directly rather than redeclaring).
DEFAULT_RISK_PER_TRADE_PCT = 0.25

# Directive section 32's hard ceilings, identical to config.loader.RiskConfig's
# defaults. A simulation enforces the SAME policy the live gates do: it
# must stop opening trades exactly where DEMO would be blocked.
DEFAULT_RISK_LIMITS = RiskLimits(
    risk_per_trade_pct=0.25, max_total_open_risk_pct=0.75, max_daily_loss_pct=2.00,
    max_drawdown_pct=5.00, max_open_positions=2, max_positions_per_symbol=1,
)

# Causal execution references: WHEN, relative to the decision, a simulated
# fill happened. A decision taken at a bar's close can only ever fill on a
# LATER bar (NEXT_BAR_OPEN); only standing broker-side orders (SL/TP)
# trigger inside a bar.
FILL_NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
FILL_STOP_TRIGGER = "STOP_TRIGGER"
FILL_TARGET_TRIGGER = "TARGET_TRIGGER"
FILL_RANGE_END_CLOSE = "RANGE_END_CLOSE"

# Advisory evidence the backtest/PAPER engine never consults. Recorded
# explicitly so provenance never implies an ML/RAG/OKF input that wasn't
# there.
NOT_CONSULTED = "NOT_CONSULTED"


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 10_000.0
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT
    min_net_edge_price: float = 0.0
    min_raw_confidence: float = 0.0
    feature_lookback: int = 20
    regime_min_confirmations: int = 2
    fill_assumptions: FillAssumptions = field(
        default_factory=lambda: FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0)
    )
    adaptive_exit_params: AdaptiveExitParams = field(default_factory=AdaptiveExitParams)
    uncertainty_margin_pct: float = 0.10
    # Directive section 81: apply only if the caller actually supplies
    # point-in-time news-block windows; otherwise the honest limitation
    # is recorded in the result rather than silently ignored.
    news_windows: tuple[tuple[int, int], ...] = ()
    risk_limits: RiskLimits = DEFAULT_RISK_LIMITS
    # A signal decided at a bar's close is only valid for the bar that
    # immediately follows. If the next bar opens later than this (weekend,
    # session break, data gap), the pending entry is dropped as stale.
    # None = two bars of the run's resolution.
    max_entry_fill_delay_seconds: int | None = None
    # Broker server-clock rule whose midnight is the swap rollover
    # (gateway/server_time.py). "UTC" keeps research runs on UTC days; PAPER
    # passes the configured `[mt5] server_time_rule`.
    server_time_rule: str = "UTC"
    # Where expected edge comes from (costs/edge_evidence.py, issue #6).
    # "NONE" = no validated evidence: the selector and the fill-bar
    # revalidation reject every candidate, so the run is FLAT.
    # "LEGACY_V1_RAW_SCORE" reproduces the frozen V1 formula
    # (raw_confidence read as p, configured target/stop as payoff) for
    # research replay only; it is never executable.
    edge_model: str = "NONE"
    # Optional provider object overriding `edge_model` (a calibrated model, or
    # a test fixture); its `model_id` enters the config fingerprint. PAPER
    # passes the runtime's vetted provider here.
    edge_provider: object | None = None
    # Config `strategies.entry_suspended`: active strategies whose signals are
    # evaluated but never selected for an entry (the same rule DEMO applies).
    suspended_strategy_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        from adaptive_scalper.costs.edge_evidence import EDGE_MODELS
        from adaptive_scalper.gateway.server_time import validate_rule

        validate_rule(self.server_time_rule)
        if self.edge_model not in EDGE_MODELS:
            raise ValueError(f"edge_model must be one of {EDGE_MODELS}, got {self.edge_model!r}")
        if self.initial_equity <= 0:
            raise ValueError(f"initial_equity must be positive, got {self.initial_equity!r}")
        if self.risk_per_trade_pct <= 0:
            raise ValueError(f"risk_per_trade_pct must be positive, got {self.risk_per_trade_pct!r}")
        if self.risk_per_trade_pct > self.risk_limits.risk_per_trade_pct:
            raise ValueError(
                f"risk_per_trade_pct {self.risk_per_trade_pct} exceeds the hard ceiling "
                f"{self.risk_limits.risk_per_trade_pct} -- no research config may override it"
            )
        if self.feature_lookback < 2:
            raise ValueError(f"feature_lookback must be >= 2, got {self.feature_lookback!r}")
        if self.max_entry_fill_delay_seconds is not None and self.max_entry_fill_delay_seconds <= 0:
            raise ValueError("max_entry_fill_delay_seconds must be positive when set")


@dataclass(frozen=True)
class SimulatedTrade:
    strategy_key: str
    direction: str
    entry_time_utc: int
    entry_price: float
    volume: float
    initial_monetary_risk: float
    entry_regime: str
    exit_time_utc: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    exit_regime: str | None = None
    realized_r: float | None = None
    realized_pnl: float | None = None      # NET: execution-price P/L minus commission, swap and fee
    total_cost: float = 0.0                # every friction component below, summed
    # The CAUSAL feature vector the strategy actually used to decide this
    # entry (directive section 64: entry model training input) -- captured
    # from the SAME bar-close features `select_proposal()` was fed, never
    # recomputed after the fact from later data. `None` when the caller
    # didn't request feature capture (e.g. a lightweight run); a `None`
    # value for one of the vector's fields means that field itself was
    # unprovable at that point (insufficient lookback, etc.) -- honest
    # N/A, never a fabricated number.
    entry_features: dict[str, float | None] | None = None
    entry_raw_confidence: float | None = None
    # Provenance and causal timing (directive section 82). Timestamps name
    # the bar whose CLOSE produced the decision.
    strategy_version: int | None = None
    signal_time_utc: int | None = None
    entry_fill_reference: str | None = None
    exit_fill_reference: str | None = None
    exit_decision_time_utc: int | None = None
    # Money, account currency. Spread/slippage are measured against mid
    # and are already inside entry_price/exit_price; commission/swap/fee
    # are deducted separately. gross_pnl = realized_pnl + total_cost =
    # mid-to-mid P/L.
    entry_spread_cost: float = 0.0
    entry_slippage_cost: float = 0.0
    exit_spread_cost: float = 0.0
    exit_slippage_cost: float = 0.0
    commission_cost: float = 0.0
    swap_cost: float = 0.0
    fee_cost: float = 0.0
    gross_pnl: float | None = None
    peak_r: float | None = None
    origin: EvidenceOrigin = EvidenceOrigin.BACKTEST
    fill_model_version: str | None = None
    cost_provenance: str | None = None
    config_fingerprint: str | None = None
    entry_evidence: dict | None = None

    @property
    def is_closed(self) -> bool:
        return self.exit_time_utc is not None


@dataclass(frozen=True)
class BacktestMetrics:
    trade_count: int
    closed_trade_count: int
    gross_pnl: float
    net_pnl: float
    total_cost: float
    win_rate: float | None
    profit_factor: float | None
    avg_r: float | None
    max_drawdown: float
    final_equity: float


@dataclass(frozen=True)
class RegimeTrackerState:
    """Resumable `regimes.classifier.RegimeTracker` hysteresis state.
    Without resuming this too, an incremental (PAPER) caller restarting
    the tracker from `UNKNOWN` every cycle would genuinely diverge from
    what a continuously-running tracker would decide -- directive section
    13's "do not flip on one noisy bar" guarantee must hold across
    cycles, not just within one bounded call."""

    confirmed: str
    candidate: str | None
    candidate_count: int


@dataclass(frozen=True)
class RiskState:
    """What the daily-loss and drawdown ceilings need across calls: the
    highest equity ever reached, and the realized P/L of the current UTC
    day. Resumed by PAPER exactly like the regime tracker -- a restart
    must not forget today's losses or the drawdown peak."""

    peak_equity: float
    day_utc: int                 # epoch_seconds // 86400 of the current UTC day
    day_realized_pnl: float


@dataclass(frozen=True)
class OpenPositionState:
    """Resumable still-open-trade state (directive section 132: PAPER
    mode is an ONGOING process, not a bounded historical range, so a
    position open at the end of one `run_backtest()` call must be
    resumable in the NEXT call rather than force-closed just because
    that call's bar window ended). Carries everything `backtest.engine
    ._OpenTrade` holds; a caller passes this back in as
    `run_backtest(..., resume_open_position=...)` to continue managing
    the SAME position rather than starting flat."""

    strategy_key: str
    direction: str
    entry_time_utc: int
    entry_price: float
    volume: float
    initial_monetary_risk: float
    entry_regime: str
    stop_price: float
    target_price: float | None
    initial_stop_distance_price: float
    # Entry-side friction (half spread + slippage) in money, already
    # embedded in `entry_price`; exit friction/commission/swap are added
    # only when the trade closes.
    total_cost: float
    entry_features: dict[str, float | None] | None = None
    entry_raw_confidence: float | None = None
    # Monotonic running maximum of current_r, starting at 0.0 -- the same
    # contract as live `position_management_state.peak_r`.
    peak_r: float = 0.0
    # A FULL_CLOSE decided at the last processed bar's close; it fills at
    # the NEXT bar's open, which may belong to the next PAPER cycle.
    pending_exit_reason: str | None = None
    pending_exit_decision_time_utc: int | None = None
    peak_r_time_utc: int | None = None
    last_current_r: float | None = None
    strategy_version: int | None = None
    signal_time_utc: int | None = None
    entry_spread_cost: float = 0.0
    entry_slippage_cost: float = 0.0
    entry_evidence: dict | None = None


@dataclass(frozen=True)
class PendingEntryState:
    """A selected entry signal decided at a bar's close that has not yet
    filled -- its earliest causal fill is the NEXT bar's open, which may
    arrive in the next PAPER cycle. Carries everything needed to
    RE-VALIDATE it immediately before that fill (news, cost, expected
    edge, risk) rather than treating the old decision as a permanent
    authorization."""

    strategy_key: str
    direction: str
    stop_distance: float
    target_distance: float
    regime: str
    raw_confidence: float
    signal_time_utc: int
    entry_features: dict[str, float | None] | None = None
    strategy_version: int | None = None
    canonical_symbol: str | None = None
    entry_method: str | None = None
    expected_duration_seconds: int | None = None
    estimated_cost_price: float | None = None
    expected_net_edge_price: float | None = None
    fingerprint: str | None = None


# Reasons a pending entry was dropped at fill time instead of filling.
REJECT_STALE_SIGNAL = "STALE_SIGNAL"
REJECT_NEWS = "BLOCK_NEWS"
REJECT_COST = "BLOCK_COST"
REJECT_EXPECTED_EDGE = "BLOCK_EXPECTED_EDGE"
REJECT_EDGE_UNVALIDATED = "BLOCK_EDGE_UNVALIDATED"
REJECT_SIZING = "BLOCK_RISK_SIZING"
REJECT_RISK = "BLOCK_RISK"
REJECT_PORTFOLIO_RISK = "BLOCK_PORTFOLIO_RISK"
REJECT_CORRELATION = "BLOCK_CORRELATION"


@dataclass(frozen=True)
class CandidateRecord:
    """One strategy signal the selector saw at a bar close, and what it did
    with it. Everything here was knowable at that close (no outcome field):
    the selector study joins outcomes on separately, from independent
    single-strategy sessions over the same bars."""

    bar_time_utc: int
    strategy_key: str
    direction: str
    raw_confidence: float
    stop_distance: float
    target_distance: float
    estimated_cost_price: float | None
    expected_net_edge_price: float | None
    selected: bool
    rejected: bool
    rejection_reason: str | None  # filter reason, or LOST_TO_HIGHER_EDGE for a qualifying loser
    regime: str


LOST_TO_HIGHER_EDGE = "lost_to_higher_expected_net_edge"


@dataclass(frozen=True)
class EntryRejection:
    signal_time_utc: int
    attempted_fill_time_utc: int
    strategy_key: str
    direction: str
    reason_code: str
    detail: str


@dataclass(frozen=True)
class BacktestResult:
    canonical_symbol: str
    resolution: str
    dataset_id: str
    range_start_utc: int
    range_end_utc: int
    trades: tuple[SimulatedTrade, ...]
    metrics: BacktestMetrics
    equity_curve: tuple[tuple[int, float], ...]  # (bar_time, equity) after each closed trade
    origin: EvidenceOrigin = EvidenceOrigin.BACKTEST
    news_limitation_note: str | None = None
    config: BacktestConfig | None = None
    # Set only when the caller passed `force_close_at_range_end=False` AND
    # a trade was still open at the final bar -- see OpenPositionState.
    open_position: OpenPositionState | None = None
    # Always populated: the regime tracker's hysteresis state at the end
    # of this run -- an incremental caller passes it back in as
    # `run_backtest(..., resume_regime_tracker=...)` next call.
    final_regime_tracker_state: RegimeTrackerState | None = None
    # Set only when `force_close_at_range_end=False` and a signal was
    # selected on the final bar -- pass back as `resume_pending_entry`.
    pending_entry: PendingEntryState | None = None
    entry_rejections: tuple[EntryRejection, ...] = ()
    # Always populated; pass back as `resume_risk_state`.
    final_risk_state: RiskState | None = None
    # Scans skipped because a daily-loss or drawdown ceiling was reached.
    risk_halted_scans: int = 0
    config_fingerprint: str | None = None
    fill_model_version: str | None = None
    cost_provenance: str | None = None


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    range_start_utc: int
    range_end_utc: int
    result: BacktestResult


# `run_walk_forward()` evaluates ONE fixed configuration across sequential
# time folds -- nothing is re-fit between folds. That is a stability check,
# not ML walk-forward validation (train -> purge -> validate -> retrain),
# which lives in `learning/model_walk_forward.py`.
SEQUENTIAL_FIXED_CONFIG_EVALUATION = "SEQUENTIAL_FIXED_CONFIG_EVALUATION"


@dataclass(frozen=True)
class WalkForwardResult:
    canonical_symbol: str
    resolution: str
    folds: tuple[WalkForwardFold, ...]
    # Metrics pooled across every fold's trades -- "does the strategy set
    # hold up walking forward through time", not just one lucky window.
    aggregate_metrics: BacktestMetrics
    embargo_bars: int
    evaluation_kind: str = SEQUENTIAL_FIXED_CONFIG_EVALUATION


@dataclass(frozen=True)
class DistributionStats:
    mean: float
    median: float
    p5: float
    p95: float
    minimum: float
    maximum: float


TRADE_ORDER_PATH_STRESS = "TRADE_ORDER_PATH_STRESS"


@dataclass(frozen=True)
class PathStressResult:
    """Trade-order path stress: the SAME realized P/Ls replayed in random
    orders. Terminal equity is identical in every permutation (a sum does
    not depend on order), so it is reported as one number, never as a
    distribution; only path-dependent quantities (drawdown, ruin) vary."""

    method: str
    n_simulations: int
    seed: int
    initial_equity: float
    terminal_equity: float
    max_drawdown: DistributionStats
    probability_of_ruin: float
    ruin_equity_fraction: float
    trade_count: int
