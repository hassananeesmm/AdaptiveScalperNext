"""Label intervals, purging, embargo, purged K-fold and CPCV.

A sample (e.g. one trade, labeled by its net outcome) is not a point in
time: its label depends on prices over `[start_utc, end_utc]` (entry to
exit). Two samples whose label intervals overlap share information, so a
model trained on one and tested on the other is not tested out of
sample. Therefore:

- PURGE: a training sample is dropped if its label interval overlaps the
  time span of ANY test sample.
- EMBARGO: a training sample is also dropped if it STARTS within
  `embargo_seconds` after a test block ends (serial correlation leaks
  forward; nothing is dropped before the test block because purging
  already handles overlap).

Every split here is by TIME: samples are ordered by `start_utc` and test
groups are contiguous blocks, never a shuffle.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb


@dataclass(frozen=True)
class LabelInterval:
    start_utc: int
    end_utc: int

    def __post_init__(self) -> None:
        if self.end_utc < self.start_utc:
            raise ValueError(f"label interval ends before it starts: {self.start_utc} > {self.end_utc}")


@dataclass(frozen=True)
class Split:
    train: tuple[int, ...]
    test: tuple[int, ...]
    test_groups: tuple[int, ...] = ()
    purged: tuple[int, ...] = ()
    embargoed: tuple[int, ...] = ()


def _check_sorted(intervals: list[LabelInterval]) -> None:
    for a, b in zip(intervals, intervals[1:]):
        if b.start_utc < a.start_utc:
            raise ValueError("label intervals must be sorted by start_utc (time order, never shuffled)")


def contiguous_groups(n_samples: int, n_groups: int) -> list[tuple[int, ...]]:
    """`n_groups` contiguous, near-equal index blocks covering all samples."""
    if n_groups < 2:
        raise ValueError(f"n_groups must be >= 2, got {n_groups}")
    if n_samples < n_groups:
        raise ValueError(f"{n_samples} samples cannot form {n_groups} non-empty groups")
    base, extra = divmod(n_samples, n_groups)
    groups, cursor = [], 0
    for g in range(n_groups):
        size = base + (1 if g < extra else 0)
        groups.append(tuple(range(cursor, cursor + size)))
        cursor += size
    return groups


def purge_and_embargo(
    intervals: list[LabelInterval], test_indices: tuple[int, ...], *,
    test_blocks: list[tuple[int, ...]], embargo_seconds: int = 0,
) -> Split:
    """Training set for one test selection. `test_blocks` are the
    contiguous test groups (embargo applies after EACH block's end)."""
    if embargo_seconds < 0:
        raise ValueError("embargo_seconds must be >= 0")
    test_set = set(test_indices)
    spans = [
        (min(intervals[i].start_utc for i in block), max(intervals[i].end_utc for i in block))
        for block in test_blocks
    ]
    train, purged, embargoed = [], [], []
    for i, interval in enumerate(intervals):
        if i in test_set:
            continue
        if any(interval.start_utc <= span_end and interval.end_utc >= span_start for span_start, span_end in spans):
            purged.append(i)
        elif any(span_end < interval.start_utc <= span_end + embargo_seconds for _, span_end in spans):
            embargoed.append(i)
        else:
            train.append(i)
    return Split(train=tuple(train), test=tuple(sorted(test_set)), purged=tuple(purged), embargoed=tuple(embargoed))


def purged_kfold(intervals: list[LabelInterval], n_splits: int, *, embargo_seconds: int = 0) -> list[Split]:
    """K contiguous test folds in time order; each fold's training set is
    every other sample minus purged and embargoed ones."""
    _check_sorted(intervals)
    groups = contiguous_groups(len(intervals), n_splits)
    splits = []
    for g, block in enumerate(groups):
        split = purge_and_embargo(intervals, block, test_blocks=[block], embargo_seconds=embargo_seconds)
        splits.append(Split(split.train, split.test, (g,), split.purged, split.embargoed))
    return splits


def cpcv_splits(
    intervals: list[LabelInterval], n_groups: int, n_test_groups: int, *, embargo_seconds: int = 0,
) -> list[Split]:
    """Combinatorial purged cross-validation: every choice of
    `n_test_groups` of the `n_groups` contiguous groups is a test set,
    C(N, k) splits in total."""
    _check_sorted(intervals)
    if not (1 <= n_test_groups < n_groups):
        raise ValueError(f"need 1 <= n_test_groups < n_groups, got {n_test_groups}/{n_groups}")
    groups = contiguous_groups(len(intervals), n_groups)
    splits = []
    for chosen in combinations(range(n_groups), n_test_groups):
        blocks = [groups[g] for g in chosen]
        test = tuple(i for block in blocks for i in block)
        split = purge_and_embargo(intervals, test, test_blocks=blocks, embargo_seconds=embargo_seconds)
        splits.append(Split(split.train, split.test, chosen, split.purged, split.embargoed))
    return splits


def cpcv_path_count(n_groups: int, n_test_groups: int) -> int:
    """Number of complete backtest paths CPCV yields: each group is tested
    in C(N-1, k-1) splits, so that many full out-of-sample paths exist."""
    return comb(n_groups - 1, n_test_groups - 1)


def cpcv_paths(n_groups: int, n_test_groups: int) -> list[list[tuple[int, int]]]:
    """Assemble paths: path p takes, for each group g, the p-th split (in
    `cpcv_splits` order) in which g was a test group. Returns, per path, a
    list of `(group, split_index)`."""
    split_groups = list(combinations(range(n_groups), n_test_groups))
    occurrences: dict[int, list[int]] = {g: [] for g in range(n_groups)}
    for s, chosen in enumerate(split_groups):
        for g in chosen:
            occurrences[g].append(s)
    n_paths = cpcv_path_count(n_groups, n_test_groups)
    return [[(g, occurrences[g][p]) for g in range(n_groups)] for p in range(n_paths)]
