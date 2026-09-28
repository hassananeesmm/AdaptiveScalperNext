"""ASN-010 terminal identity: with a pinned `[mt5] terminal_path`, the gateway
refuses any attached terminal whose install folder differs (the laptop has
two MT5 installs). No MT5 needed: the MetaTrader5 module is stubbed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_scalper.config.loader import load_config
from adaptive_scalper.gateway import mt5_gateway
from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway

PINNED = r"C:\Program Files\MetaTrader 5 IC Markets Global\terminal64.exe"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "default.toml"


class _Terminal:
    def __init__(self, attached_folder: str | None, *, init_ok: bool = True) -> None:
        self.attached_folder = attached_folder
        self.init_ok = init_ok
        self.init_calls: list[dict] = []
        self.shutdowns = 0

    def initialize(self, **kwargs):
        self.init_calls.append(kwargs)
        return self.init_ok

    def terminal_info(self):
        return None if self.attached_folder is None else SimpleNamespace(path=self.attached_folder)

    def last_error(self):
        return (-6, "Terminal: Authorization failed")

    def shutdown(self):
        self.shutdowns += 1


def _gateway(monkeypatch, terminal: _Terminal, path: str | None = PINNED) -> Mt5Gateway:
    monkeypatch.setattr(mt5_gateway, "_import_mt5", lambda: terminal)
    return Mt5Gateway("UTC+2/US_DST", terminal_path=path)


@pytest.mark.parametrize("attached", [
    r"C:\Program Files\MetaTrader 5 IC Markets Global",
    r"c:\program files\metatrader 5 ic markets global" + "\\",  # case/trailing separator normalised
])
def test_the_pinned_terminal_is_accepted(monkeypatch, attached):
    terminal = _Terminal(attached)
    assert _gateway(monkeypatch, terminal).initialize() is True
    assert terminal.init_calls == [{"path": PINNED}]
    assert terminal.shutdowns == 0


@pytest.mark.parametrize("attached", [
    r"C:\Program Files\MetaTrader 5",  # the laptop's OTHER install
    r"C:\Program Files\MetaTrader 5 IC Markets Global Copy",
    None,  # terminal_info() unavailable
])
def test_any_other_attached_terminal_is_refused_and_detached(monkeypatch, attached):
    terminal = _Terminal(attached)
    assert _gateway(monkeypatch, terminal).initialize() is False
    assert terminal.shutdowns == 1


def test_a_failed_initialize_of_the_pinned_path_is_refused(monkeypatch):
    terminal = _Terminal(r"C:\Program Files\MetaTrader 5 IC Markets Global", init_ok=False)
    assert _gateway(monkeypatch, terminal).initialize() is False


def test_shipped_configuration_pins_the_ic_markets_terminal():
    assert load_config(DEFAULT_CONFIG).mt5.terminal_path == PINNED
