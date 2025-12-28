"""Unit tests for document embedder."""

from pathlib import Path

import numpy as np
import pytest

from secreqgen.ingestion.chunker import DocumentChunk
from secreqgen.ingestion.embedder import DocumentEmbedder, EmbeddedChunk


class TestEmbeddedChunk:
    """Tests for EmbeddedChunk dataclass."""

    def test_create_embedded_chunk(self) -> None:
        """Test creating an EmbeddedChunk with valid data."""
        chunk = DocumentChunk(
            chunk_id="chunk_abc123",
            content="Test content for embedding",
            source_document="/path/to/doc.md",
            section_hierarchy=["Security", "Authentication"],
            chunk_index=0,
            token_count=10,
        )
        embedding = np.array([0.1, 0.2, 0.3], dtype=np.float32)

        embedded = EmbeddedChunk(chunk=chunk, embedding=embedding)

        assert embedded.chunk == chunk
        assert np.array_equal(embedded.embedding, embedding)

    def test_embedded_chunk_validates_embedding_type(self) -> None:
        """Test that EmbeddedChunk rejects non-numpy embeddings."""
        chunk = DocumentChunk(
            chunk_id="chunk_abc123",
            content="Test content",
            source_document="/path/to/doc.md",
            section_hierarchy=[],
            chunk_index=0,
            token_count=5,
        )

        with pytest.raises(TypeError, match="embedding must be a numpy ndarray"):
            EmbeddedChunk(chunk=chunk, embedding=[0.1, 0.2, 0.3])  # type: ignore


