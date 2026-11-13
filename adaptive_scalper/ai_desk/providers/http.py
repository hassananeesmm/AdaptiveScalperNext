"""Optional HTTP adapters (stdlib `urllib`, no extra dependency).

- `AnthropicProvider`: Anthropic Messages API.
- `OpenAICompatibleProvider`: any `/chat/completions` endpoint - xAI Grok
  (`https://api.x.ai/v1`), OpenAI-compatible hosts, or a local server such as
  Ollama / LM Studio (`http://127.0.0.1:11434/v1`, no key needed).

Each call sends only the prompt built by `security.build_prompt` (sanitized,
category-filtered research findings - never credentials, account identity or
broker identifiers), has a hard socket timeout, and turns every failure into
`ProviderError`. Error text is redacted before it is logged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from adaptive_scalper.ai_desk.providers.base import (
    Provider,
    ProviderError,
    ProviderResponse,
    estimate_tokens,
)
from adaptive_scalper.ai_desk.security import redact

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "user-agent": "adaptive-scalper-ai-desk",
                                          **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        retryable = exc.code in (408, 409, 425, 429) or exc.code >= 500
        raise ProviderError(f"HTTP {exc.code}", retryable=retryable) from None
    except TimeoutError as exc:
        raise ProviderError(f"timeout after {timeout}s") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(redact(f"connection error: {exc.reason}")) from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(f"malformed response body: {type(exc).__name__}", retryable=False) from None


class AnthropicProvider(Provider):
    kind = "anthropic"

    def complete(self, messages: list[dict], *, max_tokens: int, timeout: float) -> ProviderResponse:
        key = self.api_key()
        if not key:
            raise ProviderError(f"environment variable {self.cfg.api_key_env} is not set", retryable=False)
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {"model": self.cfg.model, "max_tokens": max_tokens, "system": system,
                "messages": [m for m in messages if m["role"] != "system"]}
        data = _post_json(self.cfg.base_url or ANTHROPIC_URL, body,
                          {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}, timeout)
        try:
            text = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("unexpected Anthropic response shape", retryable=False) from None
        usage = data.get("usage") or {}
        return ProviderResponse(text=text, input_tokens=int(usage.get("input_tokens") or estimate_tokens(
            json.dumps(messages))), output_tokens=int(usage.get("output_tokens") or estimate_tokens(text)),
            model=str(data.get("model") or self.cfg.model))


class OpenAICompatibleProvider(Provider):
    kind = "openai_compatible"

    def __init__(self, cfg, *, default_base_url: str = "http://127.0.0.1:11434/v1") -> None:
        super().__init__(cfg)
        self.base_url = (cfg.base_url or default_base_url).rstrip("/")

    def complete(self, messages: list[dict], *, max_tokens: int, timeout: float) -> ProviderResponse:
        headers = {}
        key = self.api_key()
        if self.cfg.api_key_env and not key:
            raise ProviderError(f"environment variable {self.cfg.api_key_env} is not set", retryable=False)
        if key:
            headers["authorization"] = f"Bearer {key}"
        body = {"model": self.cfg.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
        data = _post_json(f"{self.base_url}/chat/completions", body, headers, timeout)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("unexpected chat-completions response shape", retryable=False) from None
        usage = data.get("usage") or {}
        return ProviderResponse(text=text, input_tokens=int(usage.get("prompt_tokens") or estimate_tokens(
            json.dumps(messages))), output_tokens=int(usage.get("completion_tokens") or estimate_tokens(text)),
            model=str(data.get("model") or self.cfg.model))
