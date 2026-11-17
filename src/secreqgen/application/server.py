"""Check the model server with a short time limit.

check_model_server never raises. It tells "the server is down" from "a model
is missing". A GUI runs it before a run and for its status light.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    """One model that the server holds."""

    name: str
    size_bytes: int | None
    parameter_size: str | None
    quantization: str | None
    family: str | None


@dataclass(frozen=True)
class ServerStatus:
    """The answer of the model server."""

    base_url: str
    reachable: bool
    models: tuple[ModelInfo, ...]
    error: str | None
    seconds: float


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _model_info(item: Any) -> ModelInfo | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("model")
    if not isinstance(name, str) or not name:
        return None
    details = item.get("details")
    if not isinstance(details, dict):
        details = {}
    size = item.get("size")
    return ModelInfo(
        name=name,
        size_bytes=size if isinstance(size, int) and size >= 0 else None,
        parameter_size=_text_or_none(details.get("parameter_size")),
        quantization=_text_or_none(details.get("quantization_level")),
        family=_text_or_none(details.get("family")),
    )


def _resolve_base_url(cfg: Any, base_url: str | None) -> str:
    if base_url:
        return base_url
    if cfg is None:
        from secreqgen.config import get_config

        cfg = get_config()
    return cfg.llm.base_url


def check_model_server(
    cfg: Any = None, *, base_url: str | None = None, timeout: float = 3.0
) -> ServerStatus:
    """Ask the server for its models. This function never raises."""
    start = time.monotonic()
    url = ""

    def status(
        reachable: bool, models: tuple[ModelInfo, ...], error: str | None
    ) -> ServerStatus:
        return ServerStatus(url, reachable, models, error, time.monotonic() - start)

    try:
        import httpx

        url = _resolve_base_url(cfg, base_url)
        try:
            response = httpx.get(
                url.rstrip("/") + "/api/tags", timeout=httpx.Timeout(timeout)
            )
        except httpx.TimeoutException:
            return status(False, (), f"timeout after {timeout:g} s")
        except httpx.ConnectError:
            return status(False, (), f"Nothing answers at {url}")
        except (httpx.HTTPError, OSError):
            return status(False, (), f"No valid answer from {url}")
        if response.status_code != 200:
            return status(False, (), f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            return status(False, (), f"The answer of {url} is not JSON")
        items = data.get("models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return status(False, (), f"The answer of {url} has no model list")
        models = tuple(m for m in map(_model_info, items) if m is not None)
        return status(True, models, None)
    except Exception as exc:  # the caller must never see an error
        return status(False, (), f"Check failed: {type(exc).__name__}")


def model_available(status: ServerStatus, name: str) -> bool:
    """Tell if the server holds the model. The rule is the one of check_health."""
    if not name or not name.strip():
        return False
    return any(name == m.name or name in m.name for m in status.models)


def model_check_text(status: ServerStatus, names: list[str]) -> list[str]:
    """Return plain sentences for the problems. An empty list means: all well."""
    if not status.reachable:
        reason = f" ({status.error})" if status.error else ""
        return [
            f"The model server does not answer at {status.base_url}{reason}. "
            "Start Ollama, then choose Check again."
        ]
    problems = []
    for name in names:
        if not name or not name.strip():
            problems.append("A model name is empty.")
        elif not model_available(status, name):
            problems.append(f"The model {name} is missing. Run: ollama pull {name}")
    return problems
