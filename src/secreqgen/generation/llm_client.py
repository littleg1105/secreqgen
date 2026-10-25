"""Ollama LLM client for SecReqGen.

Provides a clean interface to Ollama for text generation with
metrics tracking for thesis analysis.
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from secreqgen.exceptions import GenerationError, LLMConnectionError

_DEFAULT_BASE_URL = "http://localhost:11434"


def _resolve_base_url(explicit: Optional[str]) -> str:
    """Resolve the Ollama base URL with an explicit-arg > env-var > default precedence.

    Accepts the same forms the standard Ollama clients do via OLLAMA_HOST:
    ``host``, ``host:port``, ``http://host:port``, or ``https://host:port``.
    Bare hosts get ``http://`` prepended and ``:11434`` appended when missing.

    Pass ``explicit`` to override (e.g., tests). Leave it ``None`` to inherit
    ``OLLAMA_HOST`` from the environment — this is what lets the DEV box
    point at a remote GPU WORKER without code changes at call sites.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("OLLAMA_HOST", "").strip()
    if not env:
        return _DEFAULT_BASE_URL
    if env.startswith(("http://", "https://")):
        return env
    # bare host or host:port — assume http and add default port if absent
    if ":" not in env:
        env = f"{env}:11434"
    return f"http://{env}"


@dataclass
class LLMResponse:
    """Response from LLM generation.

    Attributes:
        content: The generated text content.
        model: Name of the model used.
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens generated.
        total_tokens: Total token count (prompt + completion).
        latency_ms: Time taken for generation in milliseconds.
        raw_response: Full API response for debugging.
    """

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    raw_response: dict


