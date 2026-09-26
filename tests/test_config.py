"""Tests for adaptive_scalper.config: hard safety constants and the
validated TOML config loader.

Note: literal-string tests asserting rejection of a config value equal to
the word the REAL_MONEY_PATTERNS regexes match (e.g. mode="LIVE") are
deliberately deferred to a follow-up — the .claude/hooks/guardrails.py
PreToolUse hook currently has no path exemption for tests/, so writing
that literal string into this file gets the *edit itself* blocked. The
mode validator is otherwise fully covered here via an arbitrary invalid
value, which exercises the same code path (anything outside
ALLOWED_MODES is rejected).
"""

import pytest
from pydantic import ValidationError

from adaptive_scalper.config.constants import (
    ALLOWED_CANONICAL_SYMBOLS,
    ALLOWED_MODES,
    RETIRED_STRATEGY_KEYS,
)
from adaptive_scalper.config.loader import AppConfig, ConfigError, load_config


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

def test_allowed_canonical_symbols_is_exactly_three():
    assert ALLOWED_CANONICAL_SYMBOLS == {"XAUUSD", "GBPJPY", "BTCUSD"}


def test_retired_strategy_keys_exact():
    assert RETIRED_STRATEGY_KEYS == {"failed_breakout_fade", "support_resistance_reaction"}


def test_allowed_modes_has_no_real_money_mode():
    assert ALLOWED_MODES == {"PAPER", "DEMO"}
    assert len(ALLOWED_MODES) == 2


def test_constants_are_frozensets_not_mutable_lists():
    assert isinstance(ALLOWED_CANONICAL_SYMBOLS, frozenset)
    assert isinstance(RETIRED_STRATEGY_KEYS, frozenset)
    assert isinstance(ALLOWED_MODES, frozenset)


# --------------------------------------------------------------------------
# AppConfig defaults
# --------------------------------------------------------------------------

def test_default_config_is_valid_and_safe():
    cfg = AppConfig()
    assert cfg.mode == "PAPER"
    assert set(cfg.market.symbols) == ALLOWED_CANONICAL_SYMBOLS
    assert RETIRED_STRATEGY_KEYS <= set(cfg.strategies.retired)
    assert cfg.news.fail_closed is True


def test_mode_is_case_normalized():
    cfg = AppConfig(mode="demo")
    assert cfg.mode == "DEMO"


def test_mode_rejects_any_value_outside_allowlist():
    with pytest.raises(ValidationError):
        AppConfig(mode="FOOBAR")


# --------------------------------------------------------------------------
# market.symbols — the executable-symbol firewall
# --------------------------------------------------------------------------

def test_symbols_reject_unknown_symbol():
    with pytest.raises(ValidationError):
        AppConfig(market={"symbols": ["EURUSD"]})


def test_symbols_reject_fourth_symbol_alongside_valid_ones():
    with pytest.raises(ValidationError):
        AppConfig(market={"symbols": ["XAUUSD", "GBPJPY", "BTCUSD", "USDJPY"]})


def test_symbols_reject_empty_list():
    with pytest.raises(ValidationError):
        AppConfig(market={"symbols": []})


def test_symbols_accept_a_narrower_subset():
    cfg = AppConfig(market={"symbols": ["XAUUSD"]})
    assert cfg.market.symbols == ["XAUUSD"]


# --------------------------------------------------------------------------
# strategies.retired — the retired-strategy firewall
# --------------------------------------------------------------------------

def test_retired_list_cannot_drop_a_permanently_retired_key():
    with pytest.raises(ValidationError):
        AppConfig(strategies={"retired": ["failed_breakout_fade"]})  # missing the other one


def test_retired_list_cannot_be_emptied():
    with pytest.raises(ValidationError):
        AppConfig(strategies={"retired": []})


def test_retired_list_can_be_extended():
    cfg = AppConfig(strategies={"retired": [
        "failed_breakout_fade", "support_resistance_reaction", "some_future_bad_strategy",
    ]})
    assert "some_future_bad_strategy" in cfg.strategies.retired


# --------------------------------------------------------------------------
# risk
# --------------------------------------------------------------------------

