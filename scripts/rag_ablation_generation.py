"""Ideas 13, 14, 15 — local-LLM generation-strategy modifications.

These three ideas modify the GENERATION step, not retrieval. They use
the winning Phase A retrieval (idea 4 = h3-cap chunker + CE rerank +
threshold 0.4) and vary how the LLM is prompted to produce security
requirements.

- Idea 13 — Self-critique + refinement: qwen3:30b generates initial
  requirements; llama3:8b judges each ("strong" / "weak" / "wrong");
  qwen3 rewrites only the weak/wrong ones.
- Idea 14 — Chain-of-thought: qwen3 reasons about (threats, assets,
  trust boundaries) for ~50 tokens BEFORE writing requirements.
- Idea 15 — Few-shot exemplars: 3 hand-curated (story, requirement)
  examples prepended to the qwen3 prompt to anchor format and depth.

Output: per-project, per-story, the generated requirements. NOT chunks
to retrieve — these are the final requirements. Saved to
``/tmp/rag_ablation_generation.json`` for the manual qualitative read.

Cost: 3 ideas × 5 projects × 3 stories × ~50s/generation ≈ 35-50 min
total. Cap to 3 stories per project to keep the run finite.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
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

# Cap stories per project for time (full N=8 would take ~3× longer).
# Picked deterministically by index.
STORIES_PER_PROJECT = 3


# ─────────────────────────────────────────────────────────────────────
# Baseline + idea 14 — direct generation (with / without CoT preface)
# ─────────────────────────────────────────────────────────────────────

GEN_SYSTEM = """You are a security architect. Given a user story and
retrieved security documentation, produce 4-6 security requirements
the engineering team should implement. Each requirement must be:

- Specific and testable (not "implement security best practices")
- Tied to a security mechanism (encryption, authentication, validation, etc.)
- Mapped to an ASVS v4 category from this list:
  V1: Architecture / V2: Authentication / V3: Session Management /
  V4: Access Control / V5: Validation / V6: Cryptography /
  V7: Error Handling / V8: Data Protection / V9: Communication /
  V13: API and Web Service / V14: Configuration

Output as JSON with this shape:
{"requirements": [
  {"title": "...", "description": "...", "asvs_category": "V2: Authentication", "rationale": "..."}
]}"""

GEN_USER_TEMPLATE = """USER STORY [{story_id}] {title}
{story}
Acceptance criteria:
{acceptance_criteria}

RETRIEVED CONTEXT:
{context}"""

COT_PREFIX = """Before writing the requirements, briefly reason about (1)
the assets at risk in this user story, (2) the threats those assets face,
and (3) the trust boundaries crossed. Then output the JSON object."""

# ─────────────────────────────────────────────────────────────────────
# Idea 15 — Few-shot exemplars (hand-curated from generic ASVS practice;
# deliberately NOT drawn from any project in our evaluation set)
# ─────────────────────────────────────────────────────────────────────

FEW_SHOT_EXEMPLARS = """Example 1:
USER STORY: User Login with Password and 2FA
EXPECTED OUTPUT:
{"requirements": [
  {"title": "Argon2id password verification", "description": "Server must verify submitted passwords against an Argon2id hash with memory cost 19MiB, iterations 2, parallelism 1. Plaintext passwords must never be persisted or logged.", "asvs_category": "V2: Authentication", "rationale": "ASVS V2.4.4 requires modern memory-hard hashing; Argon2id is OWASP-recommended."},
  {"title": "TOTP second factor verification", "description": "After password verification, a 6-digit TOTP must be required. The shared secret must be stored encrypted at rest using AES-256-GCM, with a per-secret IV.", "asvs_category": "V2: Authentication", "rationale": "ASVS V2.8 mandates strong second factor; TOTP with encrypted seed prevents secret exfiltration."}
]}

Example 2:
USER STORY: User Submits a Comment with Attached File
EXPECTED OUTPUT:
{"requirements": [
  {"title": "MIME-type allow-list for uploads", "description": "Server must verify uploaded file MIME types against a server-side allow-list (image/png, image/jpeg, application/pdf). Magic-byte detection must be used in addition to Content-Type header.", "asvs_category": "V12: Files and Resources", "rationale": "ASVS V12.2; prevents content-sniffing and disguised executable upload."},
  {"title": "Filename sanitization", "description": "Uploaded filenames must be replaced with server-generated UUIDs before storage. Original filenames may be preserved in metadata but never used in filesystem paths.", "asvs_category": "V5: Validation, Sanitization and Encoding", "rationale": "ASVS V5.3; prevents path traversal via crafted filenames."}
]}

