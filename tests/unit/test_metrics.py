"""Unit tests for metrics calculator."""

import pytest

from secreqgen.evaluation.metrics import (
    ComparisonResult,
    MetricsBreakdown,
    MetricsCalculator,
)
from secreqgen.evaluation.semantic_matcher import MatchResult, MatchType


# Fixtures
@pytest.fixture
def calculator() -> MetricsCalculator:
    """Create a metrics calculator instance."""
    return MetricsCalculator()


@pytest.fixture
def perfect_matches() -> list[MatchResult]:
    """All generated requirements match ground truth."""
    return [
        MatchResult(
            generated_id="SR-001",
            generated_text="Password hashing requirement",
            ground_truth_id="GT-001",
            ground_truth_text="Secure password storage",
            similarity_score=0.92,
            match_type=MatchType.MATCH,
        ),
        MatchResult(
            generated_id="SR-002",
            generated_text="Session token security",
            ground_truth_id="GT-002",
            ground_truth_text="Session management",
            similarity_score=0.88,
            match_type=MatchType.MATCH,
        ),
    ]


@pytest.fixture
def mixed_matches() -> list[MatchResult]:
    """Mix of match, partial, and no_match results."""
    return [
        MatchResult(
            generated_id="SR-001",
            generated_text="Password hashing",
            ground_truth_id="GT-001",
            ground_truth_text="Password storage",
            similarity_score=0.90,
            match_type=MatchType.MATCH,
        ),
        MatchResult(
            generated_id="SR-002",
            generated_text="Session security",
            ground_truth_id="GT-002",
            ground_truth_text="Session management",
            similarity_score=0.78,
            match_type=MatchType.PARTIAL,
        ),
        MatchResult(
            generated_id="SR-003",
            generated_text="Unrelated requirement",
            ground_truth_id=None,
            ground_truth_text=None,
            similarity_score=0.45,
            match_type=MatchType.NO_MATCH,
        ),
    ]


@pytest.fixture
def all_hallucinations() -> list[MatchResult]:
    """All generated requirements are hallucinations."""
    return [
        MatchResult(
            generated_id="SR-001",
            generated_text="Made up requirement 1",
            ground_truth_id=None,
            ground_truth_text=None,
            similarity_score=0.30,
            match_type=MatchType.NO_MATCH,
        ),
        MatchResult(
            generated_id="SR-002",
            generated_text="Made up requirement 2",
            ground_truth_id=None,
            ground_truth_text=None,
            similarity_score=0.25,
            match_type=MatchType.NO_MATCH,
        ),
    ]


# MetricsBreakdown tests
class TestMetricsBreakdown:
    def test_summary_contains_all_metrics(self):
        """Summary should include all key metrics."""
        breakdown = MetricsBreakdown(
            true_positives=3,
            false_positives=1,
            false_negatives=2,
            partial_matches=1,
            total_generated=5,
            total_ground_truth=6,
            precision=0.75,
            recall=0.60,
            f1_score=0.67,
            hallucination_rate=0.20,
        )
        summary = breakdown.summary()

        assert "Precision:" in summary
        assert "75.00%" in summary
        assert "Recall:" in summary
        assert "60.00%" in summary
        assert "F1 Score:" in summary
        assert "Hallucination Rate:" in summary
        assert "True Positives:" in summary
        assert "3" in summary
        assert "False Positives:" in summary
        assert "1" in summary

    def test_to_evaluation_result_conversion(self):
        """Should convert to EvaluationResult correctly."""
        breakdown = MetricsBreakdown(
            true_positives=5,
            false_positives=2,
            false_negatives=1,
            partial_matches=1,
            total_generated=8,
            total_ground_truth=7,
            precision=0.714,
            recall=0.833,
            f1_score=0.769,
            hallucination_rate=0.25,
            matched_pairs=[("SR-001", "GT-001", 0.90)],
        )
        result = breakdown.to_evaluation_result()

        assert result.precision == 0.714
        assert result.recall == 0.833
        assert result.f1_score == 0.769
        assert result.hallucination_rate == 0.25
        assert result.true_positives == 5
        assert result.false_positives == 2
        assert result.false_negatives == 1
        assert result.matched_requirements == [("SR-001", "GT-001", 0.90)]


