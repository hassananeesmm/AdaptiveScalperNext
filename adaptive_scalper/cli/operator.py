"""Kill-switch commands -- the ONLY place in the codebase that constructs
an `OperatorAuthority` (enforced by `tests/test_runtime_architecture.py`).

`bootstrap` and `clear` are explicit human operator commands with a
mandatory operator id and reason, written to `configuration_audit`. No
launcher, startup path, runtime, ML, RAG or OKF code calls them. `engage`
needs no authority: it only ever makes the system safer, and it never
closes positions (open positions keep their broker-side protective stops
and continue to be managed).
"""

from __future__ import annotations

import argparse

from adaptive_scalper.cli.common import CliError, open_db, print_json
from adaptive_scalper.core import kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.core.peak_equity_reset import PeakEquityResetRefused, operator_reset
from adaptive_scalper.risk import peak_equity


def cmd_status(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    state = kill_switch.get_state(conn)
    conn.close()
    print_json({
        "status": state.status.value, "reason": state.reason, "changed_at": state.changed_at,
        "changed_by": state.changed_by, "blocks_new_entries": state.blocks_new_entries,
    })
    return 0


def cmd_engage(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    state = kill_switch.engage(conn, reason=args.reason, actor=args.actor)
    conn.close()
    print(f"kill switch ENGAGED: {state.reason} (by {state.changed_by})")
    print("new entries are blocked; open positions are NOT closed and keep being managed")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    before = kill_switch.get_state(conn)
    state = kill_switch.bootstrap(conn, OperatorAuthority(args.operator_id), reason=args.reason)
    conn.close()
    if before.status == state.status:
        print(f"kill switch already {state.status.value}; bootstrap changes nothing (use `clear` to disengage)")
        return 1 if state.status.value == "ENGAGED" else 0
    print(f"kill switch BOOTSTRAPPED -> {state.status.value}: {state.reason} (by {state.changed_by})")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    state = kill_switch.clear(conn, reason=args.reason, authority=OperatorAuthority(args.operator_id))
    conn.close()
    print(f"kill switch CLEARED: {state.reason} (by {state.changed_by})")
    return 0


def _peak_row(row) -> dict:
    return {"id": row["id"], "account": peak_equity.mask_login(row["account_login"]), "server": row["account_server"],
            "event": row["event_type"], "peak_equity": row["peak_equity"], "previous_peak_equity":
            row["previous_peak_equity"], "observed_equity": row["observed_equity"], "actor": row["actor"],
            "reason": row["reason"], "evidence_sha256": row["evidence_sha256"], "recorded_at_utc": row["recorded_at_utc"]}


def cmd_peak_status(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rows = conn.execute(
        "SELECT h.* FROM peak_equity_history h JOIN (SELECT MAX(id) AS id FROM peak_equity_history "
        "GROUP BY account_login, account_server) latest ON latest.id = h.id ORDER BY h.id").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM peak_equity_history").fetchone()[0]
    conn.close()
    print_json({"history_rows": total, "current_baselines": [_peak_row(r) for r in rows]})
    return 0


def cmd_peak_history(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rows = peak_equity.history(conn)
    conn.close()
    print_json([_peak_row(r) for r in rows[-args.limit:]])
    return 0


def cmd_peak_reset(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    try:
        row = operator_reset(conn, authority=OperatorAuthority(args.operator_id), login=args.login,
                             server=args.server, new_peak=args.peak, reason=args.reason, evidence_path=args.evidence,
                             acknowledge_lower=args.acknowledge_lower)
    except PeakEquityResetRefused as exc:
        raise CliError(f"peak-equity reset refused, nothing written: {exc}") from exc
    finally:
        conn.close()
    print_json(_peak_row(row))
    print("baseline recorded. The kill switch stays ENGAGED: clear it separately with `kill-switch clear` "
          "once the reset has been reviewed.")
    return 0


def register(sub) -> None:
    ks = sub.add_parser("kill-switch", help="kill switch operations")
    ks_sub = ks.add_subparsers(dest="ks_command", required=True)
    ks_sub.add_parser("status", help="show kill switch state").set_defaults(func=cmd_status)

    engage = ks_sub.add_parser("engage", help="engage the kill switch (blocks new entries; never closes positions)")
    engage.add_argument("--reason", required=True)
    engage.add_argument("--actor", default="operator")
    engage.set_defaults(func=cmd_engage)

    boot = ks_sub.add_parser("bootstrap", help="HUMAN OPERATOR: first-time UNINITIALIZED/INVALID -> DISENGAGED")
    boot.add_argument("--operator-id", required=True)
    boot.add_argument("--reason", required=True)
    boot.set_defaults(func=cmd_bootstrap)

    clear = ks_sub.add_parser("clear", help="HUMAN OPERATOR: disengage the kill switch")
    clear.add_argument("--reason", required=True)
    clear.add_argument("--operator-id", required=True)
    clear.set_defaults(func=cmd_clear)

    pk = sub.add_parser("peak-equity", help="account-bound drawdown baseline (ASN-034)")
    pk_sub = pk.add_subparsers(dest="pk_command", required=True)
    pk_sub.add_parser("status", help="current baseline per broker account (read-only)").set_defaults(
        func=cmd_peak_status)
    hist = pk_sub.add_parser("history", help="append-only baseline history (read-only)")
    hist.add_argument("--limit", type=int, default=50)
    hist.set_defaults(func=cmd_peak_history)
    reset = pk_sub.add_parser("reset", help="HUMAN OPERATOR: audited baseline reset (kill switch ENGAGED, flat book, "
                                            "reason, evidence); never a way to resume trading after a loss")
    reset.add_argument("--operator-id", required=True)
    reset.add_argument("--login", type=int, required=True)
    reset.add_argument("--server", required=True)
    reset.add_argument("--peak", type=float, required=True)
    reset.add_argument("--reason", required=True)
    reset.add_argument("--evidence", required=True, help="broker statement / account history export file")
    reset.add_argument("--acknowledge-lower", action="store_true",
                       help="required when the new baseline is below the current peak")
    reset.set_defaults(func=cmd_peak_reset)
