"""Model-provider abstraction with the protections every outbound call needs.

`ProviderRouter.narrate()` is the ONLY path to an external model. Before a
call it checks: external calls permitted by config (provider + data category
+ positive budget), the circuit breaker, the daily token and cost budgets
(pre-flight estimate), the per-provider minimum interval and concurrency
limit, and the response cache. The call itself has a hard timeout, bounded
retries with exponential backoff, and the response must pass
`security.parse_model_response` (strict JSON, resolvable citations, no
instructions). Any failure falls through the configured fallback chain and
ends at the deterministic provider, which returns no narrative at all - the
deterministic analysis is always complete without a model.

Keys are read from the environment variable NAMED in the config at call time
and never logged, stored or put in a prompt.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from adaptive_scalper.ai_desk.configuration import AiDeskConfig, ProviderConfig
from adaptive_scalper.ai_desk.contracts import canonical_json, sha
from adaptive_scalper.ai_desk.security import (
    NarrativeRejected,
    build_prompt,
    parse_model_response,
    redact,
)


class ProviderError(Exception):
    """A provider call failed (network, HTTP, timeout, malformed body)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class Provider:
    kind = "deterministic"

    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg

    def complete(self, messages: list[dict], *, max_tokens: int, timeout: float) -> ProviderResponse:
        raise NotImplementedError

    def api_key(self) -> str | None:
        return os.environ.get(self.cfg.api_key_env) if self.cfg.api_key_env else None


class DeterministicProvider(Provider):
    """No model. The desk's analyses are deterministic Python; this provider
    simply declines to add a narrative (it never pretends to be a model)."""

    kind = "deterministic"

    def complete(self, messages, *, max_tokens, timeout):  # pragma: no cover - never called for text
        raise ProviderError("deterministic provider produces no model text", retryable=False)


@dataclass
class _Breaker:
    failures: int = 0
    opened_at: float | None = None


@dataclass
class NarrativeOutcome:
    status: str                    # OK / CACHED / NOT_PERMITTED / CATEGORY_NOT_PERMITTED / BUDGET_EXHAUSTED /
    #                                CIRCUIT_OPEN / REJECTED / FAILED / DETERMINISTIC_ONLY
    narrative: dict | None = None
    provider: str = "deterministic"
    model: str = "deterministic/1"
    attempts: list[dict] = field(default_factory=list)


def build_provider(cfg: ProviderConfig) -> Provider:
    if cfg.kind == "deterministic":
        return DeterministicProvider(cfg)
    from adaptive_scalper.ai_desk.providers.http import (
        AnthropicProvider,
        OpenAICompatibleProvider,
    )

    if cfg.kind == "anthropic":
        return AnthropicProvider(cfg)
    if cfg.kind == "grok":
        return OpenAICompatibleProvider(cfg, default_base_url="https://api.x.ai/v1")
    return OpenAICompatibleProvider(cfg)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def utc_day_start(now: float | None = None) -> int:
    d = datetime.fromtimestamp(now or time.time(), UTC)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


