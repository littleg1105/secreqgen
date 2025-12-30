"""Evaluation framework for comparing generated requirements against ground truth."""

from secreqgen.evaluation.metrics import (
    ComparisonResult,
    MetricsBreakdown,
    MetricsCalculator,
)
from secreqgen.evaluation.semantic_matcher import (
    GroundTruthRequirement,
    MatchResult,
    MatchType,
    SemanticMatcher,
    load_ground_truth,
)

__all__ = [
    # Semantic matcher
    "GroundTruthRequirement",
    "MatchResult",
    "MatchType",
    "SemanticMatcher",
    "load_ground_truth",
    # Metrics
    "ComparisonResult",
    "MetricsBreakdown",
    "MetricsCalculator",
]
