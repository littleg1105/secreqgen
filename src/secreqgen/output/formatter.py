"""Output formatters for security requirements.

Provides formatting of GenerationResult objects to various output formats
including JSON, YAML, Markdown, and CSV for thesis documentation and analysis.
"""

import csv
import io
import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import yaml

from secreqgen.generation.schemas import GenerationResult, SecurityRequirement


class OutputFormat(str, Enum):
    """Supported output formats for requirements."""

    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "markdown"
    CSV = "csv"


class RequirementsFormatter:
    """Formatter for security requirements output.

    Converts GenerationResult objects to various string formats
    for export, display, and thesis documentation.

    Example:
        >>> formatter = RequirementsFormatter()
        >>> json_output = formatter.format(result, OutputFormat.JSON)
        >>> markdown_output = formatter.format(result, OutputFormat.MARKDOWN)
    """

    def __init__(self, include_metadata: bool = True) -> None:
        """Initialize the formatter.

        Args:
            include_metadata: Whether to include generation metadata
                (timestamp, model, RAG status) in output. Default True.
        """
        self.include_metadata = include_metadata

    def format(
        self,
        result: GenerationResult,
        output_format: OutputFormat,
        include_metadata: Optional[bool] = None,
    ) -> str:
        """Format a single generation result.

        Args:
            result: The GenerationResult to format.
            output_format: Target output format.
            include_metadata: Override default metadata inclusion.

        Returns:
            Formatted string representation.

        Raises:
            ValueError: If output_format is not supported.
        """
        include_meta = (
            include_metadata if include_metadata is not None else self.include_metadata
        )

        formatters = {
            OutputFormat.JSON: self._to_json,
            OutputFormat.YAML: self._to_yaml,
            OutputFormat.MARKDOWN: self._to_markdown,
            OutputFormat.CSV: self._to_csv,
        }

        formatter_func = formatters.get(output_format)
        if formatter_func is None:
            raise ValueError(f"Unsupported output format: {output_format}")

        return formatter_func(result, include_meta)

    def format_batch(
        self,
        results: list[GenerationResult],
        output_format: OutputFormat,
        include_metadata: Optional[bool] = None,
    ) -> str:
        """Format multiple generation results.

        Args:
            results: List of GenerationResult objects.
            output_format: Target output format.
            include_metadata: Override default metadata inclusion.

        Returns:
            Formatted string with all results.

        Raises:
            ValueError: If output_format is not supported.
        """
        include_meta = (
            include_metadata if include_metadata is not None else self.include_metadata
        )

        if output_format == OutputFormat.JSON:
            return self._batch_to_json(results, include_meta)
        elif output_format == OutputFormat.YAML:
            return self._batch_to_yaml(results, include_meta)
        elif output_format == OutputFormat.MARKDOWN:
            return self._batch_to_markdown(results, include_meta)
        elif output_format == OutputFormat.CSV:
            return self._batch_to_csv(results, include_meta)
        else:
            raise ValueError(f"Unsupported output format: {output_format}")

    def _to_json(self, result: GenerationResult, include_metadata: bool) -> str:
        """Convert result to JSON string.

        Args:
            result: The GenerationResult to convert.
            include_metadata: Whether to include metadata fields.

        Returns:
            JSON formatted string.
        """
        data = self._result_to_dict(result, include_metadata)
        return json.dumps(data, indent=2, default=self._json_serializer)

    def _to_yaml(self, result: GenerationResult, include_metadata: bool) -> str:
        """Convert result to YAML string.

        Args:
            result: The GenerationResult to convert.
            include_metadata: Whether to include metadata fields.

        Returns:
            YAML formatted string.
        """
        data = self._result_to_dict(result, include_metadata)
        # Convert datetime to string for YAML
        data = self._serialize_for_yaml(data)
        return yaml.dump(data, default_flow_style=False, sort_keys=False, width=120)

    def _to_markdown(self, result: GenerationResult, include_metadata: bool) -> str:
        """Convert result to Markdown string.

        Args:
            result: The GenerationResult to convert.
            include_metadata: Whether to include metadata fields.

        Returns:
            Markdown formatted string.
        """
        lines = []

        # Header with user story
        lines.append(f"# Security Requirements: {result.user_story.title}")
        lines.append("")

        if include_metadata:
            lines.append("## Generation Metadata")
            lines.append("")
            lines.append(f"- **Generated**: {result.generated_at.isoformat()}")
            lines.append(f"- **Model**: {result.model_used}")
            lines.append(f"- **RAG Enabled**: {result.rag_enabled}")
            lines.append("")

        # User story section
        lines.append("## User Story")
        lines.append("")
        lines.append(f"**ID**: {result.user_story.id}")
        lines.append("")
        lines.append(f"> {result.user_story.story}")
        lines.append("")

        if result.user_story.acceptance_criteria:
            lines.append("**Acceptance Criteria**:")
            lines.append("")
            for criterion in result.user_story.acceptance_criteria:
                lines.append(f"- {criterion}")
            lines.append("")

        # Requirements section
        lines.append("## Security Requirements")
        lines.append("")

        if not result.requirements:
            lines.append("*No security requirements generated.*")
        else:
            for req in result.requirements:
                lines.extend(self._requirement_to_markdown(req))
                lines.append("")

        # Threat mappings section (if present)
        if result.threat_mappings:
            lines.append("## Threat Agent Mappings")
            lines.append("")
            lines.append("| Category | Attack Vector | Capability | Relevance |")
            lines.append("|----------|---------------|------------|-----------|")
            for threat in result.threat_mappings:
                lines.append(
                    f"| {threat.category} | {threat.attack_vector} | "
                    f"{threat.complexity_capability} | {threat.relevance_ranking}/5 |"
                )
            lines.append("")

        return "\n".join(lines)

    def _to_csv(self, result: GenerationResult, include_metadata: bool) -> str:
        """Convert result to CSV string.

        Flattens requirements into rows with user story context.

        Args:
            result: The GenerationResult to convert.
            include_metadata: Whether to include metadata fields.

        Returns:
            CSV formatted string.
        """
        return self._requirements_to_csv([result], include_metadata)

    def _batch_to_json(
        self, results: list[GenerationResult], include_metadata: bool
    ) -> str:
        """Convert multiple results to JSON.

        Args:
            results: List of GenerationResult objects.
            include_metadata: Whether to include metadata fields.

        Returns:
            JSON formatted string.
        """
        data = {
            "generation_results": [
                self._result_to_dict(r, include_metadata) for r in results
            ],
            "total_user_stories": len(results),
            "total_requirements": sum(len(r.requirements) for r in results),
        }
        return json.dumps(data, indent=2, default=self._json_serializer)

    def _batch_to_yaml(
        self, results: list[GenerationResult], include_metadata: bool
    ) -> str:
        """Convert multiple results to YAML.

        Args:
            results: List of GenerationResult objects.
            include_metadata: Whether to include metadata fields.

        Returns:
            YAML formatted string.
        """
        data = {
            "generation_results": [
                self._serialize_for_yaml(self._result_to_dict(r, include_metadata))
                for r in results
            ],
            "total_user_stories": len(results),
            "total_requirements": sum(len(r.requirements) for r in results),
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False, width=120)

    def _batch_to_markdown(
        self, results: list[GenerationResult], include_metadata: bool
    ) -> str:
        """Convert multiple results to Markdown.

        Args:
            results: List of GenerationResult objects.
            include_metadata: Whether to include metadata fields.

        Returns:
            Markdown formatted string.
        """
        lines = []
        lines.append("# Security Requirements Report")
        lines.append("")
        lines.append(f"**Total User Stories**: {len(results)}")
        lines.append(
            f"**Total Requirements**: {sum(len(r.requirements) for r in results)}"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

        for i, result in enumerate(results, 1):
            # Add separator between stories
            if i > 1:
                lines.append("---")
                lines.append("")

            # Reuse single result formatting but adjust heading level
            single_md = self._to_markdown(result, include_metadata)
            # Convert # to ## for batch context
            adjusted = single_md.replace(
                "# Security Requirements:", f"## {i}. Security Requirements:"
            )
            adjusted = adjusted.replace(
                "## Generation Metadata", "### Generation Metadata"
            )
            adjusted = adjusted.replace("## User Story", "### User Story")
            adjusted = adjusted.replace(
                "## Security Requirements\n", "### Security Requirements\n"
            )
            adjusted = adjusted.replace(
                "## Threat Agent Mappings", "### Threat Agent Mappings"
            )
            lines.append(adjusted)

        return "\n".join(lines)

    def _batch_to_csv(
        self, results: list[GenerationResult], include_metadata: bool
    ) -> str:
        """Convert multiple results to CSV.

        Args:
            results: List of GenerationResult objects.
            include_metadata: Whether to include metadata fields.

        Returns:
            CSV formatted string.
        """
        return self._requirements_to_csv(results, include_metadata)

    def _result_to_dict(
        self, result: GenerationResult, include_metadata: bool
    ) -> dict[str, Any]:
        """Convert GenerationResult to dictionary.

        Args:
            result: The GenerationResult to convert.
            include_metadata: Whether to include metadata fields.

        Returns:
            Dictionary representation of the result.
        """
        data: dict[str, Any] = {
            "user_story": {
                "id": result.user_story.id,
                "title": result.user_story.title,
                "story": result.user_story.story,
                "acceptance_criteria": result.user_story.acceptance_criteria,
            },
            "requirements": [
                self._requirement_to_dict(req) for req in result.requirements
            ],
        }

        if result.threat_mappings:
            data["threat_mappings"] = [
                {
                    "category": t.category,
                    "attack_vector": t.attack_vector,
                    "complexity_capability": t.complexity_capability,
                    "relevance_ranking": t.relevance_ranking,
                }
                for t in result.threat_mappings
            ]

        if include_metadata:
            data["metadata"] = {
                "generated_at": result.generated_at,
                "model_used": result.model_used,
                "rag_enabled": result.rag_enabled,
            }
            if result.retrieval_context:
                data["metadata"]["retrieval_context_length"] = len(
                    result.retrieval_context
                )

        return data

    def _requirement_to_dict(self, req: SecurityRequirement) -> dict[str, Any]:
        """Convert SecurityRequirement to dictionary.

        Args:
            req: The SecurityRequirement to convert.

        Returns:
            Dictionary representation of the requirement.
        """
        return {
            "id": req.id,
            "user_story_id": req.user_story_id,
            "title": req.title,
            "description": req.description,
            "rationale": req.rationale,
            "acceptance_criteria": req.acceptance_criteria,
            "asvs_category": req.asvs_category.value,
            "priority": req.priority.value,
            "implementation_notes": req.implementation_notes,
            "source_references": req.source_references,
        }

    def _requirement_to_markdown(self, req: SecurityRequirement) -> list[str]:
        """Convert SecurityRequirement to Markdown lines.

        Args:
            req: The SecurityRequirement to convert.

        Returns:
            List of Markdown lines.
        """
        lines = []
        lines.append(f"### {req.id}: {req.title}")
        lines.append("")
        lines.append(f"**Priority**: {req.priority.value.upper()}")
        lines.append(f"**ASVS Category**: {req.asvs_category.value}")
        lines.append("")
        lines.append("**Description**:")
        lines.append("")
        lines.append(req.description)
        lines.append("")
        lines.append("**Rationale**:")
        lines.append("")
        lines.append(req.rationale)
        lines.append("")

        if req.acceptance_criteria:
            lines.append("**Acceptance Criteria**:")
            lines.append("")
            for criterion in req.acceptance_criteria:
                lines.append(f"- [ ] {criterion}")
            lines.append("")

        if req.implementation_notes:
            lines.append("**Implementation Notes**:")
            lines.append("")
            lines.append(req.implementation_notes)
            lines.append("")

        if req.source_references:
            lines.append("**Source References**:")
            lines.append("")
            for ref in req.source_references:
                lines.append(f"- {ref}")

        return lines

    def _requirements_to_csv(
        self, results: list[GenerationResult], include_metadata: bool
    ) -> str:
        """Convert results to CSV with flattened requirements.

        Args:
            results: List of GenerationResult objects.
            include_metadata: Whether to include metadata fields.

        Returns:
            CSV formatted string.
        """
        output = io.StringIO()

        # Define columns
        columns = [
            "user_story_id",
            "user_story_title",
            "requirement_id",
            "requirement_title",
            "description",
            "rationale",
            "asvs_category",
            "priority",
            "acceptance_criteria",
            "implementation_notes",
            "source_references",
        ]

        if include_metadata:
            columns.extend(["generated_at", "model_used", "rag_enabled"])

        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()

        for result in results:
            for req in result.requirements:
                row = {
                    "user_story_id": result.user_story.id,
                    "user_story_title": result.user_story.title,
                    "requirement_id": req.id,
                    "requirement_title": req.title,
                    "description": req.description,
                    "rationale": req.rationale,
                    "asvs_category": req.asvs_category.value,
                    "priority": req.priority.value,
                    "acceptance_criteria": "; ".join(req.acceptance_criteria),
                    "implementation_notes": req.implementation_notes or "",
                    "source_references": "; ".join(req.source_references),
                }

                if include_metadata:
                    row["generated_at"] = result.generated_at.isoformat()
                    row["model_used"] = result.model_used
                    row["rag_enabled"] = str(result.rag_enabled)

                writer.writerow(row)

        return output.getvalue()

    def _json_serializer(self, obj: Any) -> Any:
        """Custom JSON serializer for non-standard types.

        Args:
            obj: Object to serialize.

        Returns:
            Serializable representation of the object.

        Raises:
            TypeError: If object type is not supported.
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    def _serialize_for_yaml(self, data: Any) -> Any:
        """Recursively convert datetime objects to strings for YAML.

        Args:
            data: Data structure to serialize.

        Returns:
            Serialized data structure.
        """
        if isinstance(data, dict):
            return {k: self._serialize_for_yaml(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._serialize_for_yaml(item) for item in data]
        elif isinstance(data, datetime):
            return data.isoformat()
        elif isinstance(data, Enum):
            return data.value
        return data