# MetricsCalculator.calculate() tests
class TestMetricsCalculatorCalculate:
    def test_empty_inputs(self, calculator):
        """Empty matches with no GT should return perfect scores."""
        metrics = calculator.calculate([], 0)

        assert metrics.true_positives == 0
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 0
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1_score == 1.0
        assert metrics.hallucination_rate == 0.0

    def test_perfect_matches(self, calculator, perfect_matches):
        """All matches should give perfect precision."""
        metrics = calculator.calculate(perfect_matches, total_ground_truth=2)

        assert metrics.true_positives == 2
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 0
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1_score == 1.0
        assert metrics.hallucination_rate == 0.0

    def test_all_hallucinations(self, calculator, all_hallucinations):
        """All hallucinations should give zero precision."""
        metrics = calculator.calculate(all_hallucinations, total_ground_truth=3)

        assert metrics.true_positives == 0
        assert metrics.false_positives == 2
        assert metrics.false_negatives == 3  # None of the 3 GT matched
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0
        assert metrics.f1_score == 0.0
        assert metrics.hallucination_rate == 1.0

    def test_mixed_matches(self, calculator, mixed_matches):
        """Mixed results should calculate metrics correctly."""
        # 1 MATCH, 1 PARTIAL, 1 NO_MATCH, with 4 total GT
        metrics = calculator.calculate(mixed_matches, total_ground_truth=4)

        assert metrics.true_positives == 1
        assert metrics.false_positives == 1  # The NO_MATCH
        assert metrics.partial_matches == 1
        # FN = 4 GT - 2 matched (MATCH + PARTIAL both consume GT)
        assert metrics.false_negatives == 2
        assert metrics.total_generated == 3

        # Precision = 1 / (1 + 1) = 0.5
        assert metrics.precision == pytest.approx(0.5)
        # Recall = 1 / (1 + 2) = 0.333...
        assert metrics.recall == pytest.approx(1 / 3)
        # F1 = 2 * 0.5 * 0.333 / (0.5 + 0.333) = 0.4
        assert metrics.f1_score == pytest.approx(0.4)
        # Hallucination = 1 / 3 = 0.333...
        assert metrics.hallucination_rate == pytest.approx(1 / 3)

    def test_matched_pairs_populated(self, calculator, perfect_matches):
        """Matched pairs should be populated correctly."""
        metrics = calculator.calculate(perfect_matches, total_ground_truth=2)

        assert len(metrics.matched_pairs) == 2
        assert ("SR-001", "GT-001", 0.92) in metrics.matched_pairs
        assert ("SR-002", "GT-002", 0.88) in metrics.matched_pairs

    def test_no_generated_with_gt(self, calculator):
        """No generated requirements with some GT should give zero recall."""
        metrics = calculator.calculate([], total_ground_truth=5)

        assert metrics.true_positives == 0
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 5
        assert metrics.precision == 0.0  # 0/0 -> 0
        assert metrics.recall == 0.0  # 0/5 = 0
        assert metrics.f1_score == 0.0
        assert metrics.hallucination_rate == 0.0

    def test_partial_matches_count_separately(self, calculator):
        """Partial matches should be counted separately from TP."""
        matches = [
            MatchResult(
                generated_id="SR-001",
                generated_text="Test",
                ground_truth_id="GT-001",
                ground_truth_text="Test",
                similarity_score=0.75,
                match_type=MatchType.PARTIAL,
            ),
            MatchResult(
                generated_id="SR-002",
                generated_text="Test",
                ground_truth_id="GT-002",
                ground_truth_text="Test",
                similarity_score=0.80,
                match_type=MatchType.PARTIAL,
            ),
        ]
        metrics = calculator.calculate(matches, total_ground_truth=2)

        assert metrics.true_positives == 0
        assert metrics.partial_matches == 2
        assert metrics.false_negatives == 0  # Both GT consumed by partials
        assert metrics.precision == 0.0  # No TP

    def test_more_generated_than_gt(self, calculator):
        """Should handle more generated than ground truth."""
        matches = [
            MatchResult(
                generated_id=f"SR-{i:03d}",
                generated_text=f"Req {i}",
                ground_truth_id=f"GT-{i:03d}" if i < 2 else None,
                ground_truth_text=f"GT {i}" if i < 2 else None,
                similarity_score=0.90 if i < 2 else 0.30,
                match_type=MatchType.MATCH if i < 2 else MatchType.NO_MATCH,
            )
            for i in range(5)
        ]
        metrics = calculator.calculate(matches, total_ground_truth=2)

        assert metrics.true_positives == 2
        assert metrics.false_positives == 3  # The 3 hallucinations
        assert metrics.false_negatives == 0  # All 2 GT matched
        assert metrics.precision == pytest.approx(2 / 5)
        assert metrics.recall == 1.0


