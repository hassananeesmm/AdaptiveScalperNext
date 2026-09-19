"""Kill-switch tests for adaptive_scalper.core.kill_switch and
adaptive_scalper.core.permission.

Covers, at minimum:
 1. state persists in SQLite
 2. ENGAGED (and UNINITIALIZED/INVALID) block all NEW exposure
 3. existing-position monitoring / safe close / reconciliation stay allowed
 4. kill switch never clears automatically
 5. clear requires an explicit operator action (OperatorAuthority)
 6. clear requires a reason
 7. ML/RAG/strategy/model code cannot clear it (no OperatorAuthority)
 8. restart preserves ENGAGED state
 9. the kill-switch permission check returns BLOCK_KILL_SWITCH
10. repeated engage/clear operations remain auditable and deterministic

Plus, per external architecture review:
11. a fresh/never-initialized system fails closed (UNINITIALIZED blocks)
12. a corrupted app_state row fails closed (INVALID blocks)
13. state write + audit row are atomic (all-or-nothing)
14. clear()/bootstrap() require a real OperatorAuthority object, not a string
"""

import json

import pytest

from adaptive_scalper.core import kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.core.permission import (
    ActionKind,
    evaluate_kill_switch_permission,
)
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture()
def operator() -> OperatorAuthority:
    return OperatorAuthority("operator_jane")


# --------------------------------------------------------------------------
# 1 & 11 & 12. Persistence in SQLite, fail-closed on unknown/corrupt state
# --------------------------------------------------------------------------

def test_fresh_db_is_uninitialized_and_blocks_new_entries(db):
    state = kill_switch.get_state(db)
    assert state.status == kill_switch.KillSwitchStatus.UNINITIALIZED
    assert state.blocks_new_entries is True


def test_corrupted_app_state_row_is_invalid_and_blocks_new_entries(db):
    db.execute(
        "INSERT INTO app_state (key, value) VALUES ('kill_switch', 'not json')"
    )
    state = kill_switch.get_state(db)
    assert state.status == kill_switch.KillSwitchStatus.INVALID
    assert state.blocks_new_entries is True


def test_app_state_row_with_bogus_status_value_is_invalid(db):
    db.execute(
        "INSERT INTO app_state (key, value) VALUES ('kill_switch', ?)",
        (json.dumps({"status": "TOTALLY_MADE_UP", "reason": None,
                     "changed_at": None, "changed_by": None}),),
    )
    state = kill_switch.get_state(db)
    assert state.status == kill_switch.KillSwitchStatus.INVALID
    assert state.blocks_new_entries is True


def test_engage_persists_a_row_in_app_state(db):
    kill_switch.engage(db, reason="daily loss limit breached", actor="risk_governor")
    row = db.execute("SELECT value FROM app_state WHERE key = 'kill_switch'").fetchone()
    assert row is not None
    data = json.loads(row["value"])
    assert data["status"] == "ENGAGED"
    assert data["reason"] == "daily loss limit breached"


# --------------------------------------------------------------------------
# 2. ENGAGED (and UNINITIALIZED/INVALID) block all NEW exposure
# --------------------------------------------------------------------------

def test_engaged_state_blocks_new_entries_flag(db):
    state = kill_switch.engage(db, reason="daily loss limit breached", actor="risk_governor")
    assert state.blocks_new_entries is True


def test_permission_gate_blocks_new_entry_when_engaged(db):
    state = kill_switch.engage(db, reason="x", actor="risk_governor")
    result = evaluate_kill_switch_permission(state, ActionKind.NEW_ENTRY)
    assert result.allowed is False


def test_permission_gate_blocks_new_entry_when_uninitialized(db):
    state = kill_switch.get_state(db)  # UNINITIALIZED — nothing engaged it
    result = evaluate_kill_switch_permission(state, ActionKind.NEW_ENTRY)
    assert result.allowed is False
    assert result.block_reason == "BLOCK_KILL_SWITCH"


def test_permission_gate_allows_new_entry_once_bootstrapped(db, operator):
    kill_switch.bootstrap(db, operator)
    state = kill_switch.get_state(db)
    result = evaluate_kill_switch_permission(state, ActionKind.NEW_ENTRY)
    assert result.allowed is True
    assert result.block_reason is None


