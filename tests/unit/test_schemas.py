"""Tests for Pydantic schemas."""

import pytest
from pydantic import ValidationError

from secreqgen.generation.schemas import (
    ASVSCategory,
    EvaluationResult,
    GenerationResult,
    Priority,
    SecurityRequirement,
    ThreatAgentMapping,
    UserStory,
)


class TestUserStory:
    """Tests for UserStory schema."""

    def test_valid_user_story(self, sample_user_story: UserStory) -> None:
        """Test creating a valid user story."""
        assert sample_user_story.id == "US-001"
        assert sample_user_story.title == "User Login"
        assert "log in" in sample_user_story.story

    def test_user_story_minimal(self) -> None:
        """Test creating a user story with minimal fields."""
        story = UserStory(
            id="US-002",
            title="Test",
            story="As a user, I want to test",
        )
        assert story.acceptance_criteria == []

    def test_user_story_missing_required(self) -> None:
        """Test that missing required fields raises validation error."""
        with pytest.raises(ValidationError):
            UserStory(id="US-001", title="Test")  # missing story


class TestSecurityRequirement:
    """Tests for SecurityRequirement schema."""

    def test_valid_requirement(
        self, sample_security_requirement: SecurityRequirement
    ) -> None:
        """Test creating a valid security requirement."""
        assert sample_security_requirement.id == "SR-001"
        assert sample_security_requirement.asvs_category == ASVSCategory.V2_AUTHENTICATION
        assert sample_security_requirement.priority == Priority.CRITICAL

    def test_requirement_default_priority(self) -> None:
        """Test that default priority is MEDIUM."""
        req = SecurityRequirement(
            id="SR-002",
            user_story_id="US-001",
            title="Test",
            description="Test description",
            rationale="Test rationale",
            asvs_category=ASVSCategory.V1_ARCHITECTURE,
        )
        assert req.priority == Priority.MEDIUM

    def test_all_asvs_categories(self) -> None:
        """Test that all 14 ASVS categories are defined."""
        assert len(ASVSCategory) == 14
        assert ASVSCategory.V1_ARCHITECTURE.value.startswith("V1:")
        assert ASVSCategory.V14_CONFIGURATION.value.startswith("V14:")


class TestThreatAgentMapping:
    """Tests for ThreatAgentMapping schema."""

    def test_valid_mapping(self) -> None:
        """Test creating a valid threat agent mapping."""
        mapping = ThreatAgentMapping(
            category="external_attacker",
            attack_vector="credential_stuffing",
            complexity_capability="intermediate",
            relevance_ranking=4,
        )
        assert mapping.relevance_ranking == 4

    def test_relevance_ranking_bounds(self) -> None:
        """Test that relevance ranking must be 1-5."""
        with pytest.raises(ValidationError):
            ThreatAgentMapping(
                category="test",
                attack_vector="test",
                complexity_capability="test",
                relevance_ranking=0,  # invalid
            )

        with pytest.raises(ValidationError):
            ThreatAgentMapping(
                category="test",
                attack_vector="test",
                complexity_capability="test",
                relevance_ranking=6,  # invalid
            )


class TestGenerationResult:
    """Tests for GenerationResult schema."""

    def test_valid_generation_result(self, sample_user_story: UserStory) -> None:
        """Test creating a valid generation result."""
        result = GenerationResult(
            user_story=sample_user_story,
            requirements=[],
            model_used="llama3:8b",
            rag_enabled=True,
        )
        assert result.rag_enabled is True
        assert result.model_used == "llama3:8b"

    def test_generation_result_with_requirements(
        self,
        sample_user_story: UserStory,
        sample_security_requirement: SecurityRequirement,
    ) -> None:
        """Test generation result with requirements."""
        result = GenerationResult(
            user_story=sample_user_story,
            requirements=[sample_security_requirement],
            model_used="llama3:8b",
            rag_enabled=False,
        )
        assert len(result.requirements) == 1
        assert result.requirements[0].id == "SR-001"


class TestEvaluationResult:
    """Tests for EvaluationResult schema."""

    def test_valid_evaluation_result(self) -> None:
        """Test creating a valid evaluation result."""
        result = EvaluationResult(
            precision=0.85,
            recall=0.90,
            f1_score=0.87,
            hallucination_rate=0.05,
            true_positives=17,
            false_positives=3,
            false_negatives=2,
        )
        assert result.precision == 0.85
        assert result.f1_score == 0.87

    def test_evaluation_bounds(self) -> None:
        """Test that scores must be between 0 and 1."""
        with pytest.raises(ValidationError):
            EvaluationResult(
                precision=1.5,  # invalid
                recall=0.90,
                f1_score=0.87,
                hallucination_rate=0.05,
            )
