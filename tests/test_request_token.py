"""Tests for execution.request_token (execution-safety review round 2
finding #7; strengthened to a SHA-256-derived token per external review
2026-09-21 finding #14)."""

from __future__ import annotations

from adaptive_scalper.execution.request_token import embed_request_token, request_token


def test_request_token_is_deterministic_and_bounded():
    a = request_token("req-abc123-xyz789-very-long-id")
    b = request_token("req-abc123-xyz789-very-long-id")
    assert a == b
    assert len(a) <= 20  # "ASN:" + 16 hex chars, well within the 31-char MT5 comment limit


def test_token_is_asn_prefixed_and_derived_from_the_full_id_via_sha256():
    import hashlib

    client_request_id = "req-abc123-xyz789-very-long-id"
    token = request_token(client_request_id)
    assert token.startswith("ASN:")
    expected_digest = hashlib.sha256(client_request_id.encode("utf-8")).hexdigest()[:16]
    assert token == f"ASN:{expected_digest}"


def test_different_ids_produce_different_tokens():
    assert request_token("req-1") != request_token("req-2")


def test_ids_sharing_a_common_prefix_do_not_collide():
    # External review finding #14: the old client_request_id[:16] scheme
    # would collide here (both ids share their first 16 characters).
    id_a = "XAUUSD:momentum_continuation:11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    id_b = "XAUUSD:momentum_continuation:22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert id_a[:16] == id_b[:16]  # the prefix that would have collided under the old scheme
    assert request_token(id_a) != request_token(id_b)


def test_embed_with_no_comment_returns_token():
    token = request_token("req-abc")
    assert embed_request_token("req-abc") == token


def test_embed_with_comment_prefixes_token():
    result = embed_request_token("req-abc", "note")
    assert result.startswith(request_token("req-abc"))
    assert result.endswith("note")


def test_embed_result_never_exceeds_mt5_comment_limit():
    result = embed_request_token("req-abc-very-long-client-request-id", "a very long human comment that goes on")
    assert len(result) <= 31
