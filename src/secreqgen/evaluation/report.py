"""Evaluation report generator.

Generates detailed reports from evaluation pipeline results for
thesis documentation and analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from secreqgen.evaluation.pipeline import EvaluationPipelineResult, StoryEvaluationResult

logger = logging.getLogger(__name__)


class EvaluationReportGenerator:
    """Generates evaluation reports in multiple formats.

    Creates detailed reports from EvaluationPipelineResult for:
    - Human review (Markdown)
    - Data analysis (JSON)
    - Quick summary (text)

    Reports include:
    - Per-story breakdown with metrics
    - Aggregate metrics comparison
    - Partial matches flagged for manual review
    - Sample generated requirements for qualitative analysis

    Example:
        >>> generator = EvaluationReportGenerator(Path("docs/evaluation_results"))
        >>> paths = generator.generate_full_report(result, "bitwarden_eval")
        >>> print(f"Reports written to: {paths}")
    """

    def __init__(
        self,
        output_dir: Path,
        create_dirs: bool = True,
    ) -> None:
        """Initialize the report generator.

        Args:
            output_dir: Directory for report output.
            create_dirs: Whether to create directories if missing.
        """
        self.output_dir = Path(output_dir)
        self.create_dirs = create_dirs

    def generate_full_report(
        self,
        result: EvaluationPipelineResult,
        base_filename: str,
        include_samples: bool = True,
        max_samples_per_story: int = 3,
    ) -> list[Path]:
        """Generate complete reports in multiple formats.

        Args:
            result: The evaluation pipeline result.
            base_filename: Base name for report files.
            include_samples: Whether to include sample requirements.
            max_samples_per_story: Max samples to include per story.

        Returns:
            List of paths to generated report files.
        """
        if self.create_dirs:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = f"{base_filename}_{timestamp}"

        paths = []

        # Generate Markdown report (human-readable)
        md_path = self._write_markdown_report(
            result, filename_base, include_samples, max_samples_per_story
        )
        paths.append(md_path)

        # Generate JSON report (data analysis)
        json_path = self._write_json_report(result, filename_base)
        paths.append(json_path)

        logger.info(f"Generated {len(paths)} report files in {self.output_dir}")
        return paths

    def _write_markdown_report(
        self,
        result: EvaluationPipelineResult,
        filename_base: str,
        include_samples: bool,
        max_samples: int,
    ) -> Path:
        """Generate Markdown report for human review."""
        lines = []

        # Header
        lines.append("# Evaluation Report: RAG vs Baseline Comparison")
        lines.append("")
        lines.append(f"**Generated:** {result.run_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Stories Evaluated:** {result.total_stories}")
        lines.append("")

        # Thesis result summary
        if result.meets_thesis_target:
            lines.append("> **THESIS TARGET MET** - RAG shows significant improvement over baseline")
        else:
            lines.append("> **THESIS TARGET NOT MET** - RAG does not meet 20% F1 improvement threshold")
        lines.append("")

        # Aggregate metrics table
        lines.append("## Aggregate Metrics")
        lines.append("")
        lines.append("| Metric | Baseline | RAG | Delta | Improvement |")
        lines.append("|--------|----------|-----|-------|-------------|")

        comp = result.comparison
        lines.append(
            f"| Precision | {result.aggregate_baseline_metrics.precision:.2%} | "
            f"{result.aggregate_rag_metrics.precision:.2%} | "
            f"{comp.precision_delta:+.2%} | {comp.precision_improvement_pct:+.1f}% |"
        )
        lines.append(
            f"| Recall | {result.aggregate_baseline_metrics.recall:.2%} | "
            f"{result.aggregate_rag_metrics.recall:.2%} | "
            f"{comp.recall_delta:+.2%} | {comp.recall_improvement_pct:+.1f}% |"
        )
        lines.append(
            f"| F1 Score | {result.aggregate_baseline_metrics.f1_score:.2%} | "
            f"{result.aggregate_rag_metrics.f1_score:.2%} | "
            f"{comp.f1_delta:+.2%} | {comp.f1_improvement_pct:+.1f}% |"
        )
        lines.append(
            f"| Hallucination Rate | {result.aggregate_baseline_metrics.hallucination_rate:.2%} | "
            f"{result.aggregate_rag_metrics.hallucination_rate:.2%} | "
            f"{comp.hallucination_delta:+.2%} | - |"
        )
        lines.append("")

        # Generation counts
        lines.append("### Generation Summary")
        lines.append("")
        lines.append(f"- **Baseline Requirements Generated:** {result.aggregate_baseline_metrics.total_generated}")
        lines.append(f"- **RAG Requirements Generated:** {result.aggregate_rag_metrics.total_generated}")
        lines.append(f"- **Ground Truth Requirements:** {result.aggregate_baseline_metrics.total_ground_truth}")
        lines.append("")

        # Per-story breakdown
        lines.append("## Per-Story Results")
        lines.append("")

        for story_result in result.per_story_results:
            lines.append(f"### {story_result.story_id}: {story_result.story_title}")
            lines.append("")
            lines.append(f"**Ground Truth:** {story_result.ground_truth_count} requirements")
            lines.append("")

            # Story metrics table
            lines.append("| Mode | Generated | TP | FP | FN | Precision | Recall | F1 |")
            lines.append("|------|-----------|----|----|----|-----------|---------|----|")

            bm = story_result.baseline_metrics
            lines.append(
                f"| Baseline | {bm.total_generated} | {bm.true_positives} | "
                f"{bm.false_positives} | {bm.false_negatives} | "
                f"{bm.precision:.2%} | {bm.recall:.2%} | {bm.f1_score:.2%} |"
            )

            rm = story_result.rag_metrics
            lines.append(
                f"| RAG | {rm.total_generated} | {rm.true_positives} | "
                f"{rm.false_positives} | {rm.false_negatives} | "
                f"{rm.precision:.2%} | {rm.recall:.2%} | {rm.f1_score:.2%} |"
            )
            lines.append("")

            # Sample requirements (if enabled)
            if include_samples and story_result.rag_result:
                lines.append("**Sample RAG Requirements:**")
                lines.append("")
                for req in story_result.rag_result.requirements[:max_samples]:
                    lines.append(f"- **{req.title}** ({req.asvs_category.value})")
                    lines.append(f"  - {req.description[:200]}...")
                lines.append("")

        # Partial matches section
        partials = result.get_partial_matches()
        if partials:
            lines.append("## Partial Matches (Manual Review Required)")
            lines.append("")
            lines.append("These matches scored between 0.70-0.85 similarity and need human review:")
            lines.append("")
            lines.append("| Story | Generated | Ground Truth | Similarity |")
            lines.append("|-------|-----------|--------------|------------|")

            for story_id, match in partials:
                gen_text = match.generated_text[:50] + "..." if len(match.generated_text) > 50 else match.generated_text
                gt_text = (match.ground_truth_text[:50] + "...") if match.ground_truth_text and len(match.ground_truth_text) > 50 else (match.ground_truth_text or "N/A")
                lines.append(f"| {story_id} | {gen_text} | {gt_text} | {match.similarity_score:.3f} |")
            lines.append("")

        # Configuration used
        lines.append("## Configuration")
        lines.append("")
        lines.append("```yaml")
        for key, value in result.config.items():
            lines.append(f"{key}: {value}")
        lines.append("```")
        lines.append("")

        # Write file
        path = self.output_dir / f"{filename_base}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Wrote Markdown report: {path}")
        return path

    def _write_json_report(
        self,
        result: EvaluationPipelineResult,
        filename_base: str,
    ) -> Path:
        """Generate JSON report for data analysis."""
        data = self._result_to_dict(result)

        path = self.output_dir / f"{filename_base}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"Wrote JSON report: {path}")
        return path

    def _result_to_dict(self, result: EvaluationPipelineResult) -> dict[str, Any]:
        """Convert evaluation result to a serializable dictionary."""
        return {
            "run_timestamp": result.run_timestamp.isoformat(),
            "total_stories": result.total_stories,
            "meets_thesis_target": result.meets_thesis_target,
            "config": result.config,
            "aggregate_metrics": {
                "baseline": self._metrics_to_dict(result.aggregate_baseline_metrics),
                "rag": self._metrics_to_dict(result.aggregate_rag_metrics),
            },
            "comparison": {
                "precision_delta": result.comparison.precision_delta,
                "recall_delta": result.comparison.recall_delta,
                "f1_delta": result.comparison.f1_delta,
                "hallucination_delta": result.comparison.hallucination_delta,
                "precision_improvement_pct": result.comparison.precision_improvement_pct,
                "recall_improvement_pct": result.comparison.recall_improvement_pct,
                "f1_improvement_pct": result.comparison.f1_improvement_pct,
                "improvement_threshold": result.comparison.improvement_threshold,
            },
            "per_story_results": [
                self._story_result_to_dict(sr) for sr in result.per_story_results
            ],
            "partial_matches": [
                {
                    "story_id": story_id,
                    "generated_id": match.generated_id,
                    "generated_text": match.generated_text,
                    "ground_truth_id": match.ground_truth_id,
                    "ground_truth_text": match.ground_truth_text,
                    "similarity_score": match.similarity_score,
                }
                for story_id, match in result.get_partial_matches()
            ],
        }

    def _metrics_to_dict(self, metrics) -> dict[str, Any]:
        """Convert MetricsBreakdown to dictionary."""
        return {
            "true_positives": metrics.true_positives,
            "false_positives": metrics.false_positives,
            "false_negatives": metrics.false_negatives,
            "partial_matches": metrics.partial_matches,
            "total_generated": metrics.total_generated,
            "total_ground_truth": metrics.total_ground_truth,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1_score": metrics.f1_score,
            "hallucination_rate": metrics.hallucination_rate,
        }

    def _story_result_to_dict(self, sr: StoryEvaluationResult) -> dict[str, Any]:
        """Convert StoryEvaluationResult to dictionary."""
        return {
            "story_id": sr.story_id,
            "story_title": sr.story_title,
            "ground_truth_count": sr.ground_truth_count,
            "baseline_requirement_count": sr.baseline_requirement_count,
            "rag_requirement_count": sr.rag_requirement_count,
            "baseline_metrics": self._metrics_to_dict(sr.baseline_metrics),
            "rag_metrics": self._metrics_to_dict(sr.rag_metrics),
            "baseline_matches": [
                {
                    "generated_id": m.generated_id,
                    "ground_truth_id": m.ground_truth_id,
                    "similarity_score": m.similarity_score,
                    "match_type": m.match_type.value,
                }
                for m in sr.baseline_matches
            ],
            "rag_matches": [
                {
                    "generated_id": m.generated_id,
                    "ground_truth_id": m.ground_truth_id,
                    "similarity_score": m.similarity_score,
                    "match_type": m.match_type.value,
                }
                for m in sr.rag_matches
            ],
        }

    def generate_summary(self, result: EvaluationPipelineResult) -> str:
        """Generate a quick text summary suitable for console output.

        Args:
            result: The evaluation pipeline result.

        Returns:
            Multi-line summary string.
        """
        return result.summary()


def write_evaluation_report(
    result: EvaluationPipelineResult,
    output_dir: str | Path,
    base_filename: str = "evaluation",
) -> list[Path]:
    """Convenience function to write evaluation reports.

    Args:
        result: The evaluation pipeline result.
        output_dir: Directory for report output.
        base_filename: Base name for report files.

    Returns:
        List of paths to generated files.
    """
    generator = EvaluationReportGenerator(Path(output_dir))
    return generator.generate_full_report(result, base_filename)
