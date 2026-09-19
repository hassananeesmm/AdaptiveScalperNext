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

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from adaptive_scalper.config.constants import (
    ALLOWED_CANONICAL_SYMBOLS,
    ALLOWED_MODES,
    RETIRED_STRATEGY_KEYS,
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


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = 0.25
    max_total_open_risk_pct: float = 0.75
    max_daily_loss_pct: float = 2.00
    max_drawdown_pct: float = 5.00
    max_open_positions: int = 2
    max_positions_per_symbol: int = 1

    @field_validator("risk_per_trade_pct", "max_total_open_risk_pct",
                      "max_daily_loss_pct", "max_drawdown_pct")
    @classmethod
    def _positive_and_bounded(cls, value: float, info) -> float:
        if not (0 < value <= 20):
            raise ValueError(f"{info.field_name} must be in (0, 20], got {value}")
        return value

    @field_validator("max_open_positions", "max_positions_per_symbol")
    @classmethod
    def _positive_int(cls, value: int, info) -> int:
        if value < 1:
            raise ValueError(f"{info.field_name} must be >= 1, got {value}")
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


class DatabaseConfig(BaseModel):
    path: str = "data/adaptive_scalper.sqlite3"


class AppConfig(BaseModel):
    mode: str = "PAPER"
    market: MarketConfig = Field(default_factory=MarketConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

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
