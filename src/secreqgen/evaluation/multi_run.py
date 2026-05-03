"""Multi-run aggregation primitives for variance characterization.

This module is the foundation for the `compare-runs` workflow: it provides
the dataclasses, pure statistical primitives, and the per-run / per-story
aggregation helpers that the later orchestration and reporting layers
consume. Per the project's revised proposal (post-council, 2026-05-03),
the primary inferential test is Wilcoxon signed-rank with paired t-test
reported as a sensitivity check, the named effect size is paired Cohen's
d_z, and the primary pairing axis is per-story (not per-run).

This module performs no I/O. The aggregation helpers operate on the
``EvaluationPipelineResult`` data shape; the pipeline is imported only
under ``TYPE_CHECKING`` so that runtime use is fully decoupled and unit
tests can construct lightweight fixtures. Orchestration and reporting
live in sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import numpy as np
from scipy import stats

if TYPE_CHECKING:
    from pathlib import Path

    from secreqgen.evaluation.pipeline import (
        EvaluationPipeline,
        EvaluationPipelineResult,
    )


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


# ─────────────────────────────────────────────────────────────────────
# Aggregation over EvaluationPipelineResult
# ─────────────────────────────────────────────────────────────────────

_AGGREGATE_METRIC_NAMES: tuple[str, ...] = (
    "precision",
    "recall",
    "f1_score",
    "hallucination_rate",
)
_PAIRED_METRIC_NAMES: frozenset[str] = frozenset(_AGGREGATE_METRIC_NAMES)


def _run_statistics(values: list[float], metric_name: str) -> RunStatistics:
    arr = np.asarray(values, dtype=float)
    # Sample stdev (ddof=1) is the conventional "report ± std" denominator.
    # Falls back to 0.0 when N < 2 because ddof=1 is undefined there.
    std = float(np.std(arr, ddof=1)) if arr.size >= 2 else 0.0
    return RunStatistics(
        metric_name=metric_name,
        mean=float(arr.mean()),
        std=std,
        min=float(arr.min()),
        max=float(arr.max()),
        values=list(values),
    )


def aggregate_runs(
    results: list[EvaluationPipelineResult],
) -> tuple[dict[str, RunStatistics], dict[str, RunStatistics]]:
    """Mean / std / min / max for precision, recall, F1, and hallucination
    rate across N runs, separately for baseline and RAG.

    Reads each run's dataset-level ``aggregate_baseline_metrics`` and
    ``aggregate_rag_metrics`` — i.e. the headline numbers a single
    ``evaluate run`` would print — so the resulting dicts characterise
    run-to-run variability in those headlines. This is the per-run
    sensitivity axis; per-story pairing (the primary axis) is computed
    by ``per_story_paired_values``.

    Returns:
        ``(baseline_aggregate, rag_aggregate)`` — each a dict keyed by
        metric name and valued by ``RunStatistics``.
    """
    if not results:
        raise ValueError("aggregate_runs requires at least one run")

    baseline_aggregate: dict[str, RunStatistics] = {}
    rag_aggregate: dict[str, RunStatistics] = {}
    for name in _AGGREGATE_METRIC_NAMES:
        baseline_values = [
            float(getattr(run.aggregate_baseline_metrics, name)) for run in results
        ]
        rag_values = [
            float(getattr(run.aggregate_rag_metrics, name)) for run in results
        ]
        baseline_aggregate[name] = _run_statistics(baseline_values, name)
        rag_aggregate[name] = _run_statistics(rag_values, name)

    return baseline_aggregate, rag_aggregate


def collect_per_story_f1(
    results: list[EvaluationPipelineResult],
    condition: str,
) -> dict[str, list[float]]:
    """Per-story F1 trajectory across runs for the named condition.

    Returns ``{story_id: [f1_run_1, ..., f1_run_N]}``. The dict is keyed
    by ``story_id`` (not list position) so that out-of-order or
    partially-failed runs are realigned correctly: a story present in
    only some runs simply gets a shorter list.

    Args:
        results: One ``EvaluationPipelineResult`` per run.
        condition: ``"baseline"`` or ``"rag"``.
    """
    if condition == "baseline":
        attr = "baseline_metrics"
    elif condition == "rag":
        attr = "rag_metrics"
    else:
        raise ValueError(
            f"condition must be 'baseline' or 'rag', got {condition!r}"
        )

    per_story: dict[str, list[float]] = {}
    for run in results:
        for story_result in run.per_story_results:
            metrics = getattr(story_result, attr)
            per_story.setdefault(story_result.story_id, []).append(
                float(metrics.f1_score)
            )
    return per_story


def per_story_paired_values(
    results: list[EvaluationPipelineResult],
    metric: str,
) -> tuple[list[float], list[float]]:
    """Equal-length baseline/RAG vectors for per-story paired tests.

    For each story present in ``results``, takes the mean of the named
    metric across the runs in which the story appears, separately for
    baseline and RAG. The two returned lists are aligned by index — the
    i-th entry in each is the same story — and ordered by first-seen
    appearance so the pairing is deterministic.

    This is the input shape the proposal's PRIMARY inferential test
    (per-story Wilcoxon on ``f1_score``) consumes via
    ``paired_test_with_sensitivity``.

    Args:
        results: One ``EvaluationPipelineResult`` per run.
        metric: One of ``"precision"``, ``"recall"``, ``"f1_score"``,
            ``"hallucination_rate"``.
    """
    if not results:
        raise ValueError("per_story_paired_values requires at least one run")
    if metric not in _PAIRED_METRIC_NAMES:
        raise ValueError(
            f"unsupported metric {metric!r}; expected one of "
            f"{sorted(_PAIRED_METRIC_NAMES)}"
        )

    baseline_by_story: dict[str, list[float]] = {}
    rag_by_story: dict[str, list[float]] = {}
    for run in results:
        for story_result in run.per_story_results:
            sid = story_result.story_id
            baseline_by_story.setdefault(sid, []).append(
                float(getattr(story_result.baseline_metrics, metric))
            )
            rag_by_story.setdefault(sid, []).append(
                float(getattr(story_result.rag_metrics, metric))
            )

    baseline_values: list[float] = []
    rag_values: list[float] = []
    # Iterate baseline_by_story to fix the ordering; rag_by_story has the
    # same keys because both are populated in the same loop.
    for sid in baseline_by_story:
        baseline_values.append(float(np.mean(baseline_by_story[sid])))
        rag_values.append(float(np.mean(rag_by_story[sid])))
    return baseline_values, rag_values


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────

def run_multi_evaluation(
    pipeline: EvaluationPipeline,
    stories_path: Path,
    ground_truth_path: Path,
    n_runs: int,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[EvaluationPipelineResult]:
    """Drive ``pipeline.run()`` N times sequentially and return the results.

    Sequential by design: Ollama is single-threaded and the bottleneck is
    generation latency, so concurrency at this layer would only serialize
    on the model queue while inflating peak memory. Keeping it sequential
    also leaves per-run wall-clock timing interpretable for the
    ``RunStatistics`` consumers downstream.

    The pipeline-internal phase callbacks (data load, generation, scoring)
    are intentionally not forwarded; this layer reports coarser
    run-boundary progress so a caller can render a "run 3 of 20"
    progress bar without flooding the UI.

    Args:
        pipeline: A configured ``EvaluationPipeline`` whose ``run()``
            accepts ``(stories_path, ground_truth_path)``. Duck-typed so
            unit tests can substitute a ``MagicMock``.
        stories_path: Forwarded unchanged to each pipeline invocation.
        ground_truth_path: Forwarded unchanged to each pipeline invocation.
        n_runs: Number of times to invoke ``pipeline.run``. Must be >= 1.
        progress_callback: Optional ``(current, total, phase)`` callable.
            ``current`` is the 1-indexed run number, ``total`` equals
            ``n_runs``, and ``phase`` is a short status string. Invoked
            both at run start and on completion.

    Returns:
        Per-run ``EvaluationPipelineResult`` objects in invocation order
        — index ``i`` is the result of the ``(i + 1)``-th call.
    """
    if n_runs < 1:
        raise ValueError(f"n_runs must be >= 1, got {n_runs}")

    results: list[EvaluationPipelineResult] = []
    for i in range(n_runs):
        current = i + 1
        if progress_callback is not None:
            progress_callback(current, n_runs, f"starting run {current}/{n_runs}")
        result = pipeline.run(stories_path, ground_truth_path)
        results.append(result)
        if progress_callback is not None:
            progress_callback(current, n_runs, f"completed run {current}/{n_runs}")
    return results
