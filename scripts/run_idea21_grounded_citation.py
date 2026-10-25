"""Idea 21 — grounded-citation generation on the mastodon adversarial probe.

This is the head-to-head test of the extract-then-cite RAG against the
idea-15 + idea-19 baseline (already captured in
real_test_cases/mastodon_adversarial/results_compare_runs_adversarial_*).

Pipeline:
- Retrieval: idea 19 hybrid (doc CE-rerank + code-grep agent), reused
  verbatim from run_cross_family_winning_config.
- Generation:
    baseline arm  -> standard zero-shot path (unchanged, for paired
                     baseline comparison)
    RAG arm       -> Idea21Generator
                     Stage A: extract project-specific tokens from
                              retrieved context
                     Stage B: prompt-builder with citation-aware
                              exemplars + token list section

Output directory layout matches the existing adversarial probe so the
analysis script can ingest both.

Usage (smoke test, 1 run on qwen3):
    PYTHONPATH=src .venv/bin/python scripts/run_idea21_grounded_citation.py \\
        --models qwen3:30b-a3b-instruct-2507-q4_K_M --n 1

Head-to-head (N=5 on qwen3 + qwen3.6):
    PYTHONPATH=src .venv/bin/python scripts/run_idea21_grounded_citation.py \\
        --models qwen3:30b-a3b-instruct-2507-q4_K_M,qwen3.6:latest --n 5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_cross_family_winning_config import (  # noqa: E402
    Idea19Retriever,
    _ingest_project,
    _slug,
)

from secreqgen.config import SecReqGenConfig
from secreqgen.evaluation import (
    EvaluationPipeline,
    build_multi_run_report,
    run_multi_evaluation,
    serialize_individual_result,
    serialize_report,
)
from secreqgen.generation.grounded_citation import Idea21Generator
from secreqgen.generation.llm_client import OllamaClient
from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder
from secreqgen.ingestion import DocumentEmbedder
from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
from secreqgen.retrieval.reranker import CrossEncoderReranker


THESIS_ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project")
REAL_TEST_CASES = THESIS_ROOT / "real_test_cases"
SECREQGEN = THESIS_ROOT / "secreqgen"

IDEA21_PROMPTS = SECREQGEN / "data" / "winning_prompts_idea21"

ADV_PROJECT_ROOT = REAL_TEST_CASES / "mastodon_adversarial"
DOCS_DIR = REAL_TEST_CASES / "mastodon" / "docs"
REPO_NAME = "mastodon"


def run_cell(model: str, n_runs: int, label_prefix: str,
             match_threshold: float = 0.85,
             partial_threshold: float = 0.70) -> None:
    stories_path = ADV_PROJECT_ROOT / "user_stories.json"
    ground_truth_path = ADV_PROJECT_ROOT / "ground_truth.json"
    for required in (DOCS_DIR, stories_path, ground_truth_path, IDEA21_PROMPTS):
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
    print(f"\n=== [{model}] × [mastodon_adversarial] N={n_runs} (idea21) ===")
    print(f"  output: {run_dir}")
    print(f"  prompts: {IDEA21_PROMPTS}")

    # Generator LLM
    llm_client = OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=model,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
    )
    if not llm_client.check_health():
        print(f"  ! Ollama health check failed for {model}; SKIPPING")
        return

    # Agent LLM for both:
    #   - code-grep agent (idea 19)
    #   - Stage A token extractor (idea 21)
    # Must be a different family from generator. Note: qwen3.6:latest and
    # gemma4:26b are unloadable on this Ollama version (architecture
    # mismatch: 'qwen35moe' / 'gemma4'), so when the generator IS
    # mistral-small we fall back to qwen3:30b for the agent role — still
    # different-family (mistral vs qwen), still capable.
    if "mistral" in model.lower():
        agent_model = "qwen3:30b-a3b-instruct-2507-q4_K_M"
    else:
        agent_model = "mistral-small3.2:24b"
    print(f"  agent (idea-19 grep + idea-21 extractor) uses: {agent_model}")
    agent_llm = OllamaClient(
        model_name=agent_model, temperature=0.1, max_tokens=400, timeout=300.0,
    )
    # Stage-A extractor needs more headroom for token lists than the grep
    # agent (which only emits 3 patterns). Run a separate client at a higher
    # max_tokens budget.
    extractor_llm = OllamaClient(
        model_name=agent_model, temperature=0.1, max_tokens=1200, timeout=300.0,
    )

    # Vector store + ingestion
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
        print("  ! no chunks ingested; SKIPPING")
        return

    # Retriever stack (idea 19)
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
    system_context = (system_context_path.read_text()
                      if system_context_path.exists() else None)

    # Pipeline + idea-21 prompt builder + swap in Idea21Generator on the RAG arm
    pipeline = EvaluationPipeline(
        llm_client=llm_client, retriever=retriever,
        match_threshold=match_threshold, partial_threshold=partial_threshold,
        system_context=system_context,
    )
    idea21_pb = SecurityRequirementsPromptBuilder(prompts_dir=IDEA21_PROMPTS)
    # Baseline arm: the idea-21 prompt builder too, so every arm shares ONE
    # instruction set (same system prompt, same two fictitious-token
    # exemplars); the arms differ only in the project information supplied.
    # 2026-10-25: the previous zero_shot template in winning_prompts_idea21
    # had silently dropped the exemplars (24 lines vs 124 in winning_prompts),
    # so the May idea-21 "baseline" was NOT the idea-15 baseline — see
    # docs/RAG_ABLATION_LAB_NOTEBOOK.md §13.
    pipeline._runner._baseline_generator.prompt_builder = idea21_pb

    # Replace the RAG arm entirely with the idea-21 generator.
    idea21_gen = Idea21Generator(
        llm_client=llm_client,
        extractor_llm=extractor_llm,
        retriever=retriever,
        prompt_builder=idea21_pb,
    )
    pipeline._runner._rag_generator = idea21_gen
    print("  RAG arm replaced with Idea21Generator (extract-then-cite)")

    print(f"  running {n_runs} paired baseline+idea21 evaluations...")

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
        "config_label": f"{label_prefix}_adversarial_mastodon_idea21",
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
    ap.add_argument("--label-prefix", type=str, default="idea21")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"Models:   {models}")
    print(f"N:        {args.n}")
    print(f"Adversarial project: {ADV_PROJECT_ROOT}")
    print(f"Docs source:         {DOCS_DIR}")
    print(f"Idea-21 prompts:     {IDEA21_PROMPTS}")
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
