"""Ideas 17-20 — local LLM as a retrieval agent (docs + code).

Four agentic retrieval approaches. Each modifies WHAT gets fed to the
generator's downstream prompt; the generation step itself is unchanged.

**Role hygiene**: the agent role uses mistral-small3.2:24b, NOT qwen3:30b.
This is deliberate: the downstream generator (Phase B) is qwen3, so
having qwen3 also be the agent that picks what qwen3 sees creates a
closed bias loop. Using a different model family for the agent keeps
the "what to retrieve" decision independent of the "what to write"
decision.

- Idea 17 — ReAct iterative retrieval: mistral-small reflects on initial
  top-K, picks a follow-up sub-query, retrieves, repeats up to 3 rounds.
- Idea 18 — Code search agent: mistral-small emits ~3 grep patterns over
  the project source repo; we run those greps; matching code snippets
  (with file + line) become the retrieved "chunks".
- Idea 19 — Hybrid doc + code agent: idea 4 (CE-rerank doc retrieval)
  results UNION idea 18 (code search), interleaved.
- Idea 20 — Section-navigation agent: mistral-small sees the TOC (file →
  section paths), picks 3-5 sections to "read in full"; those whole
  sections become the retrieved chunks (effectively "h2 cap on demand").

Output shape matches `rag_ablation_study.py` so `rag_ablation_judge.py`
can score these the same way.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from secreqgen.config import SecReqGenConfig
from secreqgen.generation.llm_client import OllamaClient
from secreqgen.ingestion import DocumentEmbedder, ArchitectureAwareChunker
from secreqgen.ingestion.loader import MarkdownLoader
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.generation.schemas import UserStory
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
from secreqgen.retrieval.reranker import CrossEncoderReranker
from secreqgen.retrieval.vectorstore import QueryResult

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
REPOS = ROOT / "repos"
VECTORDB = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/data/vectordb")

PROJECTS = {
    "mastodon":   (ROOT / "mastodon/docs",              ROOT / "mastodon/user_stories.json"),
    "keycloak":   (ROOT / "extracted_docs_md/keycloak", ROOT / "keycloak/user_stories.json"),
    "bitwarden":  (ROOT / "bitwarden-test-data",        ROOT / "bitwarden/user_stories.json"),
    "gitlab":     (ROOT / "gitlab/docs",                ROOT / "gitlab/user_stories.json"),
    "mattermost": (ROOT / "mattermost/docs",            ROOT / "mattermost/user_stories.json"),
}


# ─────────────────────────────────────────────────────────────────────
# Shared helpers (mirror rag_ablation_llm_query.py)
# ─────────────────────────────────────────────────────────────────────

def _ac_text(story: dict) -> str:
    ac = story.get("acceptance_criteria", [])
    return "\n".join(f"- {x}" for x in ac) if ac else "(none)"

def load_user_stories(p: Path) -> list[dict]:
    raw = json.loads(p.read_text())
    if isinstance(raw, dict) and "user_stories" in raw:
        return raw["user_stories"]
    return raw

def ingest_project(docs_dir: Path, embedder, vs) -> int:
    vs.clear()
    chunker = ArchitectureAwareChunker(max_chunking_level=3)
    loader = MarkdownLoader()
    chunks = []
    for f in sorted(docs_dir.glob("**/*.md")):
        if f.is_file():
            try:
                chunks.extend(chunker.chunk_document(loader.load(f)))
            except Exception:
                pass
    if not chunks:
        return 0
    embs = embedder.embed_texts([c.content for c in chunks])
    vs.add_chunks(chunks, embs, DocumentType.ARCHITECTURE)
    return len(chunks)


def make_pseudo_chunk(content: str, source: str, idx: int, score: float = 0.5) -> QueryResult:
    """Construct a QueryResult-shaped object from non-vectordb content
    so the standard format_context() can format it."""
    return QueryResult(
        chunk_id=f"agent-{idx:03d}",
        content=content,
        source_document=source,
        section_hierarchy=[source.split("/")[-1]],
        # Convert "similarity" back to L2 distance for symmetry. The
        # format_context only reads similarity, but vectorstore.QueryResult
        # has similarity computed from distance.
        distance=2 * (1.0 - score),
        metadata={"agent_source": True},
    )


# ─────────────────────────────────────────────────────────────────────
# Idea 17 — ReAct iterative retrieval
# ─────────────────────────────────────────────────────────────────────

REACT_SYSTEM = """You are a security retrieval agent. You will be shown a
user story and an initial set of retrieved documentation chunks. Your
job is to decide whether the chunks fully cover the security concerns of
the story. If not, propose ONE follow-up search query (15-25 words, using
specific security terminology) that would fill the biggest gap.

