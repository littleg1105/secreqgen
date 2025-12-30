"""Unit tests for semantic similarity matcher."""

import numpy as np
import pytest

from secreqgen.evaluation.semantic_matcher import (
    GroundTruthRequirement,
    MatchResult,
    MatchType,
    SemanticMatcher,
    load_ground_truth,
)
from secreqgen.generation.schemas import (
    ASVSCategory,
    Priority,
    SecurityRequirement,
)


# Fixtures
@pytest.fixture
def sample_generated_requirements() -> list[SecurityRequirement]:
    """Sample generated requirements for testing."""
    return [
        SecurityRequirement(
            id="SR-001",
            user_story_id="US-001",
            title="Password Hashing",
            description="Store passwords using Argon2 hashing algorithm with secure parameters",
            rationale="To protect user passwords from exposure in case of database breach",
            asvs_category=ASVSCategory.V2_AUTHENTICATION,
            priority=Priority.CRITICAL,
        ),
        SecurityRequirement(
            id="SR-002",
            user_story_id="US-001",
            title="Session Token Security",
            description="Generate session tokens using cryptographically secure random generator",
            rationale="To prevent session prediction attacks",
            asvs_category=ASVSCategory.V3_SESSION,
            priority=Priority.HIGH,
        ),
        SecurityRequirement(
            id="SR-003",
            user_story_id="US-001",
            title="API Rate Limiting",
            description="Implement rate limiting on API endpoints to prevent abuse",
            rationale="To protect against DoS attacks",
            asvs_category=ASVSCategory.V13_API_WEB_SERVICE,
            priority=Priority.MEDIUM,
        ),
    ]


@pytest.fixture
def sample_ground_truth() -> list[GroundTruthRequirement]:
    """Sample ground truth requirements for testing."""
    return [
        GroundTruthRequirement(
            id="GT-001",
            title="Secure Password Storage",
            description="User passwords must be hashed using Argon2id with memory cost >= 64MB",
            asvs_category="V2: Authentication",
        ),
        GroundTruthRequirement(
            id="GT-002",
            title="Session Token Generation",
            description="Session tokens must be generated using CSPRNG with minimum 128 bits of entropy",
            asvs_category="V3: Session Management",
        ),
        GroundTruthRequirement(
            id="GT-003",
            title="Input Validation",
            description="All user input must be validated and sanitized before processing",
            asvs_category="V5: Validation",
        ),
    ]


@pytest.fixture
def mock_embedder():
    """Mock embedder for testing without loading real model."""

    class MockEmbedder:
        def embed_text(self, text: str) -> np.ndarray:
            # Simple hash-based embedding for testing
            # Generates deterministic but distinct vectors
            np.random.seed(hash(text) % (2**32))
            emb = np.random.randn(768).astype(np.float32)
            return emb / np.linalg.norm(emb)

        def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
            return [self.embed_text(t) for t in texts]

    return MockEmbedder()


@pytest.fixture
def semantic_matcher(mock_embedder) -> SemanticMatcher:
    """Create matcher with mock embedder."""
    return SemanticMatcher(embedder=mock_embedder)


# GroundTruthRequirement tests
class TestGroundTruthRequirement:
    def test_text_for_matching_combines_title_and_description(self):
        """Text for matching should combine title and description."""
        gt = GroundTruthRequirement(
            id="GT-001",
            title="Password Security",
            description="Hash all passwords with Argon2",
        )
        assert gt.text_for_matching == "Password Security. Hash all passwords with Argon2"

    def test_optional_asvs_category(self):
        """ASVS category should be optional."""
        gt = GroundTruthRequirement(
            id="GT-001",
            title="Test",
            description="Test description",
        )
        assert gt.asvs_category is None

    def test_with_asvs_category(self):
        """ASVS category should be stored when provided."""
        gt = GroundTruthRequirement(
            id="GT-001",
            title="Test",
            description="Test description",
            asvs_category="V2: Authentication",
        )
        assert gt.asvs_category == "V2: Authentication"


