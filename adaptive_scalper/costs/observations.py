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

import dataclasses
import json
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


# ----------------------------------------------------------------------------
# Exit side: one observation per ECONOMIC EXIT EVENT (migration 0031).
#
# An economic exit event is one broker ORDER that closed (part of) a
# position: several fills of that order are volume-weighted into the one
# event; separate orders on the same position (a partial agent close, later
# the stop loss for the remainder) are separate events and are never pooled.
# Evidence comes only from the DB (runtime-recorded deals + imported account
# history), never from an extra broker call. Nothing here is ever guessed:
# an unproven reference price leaves the reference and the slippage NULL.

# MT5 ENUM_DEAL_REASON codes (raw integers, as the gateway reports them).
DEAL_REASON_EXPERT = 3
_REASON_KIND = {0: "MANUAL", 1: "MANUAL", 2: "MANUAL", 4: "STOP_LOSS", 5: "TAKE_PROFIT", 6: "STOP_OUT"}
_ENTRY_NAMES = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
_CLOSING_ENTRIES = frozenset({"OUT", "INOUT", "OUT_BY"})
_TRIGGER_COMMENT = re.compile(r"\[(sl|tp)\s+([0-9]+(?:\.[0-9]+)?)\]", re.IGNORECASE)
_VOLUME_EPSILON = 1e-6
_ACCOUNT_HISTORY_IDENTITY_SECONDS = 300
# A closed position whose exit volume cannot be proven complete stays
# PROVISIONAL (recomputed as late deals arrive) for this long, then becomes
# FINAL with exclusion_reason UNSETTLED_VOLUME -- recorded, never counted.
EXIT_SETTLE_HORIZON_SECONDS = 7 * 86400
_CONTENT_FIELDS = (
    "position_id", "canonical_symbol", "position_direction", "exit_kind", "deal_reason", "broker_order_ticket",
    "deal_tickets", "deal_count", "entry_types", "close_request_id", "reference_price", "reference_source",
    "quote_spread_price", "exit_fill_price", "exit_volume", "exit_slippage_price", "estimated_slippage_price",
    "first_deal_time_utc", "last_deal_time_utc", "status", "exclusion_reason",
)


@dataclass(frozen=True)
class _Deal:
    ticket: str
    order_ticket: str | None
    entry: str
    volume: float
    price: float
    comment: str
    reason: int | None
    magic: int | None
    time: int


