"""Explicit edge-evidence fixtures for tests (issue #6).

Production code has no validated expected-edge evidence, so every executable
gate is FLAT by default. Tests that exercise downstream mechanics need a
proposal to get past the edge gate, and must say so explicitly:

- `v1_replay_config(...)`: a BacktestConfig replaying the frozen V1 formula
  (`edge_model="LEGACY_V1_RAW_SCORE"`) -- for tests of fills, exits,
  pending entries, restart and accounting, whose expected numbers were
  derived under V1. Research replay only, never executable.
- `fixture_validated_evidence(...)`: an EdgeEvidence object marked VALIDATED
  so final-permission / execution-service tests can exercise the gates AFTER
  the edge gate. It is built here, in tests, from an explicit p and payoff;
  nothing in `adaptive_scalper/` constructs VALIDATED evidence
  (tests/test_edge_evidence_isolation.py enforces that). Imported as
  `from edge_fixtures import ...`, like runtime_helpers.
"""

from __future__ import annotations

from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.costs.edge_evidence import (
    EDGE_MODEL_LEGACY_V1_RAW_SCORE,
    EVIDENCE_VALIDATED,
    CalibratedWinProbability,
    EdgeEvidence,
    LifecyclePayoff,
)

LEGACY_V1 = EDGE_MODEL_LEGACY_V1_RAW_SCORE


def v1_replay_config(**overrides) -> BacktestConfig:
    overrides.setdefault("edge_model", LEGACY_V1)
    return BacktestConfig(**overrides)


def fixture_validated_evidence(p: float = 0.6, avg_win: float = 10.0, avg_loss: float = 1.0) -> EdgeEvidence:
    return EdgeEvidence.from_calibration(
        CalibratedWinProbability(p, "TEST_FIXTURE_ONLY", "fixture", 1),
        LifecyclePayoff(avg_win, avg_loss, 1, "TEST_FIXTURE_ONLY"),
        model_id="TEST_FIXTURE_ONLY", status=EVIDENCE_VALIDATED,
    )


class FixtureValidatedProvider:
    """Returns `fixture_validated_evidence()` for every signal (tests only)."""

    model_id = "TEST_FIXTURE_ONLY"

    def __init__(self, **kw) -> None:
        self._evidence = fixture_validated_evidence(**kw)

    def for_signal(self, signal):
        return self._evidence
