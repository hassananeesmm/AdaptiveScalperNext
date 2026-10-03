"""Tests for the strategy selector: combines candidate signals by
cost-adjusted expected net edge, not raw confidence alone.
"""

from __future__ import annotations

import pytest

from adaptive_scalper.costs.edge_evidence import LEGACY_V1_RAW_SCORE_EVIDENCE
from adaptive_scalper.costs.model import estimate_cost
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.selector.selector import (
    REJECTED_INSUFFICIENT_EDGE,
    REJECTED_LOW_CONFIDENCE,
    REJECTED_RETIRED,
    REJECTED_EDGE_UNVALIDATED,
    REJECTED_UNKNOWN_COST,
    select_and_journal_proposal,
    select_proposal,
)
from adaptive_scalper.strategies.base import StrategySignal
from edge_fixtures import FixtureValidatedProvider


def _signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_key="momentum_continuation", strategy_version=1, canonical_symbol="XAUUSD",
        direction="BUY", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0,
        expected_duration_seconds=300, entry_method="MARKET", regime="TRENDING_UP",
        rationale="test", feature_schema_version=1, data_timestamp=1000,
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _cheap_cost():
    return estimate_cost(
        spread_price=0.05, commission_price_equivalent=0.0,
        expected_slippage_price=0.0, swap_price_equivalent=0.0, uncertainty_margin_pct=0.0,
    )


def _expensive_cost():
    return estimate_cost(
        spread_price=5.0, commission_price_equivalent=0.0,
        expected_slippage_price=0.0, swap_price_equivalent=0.0, uncertainty_margin_pct=0.0,
    )


# The ranking tests below exercise V1 selection mechanics, so they replay the
# frozen V1 edge formula explicitly; the production default (no validated
# evidence) is covered by the FLAT tests at the end of this module.
def _select(*args, **kwargs):
    kwargs.setdefault("edge_evidence", LEGACY_V1_RAW_SCORE_EVIDENCE)
    return select_proposal(*args, **kwargs)


def _select_journal(*args, **kwargs):
    kwargs.setdefault("edge_evidence", LEGACY_V1_RAW_SCORE_EVIDENCE)
    return select_and_journal_proposal(*args, **kwargs)


# --------------------------------------------------------------------------
# Core selection logic
# --------------------------------------------------------------------------

def test_flat_when_no_candidates():
    result = _select([], {})
    assert result.selected is None
    assert "FLAT" in result.reason


def test_selects_the_single_qualifying_candidate():
    sig = _signal(raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)  # gross=0.8
    result = _select([sig], {"XAUUSD": _cheap_cost()})
    assert result.selected is sig


def test_prefers_higher_net_edge_over_higher_raw_confidence():
    # High confidence but poor risk/reward -> lower net edge.
    high_conf_poor_rr = _signal(
        strategy_key="momentum_continuation", raw_confidence=0.9, stop_distance=2.0, target_distance=1.0,
    )  # EV = 0.9*1.0 - 0.1*2.0 = 0.7
    # Lower confidence but excellent risk/reward -> higher net edge.
    low_conf_good_rr = _signal(
        strategy_key="range_breakout", raw_confidence=0.4, stop_distance=1.0, target_distance=5.0,
    )  # EV = 0.4*5.0 - 0.6*1.0 = 1.4

    result = _select([high_conf_poor_rr, low_conf_good_rr], {"XAUUSD": _cheap_cost()})
    assert result.selected is low_conf_good_rr
    assert result.selected.raw_confidence < 0.9  # explicitly NOT the higher-confidence one


def test_rejects_retired_strategy_key_even_if_otherwise_excellent():
    retired_signal = _signal(strategy_key="failed_breakout_fade", raw_confidence=0.99, target_distance=10.0)
    result = _select([retired_signal], {"XAUUSD": _cheap_cost()})
    assert result.selected is None
    assert result.candidates[0].rejection_reason == REJECTED_RETIRED


def test_rejects_below_minimum_confidence():
    sig = _signal(raw_confidence=0.3)
    result = _select([sig], {"XAUUSD": _cheap_cost()}, min_raw_confidence=0.5)
    assert result.selected is None
    assert result.candidates[0].rejection_reason == REJECTED_LOW_CONFIDENCE


def test_rejects_when_cost_unknown_for_symbol():
    sig = _signal(canonical_symbol="GBPJPY")
    result = _select([sig], {"XAUUSD": _cheap_cost()})  # no GBPJPY entry
    assert result.selected is None
    assert result.candidates[0].rejection_reason == REJECTED_UNKNOWN_COST


def test_rejects_when_net_edge_insufficient():
    sig = _signal(raw_confidence=0.55, stop_distance=1.0, target_distance=1.1)  # small gross edge
    result = _select([sig], {"XAUUSD": _expensive_cost()})
    assert result.selected is None
    assert result.candidates[0].rejection_reason == REJECTED_INSUFFICIENT_EDGE


