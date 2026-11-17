"""Builders for the embedder, the vector store and the retrievers.

Each function imports the domain classes inside its body. Importing this
module loads no heavy library.
"""

from __future__ import annotations

from pathlib import Path


def build_embedder(cfg, batch_size: int | None = None):
    """Build the document embedder.

    The batch size goes to the embedder only when it is given.
    """
    from secreqgen.ingestion import DocumentEmbedder

    if batch_size is None:
        return DocumentEmbedder(
            model_name=cfg.embedding.model_name,
            device=cfg.embedding.device,
        )
    return DocumentEmbedder(
        model_name=cfg.embedding.model_name,
        device=cfg.embedding.device,
        batch_size=batch_size,
    )


def build_vector_store(cfg):
    """Build the vector store from the settings."""
    from secreqgen.retrieval import SecReqGenVectorStore

    return SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )


def build_vector_retriever(cfg):
    """Build the dense retriever: the embedder first, then the store."""
    from secreqgen.retrieval import SimpleRetriever

    embedder = build_embedder(cfg)
    vectorstore = build_vector_store(cfg)
    return SimpleRetriever(
        vectorstore=vectorstore,
        embedder=embedder,
        top_k=cfg.retrieval.top_k,
        similarity_threshold=cfg.retrieval.similarity_threshold,
    )


def build_word_retriever(
    docs: Path, product: str | None, system_context_file: Path | None = None
):
    """Build the word search retriever over a documentation folder.

    Each call builds a new index. There is no cache.
    """
    from secreqgen.retrieval.word_search import WordSearchRetriever

    return WordSearchRetriever(
        docs, product=product, system_context_file=system_context_file
    )
