"""Microstructure observer (config `[microstructure]`): publishes the rolling
VWAP and its standard-deviation bands, and the candle properties / EMA / ADX
of the newest closed entry bar, for the configured symbols once per
runtime cycle to `runtime_state["microstructure"]`.

OBSERVER ONLY: no strategy, selector, gate or sizing reads this state, and
any failure is journaled and swallowed -- it can never block or cause a
trade.
"""

from __future__ import annotations

import logging
import sqlite3

from adaptive_scalper.config.loader import AppConfig
from adaptive_scalper.features.adx import wilder_adx
from adaptive_scalper.features.candle import candle_properties
from adaptive_scalper.features.ema import ema_last
from adaptive_scalper.features.vwap import rolling_vwap_bands
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.history.resolutions import resolution_seconds
from adaptive_scalper.runtime.market_data import closed_bars
from adaptive_scalper.runtime.state import put_state, record_event

logger = logging.getLogger(__name__)

STATE_KEY = "microstructure"
UNAVAILABLE = "UNAVAILABLE"


def _position(close: float, bands) -> str:
    if close > bands.upper:
        return "ABOVE_UPPER"
    if close < bands.lower:
        return "BELOW_LOWER"
    return "ABOVE_VWAP" if close >= bands.vwap else "BELOW_VWAP"


def _candle_snapshot(gateway: Gateway, config: AppConfig, broker_symbol: str, now: int) -> dict:
    cfg = config.microstructure
    resolution = config.runtime.entry_resolution
    adx_period = config.entry_regime.adx_period
    count = max(cfg.candle_ema_period, 2 * adx_period + 1) + 50
    bars = closed_bars(gateway, broker_symbol, resolution, now_utc=now, count=count,
                       tick=gateway.symbol_info_tick(broker_symbol))
    if not bars:
        return {"status": UNAVAILABLE, "reason": "no closed bars"}
    props = candle_properties(bars[-1])
    ema = ema_last([b.close for b in bars], cfg.candle_ema_period)
    return {
        "resolution": resolution, "bar_time_utc": bars[-1].time, "close": bars[-1].close,
        "candle": props.as_dict() if props is not None else None,
        f"ema{cfg.candle_ema_period}": ema,
        "close_vs_ema": None if ema is None else ("ABOVE" if bars[-1].close > ema else
                                                  "BELOW" if bars[-1].close < ema else "AT"),
        f"adx{adx_period}": wilder_adx(bars, adx_period),
    }


def publish_vwap_bands(conn: sqlite3.Connection, gateway: Gateway, config: AppConfig,
                       symbols: dict[str, str], now: int) -> dict | None:
    """`symbols`: canonical -> broker name (the runtime's resolved map)."""
    cfg = config.microstructure
    tracked = [c for c in cfg.vwap_symbols if c in symbols]
    candle_tracked = [c for c in cfg.candle_symbols if c in symbols]
    if not tracked and not candle_tracked:
        return None
    out: dict[str, dict] = {}
    step = resolution_seconds(cfg.vwap_resolution)
    window = cfg.vwap_window_minutes * 60
    for canonical in tracked:
        try:
            tick = gateway.symbol_info_tick(symbols[canonical])
            bars = closed_bars(gateway, symbols[canonical], cfg.vwap_resolution, now_utc=now,
                               count=window // step + 1, tick=tick)
            bands = rolling_vwap_bands(bars, bar_seconds=step, window_seconds=window, band_std=cfg.vwap_band_std)
            if bands is None:
                out[canonical] = {"status": UNAVAILABLE, "reason": "no traded closed bars in the window"}
                continue
            out[canonical] = {**bands.as_dict(), "resolution": cfg.vwap_resolution,
                              "last_close": bars[-1].close, "position": _position(bars[-1].close, bands)}
        except Exception as exc:  # observation must never affect trading
            logger.warning("microstructure observer failed for %s: %s", canonical, exc)
            out[canonical] = {"status": UNAVAILABLE, "reason": f"{type(exc).__name__}: {exc}"}
            record_event(conn, "WARNING", "microstructure", "MICROSTRUCTURE_OBSERVER_FAILED",
                         f"{type(exc).__name__}: {exc}", canonical_symbol=canonical,
                         dedup_key=f"microstructure_failed:{canonical}", now_utc=now)
    candles: dict[str, dict] = {}
    for canonical in candle_tracked:
        try:
            candles[canonical] = _candle_snapshot(gateway, config, symbols[canonical], now)
        except Exception as exc:  # observation must never affect trading
            logger.warning("candle observer failed for %s: %s", canonical, exc)
            candles[canonical] = {"status": UNAVAILABLE, "reason": f"{type(exc).__name__}: {exc}"}
    snapshot = {"at": now, "symbols": out, "candles": candles}
    try:
        put_state(conn, STATE_KEY, snapshot, now_utc=now)
    except Exception:
        logger.exception("microstructure state write failed")
    return snapshot
