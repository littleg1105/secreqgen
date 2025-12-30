"""Unit tests for prompt templates."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from secreqgen.generation.prompts import (
    PromptTemplate,
    SecurityRequirementsPromptBuilder,
    format_retrieved_context,
    get_json_schema,
)
from secreqgen.generation.schemas import ASVSCategory, Priority, UserStory


class TestPromptTemplate:
    """Tests for PromptTemplate dataclass."""

    def test_basic_creation(self):
        """Test creating a basic prompt template."""
        template = PromptTemplate(
            name="test",
            template="Hello, $name!",
            required_vars=["name"],
            description="A greeting template",
        )

        assert template.name == "test"
        assert template.template == "Hello, $name!"
        assert template.required_vars == ["name"]
        assert template.description == "A greeting template"

    def test_format_simple_substitution(self):
        """Test simple variable substitution."""
        template = PromptTemplate(
            name="test",
            template="Hello, $name! You are $age years old.",
            required_vars=["name", "age"],
        )

        result = template.format(name="Alice", age="30")
        assert result == "Hello, Alice! You are 30 years old."

    def test_format_missing_required_variable(self):
        """Test that missing required variables raise KeyError."""
        template = PromptTemplate(
            name="test",
            template="Hello, $name!",
            required_vars=["name"],
        )

        with pytest.raises(KeyError) as exc_info:
            template.format()

        assert "name" in str(exc_info.value)

    def test_format_missing_multiple_required_variables(self):
        """Test error message lists all missing variables."""
        template = PromptTemplate(
            name="test",
            template="$greeting, $name!",
            required_vars=["greeting", "name"],
        )

        with pytest.raises(KeyError) as exc_info:
            template.format()

        error_msg = str(exc_info.value)
        assert "greeting" in error_msg
        assert "name" in error_msg

    def test_format_extra_variables_ignored(self):
        """Test that extra variables don't cause errors."""
        template = PromptTemplate(
            name="test",
            template="Hello, $name!",
            required_vars=["name"],
        )

        result = template.format(name="Bob", extra="ignored")
        assert result == "Hello, Bob!"

    def test_format_safe_substitute_preserves_unknown(self):
        """Test that unknown placeholders are preserved (safe_substitute)."""
        template = PromptTemplate(
            name="test",
            template="Hello, $name! Your ID is $id.",
            required_vars=["name"],  # Only name is required
        )

        result = template.format(name="Charlie")
        # $id should remain as-is since it's not in required_vars
        assert result == "Hello, Charlie! Your ID is $id."

    def test_format_no_required_vars(self):
        """Test template with no required variables."""
        template = PromptTemplate(
            name="test",
            template="Static text with $optional placeholder",
        )

        result = template.format()
        assert result == "Static text with $optional placeholder"

    def test_default_values(self):
        """Test default values for optional fields."""
        template = PromptTemplate(name="test", template="Hello")

        assert template.required_vars == []
        assert template.description == ""


