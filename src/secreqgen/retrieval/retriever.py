"""Simple semantic retriever for RAG pipeline.

Combines document embedder and vector store for semantic search,
with similarity threshold filtering and context formatting for LLM injection.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from secreqgen.generation.schemas import UserStory
from secreqgen.ingestion.embedder import DocumentEmbedder
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval.vectorstore import QueryResult, SecReqGenVectorStore


@dataclass
class RetrievalResult:
    """Result from a retrieval operation.

    Attributes:
        chunks: Retrieved chunks that passed the similarity threshold.
        query_text: The original query text.
        total_retrieved: Number of chunks retrieved before filtering.
        above_threshold: Number of chunks above similarity threshold.
        retrieval_time_ms: Time taken for retrieval in milliseconds.
    """

    chunks: list[QueryResult]
    query_text: str
    total_retrieved: int
    above_threshold: int
    retrieval_time_ms: float


class SimpleRetriever:
    """Simple semantic retriever for RAG pipeline.

    Combines the document embedder and vector store to provide semantic
    search with similarity threshold filtering. Designed for the minimum
    viable RAG pipeline - no re-ranking or query expansion.

    Example:
        >>> retriever = SimpleRetriever(vectorstore, embedder)
        >>> result = retriever.retrieve("authentication requirements")
        >>> context = retriever.format_context(result.chunks)
    """

    def __init__(
        self,
        vectorstore: SecReqGenVectorStore,
        embedder: DocumentEmbedder,
        top_k: int = 5,
        similarity_threshold: float = 0.7,
    ) -> None:
        """Initialize the retriever.

        Args:
            vectorstore: The vector store containing embedded documents.
            embedder: The embedder for generating query embeddings.
            top_k: Maximum number of results to retrieve (default: 5).
            similarity_threshold: Minimum similarity score [0-1] to include
                a result (default: 0.7). Lower = more results but less relevant.
        """
        self.vectorstore = vectorstore
        self.embedder = embedder
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold

    def retrieve(
        self,
        query: str,
        doc_types: Optional[list[DocumentType]] = None,
        top_k: Optional[int] = None,
    ) -> RetrievalResult:
        """Retrieve relevant chunks for a query.

        Args:
            query: The search query text.
            doc_types: Optional list of document types to search.
                If None, searches all collections except SYSTEM_CONTEXT.
            top_k: Override the default top_k value.

        Returns:
            RetrievalResult with matching chunks and metadata.
        """
        start_time = time.perf_counter()

        # Default to all non-system-context document types
        if doc_types is None:
            doc_types = [
                DocumentType.POLICY,
                DocumentType.ARCHITECTURE,
                DocumentType.STANDARD,
            ]

        k = top_k or self.top_k

        # Generate query embedding
        query_embedding = self.embedder.embed_text(query)

        # Query vector store
        results = self.vectorstore.query(
            query_embedding=query_embedding,
            doc_types=doc_types,
            n_results=k,
        )

        # Filter by similarity threshold
        filtered = [r for r in results if r.similarity >= self.similarity_threshold]

        retrieval_time_ms = (time.perf_counter() - start_time) * 1000

        return RetrievalResult(
            chunks=filtered,
            query_text=query,
            total_retrieved=len(results),
            above_threshold=len(filtered),
            retrieval_time_ms=retrieval_time_ms,
        )

    def retrieve_for_user_story(
        self,
        user_story: UserStory,
        top_k: Optional[int] = None,
    ) -> RetrievalResult:
        """Retrieve context relevant to a user story.

        Builds a query from the user story title, story text, and
        acceptance criteria, then retrieves relevant security documentation.

        Args:
            user_story: The user story to find relevant context for.
            top_k: Override the default top_k value.

        Returns:
            RetrievalResult with matching chunks and metadata.
        """
        query = self._build_query_from_story(user_story)
        return self.retrieve(query, top_k=top_k)

    def _build_query_from_story(self, user_story: UserStory) -> str:
        """Build a retrieval query from a user story.

        Combines the story title, text, and acceptance criteria into
        a single query string for semantic search.

        Args:
            user_story: The user story to build a query from.

        Returns:
            Query string for semantic search.
        """
        parts = [
            f"Security requirements for: {user_story.title}",
            user_story.story,
        ]

        if user_story.acceptance_criteria:
            parts.append("Acceptance criteria: " + "; ".join(user_story.acceptance_criteria))

        return " ".join(parts)

    def get_system_context(self) -> Optional[str]:
        """Retrieve the system context from the vector store.

        System context is stored in the SYSTEM_CONTEXT collection as a
        single chunk. This is injected into every generation prompt.

        Returns:
            The system context content, or None if not found.
        """
        # For system context, we don't need a real query embedding
        # since we're retrieving all content from a single-item collection.
        # We use a dummy embedding to satisfy the API.
        dummy_embedding = self.embedder.embed_text("system context")

        results = self.vectorstore.query(
            query_embedding=dummy_embedding,
            doc_types=[DocumentType.SYSTEM_CONTEXT],
            n_results=1,
        )

        if results:
            return results[0].content

        return None

    def format_context(
        self,
        chunks: list[QueryResult],
        include_scores: bool = True,
    ) -> str:
        """Format retrieved chunks for injection into prompts.

        Args:
            chunks: List of QueryResult objects to format.
            include_scores: Whether to include similarity scores.

        Returns:
            Formatted context string for prompt injection.
        """
        if not chunks:
            return "No relevant security documentation found."

        # Lazy import to avoid circular dependency
        from secreqgen.generation.prompts import format_retrieved_context

        return format_retrieved_context(
            chunks=[c.content for c in chunks],
            sources=[c.source_document for c in chunks],
            sections=[" > ".join(c.section_hierarchy) for c in chunks],
            scores=[c.similarity for c in chunks] if include_scores else [0.0] * len(chunks),
        )

    def format_result_for_prompt(self, result: RetrievalResult) -> str:
        """Format a retrieval result for injection into prompts.

        Convenience method that extracts chunks from a RetrievalResult
        and formats them.

        Args:
            result: The RetrievalResult to format.

        Returns:
            Formatted context string for prompt injection.
        """
        return self.format_context(result.chunks)
