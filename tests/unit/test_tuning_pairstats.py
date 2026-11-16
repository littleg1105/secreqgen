"""Tests of scripts/tuning_lib/pairstats.py."""

from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from tuning_lib.pairstats import paired_summary  # noqa: E402


def test_empty():
    s = paired_summary([])
    assert (s.n, s.mean, s.se, s.up, s.equal, s.down, s.sign) == (0, None, None, 0, 0, 0, "")


def test_one_value():
    s = paired_summary([2.0])
    assert s.n == 1 and s.mean == 2.0 and s.se is None and s.sign == "+"


def test_five_values_by_hand():
    d = [1.0, 2.0, 3.0, 4.0, 5.0]
    s = paired_summary(d)
    assert s.mean == 3.0
    assert s.se == pytest.approx(statistics.stdev(d) / math.sqrt(5))
    assert s.se == pytest.approx(math.sqrt(2.5) / math.sqrt(5))


def test_tolerance_and_counts():
    s = paired_summary([1e-12, -1e-12, 0.5, -0.5, -0.7])
    assert (s.up, s.equal, s.down) == (1, 2, 2)


@pytest.mark.parametrize("d,sign", [([1, 2], "+"), ([-1, -2], "-"), ([1, -1], "0")])
def test_sign(d, sign):
    assert paired_summary(d).sign == sign


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_not_finite_raises(bad):
    with pytest.raises(ValueError):
        paired_summary([1.0, bad])
