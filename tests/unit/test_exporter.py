"""Unit tests for RequirementsExporter."""

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from secreqgen.generation.schemas import (
    ASVSCategory,
    GenerationResult,
    Priority,
    SecurityRequirement,
    UserStory,
)
from secreqgen.output.exporter import RequirementsExporter
from secreqgen.output.formatter import OutputFormat, RequirementsFormatter


@pytest.fixture
def sample_user_story() -> UserStory:
    """Create a sample user story for testing."""
    return UserStory(
        id="US-001",
        title="User Login",
        story="As a user, I want to log in.",
        acceptance_criteria=["User can enter credentials"],
    )


@pytest.fixture
def sample_requirement() -> SecurityRequirement:
    """Create a sample security requirement for testing."""
    return SecurityRequirement(
        id="SR-001",
        user_story_id="US-001",
        title="Secure Password Storage",
        description="Passwords must be hashed.",
        rationale="Protects credentials.",
        asvs_category=ASVSCategory.V2_AUTHENTICATION,
        priority=Priority.HIGH,
    )


@pytest.fixture
def sample_result(
    sample_user_story: UserStory,
    sample_requirement: SecurityRequirement,
) -> GenerationResult:
    """Create a sample generation result for testing."""
    return GenerationResult(
        user_story=sample_user_story,
        requirements=[sample_requirement],
        threat_mappings=[],
        generated_at=datetime(2024, 12, 30, 14, 30, 0),
        model_used="llama3:8b",
        rag_enabled=True,
    )


