"""AI desk configuration: `config/ai_desk.toml`, separate from the trading
config on purpose (adding a section to the trading `AppConfig` would change
the PAPER session fingerprint and could halt a running session).

Defaults are the safe state: the research service is DISABLED, the provider
is `deterministic` (no network, no cost), outbound categories are empty and
paid calls need an explicit provider, model, API-key environment variable
name and a positive budget. Secrets are never stored here -- only the NAME of
the environment variable that holds the key.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_AI_DESK_CONFIG_PATH = "config/ai_desk.toml"

PROVIDER_KINDS = ("deterministic", "anthropic", "grok", "openai_compatible")
# Categories of data that may leave the machine when an external provider is
# configured. Account identity, balances and broker identifiers never leave.
EXTERNAL_DATA_CATEGORIES = ("market_features", "strategy_statistics", "news_calendar", "risk_summary")
AGENT_KEYS = ("market_analyst", "strategy_analyst", "news_analyst", "risk_analyst", "skeptic",
              "learning_analyst", "coordinator")


class AiDeskConfigError(Exception):
    pass


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "deterministic"
    kind: str = "deterministic"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""            # NAME of the environment variable, never the key
    timeout_seconds: float = 20.0
    max_retries: int = 2
    backoff_seconds: float = 1.0
    max_concurrent_requests: int = 1
    min_seconds_between_requests: float = 1.0
    input_cost_per_1k_tokens_usd: float = 0.0
    output_cost_per_1k_tokens_usd: float = 0.0
    max_output_tokens: int = 600
    circuit_breaker_failures: int = 3
    circuit_breaker_cooldown_seconds: float = 600.0

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in PROVIDER_KINDS:
            raise ValueError(f"provider kind {v!r} not in {PROVIDER_KINDS}")
        return v

    @field_validator("timeout_seconds", "backoff_seconds", "min_seconds_between_requests",
                     "circuit_breaker_cooldown_seconds", "input_cost_per_1k_tokens_usd",
                     "output_cost_per_1k_tokens_usd")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if not (v >= 0):
            raise ValueError("must be >= 0")
        return v

    @model_validator(mode="after")
    def _external_needs_details(self):
        if self.kind != "deterministic":
            if not self.model:
                raise ValueError(f"provider {self.name!r}: an external provider needs an explicit model")
            if not self.api_key_env and self.kind != "openai_compatible":
                raise ValueError(f"provider {self.name!r}: api_key_env (the variable NAME) is required")
            if not 0 <= self.max_retries <= 5:
                raise ValueError("max_retries must be within [0, 5]")
            if self.max_concurrent_requests < 1:
                raise ValueError("max_concurrent_requests must be >= 1")
        return self


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_token_budget: int = 0          # 0 = no external tokens at all
    daily_cost_budget_usd: float = 0.0   # 0 = no paid calls at all


class IntervalConfig(BaseModel):
    """Minimum seconds between two runs of the same agent (rate limiting on
    top of event deduplication). Agents never run per tick."""

    model_config = ConfigDict(extra="forbid")

    market_analyst: float = 300.0
    strategy_analyst: float = 3600.0
    news_analyst: float = 900.0
    risk_analyst: float = 120.0
    learning_analyst: float = 3600.0
    scheduled_report: float = 3600.0


class AiDeskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    journal_path: str = "data/ai_desk.sqlite3"
    trading_config_path: str = "config/default.toml"
    symbols: list[str] = Field(default_factory=lambda: ["XAUUSD", "BTCUSD", "GBPJPY"])
    market_resolution: str = "M5"
    poll_seconds: float = 15.0
    agent_timeout_seconds: float = 60.0
    narrative_budget_seconds: float = 120.0  # wall-clock cap on ALL model calls in one cycle
    stale_after_seconds: int = 1800          # market evidence older than this at as-of is STALE
    runtime_state_stale_seconds: int = 120   # a runtime heartbeat older than this = runtime not live
    retention_days: int = 90
    cache_ttl_seconds: int = 3600
    provider: str = "deterministic"           # name of the primary provider
    fallback_providers: list[str] = Field(default_factory=list)
    llm_agents: list[str] = Field(default_factory=lambda: ["coordinator"])
    allowed_external_data: list[str] = Field(default_factory=list)
    providers: list[ProviderConfig] = Field(default_factory=lambda: [ProviderConfig()])
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    intervals: IntervalConfig = Field(default_factory=IntervalConfig)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS

        bad = [s for s in v if s not in ALLOWED_CANONICAL_SYMBOLS]
        if bad:
            raise ValueError(f"symbols {bad} are outside the allowed universe")
        return v

    @field_validator("allowed_external_data")
    @classmethod
    def _categories(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in EXTERNAL_DATA_CATEGORIES]
        if bad:
            raise ValueError(f"unknown external data categories {bad}; allowed {EXTERNAL_DATA_CATEGORIES}")
        return v

    @field_validator("llm_agents")
    @classmethod
    def _agents(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if a not in AGENT_KEYS]
        if bad:
            raise ValueError(f"unknown agents {bad}")
        return v

    @model_validator(mode="after")
    def _providers_resolve(self):
        names = [p.name for p in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("provider names must be unique")
        if "deterministic" not in names:
            self.providers.append(ProviderConfig())
            names.append("deterministic")
        for n in [self.provider, *self.fallback_providers]:
            if n not in names:
                raise ValueError(f"provider {n!r} is not defined in [[providers]]")
        if self.retention_days < 1:
            raise ValueError("retention_days must be >= 1")
        return self

    def provider_config(self, name: str) -> ProviderConfig:
        for p in self.providers:
            if p.name == name:
                return p
        raise AiDeskConfigError(f"unknown provider {name!r}")

    def provider_chain(self) -> list[ProviderConfig]:
        return [self.provider_config(n) for n in [self.provider, *self.fallback_providers]]

    def external_calls_permitted(self) -> bool:
        """External (possibly paid) calls need EVERY explicit opt-in: a
        non-deterministic provider in the chain, at least one allowed data
        category and a positive token budget."""
        external = any(p.kind != "deterministic" for p in self.provider_chain())
        return external and bool(self.allowed_external_data) and self.budget.daily_token_budget > 0

    def fingerprint(self) -> str:
        """Stable hash of the effective configuration (no secrets live here)."""
        payload = json.dumps(self.model_dump(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_ai_desk_config(path: str | Path = DEFAULT_AI_DESK_CONFIG_PATH) -> AiDeskConfig:
    """Missing file = all defaults (disabled, deterministic)."""
    p = Path(path)
    if not p.exists():
        return AiDeskConfig()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        return AiDeskConfig.model_validate(data)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise AiDeskConfigError(f"{p}: {exc}") from exc
