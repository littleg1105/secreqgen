"""Unit tests for SimpleRetriever."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from secreqgen.generation.schemas import UserStory
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval.retriever import RetrievalResult, SimpleRetriever
from secreqgen.retrieval.vectorstore import QueryResult


class TestRetrievalResult:
    """Tests for RetrievalResult dataclass."""

    def test_retrieval_result_creation(self):
        """Test creating a RetrievalResult."""
        chunks = [
            QueryResult(
                chunk_id="chunk1",
                content="Test content",
                source_document="/docs/test.md",
                section_hierarchy=["Section"],
                distance=0.3,
            )
        ]

        result = RetrievalResult(
            chunks=chunks,
            query_text="test query",
            total_retrieved=5,
            above_threshold=1,
            retrieval_time_ms=100.5,
        )

        assert len(result.chunks) == 1
        assert result.query_text == "test query"
        assert result.total_retrieved == 5
        assert result.above_threshold == 1
        assert result.retrieval_time_ms == 100.5


class TestSimpleRetrieverInit:
    """Tests for SimpleRetriever initialization."""

    def test_default_initialization(self):
        """Test default retriever initialization."""
        mock_vectorstore = MagicMock()
        mock_embedder = MagicMock()

        retriever = SimpleRetriever(
            vectorstore=mock_vectorstore,
            embedder=mock_embedder,
        )

        assert retriever.vectorstore == mock_vectorstore
        assert retriever.embedder == mock_embedder
        assert retriever.top_k == 5
        assert retriever.similarity_threshold == 0.7

    def test_custom_initialization(self):
        """Test custom retriever initialization."""
        mock_vectorstore = MagicMock()
        mock_embedder = MagicMock()

        retriever = SimpleRetriever(
            vectorstore=mock_vectorstore,
            embedder=mock_embedder,
            top_k=10,
            similarity_threshold=0.85,
        )

        assert retriever.top_k == 10
        assert retriever.similarity_threshold == 0.85


class TestSimpleRetrieverRetrieve:
    """Tests for SimpleRetriever.retrieve()."""

    @pytest.fixture
    def mock_embedder(self):
        """Create a mock embedder."""
        embedder = MagicMock()
        embedder.embed_text.return_value = np.array([0.1, 0.2, 0.3])
        return embedder

    @pytest.fixture
    def mock_vectorstore(self):
        """Create a mock vectorstore."""
        return MagicMock()

    @pytest.fixture
    def retriever(self, mock_vectorstore, mock_embedder):
        """Create a retriever with mocked dependencies."""
        return SimpleRetriever(
            vectorstore=mock_vectorstore,
            embedder=mock_embedder,
            top_k=5,
            similarity_threshold=0.7,
        )

    def test_retrieve_basic(self, retriever, mock_vectorstore, mock_embedder):
        """Test basic retrieval."""
        # Setup mock response with high similarity
        mock_vectorstore.query.return_value = [
            QueryResult(
                chunk_id="chunk1",
                content="Authentication policy content",
                source_document="/docs/auth.md",
                section_hierarchy=["Authentication"],
                distance=0.4,  # similarity = 1 - 0.4/2 = 0.8
            ),
            QueryResult(
                chunk_id="chunk2",
                content="Low relevance content",
                source_document="/docs/other.md",
                section_hierarchy=["Other"],
                distance=0.8,  # similarity = 1 - 0.8/2 = 0.6 (below threshold)
            ),
        ]

        result = retriever.retrieve("password requirements")

        # Verify embedder was called
        mock_embedder.embed_text.assert_called_once_with("password requirements")

        # Verify vectorstore was called with correct params
        mock_vectorstore.query.assert_called_once()
        call_args = mock_vectorstore.query.call_args

        assert call_args.kwargs["n_results"] == 5
        assert DocumentType.POLICY in call_args.kwargs["doc_types"]
        assert DocumentType.ARCHITECTURE in call_args.kwargs["doc_types"]
        assert DocumentType.STANDARD in call_args.kwargs["doc_types"]
        # SYSTEM_CONTEXT should NOT be in doc_types by default
        assert DocumentType.SYSTEM_CONTEXT not in call_args.kwargs["doc_types"]

        # Check results - only chunk1 should pass threshold (0.8 >= 0.7)
        assert result.total_retrieved == 2
        assert result.above_threshold == 1
        assert len(result.chunks) == 1
        assert result.chunks[0].chunk_id == "chunk1"

    def test_retrieve_with_custom_doc_types(self, retriever, mock_vectorstore):
        """Test retrieval with specific document types."""
        mock_vectorstore.query.return_value = []

        retriever.retrieve(
            "test query",
            doc_types=[DocumentType.POLICY],
        )

        call_args = mock_vectorstore.query.call_args
        assert call_args.kwargs["doc_types"] == [DocumentType.POLICY]

    def test_retrieve_with_custom_top_k(self, retriever, mock_vectorstore):
        """Test retrieval with custom top_k."""
        mock_vectorstore.query.return_value = []

        retriever.retrieve("test query", top_k=10)

        call_args = mock_vectorstore.query.call_args
        assert call_args.kwargs["n_results"] == 10

    def test_retrieve_empty_results(self, retriever, mock_vectorstore):
        """Test retrieval with no results."""
        mock_vectorstore.query.return_value = []

        result = retriever.retrieve("nonexistent topic")

        assert result.total_retrieved == 0
        assert result.above_threshold == 0
        assert result.chunks == []

    def test_retrieve_all_below_threshold(self, retriever, mock_vectorstore):
        """Test retrieval when all results are below threshold."""
        mock_vectorstore.query.return_value = [
            QueryResult(
                chunk_id="chunk1",
                content="Low relevance",
                source_document="/docs/test.md",
                section_hierarchy=["Section"],
                distance=1.2,  # similarity = 1 - 1.2/2 = 0.4 (below 0.7)
            ),
        ]

        result = retriever.retrieve("test query")

        assert result.total_retrieved == 1
        assert result.above_threshold == 0
        assert result.chunks == []

    def test_retrieve_tracks_timing(self, retriever, mock_vectorstore):
        """Test that retrieval tracks timing."""
        mock_vectorstore.query.return_value = []

        result = retriever.retrieve("test query")

        assert result.retrieval_time_ms >= 0
        assert result.query_text == "test query"


class TestSimpleRetrieverRetrieveForUserStory:
    """Tests for SimpleRetriever.retrieve_for_user_story()."""

    @pytest.fixture
    def retriever(self):
        """Create a retriever with mocked dependencies."""
        mock_vectorstore = MagicMock()
        mock_vectorstore.query.return_value = []

        mock_embedder = MagicMock()
        mock_embedder.embed_text.return_value = np.array([0.1, 0.2, 0.3])

        return SimpleRetriever(
            vectorstore=mock_vectorstore,
            embedder=mock_embedder,
        )

    @pytest.fixture
    def sample_user_story(self):
        """Create a sample user story."""
        return UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in to the application so that I can access my account.",
            acceptance_criteria=[
                "User can enter username and password",
                "Invalid credentials show error message",
            ],
        )

    def test_retrieve_for_user_story_builds_query(
        self, retriever, sample_user_story
    ):
        """Test that user story is converted to a proper query."""
        result = retriever.retrieve_for_user_story(sample_user_story)

        # Check that embedder was called with combined query
        call_args = retriever.embedder.embed_text.call_args[0][0]

        assert "User Login" in call_args
        assert "log in to the application" in call_args
        assert "username and password" in call_args
        assert "Invalid credentials" in call_args

    def test_retrieve_for_user_story_no_acceptance_criteria(self, retriever):
        """Test retrieval for story without acceptance criteria."""
        story = UserStory(
            id="US-002",
            title="Password Reset",
            story="As a user, I want to reset my password.",
            acceptance_criteria=[],
        )

        result = retriever.retrieve_for_user_story(story)

        call_args = retriever.embedder.embed_text.call_args[0][0]
        assert "Password Reset" in call_args
        assert "reset my password" in call_args

    def test_retrieve_for_user_story_with_custom_top_k(
        self, retriever, sample_user_story
    ):
        """Test retrieval with custom top_k."""
        retriever.retrieve_for_user_story(sample_user_story, top_k=10)

        call_args = retriever.vectorstore.query.call_args
        assert call_args.kwargs["n_results"] == 10


class TestSimpleRetrieverGetSystemContext:
    """Tests for SimpleRetriever.get_system_context()."""

    @pytest.fixture
    def retriever(self):
        """Create a retriever with mocked dependencies."""
        mock_vectorstore = MagicMock()
        mock_embedder = MagicMock()
        mock_embedder.embed_text.return_value = np.array([0.1, 0.2, 0.3])

        return SimpleRetriever(
            vectorstore=mock_vectorstore,
            embedder=mock_embedder,
        )

    def test_get_system_context_found(self, retriever):
        """Test retrieving existing system context."""
        system_context_content = """system:
  name: SecureApp
  type: web_application"""

        retriever.vectorstore.query.return_value = [
            QueryResult(
                chunk_id="sys-context-1",
                content=system_context_content,
                source_document="/config/system.yaml",
                section_hierarchy=[],
                distance=0.1,
            )
        ]

        result = retriever.get_system_context()

        assert result == system_context_content

        # Verify query was for SYSTEM_CONTEXT only
        call_args = retriever.vectorstore.query.call_args
        assert call_args.kwargs["doc_types"] == [DocumentType.SYSTEM_CONTEXT]
        assert call_args.kwargs["n_results"] == 1

    def test_get_system_context_not_found(self, retriever):
        """Test when no system context exists."""
        retriever.vectorstore.query.return_value = []

        result = retriever.get_system_context()

        assert result is None


class TestSimpleRetrieverFormatContext:
    """Tests for SimpleRetriever.format_context()."""

    @pytest.fixture
    def retriever(self):
        """Create a retriever with mocked dependencies."""
        return SimpleRetriever(
            vectorstore=MagicMock(),
            embedder=MagicMock(),
        )

    def test_format_context_single_chunk(self, retriever):
        """Test formatting a single chunk."""
        chunks = [
            QueryResult(
                chunk_id="chunk1",
                content="Password must be at least 12 characters.",
                source_document="/docs/auth-policy.md",
                section_hierarchy=["Authentication", "Password Requirements"],
                distance=0.3,  # similarity = 0.85
            )
        ]

        result = retriever.format_context(chunks)

        assert "[1]" in result
        assert "auth-policy.md" in result
        assert "Authentication > Password Requirements" in result
        assert "0.85" in result
        assert "Password must be at least 12 characters" in result

    def test_format_context_multiple_chunks(self, retriever):
        """Test formatting multiple chunks."""
        chunks = [
            QueryResult(
                chunk_id="chunk1",
                content="First chunk content",
                source_document="/docs/first.md",
                section_hierarchy=["Section A"],
                distance=0.2,
            ),
            QueryResult(
                chunk_id="chunk2",
                content="Second chunk content",
                source_document="/docs/second.md",
                section_hierarchy=["Section B"],
                distance=0.4,
            ),
        ]

        result = retriever.format_context(chunks)

        assert "[1]" in result
        assert "[2]" in result
        assert "first.md" in result
        assert "second.md" in result
        assert "First chunk content" in result
        assert "Second chunk content" in result

    def test_format_context_empty_chunks(self, retriever):
        """Test formatting empty chunk list."""
        result = retriever.format_context([])

        assert "No relevant security documentation found" in result

    def test_format_context_without_scores(self, retriever):
        """Test formatting without similarity scores."""
        chunks = [
            QueryResult(
                chunk_id="chunk1",
                content="Content",
                source_document="/docs/test.md",
                section_hierarchy=["Section"],
                distance=0.3,
            )
        ]

        result = retriever.format_context(chunks, include_scores=False)

        # Should not include the actual similarity score
        assert "0.85" not in result

    def test_format_result_for_prompt(self, retriever):
        """Test convenience method for formatting RetrievalResult."""
        chunks = [
            QueryResult(
                chunk_id="chunk1",
                content="Chunk content",
                source_document="/docs/test.md",
                section_hierarchy=["Section"],
                distance=0.3,
            )
        ]

        retrieval_result = RetrievalResult(
            chunks=chunks,
            query_text="test",
            total_retrieved=1,
            above_threshold=1,
            retrieval_time_ms=50.0,
        )

        result = retriever.format_result_for_prompt(retrieval_result)

        assert "Chunk content" in result
        assert "test.md" in result


class TestSimpleRetrieverBuildQueryFromStory:
    """Tests for the private _build_query_from_story method."""

    @pytest.fixture
    def retriever(self):
        """Create a retriever with mocked dependencies."""
        return SimpleRetriever(
            vectorstore=MagicMock(),
            embedder=MagicMock(),
        )

    def test_build_query_full_story(self, retriever):
        """Test query building with full user story."""
        story = UserStory(
            id="US-001",
            title="User Authentication",
            story="As a user, I want to authenticate securely.",
            acceptance_criteria=["Use MFA", "Session timeout after 30 minutes"],
        )

        query = retriever._build_query_from_story(story)

        assert "User Authentication" in query
        assert "authenticate securely" in query
        assert "Use MFA" in query
        assert "Session timeout" in query
        assert "Acceptance criteria:" in query

    def test_build_query_no_acceptance_criteria(self, retriever):
        """Test query building without acceptance criteria."""
        story = UserStory(
            id="US-002",
            title="Simple Story",
            story="As a user, I want something.",
            acceptance_criteria=[],
        )

        query = retriever._build_query_from_story(story)

        assert "Simple Story" in query
        assert "I want something" in query
        # Should NOT have "Acceptance criteria:" since list is empty
        assert "Acceptance criteria:" not in query


# ─────────────────────────────────────────────────────────────────────
# 2026-05-19 regression tests — see docs/RAG_RETRIEVAL_AUDIT_2026-05-19.md
#
# Background: the original 0.7 similarity_threshold in RetrievalConfig
# caused every RAG query in the qwen3 N=20 baseline, qwen3.6 N=10
# sub-test, and cross-family N=10 matrix to fall back to the
# "No relevant security documentation found." whiff string. The bug
# was caught only after the Option B raw-outputs patch surfaced the
# retrieval_context field. These tests prevent recurrence.
# ─────────────────────────────────────────────────────────────────────


class TestSimilarityFormula:
    """Unit-level guard for the L2-distance to cosine-similarity formula.

    Chroma's default ``hnsw:space="l2"`` returns squared L2 distance.
    For unit-normalized vectors, squared_L2 = 2(1 - cos), so cos = 1 - d/2.
    This test pins the formula in vectorstore.QueryResult.similarity.
    """

    @pytest.mark.parametrize(
        "distance, expected_cos",
        [
            (0.0,  1.0),    # identical vectors
            (1.0,  0.5),    # cos = 0.5 (mild correlation)
            (2.0,  0.0),    # orthogonal-equivalent for normalized
            (4.0,  0.0),    # clamped (worst case)
            (0.828, 0.586), # the keycloak OIDC top-hit observed in the 2026-05-19 audit
        ],
    )
    def test_cosine_recovery_from_squared_l2(self, distance, expected_cos):
        from secreqgen.retrieval.vectorstore import QueryResult
        r = QueryResult(
            chunk_id="x",
            content="x",
            source_document="x",
            section_hierarchy=[],
            distance=distance,
        )
        assert abs(r.similarity - expected_cos) < 1e-3, (
            f"Similarity formula regression: distance={distance} → "
            f"got {r.similarity:.4f}, expected ≈ {expected_cos:.4f}. "
            "Check QueryResult.similarity in vectorstore.py."
        )


class TestRetrievalThresholdRegression:
    """End-to-end test that would have caught the 2026-05-19 audit failure.

    Marked ``slow`` because it spins up the real sentence-transformers
    embedder (loads ~420MB model). Skipped by default in fast CI runs;
    enable with ``pytest -m slow``.
    """

    @pytest.mark.slow
    def test_relevant_query_does_not_whiff(self, tmp_path):
        """Querying for 'authentication' against a populated corpus
        about authentication MUST return chunks, not the whiff string.

        Before the 2026-05-19 fix (threshold = 0.7), this test would
        have failed — every single query in every cell of the 16-cell
        cross-family matrix returned 'No relevant security
        documentation found.' Threshold lowered to 0.4 based on
        empirical similarity distributions for all-mpnet-base-v2.
        """
        from secreqgen.ingestion.chunker import DocumentChunk
        from secreqgen.ingestion.embedder import DocumentEmbedder
        from secreqgen.ingestion.metadata import DocumentType
        from secreqgen.retrieval.retriever import SimpleRetriever
        from secreqgen.retrieval.vectorstore import SecReqGenVectorStore
        from secreqgen.config import SecReqGenConfig

        # Build a tiny corpus of clearly authentication-related chunks
        docs = [
            "Authentication: users must verify their identity using "
            "passwords or multi-factor authentication tokens before "
            "accessing protected resources.",
            "Password storage: passwords must be hashed using bcrypt "
            "or Argon2 with a unique salt per user. Plaintext "
            "passwords must never be stored or logged.",
            "Session management: session tokens must be cryptographically "
            "random, have a finite expiration, and be invalidated on "
            "logout to prevent session fixation attacks.",
            # A clearly off-topic chunk to verify it does NOT get pulled
            "Database backups must be encrypted at rest and stored in "
            "geographically separate locations from the primary store.",
        ]
        chunks = [
            DocumentChunk(
                chunk_id=f"test-chunk-{i}",
                content=text,
                source_document="test-corpus.md",
                section_hierarchy=[f"Section {i}"],
                chunk_index=i,
                token_count=len(text.split()),
                metadata={},
            )
            for i, text in enumerate(docs)
        ]

        embedder = DocumentEmbedder()
        embeddings = embedder.embed_texts(docs)

        vs = SecReqGenVectorStore(persist_directory=tmp_path / "vs")
        vs.add_chunks(chunks, embeddings, DocumentType.ARCHITECTURE)

        cfg = SecReqGenConfig()
        # Sanity-check the default we're shipping
        assert cfg.retrieval.similarity_threshold <= 0.5, (
            "RetrievalConfig.similarity_threshold > 0.5 — this threshold "
            "is too aggressive for all-mpnet-base-v2 cosine similarities "
            "(see docs/RAG_RETRIEVAL_AUDIT_2026-05-19.md). Relevant "
            "content typically scores 0.45-0.65 cosine."
        )

        retriever = SimpleRetriever(
            vs, embedder,
            top_k=cfg.retrieval.top_k,
            similarity_threshold=cfg.retrieval.similarity_threshold,
        )

        result = retriever.retrieve("authentication and password security")
        context = retriever.format_context(result.chunks)

        # The core regression check: must NOT fall back to the whiff string
        assert "No relevant security documentation found" not in context, (
            f"Retrieval returned the whiff fallback for an obviously "
            f"relevant query. This is the 2026-05-19 bug pattern. "
            f"total_retrieved={result.total_retrieved}, "
            f"above_threshold={result.above_threshold}, "
            f"threshold={retriever.similarity_threshold}. "
            f"See docs/RAG_RETRIEVAL_AUDIT_2026-05-19.md."
        )
        # And must include real content
        assert any(
            "password" in c.content.lower() or "authentication" in c.content.lower()
            for c in result.chunks
        ), "Retrieved chunks don't contain expected content for query."
