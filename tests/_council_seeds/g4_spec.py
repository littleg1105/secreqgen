import json
from typing import Any

import pytest

from secreqgen.evaluation.baseline import ComparativeResults
from secreqgen.evaluation.metrics import ComparisonResult, MetricsBreakdown
from secreqgen.evaluation.multi_run import (
    MultiRunReport,
    build_multi_run_report,
    serialize_report,
)
from secreqgen.evaluation.pipeline import (
    EvaluationPipelineResult,
    StoryEvaluationResult,
)


def _metrics(f1: float) -> MetricsBreakdown:
    return MetricsBreakdown(
        true_positives=1,
        false_positives=1,
        false_negatives=1,
        partial_matches=0,
        total_generated=2,
        total_ground_truth=2,
        precision=0.5,
        recall=0.5,
        f1_score=f1,
        hallucination_rate=0.5,
    )


def _story(sid: str, b: float, r: float) -> StoryEvaluationResult:
    return StoryEvaluationResult(
        story_id=sid,
        story_title="t",
        ground_truth=[],
        baseline_result=None,
        rag_result=None,
        baseline_matches=[],
        rag_matches=[],
        baseline_metrics=_metrics(b),
        rag_metrics=_metrics(r),
    )


def _run(stories: list[tuple[str, float, float]]) -> EvaluationPipelineResult:
    per_story = [_story(s, b, r) for s, b, r in stories]
    agg_b = _metrics(sum(b for _, b, _ in stories) / len(stories))
    agg_r = _metrics(sum(r for _, _, r in stories) / len(stories))
    cmp = ComparisonResult(
        baseline_metrics=agg_b, rag_metrics=agg_r,
        precision_delta=0.0, recall_delta=0.0, f1_delta=0.0, hallucination_delta=0.0,
        precision_improvement_pct=0.0, recall_improvement_pct=0.0,
        f1_improvement_pct=0.0, meets_threshold=False, improvement_threshold=1.20,
    )
    comp = ComparativeResults(
        user_stories=[], baseline_results=[], rag_results=[],
        baseline_time_ms=0.0, rag_time_ms=0.0,
    )
    return EvaluationPipelineResult(
        per_story_results=per_story,
        aggregate_baseline_metrics=agg_b,
        aggregate_rag_metrics=agg_r,
        comparison=cmp,
        comparative_results=comp,
    )


def _make_runs(n_runs: int, n_stories: int) -> list[EvaluationPipelineResult]:
    return [
        _run([
            ("US-" + str(i), 0.30 + 0.005 * i, 0.45 + 0.005 * i + 0.001 * run)
            for i in range(n_stories)
        ])
        for run in range(n_runs)
    ]


def test_g4_build_report_returns_multirun_report() -> None:
    runs = _make_runs(n_runs=5, n_stories=8)
    report = build_multi_run_report(runs, config={"model": "test"})
    assert isinstance(report, MultiRunReport)
    assert report.n_runs == 5
    assert report.config == {"model": "test"}


def test_g4_report_records_per_story_as_primary_axis() -> None:
    runs = _make_runs(n_runs=5, n_stories=8)
    report = build_multi_run_report(runs, config={})
    assert report.paired_test_per_story_f1.pairing_axis == "per-story"
    assert report.paired_test_per_story_f1.n_pairs == 8
    assert report.paired_test_per_run_f1.pairing_axis == "per-run"
    assert report.paired_test_per_run_f1.n_pairs == 5


def test_g4_report_aggregates_baseline_and_rag() -> None:
    runs = _make_runs(n_runs=4, n_stories=3)
    report = build_multi_run_report(runs, config={})
    assert "f1_score" in report.baseline_aggregate
    assert "f1_score" in report.rag_aggregate
    assert len(report.baseline_aggregate["f1_score"].values) == 4


def test_g4_serialize_report_produces_valid_json() -> None:
    runs = _make_runs(n_runs=3, n_stories=4)
    report = build_multi_run_report(runs, config={"k": "v"})
    serialized = serialize_report(report)
    parsed: dict[str, Any] = json.loads(serialized)
    assert parsed["n_runs"] == 3
    assert "baseline_aggregate" in parsed
    assert "rag_aggregate" in parsed