def _ticket(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text in ("", "0") else text


def _trigger_from_comment(comment: str | None) -> tuple[str, float] | None:
    """MT5 writes a broker-triggered exit's level into the deal comment,
    e.g. "[sl 2345.67]". That is broker evidence of the trigger price."""
    match = _TRIGGER_COMMENT.search(comment or "")
    return (match.group(1).lower(), float(match.group(2))) if match else None


def _local_deals(conn: sqlite3.Connection, pid: str) -> dict[str, _Deal]:
    out = {}
    for r in conn.execute(
        "SELECT broker_deal_id, broker_order_ticket, entry_type, volume, price, comment, reason, magic, "
        "occurred_at_utc FROM deals WHERE broker_position_id = ? AND entry_type IS NOT NULL", (pid,),
    ):
        out[str(r[0])] = _Deal(str(r[0]), _ticket(r[1]), r[2], r[3], r[4], r[5] or "", r[6], r[7], r[8])
    return out


def _account_history_deals(conn: sqlite3.Connection, position) -> dict[str, _Deal]:
    """Imported account-history deals for the position -- used only when that
    history also holds the position's own opening deal (same direction,
    opened within the identity window): position tickets are unique per
    server, not across every account ever imported."""
    try:
        pid_int = int(position["broker_position_id"])
    except (TypeError, ValueError):
        return {}
    rows = conn.execute(
        "SELECT ticket, order_ticket, entry, type, volume, price, comment, reason, magic, time_utc "
        "FROM broker_account_deals WHERE position_id = ?", (pid_int,),
    ).fetchall()
    want_type = 0 if position["direction"] == "BUY" else 1
    opening = [r for r in rows if r["entry"] == 0 and r["type"] == want_type
               and abs(r["time_utc"] - position["opened_at_utc"]) <= _ACCOUNT_HISTORY_IDENTITY_SECONDS]
    if not opening:
        return {}
    return {
        str(r["ticket"]): _Deal(str(r["ticket"]), _ticket(r["order_ticket"]),
                                _ENTRY_NAMES.get(r["entry"], str(r["entry"])), r["volume"], r["price"],
                                r["comment"] or "", r["reason"], r["magic"], r["time_utc"])
        for r in rows
    }


def position_exit_deals(conn: sqlite3.Connection, position) -> list[_Deal]:
    """Every known broker deal of the position, both sources merged and
    de-duplicated by deal ticket (a reason missing from one copy of the SAME
    broker deal is taken from the other). Oldest first."""
    merged = _local_deals(conn, position["broker_position_id"])
    for ticket, deal in _account_history_deals(conn, position).items():
        if ticket not in merged:
            merged[ticket] = deal
        elif merged[ticket].reason is None and deal.reason is not None:
            merged[ticket] = dataclasses.replace(merged[ticket], reason=deal.reason)
    return sorted(merged.values(), key=lambda d: (d.time, d.ticket))


def _event_key(pid: str, deal: _Deal) -> str:
    return f"{pid}:ORDER:{deal.order_ticket}" if deal.order_ticket else f"{pid}:DEAL:{deal.ticket}"


def _classify_event(conn: sqlite3.Connection, position, deals: list[_Deal]) -> dict:
    pid = position["broker_position_id"]
    reasons = {d.reason for d in deals}
    conflict = len(reasons) > 1
    reason = None if conflict else next(iter(reasons))
    triggers = {t for t in (_trigger_from_comment(d.comment) for d in deals) if t is not None}
    trigger = next(iter(triggers)) if len(triggers) == 1 else None
    order_ticket = deals[0].order_ticket
    request = None
    if order_ticket is not None:
        request = conn.execute(
            "SELECT id, quote_bid, quote_ask FROM close_requests WHERE broker_position_id = ? "
            "AND broker_order_ticket = ? ORDER BY id LIMIT 1", (pid, order_ticket),
        ).fetchone()
    magics = {d.magic for d in deals}
    own_magic = len(magics) == 1 and None not in magics and conn.execute(
        "SELECT 1 FROM close_requests WHERE broker_position_id = ? AND magic = ? LIMIT 1", (pid, next(iter(magics))),
    ).fetchone() is not None

    if conflict:
        kind = "UNKNOWN"
    elif reason in _REASON_KIND:
        kind = _REASON_KIND[reason]
    elif reason == DEAL_REASON_EXPERT:
        kind = "AGENT_CLOSE" if (request is not None or own_magic) else "OTHER"
    elif reason is not None:
        kind = "OTHER"
    elif trigger is not None:
        kind = "STOP_LOSS" if trigger[0] == "sl" else "TAKE_PROFIT"
    elif request is not None:
        kind = "AGENT_CLOSE"
    else:
        kind = "UNKNOWN"

    entries = sorted({d.entry for d in deals})
    exclusion = ("INOUT" if "INOUT" in entries else "OUT_BY" if "OUT_BY" in entries
                 else "REASON_CONFLICT" if conflict else None)

    reference, source, spread = None, "NONE", None
    if kind == "AGENT_CLOSE" and request is not None and request["quote_bid"] is not None \
            and request["quote_ask"] is not None:
        # closing a BUY sells at the bid; closing a SELL buys at the ask
        reference = request["quote_bid"] if position["direction"] == "BUY" else request["quote_ask"]
        spread = request["quote_ask"] - request["quote_bid"]
        source = "CLOSE_QUOTE"
    elif kind in ("STOP_LOSS", "TAKE_PROFIT") and trigger is not None \
            and trigger[0] == ("sl" if kind == "STOP_LOSS" else "tp"):
        reference, source = trigger[1], "DEAL_COMMENT_TRIGGER"

    volume = sum(d.volume for d in deals)
    fill = sum(d.price * d.volume for d in deals) / volume
    slippage = None
    if reference is not None and exclusion is None:
        slippage = (reference - fill) if position["direction"] == "BUY" else (fill - reference)
    return {
        "kind": kind, "reason": reason, "order_ticket": order_ticket,
        "request_id": request["id"] if request is not None else None, "entries": ",".join(entries),
        "exclusion": exclusion, "reference": reference, "source": source, "spread": spread,
        "fill": fill, "volume": volume, "slippage": slippage,
    }


def _position_settled(position, deals: list[_Deal]) -> bool:
    """All exit deals are known: the position is closed and its closing
    volume equals its entry volume (INOUT can never be proven here)."""
    if position["status"] != "CLOSED" or any(d.entry == "INOUT" for d in deals):
        return False
    entry_volume = sum(d.volume for d in deals if d.entry == "IN")
    exit_volume = sum(d.volume for d in deals if d.entry in ("OUT", "OUT_BY"))
    return entry_volume > 0 and abs(exit_volume - entry_volume) <= _VOLUME_EPSILON


def record_exit_observations(conn: sqlite3.Connection, *, now_utc: int | None = None) -> int:
    """Upserts one `exit_cost_observations` row per economic exit event of
    every position with exit deals whose events are not all FINAL yet.
    PROVISIONAL rows are recomputed deterministically (version bumps only
    when content changes); FINAL rows are never touched (and the DB refuses
    it). Idempotent. Returns rows inserted or changed."""
    now = now_utc if now_utc is not None else int(time.time())
    positions = conn.execute(
        """
        SELECT p.id, p.broker_position_id, p.canonical_symbol, p.direction, p.status, p.opened_at_utc,
               p.closed_at_utc
        FROM positions p
        WHERE (EXISTS (SELECT 1 FROM deals d WHERE d.broker_position_id = p.broker_position_id
                       AND d.entry_type IN ('OUT', 'INOUT', 'OUT_BY'))
               OR EXISTS (SELECT 1 FROM broker_account_deals b
                          WHERE b.position_id = CAST(p.broker_position_id AS INTEGER) AND b.entry IN (1, 2, 3)))
          AND NOT (p.status = 'CLOSED'
                   AND EXISTS (SELECT 1 FROM exit_cost_observations e WHERE e.broker_position_id = p.broker_position_id)
                   AND NOT EXISTS (SELECT 1 FROM exit_cost_observations e
                                   WHERE e.broker_position_id = p.broker_position_id AND e.status = 'PROVISIONAL'))
        ORDER BY p.id
        """
    ).fetchall()
    changed = 0
    for position in positions:
        pid = position["broker_position_id"]
        deals = position_exit_deals(conn, position)
        closing = [d for d in deals if d.entry in _CLOSING_ENTRIES]
        if not closing:
            continue
        settled = _position_settled(position, deals)
        horizon_passed = position["status"] == "CLOSED" and \
            now - max([d.time for d in closing] + [position["closed_at_utc"] or 0]) >= EXIT_SETTLE_HORIZON_SECONDS
        estimate = conn.execute(
            "SELECT estimated_slippage_price FROM execution_cost_observations WHERE broker_position_id = ? "
            "ORDER BY id LIMIT 1", (pid,),
        ).fetchone()
        groups: dict[str, list[_Deal]] = {}
        for d in closing:
            groups.setdefault(_event_key(pid, d), []).append(d)
        for key, group in groups.items():
            ev = _classify_event(conn, position, group)
            exclusion = ev["exclusion"]
            if settled:
                status = "FINAL"
            elif horizon_passed:
                status, exclusion = "FINAL", exclusion or "UNSETTLED_VOLUME"
            else:
                status = "PROVISIONAL"
            content = {
                "position_id": position["id"], "canonical_symbol": position["canonical_symbol"],
                "position_direction": position["direction"], "exit_kind": ev["kind"], "deal_reason": ev["reason"],
                "broker_order_ticket": ev["order_ticket"],
                "deal_tickets": json.dumps(sorted(d.ticket for d in group)), "deal_count": len(group),
                "entry_types": ev["entries"], "close_request_id": ev["request_id"],
                "reference_price": ev["reference"], "reference_source": ev["source"],
                "quote_spread_price": ev["spread"], "exit_fill_price": ev["fill"], "exit_volume": ev["volume"],
                "exit_slippage_price": ev["slippage"],
                "estimated_slippage_price": estimate["estimated_slippage_price"] if estimate is not None else None,
                "first_deal_time_utc": min(d.time for d in group), "last_deal_time_utc": max(d.time for d in group),
                "status": status, "exclusion_reason": exclusion,
            }
            changed += _upsert_event(conn, key, pid, content, now)
    conn.commit()
    return changed


def _upsert_event(conn: sqlite3.Connection, key: str, pid: str, content: dict, now: int) -> int:
    existing = conn.execute("SELECT * FROM exit_cost_observations WHERE event_key = ?", (key,)).fetchone()
    finalized = now if content["status"] == "FINAL" else None
    if existing is None:
        cols = ("event_key", "broker_position_id") + _CONTENT_FIELDS + (
            "version", "first_recorded_at_utc", "updated_at_utc", "finalized_at_utc")
        values = (key, pid) + tuple(content[f] for f in _CONTENT_FIELDS) + (1, now, now, finalized)
        conn.execute(f"INSERT INTO exit_cost_observations ({', '.join(cols)}) "  # nosec B608 - fixed column names
                     f"VALUES ({', '.join('?' for _ in cols)})", values)
        return 1
    if existing["status"] == "FINAL":
        return 0   # immutable (also enforced by trigger)
    if all(existing[f] == content[f] for f in _CONTENT_FIELDS):
        return 0
    assignments = ", ".join(f"{f} = ?" for f in _CONTENT_FIELDS)
    conn.execute(
        f"UPDATE exit_cost_observations SET {assignments}, version = version + 1, updated_at_utc = ?, "  # nosec B608
        "finalized_at_utc = ? WHERE event_key = ? AND status = 'PROVISIONAL'",
        tuple(content[f] for f in _CONTENT_FIELDS) + (now, finalized, key),
    )
    return 1


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(q * (len(sorted_values) - 1) + 0.5))]


