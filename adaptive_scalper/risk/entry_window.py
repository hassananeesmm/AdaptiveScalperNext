"""Operator entry-hours window (config `[entry_window]`).

A NEW entry is allowed only while the UTC hour of `now_utc` is in the
half-open window [start_hour_utc, end_hour_utc). Pure function, so the
runtimes' global entry block and the final permission gate evaluate the
same rule. It only ever blocks: exits, protective closes and position
management are not entries and never consult it.
"""

from __future__ import annotations

BLOCK_SESSION = "BLOCK_SESSION"

_SECONDS_PER_DAY = 86_400


def entry_window_block(now_utc: int | None, hours: tuple[int, int] | None) -> tuple[str, str] | None:
    """(BLOCK_SESSION, reason) outside the window, None inside it or when no
    window is configured (`hours is None`). With a window configured, an
    unknown time blocks: an unprovable "inside the window" is outside it."""
    if hours is None:
        return None
    start, end = hours
    if now_utc is None:
        return BLOCK_SESSION, f"entry window [{start:02d}:00, {end:02d}:00) UTC configured but the time is unknown"
    seconds_of_day = int(now_utc) % _SECONDS_PER_DAY
    if start * 3600 <= seconds_of_day < end * 3600:
        return None
    hh, mm = divmod(seconds_of_day // 60, 60)
    return BLOCK_SESSION, (f"{hh:02d}:{mm:02d} UTC is outside the entry window "
                           f"[{start:02d}:00, {end:02d}:00) UTC -- no new entries")