class ProviderRouter:
    def __init__(self, config: AiDeskConfig, journal=None, *, providers: dict[str, Provider] | None = None,
                 sleep=time.sleep, clock=time.monotonic) -> None:
        self.config = config
        self.journal = journal
        self.sleep = sleep
        self.clock = clock
        self.providers = providers or {p.name: build_provider(p) for p in config.provider_chain()}
        self._breakers: dict[str, _Breaker] = {}
        self._last_call: dict[str, float] = {}
        self._sems: dict[str, threading.Semaphore] = {
            p.name: threading.Semaphore(max(1, p.max_concurrent_requests)) for p in config.provider_chain()}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        out = {}
        for p in self.config.provider_chain():
            b = self._breakers.get(p.name, _Breaker())
            out[p.name] = {"kind": p.kind, "model": p.model or "deterministic/1",
                           "circuit": "OPEN" if self._is_open(p) else "CLOSED", "consecutive_failures": b.failures,
                           "key_configured": bool(p.api_key_env and os.environ.get(p.api_key_env))
                           if p.kind != "deterministic" else None}
        return out

    def _is_open(self, p: ProviderConfig) -> bool:
        b = self._breakers.get(p.name)
        if b is None or b.opened_at is None:
            return False
        if self.clock() - b.opened_at >= p.circuit_breaker_cooldown_seconds:
            b.opened_at, b.failures = None, 0     # half-open: allow a new attempt
            return False
        return True

    def _record_failure(self, p: ProviderConfig) -> None:
        b = self._breakers.setdefault(p.name, _Breaker())
        b.failures += 1
        if b.failures >= p.circuit_breaker_failures:
            b.opened_at = self.clock()

    def _record_success(self, p: ProviderConfig) -> None:
        self._breakers[p.name] = _Breaker()

    # ---------------------------------------------------------------- budget
    def _budget_allows(self, estimated_tokens: int, p: ProviderConfig) -> bool:
        b = self.config.budget
        used = self.journal.usage_since(utc_day_start()) if self.journal is not None else {"tokens": 0, "cost_usd": 0}
        est_cost = (estimated_tokens / 1000) * max(p.input_cost_per_1k_tokens_usd, p.output_cost_per_1k_tokens_usd)
        if used["tokens"] + estimated_tokens > b.daily_token_budget:
            return False
        paid = p.input_cost_per_1k_tokens_usd > 0 or p.output_cost_per_1k_tokens_usd > 0
        return not (paid and used["cost_usd"] + est_cost > b.daily_cost_budget_usd)

    # --------------------------------------------------------------- narrate
    def narrate(self, *, agent: str, category: str, digest: dict, allowed_evidence_ids: frozenset[str],
                run_id: str | None = None) -> NarrativeOutcome:
        if agent not in self.config.llm_agents:
            return NarrativeOutcome("DETERMINISTIC_ONLY")
        if not self.config.external_calls_permitted():
            return NarrativeOutcome("NOT_PERMITTED")
        if category not in self.config.allowed_external_data:
            return NarrativeOutcome("CATEGORY_NOT_PERMITTED")
        messages = build_prompt(agent, digest)
        key = "nc-" + sha(canonical_json(messages) + canonical_json([p.model for p in self.config.provider_chain()]), 24)
        if self.journal is not None:
            cached = self.journal.cache_get(key, self.config.cache_ttl_seconds)
            if cached is not None:
                return NarrativeOutcome("CACHED", cached["narrative"], cached["provider"], cached["model"])
        attempts: list[dict] = []
        final_status = "FAILED"
        for p in self.config.provider_chain():
            if p.kind == "deterministic":
                attempts.append({"provider": p.name, "status": "DETERMINISTIC_FALLBACK"})
                break
            if self._is_open(p):
                attempts.append({"provider": p.name, "status": "CIRCUIT_OPEN"})
                final_status = "CIRCUIT_OPEN"
                continue
            est = estimate_tokens(canonical_json(messages)) + p.max_output_tokens
            if not self._budget_allows(est, p):
                attempts.append({"provider": p.name, "status": "BUDGET_EXHAUSTED"})
                self._log(run_id, p, agent, "BUDGET_EXHAUSTED", 0)
                final_status = "BUDGET_EXHAUSTED"
                continue
            outcome = self._call_with_retries(p, messages, allowed_evidence_ids, agent, run_id)
            attempts.append({"provider": p.name, "status": outcome.status})
            if outcome.status == "OK":
                if self.journal is not None:
                    self.journal.cache_put(key, {"narrative": outcome.narrative, "provider": p.name,
                                                 "model": p.model})
                outcome.attempts = attempts
                return outcome
            final_status = outcome.status
        return NarrativeOutcome(final_status, attempts=attempts)

    def _call_with_retries(self, p: ProviderConfig, messages, allowed_ids, agent, run_id) -> NarrativeOutcome:
        provider = self.providers[p.name]
        sem = self._sems.setdefault(p.name, threading.Semaphore(1))
        for attempt in range(p.max_retries + 1):
            with self._lock:
                wait = p.min_seconds_between_requests - (self.clock() - self._last_call.get(p.name, -1e9))
            if wait > 0:
                self.sleep(wait)
            if not sem.acquire(timeout=p.timeout_seconds):
                return NarrativeOutcome("FAILED")
            started = self.clock()
            try:
                with self._lock:
                    self._last_call[p.name] = self.clock()
                resp = provider.complete(messages, max_tokens=p.max_output_tokens, timeout=p.timeout_seconds)
            except ProviderError as exc:
                latency = int((self.clock() - started) * 1000)
                self._log(run_id, p, agent, "ERROR", latency, error=redact(str(exc)))
                self._record_failure(p)
                if not exc.retryable or attempt == p.max_retries or self._is_open(p):
                    return NarrativeOutcome("FAILED")
                self.sleep(p.backoff_seconds * (2 ** attempt))
                continue
            finally:
                sem.release()
            latency = int((self.clock() - started) * 1000)
            cost = (resp.input_tokens / 1000) * p.input_cost_per_1k_tokens_usd + \
                   (resp.output_tokens / 1000) * p.output_cost_per_1k_tokens_usd
            try:
                parsed = parse_model_response(resp.text, allowed_ids)
            except NarrativeRejected as exc:
                self._log(run_id, p, agent, "REJECTED", latency, resp.input_tokens, resp.output_tokens, cost,
                          error=str(exc))
                return NarrativeOutcome("REJECTED", provider=p.name, model=p.model)
            self._log(run_id, p, agent, "OK", latency, resp.input_tokens, resp.output_tokens, cost)
            self._record_success(p)
            return NarrativeOutcome("OK", parsed, p.name, resp.model or p.model)
        return NarrativeOutcome("FAILED")

    def _log(self, run_id, p, agent, status, latency, input_tokens=0, output_tokens=0, cost=0.0, error=None):
        if self.journal is not None:
            self.journal.record_provider_call(run_id=run_id, provider=p.name, model=p.model or "n/a", agent=agent,
                                              status=status, latency_ms=latency, input_tokens=input_tokens,
                                              output_tokens=output_tokens, cost_usd=round(cost, 6), error=error)
