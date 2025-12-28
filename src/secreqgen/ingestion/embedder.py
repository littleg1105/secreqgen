"""Document embedding generation using sentence-transformers.

Generates dense vector embeddings for document chunks to enable
semantic similarity search in the vector store.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from secreqgen.ingestion.chunker import DocumentChunk


@dataclass
class EmbeddedChunk:
    """A document chunk with its embedding vector."""

    chunk: DocumentChunk
    embedding: np.ndarray

    def __post_init__(self) -> None:
        """Validate embedding is a numpy array."""
        if not isinstance(self.embedding, np.ndarray):
            raise TypeError("embedding must be a numpy ndarray")


class DocumentEmbedder:
    """Generates embeddings for document chunks using sentence-transformers.

    The embedder uses a pre-trained sentence transformer model to create
    dense vector representations of text. These embeddings capture semantic
    meaning and are used for similarity search in the vector store.

    Key behaviors:
    - Uses all-mpnet-base-v2 by default (768-dimensional embeddings)
    - Supports batch processing for efficiency
    - Chunks already include section hierarchy prefix from the chunker,
      so no additional preprocessing is needed
    - Thread-safe for concurrent embedding operations
    """

    def __init__(
        self,
        model_name: str = "all-mpnet-base-v2",
        device: str = "cpu",
        batch_size: int = 32,
    ) -> None:
        """Initialize the embedder.

        Args:
            model_name: Name of the sentence-transformers model to use.
                Default is "all-mpnet-base-v2" which produces 768-dim embeddings.
            device: Device to run the model on ("cpu", "cuda", "mps").
            batch_size: Number of texts to embed in a single batch.
        """
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model: Optional[SentenceTransformer] = None

    @property
    def model(self) -> SentenceTransformer:
        """Lazy-load the model on first access.

        This delays the potentially expensive model loading until
        embeddings are actually needed.
        """
        if self._model is None:
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    @property
    def embedding_dimension(self) -> int:
        """Return the embedding dimension of the model."""
        return self.model.get_sentence_embedding_dimension()

    def embed_text(self, text: str) -> np.ndarray:
        """Generate an embedding for a single piece of text.

        Args:
            text: The text to embed.

        Returns:
            A numpy array containing the embedding vector.
        """
        # Handle empty text
        if not text or not text.strip():
            return np.zeros(self.embedding_dimension, dtype=np.float32)

        embedding = self.model.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.array(embedding, dtype=np.float32)

    def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        """Generate embeddings for multiple texts in batches.

        Args:
            texts: List of texts to embed.

        Returns:
            List of numpy arrays containing embedding vectors.
        """
        if not texts:
            return []

        # Handle case where all texts are empty
        non_empty_mask = [bool(t and t.strip()) for t in texts]
        non_empty_texts = [t for t, is_valid in zip(texts, non_empty_mask) if is_valid]

        if not non_empty_texts:
            # All texts are empty
            dim = self.embedding_dimension
            return [np.zeros(dim, dtype=np.float32) for _ in texts]

        # Embed non-empty texts in batches
        embeddings = self.model.encode(
            non_empty_texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        # Reconstruct full list with zeros for empty texts
        result: list[np.ndarray] = []
        non_empty_idx = 0
        dim = self.embedding_dimension

        for is_valid in non_empty_mask:
            if is_valid:
                result.append(np.array(embeddings[non_empty_idx], dtype=np.float32))
                non_empty_idx += 1
            else:
                result.append(np.zeros(dim, dtype=np.float32))

        return result

    def embed_chunk(self, chunk: DocumentChunk) -> EmbeddedChunk:
        """Generate an embedding for a single document chunk.

        The chunk content already includes section hierarchy prefix
        (added by the chunker), which provides context for the embedding.

        Args:
            chunk: The document chunk to embed.

        Returns:
            An EmbeddedChunk containing the chunk and its embedding.
        """
        embedding = self.embed_text(chunk.content)
        return EmbeddedChunk(chunk=chunk, embedding=embedding)

    def embed_chunks(
        self, chunks: list[DocumentChunk]
    ) -> list[EmbeddedChunk]:
        """Generate embeddings for multiple document chunks.

        Processes chunks in batches for efficiency. The chunk content
        already includes section hierarchy prefix for better context.

        Args:
            chunks: List of document chunks to embed.

        Returns:
            List of EmbeddedChunk objects with embeddings.
        """
        if not chunks:
            return []

        # Extract texts from chunks
        texts = [chunk.content for chunk in chunks]

        # Embed all texts in batches
        embeddings = self.embed_texts(texts)

        # Pair chunks with their embeddings
        return [
            EmbeddedChunk(chunk=chunk, embedding=embedding)
            for chunk, embedding in zip(chunks, embeddings)
        ]

    def embed_chunks_with_arrays(
        self, chunks: list[DocumentChunk]
    ) -> list[tuple[DocumentChunk, np.ndarray]]:
        """Generate embeddings returning tuples instead of EmbeddedChunk objects.

        This method matches the signature in the phase specification
        for compatibility with existing code patterns.

        Args:
            chunks: List of document chunks to embed.

        Returns:
            List of (DocumentChunk, embedding) tuples.
        """
        embedded = self.embed_chunks(chunks)
        return [(e.chunk, e.embedding) for e in embedded]
