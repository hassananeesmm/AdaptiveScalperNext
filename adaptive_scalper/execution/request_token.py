"""Compact request-token embedding for MT5 order comments
(execution-safety review round 2 finding #7).

`execution/service.py` embeds this token in every `OrderRequest.comment`
it sends, so `execution/unknown.py`'s secondary correlation path has
something to match against when a send loses `broker_order_id`
acknowledgement entirely. MT5 comment fields are broker-limited (commonly
~31 characters) and brokers MAY modify/truncate/strip comments — this
token is therefore a SECONDARY correlation signal only, never the sole
basis for resolving an UNKNOWN order (see `unknown.py`'s docstring: it
requires a unique match, and falls back to remaining UNKNOWN on any
ambiguity or absence).
"""

from __future__ import annotations

_TOKEN_LENGTH = 16
_MAX_COMMENT_LENGTH = 31


def request_token(client_request_id: str) -> str:
    """A short, deterministic slice of `client_request_id` — not a hash,
    so it stays directly greppable in broker trade history by a human
    operator too."""
    return client_request_id[:_TOKEN_LENGTH]


def embed_request_token(client_request_id: str, comment: str = "") -> str:
    token = request_token(client_request_id)
    if not comment:
        return token[:_MAX_COMMENT_LENGTH]
    combined = f"{token} {comment}"
    return combined[:_MAX_COMMENT_LENGTH]
