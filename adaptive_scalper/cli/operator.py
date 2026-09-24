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

from adaptive_scalper.cli.common import open_db, print_json
from adaptive_scalper.core import kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority


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