Output STRICTLY a JSON object:
{"need_more": true|false, "follow_up_query": "<query or empty string>", "reason": "<one short sentence>"}"""

REACT_USER_TEMPLATE = """USER STORY: [{story_id}] {title}
{story}
Acceptance criteria:
{acceptance_criteria}

CURRENTLY RETRIEVED CHUNKS:
{chunks_summary}

Output JSON only."""


def react_retrieve(story: dict, retriever: SimpleRetriever, agent_llm: OllamaClient, max_rounds: int = 3) -> list[QueryResult]:
    """Run ReAct loop: retrieve → critique → re-retrieve."""
    us = UserStory(
        id=story["id"], title=story["title"],
        story=story["story"], acceptance_criteria=story.get("acceptance_criteria", []),
    )
    result = retriever.retrieve_for_user_story(us)
    accumulated = list(result.chunks)
    seen_ids = {c.chunk_id for c in accumulated}

    for round_i in range(max_rounds):
        # Summarize what we have for the LLM
        chunks_summary = "\n".join(
            f"- ({c.similarity:.2f}) {c.source_document.split('/')[-1]} / "
            f"{' > '.join(c.section_hierarchy)}: {c.content[:200]}..."
            for c in accumulated[:8]
        )
        user_prompt = REACT_USER_TEMPLATE.format(
            story_id=story["id"], title=story["title"], story=story["story"],
            acceptance_criteria=_ac_text(story),
            chunks_summary=chunks_summary,
        )
        try:
            resp = agent_llm.generate(prompt=user_prompt, system_prompt=REACT_SYSTEM, format="json")
            obj = json.loads(resp.content.strip())
        except Exception:
            break
        if not obj.get("need_more"):
            break
        follow_up = (obj.get("follow_up_query") or "").strip()
        if not follow_up:
            break
        # Retrieve with the follow-up query
        more = retriever.retrieve(follow_up)
        for c in more.chunks:
            if c.chunk_id not in seen_ids:
                accumulated.append(c)
                seen_ids.add(c.chunk_id)

    # Return top-K by similarity
    accumulated.sort(key=lambda c: -c.similarity)
    return accumulated[: retriever.top_k]


# ─────────────────────────────────────────────────────────────────────
# Idea 18 — Code search agent
# ─────────────────────────────────────────────────────────────────────

GREP_PATTERN_SYSTEM = """You are a security code-search agent. Given a
user story, propose 3 specific grep patterns that would find
security-relevant code in a project's source tree. Patterns should be
case-insensitive, ~3-15 characters each, looking for function names /
identifiers (e.g. "csrf_token", "validate_redirect_uri", "bcrypt", "rate_limit").

Output STRICTLY:
{"patterns": ["pat1", "pat2", "pat3"]}"""

GREP_PATTERN_USER_TEMPLATE = """USER STORY: [{story_id}] {title}
{story}
Acceptance criteria:
{acceptance_criteria}

