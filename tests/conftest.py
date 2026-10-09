"""Suite-wide guard: no offline test may reach a real MetaTrader 5 terminal.

On a machine with the `MetaTrader5` package installed, `mt5.initialize()`
with no arguments LAUNCHES the installed terminal and attaches to whatever
account it last used. Tests that exercise broker-facing CLI or runtime
paths "without MT5" were only offline in the cloud because the import
failed there; on the Windows laptop they reached the live terminal
(observed 2026-09-25: `symbols`, `reconcile` and `history bootstrap` ran
against the DEMO terminal from the offline suite).

This fixture makes every test behave as in the cloud by making
`Mt5Gateway`'s lazy import raise `Mt5NotAvailableError`. The only
exception is `tests/test_mt5_gateway_live.py`, whose purpose is the live
terminal and which is itself opt-in (`ASN_LIVE_MT5=1`).
"""

from __future__ import annotations

import pytest

LIVE_MT5_TEST_MODULES = frozenset({"test_mt5_gateway_live"})


@pytest.fixture(autouse=True)
def _block_live_metatrader5(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] in LIVE_MT5_TEST_MODULES:
        yield
        return
    import adaptive_scalper.gateway.mt5_gateway as mt5_module

    def _blocked():
        raise mt5_module.Mt5NotAvailableError(
            "MetaTrader5 is blocked in the offline test suite (tests/conftest.py); "
            "live-terminal tests live in tests/test_mt5_gateway_live.py"
        )

    monkeypatch.setattr(mt5_module, "_import_mt5", _blocked)
    yield


@pytest.fixture(autouse=True)
def _test_evidence_freeze_date(monkeypatch):
    """Fixture certificates carry 1970 evidence intervals (runtime fakes run
    on 2023 clocks). For the test session ONLY the research-freeze date of
    CURRENT_PROTOCOL is relaxed -- every other preregistered threshold is
    unchanged. Production has no such hook: the executable API takes no
    protocol, and test_validation_certificate.py checks the production
    value in a clean interpreter."""
    import dataclasses

    from adaptive_scalper.validation import certificate

    monkeypatch.setattr(certificate, "CURRENT_PROTOCOL",
                        dataclasses.replace(certificate.PREREGISTERED_PROTOCOL, min_evidence_start_utc=0))
    yield