# MetricsCalculator.compare_conditions() tests
class TestMetricsCalculatorCompare:
    def test_rag_better_than_baseline(self, calculator):
        """RAG outperforming baseline should show positive deltas."""
        baseline = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.60,
            recall=0.60,
            f1_score=0.60,
            hallucination_rate=0.40,
        )
        rag = MetricsBreakdown(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.80,
            recall=0.80,
            f1_score=0.80,
            hallucination_rate=0.20,
        )

        result = calculator.compare_conditions(baseline, rag)

        assert result.precision_delta == pytest.approx(0.20)
        assert result.recall_delta == pytest.approx(0.20)
        assert result.f1_delta == pytest.approx(0.20)
        assert result.hallucination_delta == pytest.approx(-0.20)  # Negative is better
        assert result.precision_improvement_pct == pytest.approx(33.33, rel=0.01)
        assert result.f1_improvement_pct == pytest.approx(33.33, rel=0.01)

    def test_meets_20_percent_threshold(self, calculator):
        """Should detect when RAG meets 20% improvement threshold."""
        baseline = MetricsBreakdown(
            true_positives=2,
            false_positives=3,
            false_negatives=3,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.40,
            recall=0.40,
            f1_score=0.40,
            hallucination_rate=0.60,
        )
        # RAG F1 = 0.50, which is >= 0.40 * 1.20 = 0.48
        rag = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.60,
            recall=0.60,
            f1_score=0.50,  # Exactly meeting 25% improvement
            hallucination_rate=0.40,
        )

        result = calculator.compare_conditions(baseline, rag, improvement_threshold=1.20)

        assert result.meets_threshold is True

    def test_does_not_meet_threshold(self, calculator):
        """Should detect when RAG doesn't meet threshold."""
        baseline = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.60,
            recall=0.60,
            f1_score=0.60,
            hallucination_rate=0.40,
        )
        # RAG F1 = 0.65, which is < 0.60 * 1.20 = 0.72
        rag = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=1,
            total_generated=6,
            total_ground_truth=5,
            precision=0.50,
            recall=0.60,
            f1_score=0.65,
            hallucination_rate=0.33,
        )

        result = calculator.compare_conditions(baseline, rag)

        assert result.meets_threshold is False

    def test_rag_worse_than_baseline(self, calculator):
        """RAG performing worse should show negative deltas."""
        baseline = MetricsBreakdown(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.80,
            recall=0.80,
            f1_score=0.80,
            hallucination_rate=0.20,
        )
        rag = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.60,
            recall=0.60,
            f1_score=0.60,
            hallucination_rate=0.40,
        )

        result = calculator.compare_conditions(baseline, rag)

        assert result.precision_delta == pytest.approx(-0.20)
        assert result.f1_delta == pytest.approx(-0.20)
        assert result.hallucination_delta == pytest.approx(0.20)  # Worse (higher)
        assert result.f1_improvement_pct == pytest.approx(-25.0)
        assert result.meets_threshold is False

    def test_baseline_zero_f1(self, calculator):
        """Should handle baseline with zero F1."""
        baseline = MetricsBreakdown(
            true_positives=0,
            false_positives=5,
            false_negatives=5,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.0,
            recall=0.0,
            f1_score=0.0,
            hallucination_rate=1.0,
        )
        rag = MetricsBreakdown(
            true_positives=2,
            false_positives=3,
            false_negatives=3,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.40,
            recall=0.40,
            f1_score=0.40,
            hallucination_rate=0.60,
        )

        result = calculator.compare_conditions(baseline, rag)

        # Any positive RAG F1 should meet threshold when baseline is 0
        assert result.meets_threshold is True
        assert result.f1_improvement_pct == 100.0  # Capped at 100%

    def test_custom_threshold(self, calculator):
        """Should use custom improvement threshold."""
        baseline = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.60,
            recall=0.60,
            f1_score=0.50,
            hallucination_rate=0.40,
        )
        rag = MetricsBreakdown(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.80,
            recall=0.80,
            f1_score=0.60,  # 20% improvement
            hallucination_rate=0.20,
        )

        # 20% threshold: 0.60 >= 0.50 * 1.20 = 0.60 -> meets
        result_20 = calculator.compare_conditions(baseline, rag, improvement_threshold=1.20)
        assert result_20.meets_threshold is True

        # 30% threshold: 0.60 < 0.50 * 1.30 = 0.65 -> doesn't meet
        result_30 = calculator.compare_conditions(baseline, rag, improvement_threshold=1.30)
        assert result_30.meets_threshold is False