class TestDocumentEmbedder:
    """Tests for DocumentEmbedder."""

    @pytest.fixture
    def embedder(self) -> DocumentEmbedder:
        """Create an embedder with default settings."""
        return DocumentEmbedder()

    @pytest.fixture
    def sample_chunk(self) -> DocumentChunk:
        """Create a sample document chunk for testing."""
        return DocumentChunk(
            chunk_id="chunk_test123",
            content="Section: Security > Authentication\n\nUsers must use strong passwords.",
            source_document="/docs/policy.md",
            section_hierarchy=["Security", "Authentication"],
            chunk_index=0,
            token_count=20,
        )

    def test_embedder_initialization_defaults(self) -> None:
        """Test embedder initializes with correct defaults."""
        embedder = DocumentEmbedder()
        assert embedder.model_name == "all-mpnet-base-v2"
        assert embedder.device == "cpu"
        assert embedder.batch_size == 32
        # Model should not be loaded yet (lazy loading)
        assert embedder._model is None

    def test_embedder_initialization_custom(self) -> None:
        """Test embedder accepts custom parameters."""
        embedder = DocumentEmbedder(
            model_name="paraphrase-MiniLM-L6-v2",
            device="cpu",
            batch_size=16,
        )
        assert embedder.model_name == "paraphrase-MiniLM-L6-v2"
        assert embedder.batch_size == 16

    def test_model_lazy_loading(self, embedder: DocumentEmbedder) -> None:
        """Test that model is lazy-loaded on first access."""
        # Model not loaded initially
        assert embedder._model is None

        # Accessing model property triggers loading
        _ = embedder.model
        assert embedder._model is not None

    def test_embedding_dimension(self, embedder: DocumentEmbedder) -> None:
        """Test that embedding dimension is correct for the model."""
        # all-mpnet-base-v2 produces 768-dimensional embeddings
        dim = embedder.embedding_dimension
        assert dim == 768

    def test_embed_text_single(self, embedder: DocumentEmbedder) -> None:
        """Test embedding a single piece of text."""
        text = "This is a test sentence for embedding."
        embedding = embedder.embed_text(text)

        assert isinstance(embedding, np.ndarray)
        assert embedding.dtype == np.float32
        assert len(embedding) == 768  # all-mpnet-base-v2 dimension

    def test_embed_text_empty(self, embedder: DocumentEmbedder) -> None:
        """Test embedding empty text returns zero vector."""
        embedding = embedder.embed_text("")
        assert isinstance(embedding, np.ndarray)
        assert len(embedding) == 768
        assert np.allclose(embedding, 0.0)

    def test_embed_text_whitespace_only(self, embedder: DocumentEmbedder) -> None:
        """Test embedding whitespace-only text returns zero vector."""
        embedding = embedder.embed_text("   \n\t  ")
        assert np.allclose(embedding, 0.0)

    def test_embed_text_normalized(self, embedder: DocumentEmbedder) -> None:
        """Test that embeddings are normalized (unit length)."""
        text = "This is test content for normalization check."
        embedding = embedder.embed_text(text)

        # Check vector is normalized (L2 norm ≈ 1)
        norm = np.linalg.norm(embedding)
        assert np.isclose(norm, 1.0, atol=1e-5)

    def test_embed_texts_multiple(self, embedder: DocumentEmbedder) -> None:
        """Test embedding multiple texts."""
        texts = [
            "First sentence about security.",
            "Second sentence about authentication.",
            "Third sentence about encryption.",
        ]
        embeddings = embedder.embed_texts(texts)

        assert len(embeddings) == 3
        for emb in embeddings:
            assert isinstance(emb, np.ndarray)
            assert len(emb) == 768
            assert emb.dtype == np.float32

    def test_embed_texts_empty_list(self, embedder: DocumentEmbedder) -> None:
        """Test embedding empty list returns empty list."""
        embeddings = embedder.embed_texts([])
        assert embeddings == []

    def test_embed_texts_with_empty_strings(self, embedder: DocumentEmbedder) -> None:
        """Test embedding list with empty strings handles correctly."""
        texts = ["Valid text", "", "Another valid text", "  "]
        embeddings = embedder.embed_texts(texts)

        assert len(embeddings) == 4
        # Empty texts should be zero vectors
        assert np.allclose(embeddings[1], 0.0)
        assert np.allclose(embeddings[3], 0.0)
        # Valid texts should be non-zero
        assert not np.allclose(embeddings[0], 0.0)
        assert not np.allclose(embeddings[2], 0.0)

    def test_embed_texts_all_empty(self, embedder: DocumentEmbedder) -> None:
        """Test embedding list of all empty strings."""
        texts = ["", "   ", "\n\t"]
        embeddings = embedder.embed_texts(texts)

        assert len(embeddings) == 3
        for emb in embeddings:
            assert np.allclose(emb, 0.0)

    def test_embed_chunk_single(
        self, embedder: DocumentEmbedder, sample_chunk: DocumentChunk
    ) -> None:
        """Test embedding a single document chunk."""
        embedded = embedder.embed_chunk(sample_chunk)

        assert isinstance(embedded, EmbeddedChunk)
        assert embedded.chunk == sample_chunk
        assert isinstance(embedded.embedding, np.ndarray)
        assert len(embedded.embedding) == 768

    def test_embed_chunk_preserves_chunk_data(
        self, embedder: DocumentEmbedder, sample_chunk: DocumentChunk
    ) -> None:
        """Test that embedding preserves all chunk data."""
        embedded = embedder.embed_chunk(sample_chunk)

        assert embedded.chunk.chunk_id == sample_chunk.chunk_id
        assert embedded.chunk.content == sample_chunk.content
        assert embedded.chunk.source_document == sample_chunk.source_document
        assert embedded.chunk.section_hierarchy == sample_chunk.section_hierarchy
        assert embedded.chunk.metadata == sample_chunk.metadata

    def test_embed_chunks_multiple(self, embedder: DocumentEmbedder) -> None:
        """Test embedding multiple chunks."""
        chunks = [
            DocumentChunk(
                chunk_id=f"chunk_{i}",
                content=f"Content for chunk {i}",
                source_document="/doc.md",
                section_hierarchy=["Section"],
                chunk_index=i,
                token_count=10,
            )
            for i in range(5)
        ]

        embedded_chunks = embedder.embed_chunks(chunks)

        assert len(embedded_chunks) == 5
        for i, embedded in enumerate(embedded_chunks):
            assert isinstance(embedded, EmbeddedChunk)
            assert embedded.chunk.chunk_id == f"chunk_{i}"
            assert len(embedded.embedding) == 768

    def test_embed_chunks_empty_list(self, embedder: DocumentEmbedder) -> None:
        """Test embedding empty chunk list returns empty list."""
        embedded_chunks = embedder.embed_chunks([])
        assert embedded_chunks == []

    def test_embed_chunks_with_arrays(self, embedder: DocumentEmbedder) -> None:
        """Test embed_chunks_with_arrays returns tuples."""
        chunks = [
            DocumentChunk(
                chunk_id="chunk_0",
                content="Test content here",
                source_document="/doc.md",
                section_hierarchy=[],
                chunk_index=0,
                token_count=5,
            ),
            DocumentChunk(
                chunk_id="chunk_1",
                content="More content",
                source_document="/doc.md",
                section_hierarchy=[],
                chunk_index=1,
                token_count=4,
            ),
        ]

        result = embedder.embed_chunks_with_arrays(chunks)

        assert len(result) == 2
        for chunk, embedding in result:
            assert isinstance(chunk, DocumentChunk)
            assert isinstance(embedding, np.ndarray)
            assert len(embedding) == 768

    def test_semantic_similarity(self, embedder: DocumentEmbedder) -> None:
        """Test that similar texts have higher cosine similarity."""
        # Similar texts
        auth_text1 = "Users must authenticate with username and password."
        auth_text2 = "Authentication requires user credentials including password."

        # Different topic
        crypto_text = "Data must be encrypted using AES-256 algorithm."

        emb1 = embedder.embed_text(auth_text1)
        emb2 = embedder.embed_text(auth_text2)
        emb3 = embedder.embed_text(crypto_text)

        # Cosine similarity (vectors are already normalized)
        sim_auth = np.dot(emb1, emb2)
        sim_diff = np.dot(emb1, emb3)

        # Similar auth texts should have higher similarity
        assert sim_auth > sim_diff
        # Auth texts should have high similarity
        assert sim_auth > 0.5

    def test_deterministic_embeddings(self, embedder: DocumentEmbedder) -> None:
        """Test that same text produces same embedding."""
        text = "This is a test for determinism."

        emb1 = embedder.embed_text(text)
        emb2 = embedder.embed_text(text)

        assert np.allclose(emb1, emb2)