def test_risk_defaults_match_directive_starting_values():
    cfg = AppConfig()
    assert cfg.risk.risk_per_trade_pct == 0.25
    assert cfg.risk.max_total_open_risk_pct == 0.75
    assert cfg.risk.max_daily_loss_pct == 2.00
    assert cfg.risk.max_drawdown_pct == 5.00
    assert cfg.risk.max_open_positions == 2
    assert cfg.risk.max_positions_per_symbol == 1


def test_risk_rejects_zero_or_negative_percentages():
    with pytest.raises(ValidationError):
        AppConfig(risk={"risk_per_trade_pct": 0})
    with pytest.raises(ValidationError):
        AppConfig(risk={"max_daily_loss_pct": -1})


def test_risk_rejects_per_trade_exceeding_total_open_risk():
    # Both values within the hard ceilings, so only the per-trade <= total rule can fail.
    with pytest.raises(ValidationError, match="cannot exceed max_total_open_risk_pct"):
        AppConfig(risk={"risk_per_trade_pct": 0.25, "max_total_open_risk_pct": 0.20})


@pytest.mark.parametrize("field,above", [
    ("risk_per_trade_pct", 0.26), ("max_total_open_risk_pct", 0.76), ("max_daily_loss_pct", 2.01),
    ("max_drawdown_pct", 5.01), ("max_open_positions", 3), ("max_positions_per_symbol", 2),
])
def test_config_can_never_raise_a_hard_risk_ceiling(field, above):
    # Before the Windows validation fix the validator accepted up to 20 % per trade/day
    # and any number of positions.
    with pytest.raises(ValidationError, match="hard ceiling"):
        AppConfig(risk={field: above})


def test_config_may_lower_the_hard_risk_ceilings():
    cfg = AppConfig(risk={"risk_per_trade_pct": 0.10, "max_total_open_risk_pct": 0.30, "max_daily_loss_pct": 1.0,
                          "max_drawdown_pct": 3.0, "max_open_positions": 1})
    assert cfg.risk.risk_per_trade_pct == 0.10 and cfg.risk.max_open_positions == 1


def test_no_construction_path_can_build_risk_limits_above_the_ceilings():
    from adaptive_scalper.config.constants import HARD_RISK_CEILINGS
    from adaptive_scalper.risk.governor import RiskLimits

    assert HARD_RISK_CEILINGS == {"risk_per_trade_pct": 0.25, "max_total_open_risk_pct": 0.75,
                                  "max_daily_loss_pct": 2.00, "max_drawdown_pct": 5.00,
                                  "max_open_positions": 2, "max_positions_per_symbol": 1}
    RiskLimits(0.25, 0.75, 2.0, 5.0, 2, 1)
    for i, above in enumerate((0.3, 1.0, 3.0, 6.0, 3, 2)):
        values = [0.25, 0.75, 2.0, 5.0, 2, 1]
        values[i] = above
        with pytest.raises(ValueError, match="hard ceiling"):
            RiskLimits(*values)


def test_risk_rejects_zero_max_positions():
    with pytest.raises(ValidationError):
        AppConfig(risk={"max_open_positions": 0})


# --------------------------------------------------------------------------
# news — must fail closed
# --------------------------------------------------------------------------

def test_news_fail_closed_cannot_be_disabled():
    with pytest.raises(ValidationError):
        AppConfig(news={"fail_closed": False})


def test_news_rejects_negative_windows():
    with pytest.raises(ValidationError):
        AppConfig(news={"pre_high_impact_minutes": -5})


# --------------------------------------------------------------------------
# load_config — file-level behavior
# --------------------------------------------------------------------------

def test_load_config_reads_the_shipped_default_toml():
    cfg = load_config("config/default.toml")
    assert cfg.mode == "PAPER"
    # config/default.toml may *narrow* market.symbols to a non-empty subset of the
    # canonical allow-list (adaptive_scalper/config/loader.py's own documented
    # invariant) -- it is never required to list every allowed symbol.
    assert cfg.market.symbols
    assert set(cfg.market.symbols) <= ALLOWED_CANONICAL_SYMBOLS


def test_load_config_missing_file_raises_config_error():
    with pytest.raises(ConfigError):
        load_config("config/does_not_exist.toml")


def test_load_config_malformed_toml_raises_config_error(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not [ valid toml", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_load_config_unsafe_toml_raises_config_error(tmp_path):
    unsafe = tmp_path / "unsafe.toml"
    unsafe.write_text('mode = "PAPER"\n[market]\nsymbols = ["EURUSD"]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(unsafe)
