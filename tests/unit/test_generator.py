"""Unit tests for SecurityRequirementsGenerator."""

import json
from unittest.mock import MagicMock, patch

import pytest

from secreqgen.exceptions import GenerationError
from secreqgen.generation.generator import (
    GenerationMode,
    SecurityRequirementsGenerator,
)
from secreqgen.generation.llm_client import LLMResponse
from secreqgen.generation.schemas import ASVSCategory, Priority, UserStory
from secreqgen.retrieval.retriever import RetrievalResult
from secreqgen.retrieval.vectorstore import QueryResult


class TestGenerationMode:
    """Tests for GenerationMode enum."""

    def test_rag_mode_value(self):
        """Test RAG mode string value."""
        assert GenerationMode.RAG.value == "rag"

    def test_zero_shot_mode_value(self):
        """Test zero-shot mode string value."""
        assert GenerationMode.ZERO_SHOT.value == "zero_shot"


class TestSecurityRequirementsGeneratorInit:
    """Tests for generator initialization."""

    def test_minimal_initialization(self):
        """Test initialization with just LLM client."""
        mock_llm = MagicMock()

        generator = SecurityRequirementsGenerator(llm_client=mock_llm)

        assert generator.llm_client == mock_llm
        assert generator.retriever is None
        assert generator.max_retries == 3
        assert generator.retry_delay == 1.0

    def test_full_initialization(self):
        """Test initialization with all components."""
        mock_llm = MagicMock()
        mock_retriever = MagicMock()
        mock_prompt_builder = MagicMock()

        generator = SecurityRequirementsGenerator(
            llm_client=mock_llm,
            retriever=mock_retriever,
            prompt_builder=mock_prompt_builder,
            max_retries=5,
            retry_delay=2.0,
        )

        assert generator.llm_client == mock_llm
        assert generator.retriever == mock_retriever
        assert generator.prompt_builder == mock_prompt_builder
        assert generator.max_retries == 5
        assert generator.retry_delay == 2.0


