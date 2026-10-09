"""Durable close requests and their broker-truth resolution (master prompt
section 13: close-side UNKNOWN durability).

`execution.close.close_position_safely()` writes a `close_requests` row
BEFORE `order_send` (write-ahead) and settles it only from positive
evidence. Anything it cannot prove -- the send raised, the retcode is
ambiguous, the broker left a close order working, or reconciliation could
not read broker truth right after the send -- leaves the row UNRESOLVED,
with an `UNKNOWN_OUTCOME` execution incident. A process that dies between
the write and the send leaves the row UNRESOLVED too (send_outcome
SENDING), so a restart recovers into the same fail-closed state.

While a row is UNRESOLVED:
- new exposure is blocked (`has_unresolved_close`, and the incident feeds
  `reconciliation.has_dangerous_unresolved_unknown`);
- no further close is recorded or sent for that position (the partial
  unique index plus `close_position_safely`'s pre-send refusal): the prior
  outcome is never "retried" blindly.

`resolve_unresolved_closes()` decides from a single fresh broker snapshot
(open positions, working orders, deal history). It never invents a fill,
a volume or a deal. Verdicts:

- RESOLVED_CLOSED: the broker no longer reports the position AND local
  state has already recovered it as closed from real history deals
  (reconciliation owns that recovery; until it happens the row waits).
- RESOLVED_STILL_OPEN: after a settle period the broker still reports the
  position at the full requested volume, no close order is working for it
  and no closing deal exists since the request -- the close did not execute.
- RESOLVED_PARTIALLY_CLOSED: the broker reports a smaller volume and the
  closing deals since the request account for exactly the difference;
  local volume/risk are reduced from that deal evidence.
- anything else (a working close order, deals that do not explain the
  volume, a missing local row) stays UNRESOLVED with the reason recorded.

A broker query failure while resolving records the attempt and re-raises,
so the runtime publishes BROKER_TRUTH_UNAVAILABLE instead of a verdict.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway

UNRESOLVED = "UNRESOLVED"
RESOLVED_CLOSED = "RESOLVED_CLOSED"
RESOLVED_PARTIALLY_CLOSED = "RESOLVED_PARTIALLY_CLOSED"
RESOLVED_STILL_OPEN = "RESOLVED_STILL_OPEN"
RESOLVED_NOT_EXECUTED = "RESOLVED_NOT_EXECUTED"

SENDING = "SENDING"
# A close order can take a moment to appear in positions/history after an
# ambiguous send; "still open" is only concluded after this long.
CLOSE_SETTLE_SECONDS = 10
# Deals are matched from shortly before the request (clock skew between
# this process and the broker's history timestamps).
DEAL_CLOCK_SKEW_SECONDS = 5
# MT5 ENUM_DEAL_ENTRY closing entries: OUT, INOUT, OUT_BY.
_CLOSING_DEAL_ENTRIES = frozenset({1, 2, 3})
_VOLUME_EPSILON = 1e-9


class CloseRequestConflict(RuntimeError):
    """A close was requested for a position that already has an unresolved
    close -- the caller must not send."""


@dataclass(frozen=True)
class CloseResolution:
    close_request_id: int
    broker_position_id: str
    status: str
    detail: str

    @property
    def resolved(self) -> bool:
        return self.status != UNRESOLVED


def _incident_key(broker_position_id: str) -> str:
    return f"CLOSE_UNRESOLVED:position:{broker_position_id}"


def has_unresolved_close(conn: sqlite3.Connection, broker_position_id: str | None = None) -> bool:
    if broker_position_id is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM close_requests WHERE status = ?", (UNRESOLVED,)).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM close_requests WHERE status = ? AND broker_position_id = ?",
            (UNRESOLVED, str(broker_position_id)),
        ).fetchone()
    return row["n"] > 0


def get_unresolved_closes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM close_requests WHERE status = ? ORDER BY requested_at_utc, id", (UNRESOLVED,),
    ).fetchall()


def record_close_intent(
    conn: sqlite3.Connection, *, broker_position_id: str, broker_symbol: str, position_direction: str,
    requested_volume: float, magic: int, comment: str, now_utc: int,
    quote_bid: float | None = None, quote_ask: float | None = None, quote_time_msc: int | None = None,
) -> int:
    """Write-ahead row, committed before `order_send` is called. Raises
    `CloseRequestConflict` when the position already has an unresolved
    close (never a second request on top of an unproven one)."""
    if has_unresolved_close(conn, broker_position_id):
        raise CloseRequestConflict(f"position {broker_position_id} already has an unresolved close request")
    local = conn.execute(
        "SELECT id FROM positions WHERE broker_position_id = ? ORDER BY id DESC LIMIT 1", (str(broker_position_id),),
    ).fetchone()
    try:
        cursor = conn.execute(
            "INSERT INTO close_requests (position_id, broker_position_id, broker_symbol, position_direction, "
            "requested_volume, magic, comment, requested_at_utc, send_outcome, status, quote_bid, quote_ask, "
            "quote_time_msc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (local["id"] if local is not None else None, str(broker_position_id), broker_symbol, position_direction,
             requested_volume, magic, comment, now_utc, SENDING, UNRESOLVED, quote_bid, quote_ask, quote_time_msc),
        )
    except sqlite3.IntegrityError as exc:  # a concurrent writer won the partial unique index
        raise CloseRequestConflict(f"position {broker_position_id}: {exc}") from exc
    if conn.in_transaction:
        conn.commit()
    return cursor.lastrowid


def settle_close_request(
    conn: sqlite3.Connection, close_request_id: int, *, send_outcome: str, send_detail: str,
    retcode: int | None, status: str, now_utc: int, broker_order_ticket: str | None = None,
) -> None:
    """Record what the send proved. `status` UNRESOLVED keeps the request
    open and raises the UNKNOWN_OUTCOME incident that blocks new entries.
    `broker_order_ticket` (the ticket the send itself returned) is exit-cost
    evidence only: it links closing deals to this request and never affects
    the resolution; a known ticket is never overwritten with NULL."""
    conn.execute(
        "UPDATE close_requests SET send_outcome = ?, send_detail = ?, retcode = ?, "
        "broker_order_ticket = COALESCE(broker_order_ticket, ?) WHERE id = ?",
        (send_outcome, send_detail, retcode, broker_order_ticket, close_request_id),
    )
    if status == UNRESOLVED:
        _ensure_incident(conn, close_request_id, f"close outcome not proven ({send_outcome}): {send_detail}", now_utc)
    else:
        _resolve(conn, close_request_id, status, f"send outcome {send_outcome}: {send_detail}", now_utc)
    if conn.in_transaction:
        conn.commit()


def _ensure_incident(conn: sqlite3.Connection, close_request_id: int, detail: str, now: int) -> None:
    from adaptive_scalper.execution.reconciliation import record_incident
    row = conn.execute("SELECT broker_position_id FROM close_requests WHERE id = ?", (close_request_id,)).fetchone()
    incident_id = record_incident(conn, "UNKNOWN_OUTCOME", f"[CLOSE] position {row['broker_position_id']}: {detail}",
                                  dedup_key=_incident_key(row["broker_position_id"]), now_utc=now)
    conn.execute("UPDATE close_requests SET incident_id = ? WHERE id = ?", (incident_id, close_request_id))


def _resolve(conn: sqlite3.Connection, close_request_id: int, status: str, detail: str, now: int) -> None:
    from adaptive_scalper.execution.reconciliation import resolve_incident
    conn.execute(
        "UPDATE close_requests SET status = ?, resolved_at_utc = ?, resolution_detail = ? WHERE id = ?",
        (status, now, detail, close_request_id),
    )
    row = conn.execute("SELECT incident_id FROM close_requests WHERE id = ?", (close_request_id,)).fetchone()
    if row["incident_id"] is not None:
        resolve_incident(conn, row["incident_id"], f"{status}: {detail}", now_utc=now)


def _note_attempt(conn: sqlite3.Connection, close_request_id: int, detail: str, now: int) -> None:
    conn.execute(
        "UPDATE close_requests SET attempt_count = attempt_count + 1, last_attempt_at_utc = ?, "
        "last_attempt_detail = ? WHERE id = ?",
        (now, detail, close_request_id),
    )


def resolve_unresolved_closes(
    conn: sqlite3.Connection, gateway: Gateway, *, now_utc: int, settle_seconds: int = CLOSE_SETTLE_SECONDS,
) -> list[CloseResolution]:
    rows = get_unresolved_closes(conn)
    if not rows:
        return []
    try:
        positions = {p.broker_position_id: p for p in gateway.positions_get()}
        working = gateway.orders_get()
        earliest = min(r["requested_at_utc"] for r in rows) - DEAL_CLOCK_SKEW_SECONDS
        deals = gateway.history_deals_get(earliest, now_utc + 60)
    except Exception as exc:
        for row in rows:
            _note_attempt(conn, row["id"], f"broker truth unavailable: {type(exc).__name__}: {exc}", now_utc)
            _ensure_incident(conn, row["id"], "broker truth unavailable while resolving the close", now_utc)
        if conn.in_transaction:
            conn.commit()
        raise

    outcomes = []
    for row in rows:
        status, detail = _decide(conn, row, positions, working, deals, now_utc, settle_seconds)
        _note_attempt(conn, row["id"], detail, now_utc)
        if status == UNRESOLVED:
            _ensure_incident(conn, row["id"], detail, now_utc)
        else:
            _resolve(conn, row["id"], status, detail, now_utc)
        outcomes.append(CloseResolution(row["id"], row["broker_position_id"], status, detail))
    if conn.in_transaction:
        conn.commit()
    return outcomes


def _decide(conn, row, positions, working, deals, now: int, settle_seconds: int) -> tuple[str, str]:
    pid = row["broker_position_id"]
    local = conn.execute(
        "SELECT id, status, volume, initial_monetary_risk FROM positions WHERE broker_position_id = ? "
        "ORDER BY id DESC LIMIT 1", (pid,),
    ).fetchone()
    working_close = [o for o in working if o.position_id is not None and str(o.position_id) == pid]
    if working_close:
        return UNRESOLVED, f"a broker order is still working on position {pid} ({len(working_close)} order(s))"
    closing = [d for d in deals
               if str(d.position_id) == pid and d.entry in _CLOSING_DEAL_ENTRIES
               and d.time >= row["requested_at_utc"] - DEAL_CLOCK_SKEW_SECONDS]
    closed_volume = sum(d.volume for d in closing)
    live = positions.get(pid)

    if live is None:
        if local is not None and local["status"] != "OPEN":
            return RESOLVED_CLOSED, (f"broker no longer reports position {pid}; local state recovered it as "
                                     f"{local['status']} from broker history ({len(closing)} closing deal(s) since the request)")
        return UNRESOLVED, (f"broker no longer reports position {pid} but local state has not yet recovered its "
                            f"closing deals from broker history -- waiting for reconciliation")

    if local is None or local["status"] != "OPEN":
        return UNRESOLVED, f"broker still reports position {pid} but no OPEN local position matches it"
    if abs(live.volume - row["requested_volume"]) <= _VOLUME_EPSILON and not closing:
        age = now - row["requested_at_utc"]
        if age < settle_seconds:
            return UNRESOLVED, f"position {pid} still open {age}s after the request; waiting {settle_seconds}s to settle"
        return RESOLVED_STILL_OPEN, (f"broker still reports position {pid} at the full volume {live.volume}, no "
                                     f"working order and no closing deal since the request: the close did not execute")
    if closing and live.volume < local["volume"] - _VOLUME_EPSILON \
            and abs((local["volume"] - closed_volume) - live.volume) <= _VOLUME_EPSILON:
        remaining_fraction = live.volume / local["volume"]
        conn.execute("UPDATE positions SET volume = ?, initial_monetary_risk = ? WHERE id = ?",
                     (live.volume, local["initial_monetary_risk"] * remaining_fraction, local["id"]))
        return RESOLVED_PARTIALLY_CLOSED, (f"closing deals since the request total {closed_volume}; broker volume "
                                           f"{live.volume} = local {local['volume']} - {closed_volume}; local volume/risk reduced")
    if closing and abs(live.volume - local["volume"]) <= _VOLUME_EPSILON and live.volume < row["requested_volume"]:
        return RESOLVED_PARTIALLY_CLOSED, (f"local volume already matches broker volume {live.volume} after "
                                           f"{closed_volume} closed since the request")
    return UNRESOLVED, (f"conflicting evidence for position {pid}: broker volume {live.volume}, local volume "
                        f"{local['volume']}, requested {row['requested_volume']}, closing deals {closed_volume}")
