"""Authoritative MT5 trade-server return-code interpreter (execution-safety
review finding #2).

Reducing `order_send()`'s result to "10009 = success, everything else =
REJECTED" is unsafe: `TRADE_RETCODE_DONE_PARTIAL` (10010) represents
REAL broker exposure at a partial volume, and `TRADE_RETCODE_PLACED`
(10008) represents a resting order, not a rejection — misclassifying
either as REJECTED could cause the caller to believe no exposure exists
when it does, or to treat a live pending order as gone.

This module is the ONE place that owns the raw-integer-to-meaning
mapping — `execution/service.py` and `execution/close.py` both go
through `interpret_retcode()` rather than comparing `result.retcode`
against a hardcoded literal themselves.

Values below are MT5's real `ENUM_TRADE_RETCODE` constants. An
unrecognized retcode (a broker/SDK version reporting something not in
this table) is treated conservatively as `TIMEOUT_OR_ERROR` ->
`OrderState.UNKNOWN` — never guessed into REJECTED or DONE.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adaptive_scalper.execution.state_machine import OrderState

REQUOTE = 10004
REJECT = 10006
CANCEL = 10007
PLACED = 10008
DONE = 10009
DONE_PARTIAL = 10010
ERROR = 10011
TIMEOUT = 10012
INVALID = 10013
INVALID_VOLUME = 10014
INVALID_PRICE = 10015
INVALID_STOPS = 10016
TRADE_DISABLED = 10017
MARKET_CLOSED = 10018
NO_MONEY = 10019
PRICE_CHANGED = 10020
PRICE_OFF = 10021
INVALID_EXPIRATION = 10022
ORDER_CHANGED = 10023
TOO_MANY_REQUESTS = 10024
NO_CHANGES = 10025
SERVER_DISABLES_AT = 10026
CLIENT_DISABLES_AT = 10027
LOCKED = 10028
FROZEN = 10029
INVALID_FILL = 10030
CONNECTION = 10031
ONLY_REAL = 10032
LIMIT_ORDERS = 10033
LIMIT_VOLUME = 10034
INVALID_ORDER = 10035
POSITION_CLOSED = 10036
INVALID_CLOSE_VOLUME = 10038
CLOSE_ORDER_EXIST = 10039
LIMIT_POSITIONS = 10040
REJECT_CANCEL = 10041
LONG_ONLY = 10042
SHORT_ONLY = 10043
CLOSE_ONLY = 10044
FIFO_CLOSE = 10045


class RetcodeCategory(str, Enum):
    DONE = "DONE"                        # full fill/execution acknowledged
    DONE_PARTIAL = "DONE_PARTIAL"        # REAL partial exposure -- never REJECTED
    PLACED = "PLACED"                    # resting/pending, not filled, not rejected
    REQUOTE = "REQUOTE"                  # broker offered a different price; nothing filled
    REJECTED = "REJECTED"                # positive proof of rejection, no exposure created
    CANCELLED = "CANCELLED"
    INVALID_REQUEST = "INVALID_REQUEST"  # malformed/unsupported request, positively refused
    BROKER_CONSTRAINT = "BROKER_CONSTRAINT"  # account/market/broker-side constraint, positively refused
    TIMEOUT_OR_ERROR = "TIMEOUT_OR_ERROR"    # ambiguous transport outcome -- never blindly resent


_RETCODE_CATEGORY: dict[int, RetcodeCategory] = {
    DONE: RetcodeCategory.DONE,
    DONE_PARTIAL: RetcodeCategory.DONE_PARTIAL,
    PLACED: RetcodeCategory.PLACED,
    REQUOTE: RetcodeCategory.REQUOTE,
    PRICE_CHANGED: RetcodeCategory.REQUOTE,
    REJECT: RetcodeCategory.REJECTED,
    REJECT_CANCEL: RetcodeCategory.REJECTED,
    POSITION_CLOSED: RetcodeCategory.REJECTED,
    CANCEL: RetcodeCategory.CANCELLED,
    ERROR: RetcodeCategory.TIMEOUT_OR_ERROR,
    TIMEOUT: RetcodeCategory.TIMEOUT_OR_ERROR,
    CONNECTION: RetcodeCategory.TIMEOUT_OR_ERROR,
    ORDER_CHANGED: RetcodeCategory.TIMEOUT_OR_ERROR,
    TOO_MANY_REQUESTS: RetcodeCategory.TIMEOUT_OR_ERROR,
    INVALID: RetcodeCategory.INVALID_REQUEST,
    INVALID_VOLUME: RetcodeCategory.INVALID_REQUEST,
    INVALID_PRICE: RetcodeCategory.INVALID_REQUEST,
    INVALID_STOPS: RetcodeCategory.INVALID_REQUEST,
    INVALID_EXPIRATION: RetcodeCategory.INVALID_REQUEST,
    INVALID_ORDER: RetcodeCategory.INVALID_REQUEST,
    INVALID_FILL: RetcodeCategory.INVALID_REQUEST,
    INVALID_CLOSE_VOLUME: RetcodeCategory.INVALID_REQUEST,
    TRADE_DISABLED: RetcodeCategory.BROKER_CONSTRAINT,
    MARKET_CLOSED: RetcodeCategory.BROKER_CONSTRAINT,
    NO_MONEY: RetcodeCategory.BROKER_CONSTRAINT,
    PRICE_OFF: RetcodeCategory.BROKER_CONSTRAINT,
    NO_CHANGES: RetcodeCategory.BROKER_CONSTRAINT,
    SERVER_DISABLES_AT: RetcodeCategory.BROKER_CONSTRAINT,
    CLIENT_DISABLES_AT: RetcodeCategory.BROKER_CONSTRAINT,
    LOCKED: RetcodeCategory.BROKER_CONSTRAINT,
    FROZEN: RetcodeCategory.BROKER_CONSTRAINT,
    ONLY_REAL: RetcodeCategory.BROKER_CONSTRAINT,
    LIMIT_ORDERS: RetcodeCategory.BROKER_CONSTRAINT,
    LIMIT_VOLUME: RetcodeCategory.BROKER_CONSTRAINT,
    CLOSE_ORDER_EXIST: RetcodeCategory.BROKER_CONSTRAINT,
    LIMIT_POSITIONS: RetcodeCategory.BROKER_CONSTRAINT,
    LONG_ONLY: RetcodeCategory.BROKER_CONSTRAINT,
    SHORT_ONLY: RetcodeCategory.BROKER_CONSTRAINT,
    CLOSE_ONLY: RetcodeCategory.BROKER_CONSTRAINT,
    FIFO_CLOSE: RetcodeCategory.BROKER_CONSTRAINT,
}

# Category -> the OrderState a SUBMITTED order transitions to. DONE
# deliberately maps to ACCEPTED, not FILLED -- broker acknowledgement of
# a full DONE is still not a fill; the caller must resolve the true
# position via execution.position_resolution before calling it FILLED.
_CATEGORY_TO_STATE: dict[RetcodeCategory, OrderState] = {
    RetcodeCategory.DONE: OrderState.ACCEPTED,
    RetcodeCategory.DONE_PARTIAL: OrderState.PARTIAL,
    RetcodeCategory.PLACED: OrderState.RESTING,
    RetcodeCategory.REQUOTE: OrderState.REJECTED,
    RetcodeCategory.REJECTED: OrderState.REJECTED,
    RetcodeCategory.CANCELLED: OrderState.CANCELLED,
    RetcodeCategory.INVALID_REQUEST: OrderState.REJECTED,
    RetcodeCategory.BROKER_CONSTRAINT: OrderState.REJECTED,
    RetcodeCategory.TIMEOUT_OR_ERROR: OrderState.UNKNOWN,
}

_DEFINITIVE_REJECTION_CATEGORIES = frozenset({
    RetcodeCategory.REQUOTE, RetcodeCategory.REJECTED,
    RetcodeCategory.INVALID_REQUEST, RetcodeCategory.BROKER_CONSTRAINT,
})


@dataclass(frozen=True)
class RetcodeInterpretation:
    retcode: int
    category: RetcodeCategory
    order_state: OrderState
    is_definitive_rejection: bool
    detail: str


def interpret_retcode(retcode: int) -> RetcodeInterpretation:
    category = _RETCODE_CATEGORY.get(retcode)
    if category is None:
        return RetcodeInterpretation(
            retcode, RetcodeCategory.TIMEOUT_OR_ERROR, OrderState.UNKNOWN, False,
            f"unrecognized retcode={retcode} -- treated conservatively as UNKNOWN, never guessed into "
            f"REJECTED or DONE, never blindly resent",
        )
    return RetcodeInterpretation(
        retcode, category, _CATEGORY_TO_STATE[category], category in _DEFINITIVE_REJECTION_CATEGORIES,
        f"retcode={retcode} -> category={category.value} -> {_CATEGORY_TO_STATE[category].value}",
    )