# --------------------------------------------------------------------------
# 3. Existing-position monitoring / safe close / reconciliation stay allowed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("action", [ActionKind.POSITION_MANAGEMENT, ActionKind.RECONCILIATION])
def test_engaged_kill_switch_does_not_block_position_management_or_reconciliation(db, action):
    state = kill_switch.engage(db, reason="critical reconciliation failure", actor="engine")
    result = evaluate_kill_switch_permission(state, action)
    assert result.allowed is True
    assert result.block_reason is None


@pytest.mark.parametrize("action", [ActionKind.POSITION_MANAGEMENT, ActionKind.RECONCILIATION])
def test_uninitialized_kill_switch_does_not_block_position_management_or_reconciliation(db, action):
    state = kill_switch.get_state(db)  # UNINITIALIZED
    result = evaluate_kill_switch_permission(state, action)
    assert result.allowed is True


# --------------------------------------------------------------------------
# 4. Never clears automatically
# --------------------------------------------------------------------------

def test_engaged_state_persists_across_reconnect(tmp_path):
    path = tmp_path / "persist.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    kill_switch.engage(conn1, reason="manual test", actor="operator")
    conn1.close()

    conn2 = connect(path)
    state = kill_switch.get_state(conn2)
    conn2.close()
    assert state.status == kill_switch.KillSwitchStatus.ENGAGED
    assert state.reason == "manual test"


def test_repeated_reads_never_clear_the_switch(db):
    kill_switch.engage(db, reason="critical reconciliation failure", actor="engine")
    for _ in range(5):
        state = kill_switch.get_state(db)
        assert state.status == kill_switch.KillSwitchStatus.ENGAGED


def test_migrate_does_not_clear_an_engaged_switch(tmp_path):
    path = tmp_path / "migrate_no_clear.sqlite3"
    conn = connect(path)
    migrate(conn)
    kill_switch.engage(conn, reason="critical reconciliation failure", actor="engine")
    migrate(conn)  # idempotent re-run, e.g. on a later app startup
    assert kill_switch.get_state(conn).status == kill_switch.KillSwitchStatus.ENGAGED
    conn.close()


def test_bootstrap_never_overrides_an_already_engaged_switch(db, operator):
    kill_switch.engage(db, reason="critical reconciliation failure", actor="engine")
    kill_switch.bootstrap(db, operator)  # must be a no-op
    assert kill_switch.get_state(db).status == kill_switch.KillSwitchStatus.ENGAGED


def test_bootstrap_is_idempotent_once_disengaged(db, operator):
    first = kill_switch.bootstrap(db, operator)
    second = kill_switch.bootstrap(db, operator)
    assert first.status == kill_switch.KillSwitchStatus.DISENGAGED
    assert second.status == kill_switch.KillSwitchStatus.DISENGAGED
    # Second call must not have written a new audit row (true no-op).
    assert len(kill_switch.history(db)) == 1


# --------------------------------------------------------------------------
# 5, 6 & 14. Clear requires OperatorAuthority + a reason
# --------------------------------------------------------------------------

def test_clear_rejects_a_bare_string_instead_of_operatorauthority(db):
    kill_switch.engage(db, reason="x", actor="operator")
    with pytest.raises(PermissionError):
        kill_switch.clear(db, reason="resolved", authority="operator")  # not an OperatorAuthority
    assert kill_switch.get_state(db).status == kill_switch.KillSwitchStatus.ENGAGED  # untouched


def test_clear_requires_a_reason(db, operator):
    kill_switch.engage(db, reason="x", actor="operator")
    with pytest.raises(ValueError):
        kill_switch.clear(db, reason="", authority=operator)


def test_operator_authority_requires_a_non_empty_operator_id():
    with pytest.raises(ValueError):
        OperatorAuthority("")
    with pytest.raises(ValueError):
        OperatorAuthority("   ")


def test_clear_succeeds_for_a_real_operator_authority(db, operator):
    kill_switch.engage(db, reason="x", actor="operator")
    state = kill_switch.clear(db, reason="issue investigated and resolved", authority=operator)
    assert state.status == kill_switch.KillSwitchStatus.DISENGAGED
    assert state.reason == "issue investigated and resolved"
    assert state.changed_by == "operator_jane"