def test_cost_is_looked_up_per_symbol_independently():
    xau_sig = _signal(canonical_symbol="XAUUSD", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    gbp_sig = _signal(canonical_symbol="GBPJPY", strategy_key="range_breakout", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    result = _select(
        [xau_sig, gbp_sig], {"XAUUSD": _cheap_cost(), "GBPJPY": _expensive_cost()},
    )
    # XAUUSD's cheap cost lets it clear the bar; GBPJPY's expensive cost doesn't.
    assert result.selected is xau_sig


def test_multiple_qualifying_candidates_all_appear_in_evaluations():
    a = _signal(strategy_key="momentum_continuation", raw_confidence=0.6, stop_distance=1.0, target_distance=2.0)
    b = _signal(strategy_key="range_breakout", raw_confidence=0.5, stop_distance=1.0, target_distance=1.5)
    result = _select([a, b], {"XAUUSD": _cheap_cost()})
    assert len(result.candidates) == 2
    assert result.selected in (a, b)


# --------------------------------------------------------------------------
# select_and_journal_proposal
# --------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_journals_proposal_created_for_the_winner(db):
    sig = _signal()
    result = _select_journal(db, [sig], ["chain-1"], {"XAUUSD": _cheap_cost()}, now_utc=1000)
    assert result.selected is sig
    events = get_chain_events(db, "chain-1")
    assert events[0].event_type == "PROPOSAL_CREATED"


def test_journals_signal_rejected_for_a_filtered_out_candidate(db):
    retired = _signal(strategy_key="support_resistance_reaction")
    _select_journal(db, [retired], ["chain-1"], {"XAUUSD": _cheap_cost()}, now_utc=1000)
    events = get_chain_events(db, "chain-1")
    assert events[0].event_type == "SIGNAL_REJECTED"
    assert events[0].payload["reason"] == REJECTED_RETIRED


def test_journals_proposal_rejected_for_a_qualifying_but_unselected_candidate(db):
    winner = _signal(strategy_key="range_breakout", raw_confidence=0.5, stop_distance=1.0, target_distance=5.0)
    loser = _signal(strategy_key="momentum_continuation", raw_confidence=0.9, stop_distance=2.0, target_distance=1.0)
    _select_journal(
        db, [winner, loser], ["chain-winner", "chain-loser"], {"XAUUSD": _cheap_cost()}, now_utc=1000
    )
    winner_events = get_chain_events(db, "chain-winner")
    loser_events = get_chain_events(db, "chain-loser")
    assert winner_events[0].event_type == "PROPOSAL_CREATED"
    assert loser_events[0].event_type == "PROPOSAL_REJECTED"


def test_mismatched_chain_keys_length_raises(db):
    with pytest.raises(ValueError):
        _select_journal(db, [_signal(), _signal()], ["only-one-key"], {"XAUUSD": _cheap_cost()})


# --------------------------------------------------------------------------
# Issue #6: raw score is not a probability -- default is FLAT
# --------------------------------------------------------------------------

def test_default_selector_is_flat_without_validated_edge_evidence():
    strong = _signal(raw_confidence=1.0, stop_distance=1.0, target_distance=10.0)
    result = select_proposal([strong], {"XAUUSD": _cheap_cost()})
    assert result.selected is None
    assert [e.rejection_reason for e in result.candidates] == [REJECTED_EDGE_UNVALIDATED]


def test_default_journal_records_the_unvalidated_rejection(db):
    select_and_journal_proposal(db, [_signal()], ["chain-1"], {"XAUUSD": _cheap_cost()}, now_utc=1000)
    events = get_chain_events(db, "chain-1")
    assert events[-1].event_type == "SIGNAL_REJECTED"
    assert events[-1].payload["reason"] == REJECTED_EDGE_UNVALIDATED
    assert events[-1].payload["edge_model"] == "NONE"
    assert events[-1].payload["raw_score_is_probability"] is False


def test_legacy_replay_is_numerically_identical_to_the_frozen_v1_selector():
    """The freeze-digest change of selector.py is justified only if V1 is
    reproducible: under LEGACY_V1_RAW_SCORE the selection and every net edge
    equal the frozen formula p*target - (1-p)*stop - cost, p = raw score."""
    import random

    rng = random.Random(20261002)
    cost = _cheap_cost()
    for _ in range(500):
        cands = [_signal(strategy_key=k, raw_confidence=rng.random(), stop_distance=rng.uniform(0.1, 3),
                         target_distance=rng.uniform(0.1, 6), direction=rng.choice(["BUY", "SELL"]))
                 for k in rng.sample(["momentum_continuation", "pullback_continuation", "range_breakout",
                                      "statistical_reversion", "volatility_expansion",
                                      "microstructure_acceleration"], rng.randint(1, 6))]
        result = _select(cands, {"XAUUSD": cost})
        frozen = {id(s): s.raw_confidence * s.target_distance - (1 - s.raw_confidence) * s.stop_distance
                  - cost.total_cost for s in cands}
        for e in result.candidates:
            if e.expected_net_edge is not None:
                assert e.expected_net_edge == pytest.approx(frozen[id(e.signal)])
        qualifying = [s for s in cands if frozen[id(s)] > 0]
        expected = max(qualifying, key=lambda s: frozen[id(s)]) if qualifying else None
        assert result.selected is expected


def test_validated_evidence_ranks_by_realized_payoff_not_configured_target():
    sig = _signal(raw_confidence=0.99, stop_distance=1.0, target_distance=50.0)
    result = select_proposal([sig], {"XAUUSD": _cheap_cost()},
                             edge_evidence=FixtureValidatedProvider(p=0.5, avg_win=0.6, avg_loss=1.0))
    assert result.selected is None  # 0.5*0.6 - 0.5*1.0 < 0 regardless of the 50.0 configured target
    assert result.candidates[0].rejection_reason == REJECTED_INSUFFICIENT_EDGE