"""


# ─────────────────────────────────────────────────────────────────────
# Idea 13 — self-critique + refinement
# ─────────────────────────────────────────────────────────────────────

CRITIQUE_SYSTEM = """You are an ASVS security reviewer. For each
requirement in the list, judge whether it is:
- "strong": specific, testable, tied to a security mechanism
- "weak": vague, generic, or missing a security mechanism
- "wrong": off-topic or incorrect for the user story

Output STRICTLY:
{"verdicts": ["strong"|"weak"|"wrong", ...]}
List one verdict per input requirement, in order."""

CRITIQUE_USER_TEMPLATE = """USER STORY [{story_id}] {title}
{story}

REQUIREMENTS TO JUDGE:
{requirements}

Output JSON only."""

REFINE_SYSTEM = """You are a security architect. You previously produced a
list of security requirements. A reviewer flagged some as weak or wrong.
Rewrite ONLY the flagged ones to be specific, testable, and tied to
concrete security mechanisms. Strong requirements must be returned
unchanged. Use the same JSON shape as before."""

REFINE_USER_TEMPLATE = """USER STORY [{story_id}] {title}
{story}

CURRENT REQUIREMENTS WITH VERDICTS:
{annotated}

Rewrite the weak/wrong ones. Strong ones stay as-is. Output the full
list (all requirements, in the original order) as JSON."""


# ─────────────────────────────────────────────────────────────────────
# Pipeline helpers
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


def retrieve_context(story: dict, retriever: SimpleRetriever) -> str:
    us = UserStory(id=story["id"], title=story["title"], story=story["story"],
                   acceptance_criteria=story.get("acceptance_criteria", []))
    res = retriever.retrieve_for_user_story(us)
    return retriever.format_context(res.chunks)


def parse_requirements_json(raw: str) -> list[dict]:
    """Robustly parse the LLM's requirement-list response."""
    try:
        obj = json.loads(raw.strip())
        reqs = obj.get("requirements", [])
        if isinstance(reqs, list):
            return [r for r in reqs if isinstance(r, dict)]
    except json.JSONDecodeError:
        pass
    return []


# ─────────────────────────────────────────────────────────────────────
# Generation strategies
# ─────────────────────────────────────────────────────────────────────


def gen_baseline(story, context, qwen):
    """Idea-1 equivalent baseline generation: plain prompt, no CoT, no few-shot."""
    user_prompt = GEN_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story), context=context[:4000],
    )
    resp = qwen.generate(prompt=user_prompt, system_prompt=GEN_SYSTEM, format="json")
    return parse_requirements_json(resp.content), resp.content


def gen_chain_of_thought(story, context, qwen):
    """Idea 14 — qwen3 reasons about threats/assets before generating."""
    user_prompt = GEN_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story), context=context[:4000],
    )
    system = GEN_SYSTEM + "\n\n" + COT_PREFIX
    resp = qwen.generate(prompt=user_prompt, system_prompt=system, format="json")
    return parse_requirements_json(resp.content), resp.content


def gen_few_shot(story, context, qwen):
    """Idea 15 — exemplars prepended."""
    user_prompt = FEW_SHOT_EXEMPLARS + "\n" + GEN_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        acceptance_criteria=_ac_text(story), context=context[:4000],
    )
    resp = qwen.generate(prompt=user_prompt, system_prompt=GEN_SYSTEM, format="json")
    return parse_requirements_json(resp.content), resp.content


def gen_self_critique_refine(story, context, qwen, llama):
    """Idea 13 — three-step: generate → critique with llama3 → refine with qwen3."""
    # Pass 1: initial generation
    reqs1, raw1 = gen_baseline(story, context, qwen)
    if not reqs1:
        return [], raw1

    # Pass 2: critique with llama3:8b (different family)
    reqs_text = "\n".join(f"{i+1}. {r.get('title', '?')}: {r.get('description', '')[:200]}"
                          for i, r in enumerate(reqs1))
    crit_user = CRITIQUE_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        requirements=reqs_text,
    )
    try:
        crit_resp = llama.generate(prompt=crit_user, system_prompt=CRITIQUE_SYSTEM, format="json")
        verdicts = json.loads(crit_resp.content.strip()).get("verdicts", [])
    except Exception as e:
        return reqs1, raw1   # if critique fails, fall back to initial

    # If everything is strong, no need to refine
    if all(v == "strong" for v in verdicts) or len(verdicts) != len(reqs1):
        return reqs1, raw1

    # Pass 3: refine only the weak/wrong
    annotated = "\n".join(
        f"{i+1}. [{verdicts[i] if i < len(verdicts) else '?'}] "
        f"{r.get('title', '?')}: {r.get('description', '')[:200]}"
        for i, r in enumerate(reqs1)
    )
    ref_user = REFINE_USER_TEMPLATE.format(
        story_id=story["id"], title=story["title"], story=story["story"],
        annotated=annotated,
    )
    try:
        ref_resp = qwen.generate(prompt=ref_user, system_prompt=REFINE_SYSTEM, format="json")
        reqs2 = parse_requirements_json(ref_resp.content)
        if reqs2:
            return reqs2, ref_resp.content
    except Exception:
        pass
    return reqs1, raw1


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

