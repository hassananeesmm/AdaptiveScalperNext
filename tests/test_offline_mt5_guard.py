"""Regression: the offline suite can never reach a real MT5 terminal.

On the Windows laptop (MetaTrader5 installed) the broker-facing CLI tests
launched the live terminal because their skip guard checked for a module
global that the lazy import never creates. `tests/conftest.py` now blocks
the import for every offline test; these tests prove it holds on any
machine, including one where the package is installed.
"""

from __future__ import annotations

import pathlib

import pytest

from adaptive_scalper.gateway.factory import create_live_gateway
from adaptive_scalper.gateway.mt5_gateway import Mt5NotAvailableError


def test_the_live_gateway_cannot_initialize_inside_the_offline_suite():
    gateway = create_live_gateway()
    with pytest.raises(Mt5NotAvailableError, match="blocked in the offline test suite"):
        gateway.initialize()


def test_the_cli_broker_path_fails_closed_inside_the_offline_suite():
    from adaptive_scalper.cli.common import CliError, open_gateway

    with pytest.raises(CliError, match="MetaTrader5 unavailable"):
        open_gateway(require_demo=False)


def test_the_live_mt5_tests_are_opt_in():
    source = (pathlib.Path(__file__).parent / "test_mt5_gateway_live.py").read_text(encoding="utf-8")
    assert 'os.environ.get("ASN_LIVE_MT5") != "1"' in source
    script = (pathlib.Path(__file__).parent.parent / "scripts" / "windows_verify.ps1").read_text(encoding="utf-8")
    assert '$env:ASN_LIVE_MT5 = "1"' in script