Three grep patterns (JSON only):"""


# Code source extensions we'll search by project type (small, fast list)
_SOURCE_EXTS = (".rb", ".py", ".js", ".ts", ".tsx", ".go", ".java", ".cs", ".rs", ".php", ".kt", ".swift")


def code_search_agent(story: dict, project: str, agent_llm: OllamaClient, max_hits_per_pattern: int = 3) -> list[QueryResult]:
    repo_dir = REPOS / project
    if not repo_dir.is_dir():
        return []   # repo not cloned; caller handles gracefully

    prompt = GREP_PATTERN_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story),
    )
    try:
        resp = agent_llm.generate(prompt=prompt, system_prompt=GREP_PATTERN_SYSTEM, format="json")
        patterns = json.loads(resp.content.strip()).get("patterns", [])[:3]
    except Exception:
        return []

    chunks: list[QueryResult] = []
    for pi, pat in enumerate(patterns):
        if not pat or len(pat) > 80:
            continue
        # Run grep across source files
        try:
            include_args = []
            for ext in _SOURCE_EXTS:
                include_args.extend(["--include", f"*{ext}"])
            result = subprocess.run(
                ["grep", "-rEnI", "--max-count=2", *include_args, pat, str(repo_dir)],
                capture_output=True, text=True, timeout=20,
            )
            lines = result.stdout.splitlines()[:max_hits_per_pattern]
        except Exception:
            continue

        for li, line in enumerate(lines):
            # parse "/path/file.py:42:matched line"
            m = re.match(r"^(.+?):(\d+):(.*)$", line)
            if not m:
                continue
            file_path, lineno, snippet = m.groups()
            rel = Path(file_path).relative_to(repo_dir) if file_path.startswith(str(repo_dir)) else Path(file_path)
            content = (
                f"### code search hit (pattern: {pat!r})\n"
                f"File: {rel}:{lineno}\n"
                f"```\n{snippet.strip()[:600]}\n```\n"
            )
            chunks.append(make_pseudo_chunk(
                content=content,
                source=f"code:{rel}:{lineno}",
                idx=len(chunks),
                score=0.55,  # arbitrary but >threshold so it's surfaced
            ))
    return chunks[: 5]


# ─────────────────────────────────────────────────────────────────────
# Idea 19 — Hybrid doc + code agent
# ─────────────────────────────────────────────────────────────────────

def hybrid_doc_code(story: dict, project: str, retriever: SimpleRetriever, agent_llm: OllamaClient) -> list[QueryResult]:
    us = UserStory(
        id=story["id"], title=story["title"],
        story=story["story"], acceptance_criteria=story.get("acceptance_criteria", []),
    )
    doc_chunks = retriever.retrieve_for_user_story(us).chunks
    code_chunks = code_search_agent(story, project, agent_llm)
    # Interleave: doc1, code1, doc2, code2, ...
    interleaved: list[QueryResult] = []
    for d, c in zip(doc_chunks, code_chunks):
        interleaved.append(d)
        interleaved.append(c)
    # append the tail of whichever was longer
    remaining = doc_chunks[len(code_chunks):] + code_chunks[len(doc_chunks):]
    interleaved.extend(remaining)
    return interleaved[:8]


# ─────────────────────────────────────────────────────────────────────
# Idea 20 — Section-navigation agent
# ─────────────────────────────────────────────────────────────────────

NAV_SYSTEM = """You are a security documentation navigation agent. You will
be given a user story and a table of contents listing all documentation
sections by their file path and header hierarchy. Pick 3-5 sections that
would be most useful for writing security requirements for this story.

Output STRICTLY:
{"sections": [<index1>, <index2>, ...]}

Use the integer indices shown in the TOC. Be concise."""

NAV_USER_TEMPLATE = """USER STORY: [{story_id}] {title}
{story}
Acceptance criteria:
{acceptance_criteria}

TABLE OF CONTENTS (index: file/path > sections):
{toc}