class TestJSONExtraction:
    """Tests for JSON extraction from LLM responses."""

    @pytest.fixture
    def generator(self):
        """Create a generator with mocked LLM."""
        return SecurityRequirementsGenerator(llm_client=MagicMock())

    def test_extract_json_from_code_block(self, generator):
        """Test extracting JSON from markdown code block."""
        text = '''Here is the response:
```json
{"requirements": [{"id": "SR-001", "title": "Test"}]}
```
'''
        result = generator._extract_json(text)
        assert result == {"requirements": [{"id": "SR-001", "title": "Test"}]}

    def test_extract_json_from_code_block_no_lang(self, generator):
        """Test extracting JSON from code block without language specifier."""
        text = '''
```
{"requirements": []}
```
'''
        result = generator._extract_json(text)
        assert result == {"requirements": []}

    def test_extract_json_raw_object(self, generator):
        """Test extracting raw JSON object."""
        text = 'The result is: {"requirements": [{"id": "SR-001"}]}'
        result = generator._extract_json(text)
        assert result["requirements"][0]["id"] == "SR-001"

    def test_extract_json_plain(self, generator):
        """Test extracting plain JSON text."""
        text = '{"requirements": []}'
        result = generator._extract_json(text)
        assert result == {"requirements": []}

    def test_extract_json_with_whitespace(self, generator):
        """Test JSON extraction with surrounding whitespace."""
        text = '''

{"requirements": [{"id": "SR-001"}]}

'''
        result = generator._extract_json(text)
        assert result["requirements"][0]["id"] == "SR-001"

    def test_extract_json_invalid_raises(self, generator):
        """Test that invalid JSON raises JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            generator._extract_json("This is not JSON at all")


class TestASVSCategoryNormalization:
    """Tests for ASVS category normalization."""

    @pytest.fixture
    def generator(self):
        """Create a generator with mocked LLM."""
        return SecurityRequirementsGenerator(llm_client=MagicMock())

    def test_exact_match(self, generator):
        """Test exact category match."""
        result = generator._normalize_asvs_category("V2: Authentication")
        assert result == ASVSCategory.V2_AUTHENTICATION

    def test_v_number_only(self, generator):
        """Test V-number only."""
        result = generator._normalize_asvs_category("V5")
        assert result == ASVSCategory.V5_VALIDATION

    def test_v_number_with_prefix(self, generator):
        """Test v-number with different prefix."""
        result = generator._normalize_asvs_category("v3")
        assert result == ASVSCategory.V3_SESSION

    def test_keyword_authentication(self, generator):
        """Test keyword-based matching for authentication."""
        result = generator._normalize_asvs_category("Authentication Requirements")
        assert result == ASVSCategory.V2_AUTHENTICATION

    def test_keyword_cryptography(self, generator):
        """Test keyword-based matching for cryptography."""
        result = generator._normalize_asvs_category("Encryption and Cryptography")
        assert result == ASVSCategory.V6_CRYPTOGRAPHY

    def test_keyword_api(self, generator):
        """Test keyword-based matching for API."""
        result = generator._normalize_asvs_category("API Security")
        assert result == ASVSCategory.V13_API_WEB_SERVICE

    def test_keyword_session(self, generator):
        """Test keyword-based matching for session."""
        result = generator._normalize_asvs_category("Session Management")
        assert result == ASVSCategory.V3_SESSION

    def test_unknown_defaults_to_v1(self, generator):
        """Test that unknown category defaults to V1."""
        result = generator._normalize_asvs_category("Unknown Category XYZ")
        assert result == ASVSCategory.V1_ARCHITECTURE


class TestPriorityNormalization:
    """Tests for priority normalization."""

    @pytest.fixture
    def generator(self):
        """Create a generator with mocked LLM."""
        return SecurityRequirementsGenerator(llm_client=MagicMock())

    def test_exact_critical(self, generator):
        """Test exact critical priority."""
        assert generator._normalize_priority("critical") == Priority.CRITICAL

    def test_exact_high(self, generator):
        """Test exact high priority."""
        assert generator._normalize_priority("high") == Priority.HIGH

    def test_exact_medium(self, generator):
        """Test exact medium priority."""
        assert generator._normalize_priority("medium") == Priority.MEDIUM

    def test_exact_low(self, generator):
        """Test exact low priority."""
        assert generator._normalize_priority("low") == Priority.LOW

    def test_case_insensitive(self, generator):
        """Test case insensitivity."""
        assert generator._normalize_priority("CRITICAL") == Priority.CRITICAL
        assert generator._normalize_priority("High") == Priority.HIGH

    def test_synonym_highest(self, generator):
        """Test synonym 'highest' maps to critical."""
        assert generator._normalize_priority("highest") == Priority.CRITICAL

    def test_synonym_important(self, generator):
        """Test synonym 'important' maps to high."""
        assert generator._normalize_priority("important") == Priority.HIGH

    def test_synonym_moderate(self, generator):
        """Test synonym 'moderate' maps to medium."""
        assert generator._normalize_priority("moderate") == Priority.MEDIUM

    def test_unknown_defaults_to_medium(self, generator):
        """Test that unknown priority defaults to medium."""
        assert generator._normalize_priority("unknown") == Priority.MEDIUM


class TestRequirementParsing:
    """Tests for requirement parsing."""

    @pytest.fixture
    def generator(self):
        """Create a generator with mocked LLM."""
        return SecurityRequirementsGenerator(llm_client=MagicMock())

    @pytest.fixture
    def sample_user_story(self):
        """Create a sample user story."""
        return UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in.",
        )

    def test_parse_complete_requirement(self, generator, sample_user_story):
        """Test parsing a complete requirement."""
        raw = {
            "id": "SR-001",
            "user_story_id": "US-001",
            "title": "Secure Password Storage",
            "description": "Passwords must be hashed",
            "rationale": "Prevents credential theft",
            "acceptance_criteria": ["Use bcrypt", "Salt passwords"],
            "asvs_category": "V2: Authentication",
            "priority": "critical",
            "implementation_notes": "Use bcrypt with cost 12",
            "source_references": ["OWASP ASVS 2.4.1"],
        }

        req = generator._parse_requirement(raw, sample_user_story, 0)

        assert req.id == "SR-001"
        assert req.user_story_id == "US-001"
        assert req.title == "Secure Password Storage"
        assert req.description == "Passwords must be hashed"
        assert req.rationale == "Prevents credential theft"
        assert req.acceptance_criteria == ["Use bcrypt", "Salt passwords"]
        assert req.asvs_category == ASVSCategory.V2_AUTHENTICATION
        assert req.priority == Priority.CRITICAL
        assert req.implementation_notes == "Use bcrypt with cost 12"
        assert req.source_references == ["OWASP ASVS 2.4.1"]

    def test_parse_minimal_requirement(self, generator, sample_user_story):
        """Test parsing requirement with minimal fields."""
        raw = {
            "title": "Basic Requirement",
            "description": "Do something secure",
        }

        req = generator._parse_requirement(raw, sample_user_story, 0)

        assert req.id == "SR-001"  # Generated
        assert req.user_story_id == "US-001"  # From story
        assert req.title == "Basic Requirement"
        assert req.priority == Priority.MEDIUM  # Default

    def test_parse_requirement_generates_id(self, generator, sample_user_story):
        """Test that missing ID is generated from index."""
        raw = {"title": "Test"}

        req0 = generator._parse_requirement(raw, sample_user_story, 0)
        req1 = generator._parse_requirement(raw, sample_user_story, 1)
        req9 = generator._parse_requirement(raw, sample_user_story, 9)

        assert req0.id == "SR-001"
        assert req1.id == "SR-002"
        assert req9.id == "SR-010"


class TestGenerateMethod:
    """Tests for the main generate() method."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        llm = MagicMock()
        llm.model_name = "llama3:8b"
        llm.generate.return_value = LLMResponse(
            content='{"requirements": [{"id": "SR-001", "title": "Test Req", "description": "Desc", "rationale": "Why", "asvs_category": "V2: Authentication", "priority": "high"}]}',
            model="llama3:8b",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=500.0,
            raw_response={},
        )
        return llm

    @pytest.fixture
    def mock_retriever(self):
        """Create a mock retriever."""
        retriever = MagicMock()
        retriever.get_system_context.return_value = "system: test"
        retriever.retrieve_for_user_story.return_value = RetrievalResult(
            chunks=[
                QueryResult(
                    chunk_id="c1",
                    content="Auth policy content",
                    source_document="/docs/auth.md",
                    section_hierarchy=["Auth"],
                    distance=0.3,
                )
            ],
            query_text="test",
            total_retrieved=1,
            above_threshold=1,
            retrieval_time_ms=50.0,
        )
        retriever.format_result_for_prompt.return_value = "[1] Auth content..."
        return retriever

    @pytest.fixture
    def sample_story(self):
        """Create a sample user story."""
        return UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in.",
        )

    def test_generate_rag_mode(self, mock_llm, mock_retriever, sample_story):
        """Test generation in RAG mode."""
        generator = SecurityRequirementsGenerator(
            llm_client=mock_llm,
            retriever=mock_retriever,
        )

        result = generator.generate(sample_story, mode=GenerationMode.RAG)

        # Verify retriever was called
        mock_retriever.retrieve_for_user_story.assert_called_once_with(sample_story)
        mock_retriever.format_result_for_prompt.assert_called_once()

        # Verify LLM was called
        mock_llm.generate.assert_called_once()

        # Verify result
        assert result.rag_enabled is True
        assert result.model_used == "llama3:8b"
        assert len(result.requirements) == 1
        assert result.requirements[0].id == "SR-001"

    def test_generate_zero_shot_mode(self, mock_llm, sample_story):
        """Test generation in zero-shot mode."""
        generator = SecurityRequirementsGenerator(llm_client=mock_llm)

        result = generator.generate(sample_story, mode=GenerationMode.ZERO_SHOT)

        # Verify LLM was called
        mock_llm.generate.assert_called_once()

        # Verify result
        assert result.rag_enabled is False
        assert result.retrieval_context is None

    def test_generate_zero_shot_with_retriever(
        self, mock_llm, mock_retriever, sample_story
    ):
        """Test zero-shot mode doesn't use retriever even if available."""
        generator = SecurityRequirementsGenerator(
            llm_client=mock_llm,
            retriever=mock_retriever,
        )

        result = generator.generate(sample_story, mode=GenerationMode.ZERO_SHOT)

        # Retriever should NOT be called for context retrieval
        mock_retriever.retrieve_for_user_story.assert_not_called()

        # But system context may still be fetched
        assert result.rag_enabled is False

    def test_generate_rag_without_retriever_raises(self, mock_llm, sample_story):
        """Test that RAG mode without retriever raises error."""
        generator = SecurityRequirementsGenerator(llm_client=mock_llm)

        with pytest.raises(ValueError) as exc_info:
            generator.generate(sample_story, mode=GenerationMode.RAG)

        assert "no retriever provided" in str(exc_info.value).lower()

    def test_generate_with_custom_system_context(
        self, mock_llm, mock_retriever, sample_story
    ):
        """Test generation with custom system context."""
        generator = SecurityRequirementsGenerator(
            llm_client=mock_llm,
            retriever=mock_retriever,
        )

        result = generator.generate(
            sample_story,
            mode=GenerationMode.RAG,
            system_context="custom: context",
        )

        # Should not fetch system context from retriever
        mock_retriever.get_system_context.assert_not_called()


