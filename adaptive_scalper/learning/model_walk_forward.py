"""Model walk-forward: train -> purge/gap -> validate, repeated forward
through time, then the final retrain (completion directive Phase 6).

Rows are ordered by entry time and cut into `n_folds + 1` contiguous
blocks. Fold k trains ONLY on rows before block k (never the future),
drops every training row whose label interval (entry -> exit) reaches
into `[test_start - gap_seconds, ...)` -- purging label overlap plus an
optional gap -- fits the same `LogisticRegression` family as
`learning.training`, and scores block k. The concatenated out-of-fold
predictions give the evidence:

- discrimination (AUC), probabilistic accuracy (Brier, log loss) and the
  Brier skill against the TRAINING base rate (a constant predictor);
- calibration: reliability bins and expected calibration error (ECE);
- subgroup stability by strategy, entry regime and trading session.

This is evidence, never a decision: `promotion_ready` is always False
here; `learning.jobs` feeds these facts to the fail-closed
`learning.promotion` gate and reports the verdict, and no code path
promotes a model or gives it influence (Stage 2 is not implemented).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from adaptive_scalper.costs.observations import session_label
from adaptive_scalper.learning.dataset import FEATURE_COLUMNS, TrainingRow

DEFAULT_FOLDS = 5
DEFAULT_MIN_TRAIN_ROWS = 100
CALIBRATION_BINS = 10
MIN_SUBGROUP_ROWS = 20
ECE_TOLERANCE = 0.05
MIN_BRIER_SKILL = 0.01   # "beats the base rate" means by a margin, not by float noise


@dataclass(frozen=True)
class FoldResult:
    fold: int
    train_rows: int
    test_rows: int
    purged_rows: int
    trained: bool
    reason: str
    test_start_utc: int
    test_end_utc: int
    auc: float | None = None
    brier: float | None = None
    base_rate_brier: float | None = None


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_rate: float | None


@dataclass(frozen=True)
class SubgroupMetrics:
    rows: int
    base_rate: float
    mean_predicted: float
    brier: float
    auc: float | None


@dataclass(frozen=True)
class ModelWalkForwardResult:
    folds: tuple[FoldResult, ...]
    oos_rows: int
    oos_auc: float | None
    oos_brier: float | None
    oos_base_rate_brier: float | None
    brier_skill: float | None
    oos_log_loss: float | None
    ece: float | None
    calibration: tuple[CalibrationBin, ...]
    subgroups: dict[str, dict[str, SubgroupMetrics]] = field(default_factory=dict)
    beats_base_rate: bool = False
    calibrated: bool = False
    subgroups_stable: bool = False
    promotion_ready: bool = False           # never set here -- see module docstring
    detail: str = ""


def _auc(labels: list[int], scores: list[float]) -> float | None:
    if len(set(labels)) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(labels, scores))


def _brier(labels: list[int], probs: list[float]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(labels)


def _log_loss(labels: list[int], probs: list[float]) -> float:
    eps = 1e-12
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in zip(probs, labels)) / len(labels)


def calibration_bins(labels: list[int], probs: list[float], n_bins: int = CALIBRATION_BINS) -> tuple[tuple[CalibrationBin, ...], float | None]:
    if not labels:
        return (), None
    bins, ece = [], 0.0
    for b in range(n_bins):
        lower, upper = b / n_bins, (b + 1) / n_bins
        members = [(p, y) for p, y in zip(probs, labels) if lower <= p < upper or (b == n_bins - 1 and p == 1.0)]
        if not members:
            bins.append(CalibrationBin(lower, upper, 0, None, None))
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        rate = sum(y for _, y in members) / len(members)
        ece += len(members) / len(labels) * abs(mean_p - rate)
        bins.append(CalibrationBin(lower, upper, len(members), round(mean_p, 4), round(rate, 4)))
    return tuple(bins), round(ece, 4)


def _session(row: TrainingRow) -> str:
    try:
        hour = row.features[FEATURE_COLUMNS.index("hour_of_day_utc")]
        return session_label(int(hour))
    except (ValueError, IndexError):
        return "UNKNOWN"


def _subgroups(rows: list[TrainingRow], probs: list[float]) -> dict[str, dict[str, SubgroupMetrics]]:
    out: dict[str, dict[str, SubgroupMetrics]] = {}
    keyers = {"strategy": lambda r: r.strategy_key, "regime": lambda r: r.entry_regime, "session": _session}
    for dimension, key_of in keyers.items():
        groups: dict[str, list[int]] = {}
        for i, row in enumerate(rows):
            groups.setdefault(key_of(row), []).append(i)
        out[dimension] = {}
        for key, idx in sorted(groups.items()):
            labels = [rows[i].label for i in idx]
            p = [probs[i] for i in idx]
            out[dimension][key] = SubgroupMetrics(
                rows=len(idx), base_rate=round(sum(labels) / len(labels), 4), mean_predicted=round(sum(p) / len(p), 4),
                brier=round(_brier(labels, p), 4), auc=_auc(labels, p) if len(idx) >= MIN_SUBGROUP_ROWS else None,
            )
    return out


def _stable(subgroups: dict[str, dict[str, SubgroupMetrics]]) -> bool:
    """Every sufficiently large subgroup keeps positive skill over its own
    base rate -- a model that only works in one regime is not stable."""
    checked = 0
    for groups in subgroups.values():
        for metrics in groups.values():
            if metrics.rows < MIN_SUBGROUP_ROWS:
                continue
            checked += 1
            base = metrics.base_rate * (1 - metrics.base_rate)
            if metrics.brier >= base * (1 - MIN_BRIER_SKILL):
                return False
    return checked > 0


def run_model_walk_forward(
    rows: list[TrainingRow], *, n_folds: int = DEFAULT_FOLDS, gap_seconds: int = 0,
    min_train_rows: int = DEFAULT_MIN_TRAIN_ROWS, seed: int = 0,
) -> ModelWalkForwardResult:
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    if gap_seconds < 0:
        raise ValueError("gap_seconds must be >= 0")
    ordered = sorted(rows, key=lambda r: (r.entry_time_utc, r.label_end_utc))
    if len(ordered) < n_folds + 1:
        return ModelWalkForwardResult((), 0, None, None, None, None, None, None, (),
                                      detail=f"{len(ordered)} rows cannot form {n_folds + 1} time blocks")

    from sklearn.linear_model import LogisticRegression

    from adaptive_scalper.research.splits import contiguous_groups

    blocks = contiguous_groups(len(ordered), n_folds + 1)
    folds, oos_rows, oos_probs, oos_base = [], [], [], []
    for k in range(1, n_folds + 1):
        test = blocks[k]
        test_start = ordered[test[0]].entry_time_utc
        test_end = max(ordered[i].label_end_utc for i in test)
        candidates = range(test[0])
        train = [i for i in candidates if ordered[i].label_end_utc < test_start - gap_seconds]
        purged = len(candidates) - len(train)
        labels = [ordered[i].label for i in train]
        if len(train) < min_train_rows or len(set(labels)) < 2:
            reason = (f"only {len(train)} purged training rows (< {min_train_rows})" if len(train) < min_train_rows
                      else "training rows have a single outcome class")
            folds.append(FoldResult(k, len(train), len(test), purged, False, reason, test_start, test_end))
            continue
        model = LogisticRegression(max_iter=1000, random_state=seed)
        model.fit([list(ordered[i].features) for i in train], labels)
        probs = [float(p) for p in model.predict_proba([list(ordered[i].features) for i in test])[:, 1]]
        test_labels = [ordered[i].label for i in test]
        base = sum(labels) / len(labels)
        folds.append(FoldResult(
            k, len(train), len(test), purged, True, "trained", test_start, test_end,
            auc=_auc(test_labels, probs), brier=round(_brier(test_labels, probs), 4),
            base_rate_brier=round(_brier(test_labels, [base] * len(test)), 4),
        ))
        oos_rows.extend(ordered[i] for i in test)
        oos_probs.extend(probs)
        oos_base.extend([base] * len(test))

    if not oos_rows:
        return ModelWalkForwardResult(tuple(folds), 0, None, None, None, None, None, None, (),
                                      detail="no fold had enough purged training rows")
    labels = [r.label for r in oos_rows]
    brier, base_brier = _brier(labels, oos_probs), _brier(labels, oos_base)
    skill = 1 - brier / base_brier if base_brier > 0 else None
    bins, ece = calibration_bins(labels, oos_probs)
    subgroups = _subgroups(oos_rows, oos_probs)
    beats = skill is not None and skill >= MIN_BRIER_SKILL
    calibrated = ece is not None and ece <= ECE_TOLERANCE
    stable = _stable(subgroups)
    return ModelWalkForwardResult(
        folds=tuple(folds), oos_rows=len(oos_rows), oos_auc=_auc(labels, oos_probs), oos_brier=round(brier, 4),
        oos_base_rate_brier=round(base_brier, 4), brier_skill=round(skill, 4) if skill is not None else None,
        oos_log_loss=round(_log_loss(labels, oos_probs), 4), ece=ece, calibration=bins, subgroups=subgroups,
        beats_base_rate=beats, calibrated=calibrated, subgroups_stable=stable, promotion_ready=False,
        detail=(f"{sum(f.trained for f in folds)}/{len(folds)} folds trained, {len(oos_rows)} out-of-fold rows; "
                f"brier skill {skill if skill is None else round(skill, 4)}, ECE {ece}; evidence only"),
    )


def result_to_dict(result: ModelWalkForwardResult) -> dict:
    from dataclasses import asdict
    return asdict(result)
