"""Windows launchers and release scripts (completion directive Phases
10-11). The cloud cannot execute .bat/.ps1 files, so this verifies them
statically: every CLI invocation parses against the real CLI parser, the
safety banner is exact, STOP TRADING only engages, and no launcher or
script can bootstrap/clear the kill switch, start LIVE trading or
close positions. Running them is a LOCAL_MT5_HANDOFF.md item.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from adaptive_scalper.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ["SETUP.bat", "START PAPER.bat", "START DEMO.bat", "START DASHBOARD.bat", "RUN BACKTEST.bat",
             "STOP TRADING.bat", "START PAPER + DASHBOARD.bat", "START DEMO + DASHBOARD.bat"]
SCRIPTS = ["scripts/windows_verify.ps1", "scripts/build_release.ps1", "scripts/release_smoke_test.ps1"]
CLI_CALL = re.compile(r"-m adaptive_scalper\.cli (?:--config \$Cfg )?(.+)$")


def _text(rel: str) -> str:
    return (ROOT / rel).read_bytes().decode("utf-8")


def _cli_invocations(rel: str) -> list[list[str]]:
    calls = []
    for line in _text(rel).splitlines():
        if line.strip().lower().startswith("echo"):
            continue  # printed advice, not executed
        match = CLI_CALL.search(line)
        if match:
            args = match.group(1).split(";")[0].split("|")[0].strip().removesuffix("}").strip()
            calls.append(shlex.split(args.replace("%SYMBOL%", "XAUUSD").replace("%START%", "2024-01-01")
                                     .replace("%END%", "2024-06-01")))
    return calls


@pytest.mark.parametrize("rel", LAUNCHERS + SCRIPTS)
def test_files_exist_with_crlf_line_endings(rel):
    data = (ROOT / rel).read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), rel


@pytest.mark.parametrize("rel", LAUNCHERS + SCRIPTS)
def test_every_cli_invocation_parses(rel):
    parser = build_parser()
    for args in _cli_invocations(rel):
        parsed = parser.parse_args(args)
        assert callable(parsed.func), (rel, args)


@pytest.mark.parametrize("rel", LAUNCHERS + SCRIPTS)
def test_nothing_executes_a_kill_switch_bootstrap_or_clear(rel):
    for args in _cli_invocations(rel):
        if args[:1] == ["kill-switch"]:
            assert args[1] in ("engage", "status"), (rel, args)


@pytest.mark.parametrize("rel", LAUNCHERS + SCRIPTS)
def test_no_live_mode_order_or_position_closing(rel):
    text = _text(rel).lower()
    for forbidden in ("order_send", "live trading", "--live", "real account", "close_position", "positions_close"):
        assert forbidden not in text, (rel, forbidden)


def test_start_demo_prints_the_exact_safety_banner():
    text = _text("START DEMO.bat")
    for line in ("REAL-MONEY EXECUTION: DISABLED", "DEMO ACCOUNT REQUIRED", "NEW ENTRIES REQUIRE KILL SWITCH DISENGAGED"):
        assert f"echo  {line}" in text
    assert _cli_invocations("START DEMO.bat") == [["demo"]]


def test_stop_trading_only_engages_the_kill_switch():
    calls = _cli_invocations("STOP TRADING.bat")
    assert calls[0][:2] == ["kill-switch", "engage"] and calls[1] == ["kill-switch", "status"]
    assert len(calls) == 2
    assert "NOT closed" in _text("STOP TRADING.bat")


def test_paper_and_dashboard_launchers():
    assert _cli_invocations("START PAPER.bat") == [["paper"]]
    (dash,) = _cli_invocations("START DASHBOARD.bat")
    assert dash[0] == "dashboard" and dash[dash.index("--host") + 1] == "127.0.0.1"


def test_combined_launchers_check_prerequisites_then_run_one_runtime_beside_the_dashboard():
    paper = _cli_invocations("START PAPER + DASHBOARD.bat")
    demo = _cli_invocations("START DEMO + DASHBOARD.bat")
    assert paper == [["doctor"], ["kill-switch", "status"], ["paper"]]
    assert demo == [["doctor"], ["reconcile"], ["kill-switch", "status"], ["demo"]]
    for rel in ("START PAPER + DASHBOARD.bat", "START DEMO + DASHBOARD.bat"):
        text = _text(rel)
        # doctor must pass before anything starts, and a failure is shown, not hidden
        assert text.index("errorlevel 1") < text.index('start "Adaptive Scalper Next - Dashboard"')
        assert 'cmd /c "START DASHBOARD.bat"' in text        # the observer-only dashboard, 127.0.0.1
        assert text.count("-m adaptive_scalper.cli paper") + text.count("-m adaptive_scalper.cli demo") == 1
    banner = _text("START DEMO + DASHBOARD.bat")
    for line in ("REAL-MONEY EXECUTION: DISABLED", "DEMO ACCOUNT REQUIRED", "NEW ENTRIES REQUIRE KILL SWITCH DISENGAGED"):
        assert line in banner


def test_the_release_is_built_from_tracked_files_only():
    text = _text("scripts/build_release.ps1")
    assert "git archive" in text and "git status --porcelain" in text
    for pattern in ('"*.sqlite3"', '"*.db"', '".env"', '"secrets*"', '"credentials*"'):
        assert pattern in text


def test_the_windows_verifier_is_read_only_against_the_broker():
    text = _text("scripts/windows_verify.ps1")
    assert "test_mt5_gateway_live.py" in text and "doctor" in text
    assert [c[0] for c in _cli_invocations("scripts/windows_verify.ps1")] == [
        "doctor", "symbols", "okf", "kill-switch"]