class TestRetryLogic:
    """Tests for retry logic on JSON parse failures."""

    @pytest.fixture
    def sample_story(self):
        """Create a sample user story."""
        return UserStory(
            id="US-001",
            title="Test",
            story="Test story",
        )

    def test_retry_on_json_error(self, sample_story):
        """Test that generator retries on JSON parse error."""
        mock_llm = MagicMock()
        mock_llm.model_name = "test"

        # First two calls return invalid JSON, third succeeds
        mock_llm.generate.side_effect = [
            LLMResponse(
                content="Invalid JSON",
                model="test",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_ms=100,
                raw_response={},
            ),
            LLMResponse(
                content="Still invalid",
                model="test",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_ms=100,
                raw_response={},
            ),
            LLMResponse(
                content='{"requirements": []}',
                model="test",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_ms=100,
                raw_response={},
            ),
        ]

        generator = SecurityRequirementsGenerator(
            llm_client=mock_llm,
            max_retries=3,
            retry_delay=0.01,  # Fast for testing
        )

        result = generator.generate(sample_story, mode=GenerationMode.ZERO_SHOT)

        # Should have called LLM 3 times
        assert mock_llm.generate.call_count == 3
        assert result.requirements == []

    def test_exhausted_retries_raises_error(self, sample_story):
        """Test that exhausted retries raises GenerationError."""
        mock_llm = MagicMock()
        mock_llm.model_name = "test"
        mock_llm.generate.return_value = LLMResponse(
            content="Always invalid JSON",
            model="test",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=100,
            raw_response={},
        )

        generator = SecurityRequirementsGenerator(
            llm_client=mock_llm,
            max_retries=2,
            retry_delay=0.01,
        )

        with pytest.raises(GenerationError) as exc_info:
            generator.generate(sample_story, mode=GenerationMode.ZERO_SHOT)

        assert "Failed to generate valid JSON" in str(exc_info.value.message)
        assert mock_llm.generate.call_count == 2