@pytest.fixture
def temp_output_dir() -> Path:
    """Create a temporary output directory."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestRequirementsExporterInit:
    """Tests for RequirementsExporter initialization."""

    def test_default_initialization(self, temp_output_dir: Path):
        """Test default exporter initialization."""
        exporter = RequirementsExporter(temp_output_dir)

        assert exporter.output_dir == temp_output_dir
        assert exporter.create_dirs is True
        assert isinstance(exporter.formatter, RequirementsFormatter)

    def test_custom_formatter(self, temp_output_dir: Path):
        """Test initialization with custom formatter."""
        custom_formatter = RequirementsFormatter(include_metadata=False)
        exporter = RequirementsExporter(temp_output_dir, formatter=custom_formatter)

        assert exporter.formatter == custom_formatter
        assert exporter.formatter.include_metadata is False

    def test_disable_create_dirs(self, temp_output_dir: Path):
        """Test disabling directory creation."""
        exporter = RequirementsExporter(temp_output_dir, create_dirs=False)
        assert exporter.create_dirs is False


class TestRequirementsExporterExport:
    """Tests for RequirementsExporter.export()."""

    def test_export_creates_directory(self):
        """Test that export creates output directory if needed."""
        with TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "nested" / "output"
            exporter = RequirementsExporter(output_dir)

            path = exporter.export('{"test": true}', "test", OutputFormat.JSON)

            assert output_dir.exists()
            assert path.exists()

    def test_export_json_file(self, temp_output_dir: Path):
        """Test exporting JSON content to file."""
        exporter = RequirementsExporter(temp_output_dir)
        content = '{"key": "value"}'

        path = exporter.export(content, "test_output", OutputFormat.JSON)

        assert path == temp_output_dir / "test_output.json"
        assert path.exists()
        assert path.read_text() == content

    def test_export_yaml_file(self, temp_output_dir: Path):
        """Test exporting YAML content to file."""
        exporter = RequirementsExporter(temp_output_dir)
        content = "key: value\n"

        path = exporter.export(content, "test_output", OutputFormat.YAML)

        assert path == temp_output_dir / "test_output.yaml"
        assert path.exists()

    def test_export_markdown_file(self, temp_output_dir: Path):
        """Test exporting Markdown content to file."""
        exporter = RequirementsExporter(temp_output_dir)
        content = "# Test\n\nContent here."

        path = exporter.export(content, "test_output", OutputFormat.MARKDOWN)

        assert path == temp_output_dir / "test_output.md"
        assert path.exists()

    def test_export_csv_file(self, temp_output_dir: Path):
        """Test exporting CSV content to file."""
        exporter = RequirementsExporter(temp_output_dir)
        content = "col1,col2\nval1,val2\n"

        path = exporter.export(content, "test_output", OutputFormat.CSV)

        assert path == temp_output_dir / "test_output.csv"
        assert path.exists()


class TestRequirementsExporterExportResult:
    """Tests for RequirementsExporter.export_result()."""

    def test_export_result_single_format(
        self, temp_output_dir: Path, sample_result: GenerationResult
    ):
        """Test exporting to a single format."""
        exporter = RequirementsExporter(temp_output_dir)

        paths = exporter.export_result(
            sample_result, "requirements", formats=[OutputFormat.JSON]
        )

        assert len(paths) == 1
        assert paths[0].suffix == ".json"
        assert paths[0].exists()

        # Verify content
        data = json.loads(paths[0].read_text())
        assert data["user_story"]["id"] == "US-001"

    def test_export_result_multiple_formats(
        self, temp_output_dir: Path, sample_result: GenerationResult
    ):
        """Test exporting to multiple formats."""
        exporter = RequirementsExporter(temp_output_dir)

        paths = exporter.export_result(
            sample_result,
            "requirements",
            formats=[OutputFormat.JSON, OutputFormat.YAML, OutputFormat.MARKDOWN],
        )

        assert len(paths) == 3
        assert any(p.suffix == ".json" for p in paths)
        assert any(p.suffix == ".yaml" for p in paths)
        assert any(p.suffix == ".md" for p in paths)

    def test_export_result_all_formats(
        self, temp_output_dir: Path, sample_result: GenerationResult
    ):
        """Test exporting to all formats when formats=None."""
        exporter = RequirementsExporter(temp_output_dir)

        paths = exporter.export_result(sample_result, "requirements", formats=None)

        assert len(paths) == 4
        suffixes = {p.suffix for p in paths}
        assert suffixes == {".json", ".yaml", ".md", ".csv"}

    def test_export_result_without_metadata(
        self, temp_output_dir: Path, sample_result: GenerationResult
    ):
        """Test exporting without metadata."""
        exporter = RequirementsExporter(temp_output_dir)

        paths = exporter.export_result(
            sample_result,
            "requirements",
            formats=[OutputFormat.JSON],
            include_metadata=False,
        )

        data = json.loads(paths[0].read_text())
        assert "metadata" not in data


class TestRequirementsExporterExportBatch:
    """Tests for RequirementsExporter.export_batch()."""

    @pytest.fixture
    def multiple_results(
        self, sample_user_story: UserStory, sample_requirement: SecurityRequirement
    ) -> list[GenerationResult]:
        """Create multiple generation results for testing."""
        story2 = UserStory(
            id="US-002",
            title="Password Reset",
            story="As a user, I want to reset my password.",
            acceptance_criteria=[],
        )
        req2 = SecurityRequirement(
            id="SR-002",
            user_story_id="US-002",
            title="Secure Reset Token",
            description="Reset tokens must be random.",
            rationale="Security.",
            asvs_category=ASVSCategory.V2_AUTHENTICATION,
            priority=Priority.HIGH,
        )

        return [
            GenerationResult(
                user_story=sample_user_story,
                requirements=[sample_requirement],
                generated_at=datetime(2024, 12, 30, 14, 30, 0),
                model_used="llama3:8b",
                rag_enabled=True,
            ),
            GenerationResult(
                user_story=story2,
                requirements=[req2],
                generated_at=datetime(2024, 12, 30, 14, 31, 0),
                model_used="llama3:8b",
                rag_enabled=True,
            ),
        ]

    def test_export_batch_single_format(
        self, temp_output_dir: Path, multiple_results: list[GenerationResult]
    ):
        """Test batch export to a single format."""
        exporter = RequirementsExporter(temp_output_dir)

        paths = exporter.export_batch(
            multiple_results, "batch_requirements", formats=[OutputFormat.JSON]
        )

        assert len(paths) == 1
        data = json.loads(paths[0].read_text())
        assert data["total_user_stories"] == 2
        assert data["total_requirements"] == 2

    def test_export_batch_all_formats(
        self, temp_output_dir: Path, multiple_results: list[GenerationResult]
    ):
        """Test batch export to all formats."""
        exporter = RequirementsExporter(temp_output_dir)

        paths = exporter.export_batch(
            multiple_results, "batch_requirements", formats=None
        )

        assert len(paths) == 4


class TestRequirementsExporterGenerateFilename:
    """Tests for filename generation methods."""

    def test_generate_filename_rag_mode(self, temp_output_dir: Path):
        """Test filename generation for RAG mode."""
        exporter = RequirementsExporter(temp_output_dir)
        timestamp = datetime(2024, 12, 30, 14, 30, 22)

        filename = exporter.generate_filename("US-001", timestamp, rag_mode=True)

        assert filename == "US-001_rag_20241230_143022"

    def test_generate_filename_baseline_mode(self, temp_output_dir: Path):
        """Test filename generation for baseline mode."""
        exporter = RequirementsExporter(temp_output_dir)
        timestamp = datetime(2024, 12, 30, 14, 30, 22)

        filename = exporter.generate_filename("US-001", timestamp, rag_mode=False)

        assert filename == "US-001_baseline_20241230_143022"

    def test_generate_filename_uses_current_time(self, temp_output_dir: Path):
        """Test filename generation uses current time when not provided."""
        exporter = RequirementsExporter(temp_output_dir)

        filename = exporter.generate_filename("US-001", rag_mode=True)

        # Should contain today's date
        today = datetime.now().strftime("%Y%m%d")
        assert today in filename
        assert "rag" in filename

    def test_generate_batch_filename_rag_mode(self, temp_output_dir: Path):
        """Test batch filename generation for RAG mode."""
        exporter = RequirementsExporter(temp_output_dir)
        timestamp = datetime(2024, 12, 30, 14, 30, 22)

        filename = exporter.generate_batch_filename(
            prefix="batch", timestamp=timestamp, rag_mode=True
        )

        assert filename == "batch_rag_20241230_143022"

    def test_generate_batch_filename_baseline_mode(self, temp_output_dir: Path):
        """Test batch filename generation for baseline mode."""
        exporter = RequirementsExporter(temp_output_dir)
        timestamp = datetime(2024, 12, 30, 14, 30, 22)

        filename = exporter.generate_batch_filename(
            prefix="requirements", timestamp=timestamp, rag_mode=False
        )

        assert filename == "requirements_baseline_20241230_143022"

    def test_generate_batch_filename_default_prefix(self, temp_output_dir: Path):
        """Test batch filename uses default prefix."""
        exporter = RequirementsExporter(temp_output_dir)
        timestamp = datetime(2024, 12, 30, 14, 30, 22)

        filename = exporter.generate_batch_filename(timestamp=timestamp, rag_mode=True)

        assert filename == "requirements_rag_20241230_143022"


class TestRequirementsExporterFormatExtensions:
    """Tests for format to extension mapping."""

    def test_format_extensions_mapping(self, temp_output_dir: Path):
        """Test that all formats have correct extensions."""
        exporter = RequirementsExporter(temp_output_dir)

        assert exporter.FORMAT_EXTENSIONS[OutputFormat.JSON] == ".json"
        assert exporter.FORMAT_EXTENSIONS[OutputFormat.YAML] == ".yaml"
        assert exporter.FORMAT_EXTENSIONS[OutputFormat.MARKDOWN] == ".md"
        assert exporter.FORMAT_EXTENSIONS[OutputFormat.CSV] == ".csv"

    def test_all_formats_have_extensions(self, temp_output_dir: Path):
        """Test that all OutputFormat values have extensions."""
        exporter = RequirementsExporter(temp_output_dir)

        for fmt in OutputFormat:
            assert fmt in exporter.FORMAT_EXTENSIONS


class TestRequirementsExporterEdgeCases:
    """Tests for edge cases."""

    def test_export_with_create_dirs_disabled(self, sample_result: GenerationResult):
        """Test export fails gracefully when create_dirs=False and dir missing."""
        with TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "non_existent"
            exporter = RequirementsExporter(non_existent, create_dirs=False)

            with pytest.raises(OSError):
                exporter.export_result(
                    sample_result, "test", formats=[OutputFormat.JSON]
                )

    def test_export_overwrites_existing_file(
        self, temp_output_dir: Path, sample_result: GenerationResult
    ):
        """Test that export overwrites existing files."""
        exporter = RequirementsExporter(temp_output_dir)

        # First export
        paths = exporter.export_result(
            sample_result, "test", formats=[OutputFormat.JSON]
        )
        original_content = paths[0].read_text()

        # Modify result
        modified_result = GenerationResult(
            user_story=sample_result.user_story,
            requirements=sample_result.requirements,
            threat_mappings=[],
            generated_at=datetime(2025, 1, 1, 0, 0, 0),  # Different timestamp
            model_used="different-model",
            rag_enabled=False,
        )

        # Second export with same filename
        paths = exporter.export_result(
            modified_result, "test", formats=[OutputFormat.JSON]
        )
        new_content = paths[0].read_text()

        # Content should be different
        assert new_content != original_content
        assert "different-model" in new_content

    def test_export_utf8_encoding(self, temp_output_dir: Path):
        """Test that export uses UTF-8 encoding."""
        exporter = RequirementsExporter(temp_output_dir)

        # Content with special characters
        content = '{"message": "Special chars: é, ñ, 中文"}'
        path = exporter.export(content, "utf8_test", OutputFormat.JSON)

        # Read and verify
        read_content = path.read_text(encoding="utf-8")
        assert "é" in read_content
        assert "ñ" in read_content
        assert "中文" in read_content
