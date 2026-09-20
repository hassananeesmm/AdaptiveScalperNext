"""Tests for execution.request_token (execution-safety review round 2
finding #7)."""

from __future__ import annotations

from adaptive_scalper.execution.request_token import embed_request_token, request_token


def test_request_token_is_deterministic_and_short():
    a = request_token("req-abc123-xyz789-very-long-id")
    b = request_token("req-abc123-xyz789-very-long-id")
    assert a == b
    assert len(a) <= 16


def test_different_ids_produce_different_tokens():
    assert request_token("req-1") != request_token("req-2")


def test_embed_with_no_comment_returns_token():
    token = request_token("req-abc")
    assert embed_request_token("req-abc") == token


def test_embed_with_comment_prefixes_token():
    result = embed_request_token("req-abc", "manual note")
    assert result.startswith(request_token("req-abc"))
    assert "manual note" in result


def test_embed_result_never_exceeds_mt5_comment_limit():
    result = embed_request_token("req-abc-very-long-client-request-id", "a very long human comment that goes on")
    assert len(result) <= 31
