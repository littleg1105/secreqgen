"""Unit tests for RequirementsFormatter."""

import csv
import io
import json
from datetime import datetime

import pytest
import yaml

from secreqgen.generation.schemas import (
    ASVSCategory,
    GenerationResult,
    Priority,
    SecurityRequirement,
    ThreatAgentMapping,
    UserStory,
)
from secreqgen.output.formatter import OutputFormat, RequirementsFormatter


@pytest.fixture
def sample_user_story() -> UserStory:
    """Create a sample user story for testing."""
    return UserStory(
        id="US-001",
        title="User Login",
        story="As a user, I want to log in to the application so that I can access my account.",
        acceptance_criteria=[
            "User can enter username and password",
            "Invalid credentials show error message",
        ],
    )


@pytest.fixture
def sample_requirement() -> SecurityRequirement:
    """Create a sample security requirement for testing."""
    return SecurityRequirement(
        id="SR-001",
        user_story_id="US-001",
        title="Secure Password Storage",
        description="Passwords must be hashed using bcrypt with a work factor of 12.",
        rationale="Protects user credentials in case of database breach.",
        acceptance_criteria=[
            "Passwords are stored as bcrypt hashes",
            "Work factor is at least 12",
        ],
        asvs_category=ASVSCategory.V2_AUTHENTICATION,
        priority=Priority.CRITICAL,
        implementation_notes="Use the argon2 or bcrypt library.",
        source_references=["OWASP ASVS V2.1.1", "NIST SP 800-63B"],
    )


@pytest.fixture
def sample_threat_mapping() -> ThreatAgentMapping:
    """Create a sample threat mapping for testing."""
    return ThreatAgentMapping(
        category="external_attacker",
        attack_vector="Credential stuffing",
        complexity_capability="intermediate",
        relevance_ranking=4,
    )


@pytest.fixture
def sample_result(
    sample_user_story: UserStory,
    sample_requirement: SecurityRequirement,
    sample_threat_mapping: ThreatAgentMapping,
) -> GenerationResult:
    """Create a sample generation result for testing."""
    return GenerationResult(
        user_story=sample_user_story,
        requirements=[sample_requirement],
        threat_mappings=[sample_threat_mapping],
        generated_at=datetime(2024, 12, 30, 14, 30, 0),
        model_used="llama3:8b",
        rag_enabled=True,
        retrieval_context="Sample context from docs.",
    )


@pytest.fixture
def formatter() -> RequirementsFormatter:
    """Create a formatter with default settings."""
    return RequirementsFormatter()


class TestOutputFormat:
    """Tests for OutputFormat enum."""

    def test_enum_values(self):
        """Test that OutputFormat has expected values."""
        assert OutputFormat.JSON.value == "json"
        assert OutputFormat.YAML.value == "yaml"
        assert OutputFormat.MARKDOWN.value == "markdown"
        assert OutputFormat.CSV.value == "csv"

    def test_enum_is_string_compatible(self):
        """Test that OutputFormat can be used as a string."""
        assert str(OutputFormat.JSON) == "OutputFormat.JSON"
        assert OutputFormat.JSON.value == "json"


class TestRequirementsFormatterInit:
    """Tests for RequirementsFormatter initialization."""

    def test_default_include_metadata(self):
        """Test that metadata is included by default."""
        formatter = RequirementsFormatter()
        assert formatter.include_metadata is True

    def test_custom_include_metadata(self):
        """Test setting include_metadata to False."""
        formatter = RequirementsFormatter(include_metadata=False)
        assert formatter.include_metadata is False


