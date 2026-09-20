"""Tests for gateway.broker_constraints (execution-safety review finding #7)."""

from __future__ import annotations

from adaptive_scalper.gateway.broker_constraints import FILLING_FOK, FILLING_IOC, derive_filling_type


def test_prefers_ioc_when_both_supported():
    assert derive_filling_type(0b11) == FILLING_IOC


def test_falls_back_to_fok_when_ioc_unsupported():
    assert derive_filling_type(0b01) == FILLING_FOK  # only FOK bit set


def test_returns_ioc_when_only_ioc_supported():
    assert derive_filling_type(0b10) == FILLING_IOC


def test_returns_none_when_neither_supported():
    assert derive_filling_type(0b00) is None


def test_prefer_fok_explicit():
    assert derive_filling_type(0b11, prefer=FILLING_FOK) == FILLING_FOK
