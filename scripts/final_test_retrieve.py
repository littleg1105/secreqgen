"""Final test: the old search for each story text of the sealed stories (the arm OLD).

It runs the registered search code of scripts/xproject_retrieve.py (the function for one model: document search
with the reranker and the cut of the configuration, and the code-search agent on the local model of the other
family through Ollama). Only the stories and the output folder differ: the stories come from the sealed folder,
and the files go to real_test_cases/final_test/retrieval/<project>/. It never replaces a file and prints counts only.

Run from coded_project/secreqgen (Ollama must run):
  PYTHONHASHSEED=0 TOKENIZERS_PARALLELISM=false PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_retrieve.py [--project keycloak]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys

import run_tuning as rt
import xproject_retrieve as XR
from tuning_lib.final_test import RETRIEVAL_ROOT, load_final_stories, run_ids
from tuning_lib.guard import DEFAULT_GUARD, guarded_mkdir
from tuning_lib.paths import FINAL_TEST_ROOT, MODELS, PROJECTS, REPO_DIR_OVERRIDE, REPOS_ROOT, RTC, STUDY2
from tuning_lib.xproject import check_persist_dir, frozen_path


def run_project(project: str) -> int:
    from run_cross_family_winning_config import _ingest_project
    from secreqgen.config import SecReqGenConfig
    from secreqgen.ingestion import DocumentEmbedder
    from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
    from secreqgen.retrieval.reranker import CrossEncoderReranker

    cfg = SecReqGenConfig()
    if cfg.retrieval.similarity_threshold != XR.EXPECTED_THRESHOLD:  # the locked cut of the old search (0.4)
        print(f"[FAIL] similarity_threshold is {cfg.retrieval.similarity_threshold}, expected {XR.EXPECTED_THRESHOLD}")
        return 2
    docs_dir = RTC / json.loads((STUDY2 / project / "stories.json").read_text(encoding="utf-8"))["docs_dir"]
    repo_dir = REPOS_ROOT / REPO_DIR_OVERRIDE.get(project, project)
    every = load_final_stories((project,))
    stories = {sid: every[sid] for sid in run_ids(project)}  # only the stories of the run
    # A file that exists is kept as it is (nothing is replaced); only a model with no file is searched.
    todo = [m for m in MODELS if not frozen_path(project, m, RETRIEVAL_ROOT).exists()]
    if not todo:
        print(f"{project}: the file of each model exists; nothing to do", flush=True)
        return 0
    # Before any vector store object: the folder must be below final_test/ and not below secreqgen/data.
    persist = check_persist_dir(RETRIEVAL_ROOT / project / "vectordb", tuning_root=FINAL_TEST_ROOT)
    started = rt.utc_now()
    embedder = DocumentEmbedder(model_name=cfg.embedding.model_name, device=cfg.embedding.device)
    guarded_mkdir(persist, guard=DEFAULT_GUARD)
    vectorstore = SecReqGenVectorStore(persist_directory=persist, collection_prefix=cfg.vectorstore.collection_prefix)
    n_chunks = _ingest_project(docs_dir, embedder, vectorstore)
    print(f"{project}: {n_chunks} chunks ingested, {len(stories)} story texts", flush=True)
    if n_chunks == 0:
        print("[FAIL] no chunk ingested")
        return 2
    doc_retriever = SimpleRetriever(
        vectorstore=vectorstore, embedder=embedder, top_k=cfg.retrieval.top_k,
        similarity_threshold=cfg.retrieval.similarity_threshold,
        reranker=CrossEncoderReranker(), rerank_candidates=XR.RERANK_CANDIDATES,
    )
    ollama = XR._ollama_version(cfg.llm.base_url)
    for model in todo:
        print(f"=== {project} x {model} (agent {XR.CROSS_FAMILY[model]}) ===", flush=True)
        entries = XR._search_model(project, model, stories, cfg, doc_retriever)
        manifest = {
            "project": project, "model": model, "agent_model": XR.CROSS_FAMILY[model],
            "config": {
                "top_k": cfg.retrieval.top_k, "similarity_threshold": cfg.retrieval.similarity_threshold,
                "rerank_candidates": XR.RERANK_CANDIDATES, "embedding_model": cfg.embedding.model_name,
                "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            },
            "docs_dir": str(docs_dir.relative_to(RTC)), "repo_dir": str(repo_dir.relative_to(RTC)), "n_chunks_ingested": n_chunks,
            "stories": sorted(stories), "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED"),
            "ollama_version": ollama, "python": platform.python_version(), "started_utc": started, "ended_utc": rt.utc_now(),
        }
        out = frozen_path(project, model, RETRIEVAL_ROOT)
        XR._write_new(out, json.dumps(entries, indent=1))
        XR._write_new(out.with_name("manifest.json"), json.dumps(manifest, indent=2) + "\n")
        print(f"  written: final_test/retrieval/{project}/{out.parent.name}/{out.name}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", choices=PROJECTS, default=None)
    args = parser.parse_args(argv)
    XR.set_offline_env()
    if not XR.hashseed_ok():
        print("[FAIL] set PYTHONHASHSEED=0")
        return 2
    before = rt.check_all_hash_lists()  # the registered data must not change
    bad = [n for n, r in before.items() if not r.ok]
    print("[PASS] hash lists (before)" if not bad else f"[FAIL] hash lists (before): {', '.join(bad)}", flush=True)
    if bad:
        return 3
    status = 0
    for project in ([args.project] if args.project else list(PROJECTS)):
        status = status or run_project(project)
    after = rt.check_all_hash_lists()
    bad = [n for n, r in after.items() if not r.ok]
    print("[PASS] hash lists (after)" if not bad else f"[FAIL] hash lists (after): {', '.join(bad)}", flush=True)
    return 3 if bad else status


if __name__ == "__main__":
    sys.exit(main())