STRATEGY_FOR_IDEA = {
    13: ("self_critique_refine", gen_self_critique_refine),
    14: ("chain_of_thought",     gen_chain_of_thought),
    15: ("few_shot_exemplars",   gen_few_shot),
}


def main():
    out_path = Path(os.environ.get("ABLATION_GEN_OUT", "/tmp/rag_ablation_generation.json"))
    project_filter = os.environ.get("ABLATION_PROJECTS", "").split(",") if os.environ.get("ABLATION_PROJECTS") else None
    idea_filter = {int(x) for x in os.environ.get("ABLATION_IDEAS", "").split(",")} if os.environ.get("ABLATION_IDEAS") else None
    stories_per_project = int(os.environ.get("STORIES_PER_PROJECT", str(STORIES_PER_PROJECT)))

    projects_to_run = [p for p in PROJECTS if not project_filter or p in project_filter]
    ideas_to_run = [(iid, n, fn) for iid, (n, fn) in STRATEGY_FOR_IDEA.items()
                    if not idea_filter or iid in idea_filter]

    print(f"Projects: {projects_to_run}")
    print(f"Ideas:    {[i for i, _, _ in ideas_to_run]}")
    print(f"Stories per project: {stories_per_project}")
    print()

    cfg = SecReqGenConfig()
    embedder = DocumentEmbedder()
    vs = SecReqGenVectorStore(persist_directory=VECTORDB)
    reranker = CrossEncoderReranker()
    qwen = OllamaClient(
        model_name="qwen3:30b-a3b-instruct-2507-q4_K_M",
        temperature=0.1, max_tokens=1500, timeout=300.0,
    )
    llama = OllamaClient(
        model_name="llama3:8b", temperature=0.0, max_tokens=300, timeout=120.0,
    )

    results = []
    t0 = time.perf_counter()
    # Re-ingest each project once (the WINNING Phase A chunker config)
    for project in projects_to_run:
        docs_dir, stories_path = PROJECTS[project]
        if not docs_dir.exists():
            print(f"  ! project {project}: docs_dir missing, skipping")
            continue
        print(f"[{project}] ingesting docs...", end=" ", flush=True)
        nc = ingest_project(docs_dir, embedder, vs)
        print(f"{nc} chunks")

        retriever = SimpleRetriever(
            vs, embedder, top_k=cfg.retrieval.top_k,
            similarity_threshold=cfg.retrieval.similarity_threshold,
            reranker=reranker, rerank_candidates=30,
        )

        stories = load_user_stories(stories_path)[:stories_per_project]

        for s in stories:
            # Retrieve once per story, reuse across ideas
            context = retrieve_context(s, retriever)

            for idea_id, idea_name, fn in ideas_to_run:
                tt = time.perf_counter()
                print(f"  [{project}/{s['id']}] idea {idea_id} {idea_name} ... ", end="", flush=True)
                try:
                    if idea_id == 13:
                        reqs, raw = fn(s, context, qwen, llama)
                    else:
                        reqs, raw = fn(s, context, qwen)
                    elapsed = time.perf_counter() - tt
                    print(f"{len(reqs)} reqs ({elapsed:.0f}s)")
                except Exception as e:
                    reqs, raw, elapsed = [], f"ERROR: {e}", time.perf_counter() - tt
                    print(f"ERROR: {e}")

                results.append({
                    "idea_id": idea_id, "idea_name": idea_name,
                    "project": project, "story_id": s["id"],
                    "story_title": s["title"],
                    "user_story": s,
                    "retrieval_context": context[:6000],   # truncate for storage
                    "generated_requirements": reqs,
                    "raw_response": raw[:3000],
                    "elapsed_sec": elapsed,
                })
                out_path.write_text(json.dumps(results, indent=2))

    print(f"\nWrote {out_path}")
    print(f"Total wall time: {(time.perf_counter() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
