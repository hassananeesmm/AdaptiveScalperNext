"""Supported bar resolutions (directive section 47).

The single source of truth for which resolutions the historical bootstrap,
coverage reporting, and (eventually) the feature engine agree on. Not a
claim that all of these are equally useful — section 12 explicitly says not
to declare one resolution universally correct.
"""

from __future__ import annotations

SUPPORTED_BAR_RESOLUTIONS: tuple[str, ...] = ("M1", "M2", "M3", "M5", "M15")

RESOLUTION_SECONDS: dict[str, int] = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M5": 300,
    "M15": 900,
}


def resolution_seconds(resolution: str) -> int:
    try:
        return RESOLUTION_SECONDS[resolution]
    except KeyError as exc:
        raise ValueError(
            f"unsupported resolution {resolution!r}; supported: {SUPPORTED_BAR_RESOLUTIONS}"
        ) from exc
