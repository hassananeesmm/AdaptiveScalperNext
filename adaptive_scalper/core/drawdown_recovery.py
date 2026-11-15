"""Explicit human recovery with immutable policy evidence and no rebaselining.

There is deliberately no runtime caller. An initial policy must document the
flat anchor and historical peaks. Once accepted its accounting anchor cannot
be changed here. Recovery retains every peak and the separate kill switch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from adaptive_scalper.core.kill_switch import KillSwitchStatus, get_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.close_requests import has_unresolved_close
from adaptive_scalper.execution.reconciliation import (
    get_open_positions,
    get_unresolved_incidents,
)
from adaptive_scalper.execution.store import get_active_orders
from adaptive_scalper.risk.project_drawdown import (
    DrawdownEvidenceError,
    _append,
    evaluate,
    known_deals,
    latest,
    retain_deals,
)


def recover(conn, *, authority, policy, evidence, approval_path, reason, acknowledge_prior_breach, now):
    if not isinstance(authority, OperatorAuthority) or not authority.operator_id.strip():
        raise DrawdownEvidenceError('explicit human OperatorAuthority required')
    if acknowledge_prior_breach is not True or len(reason.strip()) < 20:
        raise DrawdownEvidenceError('prior breach acknowledgement and documented reason required')
    approval_bytes = Path(approval_path).read_bytes()
    approval = json.loads(approval_bytes)
    if (approval.get('policy') != asdict(policy) or approval.get('approved_by') != authority.operator_id
            or approval.get('decision') != 'APPROVE_DRAWDOWN_RECOVERY'
            or not str(approval.get('historical_evidence_reference', '')).strip()):
        raise DrawdownEvidenceError('approval must bind operator, exact policy and historical evidence reference')
    conn.execute('SAVEPOINT drawdown_human_recovery')
    try:
        row = latest(conn)
        if row is None:
            raise DrawdownEvidenceError('missing audit history cannot be recovered')
        previous = json.loads(row['state_json'])
        if previous.get('policy') not in (None, asdict(policy)):
            raise DrawdownEvidenceError('recovery cannot change account, accounting anchor or historical peaks')
        if 'policy' not in previous:
            previous['project_peak'] = policy.historical_project_peak
            previous['account_peak'] = policy.historical_account_peak
        if get_state(conn).status != KillSwitchStatus.ENGAGED:
            raise DrawdownEvidenceError('kill switch must remain ENGAGED during recovery')
        if (get_open_positions(conn) or get_active_orders(conn) or has_unresolved_close(conn)
                or get_unresolved_incidents(conn)):
            raise DrawdownEvidenceError('local book must be flat with no unresolved execution')
        if evidence is None or evidence.positions or evidence.orders:
            raise DrawdownEvidenceError('fresh broker flat-book evidence required')
        legacy = conn.execute(
            'SELECT MAX(peak_equity) FROM peak_equity_history WHERE account_login=? AND account_server=?',
            (policy.login, policy.server)).fetchone()[0]
        if legacy is not None:
            previous['account_peak'] = max(previous.get('account_peak', 0), legacy)
        previous['deals'] = known_deals(conn)
        status, state = evaluate(policy, evidence, previous, now=now)
        if status.blocked:
            raise DrawdownEvidenceError('recovery refused: ' + status.reason)
        retain_deals(conn, state)
        _append(conn, event='OPERATOR_RECOVERY', policy=policy, state=state, latched=False,
                reason=reason, now=now, actor=authority.operator_id,
                digest=hashlib.sha256(approval_bytes).hexdigest())
    except BaseException:
        conn.execute('ROLLBACK TO drawdown_human_recovery')
        raise
    finally:
        conn.execute('RELEASE drawdown_human_recovery')
    return status
