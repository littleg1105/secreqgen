import math

import numpy as np
import pytest

from secreqgen.evaluation.multi_run import (
    PairedTestResult,
    RunStatistics,
    cohens_d_z_paired,
    paired_test_with_sensitivity,
)


def test_g1_run_statistics_dataclass_holds_named_fields() -> None:
    s = RunStatistics(
        metric_name="f1_score",
        mean=0.5,
        std=0.1,
        min=0.4,
        max=0.6,
        values=[0.4, 0.5, 0.6],
    )
    assert s.metric_name == "f1_score"
    assert s.mean == pytest.approx(0.5)
    assert s.std == pytest.approx(0.1)
    assert s.values == [0.4, 0.5, 0.6]


def test_g1_cohens_d_z_matches_textbook_formula() -> None:
    baseline = [0.30, 0.32, 0.28, 0.31, 0.29, 0.30, 0.33, 0.27]
    rag = [0.42, 0.45, 0.39, 0.44, 0.41, 0.43, 0.46, 0.38]
    diffs = np.array(rag) - np.array(baseline)
    expected = float(np.mean(diffs) / np.std(diffs, ddof=1))
    d, label = cohens_d_z_paired(baseline, rag)
    assert math.isclose(d, expected, rel_tol=1e-6)
    assert isinstance(label, str)
    assert label.lower() in {"negligible", "small", "medium", "large"}


def test_g1_cohens_d_z_large_when_clear_separation() -> None:
    baseline = [0.0, 0.01, -0.01, 0.0, 0.02, -0.02, 0.0, 0.01, -0.01, 0.0]
    rag = [1.0, 1.05, 0.95, 1.0, 1.02, 0.98, 1.0, 1.01, 0.99, 1.0]
    d, label = cohens_d_z_paired(baseline, rag)
    assert d > 0.8
    assert label.lower() == "large"


def test_g1_paired_test_returns_named_fields() -> None:
    baseline = [0.30, 0.25, 0.40, 0.35, 0.20, 0.45, 0.32, 0.28, 0.38, 0.33]
    rag = [0.42, 0.38, 0.50, 0.48, 0.35, 0.55, 0.45, 0.40, 0.50, 0.46]
    result = paired_test_with_sensitivity(baseline, rag, pairing_axis="per-story")
    assert isinstance(result, PairedTestResult)
    assert result.pairing_axis == "per-story"
    assert "wilcoxon" in result.primary_test.lower()
    assert "t" in result.sensitivity_test.lower()
    assert 0.0 <= result.primary_p_value <= 1.0
    assert 0.0 <= result.sensitivity_p_value <= 1.0
    assert 0.0 <= result.shapiro_wilk_p <= 1.0
    assert result.n_pairs == len(baseline)


def test_g1_paired_test_significant_when_rag_clearly_better() -> None:
    baseline = [0.20, 0.21, 0.19, 0.20, 0.22, 0.18, 0.20, 0.21, 0.20, 0.19]
    rag = [0.45, 0.44, 0.46, 0.45, 0.43, 0.47, 0.45, 0.44, 0.46, 0.45]
    result = paired_test_with_sensitivity(baseline, rag, pairing_axis="per-run")
    assert result.primary_p_value < 0.05
    assert result.sensitivity_p_value < 0.05
    assert result.d_z > 0