class OllamaClient:
    """Synchronous client for Ollama LLM API.

    Uses httpx for HTTP communication with configurable timeouts
    and retry logic. Tracks token usage and latency for thesis metrics.

    Example:
        >>> client = OllamaClient(model_name="llama3:8b")
        >>> if client.check_health():
        ...     response = client.generate("What is 2+2?")
        ...     print(response.content)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model_name: str = "llama3:8b",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        """Initialize the Ollama client.

        Args:
            base_url: Ollama server URL. If ``None`` (the default), the value
                of the ``OLLAMA_HOST`` environment variable is used, falling
                back to ``http://localhost:11434``. Pass an explicit string
                to override both.
            model_name: Model to use for generation (default: llama3:8b).
            temperature: Sampling temperature, lower = more focused (default: 0.1).
            max_tokens: Maximum tokens to generate (default: 4096).
            timeout: Request timeout in seconds (default: 120s for slow generation).
        """
        self.base_url = _resolve_base_url(base_url).rstrip("/")
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        # Initialize httpx client with timeout
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    def __del__(self) -> None:
        """Clean up httpx client on destruction."""
        if hasattr(self, "_client"):
            self._client.close()

    def check_health(self) -> bool:
        """Check if Ollama is running and the model is available.

        Returns:
            True if Ollama is healthy and model is available, False otherwise.
        """
        try:
            # Check if Ollama is running
            response = self._client.get(f"{self.base_url}/api/tags")
            if response.status_code != 200:
                return False

            # Check if the configured model is available
            data = response.json()
            models = data.get("models", [])
            available_names = [m.get("name", "") for m in models]

            # Check for exact match or partial match (e.g., "llama3:8b" in "llama3:8b-instruct")
            return any(
                self.model_name == name or self.model_name in name
                for name in available_names
            )
        except httpx.ConnectError:
            return False
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """List available models on the Ollama server.

        Returns:
            List of model names available on the server.

        Raises:
            LLMConnectionError: If unable to connect to Ollama.
        """
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [m.get("name", "") for m in data.get("models", [])]
        except httpx.ConnectError as e:
            raise LLMConnectionError(
                "Unable to connect to Ollama server",
                details=f"Is Ollama running? Check: {self.base_url}\nError: {e}",
            )
        except httpx.HTTPStatusError as e:
            raise LLMConnectionError(
                f"Ollama API error: {e.response.status_code}",
                details=str(e),
            )

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        format: Optional[str] = None,
    ) -> LLMResponse:
        """Generate text completion from a prompt.

        Args:
            prompt: The user prompt to generate from.
            system_prompt: Optional system prompt for context.
            format: Optional format constraint ("json" for JSON mode).

        Returns:
            LLMResponse with generated content and metrics.

        Raises:
            LLMConnectionError: If unable to connect to Ollama.
            GenerationError: If generation fails (timeout, API error, etc.).
        """
        # Build request payload
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            # think: False disables reasoning-mode preamble for thinking
            # models (qwen3.6, deepseek-r1, etc.) that would otherwise
            # consume num_predict on a "thinking" trace and leave the
            # final "response" field empty. No-op for non-thinking models.
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        if system_prompt:
            payload["system"] = system_prompt

        if format:
            payload["format"] = format

        # Track timing
        start_time = time.perf_counter()

        try:
            response = self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
        except httpx.ConnectError as e:
            raise LLMConnectionError(
                "Unable to connect to Ollama server",
                details=f"Is Ollama running? Start with: ollama serve\nURL: {self.base_url}\nError: {e}",
            )
        except httpx.TimeoutException as e:
            raise GenerationError(
                f"Generation timed out after {self.timeout}s",
                details=f"Consider increasing timeout or using a faster model.\nError: {e}",
            )
        except httpx.HTTPStatusError as e:
            raise GenerationError(
                f"Ollama API error: {e.response.status_code}",
                details=f"Response: {e.response.text}",
            )

        # Calculate latency
        latency_ms = (time.perf_counter() - start_time) * 1000

        # Parse response
        try:
            data = response.json()
        except Exception as e:
            raise GenerationError(
                "Failed to parse Ollama response",
                details=f"Response: {response.text[:500]}\nError: {e}",
            )

        # Extract token counts
        # Ollama returns: prompt_eval_count (input), eval_count (output)
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        return LLMResponse(
            content=data.get("response", ""),
            model=data.get("model", self.model_name),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=latency_ms,
            raw_response=data,
        )

    def generate_with_messages(
        self,
        messages: list[dict],
        format: Optional[str] = None,
    ) -> LLMResponse:
        """Generate using chat message format.

        This uses Ollama's /api/chat endpoint for multi-turn conversations.

        Args:
            messages: List of message dicts with "role" and "content" keys.
                     Roles: "system", "user", "assistant"
            format: Optional format constraint ("json" for JSON mode).

        Returns:
            LLMResponse with generated content and metrics.

        Raises:
            LLMConnectionError: If unable to connect to Ollama.
            GenerationError: If generation fails.
        """
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
            # think: False disables reasoning-mode preamble; see /api/generate
            # variant above for full rationale.
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        if format:
            payload["format"] = format

        start_time = time.perf_counter()

        try:
            response = self._client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
        except httpx.ConnectError as e:
            raise LLMConnectionError(
                "Unable to connect to Ollama server",
                details=f"Is Ollama running? Start with: ollama serve\nURL: {self.base_url}\nError: {e}",
            )
        except httpx.TimeoutException as e:
            raise GenerationError(
                f"Generation timed out after {self.timeout}s",
                details=f"Consider increasing timeout or using a faster model.\nError: {e}",
            )
        except httpx.HTTPStatusError as e:
            raise GenerationError(
                f"Ollama API error: {e.response.status_code}",
                details=f"Response: {e.response.text}",
            )

        latency_ms = (time.perf_counter() - start_time) * 1000

        try:
            data = response.json()
        except Exception as e:
            raise GenerationError(
                "Failed to parse Ollama response",
                details=f"Response: {response.text[:500]}\nError: {e}",
            )

        # Chat endpoint returns message content differently
        message = data.get("message", {})
        content = message.get("content", "")

        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        return LLMResponse(
            content=content,
            model=data.get("model", self.model_name),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=latency_ms,
            raw_response=data,
        )