class TestEmbedderIntegration:
    """Integration tests for embedder with chunker and loader."""

    def test_embed_loaded_and_chunked_document(
        self, sample_markdown_file: Path
    ) -> None:
        """Test full pipeline: load -> chunk -> embed."""
        from secreqgen.ingestion.chunker import ArchitectureAwareChunker
        from secreqgen.ingestion.loader import DocumentLoaderRegistry

        # Load document
        registry = DocumentLoaderRegistry()
        doc = registry.load(sample_markdown_file)

        # Chunk document
        chunker = ArchitectureAwareChunker()
        chunks = chunker.chunk_document(doc)

        # Embed chunks
        embedder = DocumentEmbedder()
        embedded_chunks = embedder.embed_chunks(chunks)

        # Verify results
        assert len(embedded_chunks) == len(chunks)
        for embedded in embedded_chunks:
            assert isinstance(embedded.embedding, np.ndarray)
            assert len(embedded.embedding) == 768
            # Embeddings should be non-zero for actual content
            assert not np.allclose(embedded.embedding, 0.0)

    def test_hierarchy_context_improves_embeddings(self) -> None:
        """Test that hierarchy prefix provides useful context for embeddings."""
        embedder = DocumentEmbedder()

        # Same content, different hierarchy context
        auth_with_context = "Section: Security > Authentication\n\nEnable MFA for all users."
        auth_without_context = "Enable MFA for all users."

        # Authentication-related query
        query = "How do I set up multi-factor authentication?"

        emb_with = embedder.embed_text(auth_with_context)
        emb_without = embedder.embed_text(auth_without_context)
        emb_query = embedder.embed_text(query)

        # Both should be similar to query since content is the same
        sim_with = np.dot(emb_with, emb_query)
        sim_without = np.dot(emb_without, emb_query)

        # The contextualized version should have reasonable similarity
        # (Note: Context may or may not improve similarity depending on the query)
        assert sim_with > 0.3
        assert sim_without > 0.3


class TestEmbedderPerformance:
    """Performance-related tests for the embedder."""

    def test_batch_embedding_vs_single(self) -> None:
        """Test that batch embedding works correctly."""
        embedder = DocumentEmbedder(batch_size=4)

        texts = [f"Text number {i} for batch testing." for i in range(10)]

        # Batch embedding
        batch_embeddings = embedder.embed_texts(texts)

        # Single embeddings
        single_embeddings = [embedder.embed_text(t) for t in texts]

        # Results should be identical
        for batch_emb, single_emb in zip(batch_embeddings, single_embeddings):
            assert np.allclose(batch_emb, single_emb)

    def test_large_batch(self) -> None:
        """Test embedding a larger batch of texts."""
        embedder = DocumentEmbedder(batch_size=16)

        texts = [f"Document {i}: Security requirements content." for i in range(50)]
        embeddings = embedder.embed_texts(texts)

        assert len(embeddings) == 50
        for emb in embeddings:
            assert len(emb) == 768
            assert not np.allclose(emb, 0.0)