class TestBatchGeneration:
    """Tests for batch generation."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        llm = MagicMock()
        llm.model_name = "test"
        llm.generate.return_value = LLMResponse(
            content='{"requirements": [{"id": "SR-001", "title": "Test"}]}',
            model="test",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=100,
            raw_response={},
        )
        return llm

    def test_generate_batch(self, mock_llm):
        """Test batch generation."""
        stories = [
            UserStory(id="US-001", title="Story 1", story="First story"),
            UserStory(id="US-002", title="Story 2", story="Second story"),
            UserStory(id="US-003", title="Story 3", story="Third story"),
        ]

        generator = SecurityRequirementsGenerator(llm_client=mock_llm)

        results = generator.generate_batch(stories, mode=GenerationMode.ZERO_SHOT)

        assert len(results) == 3
        assert mock_llm.generate.call_count == 3

    def test_generate_batch_with_progress_callback(self, mock_llm):
        """Test batch generation with progress callback."""
        stories = [
            UserStory(id="US-001", title="Story 1", story="Story"),
            UserStory(id="US-002", title="Story 2", story="Story"),
        ]

        progress_calls = []

        def progress_callback(current, total):
            progress_calls.append((current, total))

        generator = SecurityRequirementsGenerator(llm_client=mock_llm)

        generator.generate_batch(
            stories,
            mode=GenerationMode.ZERO_SHOT,
            progress_callback=progress_callback,
        )

        # Should have progress calls for 0/2, 1/2, 2/2
        assert progress_calls == [(0, 2), (1, 2), (2, 2)]


class TestThreatMappingParsing:
    """Tests for threat mapping parsing."""

    @pytest.fixture
    def generator(self):
        """Create a generator with mocked LLM."""
        return SecurityRequirementsGenerator(llm_client=MagicMock())

    def test_parse_threat_mapping(self, generator):
        """Test parsing a threat mapping."""
        raw = {
            "category": "external_attacker",
            "attack_vector": "credential stuffing",
            "complexity_capability": "low",
            "relevance_ranking": 4,
        }

        threat = generator._parse_threat_mapping(raw)

        assert threat.category == "external_attacker"
        assert threat.attack_vector == "credential stuffing"
        assert threat.complexity_capability == "low"
        assert threat.relevance_ranking == 4

    def test_parse_threat_mapping_clamps_ranking(self, generator):
        """Test that relevance ranking is clamped to 1-5."""
        raw_high = {"relevance_ranking": 10}
        raw_low = {"relevance_ranking": -1}

        threat_high = generator._parse_threat_mapping(raw_high)
        threat_low = generator._parse_threat_mapping(raw_low)

        assert threat_high.relevance_ranking == 5
        assert threat_low.relevance_ranking == 1

    def test_parse_threat_mapping_defaults(self, generator):
        """Test default values for missing fields."""
        raw = {}

        threat = generator._parse_threat_mapping(raw)

        assert threat.category == "unknown"
        assert threat.attack_vector == ""
        assert threat.complexity_capability == "intermediate"
        assert threat.relevance_ranking == 3
