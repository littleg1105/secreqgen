"""Study 2 driver — four-arm, frozen-retrieval, documentation-dependence experiment.

Design (MASTER_PLAN_2026-11-02.md §1; pre-registration in
docs/RAG_ABLATION_LAB_NOTEBOOK.md §14):

  Arms, identical system prompt / exemplars / requirement budget everywhere,
  differing only in the project information supplied:
    A  no project documents                (zero-shot template)
    B  RAG: frozen idea-19 passages        (rag template)
    C  B + extract-then-cite (idea 21)     (same frozen passages)
    D  whole documentation corpus in context (rag template, large num_ctx)

  Retrieval is run ONCE per (project, generator model, story) and the same
  passages are reused by B and C across all repeats, so C-vs-B isolates the
  extraction step. The code-search agent and the Stage-A extractor run on
  the *other* generator family (CROSS_FAMILY).

  Every record stores the non-documentation part of the prompt verbatim, so
  the analyzer can subtract anything the prompt itself supplied before
  counting a citation as documentation-attributable (the §13 lesson).

Output: real_test_cases/study2/<project>/results/<model_slug>/
    manifest.json           models, digests, prompt hashes, config
    retrieval_frozen.json   per-story frozen context (B/C) + chunk stats
    corpus_D.txt            the exact corpus text given to arm D (if run)
    records.jsonl           one line per (story, arm, run); resumable

Smoke:
  TOKENIZERS_PARALLELISM=false PYTHONPATH=src .venv/bin/python scripts/run_study2.py \
      --project mastodon --models qwen3.8:27b --stories MT-ADV-001 --n 1
Full (one project, both models; never run two drivers at once):
  ... --project keycloak --models qwen3.8:27b,gemma4:26b --n 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from run_cross_family_winning_config import (  # noqa: E402
    Idea19Retriever,
    _ingest_project,
    _slug,
)
from secreqgen.config import SecReqGenConfig  # noqa: E402
from secreqgen.generation.generator import (  # noqa: E402
    GenerationMode,
    SecurityRequirementsGenerator,
)
from secreqgen.generation.grounded_citation import Idea21Generator  # noqa: E402
from secreqgen.generation.llm_client import LLMResponse, OllamaClient  # noqa: E402
from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder  # noqa: E402
from secreqgen.generation.schemas import UserStory  # noqa: E402
from secreqgen.ingestion import DocumentEmbedder  # noqa: E402
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever  # noqa: E402
from secreqgen.retrieval.reranker import CrossEncoderReranker  # noqa: E402
from secreqgen.retrieval.retriever import RetrievalResult  # noqa: E402

THESIS_ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project")
REAL_TEST_CASES = THESIS_ROOT / "real_test_cases"
STUDY2 = REAL_TEST_CASES / "study2"
SECREQGEN = THESIS_ROOT / "secreqgen"
PROMPTS_DIR = SECREQGEN / "data" / "winning_prompts_idea21"

# Generator -> agent/extractor model. Roles stay in different families.
CROSS_FAMILY = {
    "qwen3.8:27b": "gemma4:26b",
    "gemma4:26b": "qwen3.8:27b",
}

ARMS = {
    "A": "no project documents",
    "B": "RAG, frozen idea-19 passages, idea-15 exemplars",
    "C": "RAG + extract-then-cite (idea 21), same frozen passages",
    "D": "whole documentation corpus in context",
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def est_tokens(text: str) -> int:
    """Rough token estimate (4 bytes/token) used only for the arm-D fit check;
    the record stores Ollama's real prompt_eval_count."""
    return len(text.encode("utf-8")) // 4