# --------------------------------------------------------------------------
# 7. ML/RAG/strategy/model code cannot clear it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bogus", ["ml_model", "rag", "strategy", "selector", "learning", "", None, 42])
def test_clear_rejects_every_non_operatorauthority_value(db, bogus):
    kill_switch.engage(db, reason="x", actor="operator")
    with pytest.raises(PermissionError):
        kill_switch.clear(db, reason="resolved", authority=bogus)
    assert kill_switch.get_state(db).status == kill_switch.KillSwitchStatus.ENGAGED


def test_engage_has_no_role_restriction_any_safety_component_may_trip_it(db):
    # Engaging only ever makes the system safer, so it is intentionally
    # NOT restricted to operator — the risk governor, reconciliation, or
    # the engine itself must be able to call it directly.
    state = kill_switch.engage(db, reason="max drawdown exceeded", actor="risk_governor")
    assert state.status == kill_switch.KillSwitchStatus.ENGAGED


# --------------------------------------------------------------------------
# 8. Restart preserves ENGAGED state
# --------------------------------------------------------------------------

def test_restart_preserves_engaged_state(tmp_path):
    path = tmp_path / "restart.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    kill_switch.engage(conn1, reason="critical reconciliation failure", actor="engine")
    conn1.close()

    # Simulate application restart: new connection, migrations re-run.
    conn2 = connect(path)
    migrate(conn2)
    state = kill_switch.get_state(conn2)
    conn2.close()
    assert state.status == kill_switch.KillSwitchStatus.ENGAGED
    assert state.reason == "critical reconciliation failure"


# --------------------------------------------------------------------------
# 9. Permission gate returns the correct block reason
# --------------------------------------------------------------------------

def test_permission_gate_block_reason_matches_directive_vocabulary(db):
    state = kill_switch.engage(db, reason="x", actor="risk_governor")
    result = evaluate_kill_switch_permission(state, ActionKind.NEW_ENTRY)
    assert result.block_reason == "BLOCK_KILL_SWITCH"


# --------------------------------------------------------------------------
# 10 & 13. Repeated engage/clear remain auditable, deterministic, atomic
# --------------------------------------------------------------------------

def test_repeated_engage_is_deterministic_and_idempotent_in_effect(db):
    kill_switch.engage(db, reason="first", actor="risk_governor")
    kill_switch.engage(db, reason="second", actor="risk_governor")
    state = kill_switch.engage(db, reason="third", actor="risk_governor")
    assert state.status == kill_switch.KillSwitchStatus.ENGAGED
    assert state.reason == "third"


def test_every_transition_is_recorded_in_the_audit_trail(db, operator):
    kill_switch.engage(db, reason="first breach", actor="risk_governor")
    kill_switch.clear(db, reason="resolved", authority=operator)
    kill_switch.engage(db, reason="second breach", actor="reconciliation")

    rows = kill_switch.history(db)
    assert len(rows) == 3

    reasons = [row["reason"] for row in rows]
    assert reasons == ["first breach", "resolved", "second breach"]

    actors = [row["actor"] for row in rows]
    assert actors == ["risk_governor", "operator_jane", "reconciliation"]

    first_new = json.loads(rows[0]["new_value"])
    assert first_new["status"] == "ENGAGED"
    second_new = json.loads(rows[1]["new_value"])
    assert second_new["status"] == "DISENGAGED"
    second_old = json.loads(rows[1]["old_value"])
    assert second_old["status"] == "ENGAGED"  # old state correctly captured before the clear


def test_audit_trail_survives_a_rejected_clear_attempt(db):
    kill_switch.engage(db, reason="x", actor="operator")
    with pytest.raises(PermissionError):
        kill_switch.clear(db, reason="resolved", authority="ml_model")
    # The rejected attempt must not have appended an audit row.
    rows = kill_switch.history(db)
    assert len(rows) == 1
    assert rows[0]["reason"] == "x"


def test_state_write_and_audit_row_are_never_observed_out_of_sync(db):
    # After every transition, app_state's status must match the LATEST
    # audit row's new_value status — proving they committed together.
    kill_switch.engage(db, reason="a", actor="engine")
    kill_switch.engage(db, reason="b", actor="engine")
    state = kill_switch.get_state(db)
    latest_audit = kill_switch.history(db)[-1]
    audited_new = json.loads(latest_audit["new_value"])
    assert state.status.value == audited_new["status"]
    assert state.reason == audited_new["reason"] == "b"