class TestSecurityRequirementsPromptBuilder:
    """Tests for SecurityRequirementsPromptBuilder."""

    @pytest.fixture
    def builder(self) -> SecurityRequirementsPromptBuilder:
        """Create a builder with default templates."""
        return SecurityRequirementsPromptBuilder()

    @pytest.fixture
    def sample_user_story(self) -> UserStory:
        """Create a sample user story for testing."""
        return UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in to the application so that I can access my account.",
            acceptance_criteria=[
                "User can enter username and password",
                "Invalid credentials show error message",
                "Successful login redirects to dashboard",
            ],
        )

    @pytest.fixture
    def sample_system_context(self) -> str:
        """Sample system context YAML content."""
        return """system:
  name: SecureApp
  type: web_application

technologies:
  backend: Python/FastAPI
  frontend: React
  database: PostgreSQL
  auth: Keycloak"""

    def test_build_system_prompt(self, builder: SecurityRequirementsPromptBuilder):
        """Test system prompt generation."""
        prompt = builder.build_system_prompt()

        # Should contain security expert persona
        assert "Security Requirements Engineer" in prompt
        assert "OWASP" in prompt
        assert "ASVS" in prompt

        # Should contain guidelines
        assert "SPECIFIC" in prompt
        assert "ACTIONABLE" in prompt
        assert "TESTABLE" in prompt

        # Should mention JSON
        assert "JSON" in prompt

    def test_build_rag_prompt_basic(
        self,
        builder: SecurityRequirementsPromptBuilder,
        sample_user_story: UserStory,
    ):
        """Test RAG prompt generation with minimal inputs."""
        prompt = builder.build_rag_prompt(
            user_story=sample_user_story,
            retrieved_context="[Security documentation here]",
        )

        # Should contain user story details
        assert "US-001" in prompt
        assert "User Login" in prompt
        assert "log in to the application" in prompt

        # Should contain acceptance criteria
        assert "enter username and password" in prompt
        assert "Invalid credentials" in prompt

        # Should contain retrieved context
        assert "[Security documentation here]" in prompt

        # Should contain JSON schema
        assert "requirements" in prompt
        assert "asvs_category" in prompt

    def test_build_rag_prompt_with_system_context(
        self,
        builder: SecurityRequirementsPromptBuilder,
        sample_user_story: UserStory,
        sample_system_context: str,
    ):
        """Test RAG prompt includes system context."""
        prompt = builder.build_rag_prompt(
            user_story=sample_user_story,
            retrieved_context="Security docs",
            system_context=sample_system_context,
        )

        # Should contain system context
        assert "SecureApp" in prompt
        assert "FastAPI" in prompt
        assert "Keycloak" in prompt

    def test_build_rag_prompt_no_acceptance_criteria(
        self,
        builder: SecurityRequirementsPromptBuilder,
    ):
        """Test RAG prompt handles empty acceptance criteria."""
        story = UserStory(
            id="US-002",
            title="Password Reset",
            story="As a user, I want to reset my password.",
            acceptance_criteria=[],
        )

        prompt = builder.build_rag_prompt(
            user_story=story,
            retrieved_context="Context",
        )

        # Should have fallback text
        assert "No acceptance criteria provided" in prompt

    def test_build_zero_shot_prompt_basic(
        self,
        builder: SecurityRequirementsPromptBuilder,
        sample_user_story: UserStory,
    ):
        """Test zero-shot prompt generation."""
        prompt = builder.build_zero_shot_prompt(user_story=sample_user_story)

        # Should contain user story details
        assert "US-001" in prompt
        assert "User Login" in prompt

        # Should NOT contain retrieved context section
        # (zero-shot doesn't have it)
        assert "Retrieved Security Documentation" not in prompt

        # Should still have JSON schema
        assert "requirements" in prompt
        assert "asvs_category" in prompt

    def test_build_zero_shot_prompt_with_system_context(
        self,
        builder: SecurityRequirementsPromptBuilder,
        sample_user_story: UserStory,
        sample_system_context: str,
    ):
        """Test zero-shot prompt includes system context."""
        prompt = builder.build_zero_shot_prompt(
            user_story=sample_user_story,
            system_context=sample_system_context,
        )

        assert "SecureApp" in prompt
        assert "PostgreSQL" in prompt

    def test_rag_vs_zero_shot_difference(
        self,
        builder: SecurityRequirementsPromptBuilder,
        sample_user_story: UserStory,
    ):
        """Test that RAG and zero-shot prompts differ appropriately."""
        rag_prompt = builder.build_rag_prompt(
            user_story=sample_user_story,
            retrieved_context="Retrieved docs here",
        )
        zero_shot_prompt = builder.build_zero_shot_prompt(
            user_story=sample_user_story,
        )

        # RAG should have retrieved context
        assert "Retrieved docs here" in rag_prompt
        assert "Retrieved docs here" not in zero_shot_prompt

        # Both should have the user story
        assert "US-001" in rag_prompt
        assert "US-001" in zero_shot_prompt

        # Both should have JSON schema
        assert "asvs_category" in rag_prompt
        assert "asvs_category" in zero_shot_prompt

    def test_get_json_schema(self, builder: SecurityRequirementsPromptBuilder):
        """Test JSON schema getter."""
        schema = builder.get_json_schema()

        # Should describe required fields
        assert "id" in schema
        assert "title" in schema
        assert "description" in schema
        assert "rationale" in schema
        assert "acceptance_criteria" in schema
        assert "asvs_category" in schema
        assert "priority" in schema
        assert "implementation_notes" in schema
        assert "source_references" in schema

        # Should list valid ASVS categories
        assert "V2: Authentication" in schema
        assert "V4: Access Control" in schema

        # Should list valid priorities
        assert "critical" in schema
        assert "high" in schema
        assert "medium" in schema
        assert "low" in schema

    def test_get_valid_asvs_categories(self, builder: SecurityRequirementsPromptBuilder):
        """Test getting valid ASVS categories."""
        categories = builder.get_valid_asvs_categories()

        assert isinstance(categories, list)
        assert len(categories) == len(ASVSCategory)
        assert "V2: Authentication" in categories
        assert "V5: Validation, Sanitization and Encoding" in categories

    def test_get_valid_priorities(self, builder: SecurityRequirementsPromptBuilder):
        """Test getting valid priorities."""
        priorities = builder.get_valid_priorities()

        assert isinstance(priorities, list)
        assert len(priorities) == len(Priority)
        assert "critical" in priorities
        assert "high" in priorities
        assert "medium" in priorities
        assert "low" in priorities