Output JSON only."""


def section_nav_agent(story: dict, docs_dir: Path, agent_llm: OllamaClient) -> list[QueryResult]:
    """Build TOC from on-disk markdown sections; have LLM pick the
    most-relevant 3-5; return those whole sections as pseudo-chunks."""
    chunker = ArchitectureAwareChunker(max_chunking_level=3)
    loader = MarkdownLoader()
    toc_items = []   # list of (idx, file_rel, section_path, content)
    for f in sorted(docs_dir.glob("**/*.md")):
        if not f.is_file():
            continue
        try:
            d = loader.load(f)
            for ch in chunker.chunk_document(d):
                path = " > ".join(ch.section_hierarchy) or "(top)"
                toc_items.append((
                    len(toc_items),
                    f.relative_to(docs_dir),
                    path,
                    ch.content,
                ))
        except Exception:
            continue

    if not toc_items:
        return []

    # Cap TOC for the prompt (1000 max items)
    toc_lines = [
        f"{i}: {file} > {section}"
        for i, file, section, _ in toc_items[:1000]
    ]
    toc_str = "\n".join(toc_lines)

    prompt = NAV_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story),
        toc=toc_str[:50_000],  # keep prompt manageable
    )
    try:
        resp = agent_llm.generate(prompt=prompt, system_prompt=NAV_SYSTEM, format="json")
        picked = [int(x) for x in json.loads(resp.content.strip()).get("sections", [])][:5]
    except Exception:
        return []

    chunks: list[QueryResult] = []
    for idx in picked:
        if idx < 0 or idx >= len(toc_items):
            continue
        _, file, section, content = toc_items[idx]
        chunks.append(make_pseudo_chunk(
            content=f"Section: {section}\n{content[:2000]}",
            source=f"{file} > {section}",
            idx=len(chunks),
            score=0.6,
        ))
    return chunks


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

IDEAS_AGENT = [
    (17, "react_iterative_retrieval"),
    (18, "code_search_agent"),
    (19, "hybrid_doc_code_agent"),
    (20, "section_nav_agent"),
]


def run_cell(idea_id: int, idea_name: str, project: str,
             embedder, vs, reranker, agent_llm) -> dict:
    docs_dir, stories_path = PROJECTS[project]
    if not docs_dir.exists() or not stories_path.exists():
        return {"idea_id": idea_id, "idea_name": idea_name, "project": project,
                "error": f"missing inputs"}

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
            if idea_id == 17:
                chunks = react_retrieve(s, retriever, agent_llm)
            elif idea_id == 18:
                chunks = code_search_agent(s, project, agent_llm)
            elif idea_id == 19:
                chunks = hybrid_doc_code(s, project, retriever, agent_llm)
            elif idea_id == 20:
                chunks = section_nav_agent(s, docs_dir, agent_llm)
            else:
                raise ValueError(f"unknown idea {idea_id}")
            ctx = retriever.format_context(chunks)
        except Exception as e:
            ctx = f"AGENT_ERROR: {type(e).__name__}: {e}"
            chunks = []
            print(f"      ! story {s['id']} agent error: {e}")

        cell["per_story_results"].append({
            "story_id": s["id"], "story_title": s["title"],
            "baseline_result": {"user_story": s},
            "rag_result": {"user_story": s, "rag_enabled": True, "retrieval_context": ctx},
        })

    return cell


def main():
    out_path = Path(os.environ.get("ABLATION_AGENT_OUT", "/tmp/rag_ablation_agents.json"))
    project_filter = os.environ.get("ABLATION_PROJECTS", "").split(",") if os.environ.get("ABLATION_PROJECTS") else None
    idea_filter = {int(x) for x in os.environ.get("ABLATION_IDEAS", "").split(",")} if os.environ.get("ABLATION_IDEAS") else None

    projects_to_run = [p for p in PROJECTS if not project_filter or p in project_filter]
    ideas_to_run = [(iid, iname) for iid, iname in IDEAS_AGENT if not idea_filter or iid in idea_filter]

    print(f"Projects: {projects_to_run}")
    print(f"Ideas:    {[i for i, _ in ideas_to_run]}")
    print()

    embedder = DocumentEmbedder()
    vs = SecReqGenVectorStore(persist_directory=VECTORDB)
    reranker = CrossEncoderReranker()
    # Agent role uses mistral-small (different family from qwen3 generator
    # to avoid closed-loop bias — see module docstring).
    agent_llm = OllamaClient(
        model_name="mistral-small3.2:24b",
        temperature=0.1, max_tokens=400, timeout=300.0,
    )

    results = []
    t0 = time.perf_counter()
    for idea_id, idea_name in ideas_to_run:
        for project in projects_to_run:
            tt = time.perf_counter()
            print(f"[idea {idea_id} {idea_name}] × [{project}] ", end="", flush=True)
            try:
                cell = run_cell(idea_id, idea_name, project, embedder, vs, reranker, agent_llm)
            except Exception as e:
                cell = {"idea_id": idea_id, "idea_name": idea_name, "project": project,
                        "error": f"{type(e).__name__}: {e}"}
            elapsed = time.perf_counter() - tt
            if "error" in cell:
                print(f"ERROR: {cell['error']}")
            else:
                print(f"stories={len(cell['per_story_results'])}  ({elapsed:.1f}s)")
            results.append(cell)
            out_path.write_text(json.dumps(results, indent=2))

    print(f"\nWrote {out_path}")
    print(f"Total wall time: {(time.perf_counter() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