class RecordingOllamaClient(OllamaClient):
    """OllamaClient that logs every call so token counts and retries can be
    attributed to the generation that triggered them."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.call_log: list[dict] = []

    def generate(self, prompt: str, system_prompt: str | None = None,
                 format: str | None = None) -> LLMResponse:
        response = super().generate(prompt, system_prompt=system_prompt, format=format)
        self.call_log.append({
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "latency_ms": response.latency_ms,
        })
        return response

    def take_log(self) -> list[dict]:
        log, self.call_log = self.call_log, []
        return log


class FrozenRetriever:
    """Duck-typed retriever that returns pre-computed passages.

    Arms B and C (and D, with the corpus as its single 'passage') get exactly
    the same documentation text for a story on every repeat, so differences
    between arms and runs are generation-side only.
    """

    def __init__(self, results: dict[str, RetrievalResult], contexts: dict[str, str],
                 system_context: str | None) -> None:
        self._results = results
        self._contexts = contexts
        self._system_context = system_context
        self._context_by_result = {id(r): contexts[sid] for sid, r in results.items()}

    def retrieve_for_user_story(self, user_story: UserStory,
                                top_k: int | None = None) -> RetrievalResult:
        return self._results[user_story.id]

    def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        raise NotImplementedError("FrozenRetriever serves per-story contexts only")

    def format_context(self, chunks) -> str:  # pragma: no cover - not used by generators
        raise NotImplementedError

    def format_result_for_prompt(self, result: RetrievalResult) -> str:
        return self._context_by_result[id(result)]

    def get_system_context(self) -> str | None:
        return self._system_context


def empty_result(query_text: str) -> RetrievalResult:
    return RetrievalResult(chunks=[], query_text=query_text, total_retrieved=0,
                           above_threshold=0, retrieval_time_ms=0.0)


def load_stories(project: str, only: set[str] | None) -> tuple[dict, list[UserStory]]:
    data = json.loads((STUDY2 / project / "stories.json").read_text())
    stories = [
        UserStory(id=s["id"], title=s["title"], story=s["story"],
                  acceptance_criteria=list(s["acceptance_criteria"]))
        for s in data["stories"]
        if only is None or s["id"] in only
    ]
    return data, stories


def build_corpus(docs_dir: Path) -> str:
    parts = []
    for f in sorted(docs_dir.rglob("*.md")):
        if f.is_file():
            parts.append(f"### FILE: {f.relative_to(docs_dir)}\n{f.read_text(errors='ignore')}")
    return "\n\n".join(parts)


def ollama_digests(base_url: str) -> dict[str, str]:
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=10.0)
        r.raise_for_status()
        return {m["name"]: m.get("digest", "") for m in r.json().get("models", [])}
    except Exception as e:  # pragma: no cover - manifest only
        return {"error": str(e)}


def ollama_version(base_url: str) -> str:
    try:
        return httpx.get(f"{base_url}/api/version", timeout=5.0).json().get("version", "?")
    except Exception:
        return "unreachable"


def requirement_dicts(result) -> list[dict]:
    return [r.model_dump(mode="json") for r in result.requirements]


# ─────────────────────────────────────────────────────────────────────
# One (project, model) cell
# ─────────────────────────────────────────────────────────────────────

def run_cell(project: str, model: str, stories: list[UserStory], dataset: dict,
             arms: list[str], n_runs: int, num_ctx: int, num_ctx_d: int,
             cfg: SecReqGenConfig, embedder, vectorstore, n_chunks: int,
             dry_run: bool) -> None:
    out_dir = STUDY2 / project / "results" / _slug(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    records_path = out_dir / "records.jsonl"
    docs_dir = REAL_TEST_CASES / dataset["docs_dir"]
    system_context_path = REAL_TEST_CASES / project / "system_context.yaml"
    system_context = system_context_path.read_text() if system_context_path.exists() else None

    agent_model = CROSS_FAMILY.get(model)
    if agent_model is None:
        print(f"  ! no cross-family agent mapped for {model}; SKIPPING cell")
        return
    print(f"\n=== Study 2 · [{project}] × [{model}] · agent/extractor={agent_model} · arms={''.join(arms)} · N={n_runs} ===")
    print(f"  output: {out_dir}")

    pb = SecurityRequirementsPromptBuilder(prompts_dir=PROMPTS_DIR)
    prompt_hashes = {p.name: sha256_text(p.read_text()) for p in sorted(PROMPTS_DIR.glob("*.txt"))}

    # Arm D fit check (done up front so the manifest records the decision)
    corpus = build_corpus(docs_dir)
    corpus_est = est_tokens(corpus)
    d_fits = int(corpus_est * 1.15) + 8000 <= num_ctx_d
    if "D" in arms and not d_fits:
        print(f"  arm D skipped: corpus ≈ {corpus_est:,} tokens does not fit num_ctx_d={num_ctx_d:,}")
    run_arms = [a for a in arms if a != "D" or d_fits]

    manifest = {
        "study": "study2", "project": project, "model": model, "agent_model": agent_model,
        "arms": {a: ARMS[a] for a in run_arms},
        "arm_D_skipped_reason": None if d_fits or "D" not in arms else f"corpus ~{corpus_est} tokens > num_ctx_d {num_ctx_d}",
        "n_runs": n_runs, "stories": [s.id for s in stories],
        "dataset_sha256": sha256_text((STUDY2 / project / "stories.json").read_text()),
        "dataset_status": dataset.get("status"),
        "prompts_dir": str(PROMPTS_DIR), "prompt_sha256": prompt_hashes,
        "docs_dir": str(docs_dir), "n_chunks_ingested": n_chunks,
        "corpus_sha256": sha256_text(corpus), "corpus_est_tokens": corpus_est,
        "config": {
            "num_ctx": num_ctx, "num_ctx_D": num_ctx_d,
            "temperature": cfg.llm.temperature, "max_tokens": cfg.llm.max_tokens,
            "top_k": cfg.retrieval.top_k, "similarity_threshold": cfg.retrieval.similarity_threshold,
            "rerank_candidates": 30, "embedding_model": cfg.embedding.model_name,
            "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        },
        "ollama": {"version": ollama_version(cfg.llm.base_url),
                   "digests": ollama_digests(cfg.llm.base_url)},
        "host": platform.node(), "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if "D" in run_arms:
        (out_dir / "corpus_D.txt").write_text(corpus)

    if dry_run:
        for s in stories:
            zs = pb.build_zero_shot_prompt(user_story=s, system_context=system_context)
            print(f"  [dry] {s.id}: zero-shot prompt ≈ {est_tokens(zs):,} tokens; corpus ≈ {corpus_est:,}")
        print("  [dry] manifest written; no model calls made")
        return

    # Clients
    llm = RecordingOllamaClient(base_url=cfg.llm.base_url, model_name=model,
                                temperature=cfg.llm.temperature, max_tokens=cfg.llm.max_tokens,
                                timeout=900.0, num_ctx=num_ctx)
    llm_d = RecordingOllamaClient(base_url=cfg.llm.base_url, model_name=model,
                                  temperature=cfg.llm.temperature, max_tokens=cfg.llm.max_tokens,
                                  timeout=3600.0, num_ctx=num_ctx_d)
    if not llm.check_health():
        print(f"  ! Ollama health check failed for {model}; SKIPPING cell")
        return
    agent_llm = OllamaClient(model_name=agent_model, temperature=0.1, max_tokens=400,
                             timeout=600.0, num_ctx=num_ctx)
    extractor_llm = RecordingOllamaClient(model_name=agent_model, temperature=0.1,
                                          max_tokens=1200, timeout=600.0, num_ctx=num_ctx)

    # Retrieval stack (idea 19) + freeze
    frozen_path = out_dir / "retrieval_frozen.json"
    frozen_results: dict[str, RetrievalResult] = {}
    frozen_contexts: dict[str, str] = {}
    frozen_meta: dict[str, dict] = {}
    if frozen_path.exists():
        saved = json.loads(frozen_path.read_text())
        for sid, entry in saved.items():
            frozen_contexts[sid] = entry["context"]
            frozen_results[sid] = empty_result(entry.get("query_text", ""))
            frozen_meta[sid] = entry
        print(f"  reusing frozen retrieval for {len(frozen_contexts)} stories")
    need_retrieval = [s for s in stories if s.id not in frozen_contexts and any(a in run_arms for a in "BC")]
    if need_retrieval:
        reranker = CrossEncoderReranker()
        doc_retriever = SimpleRetriever(
            vectorstore=vectorstore, embedder=embedder, top_k=cfg.retrieval.top_k,
            similarity_threshold=cfg.retrieval.similarity_threshold,
            reranker=reranker, rerank_candidates=30,
        )
        retriever = Idea19Retriever(doc_retriever=doc_retriever, agent_llm=agent_llm, project=project)
        for s in need_retrieval:
            t0 = time.perf_counter()
            result = retriever.retrieve_for_user_story(s)
            context = retriever.format_result_for_prompt(result)
            ms = (time.perf_counter() - t0) * 1000
            n_code = sum(1 for c in result.chunks if "code" in str(getattr(c, "metadata", {})).lower())
            frozen_results[s.id] = result
            frozen_contexts[s.id] = context
            frozen_meta[s.id] = {
                "query_text": result.query_text, "n_chunks": len(result.chunks),
                "n_code_chunks_guess": n_code, "above_threshold": result.above_threshold,
                "retrieval_ms": round(ms, 1), "context_chars": len(context),
                "context_sha256": sha256_text(context), "context": context,
            }
            flag = "  !! 41-char fallback" if len(context) <= 41 else ""
            print(f"  retrieval {s.id}: {len(result.chunks)} chunks, {len(context):,} chars, {ms/1000:.1f}s{flag}")
        frozen_path.write_text(json.dumps(frozen_meta, indent=1))

    # Resume support
    done: set[tuple[str, str, int]] = set()
    if records_path.exists():
        for line in records_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if not r.get("error"):
                    done.add((r["story_id"], r["arm"], r["run"]))
        if done:
            print(f"  resuming: {len(done)} records already present")

    frozen = FrozenRetriever(frozen_results, frozen_contexts, system_context)
    corpus_results = {s.id: empty_result(f"corpus:{project}") for s in stories}
    corpus_ctx = {s.id: corpus for s in stories}
    frozen_corpus = FrozenRetriever(corpus_results, corpus_ctx, system_context)

    gens = {
        "A": SecurityRequirementsGenerator(llm_client=llm, retriever=None, prompt_builder=pb),
        "B": SecurityRequirementsGenerator(llm_client=llm, retriever=frozen, prompt_builder=pb),
        "C": Idea21Generator(llm_client=llm, extractor_llm=extractor_llm, retriever=frozen, prompt_builder=pb),
        "D": SecurityRequirementsGenerator(llm_client=llm_d, retriever=frozen_corpus, prompt_builder=pb),
    }
    system_prompt = pb.build_system_prompt()

    total = len(run_arms) * len(stories) * n_runs
    i = 0
    with records_path.open("a") as out:
        for arm in run_arms:  # arm-major so the model is reloaded once for D's larger window
            for s in stories:
                non_doc_prompt = (
                    pb.build_zero_shot_prompt(user_story=s, system_context=system_context)
                    if arm == "A" else
                    pb.build_rag_prompt(user_story=s, retrieved_context="", system_context=system_context)
                )
                for run in range(1, n_runs + 1):
                    i += 1
                    if (s.id, arm, run) in done:
                        continue
                    client = llm_d if arm == "D" else llm
                    client.take_log()
                    extractor_llm.take_log()
                    rec = {
                        "project": project, "model": model, "agent_model": agent_model,
                        "story_id": s.id, "arm": arm, "run": run,
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "prompt_system": system_prompt, "prompt_user_non_doc": non_doc_prompt,
                        "doc_context_ref": {"A": None, "B": "frozen", "C": "frozen+tokens", "D": "corpus_D.txt"}[arm],
                        "doc_context_sha256": None if arm == "A" else (sha256_text(corpus) if arm == "D" else frozen_meta[s.id]["context_sha256"]),
                    }
                    t0 = time.perf_counter()
                    try:
                        mode = GenerationMode.ZERO_SHOT if arm == "A" else GenerationMode.RAG
                        result = gens[arm].generate(s, mode=mode, system_context=system_context)
                        rec["requirements"] = requirement_dicts(result)
                        rec["n_requirements"] = len(result.requirements)
                        rec["n_threat_mappings"] = len(result.threat_mappings)
                        if arm == "C":
                            rec["extracted_tokens"] = list(gens["C"].last_extraction.get(s.id, []))
                            rec["extractor_calls"] = extractor_llm.take_log()
                        rec["error"] = None
                    except Exception as e:
                        rec["requirements"] = []
                        rec["n_requirements"] = 0
                        rec["error"] = f"{type(e).__name__}: {e}"
                        traceback.print_exc()
                    calls = client.take_log()
                    rec["llm_calls"] = calls
                    rec["prompt_tokens"] = calls[-1]["prompt_tokens"] if calls else None
                    rec["completion_tokens"] = sum(c["completion_tokens"] for c in calls)
                    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out.flush()
                    status = "ERR" if rec["error"] else f"{rec['n_requirements']} reqs"
                    print(f"  [{i}/{total}] {s.id} arm {arm} run {run}: {status}, "
                          f"{rec['prompt_tokens']} prompt tok, {rec['latency_ms']/1000:.0f}s")

    manifest["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"  ✓ cell complete → {records_path}")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, choices=["mastodon", "keycloak", "bitwarden"])
    ap.add_argument("--models", default="qwen3.8:27b,gemma4:26b")
    ap.add_argument("--arms", default="ABCD", help="subset of ABCD, e.g. ABC")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--stories", default=None, help="comma-separated story ids (smoke)")
    ap.add_argument("--num-ctx", type=int, default=16384, help="context window for arms A/B/C")
    ap.add_argument("--num-ctx-d", type=int, default=98304, help="context window for arm D")
    ap.add_argument("--dry-run", action="store_true", help="load everything, make no model calls")
    args = ap.parse_args()

    arms = [a for a in "ABCD" if a in args.arms.upper()]
    only = set(args.stories.split(",")) if args.stories else None
    dataset, stories = load_stories(args.project, only)
    if not stories:
        sys.exit("no stories selected")
    cfg = SecReqGenConfig()

    embedder = vectorstore = None
    n_chunks = 0
    if not args.dry_run and any(a in arms for a in "BC"):
        docs_dir = REAL_TEST_CASES / dataset["docs_dir"]
        embedder = DocumentEmbedder(model_name=cfg.embedding.model_name, device=cfg.embedding.device)
        vectorstore = SecReqGenVectorStore(persist_directory=cfg.vectorstore.persist_directory,
                                           collection_prefix=cfg.vectorstore.collection_prefix)
        print(f"ingesting {docs_dir} ...", end=" ", flush=True)
        n_chunks = _ingest_project(docs_dir, embedder, vectorstore)
        print(f"{n_chunks} chunks")
        if n_chunks == 0:
            sys.exit("no chunks ingested")

    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        try:
            run_cell(args.project, model, stories, dataset, arms, args.n, args.num_ctx,
                     args.num_ctx_d, cfg, embedder, vectorstore, n_chunks, args.dry_run)
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()
