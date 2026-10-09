"""Validated TOML configuration loading.

Per MASTER_BUILD_DIRECTIVE.md section 114: hard/non-learning configuration
sections (mode, allowed symbols, retired strategies, risk, news, execution,
DEMO gate, database) must fail startup on unsafe or invalid values rather
than silently falling back to a default. This module is that validation
boundary.

The tradable-symbol allow-list and retired-strategy list are NOT sourced
from the TOML file's own values in isolation — config may only ever
*narrow* the symbol set (a subset of ALLOWED_CANONICAL_SYMBOLS) and must
always retire at least RETIRED_STRATEGY_KEYS. It can never widen either.
"""

from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from adaptive_scalper.config.constants import (
    ALLOWED_CANONICAL_SYMBOLS,
    ALLOWED_MODES,
    RETIRED_STRATEGY_KEYS,
    check_hard_risk_ceilings,
)


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or unsafe.

    Unsafe configuration must fail startup (directive section 114) rather
    than be coerced into something "safe enough" — there is no safe
    default for, say, an unrecognized operating mode.
    """


class MarketConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: sorted(ALLOWED_CANONICAL_SYMBOLS))

    @field_validator("symbols")
    @classmethod
    def _symbols_are_a_subset_of_the_allowlist(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("market.symbols must not be empty")
        unknown = set(value) - ALLOWED_CANONICAL_SYMBOLS
        if unknown:
            raise ValueError(
                f"market.symbols contains symbols outside the canonical "
                f"allow-list {sorted(ALLOWED_CANONICAL_SYMBOLS)}: {sorted(unknown)}"
            )
        return value


class StrategiesConfig(BaseModel):
    retired: list[str] = Field(default_factory=lambda: sorted(RETIRED_STRATEGY_KEYS))

    @field_validator("retired")
    @classmethod
    def _retired_list_must_cover_the_permanent_set(cls, value: list[str]) -> list[str]:
        missing = RETIRED_STRATEGY_KEYS - set(value)
        if missing:
            raise ValueError(
                f"strategies.retired is missing permanently-retired keys "
                f"{sorted(missing)}; config may add to this list but may "
                f"never remove from it"
            )
        return value

    # Active strategies that may still SIGNAL (journaled as evidence) but may
    # never open a position. Narrow-only: a key must be an active strategy, so
    # this list can never add, rename or revive anything.
    entry_suspended: list[str] = Field(default_factory=list)

    @field_validator("entry_suspended")
    @classmethod
    def _suspended_keys_must_be_active(cls, value: list[str]) -> list[str]:
        from adaptive_scalper.strategies import build_active_registry

        active = build_active_registry().active_keys()
        unknown = sorted(set(value) - active)
        if unknown:
            raise ValueError(f"strategies.entry_suspended names non-active strategies {unknown}; active: {sorted(active)}")
        if len(set(value)) != len(value):
            raise ValueError(f"strategies.entry_suspended has duplicate keys: {value}")
        return value


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = 0.25
    max_total_open_risk_pct: float = 0.75
    max_daily_loss_pct: float = 2.00
    max_drawdown_pct: float = 5.00
    max_open_positions: int = 2
    max_positions_per_symbol: int = 1

    @field_validator("risk_per_trade_pct", "max_total_open_risk_pct", "max_daily_loss_pct", "max_drawdown_pct",
                     "max_open_positions", "max_positions_per_symbol")
    @classmethod
    def _within_hard_ceiling(cls, value, info):
        check_hard_risk_ceilings(**{info.field_name: value})
        return value

    @model_validator(mode="after")
    def _per_trade_cannot_exceed_total(self) -> "RiskConfig":
        if self.risk_per_trade_pct > self.max_total_open_risk_pct:
            raise ValueError(
                "risk_per_trade_pct cannot exceed max_total_open_risk_pct "
                f"({self.risk_per_trade_pct} > {self.max_total_open_risk_pct})"
            )
        return self


class NewsConfig(BaseModel):
    primary_provider: str = "financecalendar"
    secondary_provider: str = "forexfactory"
    pre_high_impact_minutes: int = 15
    post_high_impact_minutes: int = 30
    fail_closed: bool = True

    @field_validator("fail_closed")
    @classmethod
    def _must_fail_closed(cls, value: bool) -> bool:
        # Directive section 43/114: calendar outage must block new entries,
        # never be silently interpreted as "no news". This is not a
        # user-tunable safety knob.
        if not value:
            raise ValueError(
                "news.fail_closed must be true — a calendar outage must "
                "block new entries, never be treated as 'no events'"
            )
        return value

    @field_validator("pre_high_impact_minutes", "post_high_impact_minutes")
    @classmethod
    def _non_negative(cls, value: int, info) -> int:
        if value < 0:
            raise ValueError(f"{info.field_name} must be >= 0, got {value}")
        return value


class EntryWindowConfig(BaseModel):
    """Operator entry-hours policy: NEW entries are allowed only while the
    UTC hour is in [start_hour_utc, end_hour_utc). Disabled by default
    (directive section 73: no hard-coded session claims); when enabled it can
    only block entries, never allow one another gate blocked. Exits and
    position management are never restricted by it."""
    enabled: bool = False
    start_hour_utc: int = 0
    end_hour_utc: int = 24

    @model_validator(mode="after")
    def _valid_window(self) -> "EntryWindowConfig":
        if not (0 <= self.start_hour_utc < self.end_hour_utc <= 24):
            raise ValueError(
                f"entry_window needs 0 <= start_hour_utc < end_hour_utc <= 24, got "
                f"[{self.start_hour_utc}, {self.end_hour_utc})"
            )
        return self

    def hours(self) -> tuple[int, int] | None:
        return (self.start_hour_utc, self.end_hour_utc) if self.enabled else None


class HardStopConfig(BaseModel):
    """Hard cut-loss backstop: an open position whose adverse move from its
    broker fill price exceeds `max_adverse_move_pct[SYMBOL]` percent (on the
    price it would close at: bid for a BUY, ask for a SELL) is closed at
    market through the safe close service, independent of its broker SL.
    A symbol with no entry has no backstop (the code default)."""
    max_adverse_move_pct: dict[str, float] = Field(default_factory=dict)

    @field_validator("max_adverse_move_pct")
    @classmethod
    def _valid(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = set(value) - ALLOWED_CANONICAL_SYMBOLS
        if unknown:
            raise ValueError(f"hard_stop.max_adverse_move_pct for symbols outside the allow-list: {sorted(unknown)}")
        for symbol, pct in value.items():
            if not (math.isfinite(pct) and 0.0 < pct < 100.0):
                raise ValueError(f"hard_stop.max_adverse_move_pct[{symbol}] must be in (0, 100), got {pct!r}")
        return value


class EntryRegimeConfig(BaseModel):
    """Operator directional-momentum gate: NEW entries only while Wilder
    ADX(adx_period) of the entry-resolution closed bars is strictly above
    `min_adx`. None (the code default) disables it. Blocks only; exits are
    never restricted. Not a validated edge filter (operator policy)."""
    min_adx: float | None = None
    adx_period: int = 14

    @field_validator("min_adx")
    @classmethod
    def _valid_threshold(cls, value: float | None) -> float | None:
        if value is not None and not (math.isfinite(value) and 0.0 <= value < 100.0):
            raise ValueError(f"entry_regime.min_adx must be finite and in [0, 100), got {value!r}")
        return value

    @field_validator("adx_period")
    @classmethod
    def _valid_period(cls, value: int) -> int:
        if value < 2:
            raise ValueError(f"entry_regime.adx_period must be >= 2, got {value}")
        return value


class MicrostructureConfig(BaseModel):
    """Observer-only microstructure tracking (no trading authority): a
    rolling VWAP with +/- `vwap_band_std` volume-weighted standard-deviation
    bands over the last `vwap_window_minutes` of closed `vwap_resolution`
    bars, published per cycle for `vwap_symbols`. Empty list (the code
    default) tracks nothing."""
    vwap_symbols: list[str] = Field(default_factory=list)
    vwap_resolution: str = "M1"
    vwap_window_minutes: int = 10
    vwap_band_std: float = 2.0
    # Per closed entry-resolution bar: candle ratios (features/candle.py), EMA(candle_ema_period)
    # and Wilder ADX(entry_regime.adx_period) of the closes -- observation only.
    candle_symbols: list[str] = Field(default_factory=list)
    candle_ema_period: int = 20

    @field_validator("candle_ema_period")
    @classmethod
    def _valid_ema_period(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"microstructure.candle_ema_period must be >= 1, got {value}")
        return value

    @field_validator("vwap_symbols", "candle_symbols")
    @classmethod
    def _allowed_symbols(cls, value: list[str]) -> list[str]:
        unknown = set(value) - ALLOWED_CANONICAL_SYMBOLS
        if unknown:
            raise ValueError(f"microstructure symbols outside the allow-list: {sorted(unknown)}")
        return value

    @field_validator("vwap_resolution")
    @classmethod
    def _supported_resolution(cls, value: str) -> str:
        from adaptive_scalper.history.resolutions import SUPPORTED_BAR_RESOLUTIONS
        if value not in SUPPORTED_BAR_RESOLUTIONS:
            raise ValueError(f"microstructure.vwap_resolution must be one of {SUPPORTED_BAR_RESOLUTIONS}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _window_and_bands(self) -> "MicrostructureConfig":
        from adaptive_scalper.history.resolutions import resolution_seconds
        if self.vwap_window_minutes * 60 < resolution_seconds(self.vwap_resolution):
            raise ValueError("microstructure.vwap_window_minutes is shorter than one vwap_resolution bar")
        if not (math.isfinite(self.vwap_band_std) and self.vwap_band_std > 0):
            raise ValueError(f"microstructure.vwap_band_std must be positive and finite, got {self.vwap_band_std!r}")
        return self


class DatabaseConfig(BaseModel):
    path: str = "data/adaptive_scalper.sqlite3"


class Mt5Config(BaseModel):
    # The broker server clock MT5 stamps every time with (BUG_BACKLOG #14,
    # gateway/server_time.py). "UTC" = no conversion. The runtime refuses to
    # start when a live quote proves the rule wrong.
    server_time_rule: str = "UTC"
    # ASN-010: with several MT5 installations on one computer, an unpinned
    # initialize() attaches to whichever terminal MetaTrader5 picks. When
    # set, the gateway initializes THIS terminal64.exe and refuses to
    # continue unless the attached terminal reports the same install folder.
    terminal_path: str | None = None

    @field_validator("server_time_rule")
    @classmethod
    def _known_rule(cls, value: str) -> str:
        from adaptive_scalper.gateway.server_time import validate_rule

        return validate_rule(value)

    @field_validator("terminal_path")
    @classmethod
    def _terminal_exe(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not value.strip().lower().endswith("terminal64.exe"):
            raise ValueError("terminal_path must be the full path to terminal64.exe")
        return value.strip()


_COST_PROVENANCES = ("UNVERIFIED_ASSUMPTION", "BROKER_SPEC_ESTIMATE", "BROKER_DEMO_CONFIRMED")


class SymbolCostConfig(BaseModel):
    """Per-symbol execution-cost evidence (directive section 34: never one
    generic cost for all markets). `None` means UNKNOWN, never zero: DEMO
    and PAPER both block new entries with BLOCK_COST while commission or
    slippage is unknown. Spread always comes from the live quote/bar.

    `slippage_price` is PER FILL (costs/model.py FILLS_PER_ROUND_TRIP).
    `swap_per_lot_per_day` is the conservative per-rollover cost; unknown
    (None) is only acceptable for decisions whose maximum hold cannot cross
    the broker's server-midnight rollover (costs/swap_horizon.py, issue #8)."""

    commission_per_lot_round_trip: float | None = None
    slippage_price: float | None = None
    swap_per_lot_per_day: float | None = None
    provenance: str = "UNVERIFIED_ASSUMPTION"
    # Hard cap on the live quote's spread (ask - bid, price units), enforced
    # by execution.service at both pre-send rounds. `None` = no cap known:
    # DEMO refuses new entries for the symbol (fail closed).
    max_spread_price: float | None = None

    @field_validator("commission_per_lot_round_trip", "slippage_price", "swap_per_lot_per_day")
    @classmethod
    def _non_negative(cls, value, info):
        if value is not None and value < 0:
            raise ValueError(f"{info.field_name} must be >= 0, got {value}")
        return value

    @field_validator("max_spread_price")
    @classmethod
    def _positive_finite_cap(cls, value):
        if value is not None and not (math.isfinite(value) and value > 0):
            raise ValueError(f"max_spread_price must be a positive finite number, got {value}")
        return value

    @field_validator("provenance")
    @classmethod
    def _known_provenance(cls, value: str) -> str:
        if value not in _COST_PROVENANCES:
            raise ValueError(f"provenance must be one of {_COST_PROVENANCES}, got {value!r}")
        return value

    @property
    def fully_known(self) -> bool:
        return self.commission_per_lot_round_trip is not None and self.slippage_price is not None