def summarize_exit_observations(conn: sqlite3.Connection, canonical_symbol: str) -> dict:
    """Per exit kind, from FINAL non-excluded events only: how many carry a
    proven reference, the adverse exit slippage distribution (p50/p75/p90/
    p95) in PRICE units next to the per-fill assumption, and a PER-KIND
    minimum-sample flag. There is deliberately no pooled flag: stop exits,
    targets and agent closes are different fills and never add up to one
    sample. Provisional and excluded events are counted, never measured."""
    rows = conn.execute(
        "SELECT exit_kind, status, exclusion_reason, exit_slippage_price, estimated_slippage_price "
        "FROM exit_cost_observations WHERE canonical_symbol = ?", (canonical_symbol,),
    ).fetchall()
    out: dict = {"symbol": canonical_symbol, "events": len(rows), "min_samples_per_kind": MIN_SAMPLES_FOR_EVIDENCE,
                 "provisional": sum(1 for r in rows if r["status"] == "PROVISIONAL"),
                 "excluded": {}, "by_kind": {},
                 "note": "evidence only: meets_min_samples is a per-kind sample count, never a "
                         "recalibration trigger; [costs.SYMBOL] changes only by operator decision"}
    for r in rows:
        if r["exclusion_reason"] is not None:
            out["excluded"][r["exclusion_reason"]] = out["excluded"].get(r["exclusion_reason"], 0) + 1
    kinds: dict = {}
    for r in rows:
        kinds.setdefault(r["exit_kind"], []).append(r)
    for kind, group in sorted(kinds.items()):
        usable = [r for r in group if r["status"] == "FINAL" and r["exclusion_reason"] is None]
        slip = sorted(r["exit_slippage_price"] for r in usable if r["exit_slippage_price"] is not None)
        assumed = [r["estimated_slippage_price"] for r in usable if r["estimated_slippage_price"] is not None]
        out["by_kind"][kind] = {
            "events": len(group), "final_usable": len(usable), "with_reference": len(slip),
            "p50": _percentile(slip, 0.50), "p75": _percentile(slip, 0.75),
            "p90": _percentile(slip, 0.90), "p95": _percentile(slip, 0.95),
            "mean": statistics.fmean(slip) if slip else None,
            "adverse_share": (sum(1 for s in slip if s > 0) / len(slip)) if slip else None,
            "assumed_per_fill": statistics.median(assumed) if assumed else None,
            "meets_min_samples": len(slip) >= MIN_SAMPLES_FOR_EVIDENCE,
        }
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
