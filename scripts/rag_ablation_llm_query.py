"""Ideas 11, 12, 16 — local LLM-driven query modification.

These three ideas modify ONLY the retrieval-time query, not the
generation step or the chunker. They reuse the Phase A retrieval
infrastructure (`SimpleRetriever`, the patched chunker, the CE reranker
from idea 4) but replace the query construction with one of:

- Idea 11 — HyDE: a local LLM (mistral-small3.2:24b) drafts a hypothetical
  answer to the user story, embed that, retrieve.
- Idea 12 — LLM query rewrite: a local LLM rewrites the user story into
  a single security-focused query, replacing the keyword-map expansion.
- Idea 16 — Multi-query decomposition: a local LLM breaks the user story
  into 3-5 sub-queries; retrieve for each; merge via reciprocal-rank fusion
  on the union of top-K from each sub-query.

This script writes a raw_runs-shaped JSON for each (idea, project) cell,
in the same shape as `rag_ablation_study.py`, so `rag_ablation_judge.py`
can score these cells the same way.

Run::

    PYTHONPATH=src python scripts/rag_ablation_llm_query.py
    PYTHONPATH=src python scripts/rag_ablation_judge.py
        ABLATION_INPUT=/tmp/rag_ablation_llm_query.json ...
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from secreqgen.config import SecReqGenConfig
from secreqgen.generation.llm_client import OllamaClient
from secreqgen.generation.schemas import UserStory
from secreqgen.ingestion import DocumentEmbedder, ArchitectureAwareChunker
from secreqgen.ingestion.loader import MarkdownLoader
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
from secreqgen.retrieval.reranker import CrossEncoderReranker

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
VECTORDB = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/data/vectordb")

PROJECTS = {
    "mastodon":   (ROOT / "mastodon/docs",              ROOT / "mastodon/user_stories.json"),
    "keycloak":   (ROOT / "extracted_docs_md/keycloak", ROOT / "keycloak/user_stories.json"),
    "bitwarden":  (ROOT / "bitwarden-test-data",        ROOT / "bitwarden/user_stories.json"),
    "gitlab":     (ROOT / "gitlab/docs",                ROOT / "gitlab/user_stories.json"),
    "mattermost": (ROOT / "mattermost/docs",            ROOT / "mattermost/user_stories.json"),
}


# ─────────────────────────────────────────────────────────────────────
# Prompts for the LLM query transformers
# ─────────────────────────────────────────────────────────────────────

HYDE_SYSTEM = """You are a security engineer. Given a user story, write the
text of a hypothetical security-architecture documentation passage that
would directly answer the security needs of that story. The passage should
be 100-200 words, use security terminology (authentication, authorization,
input validation, encryption, audit logging, OWASP/ASVS), and read like it
came from an actual admin or developer guide. Output ONLY the passage
text — no preamble, no formatting headers, no JSON."""

HYDE_USER_TEMPLATE = """USER STORY: {title}
{story}
Acceptance criteria:
{acceptance_criteria}

Hypothetical security-documentation passage:"""


REWRITE_SYSTEM = """You are a security retrieval-query writer. Given a user
story, output a single dense retrieval query (one to three sentences, 30-60
words) that would find the most relevant security-architecture and
security-config documentation. Mention specific security mechanisms (e.g.
PKCE, MFA, redirect URI validation, rate limiting, HTTP signatures, RBAC,
audit logging). Output ONLY the query text — no preamble, no JSON, no
quotes."""

REWRITE_USER_TEMPLATE = """USER STORY: {title}
{story}
Acceptance criteria:
{acceptance_criteria}

Retrieval query:"""


DECOMPOSE_SYSTEM = """You are a security retrieval-query writer. Given a
user story, decompose it into 3-5 independent sub-queries that each target
ONE specific security concern (e.g. "PKCE in OAuth flow", "redirect URI
validation", "session timeout configuration"). Each sub-query should be a
single sentence, 8-20 words, using security terminology. Output as a JSON
array of strings, no extra text:
{"sub_queries": ["sub-query 1", "sub-query 2", "sub-query 3"]}"""

DECOMPOSE_USER_TEMPLATE = """USER STORY: {title}
{story}
Acceptance criteria:
{acceptance_criteria}

Output JSON only."""


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _ac_text(story: dict) -> str:
    ac = story.get("acceptance_criteria", [])
    if not ac:
        return "(none)"
    return "\n".join(f"- {x}" for x in ac)


def load_user_stories(p: Path) -> list[dict]:
    raw = json.loads(p.read_text())
    if isinstance(raw, dict) and "user_stories" in raw:
        return raw["user_stories"]
    return raw


def ingest_project(docs_dir: Path, embedder: DocumentEmbedder, vs: SecReqGenVectorStore) -> int:
    """Re-ingest with the WINNING chunker config from Phase A (idea 4:
    h3 cap + front-matter strip)."""
    vs.clear()
    chunker = ArchitectureAwareChunker(max_chunking_level=3)
    loader = MarkdownLoader()
    chunks = []
    for f in sorted(docs_dir.glob("**/*.md")):
        if not f.is_file():
            continue
        try:
            d = loader.load(f)
            chunks.extend(chunker.chunk_document(d))
        except Exception as e:
            print(f"    ! load failed for {f.name}: {e}")
    if not chunks:
        return 0
    embs = embedder.embed_texts([c.content for c in chunks])
    vs.add_chunks(chunks, embs, DocumentType.ARCHITECTURE)
    return len(chunks)


# ─────────────────────────────────────────────────────────────────────
# Idea 11 — HyDE
# ─────────────────────────────────────────────────────────────────────


def hyde_query(story: dict, llm: OllamaClient) -> str:
    """LLM drafts a hypothetical doc passage. Return passage text."""
    prompt = HYDE_USER_TEMPLATE.format(
        title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story),
    )
    resp = llm.generate(prompt=prompt, system_prompt=HYDE_SYSTEM)
    return resp.content.strip()


# ─────────────────────────────────────────────────────────────────────
# Idea 12 — LLM query rewrite
# ─────────────────────────────────────────────────────────────────────


def llm_rewrite_query(story: dict, llm: OllamaClient) -> str:
    prompt = REWRITE_USER_TEMPLATE.format(
        title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story),
    )
    resp = llm.generate(prompt=prompt, system_prompt=REWRITE_SYSTEM)
    return resp.content.strip()


# ─────────────────────────────────────────────────────────────────────
# Idea 16 — Multi-query decomposition
# ─────────────────────────────────────────────────────────────────────


def decompose_query(story: dict, llm: OllamaClient) -> list[str]:
    prompt = DECOMPOSE_USER_TEMPLATE.format(
        title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story),
    )
    resp = llm.generate(prompt=prompt, system_prompt=DECOMPOSE_SYSTEM, format="json")
    raw = resp.content.strip()
    try:
        obj = json.loads(raw)
        subs = obj.get("sub_queries", [])
        if isinstance(subs, list) and subs:
            return [str(x) for x in subs[:5]]
    except json.JSONDecodeError:
        pass
    # Fall back: split on newlines
    return [line.strip(" -*\t") for line in raw.split("\n") if line.strip()][:5]


def multi_query_retrieve(sub_queries: list[str], retriever: SimpleRetriever, top_k: int = 5):
    """Run retrieve() for each sub-query, fuse with RRF, return top-K."""
    all_lists = []
    for q in sub_queries:
        r = retriever.retrieve(q)
        all_lists.append(r.chunks)
    # RRF on chunk_id (k=60)
    scores = {}
    chunks_by_id = {}
    for ranked_list in all_lists:
        for rank, ch in enumerate(ranked_list, start=1):
            scores[ch.chunk_id] = scores.get(ch.chunk_id, 0.0) + 1.0 / (60 + rank)
            chunks_by_id[ch.chunk_id] = ch
    sorted_ids = sorted(scores, key=lambda c: -scores[c])
    return [chunks_by_id[cid] for cid in sorted_ids[:top_k]]


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

IDEAS_LLM_QUERY = [
    (11, "hyde_query"),
    (12, "llm_query_rewrite"),
    (16, "multi_query_decompose"),
]


def run_cell(idea_id: int, idea_name: str, project: str,
             embedder: DocumentEmbedder, vs: SecReqGenVectorStore,
             reranker: CrossEncoderReranker, mistral: OllamaClient) -> dict:
    """Run one (idea, project) cell for an LLM-query idea."""
    docs_dir, stories_path = PROJECTS[project]
    if not docs_dir.exists() or not stories_path.exists():
        return {"idea_id": idea_id, "idea_name": idea_name, "project": project,
                "error": f"missing inputs ({docs_dir} or {stories_path})"}

    chunk_count = ingest_project(docs_dir, embedder, vs)

    cfg = SecReqGenConfig()
    retriever = SimpleRetriever(
        vs, embedder,
        top_k=cfg.retrieval.top_k,
        similarity_threshold=cfg.retrieval.similarity_threshold,
        reranker=reranker,
        rerank_candidates=30,
    )

    stories = load_user_stories(stories_path)
    cell = {
        "idea_id": idea_id, "idea_name": idea_name, "project": project,
        "chunk_count": chunk_count, "per_story_results": [],
    }

    for s in stories:
        try:
            if idea_id == 11:
                # HyDE: replace query with hypothetical-doc text
                query = hyde_query(s, mistral)
                result = retriever.retrieve(query)
                chunks = result.chunks
                cell.setdefault("llm_query_log", []).append({"story_id": s["id"], "hyde_query": query[:300]})
            elif idea_id == 12:
                # LLM query rewrite
                query = llm_rewrite_query(s, mistral)
                result = retriever.retrieve(query)
                chunks = result.chunks
                cell.setdefault("llm_query_log", []).append({"story_id": s["id"], "rewritten_query": query[:300]})
            elif idea_id == 16:
                # Multi-query decomposition
                subs = decompose_query(s, mistral)
                chunks = multi_query_retrieve(subs, retriever, top_k=cfg.retrieval.top_k)
                cell.setdefault("llm_query_log", []).append({"story_id": s["id"], "sub_queries": subs})
            else:
                raise ValueError(f"unknown idea {idea_id}")

            ctx = retriever.format_context(chunks)
        except Exception as e:
            ctx = f"ERROR: {type(e).__name__}: {e}"
            print(f"    ! story {s['id']} failed: {e}")

        cell["per_story_results"].append({
            "story_id": s["id"], "story_title": s["title"],
            "baseline_result": {"user_story": s},
            "rag_result": {"user_story": s, "rag_enabled": True, "retrieval_context": ctx},
        })

    return cell


def main():
    out_path = Path(os.environ.get("ABLATION_LLM_QUERY_OUT", "/tmp/rag_ablation_llm_query.json"))
    project_filter = os.environ.get("ABLATION_PROJECTS", "").split(",") if os.environ.get("ABLATION_PROJECTS") else None
    idea_filter = {int(x) for x in os.environ.get("ABLATION_IDEAS", "").split(",")} if os.environ.get("ABLATION_IDEAS") else None

    projects_to_run = [p for p in PROJECTS if not project_filter or p in project_filter]
    ideas_to_run = [(iid, iname) for iid, iname in IDEAS_LLM_QUERY if not idea_filter or iid in idea_filter]

    print(f"Projects: {projects_to_run}")
    print(f"Ideas:    {[i for i, _ in ideas_to_run]}")
    print(f"Output:   {out_path}")
    print()

    embedder = DocumentEmbedder()
    vs = SecReqGenVectorStore(persist_directory=VECTORDB)
    reranker = CrossEncoderReranker()   # Phase A winner: include CE rerank
    mistral = OllamaClient(
        model_name="mistral-small3.2:24b",
        temperature=0.1,
        max_tokens=300,
        timeout=180.0,
    )

    results = []
    t0 = time.perf_counter()
    for idea_id, idea_name in ideas_to_run:
        for project in projects_to_run:
            tt = time.perf_counter()
            print(f"[idea {idea_id} {idea_name}] × [{project}] ", end="", flush=True)
            try:
                cell = run_cell(idea_id, idea_name, project, embedder, vs, reranker, mistral)
            except Exception as e:
                cell = {"idea_id": idea_id, "idea_name": idea_name, "project": project,
                        "error": f"{type(e).__name__}: {e}"}
            elapsed = time.perf_counter() - tt
            if "error" in cell:
                print(f"ERROR: {cell['error']}")
            else:
                print(f"chunks={cell.get('chunk_count', 0)}  stories={len(cell['per_story_results'])}  ({elapsed:.1f}s)")
            results.append(cell)
            out_path.write_text(json.dumps(results, indent=2))   # incremental write

    print(f"\nWrote {out_path}")
    print(f"Total wall time: {(time.perf_counter() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
