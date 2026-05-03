"""Evaluation framework for comparing generated requirements against ground truth."""

from secreqgen.evaluation.baseline import (
    BaselineRunner,
    ComparativeResults,
    create_baseline_runner,
)
from secreqgen.evaluation.metrics import (
    ComparisonResult,
    MetricsBreakdown,
    MetricsCalculator,
)
from secreqgen.evaluation.multi_run import (
    MultiRunReport,
    PairedTestResult,
    RunStatistics,
    build_multi_run_report,
    run_multi_evaluation,
    serialize_report,
)
from secreqgen.evaluation.pipeline import (
    EvaluationPipeline,
    EvaluationPipelineResult,
    StoryEvaluationResult,
    create_evaluation_pipeline,
)
from secreqgen.evaluation.report import (
    EvaluationReportGenerator,
    write_evaluation_report,
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
    # Baseline runner
    "BaselineRunner",
    "ComparativeResults",
    "create_baseline_runner",
    # Evaluation pipeline
    "EvaluationPipeline",
    "EvaluationPipelineResult",
    "StoryEvaluationResult",
    "create_evaluation_pipeline",
    # Report generation
    "EvaluationReportGenerator",
    "write_evaluation_report",
    # Multi-run aggregation
    "MultiRunReport",
    "PairedTestResult",
    "RunStatistics",
    "build_multi_run_report",
    "run_multi_evaluation",
    "serialize_report",
]
