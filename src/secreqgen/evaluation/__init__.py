"""Evaluation framework for comparing generated requirements against ground truth."""

from secreqgen.evaluation.semantic_matcher import (
    GroundTruthRequirement,
    MatchResult,
    MatchType,
    SemanticMatcher,
    load_ground_truth,
)

__all__ = [
    "GroundTruthRequirement",
    "MatchResult",
    "MatchType",
    "SemanticMatcher",
    "load_ground_truth",
]
