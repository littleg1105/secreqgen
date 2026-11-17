"""Progress events of the use cases."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProgressEvent:
    """One story is done. The error is set when the story failed.

    The outcome is the result of the story (a ThreatListOutcome or a
    GenerationResult). It is None when the story failed.
    """

    done: int
    total: int
    story_id: str
    error: Exception | None = None
    # The outcome stays out of the comparison, so that two events with the same
    # counts are equal.
    outcome: object | None = field(default=None, compare=False)


ProgressCallback = Callable[[ProgressEvent], None]