class RuntimeConfig(BaseModel):
    """Scheduler cadences and runtime knobs (directive section 16). None of
    these can loosen a safety gate; they only change how often things run."""

    entry_resolution: str = "M5"
    position_cycle_seconds: float = 1.0
    entry_cycle_seconds: float = 4.0
    heartbeat_seconds: float = 1.0
    news_refresh_seconds: int = 1200
    news_stale_after_seconds: int = 7200
    bar_history_count: int = 300
    correlation_min_samples: int = 30
    paper_initial_equity: float = 10_000.0
    # Part of every PAPER session key. A session refuses to resume under a
    # different configuration; change this tag to start fresh sessions
    # after deliberately changing strategy/risk/cost/exit settings.
    paper_session_tag: str = "v1"
    magic: int = 240924
    log_dir: str = "logs"

    @field_validator("entry_resolution")
    @classmethod
    def _supported_resolution(cls, value: str) -> str:
        from adaptive_scalper.history.resolutions import SUPPORTED_BAR_RESOLUTIONS
        if value not in SUPPORTED_BAR_RESOLUTIONS:
            raise ValueError(f"entry_resolution must be one of {SUPPORTED_BAR_RESOLUTIONS}, got {value!r}")
        return value

    @model_validator(mode="after")
    def _sane_cadences(self) -> "RuntimeConfig":
        if not (0.2 <= self.position_cycle_seconds <= 10):
            raise ValueError("position_cycle_seconds must be in [0.2, 10]")
        if not (1 <= self.entry_cycle_seconds <= 60):
            raise ValueError("entry_cycle_seconds must be in [1, 60]")
        if self.position_cycle_seconds > self.entry_cycle_seconds:
            raise ValueError("position reviews must run at least as often as entry scans (directive section 15)")
        if not (60 <= self.news_refresh_seconds <= 3600):
            raise ValueError("news_refresh_seconds must be in [60, 3600]")
        if self.news_stale_after_seconds < self.news_refresh_seconds:
            raise ValueError("news_stale_after_seconds must be >= news_refresh_seconds")
        if self.bar_history_count < 60:
            raise ValueError("bar_history_count must be >= 60")
        if self.paper_initial_equity <= 0:
            raise ValueError("paper_initial_equity must be positive")
        return self


