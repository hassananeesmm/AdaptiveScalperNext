"""Backtest configuration and result types (directive section 80)."""

from __future__ import annotations

from dataclasses import dataclass, field

from adaptive_scalper.position_management.adaptive_exit import AdaptiveExitParams
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.simulation.types import EvidenceOrigin

# Directive section 20's exact initial research defaults -- starting
# points for validation, never claimed optimal or profitable, and never
# silently diverging from position_management.adaptive_exit's own
# defaults (which this module reuses directly rather than redeclaring).
DEFAULT_RISK_PER_TRADE_PCT = 0.25


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

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError(f"initial_equity must be positive, got {self.initial_equity!r}")
        if self.risk_per_trade_pct <= 0:
            raise ValueError(f"risk_per_trade_pct must be positive, got {self.risk_per_trade_pct!r}")
        if self.feature_lookback < 2:
            raise ValueError(f"feature_lookback must be >= 2, got {self.feature_lookback!r}")


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
    realized_pnl: float | None = None
    total_cost: float = 0.0
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


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    range_start_utc: int
    range_end_utc: int
    result: BacktestResult


@dataclass(frozen=True)
class WalkForwardResult:
    canonical_symbol: str
    resolution: str
    folds: tuple[WalkForwardFold, ...]
    # Metrics pooled across every fold's trades -- "does the strategy set
    # hold up walking forward through time", not just one lucky window.
    aggregate_metrics: BacktestMetrics
    embargo_bars: int


@dataclass(frozen=True)
class DistributionStats:
    mean: float
    median: float
    p5: float
    p95: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class MonteCarloResult:
    n_simulations: int
    seed: int
    initial_equity: float
    final_equity: DistributionStats
    max_drawdown: DistributionStats
    probability_of_ruin: float
    ruin_equity_fraction: float
