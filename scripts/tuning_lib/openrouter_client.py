"""Client for hosted models through OpenRouter (step 0 of the tuning runner).

The client pins one provider endpoint, switches reasoning off, sends no app
header and no user field, and records provider, model and cost for each call.
Nothing here is silent: an unexpected state raises a named error.

The module has no side effect at import time and makes no network call.
"""

from __future__ import annotations

import dataclasses
import getpass
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from secreqgen.generation.llm_client import LLMResponse

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPin:
    """One hosted model and the one endpoint that may serve it."""

    local_name: str
    model_id: str
    provider_tag: str
    expected_provider_name: str
    top_p: float
    top_k: int
    extra_sampling: Mapping[str, float]
    price_in_per_mtok: float
    price_out_per_mtok: float
    confirmed: bool


HOSTED_MODELS: dict[str, ModelPin] = {
    "gemma4:26b": ModelPin(
        local_name="gemma4:26b",
        model_id="google/gemma-4-26b-a4b-it",
        provider_tag="parasail/bf16",
        expected_provider_name="Parasail",  # confirmed by the run smoke1 on 16 November 2026
        top_p=0.95,
        top_k=64,
        extra_sampling={},
        price_in_per_mtok=0.09,
        price_out_per_mtok=0.30,
        confirmed=True,
    ),
    "qwen3.8:27b": ModelPin(
        local_name="qwen3.8:27b",
        model_id="qwen/qwen3.8-27b",
        provider_tag="deepinfra/bf16",
        expected_provider_name="DeepInfra",  # confirmed by the run smoke1 on 16 November 2026
        top_p=0.95,
        top_k=20,
        extra_sampling={"min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0},
        price_in_per_mtok=0.15,
        price_out_per_mtok=1.875,
        confirmed=True,
    ),
}
SAMPLING_COMMON: dict[str, float | int] = {"temperature": 0.1, "max_completion_tokens": 4096}
KEYCHAIN_SERVICE = "openrouter-api-key"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"
KEY_ENV_NAME = "OPENROUTER_API_KEY"
_ERROR_TEXT_LIMIT = 500
_RETRY_AFTER_MAX_S = 60
_GENERATION_WAITS_S: tuple[float, ...] = (1.0, 2.0, 4.0)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HostedError(Exception):
    """Base class. The message never holds the key, a header or a request body."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reply: HostedReply | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reply = reply


class TransientHostedError(HostedError):
    """A failure that may pass: 429, 5xx, timeout, connection failure."""


class FatalHostedError(HostedError):
    """The run must stop."""


class ContractViolation(FatalHostedError):
    """The answer broke a rule of the design (reasoning, provider, model)."""


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


class ApiKey:
    """Holds the key and never shows it in repr, str or an error text."""

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the key. Use it only inside build_headers."""
        return self._value

    def __repr__(self) -> str:
        return "ApiKey(***)"

    __str__ = __repr__


def load_api_key(
    *,
    environ: Mapping[str, str] = os.environ,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    account: str | None = None,
) -> ApiKey:
    """Read the key from the environment, else from the macOS Keychain."""
    env_value = environ.get(KEY_ENV_NAME, "").strip()
    if env_value:
        return ApiKey(env_value)
    advice = (
        "the OpenRouter key was not found. Store it once with: "
        f"security add-generic-password -a \"$USER\" -s {KEYCHAIN_SERVICE} -w "
        f"(the tool asks for the value). Or set {KEY_ENV_NAME} for one command."
    )
    cmd = [
        "/usr/bin/security",
        "find-generic-password",
        "-s",
        KEYCHAIN_SERVICE,
        "-a",
        account or getpass.getuser(),
        "-w",
    ]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FatalHostedError(f"{advice} (Keychain call failed: {type(exc).__name__})") from None
    if result.returncode != 0:
        raise FatalHostedError(f"{advice} (Keychain return code {result.returncode})")
    value = (result.stdout or "").strip()
    if not value:
        raise FatalHostedError(f"{advice} (Keychain gave an empty value)")
    return ApiKey(value)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def build_request_body(pin: ModelPin, system_prompt: str | None, user_prompt: str) -> dict:
    """Build the JSON body of one request. Checks it for private text."""
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    body: dict[str, Any] = {
        "model": pin.model_id,
        "messages": messages,
        "stream": False,
        "temperature": SAMPLING_COMMON["temperature"],
        "top_p": pin.top_p,
        "top_k": pin.top_k,
        "max_completion_tokens": SAMPLING_COMMON["max_completion_tokens"],
        "reasoning": {"effort": "none"},
        "provider": {
            "order": [pin.provider_tag],
            "only": [pin.provider_tag],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
        },
        "plugins": [{"id": "context-compression", "enabled": False}],
    }
    for name, value in pin.extra_sampling.items():
        if name in body:
            raise FatalHostedError(f"extra_sampling key '{name}' clashes with a fixed request field")
        body[name] = value
    from tuning_lib.guard import assert_no_private_text  # lazy: keeps this module light

    assert_no_private_text(json.dumps(body, ensure_ascii=False), what="request body")
    return body


def build_headers(key: ApiKey) -> dict[str, str]:
    """Build the request headers. No Referer, no title, no category."""
    return {
        "Authorization": f"Bearer {key.reveal()}",
        "Content-Type": "application/json",
        "X-OpenRouter-Metadata": "enabled",
        "X-OpenRouter-Cache": "false",
    }


# ---------------------------------------------------------------------------
# Reply
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostedReply:
    """One paid answer with its origin and cost. No header, no key, no body."""

    text: str
    generation_id: str
    response_model: str
    provider: str
    provider_source: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    reasoning_tokens_source: str
    cached_tokens: int
    cost_usd: float
    latency_ms: float
    transport_retries: int

    def to_dict(self) -> dict:
        """Return a JSON-safe dict."""
        return dataclasses.asdict(self)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _norm_provider(name: str) -> str:
    return "".join(name.split()).casefold()


class OpenRouterClient:
    """One client for one worker thread. It holds no module-level state."""

    def __init__(
        self,
        pin: ModelPin,
        key: ApiKey,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 300.0,
        max_transport_retries: int = 3,
        backoff_s: Sequence[float] = (2.0, 4.0, 8.0),
        sleep: Callable[[float], None] = time.sleep,
        structured_output: bool = False,
    ) -> None:
        if max_transport_retries < 0:
            raise ValueError("max_transport_retries must be 0 or more")
        if max_transport_retries > 0 and not backoff_s:
            raise ValueError("backoff_s must not be empty when retries are allowed")
        self.pin = pin
        self.model_name: str = pin.model_id
        self.last_reply: HostedReply | None = None
        self._key = key
        self._max_retries = max_transport_retries
        self._backoff = tuple(backoff_s)
        self._sleep = sleep
        self._structured_output = structured_output
        self._http = httpx.Client(transport=transport, timeout=timeout_s)

    def __repr__(self) -> str:
        return f"OpenRouterClient(model={self.pin.model_id!r})"

    __str__ = __repr__

    def close(self) -> None:
        """Close the HTTP client."""
        self._http.close()

    # -- helpers ------------------------------------------------------------

    def _scrub(self, text: str) -> str:
        secret = self._key.reveal()
        if secret:
            text = text.replace(secret, "***")
        return text[:_ERROR_TEXT_LIMIT]

    @staticmethod
    def _server_message(resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return "(no readable message)"
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message") is not None:
            return str(err["message"])
        return "(no readable message)"

    def _wait_for(self, attempt_index: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return float(min(max(int(retry_after.strip()), 0), _RETRY_AFTER_MAX_S))
            except ValueError:
                pass
        return float(self._backoff[min(attempt_index, len(self._backoff) - 1)])

    def _raise_for_status(self, resp: httpx.Response) -> None:
        status = resp.status_code
        msg = self._scrub(self._server_message(resp))
        if status == 400:
            raise FatalHostedError(f"HTTP 400: {msg}", status=status)
        if status in (401, 403):
            raise FatalHostedError("the key was refused or its limit is spent", status=status)
        if status == 402:
            raise FatalHostedError("not enough credit", status=status)
        if status == 404:
            raise FatalHostedError(
                "no endpoint matches the pin (check model id and provider tag)", status=status
            )
        raise FatalHostedError(f"HTTP {status}: {msg}", status=status)

    @staticmethod
    def _error_code(err: Any) -> int | None:
        if not isinstance(err, dict):
            return None
        code = err.get("code")
        if _is_int(code):
            return code
        if isinstance(code, str) and code.strip().isdigit():
            return int(code.strip())
        return None

    @staticmethod
    def _is_transient_error(err: Any, code: int | None) -> bool:
        """An error object inside an HTTP 200 answer that can pass (retry it)."""
        if code is not None and (code in (408, 429) or 500 <= code <= 599):
            return True
        if isinstance(err, dict):
            meta = err.get("metadata")
            if isinstance(meta, dict):
                kind = meta.get("error_type")
                if isinstance(kind, str) and kind.strip().lower() == "timeout":
                    return True
        return False

    def _post_with_retries(self, body: dict) -> tuple[dict, int, float]:
        """POST until a usable JSON answer comes. Return (data, retries, latency_ms)."""
        headers = build_headers(self._key)
        retries = 0
        while True:
            retryable_status: int | None = None
            retry_after: str | None = None
            start = time.perf_counter()
            try:
                resp = self._http.post(API_URL, headers=headers, json=body)
            except httpx.TimeoutException:
                resp = None
                retryable_status = None
                timed_out = True
            except httpx.HTTPError as exc:
                raise TransientHostedError(
                    f"connection failure: {type(exc).__name__}"
                ) from None
            else:
                timed_out = False
            latency_ms = (time.perf_counter() - start) * 1000.0
            data: dict | None = None
            if resp is not None:
                status = resp.status_code
                if status == 429 or 500 <= status <= 599:
                    retryable_status = status
                    retry_after = resp.headers.get("Retry-After")
                elif status != 200:
                    self._raise_for_status(resp)
                else:
                    cache_state = resp.headers.get("X-OpenRouter-Cache-Status", "")
                    if cache_state.strip().upper() == "HIT":
                        raise ContractViolation(
                            "cached reply (X-OpenRouter-Cache-Status: HIT); "
                            "every repeat must be a new generation",
                            status=200,
                        )
                    try:
                        parsed = resp.json()
                    except ValueError:
                        raise FatalHostedError(
                            "HTTP 200 answer is not JSON", status=200
                        ) from None
                    if not isinstance(parsed, dict):
                        raise FatalHostedError("HTTP 200 answer is not a JSON object", status=200)
                    err = parsed.get("error")
                    where = "error inside HTTP 200 answer"
                    if err is None:
                        choices = parsed.get("choices")
                        if (
                            isinstance(choices, list)
                            and choices
                            and isinstance(choices[0], dict)
                            and choices[0].get("error") is not None
                        ):
                            err = choices[0].get("error")
                            where = "the choice holds an error"
                    if err is not None:
                        code = self._error_code(err)
                        message = self._scrub(
                            str(err.get("message")) if isinstance(err, dict) else str(err)
                        )
                        if self._is_transient_error(err, code):
                            # a provider timeout or overload: it can pass, so try again
                            retryable_status = code
                        else:
                            raise FatalHostedError(
                                f"{where} (code {code}): {message}",
                                status=200,
                            )
                    else:
                        data = parsed
            if data is not None:
                return data, retries, latency_ms
            if retries >= self._max_retries:
                what = "timeout" if timed_out else f"status {retryable_status}"
                raise TransientHostedError(
                    f"{what} after {retries} retries", status=retryable_status
                )
            wait = self._wait_for(retries, retry_after)
            _log.debug("retry %d after transient failure, wait %.1f s", retries + 1, wait)
            self._sleep(wait)
            retries += 1

    def _provider_from_metadata(self, data: dict) -> str | None:
        meta = data.get("openrouter_metadata")
        if not isinstance(meta, dict):
            return None
        endpoints = meta.get("endpoints")
        available = endpoints.get("available") if isinstance(endpoints, dict) else None
        if not isinstance(available, list):
            return None
        for entry in available:
            if isinstance(entry, dict) and entry.get("selected") is True:
                provider = entry.get("provider")
                if isinstance(provider, str) and provider.strip():
                    return provider
        return None

    def _provider_from_generation(self, generation_id: str) -> str:
        headers = build_headers(self._key)
        for wait in _GENERATION_WAITS_S:
            self._sleep(wait)
            try:
                resp = self._http.get(
                    GENERATION_URL, headers=headers, params={"id": generation_id}
                )
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            try:
                payload = resp.json()
            except ValueError:
                continue
            record = payload.get("data") if isinstance(payload, dict) else None
            name = record.get("provider_name") if isinstance(record, dict) else None
            if isinstance(name, str) and name.strip():
                return name
        raise FatalHostedError("the provider cannot be read")

    # -- main call ----------------------------------------------------------

    def generate_reply(self, prompt: str, system_prompt: str | None = None) -> HostedReply:
        """Send one request and return a checked reply."""
        body = build_request_body(self.pin, system_prompt, prompt)
        data, retries, latency_ms = self._post_with_retries(body)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise FatalHostedError("the answer has no choices")
        choice = choices[0]
        if choice.get("error") is not None:
            raise FatalHostedError(
                self._scrub(f"the choice holds an error: {choice.get('error')}")
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise FatalHostedError("the answer has no message")
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise FatalHostedError("the message content is not text")

        usage = data.get("usage")
        if not isinstance(usage, dict):
            raise FatalHostedError("the answer has no usage block, so the cost cap cannot work")
        cost = usage.get("cost")
        if not _is_number(cost):
            raise FatalHostedError("cost is missing, so the cost cap cannot work")
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if not _is_int(prompt_tokens) or not _is_int(completion_tokens):
            raise FatalHostedError("usage token counts are missing or not integers")
        if prompt_tokens == 0:
            raise ContractViolation(
                "the answer reports prompt_tokens 0 (a cached or empty reply)", status=200
            )

        details = usage.get("completion_tokens_details")
        raw_reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        reasoning_text = (
            bool(message.get("reasoning"))
            or bool(message.get("reasoning_details"))
            or "<think>" in content
            or "</think>" in content
        )
        if raw_reasoning is None:
            reasoning_tokens = 0
            reasoning_source = "absent_assumed_zero"
        elif _is_int(raw_reasoning):
            reasoning_tokens = raw_reasoning
            reasoning_source = "usage"
        else:
            raise FatalHostedError("reasoning_tokens is not an integer")
        prompt_details = usage.get("prompt_tokens_details")
        cached = prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else 0
        if not _is_int(cached):
            cached = 0

        generation_id = data.get("id")
        if not isinstance(generation_id, str) or not generation_id:
            raise FatalHostedError("the answer has no generation id")
        response_model = data.get("model")
        if not isinstance(response_model, str):
            raise FatalHostedError("the answer has no model name")
        finish = choice.get("finish_reason")
        finish_reason = finish if isinstance(finish, str) else None

        def make(provider: str, source: str) -> HostedReply:
            return HostedReply(
                text=content,
                generation_id=generation_id,
                response_model=response_model,
                provider=provider,
                provider_source=source,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                reasoning_tokens_source=reasoning_source,
                cached_tokens=cached,
                cost_usd=float(cost),
                latency_ms=latency_ms,
                transport_retries=retries,
            )

        meta_provider = self._provider_from_metadata(data)

        if reasoning_tokens > 0 or reasoning_text:
            partial = make(meta_provider or "unknown", "metadata" if meta_provider else "unread")
            raise ContractViolation(
                "the answer holds reasoning (reasoning is not switched off); "
                f"reasoning_tokens={reasoning_tokens}",
                reply=partial,
            )

        if meta_provider is not None:
            reply = make(meta_provider, "metadata")
        else:
            try:
                gen_provider = self._provider_from_generation(generation_id)
            except FatalHostedError as exc:
                exc.reply = make("unknown", "unread")
                raise
            reply = make(gen_provider, "generation_endpoint")

        if _norm_provider(reply.provider) != _norm_provider(self.pin.expected_provider_name):
            raise ContractViolation(
                f"provider '{reply.provider}' is not the pinned provider "
                f"'{self.pin.expected_provider_name}'",
                reply=reply,
            )
        if not reply.response_model.startswith(self.pin.model_id):
            raise ContractViolation(
                f"answer model '{reply.response_model}' does not match the pin "
                f"'{self.pin.model_id}'",
                reply=reply,
            )
        meta = data.get("openrouter_metadata")
        attempts = meta.get("attempts") if isinstance(meta, dict) else None
        if isinstance(attempts, list) and len(attempts) > 1:
            raise ContractViolation(
                f"the call used {len(attempts)} attempts; the pin allows exactly one",
                reply=reply,
            )
        self.last_reply = reply
        return reply

    def generate(
        self, prompt: str, system_prompt: str | None = None, format: str | None = None
    ) -> LLMResponse:
        """Old call surface. The format argument is accepted and not sent."""
        if self._structured_output:
            raise NotImplementedError("structured output is not supported by this client")
        reply = self.generate_reply(prompt, system_prompt)
        self.last_reply = reply
        return LLMResponse(
            content=reply.text,
            model=reply.response_model,
            prompt_tokens=reply.prompt_tokens,
            completion_tokens=reply.completion_tokens,
            total_tokens=reply.prompt_tokens + reply.completion_tokens,
            latency_ms=reply.latency_ms,
            raw_response=reply.to_dict(),
        )
