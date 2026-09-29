"""V1 strategy baseline freeze (2026-09-28).

Independent development-data research found every strategy x symbol session
net NEGATIVE after costs (docs/research/INDEPENDENT_STRATEGY_RESEARCH_2026-09-27.md).
The six live strategies and the live selector are therefore frozen as the V1
baseline: byte-identical to deployed release 0.2.2 (`eaa024c`). Improvements
must be RESEARCH-ONLY V2 candidates (`adaptive_scalper/research/v2/`) until
they pass the promotion gate -- never an in-place edit of these files.

If this test fails you changed live decision code. Revert it, or -- only via
an approved, versioned promotion -- bump the strategy's STRATEGY_VERSION and
update the pinned digest here in the same reviewed commit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from adaptive_scalper.strategies import build_active_registry

ROOT = Path(__file__).resolve().parents[1] / "adaptive_scalper"

# sha256 of the source with CRLF normalised to LF.
V1_SOURCE_DIGESTS = {
    "strategies/microstructure_acceleration.py": "596e0dc229c0732807f039bae445bcc95dfd9c346b1e682ece3027308df063fa",
    "strategies/momentum_continuation.py": "616085cd5833ee121549d6144ffbffa7d7052f68c7268343701d92cf35f12b3c",
    "strategies/pullback_continuation.py": "c6d39a9cdb69f40b16bd1f441c48a4bdfd91347fbaaad64ff5c4dfde1991ca13",
    "strategies/range_breakout.py": "44ff3600c695cdacddc465e0f07191f614f664c4ef660d5d74f9a0def704dff8",
    "strategies/statistical_reversion.py": "4d324635b884d53fa247db66501bd4ba10fbc9f50f847c25e878be319ee154e0",
    "strategies/volatility_expansion.py": "ef41d9ba612412a6fd14eb08347414d28d471642267caa7ce092ac2fd0dbc8fc",
    "strategies/__init__.py": "c3141196246f4e40ef2fb5ff60daf37fa7952f64e734e67f44d4bae4d977301d",
    "strategies/base.py": "05a9646ef2b9e7ea0a936dc47b2d638bf6632429202c7c817b84590fd1a448d1",
    "selector/selector.py": "367da84ebcbe56099a729bce631ac7568fdfaadd32a2bfc5bda4ea8369e38abe",
}

V1_ACTIVE = {
    "microstructure_acceleration": 1,
    "momentum_continuation": 1,
    "pullback_continuation": 1,
    "range_breakout": 1,
    "statistical_reversion": 1,
    "volatility_expansion": 1,
}


@pytest.mark.parametrize("relative", sorted(V1_SOURCE_DIGESTS))
def test_v1_decision_source_is_unchanged(relative):
    data = (ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == V1_SOURCE_DIGESTS[relative], (
        f"{relative} is frozen V1 baseline code; put changes in a research-only V2 candidate"
    )


def test_the_live_registry_is_exactly_the_six_v1_strategies():
    active = {s.key: s.version for s in build_active_registry().all_active()}
    assert active == V1_ACTIVE