# MatchResult tests
class TestMatchResult:
    def test_is_match_true_for_match_type(self):
        """is_match should be True for MATCH type."""
        result = MatchResult(
            generated_id="SR-001",
            generated_text="Test",
            ground_truth_id="GT-001",
            ground_truth_text="Test",
            similarity_score=0.90,
            match_type=MatchType.MATCH,
        )
        assert result.is_match is True
        assert result.is_partial is False
        assert result.is_hallucination is False

    def test_is_partial_true_for_partial_type(self):
        """is_partial should be True for PARTIAL type."""
        result = MatchResult(
            generated_id="SR-001",
            generated_text="Test",
            ground_truth_id="GT-001",
            ground_truth_text="Test",
            similarity_score=0.75,
            match_type=MatchType.PARTIAL,
        )
        assert result.is_match is False
        assert result.is_partial is True
        assert result.is_hallucination is False

    def test_is_hallucination_true_for_no_match(self):
        """is_hallucination should be True for NO_MATCH type."""
        result = MatchResult(
            generated_id="SR-001",
            generated_text="Test",
            ground_truth_id=None,
            ground_truth_text=None,
            similarity_score=0.30,
            match_type=MatchType.NO_MATCH,
        )
        assert result.is_match is False
        assert result.is_partial is False
        assert result.is_hallucination is True


