"""ChromaDB vector store for SecReqGen.

Provides multi-collection storage organized by document type,
enabling efficient semantic search with metadata filtering.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import chromadb
import numpy as np
from chromadb.api.models.Collection import Collection

from secreqgen.exceptions import VectorStoreError
from secreqgen.ingestion.chunker import DocumentChunk
from secreqgen.ingestion.metadata import DocumentType


@dataclass
class QueryResult:
    """Result from a vector store query."""

    chunk_id: str
    content: str
    source_document: str
    section_hierarchy: list[str]
    distance: float
    metadata: dict = field(default_factory=dict)

    @property
    def similarity(self) -> float:
        """Convert distance to similarity score (1 - distance for L2).

        ChromaDB uses L2 distance by default. Smaller distance = more similar.
        For normalized vectors, L2 distance ranges from 0 (identical) to 2 (opposite).
        """
        # Clamp to [0, 2] and convert to [0, 1] similarity
        return max(0.0, 1.0 - (self.distance / 2.0))


class SecReqGenVectorStore:
    """Multi-collection vector store for different document types.

    Organizes documents into separate ChromaDB collections by type,
    enabling efficient filtered retrieval. Uses persistent storage
    to maintain the index across sessions.

    Collections:
    - secreqgen_policies: Security policy documents
    - secreqgen_architecture: Architecture documents
    - secreqgen_standards: Security standards (ASVS, CWE, etc.)
    - secreqgen_system_context: System context YAML files
    """

    # Mapping from DocumentType to collection name suffix
    COLLECTIONS = {
        DocumentType.POLICY: "policies",
        DocumentType.ARCHITECTURE: "architecture",
        DocumentType.STANDARD: "standards",
        DocumentType.SYSTEM_CONTEXT: "system_context",
    }

    def __init__(
        self,
        persist_directory: Optional[Path] = None,
        collection_prefix: str = "secreqgen",
    ) -> None:
        """Initialize the vector store.

        Args:
            persist_directory: Directory for ChromaDB persistence.
                If None, uses an ephemeral in-memory client (useful for tests).
            collection_prefix: Prefix for collection names (default: "secreqgen").
        """
        self.collection_prefix = collection_prefix
        self.persist_directory = persist_directory

        # Initialize ChromaDB client
        if persist_directory:
            persist_directory.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_directory))
        else:
            # In-memory client for tests
            self._client = chromadb.Client()

        # Cache for collection objects
        self._collections: dict[DocumentType, Collection] = {}

    def _get_collection_name(self, doc_type: DocumentType) -> str:
        """Get the full collection name for a document type.

        Args:
            doc_type: The document type.

        Returns:
            The full collection name with prefix.
        """
        suffix = self.COLLECTIONS.get(doc_type)
        if suffix is None:
            raise VectorStoreError(
                f"No collection mapping for document type: {doc_type}",
                details="Only POLICY, ARCHITECTURE, STANDARD, and SYSTEM_CONTEXT are supported.",
            )
        return f"{self.collection_prefix}_{suffix}"

    def _get_collection(self, doc_type: DocumentType) -> Collection:
        """Get or create a collection for a document type.

        Args:
            doc_type: The document type.

        Returns:
            The ChromaDB collection.
        """
        if doc_type not in self._collections:
            collection_name = self._get_collection_name(doc_type)
            self._collections[doc_type] = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "l2"},  # L2 distance for normalized vectors
            )
        return self._collections[doc_type]

    def add_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[np.ndarray],
        doc_type: DocumentType,
    ) -> int:
        """Add document chunks with embeddings to the appropriate collection.

        Args:
            chunks: List of document chunks to add.
            embeddings: List of embedding vectors (must match chunks length).
            doc_type: The document type determining which collection to use.

        Returns:
            Number of chunks added.

        Raises:
            VectorStoreError: If chunks and embeddings lengths don't match
                or if the document type is not supported.
        """
        if len(chunks) != len(embeddings):
            raise VectorStoreError(
                "Chunks and embeddings must have the same length",
                details=f"Got {len(chunks)} chunks and {len(embeddings)} embeddings.",
            )

        if not chunks:
            return 0

        collection = self._get_collection(doc_type)

        # Prepare data for ChromaDB
        ids = [chunk.chunk_id for chunk in chunks]
        documents = [chunk.content for chunk in chunks]
        embedding_lists = [emb.tolist() for emb in embeddings]

        # Build metadata for each chunk
        metadatas = []
        for chunk in chunks:
            metadata = {
                "source_document": chunk.source_document,
                "section_hierarchy": " > ".join(chunk.section_hierarchy),
                "chunk_index": chunk.chunk_index,
                "token_count": chunk.token_count,
                "document_type": doc_type.value,
            }
            # Include any additional metadata from the chunk
            if chunk.metadata:
                for key, value in chunk.metadata.items():
                    # ChromaDB metadata must be str, int, float, or bool
                    if isinstance(value, (str, int, float, bool)):
                        metadata[key] = value
                    elif isinstance(value, list):
                        # Convert lists to comma-separated strings
                        metadata[key] = ",".join(str(v) for v in value)
            metadatas.append(metadata)

        # Add to collection (upsert to handle duplicates)
        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embedding_lists,
            metadatas=metadatas,
        )

        return len(chunks)

    def query(
        self,
        query_embedding: np.ndarray,
        doc_types: Optional[list[DocumentType]] = None,
        n_results: int = 5,
        where: Optional[dict] = None,
    ) -> list[QueryResult]:
        """Query the vector store for similar documents.

        Args:
            query_embedding: The query embedding vector.
            doc_types: Optional list of document types to search.
                If None, searches all collections.
            n_results: Maximum number of results per collection.
            where: Optional ChromaDB where clause for metadata filtering.

        Returns:
            List of QueryResult objects sorted by distance (most similar first).
        """
        # Determine which collections to search
        if doc_types is None:
            search_types = list(self.COLLECTIONS.keys())
        else:
            search_types = [dt for dt in doc_types if dt in self.COLLECTIONS]

        if not search_types:
            return []

        all_results: list[QueryResult] = []
        query_list = query_embedding.tolist()

        for doc_type in search_types:
            collection = self._get_collection(doc_type)

            # Skip empty collections
            if collection.count() == 0:
                continue

            # Build query kwargs
            query_kwargs = {
                "query_embeddings": [query_list],
                "n_results": min(n_results, collection.count()),
                "include": ["documents", "metadatas", "distances"],
            }

            if where:
                query_kwargs["where"] = where

            # Execute query
            try:
                results = collection.query(**query_kwargs)
            except Exception as e:
                raise VectorStoreError(
                    f"Query failed for collection {doc_type.value}",
                    details=str(e),
                )

            # Process results
            if results and results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                    # Parse section hierarchy back from string
                    hierarchy_str = metadata.pop("section_hierarchy", "")
                    section_hierarchy = (
                        hierarchy_str.split(" > ") if hierarchy_str else []
                    )

                    all_results.append(
                        QueryResult(
                            chunk_id=chunk_id,
                            content=results["documents"][0][i],
                            source_document=metadata.pop("source_document", ""),
                            section_hierarchy=section_hierarchy,
                            distance=results["distances"][0][i],
                            metadata=metadata,
                        )
                    )

        # Sort by distance (ascending = most similar first)
        all_results.sort(key=lambda r: r.distance)

        # Return top n_results overall
        return all_results[:n_results]

    def get_stats(self) -> dict:
        """Get statistics about the vector store.

        Returns:
            Dictionary with collection statistics.
        """
        stats = {
            "total_chunks": 0,
            "collections": {},
        }

        for doc_type in self.COLLECTIONS.keys():
            collection_name = self._get_collection_name(doc_type)
            collection = self._get_collection(doc_type)
            count = collection.count()

            stats["collections"][doc_type.value] = {
                "name": collection_name,
                "count": count,
            }
            stats["total_chunks"] += count

        return stats

    def delete_collection(self, doc_type: DocumentType) -> bool:
        """Delete a collection for a document type.

        Args:
            doc_type: The document type whose collection to delete.

        Returns:
            True if collection was deleted, False if it didn't exist.
        """
        collection_name = self._get_collection_name(doc_type)

        try:
            self._client.delete_collection(collection_name)
            # Remove from cache
            self._collections.pop(doc_type, None)
            return True
        except ValueError:
            # Collection doesn't exist
            return False

    def clear(self) -> None:
        """Clear all collections (delete and recreate).

        Useful for testing and resetting the store.
        """
        for doc_type in list(self.COLLECTIONS.keys()):
            self.delete_collection(doc_type)
        self._collections.clear()

    def get_chunk_by_id(
        self, chunk_id: str, doc_type: Optional[DocumentType] = None
    ) -> Optional[QueryResult]:
        """Retrieve a specific chunk by its ID.

        Args:
            chunk_id: The chunk ID to retrieve.
            doc_type: Optional document type to narrow the search.
                If None, searches all collections.

        Returns:
            QueryResult if found, None otherwise.
        """
        search_types = [doc_type] if doc_type else list(self.COLLECTIONS.keys())

        for dt in search_types:
            if dt not in self.COLLECTIONS:
                continue

            collection = self._get_collection(dt)

            try:
                result = collection.get(
                    ids=[chunk_id],
                    include=["documents", "metadatas"],
                )

                if result["ids"]:
                    metadata = result["metadatas"][0] if result["metadatas"] else {}

                    # Parse section hierarchy
                    hierarchy_str = metadata.pop("section_hierarchy", "")
                    section_hierarchy = (
                        hierarchy_str.split(" > ") if hierarchy_str else []
                    )

                    return QueryResult(
                        chunk_id=chunk_id,
                        content=result["documents"][0],
                        source_document=metadata.pop("source_document", ""),
                        section_hierarchy=section_hierarchy,
                        distance=0.0,  # No distance for direct retrieval
                        metadata=metadata,
                    )
            except Exception:
                continue

        return None
