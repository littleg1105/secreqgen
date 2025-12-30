"""Metrics calculation for evaluation framework.

Computes Precision, Recall, F1, and hallucination rate from match results
to quantitatively compare RAG vs baseline generation for the thesis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from secreqgen.evaluation.semantic_matcher import MatchResult, MatchType
from secreqgen.generation.schemas import EvaluationResult


@dataclass
class MetricsBreakdown:
    """Detailed metrics breakdown for a generation evaluation.

    Attributes:
        true_positives: Count of generated requirements matching ground truth (sim >= 0.85).
        false_positives: Count of generated requirements with no GT match (hallucinations).
        false_negatives: Count of GT requirements not matched by any generated.
        partial_matches: Count of partial matches (0.70 <= sim < 0.85) flagged for review.
        total_generated: Total number of generated requirements evaluated.
        total_ground_truth: Total number of ground truth requirements.
        precision: TP / (TP + FP). Fraction of generated that are correct.
        recall: TP / (TP + FN). Fraction of GT that were found.
        f1_score: Harmonic mean of precision and recall.
        hallucination_rate: FP / total_generated. Fraction of generated that are made up.
        matched_pairs: List of (generated_id, ground_truth_id, similarity) for matches.
    """

    true_positives: int
    false_positives: int
    false_negatives: int
    partial_matches: int
    total_generated: int
    total_ground_truth: int
    precision: float
    recall: float
    f1_score: float
    hallucination_rate: float
    matched_pairs: list[tuple[str, str, float]] = field(default_factory=list)

    def to_evaluation_result(self) -> EvaluationResult:
        """Convert to EvaluationResult for compatibility with existing schemas."""
        return EvaluationResult(
            precision=self.precision,
            recall=self.recall,
            f1_score=self.f1_score,
            hallucination_rate=self.hallucination_rate,
            true_positives=self.true_positives,
            false_positives=self.false_positives,
            false_negatives=self.false_negatives,
            matched_requirements=self.matched_pairs,
        )

    def summary(self) -> str:
        """Generate a human-readable summary of the metrics."""
        return (
            f"Metrics Summary:\n"
            f"  Precision:         {self.precision:.2%}\n"
            f"  Recall:            {self.recall:.2%}\n"
            f"  F1 Score:          {self.f1_score:.2%}\n"
            f"  Hallucination Rate: {self.hallucination_rate:.2%}\n"
            f"\n"
            f"  True Positives:    {self.true_positives}\n"
            f"  False Positives:   {self.false_positives}\n"
            f"  False Negatives:   {self.false_negatives}\n"
            f"  Partial Matches:   {self.partial_matches}\n"
            f"\n"
            f"  Generated: {self.total_generated}, Ground Truth: {self.total_ground_truth}"
        )


@dataclass
class ComparisonResult:
    """Result of comparing two evaluation conditions (e.g., RAG vs Baseline).

    Attributes:
        baseline_metrics: Metrics from baseline (zero-shot) generation.
        rag_metrics: Metrics from RAG-enhanced generation.
        precision_delta: RAG precision - Baseline precision.
        recall_delta: RAG recall - Baseline recall.
        f1_delta: RAG F1 - Baseline F1.
        hallucination_delta: RAG hallucination rate - Baseline (negative is better).
        precision_improvement_pct: Percentage improvement in precision.
        recall_improvement_pct: Percentage improvement in recall.
        f1_improvement_pct: Percentage improvement in F1.
        meets_threshold: True if RAG F1 >= Baseline F1 * improvement_threshold.
        improvement_threshold: The threshold used (default 1.20 for 20% improvement).
    """

    baseline_metrics: MetricsBreakdown
    rag_metrics: MetricsBreakdown
    precision_delta: float
    recall_delta: float
    f1_delta: float
    hallucination_delta: float
    precision_improvement_pct: float
    recall_improvement_pct: float
    f1_improvement_pct: float
    meets_threshold: bool
    improvement_threshold: float

    def summary(self) -> str:
        """Generate a human-readable comparison summary."""
        status = "✓ MEETS" if self.meets_threshold else "✗ DOES NOT MEET"
        threshold_pct = (self.improvement_threshold - 1) * 100

        return (
            f"RAG vs Baseline Comparison:\n"
            f"\n"
            f"                    Baseline    RAG         Delta       Improvement\n"
            f"  Precision:        {self.baseline_metrics.precision:>6.2%}      {self.rag_metrics.precision:>6.2%}      {self.precision_delta:>+7.2%}     {self.precision_improvement_pct:>+6.1f}%\n"
            f"  Recall:           {self.baseline_metrics.recall:>6.2%}      {self.rag_metrics.recall:>6.2%}      {self.recall_delta:>+7.2%}     {self.recall_improvement_pct:>+6.1f}%\n"
            f"  F1 Score:         {self.baseline_metrics.f1_score:>6.2%}      {self.rag_metrics.f1_score:>6.2%}      {self.f1_delta:>+7.2%}     {self.f1_improvement_pct:>+6.1f}%\n"
            f"  Hallucination:    {self.baseline_metrics.hallucination_rate:>6.2%}      {self.rag_metrics.hallucination_rate:>6.2%}      {self.hallucination_delta:>+7.2%}\n"
            f"\n"
            f"  Thesis Target ({threshold_pct:.0f}% F1 improvement): {status}\n"
        )


class MetricsCalculator:
    """Calculator for evaluation metrics from match results.

    Computes Precision, Recall, F1, and hallucination rate from the
    MatchResult objects produced by SemanticMatcher. Also provides
    comparison utilities for RAG vs baseline analysis.

    Example:
        >>> calculator = MetricsCalculator()
        >>> matches = matcher.match_requirements(generated, ground_truth)
        >>> metrics = calculator.calculate(matches, len(ground_truth))
        >>> print(metrics.summary())
    """

    def calculate(
        self,
        matches: list[MatchResult],
        total_ground_truth: int,
    ) -> MetricsBreakdown:
        """Calculate metrics from match results.

        Args:
            matches: List of MatchResult objects from SemanticMatcher.
            total_ground_truth: Total number of ground truth requirements.
                This is needed because some GT may not be matched at all.

        Returns:
            MetricsBreakdown with all computed metrics.

        Note:
            - TP = count of MATCH results (similarity >= match_threshold)
            - FP = count of NO_MATCH results (hallucinations)
            - FN = total_ground_truth - number of unique GT matched
            - Partial matches are counted separately (not in TP or FP)
        """
        if not matches and total_ground_truth == 0:
            # Edge case: no generated, no ground truth
            return MetricsBreakdown(
                true_positives=0,
                false_positives=0,
                false_negatives=0,
                partial_matches=0,
                total_generated=0,
                total_ground_truth=0,
                precision=1.0,  # No FP when nothing generated
                recall=1.0,  # No FN when no GT exists
                f1_score=1.0,
                hallucination_rate=0.0,
                matched_pairs=[],
            )

        # Count match types
        true_positives = 0
        false_positives = 0
        partial_matches = 0
        matched_pairs: list[tuple[str, str, float]] = []
        matched_gt_ids: set[str] = set()

        for match in matches:
            if match.match_type == MatchType.MATCH:
                true_positives += 1
                if match.ground_truth_id:
                    matched_gt_ids.add(match.ground_truth_id)
                    matched_pairs.append(
                        (match.generated_id, match.ground_truth_id, match.similarity_score)
                    )
            elif match.match_type == MatchType.PARTIAL:
                partial_matches += 1
                # Partial matches also consume a GT
                if match.ground_truth_id:
                    matched_gt_ids.add(match.ground_truth_id)
            else:  # NO_MATCH
                false_positives += 1

        # False negatives = GT requirements not matched by any generated
        # Note: Both MATCH and PARTIAL consume GT, so FN excludes both
        false_negatives = total_ground_truth - len(matched_gt_ids)

        total_generated = len(matches)

        # Calculate metrics with division-by-zero protection
        precision = self._safe_divide(true_positives, true_positives + false_positives)
        recall = self._safe_divide(true_positives, true_positives + false_negatives)
        f1_score = self._safe_divide(2 * precision * recall, precision + recall)
        hallucination_rate = self._safe_divide(false_positives, total_generated)

        return MetricsBreakdown(
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
            partial_matches=partial_matches,
            total_generated=total_generated,
            total_ground_truth=total_ground_truth,
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            hallucination_rate=hallucination_rate,
            matched_pairs=matched_pairs,
        )

    def compare_conditions(
        self,
        baseline: MetricsBreakdown,
        rag: MetricsBreakdown,
        improvement_threshold: float = 1.20,
    ) -> ComparisonResult:
        """Compare RAG metrics against baseline metrics.

        Args:
            baseline: Metrics from baseline (zero-shot) generation.
            rag: Metrics from RAG-enhanced generation.
            improvement_threshold: Required F1 improvement factor (default 1.20 = 20%).

        Returns:
            ComparisonResult with deltas, improvements, and threshold check.
        """
        # Calculate deltas (positive = RAG is better, except for hallucination)
        precision_delta = rag.precision - baseline.precision
        recall_delta = rag.recall - baseline.recall
        f1_delta = rag.f1_score - baseline.f1_score
        hallucination_delta = rag.hallucination_rate - baseline.hallucination_rate

        # Calculate percentage improvements
        precision_improvement_pct = self._percentage_change(
            baseline.precision, rag.precision
        )
        recall_improvement_pct = self._percentage_change(baseline.recall, rag.recall)
        f1_improvement_pct = self._percentage_change(baseline.f1_score, rag.f1_score)

        # Check if RAG meets the improvement threshold
        # Thesis target: RAG F1 >= Baseline F1 * 1.20 (20% improvement)
        if baseline.f1_score == 0:
            # If baseline is 0, any positive RAG F1 meets threshold
            meets_threshold = rag.f1_score > 0
        else:
            meets_threshold = rag.f1_score >= baseline.f1_score * improvement_threshold

        return ComparisonResult(
            baseline_metrics=baseline,
            rag_metrics=rag,
            precision_delta=precision_delta,
            recall_delta=recall_delta,
            f1_delta=f1_delta,
            hallucination_delta=hallucination_delta,
            precision_improvement_pct=precision_improvement_pct,
            recall_improvement_pct=recall_improvement_pct,
            f1_improvement_pct=f1_improvement_pct,
            meets_threshold=meets_threshold,
            improvement_threshold=improvement_threshold,
        )

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        """Safely divide, returning 0.0 if denominator is 0."""
        if denominator == 0:
            return 0.0
        return numerator / denominator

    @staticmethod
    def _percentage_change(old_value: float, new_value: float) -> float:
        """Calculate percentage change from old to new value.

        Returns:
            Percentage change (e.g., 20.0 for 20% improvement).
            Returns 0.0 if old_value is 0 and new_value is also 0.
            Returns 100.0 if old_value is 0 but new_value is positive.
        """
        if old_value == 0:
            if new_value == 0:
                return 0.0
            return 100.0  # Infinite improvement, cap at 100%
        return ((new_value - old_value) / old_value) * 100
