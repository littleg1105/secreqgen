"""Semantic similarity matcher for evaluation.

Compares generated security requirements against ground truth using
SBERT embeddings and cosine similarity for the thesis evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from secreqgen.generation.schemas import SecurityRequirement


class MatchType(str, Enum):
    """Classification of requirement match quality."""

    MATCH = "match"  # Strong match (similarity >= match_threshold)
    PARTIAL = "partial"  # Partial match (partial_threshold <= sim < match_threshold)
    NO_MATCH = "no_match"  # No match (similarity < partial_threshold)


@dataclass
class MatchResult:
    """Result of matching a generated requirement to ground truth.

    Attributes:
        generated_id: ID of the generated requirement.
        generated_text: Combined title + description text used for matching.
        ground_truth_id: ID of the matched ground truth (None if no match).
        ground_truth_text: Combined GT text (None if no match).
        similarity_score: Cosine similarity score [0, 1].
        match_type: Classification of match quality.
    """

    generated_id: str
    generated_text: str
    ground_truth_id: Optional[str]
    ground_truth_text: Optional[str]
    similarity_score: float
    match_type: MatchType

    @property
    def is_match(self) -> bool:
        """True if this is a strong match (True Positive)."""
        return self.match_type == MatchType.MATCH

    @property
    def is_partial(self) -> bool:
        """True if this is a partial match (flagged for review)."""
        return self.match_type == MatchType.PARTIAL

    @property
    def is_hallucination(self) -> bool:
        """True if generated requirement has no ground truth match (False Positive)."""
        return self.match_type == MatchType.NO_MATCH


@dataclass
class GroundTruthRequirement:
    """A ground truth security requirement for evaluation.

    This is a simplified structure for ground truth data loaded
    from JSON files. Unlike generated SecurityRequirement, it only
    needs the fields relevant for semantic comparison.

    Attributes:
        id: Unique identifier for the ground truth requirement.
        title: Brief title of the requirement.
        description: Detailed description of the requirement.
        asvs_category: Optional ASVS category for additional validation.
    """

    id: str
    title: str
    description: str
    asvs_category: Optional[str] = None

    @property
    def text_for_matching(self) -> str:
        """Combined text used for semantic similarity matching."""
        return f"{self.title}. {self.description}"


class SemanticMatcher:
    """Matches generated requirements to ground truth using semantic similarity.

    Uses sentence-transformer embeddings and cosine similarity to find
    the best match between generated requirements and ground truth.
    Implements greedy matching where each generated requirement is paired
    with the best available (unmatched) ground truth requirement.

    Thresholds:
        - match_threshold (default 0.85): Similarity >= this is a "match" (True Positive)
        - partial_threshold (default 0.70): Similarity in [0.70, 0.85) is "partial"
        - Below partial_threshold: "no_match" (False Positive / hallucination)

    Example:
        >>> matcher = SemanticMatcher()
        >>> matches = matcher.match_requirements(generated_reqs, ground_truth)
        >>> for m in matches:
        ...     print(f"{m.generated_id} -> {m.ground_truth_id}: {m.match_type}")
    """

    def __init__(
        self,
        match_threshold: float = 0.85,
        partial_threshold: float = 0.70,
        embedder: Optional[object] = None,
    ) -> None:
        """Initialize the semantic matcher.

        Args:
            match_threshold: Minimum similarity for a "match" (True Positive).
                Default is 0.85 per thesis spec.
            partial_threshold: Minimum similarity for a "partial" match.
                Default is 0.70 per thesis spec.
            embedder: Optional DocumentEmbedder instance. If None, creates
                a new one with default settings on first use.
        """
        if partial_threshold > match_threshold:
            raise ValueError(
                f"partial_threshold ({partial_threshold}) must be <= "
                f"match_threshold ({match_threshold})"
            )

        self.match_threshold = match_threshold
        self.partial_threshold = partial_threshold
        self._embedder = embedder
        self._embeddings_cache: dict[str, np.ndarray] = {}

    @property
    def embedder(self):
        """Lazy-load the embedder on first access."""
        if self._embedder is None:
            # Import here to avoid circular dependency and speed up module load
            from secreqgen.ingestion.embedder import DocumentEmbedder

            self._embedder = DocumentEmbedder()
        return self._embedder

    def compute_similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts.

        Uses SBERT embeddings (all-mpnet-base-v2 by default) for
        semantic similarity. Embeddings are normalized, so cosine
        similarity is computed via dot product.

        Args:
            text1: First text to compare.
            text2: Second text to compare.

        Returns:
            Cosine similarity score in range [0, 1].
            Higher = more similar. 1.0 = identical meaning.
        """
        emb1 = self._get_embedding(text1)
        emb2 = self._get_embedding(text2)

        # Embeddings are normalized, so dot product = cosine similarity
        similarity = float(np.dot(emb1, emb2))

        # Clamp to [0, 1] to handle floating point imprecision
        return max(0.0, min(1.0, similarity))

    def _get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text, using cache if available."""
        if text not in self._embeddings_cache:
            self._embeddings_cache[text] = self.embedder.embed_text(text)
        return self._embeddings_cache[text]

    def _embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts efficiently."""
        # Find which texts need embedding
        uncached = [t for t in texts if t not in self._embeddings_cache]

        if uncached:
            embeddings = self.embedder.embed_texts(uncached)
            for text, emb in zip(uncached, embeddings):
                self._embeddings_cache[text] = emb

        return [self._embeddings_cache[t] for t in texts]

    def _classify_match(self, similarity: float) -> MatchType:
        """Classify a similarity score into a match type."""
        if similarity >= self.match_threshold:
            return MatchType.MATCH
        elif similarity >= self.partial_threshold:
            return MatchType.PARTIAL
        else:
            return MatchType.NO_MATCH

    def match_requirements(
        self,
        generated: list[SecurityRequirement],
        ground_truth: list[GroundTruthRequirement],
    ) -> list[MatchResult]:
        """Match generated requirements to ground truth.

        Uses greedy matching: for each generated requirement, find the
        best matching ground truth that hasn't been matched yet. This
        ensures 1:1 matching and prevents a single GT from being matched
        multiple times.

        Args:
            generated: List of generated SecurityRequirement objects.
            ground_truth: List of ground truth requirements.

        Returns:
            List of MatchResult objects, one per generated requirement.
        """
        if not generated:
            return []

        # Extract text for matching
        gen_texts = [self._requirement_to_text(req) for req in generated]
        gt_texts = [gt.text_for_matching for gt in ground_truth]

        # Pre-compute all embeddings for efficiency
        all_texts = gen_texts + gt_texts
        self._embed_batch(all_texts)

        # Compute similarity matrix (generated x ground_truth)
        gen_embeddings = [self._embeddings_cache[t] for t in gen_texts]
        gt_embeddings = [self._embeddings_cache[t] for t in gt_texts]

        if not gt_embeddings:
            # No ground truth - all generated are hallucinations
            return [
                MatchResult(
                    generated_id=req.id,
                    generated_text=gen_texts[i],
                    ground_truth_id=None,
                    ground_truth_text=None,
                    similarity_score=0.0,
                    match_type=MatchType.NO_MATCH,
                )
                for i, req in enumerate(generated)
            ]

        # Build similarity matrix
        similarity_matrix = np.zeros((len(generated), len(ground_truth)))
        for i, gen_emb in enumerate(gen_embeddings):
            for j, gt_emb in enumerate(gt_embeddings):
                similarity_matrix[i, j] = float(np.dot(gen_emb, gt_emb))

        # Greedy matching: assign each generated to best available GT
        results = []
        matched_gt_indices: set[int] = set()

        for i, req in enumerate(generated):
            best_gt_idx = None
            best_similarity = -1.0

            for j in range(len(ground_truth)):
                if j not in matched_gt_indices:
                    sim = similarity_matrix[i, j]
                    if sim > best_similarity:
                        best_similarity = sim
                        best_gt_idx = j

            # Classify the match
            match_type = self._classify_match(best_similarity)

            if best_gt_idx is not None and match_type in (
                MatchType.MATCH,
                MatchType.PARTIAL,
            ):
                # Mark GT as matched (prevent reuse)
                matched_gt_indices.add(best_gt_idx)
                gt = ground_truth[best_gt_idx]
                results.append(
                    MatchResult(
                        generated_id=req.id,
                        generated_text=gen_texts[i],
                        ground_truth_id=gt.id,
                        ground_truth_text=gt.text_for_matching,
                        similarity_score=best_similarity,
                        match_type=match_type,
                    )
                )
            else:
                # No match above threshold
                results.append(
                    MatchResult(
                        generated_id=req.id,
                        generated_text=gen_texts[i],
                        ground_truth_id=None,
                        ground_truth_text=None,
                        similarity_score=best_similarity if best_gt_idx is not None else 0.0,
                        match_type=MatchType.NO_MATCH,
                    )
                )

        return results

    def _requirement_to_text(self, req: SecurityRequirement) -> str:
        """Convert a SecurityRequirement to text for matching.

        Per thesis spec, combines title and description for semantic comparison.
        """
        return f"{req.title}. {req.description}"

    def clear_cache(self) -> None:
        """Clear the embeddings cache."""
        self._embeddings_cache.clear()


def load_ground_truth(data: list[dict]) -> list[GroundTruthRequirement]:
    """Load ground truth requirements from parsed JSON/YAML data.

    Expected format (per thesis spec):
    [
        {
            "id": "GT-001",
            "title": "File Upload Validation",
            "description": "Validate file type using magic bytes...",
            "asvs_category": "V12 - Files and Resources"  # optional
        },
        ...
    ]

    Args:
        data: List of dictionaries with requirement data.

    Returns:
        List of GroundTruthRequirement objects.
    """
    results = []
    for i, item in enumerate(data):
        gt = GroundTruthRequirement(
            id=item.get("id", f"GT-{i+1:03d}"),
            title=item.get("title", ""),
            description=item.get("description", ""),
            asvs_category=item.get("asvs_category"),
        )
        results.append(gt)
    return results
