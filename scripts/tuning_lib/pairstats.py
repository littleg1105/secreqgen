"""Paired summary used by the lab report and by the judging scorer.

One function, so that "stories up, equal, down" means the same everywhere.
Importing this module has no side effect.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PairedSummary:
    """Summary of paired differences (new minus old)."""

    n: int
    mean: float | None  # None when n == 0
    se: float | None  # sample sd / sqrt(n); None when n < 2
    up: int  # differences above +tol
    equal: int  # |difference| <= tol
    down: int  # differences below -tol
    sign: str  # "+", "-" or "0" (sign of the mean); "" when n == 0


def paired_summary(diffs: Sequence[float], *, tol: float = 1e-9) -> PairedSummary:
    """Summarise differences. A value that is not finite raises ValueError."""
    values = [float(d) for d in diffs]
    for v in values:
        if not math.isfinite(v):
            raise ValueError(f"a difference is not finite: {v!r}")
    n = len(values)
    if n == 0:
        return PairedSummary(0, None, None, 0, 0, 0, "")
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(n) if n >= 2 else None
    up = sum(1 for v in values if v > tol)
    down = sum(1 for v in values if v < -tol)
    sign = "+" if mean > tol else "-" if mean < -tol else "0"
    return PairedSummary(n, mean, se, up, n - up - down, down, sign)
