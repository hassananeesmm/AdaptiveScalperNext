"""Configuration fingerprint for simulated runs and PAPER sessions.

A PAPER session's persisted state (open position, pending entry, regime
tracker, risk state, equity) is only meaningful under the configuration
that produced it. Resuming it under a different strategy set, risk
policy, fill model or exit configuration would silently mix two
different systems' decisions into one track record. The fingerprint is
a SHA-256 over a canonical JSON description of everything that changes
decisions; `paper.engine` refuses to resume a session whose fingerprint
differs.

Deliberately EXCLUDED: `initial_equity` (only seeds a new session; a
running session carries its own equity) and `news_windows` (point-in-
time data, not configuration).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.features.bar_features import FEATURE_SCHEMA_VERSION
from adaptive_scalper.regimes.classifier import REGIME_VERSION
from adaptive_scalper.costs.edge_evidence import edge_model_id_of
from adaptive_scalper.simulation.fill_model import FILL_MODEL_VERSION

# Bumped whenever the meaning of RiskLimits/its enforcement changes.
RISK_POLICY_VERSION = 1
# Bumped whenever WHEN a simulated open trade is reviewed/exited changes.
# 2: holding time measured to the review bar's close (BUG_BACKLOG #13), so a
# PAPER session started under v1 timing halts instead of mixing semantics.
EXIT_REVIEW_TIMING_VERSION = 2
# Bar times are real UTC from schema 27 on (Mt5Gateway converts the broker
# server clock, BUG_BACKLOG #14). A PAPER session started on server-time bars
# halts instead of resuming with its bar cursor hours off.
BAR_TIME_BASIS = "UTC"


def describe_config(
    config: BacktestConfig, *, canonical_symbol: str, resolutions: tuple[str, ...],
    strategies: tuple[tuple[str, int], ...],
) -> dict:
    return {
        "canonical_symbol": canonical_symbol,
        "resolutions": sorted(resolutions),
        "strategies": sorted([key, version] for key, version in strategies),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "regime_version": REGIME_VERSION,
        "regime_min_confirmations": config.regime_min_confirmations,
        "feature_lookback": config.feature_lookback,
        "risk_policy_version": RISK_POLICY_VERSION,
        "risk_per_trade_pct": config.risk_per_trade_pct,
        "risk_limits": asdict(config.risk_limits),
        "fill_model_version": FILL_MODEL_VERSION,
        "fill_assumptions": asdict(config.fill_assumptions),
        "adaptive_exit": asdict(config.adaptive_exit_params),
        "exit_review_timing_version": EXIT_REVIEW_TIMING_VERSION,
        "bar_time_basis": BAR_TIME_BASIS,
        "min_net_edge_price": config.min_net_edge_price,
        "min_raw_confidence": config.min_raw_confidence,
        "uncertainty_margin_pct": config.uncertainty_margin_pct,
        "max_entry_fill_delay_seconds": config.max_entry_fill_delay_seconds,
        "swap_rollover_rule": config.server_time_rule,
        "edge_model": edge_model_id_of(config),
    }


def compute_config_fingerprint(
    config: BacktestConfig, *, canonical_symbol: str, resolutions: tuple[str, ...],
    strategies: tuple[tuple[str, int], ...],
) -> tuple[str, str]:
    """Returns `(sha256_hex, canonical_json)`."""
    canonical = json.dumps(
        describe_config(config, canonical_symbol=canonical_symbol, resolutions=resolutions, strategies=strategies),
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical
