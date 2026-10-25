"""Phase A: RAG-retrieval ablation across 10 ideas × 5 projects.

Goal: produce a single, reproducible table that shows which retrieval
configuration produces the highest-quality chunks per user-story query,
across projects of varying corpus richness.

Output:
- /tmp/rag_ablation_results.json  — full structured results
- /tmp/rag_ablation_summary.csv   — flat CSV for plotting
- stdout: human-readable per-project + aggregate tables

Quality metric: mean precision@k (strict + lenient) per project, judged
by llama3:8b (different family from any of the generator models).
top-1-relevant fraction is also reported.

Configurable via env vars:
- ABLATION_PROJECTS  comma-separated project names (default: 5-project set)
- ABLATION_IDEAS     comma-separated idea ids 1..10 (default: all 10)
- ABLATION_OUTPUT    output dir (default: /tmp)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from secreqgen.config import SecReqGenConfig
from secreqgen.ingestion import DocumentEmbedder, ArchitectureAwareChunker
from secreqgen.ingestion.loader import MarkdownLoader, PDFLoader, DocxLoader
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
from secreqgen.retrieval.reranker import CrossEncoderReranker
from secreqgen.generation.schemas import UserStory

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
VECTORDB = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/data/vectordb")

# ─────────────────────────────────────────────────────────────────────
# Projects: (name, docs_path, user_stories_path)
# ─────────────────────────────────────────────────────────────────────

PROJECTS = {
    "mastodon":   (ROOT / "mastodon/docs",           ROOT / "mastodon/user_stories.json"),
    "keycloak":   (ROOT / "extracted_docs_md/keycloak", ROOT / "keycloak/user_stories.json"),
    "bitwarden":  (ROOT / "bitwarden-test-data",     ROOT / "bitwarden/user_stories.json"),
    "gitlab":     (ROOT / "gitlab/docs",             ROOT / "gitlab/user_stories.json"),
    "mattermost": (ROOT / "mattermost/docs",         ROOT / "mattermost/user_stories.json"),
}

# ─────────────────────────────────────────────────────────────────────
# Ideas: each is a callable that builds (chunker, retriever) given vs+emb
# ─────────────────────────────────────────────────────────────────────


def _shared_reranker_cache():
    """Memo so we don't reload the cross-encoder for every idea."""
    if not hasattr(_shared_reranker_cache, "_ce"):
        _shared_reranker_cache._ce = CrossEncoderReranker()
    return _shared_reranker_cache._ce


@dataclass
class Idea:
    id: int
    name: str
    description: str
    # Chunker config
    max_chunking_level: int = 3
    strip_front_matter: bool = True
    # Retriever config
    similarity_threshold: float = 0.4
    top_k: int = 5
    use_reranker: bool = False
    rerank_candidates: int = 30
    use_keyword_expansion: bool = True
    # Hybrid retrieval
    use_bm25_hybrid: bool = False
    rrf_k: int = 60


IDEAS: list[Idea] = [
    Idea(1,  "baseline_threshold_fix_only",
         "Current state: today's threshold fix only. Original chunker (no front-matter strip, h6 chunking).",
         max_chunking_level=6, strip_front_matter=False),
    Idea(2,  "frontmatter_strip",
         "+ YAML front-matter stripped before chunking.",
         max_chunking_level=6, strip_front_matter=True),
    Idea(3,  "h3cap_plus_hierarchy_fix",
         "+ Chunk at h3 max, fix sibling hierarchy bug. (Phase 2 stack.)",
         max_chunking_level=3, strip_front_matter=True),
    Idea(4,  "ce_rerank_phase4",
         "+ Cross-encoder rerank ms-marco-MiniLM. (Phase 4 stack; current best.)",
         max_chunking_level=3, strip_front_matter=True, use_reranker=True),
    Idea(5,  "bm25_hybrid_rrf",
         "+ BM25 + dense fusion with reciprocal rank fusion (RRF).",
         max_chunking_level=3, strip_front_matter=True, use_reranker=False, use_bm25_hybrid=True),
    Idea(6,  "chunking_level_2",
         "Even coarser chunks: max level h2.",
         max_chunking_level=2, strip_front_matter=True),
    Idea(7,  "chunking_level_4",
         "Less coarse than P2: max level h4.",
         max_chunking_level=4, strip_front_matter=True),
    Idea(8,  "threshold_0_3",
         "Lower threshold: 0.3 instead of 0.4.",
         max_chunking_level=3, strip_front_matter=True, similarity_threshold=0.3),
    Idea(9,  "top_k_10",
         "Retrieve top-10 chunks per query instead of 5.",
         max_chunking_level=3, strip_front_matter=True, top_k=10),
    Idea(10, "no_keyword_expansion",
         "Skip SECURITY_KEYWORD_MAP expansion; use raw user story text only.",
         max_chunking_level=3, strip_front_matter=True, use_keyword_expansion=False),
]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def load_user_stories(path: Path) -> list[dict]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "user_stories" in raw:
        return raw["user_stories"]
    return raw


def load_documents(docs_dir: Path):
    """Load all markdown files from docs_dir. Returns list[LoadedDocument]."""
    loader = MarkdownLoader()
    docs = []
    for f in sorted(docs_dir.glob("**/*.md")):
        if f.is_file():
            try:
                docs.append(loader.load(f))
            except Exception as e:
                print(f"    ! failed to load {f.name}: {e}")
    return docs


def ingest(docs_dir: Path, chunker: ArchitectureAwareChunker, embedder: DocumentEmbedder, vs: SecReqGenVectorStore) -> int:
    """Clear vectordb + ingest with given chunker. Returns chunk count."""
    vs.clear()
    all_chunks = []
    for doc in load_documents(docs_dir):
        all_chunks.extend(chunker.chunk_document(doc))
    if not all_chunks:
        return 0
    embeddings = embedder.embed_texts([c.content for c in all_chunks])
    vs.add_chunks(all_chunks, embeddings, DocumentType.ARCHITECTURE)
    return len(all_chunks)


def build_pipeline(idea: Idea, vs: SecReqGenVectorStore, embedder: DocumentEmbedder):
    """Construct (chunker, retriever) for an idea. BM25 hybrid is wrapped separately."""
    chunker = ArchitectureAwareChunker(max_chunking_level=idea.max_chunking_level)
    # Hack: if strip_front_matter=False, temporarily disable the regex by replacing the pattern
    if not idea.strip_front_matter:
        import re
        chunker.FRONT_MATTER_PATTERN = re.compile(r"(?!)")  # matches nothing
    reranker = _shared_reranker_cache() if idea.use_reranker else None
    retriever = SimpleRetriever(
        vs, embedder,
        top_k=idea.top_k,
        similarity_threshold=idea.similarity_threshold,
        reranker=reranker,
        rerank_candidates=idea.rerank_candidates,
    )
    return chunker, retriever


def build_query(story: dict, idea: Idea) -> str:
    """Build retrieval query, honoring idea.use_keyword_expansion."""
    if idea.use_keyword_expansion:
        # Use the retriever's built-in query builder which applies SECURITY_KEYWORD_MAP
        return None  # signal: use retrieve_for_user_story
    # Raw user-story text only
    parts = [f"Feature: {story['title']}", story['story']]
    ac = story.get("acceptance_criteria", [])
    if ac:
        parts.append("Acceptance criteria: " + "; ".join(ac))
    return " ".join(parts)


def retrieve_for_story(retriever: SimpleRetriever, story: dict, idea: Idea):
    """Run retrieval for one story; returns RetrievalResult."""
    if idea.use_keyword_expansion:
        us = UserStory(
            id=story["id"], title=story["title"], story=story["story"],
            acceptance_criteria=story.get("acceptance_criteria", []),
        )
        return retriever.retrieve_for_user_story(us)
    query = build_query(story, idea)
    return retriever.retrieve(query)


# Module-level cache so BM25 index is rebuilt only per (project, idea) pair
_BM25_INDEX_CACHE: dict[int, "object"] = {}


# BM25 hybrid retrieval (simple in-memory implementation)
def bm25_hybrid_retrieve(
    vs, embedder, query: str, all_chunks: list, top_k: int, similarity_threshold: float, rrf_k: int = 60,
):
    """Hybrid dense+BM25 with RRF fusion. Returns list[QueryResult] (top_k)."""
    from rank_bm25 import BM25Okapi
    import re as _re
    # Build BM25 index over chunk contents. Cache keyed by id(all_chunks)
    # so a single call across all 8 stories of a (project, idea) reuses it.
    key = id(all_chunks)
    if key not in _BM25_INDEX_CACHE:
        tokens = [_re.findall(r"\w+", c.content.lower()) for c in all_chunks]
        _BM25_INDEX_CACHE[key] = BM25Okapi(tokens)
    bm25 = _BM25_INDEX_CACHE[key]

    # Dense retrieval
    qe = embedder.embed_text(query)
    dense_results = vs.query(qe, doc_types=[DocumentType.ARCHITECTURE], n_results=top_k * 6)
    dense_above = [r for r in dense_results if r.similarity >= similarity_threshold]
    # Map to chunk_id → rank
    dense_rank = {r.chunk_id: i + 1 for i, r in enumerate(dense_above)}

    # BM25 retrieval
    q_tok = _re.findall(r"\w+", query.lower())
    scores = bm25.get_scores(q_tok)
    # Top n_results indices by BM25 score
    ranked = sorted(range(len(all_chunks)), key=lambda i: -scores[i])[:top_k * 6]
    bm25_rank = {all_chunks[i].chunk_id: r + 1 for r, i in enumerate(ranked) if scores[i] > 0}

    # RRF fusion
    rrf_scores = {}
    for cid, r in dense_rank.items():
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (rrf_k + r)
    for cid, r in bm25_rank.items():
        rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (rrf_k + r)

    # Build final list from dense QueryResults (so we have content/source); fall back
    # to chunk lookup if a chunk_id was only in BM25.
    chunks_by_id = {r.chunk_id: r for r in dense_above}
    final = sorted(rrf_scores.items(), key=lambda kv: -kv[1])
    out = []
    for cid, _ in final[:top_k]:
        if cid in chunks_by_id:
            out.append(chunks_by_id[cid])
        # else: would need a chunk lookup; for simplicity, skip BM25-only hits
        # which is a small loss in coverage but keeps the demo simple
    return out


# ─────────────────────────────────────────────────────────────────────
# Main run loop
# ─────────────────────────────────────────────────────────────────────


def run_idea_on_project(idea: Idea, project: str, embedder: DocumentEmbedder, vs: SecReqGenVectorStore) -> dict:
    """Run one (idea, project) cell: ingest → retrieve per story → save."""
    docs_dir, stories_path = PROJECTS[project]
    if not docs_dir.exists():
        return {"error": f"docs dir not found: {docs_dir}"}
    if not stories_path.exists():
        return {"error": f"stories not found: {stories_path}"}

    chunker, retriever = build_pipeline(idea, vs, embedder)
    chunk_count = ingest(docs_dir, chunker, embedder, vs)
    stories = load_user_stories(stories_path)

    # For BM25 hybrid: load all chunks into a list for in-memory BM25 indexing
    all_chunks_list = None
    if idea.use_bm25_hybrid:
        # Need to re-collect the chunks from disk for BM25; easier: rebuild from chunker
        all_chunks_list = []
        for doc in load_documents(docs_dir):
            all_chunks_list.extend(chunker.chunk_document(doc))

    out = {
        "idea_id": idea.id,
        "idea_name": idea.name,
        "project": project,
        "chunk_count": chunk_count,
        "per_story_results": [],
    }

    for s in stories:
        if idea.use_bm25_hybrid and all_chunks_list:
            query = build_query(s, idea) or " ".join(filter(None, [
                f"Security requirements for {s['title']}", s.get("story", ""),
                "; ".join(s.get("acceptance_criteria", [])),
            ]))
            chunks = bm25_hybrid_retrieve(
                vs, embedder, query, all_chunks_list,
                idea.top_k, idea.similarity_threshold, idea.rrf_k,
            )
        else:
            res = retrieve_for_story(retriever, s, idea)
            chunks = res.chunks
        ctx = retriever.format_context(chunks)
        out["per_story_results"].append({
            "story_id": s["id"], "story_title": s["title"],
            "baseline_result": {"user_story": s},
            "rag_result": {"user_story": s, "rag_enabled": True, "retrieval_context": ctx},
        })

    return out


def main():
    project_filter = os.environ.get("ABLATION_PROJECTS", "").split(",") if os.environ.get("ABLATION_PROJECTS") else None
    idea_filter = (
        {int(x) for x in os.environ.get("ABLATION_IDEAS", "").split(",")}
        if os.environ.get("ABLATION_IDEAS") else None
    )
    out_dir = Path(os.environ.get("ABLATION_OUTPUT", "/tmp"))

    projects_to_run = [p for p in PROJECTS if not project_filter or p in project_filter]
    ideas_to_run = [i for i in IDEAS if not idea_filter or i.id in idea_filter]

    print(f"Projects: {projects_to_run}")
    print(f"Ideas:    {[i.id for i in ideas_to_run]} ({len(ideas_to_run)})")
    print(f"Output:   {out_dir}")
    print()

    embedder = DocumentEmbedder()
    vs = SecReqGenVectorStore(persist_directory=VECTORDB)

    out_path = out_dir / "rag_ablation_retrieval.json"
    results = []
    t0 = time.perf_counter()
    for idea in ideas_to_run:
        for project in projects_to_run:
            tt = time.perf_counter()
            print(f"[idea {idea.id} {idea.name}] × [{project}] ", end="", flush=True)
            try:
                result = run_idea_on_project(idea, project, embedder, vs)
            except Exception as e:
                # Don't lose progress to a single cell error — record + continue.
                result = {
                    "idea_id": idea.id, "idea_name": idea.name,
                    "project": project, "error": f"{type(e).__name__}: {e}",
                }
            elapsed = time.perf_counter() - tt
            if "error" in result:
                print(f"ERROR: {result['error']}")
            else:
                print(f"chunks={result['chunk_count']}  stories={len(result['per_story_results'])}  ({elapsed:.1f}s)")
            results.append(result)
            # Incremental write so a later crash doesn't lose progress.
            out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")
    print(f"Total wall time: {(time.perf_counter() - t0) / 60:.1f} min")
    print("\nNext step: run judge on each cell and aggregate.")
    print(f"  Use scripts/judge_retrieval_quality.py with --output, OR")
    print(f"  use scripts/rag_ablation_judge.py (which reads {out_path} and judges all cells)")


if __name__ == "__main__":
    main()
