import pytest

from secreqgen.evaluation.baseline import ComparativeResults
from secreqgen.evaluation.metrics import ComparisonResult, MetricsBreakdown
from secreqgen.evaluation.multi_run import (
    RunStatistics,
    aggregate_runs,
    collect_per_story_f1,
    per_story_paired_values,
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


def _story(sid: str, baseline_f1: float, rag_f1: float) -> StoryEvaluationResult:
    return StoryEvaluationResult(
        story_id=sid,
        story_title="t",
        ground_truth=[],
        baseline_result=None,
        rag_result=None,
        baseline_matches=[],
        rag_matches=[],
        baseline_metrics=_metrics(baseline_f1),
        rag_metrics=_metrics(rag_f1),
    )


def _run(stories: list[tuple[str, float, float]]) -> EvaluationPipelineResult:
    per_story = [_story(s, b, r) for s, b, r in stories]
    agg_b = _metrics(sum(b for _, b, _ in stories) / len(stories))
    agg_r = _metrics(sum(r for _, _, r in stories) / len(stories))
    cmp = ComparisonResult(
        baseline_metrics=agg_b,
        rag_metrics=agg_r,
        precision_delta=0.0,
        recall_delta=0.0,
        f1_delta=agg_r.f1_score - agg_b.f1_score,
        hallucination_delta=0.0,
        precision_improvement_pct=0.0,
        recall_improvement_pct=0.0,
        f1_improvement_pct=0.0,
        meets_threshold=False,
        improvement_threshold=1.20,
    )
    comparative = ComparativeResults(
        user_stories=[],
        baseline_results=[],
        rag_results=[],
        baseline_time_ms=0.0,
        rag_time_ms=0.0,
    )
    return EvaluationPipelineResult(
        per_story_results=per_story,
        aggregate_baseline_metrics=agg_b,
        aggregate_rag_metrics=agg_r,
        comparison=cmp,
        comparative_results=comparative,
    )


def test_g2_aggregate_runs_returns_keyed_dicts() -> None:
    runs = [_run([("US-001", 0.3, 0.4), ("US-002", 0.3, 0.5)]) for _ in range(3)]
    baseline_agg, rag_agg = aggregate_runs(runs)
    for key in ("precision", "recall", "f1_score", "hallucination_rate"):
        assert key in baseline_agg
        assert key in rag_agg
        assert isinstance(rag_agg[key], RunStatistics)


def test_g2_aggregate_runs_mean_uses_run_aggregates() -> None:
    runs = [_run([("US-001", 0.3, 0.4), ("US-002", 0.3, 0.5)]) for _ in range(3)]
    _, rag_agg = aggregate_runs(runs)
    assert rag_agg["f1_score"].mean == pytest.approx(0.45, abs=1e-6)
    assert len(rag_agg["f1_score"].values) == 3


def test_g2_collect_per_story_f1_keyed_by_story_id() -> None:
    runs = [_run([("US-001", 0.3, 0.4), ("US-002", 0.3, 0.5)]) for _ in range(3)]
    rag_per_story = collect_per_story_f1(runs, condition="rag")
    assert set(rag_per_story.keys()) == {"US-001", "US-002"}
    assert rag_per_story["US-001"] == [0.4, 0.4, 0.4]
    assert rag_per_story["US-002"] == [0.5, 0.5, 0.5]


def test_g2_per_story_paired_values_returns_equal_length_lists() -> None:
    runs = [_run([("US-A", 0.3, 0.4), ("US-B", 0.3, 0.5), ("US-C", 0.3, 0.6)]) for _ in range(3)]
    baseline_vals, rag_vals = per_story_paired_values(runs, metric="f1_score")
    assert len(baseline_vals) == len(rag_vals) == 3
    assert sorted(rag_vals) == pytest.approx([0.4, 0.5, 0.6])
    assert all(b == pytest.approx(0.3) for b in baseline_vals)
