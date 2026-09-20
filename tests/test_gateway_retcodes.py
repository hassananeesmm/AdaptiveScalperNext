"""Tests for the authoritative MT5 retcode interpreter (execution-safety
review finding #2)."""

from __future__ import annotations

from adaptive_scalper.execution.state_machine import OrderState
from adaptive_scalper.gateway.retcodes import (
    CANCEL,
    DONE,
    DONE_PARTIAL,
    ERROR,
    INVALID_VOLUME,
    MARKET_CLOSED,
    NO_MONEY,
    PLACED,
    REJECT,
    REQUOTE,
    TIMEOUT,
    interpret_retcode,
)


def test_done_maps_to_accepted_not_filled():
    result = interpret_retcode(DONE)
    assert result.order_state == OrderState.ACCEPTED
    assert result.is_definitive_rejection is False


def test_done_partial_maps_to_partial_never_rejected():
    result = interpret_retcode(DONE_PARTIAL)
    assert result.order_state == OrderState.PARTIAL
    assert result.is_definitive_rejection is False


def test_placed_maps_to_resting_never_rejected():
    result = interpret_retcode(PLACED)
    assert result.order_state == OrderState.RESTING
    assert result.is_definitive_rejection is False


def test_reject_maps_to_rejected_with_definitive_flag():
    result = interpret_retcode(REJECT)
    assert result.order_state == OrderState.REJECTED
    assert result.is_definitive_rejection is True


def test_requote_maps_to_rejected_no_exposure():
    result = interpret_retcode(REQUOTE)
    assert result.order_state == OrderState.REJECTED
    assert result.is_definitive_rejection is True


def test_cancel_maps_to_cancelled():
    result = interpret_retcode(CANCEL)
    assert result.order_state == OrderState.CANCELLED


def test_timeout_maps_to_unknown_never_rejected():
    result = interpret_retcode(TIMEOUT)
    assert result.order_state == OrderState.UNKNOWN
    assert result.is_definitive_rejection is False


def test_error_maps_to_unknown_never_rejected():
    result = interpret_retcode(ERROR)
    assert result.order_state == OrderState.UNKNOWN
    assert result.is_definitive_rejection is False


def test_invalid_volume_is_a_definitive_rejection():
    result = interpret_retcode(INVALID_VOLUME)
    assert result.order_state == OrderState.REJECTED
    assert result.is_definitive_rejection is True


def test_market_closed_is_a_definitive_rejection():
    result = interpret_retcode(MARKET_CLOSED)
    assert result.order_state == OrderState.REJECTED
    assert result.is_definitive_rejection is True


def test_no_money_is_a_definitive_rejection():
    result = interpret_retcode(NO_MONEY)
    assert result.order_state == OrderState.REJECTED
    assert result.is_definitive_rejection is True


def test_unrecognized_retcode_is_conservatively_unknown():
    result = interpret_retcode(999999)
    assert result.order_state == OrderState.UNKNOWN
    assert result.is_definitive_rejection is False
    assert "unrecognized" in result.detail


def test_10008_cannot_become_rejected():
    assert interpret_retcode(10008).order_state != OrderState.REJECTED


def test_10009_cannot_become_rejected():
    assert interpret_retcode(10009).order_state != OrderState.REJECTED


def test_10010_cannot_become_rejected():
    assert interpret_retcode(10010).order_state != OrderState.REJECTED


def test_timeout_cannot_become_rejected_or_done():
    result = interpret_retcode(TIMEOUT)
    assert result.order_state not in (OrderState.REJECTED, OrderState.ACCEPTED, OrderState.PARTIAL)