# SemanticMatcher tests
class TestSemanticMatcher:
    def test_init_with_default_thresholds(self):
        """Matcher should use default thresholds."""
        matcher = SemanticMatcher(embedder=object())  # Dummy embedder
        assert matcher.match_threshold == 0.85
        assert matcher.partial_threshold == 0.70

    def test_init_with_custom_thresholds(self):
        """Matcher should accept custom thresholds."""
        matcher = SemanticMatcher(
            match_threshold=0.90,
            partial_threshold=0.75,
            embedder=object(),
        )
        assert matcher.match_threshold == 0.90
        assert matcher.partial_threshold == 0.75

    def test_init_invalid_threshold_order_raises(self):
        """Should raise if partial_threshold > match_threshold."""
        with pytest.raises(ValueError, match="partial_threshold"):
            SemanticMatcher(
                match_threshold=0.70,
                partial_threshold=0.85,
                embedder=object(),
            )

    def test_compute_similarity_returns_float(self, semantic_matcher):
        """compute_similarity should return a float score."""
        score = semantic_matcher.compute_similarity(
            "User authentication with passwords",
            "Password-based user login",
        )
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_compute_similarity_same_text_is_one(self, semantic_matcher):
        """Identical text should have similarity of 1.0."""
        text = "This is a test sentence for comparison"
        score = semantic_matcher.compute_similarity(text, text)
        assert score == pytest.approx(1.0, rel=1e-5)

    def test_compute_similarity_caches_embeddings(self, semantic_matcher):
        """Embeddings should be cached for reuse."""
        text1 = "First text"
        text2 = "Second text"

        # First call
        semantic_matcher.compute_similarity(text1, text2)
        assert text1 in semantic_matcher._embeddings_cache
        assert text2 in semantic_matcher._embeddings_cache

    def test_classify_match_above_threshold(self, semantic_matcher):
        """Similarity above match_threshold should be MATCH."""
        result = semantic_matcher._classify_match(0.90)
        assert result == MatchType.MATCH

    def test_classify_match_partial(self, semantic_matcher):
        """Similarity in partial range should be PARTIAL."""
        result = semantic_matcher._classify_match(0.75)
        assert result == MatchType.PARTIAL

    def test_classify_match_below_partial(self, semantic_matcher):
        """Similarity below partial_threshold should be NO_MATCH."""
        result = semantic_matcher._classify_match(0.50)
        assert result == MatchType.NO_MATCH

    def test_classify_match_at_threshold(self, semantic_matcher):
        """Similarity at threshold should be classified correctly."""
        # At match threshold -> MATCH
        assert semantic_matcher._classify_match(0.85) == MatchType.MATCH
        # At partial threshold -> PARTIAL
        assert semantic_matcher._classify_match(0.70) == MatchType.PARTIAL

    def test_match_requirements_empty_generated(
        self, semantic_matcher, sample_ground_truth
    ):
        """Empty generated list should return empty results."""
        results = semantic_matcher.match_requirements([], sample_ground_truth)
        assert results == []

    def test_match_requirements_empty_ground_truth(
        self, semantic_matcher, sample_generated_requirements
    ):
        """Empty ground truth should mark all generated as no_match."""
        results = semantic_matcher.match_requirements(
            sample_generated_requirements, []
        )
        assert len(results) == len(sample_generated_requirements)
        for result in results:
            assert result.match_type == MatchType.NO_MATCH
            assert result.ground_truth_id is None

    def test_match_requirements_returns_one_per_generated(
        self, semantic_matcher, sample_generated_requirements, sample_ground_truth
    ):
        """Should return one MatchResult per generated requirement."""
        results = semantic_matcher.match_requirements(
            sample_generated_requirements, sample_ground_truth
        )
        assert len(results) == len(sample_generated_requirements)

    def test_match_requirements_no_duplicate_gt_matches(
        self, semantic_matcher, sample_generated_requirements, sample_ground_truth
    ):
        """Each ground truth should only be matched once."""
        results = semantic_matcher.match_requirements(
            sample_generated_requirements, sample_ground_truth
        )
        matched_gts = [
            r.ground_truth_id for r in results if r.ground_truth_id is not None
        ]
        assert len(matched_gts) == len(set(matched_gts))  # No duplicates

    def test_match_requirements_result_structure(
        self, semantic_matcher, sample_generated_requirements, sample_ground_truth
    ):
        """MatchResult should have correct structure."""
        results = semantic_matcher.match_requirements(
            sample_generated_requirements, sample_ground_truth
        )
        for result in results:
            assert isinstance(result, MatchResult)
            assert isinstance(result.generated_id, str)
            assert isinstance(result.similarity_score, float)
            assert isinstance(result.match_type, MatchType)

    def test_clear_cache(self, semantic_matcher):
        """clear_cache should empty the cache."""
        semantic_matcher.compute_similarity("text1", "text2")
        assert len(semantic_matcher._embeddings_cache) > 0

        semantic_matcher.clear_cache()
        assert len(semantic_matcher._embeddings_cache) == 0


class TestSemanticMatcherHighSimilarity:
    """Tests with controlled high similarity scores."""

    @pytest.fixture
    def high_similarity_embedder(self):
        """Embedder that returns similar vectors for similar text patterns."""

        class HighSimilarityEmbedder:
            def embed_text(self, text: str) -> np.ndarray:
                # Base vector that we modify slightly based on content
                base = np.ones(768, dtype=np.float32)

                # Add features based on keywords
                if "password" in text.lower():
                    base[:100] += 1.0
                if "session" in text.lower():
                    base[100:200] += 1.0
                if "api" in text.lower() or "rate" in text.lower():
                    base[200:300] += 1.0
                if "input" in text.lower() or "validation" in text.lower():
                    base[300:400] += 1.0

                # Add small noise
                np.random.seed(len(text))
                base += np.random.randn(768).astype(np.float32) * 0.1

                return base / np.linalg.norm(base)

            def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
                return [self.embed_text(t) for t in texts]

        return HighSimilarityEmbedder()

    def test_similar_topics_get_matched(self, high_similarity_embedder):
        """Requirements about the same topic should match."""
        matcher = SemanticMatcher(embedder=high_similarity_embedder)

        generated = [
            SecurityRequirement(
                id="SR-001",
                user_story_id="US-001",
                title="Password Hashing",
                description="Store user passwords with secure hashing",
                rationale="Security",
                asvs_category=ASVSCategory.V2_AUTHENTICATION,
            )
        ]
        ground_truth = [
            GroundTruthRequirement(
                id="GT-001",
                title="Secure Password Storage",
                description="Hash passwords before storing in database",
            )
        ]

        results = matcher.match_requirements(generated, ground_truth)
        assert len(results) == 1
        # Both mention "password" so should have high similarity
        assert results[0].ground_truth_id == "GT-001"

    def test_different_topics_lower_similarity(self, high_similarity_embedder):
        """Requirements about different topics should have lower similarity."""
        matcher = SemanticMatcher(
            match_threshold=0.95,  # Very high threshold
            partial_threshold=0.90,
            embedder=high_similarity_embedder,
        )

        generated = [
            SecurityRequirement(
                id="SR-001",
                user_story_id="US-001",
                title="API Rate Limiting",
                description="Limit API requests per client",
                rationale="Security",
                asvs_category=ASVSCategory.V13_API_WEB_SERVICE,
            )
        ]
        ground_truth = [
            GroundTruthRequirement(
                id="GT-001",
                title="Input Validation",
                description="Validate all user input fields",
            )
        ]

        results = matcher.match_requirements(generated, ground_truth)
        # Different topics, should not reach high threshold
        assert results[0].match_type in (MatchType.PARTIAL, MatchType.NO_MATCH)


