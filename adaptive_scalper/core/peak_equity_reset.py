"""Audited drawdown-baseline reset (ASN-034) -- a HUMAN OPERATOR action.

The only code path that can LOWER an account's drawdown baseline, or record
one for an account the runtime refuses to bind automatically (an account
change). It appends one OPERATOR_RESET row to the append-only
`peak_equity_history` (risk/peak_equity.py); earlier rows are kept.

A reset is legitimate only for an accounting reason the drawdown limit was
never meant to measure (a withdrawal, a replaced account), documented by a
broker statement. It is never a way to resume trading after a loss: the
limit exists precisely to stop entries after one.

Preconditions (each refusal writes nothing):
- an `OperatorAuthority` (constructed only by cli/operator.py);
- the kill switch ENGAGED, so no new entry can race the change (the operator
  clears it separately, afterwards, with `kill-switch clear`);
- a flat book: no open position, active order or unresolved close request;
- a written reason of at least MIN_RESET_REASON_CHARS characters;
- an existing evidence file (path and SHA-256 recorded);
- a positive finite value, and `acknowledge_lower=True` when it is below the
  account's current peak.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import time
from pathlib import Path

from adaptive_scalper.core.kill_switch import KillSwitchStatus
from adaptive_scalper.core.kill_switch import get_state as get_kill_switch_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.risk.peak_equity import EVENT_OPERATOR_RESET, append_row, current_peak, mask_login
from adaptive_scalper.runtime.state import record_event

MIN_RESET_REASON_CHARS = 20


class PeakEquityResetRefused(ValueError):
    """A reset precondition failed; nothing was written."""


def evidence_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def operator_reset(conn: sqlite3.Connection, *, authority: OperatorAuthority, login: int, server: str,
                   new_peak: float, reason: str, evidence_path: str | Path, acknowledge_lower: bool = False,
                   now_utc: int | None = None) -> sqlite3.Row:
    from adaptive_scalper.execution.close_requests import has_unresolved_close
    from adaptive_scalper.execution.reconciliation import get_open_positions
    from adaptive_scalper.execution.store import get_active_orders

    now = now_utc if now_utc is not None else int(time.time())

    def refuse(why: str) -> None:
        raise PeakEquityResetRefused(why)

    if not isinstance(authority, OperatorAuthority):
        refuse("an OperatorAuthority is required (human operator CLI only)")
    if not isinstance(login, int) or isinstance(login, bool) or login <= 0:
        refuse(f"login must be a positive integer, got {login!r}")
    if not isinstance(server, str) or not server.strip():
        refuse("server is required")
    if not isinstance(new_peak, (int, float)) or isinstance(new_peak, bool) or not math.isfinite(new_peak) \
            or new_peak <= 0:
        refuse(f"new peak must be a positive finite number, got {new_peak!r}")
    reason = (reason or "").strip()
    if len(reason) < MIN_RESET_REASON_CHARS:
        refuse(f"a reason of at least {MIN_RESET_REASON_CHARS} characters is required")
    kill = get_kill_switch_state(conn)
    if kill.status != KillSwitchStatus.ENGAGED:
        refuse(f"the kill switch must be ENGAGED during a baseline reset (it is {kill.status.value})")
    if get_open_positions(conn):
        refuse("open positions exist: a baseline reset needs a flat book")
    if get_active_orders(conn):
        refuse("active orders exist: a baseline reset needs a flat book")
    if has_unresolved_close(conn):
        refuse("a close request is unresolved")
    path = Path(evidence_path)
    if not path.is_file():
        refuse(f"evidence file not found: {path}")
    digest = evidence_sha256(path)
    row = current_peak(conn, login, server)
    previous = float(row["peak_equity"]) if row is not None else None
    if previous is not None and new_peak < previous and not acknowledge_lower:
        refuse(f"{new_peak:.2f} is below the current peak {previous:.2f}: lowering the drawdown baseline needs an "
               f"explicit acknowledgement")
    append_row(conn, login=login, server=server, event_type=EVENT_OPERATOR_RESET, peak=float(new_peak),
               previous=previous, observed=None, actor=authority.operator_id, reason=reason, now=now,
               evidence_path=str(path.resolve()), evidence_sha256=digest)
    record_event(conn, "WARNING", "risk", "PEAK_EQUITY_OPERATOR_RESET",
                 f"account {mask_login(login)} on {server!r}: baseline {previous} -> {float(new_peak):.2f} by "
                 f"{authority.operator_id} (evidence sha256 {digest[:12]}...): {reason}", now_utc=now)
    return current_peak(conn, login, server)
