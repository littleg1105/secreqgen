"""Adversarial probe: do the 5 mastodon stories that REQUIRE project-specific
knowledge show a real baseline-vs-RAG gap?

The hypothesis is that the regular 8 mastodon stories don't probe the
training-data ceiling — they're generic enough that the base LLM already
knows what to say. The 5 adversarial stories at
real_test_cases/mastodon_adversarial/ explicitly require Mastodon config
keys (CAS_UID_KEY, LIMITED_FEDERATION_MODE, S3_HOSTNAME/S3_ALIAS_HOST,
ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH, followers_synchronization). A base
LLM without retrieval should struggle on these; a RAG-equipped LLM should
land specific, grounded requirements.

This script reuses the cross-family driver's components: the idea-19
hybrid retriever + the winning_prompts few-shot template.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Import the driver's helpers so we get exactly the same retrieval + prompt
# behaviour as the main cross-family run.
sys.path.insert(0, str(Path(__file__).parent))
from run_cross_family_winning_config import (  # noqa: E402
    Idea19Retriever,
    _ingest_project,
    _slug,
    REPO_DIR_OVERRIDE,  # noqa: F401  (kept for parity)
    WINNING_PROMPTS,
)

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
from secreqgen.ingestion import DocumentEmbedder
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
from secreqgen.retrieval.reranker import CrossEncoderReranker


THESIS_ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project")
REAL_TEST_CASES = THESIS_ROOT / "real_test_cases"

# Adversarial probe configuration:
# - docs come from the regular mastodon corpus (no docs would mean nothing to retrieve)
# - stories + ground_truth come from the adversarial set
ADV_PROJECT_ROOT = REAL_TEST_CASES / "mastodon_adversarial"
DOCS_DIR = REAL_TEST_CASES / "mastodon" / "docs"
REPO_NAME = "mastodon"  # for code-search agent under repos/mastodon


def run_cell(model: str, n_runs: int, label_prefix: str,
             match_threshold: float = 0.85, partial_threshold: float = 0.70) -> None:
    stories_path = ADV_PROJECT_ROOT / "user_stories.json"
    ground_truth_path = ADV_PROJECT_ROOT / "ground_truth.json"

    for required in (DOCS_DIR, stories_path, ground_truth_path):
        if not required.exists():
            print(f"  missing required input: {required}; SKIPPING")
            return

    out_dir_name = f"results_compare_runs_{label_prefix}_{_slug(model)}_n{n_runs}"
    output_dir = ADV_PROJECT_ROOT / out_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / f"run_comparison_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw_runs").mkdir(exist_ok=True)

    cfg = SecReqGenConfig()
    print(f"\n=== [{model}] × [mastodon_adversarial] N={n_runs} ===")
    print(f"  output: {run_dir}")

    llm_client = OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=model,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
    )
    if not llm_client.check_health():
        print(f"  ! Ollama health check failed for {model}; SKIPPING")
        return

    if "mistral" in model.lower():
        agent_model = "qwen3.6:latest"
    else:
        agent_model = "mistral-small3.2:24b"
    print(f"  agent role uses: {agent_model}")
    agent_llm = OllamaClient(
        model_name=agent_model, temperature=0.1, max_tokens=400, timeout=300.0,
    )

    embedder = DocumentEmbedder(
        model_name=cfg.embedding.model_name, device=cfg.embedding.device,
    )
    vectorstore = SecReqGenVectorStore(
        persist_directory=cfg.vectorstore.persist_directory,
        collection_prefix=cfg.vectorstore.collection_prefix,
    )
    print(f"  ingesting {DOCS_DIR} ...", end=" ", flush=True)
    n_chunks = _ingest_project(DOCS_DIR, embedder, vectorstore)
    print(f"{n_chunks} chunks")

    if n_chunks == 0:
        print(f"  ! no chunks ingested; SKIPPING")
        return

    reranker = CrossEncoderReranker()
    doc_retriever = SimpleRetriever(
        vectorstore=vectorstore, embedder=embedder,
        top_k=cfg.retrieval.top_k,
        similarity_threshold=cfg.retrieval.similarity_threshold,
        reranker=reranker, rerank_candidates=30,
    )
    retriever = Idea19Retriever(
        doc_retriever=doc_retriever, agent_llm=agent_llm, project=REPO_NAME,
    )

    system_context_path = REAL_TEST_CASES / "mastodon" / "system_context.yaml"
    system_context = system_context_path.read_text() if system_context_path.exists() else None

    pipeline = EvaluationPipeline(
        llm_client=llm_client, retriever=retriever,
        match_threshold=match_threshold, partial_threshold=partial_threshold,
        system_context=system_context,
    )
    winning_pb = SecurityRequirementsPromptBuilder(prompts_dir=WINNING_PROMPTS)
    pipeline._runner._baseline_generator.prompt_builder = winning_pb
    if pipeline._runner._rag_generator is not None:
        pipeline._runner._rag_generator.prompt_builder = winning_pb
    print(f"  prompt builder: winning_prompts")
    print(f"  running {n_runs} paired baseline+RAG evaluations...")

    def progress_cb(current: int, total: int, phase: str) -> None:
        if phase.startswith("completed"):
            print(f"    run {current}/{total} complete")

    individual_results = run_multi_evaluation(
        pipeline=pipeline, stories_path=stories_path,
        ground_truth_path=ground_truth_path, n_runs=n_runs,
        progress_callback=progress_cb,
    )

    for i, result in enumerate(individual_results, start=1):
        raw_path = run_dir / "raw_runs" / f"run_{i:03d}.json"
        raw_path.write_text(serialize_individual_result(result))

    report_config = {
        "stories": str(stories_path),
        "ground_truth": str(ground_truth_path),
        "model": model,
        "match_threshold": match_threshold,
        "partial_threshold": partial_threshold,
        "n_runs": n_runs,
        "pilot": False,
        "config_label": f"{label_prefix}_adversarial_mastodon",
        "agent_model": agent_model,
        "n_chunks_ingested": n_chunks,
    }
    report = build_multi_run_report(individual_results, report_config)
    (run_dir / "multi_run_report.json").write_text(serialize_report(report))

    n_ok = sum(1 for r in individual_results if r is not None)
    print(f"  ✓ {n_ok}/{n_runs} runs completed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", type=str, required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--label-prefix", type=str, default="adversarial")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"Models:   {models}")
    print(f"N:        {args.n}")
    print(f"Adversarial project: {ADV_PROJECT_ROOT}")
    print(f"Docs source:         {DOCS_DIR}")
    print()

    t0 = time.perf_counter()
    for model in models:
        try:
            run_cell(model=model, n_runs=args.n, label_prefix=args.label_prefix)
        except Exception as e:
            print(f"  ! cell [{model}] crashed: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    elapsed_min = (time.perf_counter() - t0) / 60
    print(f"\nAll cells done. Total wall time: {elapsed_min:.1f} min.")


if __name__ == "__main__":
    main()
