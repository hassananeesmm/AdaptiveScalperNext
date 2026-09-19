"""Broker symbol resolution.

Per MASTER_BUILD_DIRECTIVE.md section 6: never assume a broker uses the
exact canonical names. For each canonical symbol, try an exact match,
then reasonable prefix/suffix aliases, and require exactly one safe
match. Fail closed (no match, or more than one match) rather than guess.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS
from adaptive_scalper.gateway.types import SymbolSpec

NO_MATCH = "no_match"
AMBIGUOUS = "ambiguous"
EXACT_MATCH = "exact_match"
ALIAS_MATCH = "alias_match"

# A broker alias is the canonical name with an optional short non-alphanumeric
# prefix, and an optional suffix that is EITHER a delimiter (any non-alnum
# char) followed by up to 4 alnum chars, OR up to 3 lowercase-only chars
# with no delimiter (typical broker markers: "m", "pro", "ecn", "raw").
#
# The suffix branches are deliberately asymmetric on case: a no-delimiter
# suffix must be lowercase. An uppercase no-delimiter suffix is far more
# likely to be a different quote currency/asset than a broker marker —
# e.g. "XAUUSDT" (gold priced in Tether, a genuinely different instrument
# on many brokers/exchanges) must NOT alias-match "XAUUSD", but it did
# under an earlier version of this pattern that allowed any 0-3 alnum
# chars regardless of case. Caught by security review before release; see
# tests/test_symbol_resolver.py::test_currency_like_suffix_does_not_alias_match.
#
# `(?i:...)` scopes case-insensitivity to just the canonical-symbol
# portion (Python 3.11+ scoped inline flags) so the trailing lowercase-only
# branch isn't also made case-insensitive by a blanket re.IGNORECASE.
_ALIAS_RE_TEMPLATE = (
    r"^[^A-Za-z0-9]{0,2}(?i:%s)(?:[^A-Za-z0-9][A-Za-z0-9]{0,4}|[a-z0-9]{0,3})?$"
)


@dataclass(frozen=True)
class ResolutionResult:
    canonical: str
    broker_symbol: str | None
    resolved: bool
    reason: str
    candidates: tuple[str, ...] = ()


def _alias_pattern(canonical: str) -> re.Pattern:
    # No blanket re.IGNORECASE here — case sensitivity is scoped inline via
    # (?i:...) around just the canonical-symbol portion of the template
    # (see _ALIAS_RE_TEMPLATE's comment for why the suffix must stay
    # case-sensitive).
    return re.compile(_ALIAS_RE_TEMPLATE % re.escape(canonical))


def resolve_symbol(canonical: str, broker_symbols: list[SymbolSpec]) -> ResolutionResult:
    """Resolve one canonical symbol against the broker's symbol list.

    Fails closed: returns resolved=False for zero matches (NO_MATCH) or
    more than one plausible match (AMBIGUOUS). Callers must treat
    resolved=False as BLOCK_SYMBOL_NOT_ALLOWED for that symbol, never fall
    back to a guess.
    """
    if canonical not in ALLOWED_CANONICAL_SYMBOLS:
        raise ValueError(
            f"{canonical!r} is not in the canonical allow-list "
            f"{sorted(ALLOWED_CANONICAL_SYMBOLS)}; refusing to resolve it"
        )

    names = [s.name for s in broker_symbols]

    exact = [n for n in names if n == canonical]
    if len(exact) == 1:
        return ResolutionResult(canonical, exact[0], True, EXACT_MATCH, tuple(exact))
    if len(exact) > 1:
        # Broker symbol lists are keyed by name; this should be structurally
        # impossible, but fail closed rather than assume.
        return ResolutionResult(canonical, None, False, AMBIGUOUS, tuple(exact))

    pattern = _alias_pattern(canonical)
    aliases = [n for n in names if pattern.match(n)]
    if len(aliases) == 1:
        return ResolutionResult(canonical, aliases[0], True, ALIAS_MATCH, tuple(aliases))
    if len(aliases) == 0:
        return ResolutionResult(canonical, None, False, NO_MATCH)
    return ResolutionResult(canonical, None, False, AMBIGUOUS, tuple(sorted(aliases)))


def resolve_all(broker_symbols: list[SymbolSpec]) -> dict[str, ResolutionResult]:
    """Resolve every canonical symbol. Always returns an entry for all
    three symbols, resolved or not — callers must check .resolved on
    each rather than assume presence means success."""
    return {
        canonical: resolve_symbol(canonical, broker_symbols)
        for canonical in sorted(ALLOWED_CANONICAL_SYMBOLS)
    }


def persist_resolution(conn: sqlite3.Connection, result: ResolutionResult) -> None:
    """Persist one symbol's resolution, overwriting any previous mapping
    for that canonical symbol (only the current mapping is meaningful)."""
    conn.execute(
        """
        INSERT INTO symbol_mapping (canonical, broker_symbol, resolved, reason, resolved_at)
        VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(canonical) DO UPDATE SET
            broker_symbol = excluded.broker_symbol,
            resolved = excluded.resolved,
            reason = excluded.reason,
            resolved_at = excluded.resolved_at
        """,
        (result.canonical, result.broker_symbol, int(result.resolved), result.reason),
    )


def persist_all(conn: sqlite3.Connection, results: dict[str, ResolutionResult]) -> None:
    for result in results.values():
        persist_resolution(conn, result)


def load_persisted_mapping(conn: sqlite3.Connection, canonical: str) -> ResolutionResult | None:
    row = conn.execute(
        "SELECT canonical, broker_symbol, resolved, reason FROM symbol_mapping WHERE canonical = ?",
        (canonical,),
    ).fetchone()
    if row is None:
        return None
    return ResolutionResult(
        canonical=row["canonical"],
        broker_symbol=row["broker_symbol"],
        resolved=bool(row["resolved"]),
        reason=row["reason"],
    )
