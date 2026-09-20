"""Order execution: state machine, idempotency, UNKNOWN resolution, and
reconciliation (directive sections 29-31).

`order_send` does not exist anywhere in this codebase yet. This package
is the safety layer that must exist and be tested BEFORE it is ever
added — see `state_machine.py`, `store.py`, `unknown.py`, and
`reconciliation.py`.
"""