# ComparisonResult tests
class TestComparisonResult:
    def test_summary_format(self, calculator):
        """Summary should be properly formatted."""
        baseline = MetricsBreakdown(
            true_positives=3,
            false_positives=2,
            false_negatives=2,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.60,
            recall=0.60,
            f1_score=0.60,
            hallucination_rate=0.40,
        )
        rag = MetricsBreakdown(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.80,
            recall=0.80,
            f1_score=0.80,
            hallucination_rate=0.20,
        )

        result = calculator.compare_conditions(baseline, rag)
        summary = result.summary()

        assert "RAG vs Baseline Comparison" in summary
        assert "Baseline" in summary
        assert "RAG" in summary
        assert "Delta" in summary
        assert "Improvement" in summary
        assert "MEETS" in summary  # Should meet 20% threshold

    def test_summary_shows_failure(self, calculator):
        """Summary should indicate when threshold not met."""
        baseline = MetricsBreakdown(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.80,
            recall=0.80,
            f1_score=0.80,
            hallucination_rate=0.20,
        )
        rag = MetricsBreakdown(
            true_positives=4,
            false_positives=1,
            false_negatives=1,
            partial_matches=0,
            total_generated=5,
            total_ground_truth=5,
            precision=0.80,
            recall=0.80,
            f1_score=0.80,  # Same as baseline, doesn't meet 20% improvement
            hallucination_rate=0.20,
        )

        result = calculator.compare_conditions(baseline, rag)
        summary = result.summary()

        assert "DOES NOT MEET" in summary


# Helper function tests
class TestHelperFunctions:
    def test_safe_divide_normal(self, calculator):
        """safe_divide should work for normal division."""
        assert calculator._safe_divide(10, 2) == 5.0
        assert calculator._safe_divide(1, 3) == pytest.approx(0.333, rel=0.01)

    def test_safe_divide_by_zero(self, calculator):
        """safe_divide should return 0 for division by zero."""
        assert calculator._safe_divide(10, 0) == 0.0
        assert calculator._safe_divide(0, 0) == 0.0

    def test_percentage_change_normal(self, calculator):
        """percentage_change should calculate correctly."""
        # 50% increase: 100 -> 150
        assert calculator._percentage_change(100, 150) == 50.0
        # 25% decrease: 100 -> 75
        assert calculator._percentage_change(100, 75) == -25.0

    def test_percentage_change_from_zero(self, calculator):
        """percentage_change from zero should handle edge cases."""
        # 0 -> 0 = 0% change
        assert calculator._percentage_change(0, 0) == 0.0
        # 0 -> positive = 100% (capped)
        assert calculator._percentage_change(0, 50) == 100.0