class TestRequirementsFormatterJSON:
    """Tests for JSON formatting."""

    def test_format_json_basic(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test basic JSON formatting."""
        output = formatter.format(sample_result, OutputFormat.JSON)

        # Should be valid JSON
        data = json.loads(output)

        assert "user_story" in data
        assert "requirements" in data
        assert data["user_story"]["id"] == "US-001"
        assert len(data["requirements"]) == 1
        assert data["requirements"][0]["id"] == "SR-001"

    def test_format_json_includes_metadata(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that JSON includes metadata by default."""
        output = formatter.format(sample_result, OutputFormat.JSON)
        data = json.loads(output)

        assert "metadata" in data
        assert data["metadata"]["model_used"] == "llama3:8b"
        assert data["metadata"]["rag_enabled"] is True
        assert "generated_at" in data["metadata"]

    def test_format_json_excludes_metadata(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test JSON without metadata."""
        output = formatter.format(
            sample_result, OutputFormat.JSON, include_metadata=False
        )
        data = json.loads(output)

        assert "metadata" not in data

    def test_format_json_datetime_serialization(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that datetime is serialized to ISO format."""
        output = formatter.format(sample_result, OutputFormat.JSON)
        data = json.loads(output)

        generated_at = data["metadata"]["generated_at"]
        assert "2024-12-30" in generated_at

    def test_format_json_enum_serialization(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that enums are serialized to values."""
        output = formatter.format(sample_result, OutputFormat.JSON)
        data = json.loads(output)

        req = data["requirements"][0]
        assert req["asvs_category"] == "V2: Authentication"
        assert req["priority"] == "critical"

    def test_format_json_threat_mappings(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test JSON includes threat mappings."""
        output = formatter.format(sample_result, OutputFormat.JSON)
        data = json.loads(output)

        assert "threat_mappings" in data
        assert len(data["threat_mappings"]) == 1
        assert data["threat_mappings"][0]["category"] == "external_attacker"


class TestRequirementsFormatterYAML:
    """Tests for YAML formatting."""

    def test_format_yaml_basic(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test basic YAML formatting."""
        output = formatter.format(sample_result, OutputFormat.YAML)

        # Should be valid YAML
        data = yaml.safe_load(output)

        assert "user_story" in data
        assert "requirements" in data
        assert data["user_story"]["id"] == "US-001"

    def test_format_yaml_includes_metadata(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that YAML includes metadata."""
        output = formatter.format(sample_result, OutputFormat.YAML)
        data = yaml.safe_load(output)

        assert "metadata" in data
        assert data["metadata"]["model_used"] == "llama3:8b"

    def test_format_yaml_excludes_metadata(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test YAML without metadata."""
        output = formatter.format(
            sample_result, OutputFormat.YAML, include_metadata=False
        )
        data = yaml.safe_load(output)

        assert "metadata" not in data


class TestRequirementsFormatterMarkdown:
    """Tests for Markdown formatting."""

    def test_format_markdown_basic(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test basic Markdown formatting."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        # Should contain headers
        assert "# Security Requirements: User Login" in output
        assert "## User Story" in output
        assert "## Security Requirements" in output

    def test_format_markdown_user_story(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test Markdown includes user story details."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        assert "**ID**: US-001" in output
        assert "log in to the application" in output
        assert "enter username and password" in output

    def test_format_markdown_requirements(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test Markdown includes requirement details."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        assert "### SR-001: Secure Password Storage" in output
        assert "**Priority**: CRITICAL" in output
        assert "**ASVS Category**: V2: Authentication" in output
        assert "bcrypt" in output

    def test_format_markdown_includes_metadata(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test Markdown includes metadata."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        assert "## Generation Metadata" in output
        assert "**Model**: llama3:8b" in output
        assert "**RAG Enabled**: True" in output

    def test_format_markdown_excludes_metadata(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test Markdown without metadata."""
        output = formatter.format(
            sample_result, OutputFormat.MARKDOWN, include_metadata=False
        )

        assert "## Generation Metadata" not in output

    def test_format_markdown_acceptance_criteria_checkboxes(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that acceptance criteria have checkboxes."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        assert "- [ ] Passwords are stored as bcrypt hashes" in output

    def test_format_markdown_source_references(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test Markdown includes source references."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        assert "**Source References**:" in output
        assert "OWASP ASVS V2.1.1" in output
        assert "NIST SP 800-63B" in output

    def test_format_markdown_threat_mappings_table(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test Markdown includes threat mappings as a table."""
        output = formatter.format(sample_result, OutputFormat.MARKDOWN)

        assert "## Threat Agent Mappings" in output
        assert "| Category | Attack Vector | Capability | Relevance |" in output
        assert "external_attacker" in output
        assert "Credential stuffing" in output

    def test_format_markdown_no_requirements(
        self, formatter: RequirementsFormatter, sample_user_story: UserStory
    ):
        """Test Markdown handles no requirements gracefully."""
        result = GenerationResult(
            user_story=sample_user_story,
            requirements=[],
            threat_mappings=[],
            generated_at=datetime.now(),
            model_used="test",
            rag_enabled=False,
        )

        output = formatter.format(result, OutputFormat.MARKDOWN)
        assert "*No security requirements generated.*" in output


class TestRequirementsFormatterCSV:
    """Tests for CSV formatting."""

    def test_format_csv_basic(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test basic CSV formatting."""
        output = formatter.format(sample_result, OutputFormat.CSV)

        # Should be valid CSV
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["user_story_id"] == "US-001"
        assert rows[0]["requirement_id"] == "SR-001"

    def test_format_csv_has_all_columns(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test CSV has all expected columns."""
        output = formatter.format(sample_result, OutputFormat.CSV)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        expected_columns = [
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
            "generated_at",
            "model_used",
            "rag_enabled",
        ]

        for col in expected_columns:
            assert col in rows[0], f"Missing column: {col}"

    def test_format_csv_excludes_metadata_columns(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test CSV without metadata columns."""
        output = formatter.format(
            sample_result, OutputFormat.CSV, include_metadata=False
        )
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        assert "generated_at" not in rows[0]
        assert "model_used" not in rows[0]
        assert "rag_enabled" not in rows[0]

    def test_format_csv_flattens_lists(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that lists are flattened with semicolons."""
        output = formatter.format(sample_result, OutputFormat.CSV)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        # Acceptance criteria should be semicolon-separated
        ac = rows[0]["acceptance_criteria"]
        assert ";" in ac
        assert "Passwords are stored as bcrypt hashes" in ac
        assert "Work factor is at least 12" in ac


class TestRequirementsFormatterBatch:
    """Tests for batch formatting."""

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
            description="Reset tokens must be cryptographically random.",
            rationale="Prevents token prediction attacks.",
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

    def test_format_batch_json(
        self, formatter: RequirementsFormatter, multiple_results: list[GenerationResult]
    ):
        """Test batch JSON formatting."""
        output = formatter.format_batch(multiple_results, OutputFormat.JSON)
        data = json.loads(output)

        assert "generation_results" in data
        assert len(data["generation_results"]) == 2
        assert data["total_user_stories"] == 2
        assert data["total_requirements"] == 2

    def test_format_batch_yaml(
        self, formatter: RequirementsFormatter, multiple_results: list[GenerationResult]
    ):
        """Test batch YAML formatting."""
        output = formatter.format_batch(multiple_results, OutputFormat.YAML)
        data = yaml.safe_load(output)

        assert "generation_results" in data
        assert len(data["generation_results"]) == 2
        assert data["total_user_stories"] == 2

    def test_format_batch_markdown(
        self, formatter: RequirementsFormatter, multiple_results: list[GenerationResult]
    ):
        """Test batch Markdown formatting."""
        output = formatter.format_batch(multiple_results, OutputFormat.MARKDOWN)

        # Should have report header
        assert "# Security Requirements Report" in output
        assert "**Total User Stories**: 2" in output
        assert "**Total Requirements**: 2" in output

        # Should have both user stories
        assert "User Login" in output
        assert "Password Reset" in output

        # Should have separators
        assert "---" in output

    def test_format_batch_csv(
        self, formatter: RequirementsFormatter, multiple_results: list[GenerationResult]
    ):
        """Test batch CSV formatting."""
        output = formatter.format_batch(multiple_results, OutputFormat.CSV)
        reader = csv.DictReader(io.StringIO(output))
        rows = list(reader)

        # Should have one row per requirement
        assert len(rows) == 2
        assert rows[0]["user_story_id"] == "US-001"
        assert rows[1]["user_story_id"] == "US-002"


class TestRequirementsFormatterEdgeCases:
    """Tests for edge cases."""

    def test_format_unsupported_format(
        self, formatter: RequirementsFormatter, sample_result: GenerationResult
    ):
        """Test that unsupported format raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            formatter.format(sample_result, "unsupported")  # type: ignore

        assert "Unsupported output format" in str(exc_info.value)

    def test_format_batch_empty_list(self, formatter: RequirementsFormatter):
        """Test batch formatting with empty list."""
        output = formatter.format_batch([], OutputFormat.JSON)
        data = json.loads(output)

        assert data["total_user_stories"] == 0
        assert data["total_requirements"] == 0

    def test_format_result_without_threat_mappings(
        self, formatter: RequirementsFormatter, sample_user_story: UserStory
    ):
        """Test formatting result without threat mappings."""
        result = GenerationResult(
            user_story=sample_user_story,
            requirements=[],
            threat_mappings=[],
            generated_at=datetime.now(),
            model_used="test",
            rag_enabled=False,
        )

        output = formatter.format(result, OutputFormat.JSON)
        data = json.loads(output)

        assert "threat_mappings" not in data

    def test_format_result_without_retrieval_context(
        self, formatter: RequirementsFormatter, sample_user_story: UserStory
    ):
        """Test formatting result without retrieval context."""
        result = GenerationResult(
            user_story=sample_user_story,
            requirements=[],
            threat_mappings=[],
            generated_at=datetime.now(),
            model_used="test",
            rag_enabled=False,
            retrieval_context=None,
        )

        output = formatter.format(result, OutputFormat.JSON)
        data = json.loads(output)

        assert "retrieval_context_length" not in data.get("metadata", {})

    def test_format_overrides_instance_include_metadata(
        self, sample_result: GenerationResult
    ):
        """Test that format() can override instance include_metadata setting."""
        formatter = RequirementsFormatter(include_metadata=True)

        # Override to exclude
        output = formatter.format(
            sample_result, OutputFormat.JSON, include_metadata=False
        )
        data = json.loads(output)
        assert "metadata" not in data

        # Default should still include
        output = formatter.format(sample_result, OutputFormat.JSON)
        data = json.loads(output)
        assert "metadata" in data
