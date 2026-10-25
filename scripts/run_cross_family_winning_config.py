"""Cross-family thesis experiment under the winning RAG configuration.

This is the authoritative empirical replacement for the pre-audit
cross-family runs. It applies the 2026-05-19 ablation winners:

  Retrieval: idea 19 (hybrid doc + code agent)
    - Dense + CE-rerank doc retrieval (via SimpleRetriever with the
      CrossEncoderReranker added in the 2026-05-19 audit-fix)
    - Code-grep agent (mistral-small3.2:24b emits 3 grep patterns
      against the project source repo; matching code snippets become
      pseudo-chunks)
    - Doc and code chunks interleaved into final context

  Generation: idea 15 (few-shot exemplars)
    - Two hand-curated worked examples prepended to the user prompt
    - Exemplars deliberately drawn from generic ASVS practice (NOT
      from any evaluation project) to avoid contamination
    - Loaded via the existing PromptBuilder's prompts_dir mechanism
      from data/winning_prompts/

Models evaluated (cross-family, role-segregated):
  qwen3:30b-a3b-instruct-2507-q4_K_M  (the main baseline)
  qwen3.6:latest                        (within-family cross-test)
  gemma4:26b                            (out-of-family)
  mistral-small3.2:24b                  (out-of-family)
                                        (separate from agent role
                                        in the 4-of-4 model run)

For the mistral-small generator case, the agent role uses qwen3.6
instead to maintain different-family hygiene; the agent role is
otherwise mistral-small3.2:24b throughout.

Output format matches the existing `compare-runs` pilots:
  real_test_cases/<project>/results_compare_runs_<label>_n<N>/
    run_comparison_<timestamp>/
      multi_run_report.json
      raw_runs/run_001.json … run_NNN.json

Smoke-test launch:
  PYTHONPATH=src .venv/bin/python scripts/run_cross_family_winning_config.py \\
    --models qwen3:30b-a3b-instruct-2507-q4_K_M --projects mastodon --n 2

Full launch (16–40 hours):
  PYTHONPATH=src .venv/bin/python scripts/run_cross_family_winning_config.py \\
    --models qwen3:30b-a3b-instruct-2507-q4_K_M,qwen3.6:latest,gemma4:26b,mistral-small3.2:24b \\
    --projects mastodon,keycloak,bitwarden,gitlab,mattermost \\
    --n 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from secreqgen.config import SecReqGenConfig
from secreqgen.evaluation import (
    BaselineRunner,
    EvaluationPipeline,
    build_multi_run_report,
    run_multi_evaluation,
    serialize_individual_result,
    serialize_report,
)
from secreqgen.generation.llm_client import OllamaClient
from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder
from secreqgen.generation.schemas import UserStory
from secreqgen.ingestion import DocumentEmbedder, ArchitectureAwareChunker
from secreqgen.ingestion.loader import MarkdownLoader
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
from secreqgen.retrieval.reranker import CrossEncoderReranker
from secreqgen.retrieval.retriever import RetrievalResult
from secreqgen.retrieval.vectorstore import QueryResult


# ─────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────

THESIS_ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project")
REAL_TEST_CASES = THESIS_ROOT / "real_test_cases"
SECREQGEN = THESIS_ROOT / "secreqgen"
VECTORDB = SECREQGEN / "data" / "vectordb"
WINNING_PROMPTS = SECREQGEN / "data" / "winning_prompts"
REPOS = REAL_TEST_CASES / "repos"

PROJECTS = {
    "mastodon":   (REAL_TEST_CASES / "mastodon",   REAL_TEST_CASES / "mastodon/docs"),
    "keycloak":   (REAL_TEST_CASES / "keycloak",   REAL_TEST_CASES / "extracted_docs_md/keycloak"),
    "bitwarden":  (REAL_TEST_CASES / "bitwarden",  REAL_TEST_CASES / "bitwarden-test-data"),
    "gitlab":     (REAL_TEST_CASES / "gitlab",     REAL_TEST_CASES / "gitlab/docs"),
    "mattermost": (REAL_TEST_CASES / "mattermost", REAL_TEST_CASES / "mattermost/docs"),
}

# Project key → repo directory name under REPOS (when they differ).
# bitwarden's source repo on disk is `bitwarden-server`, not `bitwarden`;
# without this mapping the code-search agent silently returned 0 chunks
# for all bitwarden cells in the 2026-05-20 cross-family run.
REPO_DIR_OVERRIDE = {
    "bitwarden": "bitwarden-server",
}


# ─────────────────────────────────────────────────────────────────────
# Idea 19 hybrid retriever (doc + code agent)
# ─────────────────────────────────────────────────────────────────────

CODE_AGENT_SYSTEM = """You are a code-search assistant for a security architect.
You will be given a user story and you must emit exactly 3 grep patterns that
would help find security-relevant code in the project's source repository.

