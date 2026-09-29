"""DEMO execution cost observations (completion directive Phase 7).

`record_entry_observation()` runs AFTER `submit_new_entry()` returns, once
per order that reached the broker: it stores the decision-time quote,
the request, the estimate, the broker's actual result (volume-weighted
fill price over the order's IN deals, adverse slippage, commission/fee,
retcode, fill type) and market context. `sweep_exit_costs()` runs off the
hot path and completes each row once its position has closed (exit
commission/fee and swap from the position's non-IN deals).
`summarize_observations()` turns the rows into per-symbol evidence an
OPERATOR can compare against `[costs.SYMBOL]` -- nothing here ever writes
configuration or relabels a cost as BROKER_DEMO_CONFIRMED by itself.

Every write is best-effort from the caller's point of view: the runtime
wraps it so a failure here can never affect order handling.
"""

from __future__ import annotations

import re
import sqlite3
import statistics
import time
from dataclasses import dataclass

from adaptive_scalper.costs.model import CostEstimate

SESSIONS = (
    (0, 7, "ASIA"), (7, 12, "LONDON"), (12, 16, "LONDON_NEW_YORK"), (16, 21, "NEW_YORK"), (21, 24, "LATE"),
)
MIN_SAMPLES_FOR_EVIDENCE = 30


def session_label(hour_utc: int) -> str:
    for start, end, label in SESSIONS:
        if start <= hour_utc < end:
            return label
    raise ValueError(f"hour_utc out of range: {hour_utc}")


def news_proximity(windows: tuple[tuple[int, int], ...], now_utc: int) -> tuple[int | None, int | None]:
    """(seconds until the next block window starts -- 0 while inside one,
    seconds since the last window ended)."""
    upcoming = [start - now_utc for start, end in windows if end > now_utc]
    inside = any(start <= now_utc < end for start, end in windows)
    past = [now_utc - end for start, end in windows if end <= now_utc]
    return (0 if inside else (min(upcoming) if upcoming else None)), (min(past) if past else None)