class AppConfig(BaseModel):
    mode: str = "PAPER"
    market: MarketConfig = Field(default_factory=MarketConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    entry_window: EntryWindowConfig = Field(default_factory=EntryWindowConfig)
    entry_regime: EntryRegimeConfig = Field(default_factory=EntryRegimeConfig)
    microstructure: MicrostructureConfig = Field(default_factory=MicrostructureConfig)
    hard_stop: HardStopConfig = Field(default_factory=HardStopConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    mt5: Mt5Config = Field(default_factory=Mt5Config)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    costs: dict[str, SymbolCostConfig] = Field(default_factory=dict)

    @field_validator("costs")
    @classmethod
    def _costs_only_for_allowed_symbols(cls, value: dict) -> dict:
        unknown = set(value) - ALLOWED_CANONICAL_SYMBOLS
        if unknown:
            raise ValueError(f"costs configured for symbols outside the allow-list: {sorted(unknown)}")
        return value

    def cost_for(self, canonical_symbol: str) -> SymbolCostConfig:
        return self.costs.get(canonical_symbol, SymbolCostConfig())

    @field_validator("mode")
    @classmethod
    def _mode_must_be_allowed(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in ALLOWED_MODES:
            raise ValueError(
                f"mode must be one of {sorted(ALLOWED_MODES)}, got {value!r}. "
                f"There is no real-money execution mode."
            )
        return normalized


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file is not valid TOML: {path}: {exc}") from exc


def load_config(path: str | Path) -> AppConfig:
    """Load and validate configuration from a TOML file.

    Raises ConfigError for anything malformed or unsafe. Never returns a
    partially-valid or coerced-safe config — callers must treat a raised
    ConfigError as a hard startup failure (directive section 114).
    """
    raw = _read_toml(Path(path))
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, primarily
        raise ConfigError(f"invalid configuration in {path}: {exc}") from exc
