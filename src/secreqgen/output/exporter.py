"""File exporter for security requirements.

Handles writing formatted requirements to files with support for
multiple output formats and directory management.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from secreqgen.generation.schemas import GenerationResult
from secreqgen.output.formatter import OutputFormat, RequirementsFormatter

logger = logging.getLogger(__name__)


class RequirementsExporter:
    """Exporter for security requirements to files.

    Manages file output for generated requirements, handling
    directory creation and format-specific file extensions.

    Example:
        >>> exporter = RequirementsExporter(Path("./output"))
        >>> paths = exporter.export_result(result, "login_requirements")
        >>> print(f"Exported to: {paths}")
    """

    # File extensions for each output format
    FORMAT_EXTENSIONS = {
        OutputFormat.JSON: ".json",
        OutputFormat.YAML: ".yaml",
        OutputFormat.MARKDOWN: ".md",
        OutputFormat.CSV: ".csv",
    }

    def __init__(
        self,
        output_dir: Path,
        formatter: Optional[RequirementsFormatter] = None,
        create_dirs: bool = True,
    ) -> None:
        """Initialize the exporter.

        Args:
            output_dir: Base directory for output files.
            formatter: Optional custom formatter. If None, uses default.
            create_dirs: Whether to create directories if they don't exist.
                Default True.
        """
        self.output_dir = Path(output_dir)
        self.formatter = formatter or RequirementsFormatter()
        self.create_dirs = create_dirs

    def export(
        self,
        content: str,
        filename: str,
        output_format: OutputFormat,
    ) -> Path:
        """Export pre-formatted content to a file.

        Args:
            content: The formatted content string to write.
            filename: Base filename (without extension).
            output_format: Format to determine file extension.

        Returns:
            Path to the written file.

        Raises:
            OSError: If file cannot be written.
        """
        # Ensure output directory exists
        if self.create_dirs:
            self.output_dir.mkdir(parents=True, exist_ok=True)

        # Build file path with appropriate extension
        extension = self.FORMAT_EXTENSIONS.get(output_format, ".txt")
        file_path = self.output_dir / f"{filename}{extension}"

        # Write content
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Exported {output_format.value} to: {file_path}")

        return file_path

    def export_result(
        self,
        result: GenerationResult,
        base_filename: str,
        formats: Optional[list[OutputFormat]] = None,
        include_metadata: bool = True,
    ) -> list[Path]:
        """Export a generation result to one or more formats.

        Args:
            result: The GenerationResult to export.
            base_filename: Base filename (without extension).
            formats: List of output formats. If None, exports to all formats.
            include_metadata: Whether to include generation metadata.

        Returns:
            List of paths to written files.
        """
        if formats is None:
            formats = list(OutputFormat)

        exported_paths = []

        for output_format in formats:
            content = self.formatter.format(
                result,
                output_format=output_format,
                include_metadata=include_metadata,
            )
            path = self.export(content, base_filename, output_format)
            exported_paths.append(path)

        return exported_paths

    def export_batch(
        self,
        results: list[GenerationResult],
        base_filename: str,
        formats: Optional[list[OutputFormat]] = None,
        include_metadata: bool = True,
    ) -> list[Path]:
        """Export multiple generation results to one or more formats.

        Args:
            results: List of GenerationResult objects to export.
            base_filename: Base filename (without extension).
            formats: List of output formats. If None, exports to all formats.
            include_metadata: Whether to include generation metadata.

        Returns:
            List of paths to written files.
        """
        if formats is None:
            formats = list(OutputFormat)

        exported_paths = []

        for output_format in formats:
            content = self.formatter.format_batch(
                results,
                output_format=output_format,
                include_metadata=include_metadata,
            )
            path = self.export(content, base_filename, output_format)
            exported_paths.append(path)

        return exported_paths

    def generate_filename(
        self,
        user_story_id: str,
        timestamp: Optional[datetime] = None,
        rag_mode: bool = True,
    ) -> str:
        """Generate a descriptive filename for requirements output.

        Args:
            user_story_id: The user story ID.
            timestamp: Optional timestamp. If None, uses current time.
            rag_mode: Whether RAG was used (affects filename).

        Returns:
            Generated filename (without extension).

        Example:
            >>> exporter.generate_filename("US-001", rag_mode=True)
            "US-001_rag_20241230_143022"
        """
        ts = timestamp or datetime.now()
        mode_suffix = "rag" if rag_mode else "baseline"
        date_str = ts.strftime("%Y%m%d_%H%M%S")
        return f"{user_story_id}_{mode_suffix}_{date_str}"

    def generate_batch_filename(
        self,
        prefix: str = "requirements",
        timestamp: Optional[datetime] = None,
        rag_mode: bool = True,
    ) -> str:
        """Generate a descriptive filename for batch requirements output.

        Args:
            prefix: Filename prefix. Default "requirements".
            timestamp: Optional timestamp. If None, uses current time.
            rag_mode: Whether RAG was used (affects filename).

        Returns:
            Generated filename (without extension).

        Example:
            >>> exporter.generate_batch_filename(prefix="batch", rag_mode=False)
            "batch_baseline_20241230_143022"
        """
        ts = timestamp or datetime.now()
        mode_suffix = "rag" if rag_mode else "baseline"
        date_str = ts.strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{mode_suffix}_{date_str}"
