"""Prospective SHADOW observer (forward-only evidence; migration 0032).

The H1-H9 historical strategy search is frozen and no strategy has a
validated net edge, so new evidence must be collected FORWARD, without
trading. For every closed decision bar this records every active strategy's
candidate -- including the ones the selector rejects or never evaluates
because of a global block -- with the causal features known at that bar's
close, and later resolves what the market actually did over fixed horizons.

Guarantees:
- No order path: this module imports no gateway, execution or broker code and
  is handed only data the caller already has (tests/test_shadow_observer.py
  checks the imports and that it never names order_send/order_check).
- No lookahead in candidate rows: only the decision bar's features, the
  decision-time cost estimate and the selector's disposition are stored.
- Outcomes only after their horizon elapsed: a (candidate, horizon) row is
  inserted once every bar it needs has CLOSED (`bar.time + bar_seconds <=
  now`), and then never changed (append-only triggers).
- Honest resolution: a horizon that is not a multiple of the bar length is
  UNSUPPORTED_RESOLUTION, never interpolated; missing bars are
  INSUFFICIENT_DATA.

Price convention: bar prices are mid prices (simulation/fill_model.py). The
entry reference is the open of the first bar at/after the decision time;
gross_r = signed (exit_mid - entry_mid) / stop_distance; net_r_estimated =
gross_r - estimated_round_trip_cost / stop_distance (spread counted once, in
the cost). Same-bar stop and target -> the stop is assumed first.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass

from adaptive_scalper.gateway.types import Bar

OBSERVER_VERSION = "shadow_observer/v1"
SHADOW_HORIZONS_SECONDS = (60, 180, 300, 600, 900, 1800)

SELECTED = "SELECTED"
LOST_TO_HIGHER_EDGE = "LOST_TO_HIGHER_EDGE"
REJECTED = "REJECTED"
NOT_EVALUATED = "NOT_EVALUATED"

RESOLVED = "RESOLVED"
UNSUPPORTED_RESOLUTION = "UNSUPPORTED_RESOLUTION"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class ShadowCandidate:
    canonical_symbol: str
    resolution: str
    bar_seconds: int
    decision_bar_time_utc: int
    strategy_key: str
    strategy_version: int
    direction: str
    raw_score: float
    stop_distance: float
    target_distance: float
    expected_duration_seconds: int | None
    raw_regime: str | None
    confirmed_regime: str | None
    regime_confidence: float | None
    session: str | None
    news_status: str | None
    news_detail: str | None
    spread_points: float | None
    spread_percentile: float | None
    atr: float | None
    realized_volatility: float | None
    movement_to_cost: float | None
    estimated_round_trip_cost_price: float | None
    cost_horizon: str | None
    cost_provenance: str | None
    edge_model: str
    scheduler_lag_seconds: float | None
    selector_disposition: str
    rejection_reason: str | None
    model_observer_score: float | None
    features: dict
    final_permission_result: str = "NOT_REACHED"
    chain_key: str = ""

    @property
    def decision_time_utc(self) -> int:
        return self.decision_bar_time_utc + self.bar_seconds

    @property
    def key(self) -> str:
        return (f"{self.canonical_symbol}:{self.resolution}:{self.decision_bar_time_utc}:"
                f"{self.strategy_key}:{self.direction}")


def _finite_or_none(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def record_candidates(conn: sqlite3.Connection, candidates: list[ShadowCandidate], *, mode: str,
                      now_utc: int) -> int:
    """Insert each candidate once (idempotent on its key). Returns rows written."""
    written = 0
    for c in candidates:
        if c.direction not in ("BUY", "SELL") or not (c.stop_distance > 0 and c.target_distance > 0):
            continue
        features = {k: _finite_or_none(v) for k, v in sorted(c.features.items())}
        cur = conn.execute(
            "INSERT OR IGNORE INTO shadow_candidates (candidate_key, observer_version, observed_at_utc, mode, "
            "canonical_symbol, resolution, bar_seconds, decision_bar_time_utc, decision_time_utc, strategy_key, "
            "strategy_version, direction, raw_score, raw_regime, confirmed_regime, regime_confidence, session, "
            "news_status, news_detail, spread_points, spread_percentile, atr, realized_volatility, movement_to_cost, "
            "stop_distance, target_distance, expected_duration_seconds, estimated_round_trip_cost_price, cost_horizon, "
            "cost_provenance, edge_model, scheduler_lag_seconds, selector_disposition, rejection_reason, "
            "final_permission_result, chain_key, model_observer_score, features_json) VALUES ("
            + ",".join("?" * 38) + ")",
            (c.key, OBSERVER_VERSION, now_utc, mode, c.canonical_symbol, c.resolution, c.bar_seconds,
             c.decision_bar_time_utc, c.decision_time_utc, c.strategy_key, c.strategy_version, c.direction,
             float(c.raw_score), c.raw_regime, c.confirmed_regime, _finite_or_none(c.regime_confidence), c.session,
             c.news_status, c.news_detail, _finite_or_none(c.spread_points), _finite_or_none(c.spread_percentile),
             _finite_or_none(c.atr), _finite_or_none(c.realized_volatility), _finite_or_none(c.movement_to_cost),
             c.stop_distance, c.target_distance, c.expected_duration_seconds,
             _finite_or_none(c.estimated_round_trip_cost_price), c.cost_horizon, c.cost_provenance, c.edge_model,
             _finite_or_none(c.scheduler_lag_seconds), c.selector_disposition, c.rejection_reason,
             c.final_permission_result, c.chain_key or c.key,
             _finite_or_none(c.model_observer_score), json.dumps(features, sort_keys=True)),
        )
        written += cur.rowcount
    conn.commit()
    return written


@dataclass(frozen=True)
class Outcome:
    status: str
    entry_time_utc: int | None = None
    entry_reference_price: float | None = None
    exit_reference_price: float | None = None
    gross_r: float | None = None
    net_r_estimated: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    time_to_mfe_seconds: int | None = None
    time_to_mae_seconds: int | None = None
    first_touch: str | None = None
    first_touch_seconds: int | None = None
    bars_used: int | None = None


def evaluate_outcome(*, direction: str, decision_time_utc: int, stop_distance: float, target_distance: float,
                     estimated_cost_price: float | None, bar_seconds: int, horizon_seconds: int,
                     bars: list[Bar]) -> Outcome:
    """Pure: the outcome of one candidate over one horizon from CLOSED bars."""
    if horizon_seconds % bar_seconds != 0:
        return Outcome(UNSUPPORTED_RESOLUTION)
    sign = 1.0 if direction == "BUY" else -1.0
    window = sorted((b for b in bars if b.time >= decision_time_utc), key=lambda b: b.time)
    if not window or window[0].time != decision_time_utc:
        return Outcome(INSUFFICIENT_DATA)          # the earliest causal fill bar is missing (gap)
    entry_time = window[0].time
    needed = horizon_seconds // bar_seconds
    span = [b for b in window if b.time < entry_time + horizon_seconds]
    if len(span) != needed or any(b.time != entry_time + i * bar_seconds for i, b in enumerate(span)):
        return Outcome(INSUFFICIENT_DATA)
    entry = span[0].open
    stop_level = entry - sign * stop_distance
    target_level = entry + sign * target_distance
    mfe = mae = 0.0
    t_mfe = t_mae = 0
    first, first_t = "NEITHER", None
    for b in span:
        fav = (b.high - entry) if sign > 0 else (entry - b.low)
        adv = (entry - b.low) if sign > 0 else (b.high - entry)
        offset = b.time - entry_time
        if fav > mfe:
            mfe, t_mfe = fav, offset
        if adv > mae:
            mae, t_mae = adv, offset
        if first == "NEITHER":
            stop_hit = (b.low <= stop_level) if sign > 0 else (b.high >= stop_level)
            target_hit = (b.high >= target_level) if sign > 0 else (b.low <= target_level)
            if stop_hit and target_hit:
                first, first_t = "BOTH_SAME_BAR_STOP_ASSUMED", offset
            elif stop_hit:
                first, first_t = "STOP", offset
            elif target_hit:
                first, first_t = "TARGET", offset
    exit_price = span[-1].close
    gross = sign * (exit_price - entry) / stop_distance
    net = None if estimated_cost_price is None else gross - estimated_cost_price / stop_distance
    return Outcome(RESOLVED, entry_time, entry, exit_price, gross, net, mfe / stop_distance, mae / stop_distance,
                   t_mfe, t_mae, first, first_t, len(span))


def resolve_due(conn: sqlite3.Connection, canonical_symbol: str, bars: list[Bar], *, now_utc: int,
                horizons: tuple[int, ...] = SHADOW_HORIZONS_SECONDS) -> int:
    """Insert outcomes whose horizon has fully elapsed in CLOSED bars.
    `bars` may contain the forming bar; only bars that have closed by
    `now_utc` are used. Returns rows written."""
    rows = conn.execute(
        "SELECT c.id, c.direction, c.decision_time_utc, c.stop_distance, c.target_distance, "
        "c.estimated_round_trip_cost_price, c.bar_seconds FROM shadow_candidates c "
        "WHERE c.canonical_symbol = ? AND (SELECT COUNT(*) FROM shadow_outcomes o WHERE o.candidate_id = c.id) < ? "
        "AND c.decision_time_utc <= ?",
        (canonical_symbol, len(horizons), now_utc),
    ).fetchall()
    written = 0
    for cid, direction, decision_time, stop, target, cost, bar_seconds in rows:
        closed = [b for b in bars if b.time + bar_seconds <= now_utc]
        done = {h for (h,) in conn.execute("SELECT horizon_seconds FROM shadow_outcomes WHERE candidate_id = ?", (cid,))}
        for h in horizons:
            if h in done:
                continue
            unsupported = h % bar_seconds != 0
            if not unsupported and decision_time + h > now_utc:
                continue                                   # horizon not elapsed yet
            outcome = evaluate_outcome(direction=direction, decision_time_utc=decision_time, stop_distance=stop,
                                       target_distance=target, estimated_cost_price=cost, bar_seconds=bar_seconds,
                                       horizon_seconds=h, bars=closed)
            if outcome.status == INSUFFICIENT_DATA and decision_time + h + 3 * bar_seconds > now_utc:
                continue                                   # bars may still arrive; decide later
            conn.execute(
                "INSERT OR IGNORE INTO shadow_outcomes (candidate_id, horizon_seconds, status, observer_version, "
                "resolved_at_utc, entry_time_utc, entry_reference_price, exit_reference_price, gross_r, "
                "net_r_estimated, mfe_r, mae_r, time_to_mfe_seconds, time_to_mae_seconds, first_touch, "
                "first_touch_seconds, bars_used) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, h, outcome.status, OBSERVER_VERSION, now_utc, outcome.entry_time_utc,
                 outcome.entry_reference_price, outcome.exit_reference_price, outcome.gross_r,
                 outcome.net_r_estimated, outcome.mfe_r, outcome.mae_r, outcome.time_to_mfe_seconds,
                 outcome.time_to_mae_seconds, outcome.first_touch, outcome.first_touch_seconds, outcome.bars_used),
            )
            written += 1
    conn.commit()
    return written
