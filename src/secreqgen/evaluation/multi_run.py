"""Multi-run aggregation primitives for variance characterization.

This module is the foundation for the `compare-runs` workflow: it provides
the dataclasses and pure statistical primitives used by later orchestration
layers. Per the project's revised proposal (post-council, 2026-05-03), the
primary inferential test is Wilcoxon signed-rank with paired t-test reported
as a sensitivity check, and the named effect size is paired Cohen's d_z.

This module performs no I/O and does not import from the evaluation
pipeline; aggregation, orchestration, and reporting live in sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats


@dataclass
class RunStatistics:
    """Mean / std / range for one metric across N runs of one condition."""

    metric_name: str
    mean: float
    std: float
    min: float
    max: float
    values: list[float] = field(default_factory=list)


@dataclass
class PairedTestResult:
    """Paired test result for either run-level or story-level pairing.

    Wilcoxon signed-rank is the pre-registered primary; the paired t-test
    is reported as a sensitivity check and Shapiro-Wilk on the paired
    differences is recorded so reviewers can interpret the t-test result.
    """

    pairing_axis: str
    primary_test: str
    primary_statistic: float
    primary_p_value: float
    sensitivity_test: str
    sensitivity_statistic: float
    sensitivity_p_value: float
    shapiro_wilk_p: float
    d_z: float
    d_z_interpretation: str
    n_pairs: int


def _interpret_d_z(d_z: float) -> str:
    """Cohen 1988 interpretation thresholds applied to |d_z|.

    Adds a "negligible" bucket below 0.2 so that small null-effect samples
    are not labelled "small", which would overstate them.
    """
    magnitude = abs(d_z)
    if magnitude < 0.2:
        return "negligible"
    if magnitude < 0.5:
        return "small"
    if magnitude < 0.8:
        return "medium"
    return "large"


def cohens_d_z_paired(
    baseline_values: list[float],
    rag_values: list[float],
) -> tuple[float, str]:
    """Paired-sample Cohen's d_z (NOT generic Cohen's d).

    d_z = mean(rag - baseline) / std(rag - baseline, ddof=1)

    The 'z' subscript distinguishes this from d_av and d_rm, the other
    common paired-sample variants. Returns the numeric d_z and a
    Cohen-1988 label ("negligible" | "small" | "medium" | "large").
    """
    if len(baseline_values) != len(rag_values):
        raise ValueError(
            f"baseline ({len(baseline_values)}) and rag ({len(rag_values)}) "
            "must have equal length for paired comparison"
        )
    if len(baseline_values) < 2:
        raise ValueError("need at least 2 paired observations for d_z")

    diffs = np.asarray(rag_values, dtype=float) - np.asarray(
        baseline_values, dtype=float
    )
    diff_std = float(np.std(diffs, ddof=1))
    if diff_std == 0.0:
        # All differences identical — effect is either exactly zero
        # (negligible) or perfectly consistent in one direction (treat as
        # "large" only when the constant is non-zero).
        mean_diff = float(np.mean(diffs))
        if mean_diff == 0.0:
            return 0.0, "negligible"
        return float("inf") if mean_diff > 0 else float("-inf"), "large"

    d_z = float(np.mean(diffs) / diff_std)
    return d_z, _interpret_d_z(d_z)


def paired_test_with_sensitivity(
    baseline_values: list[float],
    rag_values: list[float],
    pairing_axis: str,
) -> PairedTestResult:
    """Run Wilcoxon (primary) + paired t-test (sensitivity) + Shapiro-Wilk.

    The primary test is fixed to Wilcoxon for pre-registration discipline:
    we do NOT switch primaries based on the Shapiro-Wilk outcome. The
    Shapiro-Wilk p-value is reported so reviewers can judge whether the
    t-test sensitivity check is interpretable.
    """
    if len(baseline_values) != len(rag_values):
        raise ValueError(
            f"baseline ({len(baseline_values)}) and rag ({len(rag_values)}) "
            "must have equal length for paired comparison"
        )
    n_pairs = len(baseline_values)
    if n_pairs < 2:
        raise ValueError("need at least 2 paired observations for paired test")

    baseline_arr = np.asarray(baseline_values, dtype=float)
    rag_arr = np.asarray(rag_values, dtype=float)
    diffs = rag_arr - baseline_arr

    # Primary: Wilcoxon signed-rank. zero_method="wilcox" is the default and
    # drops zero differences; if every diff is zero the test is undefined.
    if np.all(diffs == 0):
        wilcoxon_stat = 0.0
        wilcoxon_p = 1.0
    else:
        wilcoxon_result = stats.wilcoxon(rag_arr, baseline_arr)
        wilcoxon_stat = float(wilcoxon_result.statistic)
        wilcoxon_p = float(wilcoxon_result.pvalue)

    # Sensitivity: paired t-test on the same pairs.
    t_result = stats.ttest_rel(rag_arr, baseline_arr)
    t_stat = float(t_result.statistic)
    t_p = float(t_result.pvalue)

    # Normality of differences for interpretability of the t-test result.
    # Shapiro-Wilk needs at least 3 observations; below that report 1.0
    # (cannot reject normality with no data).
    if n_pairs >= 3 and np.std(diffs, ddof=1) > 0:
        shapiro_p = float(stats.shapiro(diffs).pvalue)
    else:
        shapiro_p = 1.0

    d_z, d_z_label = cohens_d_z_paired(baseline_values, rag_values)

    return PairedTestResult(
        pairing_axis=pairing_axis,
        primary_test="wilcoxon-signed-rank",
        primary_statistic=wilcoxon_stat,
        primary_p_value=wilcoxon_p,
        sensitivity_test="paired-t-test",
        sensitivity_statistic=t_stat,
        sensitivity_p_value=t_p,
        shapiro_wilk_p=shapiro_p,
        d_z=d_z,
        d_z_interpretation=d_z_label,
        n_pairs=n_pairs,
    )