- Each pattern should be a regex compatible with `ripgrep` (or POSIX grep -E).
- Aim for ~one pattern per security concern in the story (auth, validation,
  storage, etc.).
- Patterns should match function names, class names, or keywords that would
  appear in code dealing with the concern. Do not match documentation prose.

Output STRICTLY:
{"patterns": ["pat1", "pat2", "pat3"]}"""

CODE_AGENT_USER_TEMPLATE = """USER STORY: [{story_id}] {title}
{story}
Acceptance criteria:
{acceptance_criteria}

Output JSON only."""


def _make_pseudo_chunk(content: str, source: str, idx: int, score: float = 0.5) -> QueryResult:
    """Construct a QueryResult-shaped object from code-grep hits."""
    return QueryResult(
        chunk_id=f"agent-{idx:03d}",
        content=content,
        source_document=source,
        section_hierarchy=[source.split("/")[-1]],
        distance=2 * (1.0 - score),
        metadata={"agent_source": True},
    )


def _code_search(story: dict, project: str, agent_llm: OllamaClient,
                 max_hits_per_pattern: int = 3) -> list[QueryResult]:
    """Have mistral-small emit grep patterns; run them against repos/<project>/."""
    repo_name = REPO_DIR_OVERRIDE.get(project, project)
    repo_dir = REPOS / repo_name
    if not repo_dir.exists():
        return []

    ac = "\n".join(f"- {x}" for x in story.get("acceptance_criteria", []))
    user_prompt = CODE_AGENT_USER_TEMPLATE.format(
        story_id=story.get("id", "?"),
        title=story.get("title", "?"),
        story=story.get("story", "?"),
        acceptance_criteria=ac or "(none)",
    )
    try:
        resp = agent_llm.generate(prompt=user_prompt, system_prompt=CODE_AGENT_SYSTEM, format="json")
        obj = json.loads(resp.content.strip())
        patterns = [p for p in obj.get("patterns", []) if isinstance(p, str) and p.strip()]
    except Exception as e:
        print(f"    [code-agent error] {e}")
        return []
    if not patterns:
        return []

    # Use plain `grep -rEnI` to match the original rag_ablation_agents.py
    # implementation (ripgrep is not installed on this host).
    import subprocess
    source_exts = (".py", ".rb", ".go", ".ts", ".tsx", ".js", ".jsx", ".java", ".rs",
                   ".cs", ".php", ".scala", ".kt", ".swift", ".cpp", ".c", ".h", ".hpp")
    include_args: list[str] = []
    for ext in source_exts:
        include_args.extend(["--include", f"*{ext}"])

    chunks: list[QueryResult] = []
    for i, pat in enumerate(patterns[:3]):
        try:
            proc = subprocess.run(
                ["grep", "-rEnI", f"--max-count={max_hits_per_pattern}",
                 *include_args, pat, str(repo_dir)],
                capture_output=True, text=True, timeout=20,
            )
            for line in proc.stdout.splitlines()[:max_hits_per_pattern]:
                if not line.strip():
                    continue
                # grep output: <path>:<line>:<content>
                import re as _re
                m = _re.match(r"^(.+?):(\d+):(.*)$", line)
                if not m:
                    continue
                file_path, lineno, snippet = m.groups()
                rel_path = (Path(file_path).relative_to(repo_dir)
                            if file_path.startswith(str(repo_dir))
                            else Path(file_path))
                source = f"code:{rel_path}:{lineno}"
                content = (
                    f"### code search hit (pattern: {pat!r})\n"
                    f"File: {rel_path}:{lineno}\n"
                    f"```\n{snippet.strip()[:600]}\n```\n"
                )
                chunks.append(_make_pseudo_chunk(
                    content=content, source=source,
                    idx=len(chunks) + 1, score=0.55,
                ))
        except subprocess.TimeoutExpired:
            print(f"    [code-search timeout on pattern {pat!r}]")
            continue
        except Exception as e:
            print(f"    [code-search error on pattern {pat!r}: {e}]")
            continue
    return chunks[:5]


class Idea19Retriever:
    """Hybrid doc + code retriever matching idea 19 from the 2026-05-19 ablation.

    Duck-types SimpleRetriever's interface: retrieve_for_user_story + format_context.
    Does NOT subclass to avoid infinite recursion when composing the inner
    SimpleRetriever for the doc side.
    """

    def __init__(self, doc_retriever: SimpleRetriever, agent_llm: OllamaClient,
                 project: str, max_chunks: int = 8) -> None:
        self.doc_retriever = doc_retriever
        self.agent_llm = agent_llm
        self.project = project
        self.max_chunks = max_chunks
        # Cache embedder/vectorstore references for compatibility with code that
        # introspects the retriever (e.g., system-context lookup).
        self.embedder = doc_retriever.embedder
        self.vectorstore = doc_retriever.vectorstore
        self.top_k = doc_retriever.top_k
        self.similarity_threshold = doc_retriever.similarity_threshold

    def retrieve_for_user_story(self, user_story: UserStory,
                                 top_k: Optional[int] = None) -> RetrievalResult:
        t0 = time.perf_counter()
        # Doc side via the inner SimpleRetriever (CE-rerank already wired)
        doc_result = self.doc_retriever.retrieve_for_user_story(user_story, top_k=self.top_k)
        doc_chunks = doc_result.chunks

        # Code side via the agent
        story_dict = {
            "id": user_story.id, "title": user_story.title,
            "story": user_story.story,
            "acceptance_criteria": list(user_story.acceptance_criteria or []),
        }
        code_chunks = _code_search(story_dict, self.project, self.agent_llm)

        # Interleave: doc1, code1, doc2, code2, ...
        interleaved: list[QueryResult] = []
        for d, c in zip(doc_chunks, code_chunks):
            interleaved.append(d)
            interleaved.append(c)
        # Append the tail of whichever was longer
        remaining = doc_chunks[len(code_chunks):] + code_chunks[len(doc_chunks):]
        interleaved.extend(remaining)
        chunks = interleaved[:self.max_chunks]

        retrieval_time_ms = (time.perf_counter() - t0) * 1000
        return RetrievalResult(
            chunks=chunks, query_text=user_story.title,
            total_retrieved=len(doc_chunks) + len(code_chunks),
            above_threshold=len(chunks),
            retrieval_time_ms=retrieval_time_ms,
        )

    def retrieve(self, query: str, top_k: Optional[int] = None,
                 doc_types=None) -> RetrievalResult:
        # Plain text query: just delegate to the doc retriever; code agent
        # needs a user story for context.
        return self.doc_retriever.retrieve(query, top_k=top_k, doc_types=doc_types)

    def format_context(self, chunks) -> str:
        return self.doc_retriever.format_context(chunks)

    def format_result_for_prompt(self, result: RetrievalResult) -> str:
        """The generator uses this name to format a RetrievalResult."""
        return self.doc_retriever.format_context(result.chunks)

    def get_system_context(self):
        return self.doc_retriever.get_system_context()


# ─────────────────────────────────────────────────────────────────────
# Ingestion (clears + repopulates vector store per project)
# ─────────────────────────────────────────────────────────────────────

def _ingest_project(docs_dir: Path, embedder, vectorstore) -> int:
    vectorstore.clear()
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
    embeddings = embedder.embed_texts([c.content for c in chunks])
    vectorstore.add_chunks(chunks, embeddings, DocumentType.ARCHITECTURE)
    return len(chunks)


# ─────────────────────────────────────────────────────────────────────
# Run one (model × project) cell
# ─────────────────────────────────────────────────────────────────────

def _slug(model: str) -> str:
    return model.replace(":", "_").replace(".", "_").replace("/", "_")


def run_cell(model: str, project: str, n_runs: int,
             match_threshold: float, partial_threshold: float,
             label_prefix: str, output_root: Path) -> None:
    proj_root, docs_dir = PROJECTS[project]
    stories_path = proj_root / "user_stories.json"
    ground_truth_path = proj_root / "ground_truth.json"
    system_context_path = proj_root / "system_context.yaml"

    for required in (docs_dir, stories_path, ground_truth_path):
        if not required.exists():
            print(f"  [{project}] missing required input: {required}; SKIPPING cell")
            return

    out_dir_name = f"results_compare_runs_{label_prefix}_{_slug(model)}_n{n_runs}"
    output_dir = proj_root / out_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / f"run_comparison_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw_runs").mkdir(exist_ok=True)

    cfg = SecReqGenConfig()
    print(f"\n=== [{model}] × [{project}] N={n_runs} ===")
    print(f"  output: {run_dir}")

    # Generator LLM (the model under test)
    llm_client = OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=model,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
    )
    if not llm_client.check_health():
        print(f"  ! Ollama health check failed for {model}; SKIPPING cell")
        return

    # Agent LLM (different family from generator)
    # Default: mistral-small3.2:24b. If the generator IS mistral-small, fall back to qwen3.6.
    if "mistral" in model.lower():
        agent_model = "qwen3.6:latest"
    else:
        agent_model = "mistral-small3.2:24b"
    print(f"  agent role uses: {agent_model}")
    agent_llm = OllamaClient(
        model_name=agent_model, temperature=0.1, max_tokens=400, timeout=300.0,
    )

    # Embedder + vector store + ingestion
    embedder = DocumentEmbedder(
        model_name=cfg.embedding.model_name, device=cfg.embedding.device,
    )
    vectorstore = SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )
    print(f"  ingesting {docs_dir} ...", end=" ", flush=True)
    n_chunks = _ingest_project(docs_dir, embedder, vectorstore)
    print(f"{n_chunks} chunks")

    if n_chunks == 0:
        print(f"  ! no chunks; SKIPPING cell")
        return

    # Doc retriever (CE-rerank-augmented SimpleRetriever) + idea-19 hybrid wrapper
    reranker = CrossEncoderReranker()
    doc_retriever = SimpleRetriever(
        vectorstore=vectorstore, embedder=embedder,
        top_k=cfg.retrieval.top_k,
        similarity_threshold=cfg.retrieval.similarity_threshold,
        reranker=reranker, rerank_candidates=30,
    )
    retriever = Idea19Retriever(
        doc_retriever=doc_retriever, agent_llm=agent_llm, project=project,
    )

    # System context (optional)
    system_context = None
    if system_context_path.exists():
        system_context = system_context_path.read_text()

    # Build the pipeline; monkey-patch the prompt builders to use winning_prompts
    pipeline = EvaluationPipeline(
        llm_client=llm_client, retriever=retriever,
        match_threshold=match_threshold, partial_threshold=partial_threshold,
        system_context=system_context,
    )
    winning_pb = SecurityRequirementsPromptBuilder(prompts_dir=WINNING_PROMPTS)
    pipeline._runner._baseline_generator.prompt_builder = winning_pb
    if pipeline._runner._rag_generator is not None:
        pipeline._runner._rag_generator.prompt_builder = winning_pb
    print(f"  prompt builder: winning_prompts ({WINNING_PROMPTS})")

    # Run N evaluations
    print(f"  running {n_runs} paired baseline+RAG evaluations...")

    def progress_cb(current: int, total: int, phase: str) -> None:
        if phase.startswith("completed"):
            print(f"    run {current}/{total} complete")

    individual_results = run_multi_evaluation(
        pipeline=pipeline, stories_path=stories_path,
        ground_truth_path=ground_truth_path, n_runs=n_runs,
        progress_callback=progress_cb,
    )

    # Save raw runs for qualitative inspection
    for i, result in enumerate(individual_results, start=1):
        raw_path = run_dir / "raw_runs" / f"run_{i:03d}.json"
        raw_path.write_text(serialize_individual_result(result))

    # Build + save aggregate report
    report_config = {
        "stories": str(stories_path),
        "ground_truth": str(ground_truth_path),
        "model": model,
        "match_threshold": match_threshold,
        "partial_threshold": partial_threshold,
        "n_runs": n_runs,
        "pilot": False,
        "config_label": f"{label_prefix}_idea19_idea15",
        "agent_model": agent_model,
        "n_chunks_ingested": n_chunks,
    }
    report = build_multi_run_report(individual_results, report_config)
    (run_dir / "multi_run_report.json").write_text(serialize_report(report))

    # Quick console summary
    n_ok = sum(1 for r in individual_results if r is not None)
    print(f"  ✓ {n_ok}/{n_runs} runs completed; report at {run_dir / 'multi_run_report.json'}")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=str, required=True,
                    help="Comma-separated generator models.")
    ap.add_argument("--projects", type=str, required=True,
                    help="Comma-separated projects (mastodon, keycloak, bitwarden, gitlab, mattermost).")
    ap.add_argument("--n", type=int, default=10, help="Number of paired runs per cell.")
    ap.add_argument("--match-threshold", type=float, default=0.85)
    ap.add_argument("--partial-threshold", type=float, default=0.70)
    ap.add_argument("--label-prefix", type=str, default="winning",
                    help="Prefix for output directory naming.")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    unknown = [p for p in projects if p not in PROJECTS]
    if unknown:
        print(f"Unknown projects: {unknown}. Valid: {list(PROJECTS.keys())}")
        sys.exit(1)

    print(f"Models:   {models}")
    print(f"Projects: {projects}")
    print(f"N:        {args.n}")
    print(f"Total cells: {len(models) * len(projects)}")
    print(f"Estimated time per cell: 30 minutes – 5 hours depending on N and project size.")
    print()

    t0 = time.perf_counter()
    for model in models:
        for project in projects:
            try:
                run_cell(
                    model=model, project=project, n_runs=args.n,
                    match_threshold=args.match_threshold,
                    partial_threshold=args.partial_threshold,
                    label_prefix=args.label_prefix,
                    output_root=REAL_TEST_CASES,
                )
            except Exception as e:
                print(f"  ! cell [{model}] × [{project}] crashed: {type(e).__name__}: {e}")
                import traceback; traceback.print_exc()
    elapsed_min = (time.perf_counter() - t0) / 60
    print(f"\nAll cells done. Total wall time: {elapsed_min:.1f} min.")


if __name__ == "__main__":
    main()