class TestPromptBuilderExternalTemplates:
    """Tests for external template file loading."""

    def test_load_custom_system_prompt(self):
        """Test loading custom system prompt from file."""
        with TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir)
            custom_prompt = "You are a CUSTOM security expert."
            (prompts_dir / "system_prompt.txt").write_text(custom_prompt)

            builder = SecurityRequirementsPromptBuilder(prompts_dir=prompts_dir)
            result = builder.build_system_prompt()

            assert result == custom_prompt

    def test_load_custom_rag_template(self):
        """Test loading custom RAG template from file."""
        with TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir)
            custom_template = "CUSTOM RAG: $story_id - $story_title\n$retrieved_context"
            (prompts_dir / "rag_user_prompt.txt").write_text(custom_template)

            builder = SecurityRequirementsPromptBuilder(prompts_dir=prompts_dir)
            story = UserStory(id="US-TEST", title="Test Story", story="Test")

            result = builder.build_rag_prompt(
                user_story=story,
                retrieved_context="Retrieved content",
            )

            assert "CUSTOM RAG" in result
            assert "US-TEST" in result
            assert "Retrieved content" in result

    def test_fallback_to_defaults_when_no_dir(self):
        """Test fallback to default templates when no directory specified."""
        builder = SecurityRequirementsPromptBuilder(prompts_dir=None)

        # Should use default templates
        system_prompt = builder.build_system_prompt()
        assert "Security Requirements Engineer" in system_prompt

    def test_fallback_to_defaults_when_file_missing(self):
        """Test fallback to defaults when template file doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir)
            # Don't create any files

            builder = SecurityRequirementsPromptBuilder(prompts_dir=prompts_dir)

            # Should use default templates
            system_prompt = builder.build_system_prompt()
            assert "Security Requirements Engineer" in system_prompt


class TestFormatRetrievedContext:
    """Tests for format_retrieved_context helper function."""

    def test_format_single_chunk(self):
        """Test formatting a single chunk."""
        result = format_retrieved_context(
            chunks=["This is the chunk content about authentication."],
            sources=["/docs/auth-policy.md"],
            sections=["Authentication > Password Requirements"],
            scores=[0.85],
        )

        assert "[1]" in result
        assert "auth-policy.md" in result
        assert "Authentication > Password Requirements" in result
        assert "0.85" in result
        assert "authentication" in result

    def test_format_multiple_chunks(self):
        """Test formatting multiple chunks."""
        result = format_retrieved_context(
            chunks=["First chunk about passwords", "Second chunk about encryption"],
            sources=["/docs/auth.md", "/docs/crypto.md"],
            sections=["Auth", "Crypto"],
            scores=[0.90, 0.75],
        )

        assert "[1]" in result
        assert "[2]" in result
        assert "auth.md" in result
        assert "crypto.md" in result
        assert "passwords" in result
        assert "encryption" in result
        assert "0.90" in result
        assert "0.75" in result

        # Chunks should be separated
        assert "---" in result

    def test_format_empty_chunks(self):
        """Test formatting when no chunks provided."""
        result = format_retrieved_context(
            chunks=[],
            sources=[],
            sections=[],
            scores=[],
        )

        assert "No relevant security documentation found" in result

    def test_format_handles_empty_source(self):
        """Test formatting handles empty source path."""
        result = format_retrieved_context(
            chunks=["Content"],
            sources=[""],  # Empty source
            sections=["Section"],
            scores=[0.80],
        )

        assert "Unknown" in result  # Fallback for empty source

    def test_format_handles_empty_section(self):
        """Test formatting handles empty section."""
        result = format_retrieved_context(
            chunks=["Content"],
            sources=["/doc.md"],
            sections=[""],  # Empty section
            scores=[0.80],
        )

        assert "General" in result  # Fallback for empty section

    def test_format_extracts_filename_from_path(self):
        """Test that source paths are reduced to filenames."""
        result = format_retrieved_context(
            chunks=["Content"],
            sources=["/very/long/path/to/document.md"],
            sections=["Section"],
            scores=[0.80],
        )

        # Should show filename, not full path
        assert "document.md" in result
        assert "/very/long/path" not in result


class TestGetJsonSchema:
    """Tests for get_json_schema function."""

    def test_schema_contains_all_fields(self):
        """Test that schema describes all required fields."""
        schema = get_json_schema()

        required_fields = [
            "id",
            "user_story_id",
            "title",
            "description",
            "rationale",
            "acceptance_criteria",
            "asvs_category",
            "priority",
            "implementation_notes",
            "source_references",
        ]

        for field in required_fields:
            assert field in schema, f"Missing field: {field}"

    def test_schema_includes_asvs_categories(self):
        """Test that schema lists ASVS categories."""
        schema = get_json_schema()

        # Check for some key categories
        assert "V1: Architecture" in schema
        assert "V2: Authentication" in schema
        assert "V3: Session Management" in schema
        assert "V4: Access Control" in schema
        assert "V14: Configuration" in schema

    def test_schema_includes_priorities(self):
        """Test that schema lists priorities."""
        schema = get_json_schema()

        for priority in ["critical", "high", "medium", "low"]:
            assert priority in schema
