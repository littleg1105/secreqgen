"""Cross-encoder reranking for two-stage retrieval (2026-05-19).

The dense bi-encoder (all-mpnet-base-v2 + cosine over Chroma) embeds the
query and each chunk independently. That's fast but loses query-chunk
interaction information, so the top-1 chunk it returns is often "in the
neighbourhood" rather than "directly relevant". This module adds a
second stage: a cross-encoder scores (query, chunk) PAIRS jointly,
producing a relevance score that captures fine-grained interactions.

BEIR (Thakur et al., NeurIPS 2021) reports +3-10 nDCG@10 points from
adding a cross-encoder rerank stage over dense retrieval alone. We use
``cross-encoder/ms-marco-MiniLM-L-6-v2`` (~22M params, ~5-15 ms/pair on
CPU) which is the standard lightweight choice; ``BAAI/bge-reranker-v2-m3``
is the heavier, slightly higher-quality option but ~10x slower on CPU.

See docs/RAG_RETRIEVAL_AUDIT_2026-05-19.md §"Phase 4 — Cross-Encoder
Reranking" for the motivation and the empirical comparison.
"""

from __future__ import annotations

import logging
from typing import Optional

from sentence_transformers import CrossEncoder

from secreqgen.retrieval.vectorstore import QueryResult

logger = logging.getLogger(__name__)


# Default model: lightweight, well-trained on MS-MARCO passage ranking.
# Suitable for CPU inference, ~5-15 ms/pair on Apple Silicon.
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    """Reranks dense-retrieval candidates using a cross-encoder.

    Usage::

        reranker = CrossEncoderReranker()
        # candidates: list[QueryResult] from SimpleRetriever (e.g., top-30)
        top_k = reranker.rerank(query="LDAP authentication setup",
                                candidates=candidates,
                                top_k=5)
    """

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        device: Optional[str] = None,
    ) -> None:
        """Initialize the reranker.

        Args:
            model_name: HuggingFace cross-encoder model identifier.
            device: ``"cpu"``, ``"mps"``, ``"cuda"``, or ``None`` for auto.
                On Apple Silicon, MPS works but MiniLM is fast enough on CPU
                that the data-transfer overhead often makes CPU faster for
                batches < ~100. Default ``None`` lets sentence-transformers
                choose.
        """
        self.model_name = model_name
        self._device = device
        self._model: Optional[CrossEncoder] = None  # lazy-load

    @property
    def model(self) -> CrossEncoder:
        """Lazy-load the cross-encoder (downloading on first use)."""
        if self._model is None:
            logger.info("Loading cross-encoder %s", self.model_name)
            self._model = CrossEncoder(self.model_name, device=self._device)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: list[QueryResult],
        top_k: Optional[int] = None,
    ) -> list[QueryResult]:
        """Rerank dense-retrieval candidates by cross-encoder score.

        The original ``QueryResult.distance`` (and derived ``similarity``)
        is preserved as the dense-bi-encoder score so consumers can see
        both signals. The cross-encoder score is attached to the
        ``metadata["rerank_score"]`` field, and the returned list is
        sorted by that score (descending — higher = more relevant for
        the ms-marco cross-encoder convention).

        Args:
            query: The original query text (verbatim, no expansion).
            candidates: List of QueryResult from a dense retriever.
                Typically a larger top-N (e.g., 30) than the final
                desired top-K.
            top_k: Return only the top-K after reranking. None = return
                all reranked.

        Returns:
            Reranked list of QueryResult, sorted by cross-encoder score
            descending. ``metadata["rerank_score"]`` is set on each.
            ``metadata["dense_rank"]`` records the original dense rank
            (1-indexed) so we can quantify how much the rerank moved
            things.
        """
        if not candidates:
            return []

        # Tag each candidate with its original (dense) rank before reranking
        for dense_rank, c in enumerate(candidates, 1):
            c.metadata["dense_rank"] = dense_rank

        # Build (query, chunk_content) pairs for the cross-encoder
        pairs = [(query, c.content) for c in candidates]
        scores = self.model.predict(pairs)

        # Attach scores + sort
        for c, s in zip(candidates, scores):
            c.metadata["rerank_score"] = float(s)
        reranked = sorted(candidates, key=lambda r: -r.metadata["rerank_score"])

        if top_k is not None:
            reranked = reranked[:top_k]
        return reranked
