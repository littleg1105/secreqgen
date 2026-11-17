"""Progress events of the use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    """One story is done. The error is set when the story failed."""

    done: int
    total: int
    story_id: str
    error: Exception | None = None


ProgressCallback = Callable[[ProgressEvent], None]