# load_ground_truth tests
class TestLoadGroundTruth:
    def test_load_from_list(self):
        """Should load ground truth from list of dicts."""
        data = [
            {
                "id": "GT-001",
                "title": "Test Title",
                "description": "Test Description",
                "asvs_category": "V2: Authentication",
            }
        ]
        results = load_ground_truth(data)
        assert len(results) == 1
        assert results[0].id == "GT-001"
        assert results[0].title == "Test Title"
        assert results[0].description == "Test Description"
        assert results[0].asvs_category == "V2: Authentication"

    def test_load_generates_ids_if_missing(self):
        """Should generate IDs if not provided."""
        data = [
            {"title": "First", "description": "Desc 1"},
            {"title": "Second", "description": "Desc 2"},
        ]
        results = load_ground_truth(data)
        assert results[0].id == "GT-001"
        assert results[1].id == "GT-002"

    def test_load_handles_missing_asvs_category(self):
        """Should handle missing ASVS category."""
        data = [{"id": "GT-001", "title": "Test", "description": "Desc"}]
        results = load_ground_truth(data)
        assert results[0].asvs_category is None

    def test_load_empty_list(self):
        """Should handle empty list."""
        results = load_ground_truth([])
        assert results == []

    def test_load_multiple_items(self):
        """Should load multiple items."""
        data = [
            {"id": f"GT-{i:03d}", "title": f"Title {i}", "description": f"Desc {i}"}
            for i in range(5)
        ]
        results = load_ground_truth(data)
        assert len(results) == 5
        for i, result in enumerate(results):
            assert result.id == f"GT-{i:03d}"


# Integration test with real embedder (marked slow)
@pytest.mark.slow
class TestSemanticMatcherIntegration:
    """Integration tests that use the real embedder (slow)."""

    def test_real_embedder_similar_requirements(self):
        """Similar requirements should have high similarity with real embedder."""
        matcher = SemanticMatcher()  # Uses real embedder

        score = matcher.compute_similarity(
            "Password Hashing. Store user passwords using Argon2 hashing algorithm",
            "Secure Password Storage. Hash passwords with Argon2id before storing",
        )
        # These are semantically similar
        assert score > 0.7

    def test_real_embedder_dissimilar_requirements(self):
        """Dissimilar requirements should have lower similarity."""
        matcher = SemanticMatcher()

        score = matcher.compute_similarity(
            "Password Hashing. Store user passwords using Argon2 hashing algorithm",
            "File Upload. Validate file types using magic bytes and reject executables",
        )
        # These are semantically different
        assert score < 0.7
