"""Tests for SynchronizedGateway (external review #4: MT5 concurrency).

Verifies both plain delegation (values pass through correctly) and actual
mutual exclusion under real concurrent threads — a delegation-only test
would not catch a broken/missing lock.
"""

from __future__ import annotations

import threading
import time

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.synchronized_gateway import SynchronizedGateway
from adaptive_scalper.gateway.types import AccountSnapshot, TradeMode


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        login=1, trade_mode=TradeMode.DEMO, balance=1000.0, equity=1000.0, margin_free=1000.0,
        currency="USD", server="Test-Server", company="Test", trade_allowed=True, trade_expert=True,
    )


def test_delegates_calls_and_return_values():
    inner = FakeGateway(account=_account())
    gw = SynchronizedGateway(inner)
    assert gw.initialize() is True
    assert gw.account_info() == _account()
    gw.shutdown()


class _ConcurrencyProbeGateway:
    """A minimal gateway stand-in whose only real method (symbol_info_tick)
    detects concurrent entry: it increments a shared counter, sleeps
    briefly, then checks whether the counter exceeded 1 while it slept.
    Under a working lock, no two calls ever overlap, so the counter never
    exceeds 1. Without a lock, concurrent threads would overlap and the
    counter would exceed 1 at least once across many concurrent calls."""

    def __init__(self) -> None:
        self._active = 0
        self._max_active = 0
        self._guard = threading.Lock()  # protects the counters themselves, not the "business" call

    def symbol_info_tick(self, name: str):
        with self._guard:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
        time.sleep(0.01)
        with self._guard:
            self._active -= 1
        return None

    def last_error(self):
        return (1, "Success")


def test_serializes_concurrent_calls_across_threads():
    probe = _ConcurrencyProbeGateway()
    gw = SynchronizedGateway(probe)

    def worker():
        for _ in range(5):
            gw.symbol_info_tick("XAUUSD")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert probe._max_active == 1, (
        f"expected calls to serialize (max concurrent = 1), observed {probe._max_active} "
        "concurrent calls — the lock did not prevent overlap"
    )


def test_unsynchronized_baseline_would_show_overlap():
    """Sanity check for the probe itself: calling the UNWRAPPED gateway
    directly (no SynchronizedGateway) from multiple threads should be able
    to show overlap, proving the probe can actually detect a missing lock
    rather than trivially always reporting max_active == 1."""
    probe = _ConcurrencyProbeGateway()

    def worker():
        for _ in range(5):
            probe.symbol_info_tick("XAUUSD")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert probe._max_active > 1, "probe never observed overlap even unsynchronized — probe is not sensitive enough"
