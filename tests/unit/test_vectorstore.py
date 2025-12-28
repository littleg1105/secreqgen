"""Unit tests for ChromaDB vector store."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

from secreqgen.exceptions import VectorStoreError
from secreqgen.ingestion.chunker import DocumentChunk
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval.vectorstore import QueryResult, SecReqGenVectorStore


class TestQueryResult:
    """Tests for QueryResult dataclass."""

    def test_create_query_result(self) -> None:
        """Test creating a QueryResult."""
        result = QueryResult(
            chunk_id="chunk_abc123",
            content="Test content",
            source_document="/path/to/doc.md",
            section_hierarchy=["Security", "Auth"],
            distance=0.5,
            metadata={"doc_type": "policy"},
        )
        assert result.chunk_id == "chunk_abc123"
        assert result.content == "Test content"
        assert result.section_hierarchy == ["Security", "Auth"]
        assert result.distance == 0.5

    def test_similarity_property(self) -> None:
        """Test that similarity is correctly computed from distance."""
        # Distance 0 = identical = similarity 1.0
        result1 = QueryResult(
            chunk_id="id1",
            content="",
            source_document="",
            section_hierarchy=[],
            distance=0.0,
        )
        assert result1.similarity == 1.0

        # Distance 2 = opposite vectors = similarity 0.0
        result2 = QueryResult(
            chunk_id="id2",
            content="",
            source_document="",
            section_hierarchy=[],
            distance=2.0,
        )
        assert result2.similarity == 0.0

        # Distance 1 = 90 degrees = similarity 0.5
        result3 = QueryResult(
            chunk_id="id3",
            content="",
            source_document="",
            section_hierarchy=[],
            distance=1.0,
        )
        assert result3.similarity == 0.5


class TestSecReqGenVectorStore:
    """Tests for SecReqGenVectorStore."""

    @pytest.fixture
    def store(self) -> SecReqGenVectorStore:
        """Create an in-memory vector store for testing."""
        store = SecReqGenVectorStore(persist_directory=None)
        # Clear any existing data for test isolation
        store.clear()
        return store

    @pytest.fixture
    def sample_chunks(self) -> list[DocumentChunk]:
        """Create sample document chunks."""
        return [
            DocumentChunk(
                chunk_id="chunk_001",
                content="Section: Security > Authentication\n\nUsers must authenticate.",
                source_document="/docs/policy.md",
                section_hierarchy=["Security", "Authentication"],
                chunk_index=0,
                token_count=15,
                metadata={"section_title": "Authentication"},
            ),
            DocumentChunk(
                chunk_id="chunk_002",
                content="Section: Security > Authorization\n\nRBAC must be implemented.",
                source_document="/docs/policy.md",
                section_hierarchy=["Security", "Authorization"],
                chunk_index=1,
                token_count=12,
                metadata={"section_title": "Authorization"},
            ),
            DocumentChunk(
                chunk_id="chunk_003",
                content="Section: Crypto\n\nData must be encrypted with AES-256.",
                source_document="/docs/standards.md",
                section_hierarchy=["Crypto"],
                chunk_index=0,
                token_count=18,
            ),
        ]

    @pytest.fixture
    def sample_embeddings(self) -> list[np.ndarray]:
        """Create sample embeddings (768-dimensional, normalized)."""
        embeddings = []
        for i in range(3):
            # Create random normalized vector
            vec = np.random.randn(768).astype(np.float32)
            vec = vec / np.linalg.norm(vec)
            embeddings.append(vec)
        return embeddings

    def test_store_initialization_in_memory(self) -> None:
        """Test in-memory store initialization."""
        store = SecReqGenVectorStore(persist_directory=None)
        assert store.collection_prefix == "secreqgen"
        assert store.persist_directory is None

    def test_store_initialization_persistent(self) -> None:
        """Test persistent store initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "vectordb"
            store = SecReqGenVectorStore(persist_directory=persist_path)

            assert store.persist_directory == persist_path
            assert persist_path.exists()

    def test_store_custom_prefix(self) -> None:
        """Test store with custom collection prefix."""
        store = SecReqGenVectorStore(
            persist_directory=None, collection_prefix="myproject"
        )
        assert store.collection_prefix == "myproject"

        # Check collection name generation
        name = store._get_collection_name(DocumentType.POLICY)
        assert name == "myproject_policies"

    def test_collection_name_mapping(self, store: SecReqGenVectorStore) -> None:
        """Test collection name generation for all document types."""
        expected = {
            DocumentType.POLICY: "secreqgen_policies",
            DocumentType.ARCHITECTURE: "secreqgen_architecture",
            DocumentType.STANDARD: "secreqgen_standards",
            DocumentType.SYSTEM_CONTEXT: "secreqgen_system_context",
        }

        for doc_type, expected_name in expected.items():
            assert store._get_collection_name(doc_type) == expected_name

    def test_collection_name_unknown_type_raises(
        self, store: SecReqGenVectorStore
    ) -> None:
        """Test that UNKNOWN document type raises error."""
        with pytest.raises(VectorStoreError, match="No collection mapping"):
            store._get_collection_name(DocumentType.UNKNOWN)

    def test_add_chunks_empty_list(self, store: SecReqGenVectorStore) -> None:
        """Test adding empty list returns 0."""
        count = store.add_chunks([], [], DocumentType.POLICY)
        assert count == 0

    def test_add_chunks_mismatched_lengths_raises(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test that mismatched chunks/embeddings lengths raise error."""
        with pytest.raises(VectorStoreError, match="same length"):
            store.add_chunks(sample_chunks[:2], sample_embeddings, DocumentType.POLICY)

    def test_add_chunks_success(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test successfully adding chunks."""
        count = store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)
        assert count == 3

        # Verify they're in the store
        stats = store.get_stats()
        assert stats["collections"]["policy"]["count"] == 3

    def test_add_chunks_upsert_duplicates(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test that adding same chunks again updates them (upsert)."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        # Should still have 3 (upsert, not duplicate)
        stats = store.get_stats()
        assert stats["collections"]["policy"]["count"] == 3

    def test_query_empty_store(self, store: SecReqGenVectorStore) -> None:
        """Test querying empty store returns empty list."""
        query_vec = np.random.randn(768).astype(np.float32)
        results = store.query(query_vec)
        assert results == []

    def test_query_basic(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test basic query returns results."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        # Query with the first embedding (should find itself)
        results = store.query(sample_embeddings[0], n_results=3)

        assert len(results) > 0
        # First result should be the chunk itself (distance ~0)
        assert results[0].chunk_id == "chunk_001"
        assert results[0].distance < 0.01

    def test_query_with_doc_type_filter(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test querying with document type filter."""
        # Add to different collections
        store.add_chunks(sample_chunks[:2], sample_embeddings[:2], DocumentType.POLICY)
        store.add_chunks(
            sample_chunks[2:], sample_embeddings[2:], DocumentType.STANDARD
        )

        # Query only policies
        results = store.query(
            sample_embeddings[0], doc_types=[DocumentType.POLICY], n_results=10
        )

        assert len(results) == 2
        for r in results:
            assert r.metadata.get("document_type") == "policy"

    def test_query_multiple_collections(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test querying across multiple collections."""
        store.add_chunks(sample_chunks[:1], sample_embeddings[:1], DocumentType.POLICY)
        store.add_chunks(
            sample_chunks[1:2], sample_embeddings[1:2], DocumentType.ARCHITECTURE
        )
        store.add_chunks(
            sample_chunks[2:], sample_embeddings[2:], DocumentType.STANDARD
        )

        # Query all collections
        results = store.query(sample_embeddings[0], n_results=10)

        assert len(results) == 3

    def test_query_n_results_limit(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test n_results limits output."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        results = store.query(sample_embeddings[0], n_results=1)
        assert len(results) == 1

    def test_query_results_sorted_by_distance(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test that results are sorted by distance (ascending)."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        results = store.query(sample_embeddings[0], n_results=3)

        # Check distances are in ascending order
        for i in range(len(results) - 1):
            assert results[i].distance <= results[i + 1].distance

    def test_query_preserves_section_hierarchy(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test that section hierarchy is preserved in results."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        results = store.query(sample_embeddings[0], n_results=1)

        assert results[0].section_hierarchy == ["Security", "Authentication"]

    def test_get_stats_empty_store(self, store: SecReqGenVectorStore) -> None:
        """Test stats on empty store."""
        stats = store.get_stats()

        assert stats["total_chunks"] == 0
        assert "policy" in stats["collections"]
        assert stats["collections"]["policy"]["count"] == 0

    def test_get_stats_with_data(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test stats with data in store."""
        store.add_chunks(sample_chunks[:2], sample_embeddings[:2], DocumentType.POLICY)
        store.add_chunks(
            sample_chunks[2:], sample_embeddings[2:], DocumentType.STANDARD
        )

        stats = store.get_stats()

        assert stats["total_chunks"] == 3
        assert stats["collections"]["policy"]["count"] == 2
        assert stats["collections"]["standard"]["count"] == 1

    def test_delete_collection(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test deleting a collection."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        # Verify data exists
        assert store.get_stats()["collections"]["policy"]["count"] == 3

        # Delete collection
        result = store.delete_collection(DocumentType.POLICY)
        assert result is True

        # Verify data is gone
        assert store.get_stats()["collections"]["policy"]["count"] == 0

    def test_delete_nonexistent_collection(
        self, store: SecReqGenVectorStore
    ) -> None:
        """Test deleting a collection that doesn't exist."""
        # First ensure collection exists by accessing it
        store._get_collection(DocumentType.POLICY)
        store.delete_collection(DocumentType.POLICY)

        # Deleting again should return False
        result = store.delete_collection(DocumentType.POLICY)
        assert result is False

    def test_clear(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test clearing all collections."""
        store.add_chunks(sample_chunks[:2], sample_embeddings[:2], DocumentType.POLICY)
        store.add_chunks(
            sample_chunks[2:], sample_embeddings[2:], DocumentType.STANDARD
        )

        # Verify data exists
        assert store.get_stats()["total_chunks"] == 3

        # Clear all
        store.clear()

        # Verify all data is gone
        assert store.get_stats()["total_chunks"] == 0

    def test_get_chunk_by_id_found(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test retrieving a chunk by ID."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        result = store.get_chunk_by_id("chunk_001")

        assert result is not None
        assert result.chunk_id == "chunk_001"
        assert "authenticate" in result.content.lower()
        assert result.source_document == "/docs/policy.md"

    def test_get_chunk_by_id_not_found(self, store: SecReqGenVectorStore) -> None:
        """Test retrieving a non-existent chunk returns None."""
        result = store.get_chunk_by_id("nonexistent_chunk")
        assert result is None

    def test_get_chunk_by_id_with_doc_type(
        self,
        store: SecReqGenVectorStore,
        sample_chunks: list[DocumentChunk],
        sample_embeddings: list[np.ndarray],
    ) -> None:
        """Test retrieving chunk by ID with doc_type filter."""
        store.add_chunks(sample_chunks, sample_embeddings, DocumentType.POLICY)

        # Should find when searching correct type
        result = store.get_chunk_by_id("chunk_001", doc_type=DocumentType.POLICY)
        assert result is not None

        # Should not find when searching wrong type
        result = store.get_chunk_by_id("chunk_001", doc_type=DocumentType.STANDARD)
        assert result is None


class TestVectorStorePersistence:
    """Tests for vector store persistence."""

    def test_persistence_across_instances(self) -> None:
        """Test that data persists across store instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            persist_path = Path(tmpdir) / "vectordb"

            # Create store and add data
            store1 = SecReqGenVectorStore(persist_directory=persist_path)
            chunk = DocumentChunk(
                chunk_id="persist_test",
                content="Persistent content",
                source_document="/test.md",
                section_hierarchy=["Test"],
                chunk_index=0,
                token_count=5,
            )
            embedding = np.random.randn(768).astype(np.float32)
            embedding = embedding / np.linalg.norm(embedding)

            store1.add_chunks([chunk], [embedding], DocumentType.POLICY)

            # Verify data exists
            assert store1.get_stats()["collections"]["policy"]["count"] == 1

            # Create new store instance pointing to same directory
            store2 = SecReqGenVectorStore(persist_directory=persist_path)

            # Data should still be there
            assert store2.get_stats()["collections"]["policy"]["count"] == 1

            # Should be able to query
            result = store2.get_chunk_by_id("persist_test")
            assert result is not None
            assert result.content == "Persistent content"


class TestVectorStoreIntegration:
    """Integration tests with embedder."""

    def test_full_pipeline(self, sample_markdown_file: Path) -> None:
        """Test full pipeline: load -> chunk -> embed -> store -> query."""
        from secreqgen.ingestion.chunker import ArchitectureAwareChunker
        from secreqgen.ingestion.embedder import DocumentEmbedder
        from secreqgen.ingestion.loader import DocumentLoaderRegistry

        # Load
        registry = DocumentLoaderRegistry()
        doc = registry.load(sample_markdown_file)

        # Chunk
        chunker = ArchitectureAwareChunker()
        chunks = chunker.chunk_document(doc)

        assert len(chunks) > 0

        # Embed
        embedder = DocumentEmbedder()
        embedded = embedder.embed_chunks(chunks)

        # Store (with fresh cleared store)
        store = SecReqGenVectorStore(persist_directory=None)
        store.clear()  # Ensure isolation
        embeddings = [e.embedding for e in embedded]
        store.add_chunks(chunks, embeddings, DocumentType.POLICY)

        # Verify stored
        stats = store.get_stats()
        assert stats["collections"]["policy"]["count"] == len(chunks)

        # Query with a related embedding
        query_text = "security authentication password"
        query_embedding = embedder.embed_text(query_text)
        results = store.query(query_embedding, n_results=3)

        assert len(results) > 0
        # Results should have content related to the document
        assert any("security" in r.content.lower() for r in results)

    def test_semantic_search_quality(self) -> None:
        """Test that semantic search returns relevant results."""
        from secreqgen.ingestion.embedder import DocumentEmbedder

        embedder = DocumentEmbedder()
        store = SecReqGenVectorStore(persist_directory=None)
        store.clear()  # Ensure isolation

        # Create chunks with distinct topics
        chunks = [
            DocumentChunk(
                chunk_id="auth_chunk",
                content="Users must authenticate with multi-factor authentication.",
                source_document="/auth.md",
                section_hierarchy=["Auth"],
                chunk_index=0,
                token_count=10,
            ),
            DocumentChunk(
                chunk_id="crypto_chunk",
                content="All data must be encrypted using AES-256 encryption.",
                source_document="/crypto.md",
                section_hierarchy=["Crypto"],
                chunk_index=0,
                token_count=10,
            ),
            DocumentChunk(
                chunk_id="network_chunk",
                content="Firewall rules must block unauthorized network traffic.",
                source_document="/network.md",
                section_hierarchy=["Network"],
                chunk_index=0,
                token_count=10,
            ),
        ]

        # Embed and store
        embedded = embedder.embed_chunks(chunks)
        embeddings = [e.embedding for e in embedded]
        store.add_chunks(chunks, embeddings, DocumentType.POLICY)

        # Query about authentication
        auth_query = embedder.embed_text("How do users log in? What about passwords?")
        results = store.query(auth_query, n_results=1)

        assert len(results) == 1
        assert results[0].chunk_id == "auth_chunk"

        # Query about encryption
        crypto_query = embedder.embed_text("How is data encrypted?")
        results = store.query(crypto_query, n_results=1)

        assert len(results) == 1
        assert results[0].chunk_id == "crypto_chunk"
