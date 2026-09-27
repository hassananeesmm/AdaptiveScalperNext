"""Selector study: does ranking by expected net edge pick the better trades?

Inputs are one `IndependentStudy` (research/independent.py): the combined
selector session's `CandidateRecord`s (what the selector knew at each bar
close) and the independent single-strategy sessions over the SAME bars.

The join is causal in one direction only. A candidate is matched to the
trade its OWN strategy's independent session opened from the same bar
close (same strategy, same `signal_time_utc`, same direction). The outcome
is attached AFTER the fact for evaluation; the selector itself never saw
it, and nothing here feeds back into a decision. A candidate is unmatched
when that independent session was not flat at that bar, or its entry was
rejected at fill -- unmatched candidates are counted, never guessed.

Expected quantities are expressed in R (price edge / the signal's own stop
distance), so strategies with different ATR multiples compare on one scale:

- `expected_net_r` = expected_net_edge_price / stop_distance
- `expected_cost_r` = estimated_cost_price / stop_distance
- `stated_p` = raw_confidence (taken at face value by the selector)
"""

from __future__ import annotations

from collections import Counter, defaultdict

from adaptive_scalper.backtest.types import LOST_TO_HIGHER_EDGE
from adaptive_scalper.research.independent import SELECTOR_SESSION, IndependentStudy
from adaptive_scalper.research.trade_analysis import summarize


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def independent_outcomes(study: IndependentStudy) -> dict[tuple[str, int, str], dict]:
    """(strategy, signal bar time, direction) -> realized R of the trade the
    strategy's own independent session opened from that bar close."""
    out = {}
    for key, session in study.sessions.items():
        if key == SELECTOR_SESSION or session.error is not None:
            continue
        for fold in session.folds:
            for t in fold.trades:
                if t.exit_time_utc is None or t.realized_pnl is None or not t.initial_monetary_risk:
                    continue
                risk = t.initial_monetary_risk
                out[(t.strategy_key, t.signal_time_utc, t.direction)] = {
                    "net_r": t.realized_pnl / risk, "gross_r": (t.realized_pnl + t.total_cost) / risk,
                    "cost_r": t.total_cost / risk,
                }
    return out


def _quantile_edges(values: list[float], n: int) -> list[float]:
    ordered = sorted(values)
    return [ordered[min(len(ordered) - 1, (len(ordered) * q) // n)] for q in range(1, n)]


def _key(c) -> tuple[str, int, str]:
    return c.strategy_key, c.bar_time_utc, c.direction


def selector_study(study: IndependentStudy, *, n_quantiles: int = 5) -> dict:
    outcomes = independent_outcomes(study)
    by_strategy: dict[str, list] = defaultdict(list)
    for c in study.candidates:
        by_strategy[c.strategy_key].append(c)
    total_selected = sum(1 for c in study.candidates if c.selected)

    def exp_r(c, attr):
        value = getattr(c, attr)
        return value / c.stop_distance if value is not None and c.stop_distance > 0 else None

    def realized(group, name):
        return _mean([outcomes[_key(c)][name] for c in group if _key(c) in outcomes])

    def matched(group):
        return sum(1 for c in group if _key(c) in outcomes)

    selector_trades = study.sessions[SELECTOR_SESSION].trades if SELECTOR_SESSION in study.sessions else []
    per_strategy = {}
    for key in sorted(k for k in study.sessions if k != SELECTOR_SESSION):
        cands = by_strategy.get(key, [])
        selected = [c for c in cands if c.selected]
        lost = [c for c in cands if c.rejection_reason == LOST_TO_HIGHER_EDGE]
        filtered = [c for c in cands if c.rejected]
        independent = study.sessions[key]
        per_strategy[key] = {
            "signals": len(cands),
            "selected": len(selected),
            "lost_to_higher_edge": len(lost),
            "filter_rejected": len(filtered),
            "filter_reasons": dict(Counter(c.rejection_reason for c in filtered)),
            "selection_share": len(selected) / total_selected if total_selected else None,
            "stated_p_selected": _mean([c.raw_confidence for c in selected]),
            "capped_confidence_share": (sum(1 for c in cands if c.raw_confidence >= 0.99) / len(cands))
            if cands else None,
            "expected_net_r_selected": _mean([exp_r(c, "expected_net_edge_price") for c in selected]),
            "expected_cost_r_selected": _mean([exp_r(c, "estimated_cost_price") for c in selected]),
            "realized_net_r_selected_matched": realized(selected, "net_r"),
            "realized_cost_r_selected_matched": realized(selected, "cost_r"),
            "realized_gross_r_selected_matched": realized(selected, "gross_r"),
            "selected_matched": matched(selected),
            "realized_net_r_lost_matched": realized(lost, "net_r"),
            "lost_matched": matched(lost),
            "realized_net_r_filtered_matched": realized(filtered, "net_r"),
            "filtered_matched": matched(filtered),
            "independent": summarize(independent.trades) if independent.error is None else {"error": independent.error},
            "in_selector_session": summarize([t for t in selector_trades if t.strategy_key == key]),
        }

    # Contested bars: >= 2 strategies cleared every filter at the same close.
    bars: dict[int, list] = defaultdict(list)
    for c in study.candidates:
        if not c.rejected:
            bars[c.bar_time_utc].append(c)
    contested = {t: cs for t, cs in bars.items() if len({c.strategy_key for c in cs}) >= 2}
    winners: Counter = Counter()
    pairs: Counter = Counter()
    for cs in contested.values():
        winner = next((c for c in cs if c.selected), None)
        if winner is None:
            continue
        winners[winner.strategy_key] += 1
        for c in cs:
            if c is not winner:
                pairs[f"{winner.strategy_key} over {c.strategy_key}"] += 1

    # Is expected net R predictive of realized net R? Quantiles over every
    # matched qualifying candidate (selected or not), pooled across strategies.
    qualifying = [c for c in study.candidates if not c.rejected and c.stop_distance > 0
                  and c.expected_net_edge_price is not None and _key(c) in outcomes]
    quantiles = []
    if len(qualifying) >= n_quantiles * 5:
        exp_values = [c.expected_net_edge_price / c.stop_distance for c in qualifying]
        edges = _quantile_edges(exp_values, n_quantiles)
        buckets: dict[int, list] = defaultdict(list)
        for c, e in zip(qualifying, exp_values):
            buckets[sum(1 for edge in edges if e > edge)].append((c, e))
        for q in sorted(buckets):
            items = buckets[q]
            quantiles.append({
                "quantile": q + 1, "candidates": len(items),
                "expected_net_r_mean": _mean([e for _, e in items]),
                "realized_net_r_mean": _mean([outcomes[_key(c)]["net_r"] for c, _ in items]),
                "realized_cost_r_mean": _mean([outcomes[_key(c)]["cost_r"] for c, _ in items]),
                "strategy_mix": dict(Counter(c.strategy_key for c, _ in items)),
            })

    return {
        "candidates": len(study.candidates),
        "selected": total_selected,
        "matched_outcomes": sum(1 for c in study.candidates if _key(c) in outcomes),
        "per_strategy": per_strategy,
        "contested_bars": len(contested),
        "contested_winners": dict(winners),
        "contested_pairs": dict(pairs.most_common(12)),
        "expected_vs_realized_quantiles": quantiles,
    }