def record_entry_observation(
    conn: sqlite3.Connection, *, order_id: int, chain_key: str | None, canonical_symbol: str, broker_symbol: str,
    direction: str, outcome_status: str, decided_at_utc: int, tick, requested_price: float | None,
    requested_volume: float, estimate: CostEstimate | None, features: dict | None,
    news_windows: tuple[tuple[int, int], ...] = (), now_utc: int | None = None,
) -> int:
    now = now_utc if now_utc is not None else int(time.time())
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    deals = conn.execute(
        "SELECT price, volume, commission, fee, broker_position_id FROM deals WHERE order_id = ? AND entry_type = 'IN'",
        (order_id,),
    ).fetchall()
    filled = sum(d["volume"] for d in deals)
    fill_price = sum(d["price"] * d["volume"] for d in deals) / filled if filled > 0 else None
    sign = 1.0 if direction == "BUY" else -1.0
    slippage = sign * (fill_price - requested_price) if fill_price is not None and requested_price else None
    fill_type = "NONE" if filled <= 0 else ("FULL" if filled >= requested_volume - 1e-9 else "PARTIAL")
    features = features or {}
    hour = int(features["hour_of_day_utc"]) if features.get("hour_of_day_utc") is not None else (decided_at_utc // 3600) % 24
    to_news, since_news = news_proximity(news_windows, decided_at_utc)
    position_id = next((d["broker_position_id"] for d in deals if d["broker_position_id"]), None)
    if position_id is None and order is not None:
        position_id = order["broker_position_id"]

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO execution_cost_observations (
            order_id, chain_key, canonical_symbol, broker_symbol, direction, outcome_status, decided_at_utc,
            quote_time, quote_time_msc, bid, ask, spread_price, requested_price, requested_volume, filled_volume,
            fill_price, slippage_price, entry_commission, entry_fee, broker_retcode, broker_comment, fill_type,
            deal_count, estimated_spread_price, estimated_commission_price, estimated_slippage_price,
            estimated_total_price, session, hour_utc, atr, realized_volatility, spread_percentile,
            seconds_to_next_news, seconds_since_last_news, broker_position_id, recorded_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id, chain_key, canonical_symbol, broker_symbol, direction, outcome_status, decided_at_utc,
            getattr(tick, "time", None), getattr(tick, "time_msc", None), getattr(tick, "bid", None),
            getattr(tick, "ask", None), (tick.ask - tick.bid) if tick is not None else None, requested_price,
            requested_volume, filled, fill_price, slippage,
            sum(d["commission"] or 0.0 for d in deals) if deals else None,
            sum(d["fee"] or 0.0 for d in deals) if deals else None,
            order["last_broker_retcode"] if order is not None else None,
            order["last_broker_comment"] if order is not None else None, fill_type, len(deals),
            estimate.spread_cost if estimate else None, estimate.commission_cost if estimate else None,
            estimate.slippage_cost if estimate else None, estimate.total_cost if estimate else None,
            session_label(hour), hour, features.get("atr"), features.get("realized_volatility"),
            features.get("spread_percentile"), to_news, since_news, position_id, now,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def sweep_exit_costs(conn: sqlite3.Connection, *, now_utc: int | None = None) -> int:
    """Completes observations whose position has CLOSED locally. Returns
    the number of rows completed. Idempotent: a completed row is never
    rewritten."""
    now = now_utc if now_utc is not None else int(time.time())
    rows = conn.execute(
        """
        SELECT o.id, o.broker_position_id FROM execution_cost_observations o
        JOIN positions p ON p.broker_position_id = o.broker_position_id
        WHERE o.exit_recorded_at_utc IS NULL AND o.broker_position_id IS NOT NULL AND p.status = 'CLOSED'
        """
    ).fetchall()
    for row in rows:
        exit_deals = conn.execute(
            "SELECT commission, fee, swap FROM deals WHERE broker_position_id = ? AND entry_type != 'IN'",
            (row["broker_position_id"],),
        ).fetchall()
        all_swap = conn.execute(
            "SELECT COALESCE(SUM(swap), 0) FROM deals WHERE broker_position_id = ?", (row["broker_position_id"],),
        ).fetchone()[0]
        conn.execute(
            "UPDATE execution_cost_observations SET exit_commission = ?, exit_fee = ?, swap = ?, "
            "exit_recorded_at_utc = ? WHERE id = ? AND exit_recorded_at_utc IS NULL",
            (sum(d["commission"] or 0.0 for d in exit_deals), sum(d["fee"] or 0.0 for d in exit_deals),
             all_swap, now, row["id"]),
        )
    conn.commit()
    record_exit_observations(conn, now_utc=now)
    return len(rows)


# MT5 ENUM_DEAL_REASON codes (raw integers, as the gateway reports them).
_DEAL_REASON_KIND = {0: "MANUAL", 1: "MANUAL", 2: "MANUAL", 3: "AGENT_CLOSE", 4: "STOP_LOSS", 5: "TAKE_PROFIT",
                     6: "STOP_OUT"}
_TRIGGER_COMMENT = re.compile(r"\[(sl|tp)\s+([0-9]+(?:\.[0-9]+)?)\]", re.IGNORECASE)


def _trigger_from_comment(comment: str | None) -> tuple[str, float] | None:
    """MT5 writes a broker-triggered exit's level into the deal comment,
    e.g. "[sl 2345.67]". That is broker evidence of the trigger price."""
    match = _TRIGGER_COMMENT.search(comment or "")
    return (match.group(1).lower(), float(match.group(2))) if match else None


def record_exit_observations(conn: sqlite3.Connection, *, now_utc: int | None = None) -> int:
    """One `exit_cost_observations` row per CLOSED position with recorded
    exit deals, from evidence already in the DB (no broker call). The
    reference price comes only from broker evidence or the recorded close
    quote; where none exists it stays NULL and so does the slippage --
    never guessed. Idempotent (one row per broker position). Returns rows
    written."""
    now = now_utc if now_utc is not None else int(time.time())
    positions = conn.execute(
        """
        SELECT p.id, p.broker_position_id, p.canonical_symbol, p.direction, p.entry_order_id
        FROM positions p
        WHERE p.status = 'CLOSED'
          AND NOT EXISTS (SELECT 1 FROM exit_cost_observations e WHERE e.broker_position_id = p.broker_position_id)
          AND EXISTS (SELECT 1 FROM deals d WHERE d.broker_position_id = p.broker_position_id
                      AND d.entry_type IS NOT NULL AND d.entry_type != 'IN')
        """
    ).fetchall()
    written = 0
    for p in positions:
        deals = conn.execute(
            "SELECT price, volume, comment, reason, occurred_at_utc FROM deals "
            "WHERE broker_position_id = ? AND entry_type IS NOT NULL AND entry_type != 'IN' ORDER BY occurred_at_utc",
            (p["broker_position_id"],),
        ).fetchall()
        volume = sum(d["volume"] for d in deals)
        fill = sum(d["price"] * d["volume"] for d in deals) / volume if volume > 0 else None
        reasons = {d["reason"] for d in deals}
        reason = next(iter(reasons)) if len(reasons) == 1 else None
        triggers = {t for t in (_trigger_from_comment(d["comment"]) for d in deals) if t is not None}
        trigger = next(iter(triggers)) if len(triggers) == 1 else None
        close_request = conn.execute(
            "SELECT id, quote_bid, quote_ask FROM close_requests WHERE broker_position_id = ? "
            "AND status IN ('RESOLVED_CLOSED', 'RESOLVED_PARTIALLY_CLOSED') ORDER BY id DESC LIMIT 1",
            (p["broker_position_id"],),
        ).fetchone()

        if reason is not None:
            kind = _DEAL_REASON_KIND.get(reason, "OTHER")
        elif trigger is not None:
            kind = "STOP_LOSS" if trigger[0] == "sl" else "TAKE_PROFIT"
        elif close_request is not None:
            kind = "AGENT_CLOSE"
        else:
            kind = "UNKNOWN"

        reference, source, spread = None, "NONE", None
        if kind == "AGENT_CLOSE" and close_request is not None and close_request["quote_bid"] is not None:
            # closing a BUY sells at the bid; closing a SELL buys at the ask
            reference = close_request["quote_bid"] if p["direction"] == "BUY" else close_request["quote_ask"]
            spread = close_request["quote_ask"] - close_request["quote_bid"]
            source = "CLOSE_QUOTE"
        elif kind in ("STOP_LOSS", "TAKE_PROFIT") and trigger is not None \
                and trigger[0] == ("sl" if kind == "STOP_LOSS" else "tp"):
            reference, source = trigger[1], "DEAL_COMMENT_TRIGGER"
        elif kind == "TAKE_PROFIT" and p["entry_order_id"] is not None:
            # targets are never moved after entry; stops are (breakeven), so no such fallback for STOP_LOSS
            order = conn.execute("SELECT take_profit FROM orders WHERE id = ?", (p["entry_order_id"],)).fetchone()
            if order is not None and order["take_profit"]:
                reference, source = order["take_profit"], "ENTRY_ORDER_LEVEL"

        slippage = None
        if reference is not None and fill is not None:
            slippage = (reference - fill) if p["direction"] == "BUY" else (fill - reference)
        estimate = conn.execute(
            "SELECT estimated_slippage_price FROM execution_cost_observations WHERE broker_position_id = ? "
            "ORDER BY id LIMIT 1", (p["broker_position_id"],),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO exit_cost_observations (
                broker_position_id, position_id, canonical_symbol, position_direction, exit_kind, deal_reason,
                close_request_id, reference_price, reference_source, quote_spread_price, exit_fill_price,
                exit_volume, exit_deal_count, exit_slippage_price, estimated_slippage_price, exit_time_utc,
                recorded_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (p["broker_position_id"], p["id"], p["canonical_symbol"], p["direction"], kind, reason,
             close_request["id"] if close_request is not None else None, reference, source, spread, fill, volume,
             len(deals), slippage, estimate["estimated_slippage_price"] if estimate is not None else None,
             deals[-1]["occurred_at_utc"] if deals else None, now),
        )
        written += 1
    conn.commit()
    return written


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(q * (len(sorted_values) - 1) + 0.5))]


def summarize_exit_observations(conn: sqlite3.Connection, canonical_symbol: str) -> dict:
    """Per exit kind: count, how many have a proven reference, and the
    adverse exit slippage distribution (p50/p75/p90/p95) in PRICE units,
    next to the per-fill assumption the entries were costed with."""
    rows = conn.execute(
        "SELECT exit_kind, exit_slippage_price, estimated_slippage_price FROM exit_cost_observations "
        "WHERE canonical_symbol = ?", (canonical_symbol,),
    ).fetchall()
    out: dict = {"symbol": canonical_symbol, "exits": len(rows), "by_kind": {},
                 "sufficient": False, "min_samples": MIN_SAMPLES_FOR_EVIDENCE}
    kinds: dict = {}
    for r in rows:
        kinds.setdefault(r["exit_kind"], []).append(r)
    for kind, group in sorted(kinds.items()):
        slip = sorted(r["exit_slippage_price"] for r in group if r["exit_slippage_price"] is not None)
        assumed = [r["estimated_slippage_price"] for r in group if r["estimated_slippage_price"] is not None]
        out["by_kind"][kind] = {
            "exits": len(group), "with_reference": len(slip),
            "p50": _percentile(slip, 0.50), "p75": _percentile(slip, 0.75),
            "p90": _percentile(slip, 0.90), "p95": _percentile(slip, 0.95),
            "mean": statistics.fmean(slip) if slip else None,
            "adverse_share": (sum(1 for s in slip if s > 0) / len(slip)) if slip else None,
            "assumed_per_fill": statistics.median(assumed) if assumed else None,
        }
    measured = sum(v["with_reference"] for v in out["by_kind"].values())
    out["sufficient"] = measured >= MIN_SAMPLES_FOR_EVIDENCE
    return out


@dataclass(frozen=True)
class ObservedCostSummary:
    canonical_symbol: str
    observations: int
    filled: int
    median_spread_price: float | None
    median_slippage_price: float | None
    p90_slippage_price: float | None
    mean_commission_per_lot_round_trip: float | None
    partial_fills: int
    rejected_or_unknown: int
    sufficient: bool
    detail: str


def summarize_observations(conn: sqlite3.Connection, canonical_symbol: str) -> ObservedCostSummary:
    rows = conn.execute(
        "SELECT * FROM execution_cost_observations WHERE canonical_symbol = ? ORDER BY decided_at_utc",
        (canonical_symbol,),
    ).fetchall()
    filled = [r for r in rows if (r["filled_volume"] or 0) > 0]
    spreads = [r["spread_price"] for r in rows if r["spread_price"] is not None]
    slippage = sorted(r["slippage_price"] for r in filled if r["slippage_price"] is not None)
    round_trip = [
        abs((r["entry_commission"] or 0.0) + (r["exit_commission"] or 0.0)) / r["filled_volume"]
        for r in filled if r["exit_recorded_at_utc"] is not None
    ]
    sufficient = len(filled) >= MIN_SAMPLES_FOR_EVIDENCE and len(round_trip) >= MIN_SAMPLES_FOR_EVIDENCE
    return ObservedCostSummary(
        canonical_symbol=canonical_symbol, observations=len(rows), filled=len(filled),
        median_spread_price=statistics.median(spreads) if spreads else None,
        median_slippage_price=statistics.median(slippage) if slippage else None,
        p90_slippage_price=slippage[min(len(slippage) - 1, int(0.9 * len(slippage)))] if slippage else None,
        mean_commission_per_lot_round_trip=statistics.fmean(round_trip) if round_trip else None,
        partial_fills=sum(1 for r in rows if r["fill_type"] == "PARTIAL"),
        rejected_or_unknown=sum(1 for r in rows if r["outcome_status"] in ("REJECTED", "UNKNOWN")),
        sufficient=sufficient,
        detail=(f"{len(filled)} filled / {len(round_trip)} closed observations; "
                + ("enough to review [costs] (operator decision)" if sufficient
                   else f"need >= {MIN_SAMPLES_FOR_EVIDENCE} filled and closed before treating as evidence")),
    )
