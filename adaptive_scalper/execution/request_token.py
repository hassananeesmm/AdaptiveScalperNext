"""Compact request-token embedding for MT5 order comments
(execution-safety review round 2 finding #7; strengthened per external
review 2026-09-21 finding #14).

`execution/service.py` embeds this token in every `OrderRequest.comment`
it sends, so `execution/unknown.py`'s secondary correlation path has
something to match against when a send loses `broker_order_id`
acknowledgement entirely. MT5 comment fields are broker-limited (commonly
~31 characters) and brokers MAY modify/truncate/strip comments — this
token is therefore a SECONDARY correlation signal only, never the sole
basis for resolving an UNKNOWN order (see `unknown.py`'s docstring: it
requires a unique match, and falls back to remaining UNKNOWN on any
ambiguity or absence).

**Why a hash, not a prefix slice** (external review finding #14): a plain
`client_request_id[:16]` prefix collides whenever two distinct request
ids happen to share a common prefix — a real risk when ids are generated
with a structured scheme (e.g. `f"{symbol}:{strategy}:{uuid}"`, where many
requests legitimately share everything up to the UUID). `request_token()`
instead derives a collision-resistant token from the FULL id via SHA-256,
so any difference anywhere in `client_request_id` — not just its first 16
characters — changes the token. The `ASN:` prefix makes an embedded token
recognizable in raw MT5 trade history at a glance (broker/operator
tooling can grep for it) without weakening the hash itself.
"""

from __future__ import annotations

import hashlib

_TOKEN_PREFIX = "ASN:"
_HASH_HEX_LENGTH = 16  # 64 bits of the full SHA-256 digest
_MAX_COMMENT_LENGTH = 31


def request_token(client_request_id: str) -> str:
    """A compact, collision-resistant token derived from the FULL
    `client_request_id` via SHA-256 — never a truncated prefix of the id
    itself (external review finding #14). Deterministic: the same
    `client_request_id` always yields the same token, so a caller can
    always re-derive it to search broker history."""
    digest = hashlib.sha256(client_request_id.encode("utf-8")).hexdigest()
    return f"{_TOKEN_PREFIX}{digest[:_HASH_HEX_LENGTH]}"


def embed_request_token(client_request_id: str, comment: str = "") -> str:
    token = request_token(client_request_id)
    if not comment:
        return token[:_MAX_COMMENT_LENGTH]
    combined = f"{token} {comment}"
    return combined[:_MAX_COMMENT_LENGTH]
