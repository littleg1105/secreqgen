"""The search step for an outside product, with the search configuration of Study 2.

For each generator model, the code-search agent is the other model (CROSS_FAMILY of run_study2.py).
The script builds a NEW vector store below tuning/_xproject/<project>/vectordb, searches for each
story, and writes retrieval_frozen.json and manifest.json for each model. It never writes below
study2, study2b or secreqgen/data. It never overwrites a frozen file.

Run it with PYTHONHASHSEED=0 (the search text of the old code depends on the hash seed).
Use --dry-run to make all checks with no model load, no Ollama call and no write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_tuning as rt  # noqa: E402
from tuning_lib.guard import (  # noqa: E402
    DEFAULT_GUARD,
    OTHER_SCRIPT_NAMES,
    GuardError,
    find_other_experiment_processes,
    guarded_mkdir,
    guarded_write_text,
)
from tuning_lib.passages import PassagesError, doc_passages, split_context  # noqa: E402
from tuning_lib.paths import FALLBACK_CONTEXT, model_slug  # noqa: E402
from tuning_lib.xproject import (  # noqa: E402
    ALLOWED_PRODUCTS,
    XPROJECT_ROOT,
    XProjectError,
    check_persist_dir,
    check_product_folders,
    frozen_path,
    load_xstories,
    persist_dir_of,
)

# Generator -> agent model. Copy of CROSS_FAMILY of run_study2.py (roles stay in different families).
CROSS_FAMILY: dict[str, str] = {
    "qwen3.8:27b": "gemma4:26b",
    "gemma4:26b": "qwen3.8:27b",
}
EXPECTED_THRESHOLD = 0.4
RERANK_CANDIDATES = 30
OUR_SCRIPTS: tuple[str, ...] = ("xproject_retrieve.py", "xproject_run.py")
OFFLINE_ENV: dict[str, str] = {
    "TOKENIZERS_PARALLELISM": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}


def set_offline_env(environ: dict[str, str] | None = None) -> None:
    """Set the embedding environment variables. Call it before the embedding code is imported."""
    target = os.environ if environ is None else environ
    target.update(OFFLINE_ENV)


def hashseed_ok(environ: Mapping[str, str] | None = None, hash_randomization: int | None = None) -> bool:
    """True when PYTHONHASHSEED is "0" and this process really runs with a fixed hash seed."""
    env = os.environ if environ is None else environ
    flag = sys.flags.hash_randomization if hash_randomization is None else hash_randomization
    return env.get("PYTHONHASHSEED") == "0" and flag == 0


def sha256_text(text: str) -> str:
    """SHA-256 of a text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def models_list(value: str) -> list[str]:
    """Split a comma list of generator models and check that each has an agent model."""
    models = [m.strip() for m in value.split(",") if m.strip()]
    if not models or len(set(models)) != len(models):
        raise XProjectError("--models needs at least one name and no duplicate")
    bad = [m for m in models if m not in CROSS_FAMILY]
    if bad:
        raise XProjectError(f"no agent model mapped for: {', '.join(bad)}")
    return models


def existing_frozen(project: str, models: Sequence[str], xroot: Path = XPROJECT_ROOT) -> list[Path]:
    """Frozen or manifest files that exist already. A real run must not start if there is one."""
    found = []
    for model in models:
        path = frozen_path(project, model, xroot)
        found += [p for p in (path, path.with_name("manifest.json")) if p.exists()]
    return found


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _config_threshold() -> tuple[float, int]:
    """Threshold and top_k of SecReqGenConfig(). The config is imported late, on purpose."""
    from secreqgen.config import SecReqGenConfig

    cfg = SecReqGenConfig()
    return cfg.retrieval.similarity_threshold, cfg.retrieval.top_k


def _docs_dir_matches_study_dict(project: str) -> bool:
    """True when the allow-list docs folder equals PROJECTS of run_cross_family_winning_config.py."""
    import run_cross_family_winning_config as old

    return Path(old.PROJECTS[project][1]).resolve() == ALLOWED_PRODUCTS[project].docs_dir.resolve()


def run_checks(project: str, models: Sequence[str], *, dry_run: bool, xroot: Path = XPROJECT_ROOT) -> list[rt.Check]:
    """All checks before the search. Nothing is written."""
    checks: list[rt.Check] = []

    def add(name: str, fn: Any) -> None:
        checks.append(rt._check_step(name, fn))

    def c_seed() -> rt.Check:
        if hashseed_ok():
            return rt.Check("PYTHONHASHSEED", "PASS", "fixed to 0")
        text = "PYTHONHASHSEED is not 0; start the script with PYTHONHASHSEED=0"
        return rt.Check("PYTHONHASHSEED", "WARN" if dry_run else "FAIL", text)

    def c_product() -> rt.Check:
        item = check_product_folders(project)
        if not _docs_dir_matches_study_dict(project):
            return rt.Check("product", "FAIL", "the documentation folder differs from PROJECTS of the old script")
        n_md = sum(1 for _ in item.docs_dir.glob("**/*.md"))
        return rt.Check("product", "PASS", f"{project}: {n_md} Markdown files, repository folder exists")

    def c_stories() -> rt.Check:
        stories = load_xstories(project)
        return rt.Check("stories", "PASS", f"{len(stories)} stories, none is a story of the lab")

    def c_config() -> rt.Check:
        threshold, top_k = _config_threshold()
        if threshold != EXPECTED_THRESHOLD:
            return rt.Check("config", "FAIL", f"similarity_threshold is {threshold}, expected {EXPECTED_THRESHOLD}")
        return rt.Check("config", "PASS", f"similarity_threshold {threshold}, top_k {top_k}, rerank_candidates {RERANK_CANDIDATES}")

    def c_persist() -> rt.Check:
        resolved = check_persist_dir(persist_dir_of(project, xroot))
        return rt.Check("vector store folder", "PASS", f"{resolved}")

    def c_hash(label: str) -> rt.Check:
        results = rt.check_all_hash_lists()
        bad = [n for n, r in results.items() if not r.ok]
        if bad:
            return rt.Check(label, "FAIL", "not ok: " + ", ".join(bad), data=results)
        return rt.Check(label, "PASS", ", ".join(f"{n}: {r.n_listed} listed" for n, r in results.items()), data=results)

    def c_procs() -> rt.Check:
        rows = rt.list_processes()
        found = find_other_experiment_processes(rows, names=(*OTHER_SCRIPT_NAMES, *OUR_SCRIPTS))
        if found:
            shown = "; ".join(f"pid {r.pid}: {r.command[:100]}" for r in found[:3])
            return rt.Check("other processes", "FAIL", f"another experiment process runs: {shown}")
        return rt.Check("other processes", "PASS", "no other experiment process")

    def c_frozen() -> rt.Check:
        found = existing_frozen(project, models, xroot)
        if found:
            return rt.Check("frozen files", "FAIL", "exists already, never overwritten: " + ", ".join(str(p) for p in found))
        return rt.Check("frozen files", "PASS", "no frozen file and no manifest exist yet")

    def c_agents() -> rt.Check:
        text = ", ".join(f"{m} (agent {CROSS_FAMILY[m]})" for m in models)
        return rt.Check("models", "PASS", text)

    add("PYTHONHASHSEED", c_seed)
    add("product", c_product)
    add("stories", c_stories)
    add("config", c_config)
    add("vector store folder", c_persist)
    add("hash lists (before)", lambda: c_hash("hash lists (before)"))
    add("other processes", c_procs)
    add("frozen files", c_frozen)
    add("models", c_agents)
    return checks


# ---------------------------------------------------------------------------
# The real run
# ---------------------------------------------------------------------------


def _ollama_version(base_url: str) -> str:
    import httpx

    try:
        return str(httpx.get(f"{base_url}/api/version", timeout=5.0).json().get("version", "?"))
    except Exception:
        return "unreachable"


def count_passages(context: str) -> tuple[int, int, bool]:
    """(documentation passages, code hits, is_fallback) of a context."""
    if context == FALLBACK_CONTEXT or len(context) <= len(FALLBACK_CONTEXT):
        return 0, 0, True
    try:
        passages = split_context(context)
    except PassagesError:
        return 0, 0, True
    n_doc = len(doc_passages(passages))
    return n_doc, len(passages) - n_doc, False


def _search_model(project: str, model: str, stories: Mapping[str, Any], cfg: Any, doc_retriever: Any) -> dict[str, dict]:
    """Search for each story with one generator model. Returns the frozen entries."""
    from secreqgen.generation.llm_client import OllamaClient
    from secreqgen.generation.schemas import UserStory
    from run_cross_family_winning_config import Idea19Retriever

    agent_model = CROSS_FAMILY[model]
    agent_llm = OllamaClient(
        base_url=cfg.llm.base_url, model_name=agent_model, temperature=0.1, max_tokens=400,
        timeout=600.0, num_ctx=16384, structured_output=False,
    )
    if not agent_llm.check_health():
        raise XProjectError(f"Ollama health check failed for the agent model {agent_model}")
    retriever = Idea19Retriever(doc_retriever=doc_retriever, agent_llm=agent_llm, project=project)
    entries: dict[str, dict] = {}
    for story_id in sorted(stories):
        s = stories[story_id]
        user_story = UserStory(id=s.story_id, title=s.title, story=s.story,
                               acceptance_criteria=list(s.acceptance_criteria))
        t0 = time.perf_counter()
        result = retriever.retrieve_for_user_story(user_story)
        context = retriever.format_result_for_prompt(result)
        ms = (time.perf_counter() - t0) * 1000
        n_code = sum(1 for c in result.chunks if "code" in str(getattr(c, "metadata", {})).lower())
        entries[story_id] = {
            "query_text": result.query_text, "n_chunks": len(result.chunks),
            "n_code_chunks_guess": n_code, "above_threshold": result.above_threshold,
            "retrieval_ms": round(ms, 1), "context_chars": len(context),
            "context_sha256": sha256_text(context), "context": context,
        }
        n_doc, n_hit, fallback = count_passages(context)
        flag = "  !! FALLBACK TEXT, retrieval failed" if fallback else ""
        print(f"  {model} {story_id}: {n_doc} documentation passages, {n_hit} code hits, "
              f"{len(context)} characters{flag}")
    return entries


def _write_new(path: Path, text: str) -> None:
    """Write a file through the guard. A file that exists is never replaced."""
    if path.exists():
        raise XProjectError(f"{path} exists already; never overwritten")
    guarded_mkdir(path.parent, guard=DEFAULT_GUARD)
    guarded_write_text(path, text, guard=DEFAULT_GUARD)


def real_run(project: str, models: Sequence[str], xroot: Path = XPROJECT_ROOT) -> int:
    """Steps 3 to 8 of the search. Returns the exit code."""
    from run_cross_family_winning_config import _ingest_project
    from secreqgen.config import SecReqGenConfig
    from secreqgen.ingestion import DocumentEmbedder
    from secreqgen.retrieval import SecReqGenVectorStore, SimpleRetriever
    from secreqgen.retrieval.reranker import CrossEncoderReranker

    cfg = SecReqGenConfig()
    item = check_product_folders(project)
    stories = load_xstories(project)
    persist = check_persist_dir(persist_dir_of(project, xroot))  # before any vector store object
    started = rt.utc_now()
    embedder = DocumentEmbedder(model_name=cfg.embedding.model_name, device=cfg.embedding.device)
    guarded_mkdir(persist, guard=DEFAULT_GUARD)
    vectorstore = SecReqGenVectorStore(persist_directory=persist, collection_prefix=cfg.vectorstore.collection_prefix)
    print(f"ingesting {item.docs_dir} into {persist} ...", flush=True)
    n_chunks = _ingest_project(item.docs_dir, embedder, vectorstore)
    print(f"{n_chunks} chunks")
    if n_chunks == 0:
        print("[FAIL] no chunk ingested")
        return 2
    doc_retriever = SimpleRetriever(
        vectorstore=vectorstore, embedder=embedder, top_k=cfg.retrieval.top_k,
        similarity_threshold=cfg.retrieval.similarity_threshold,
        reranker=CrossEncoderReranker(), rerank_candidates=RERANK_CANDIDATES,
    )
    ollama = _ollama_version(cfg.llm.base_url)
    for model in models:
        print(f"=== {project} x {model} (agent {CROSS_FAMILY[model]}) ===")
        entries = _search_model(project, model, stories, cfg, doc_retriever)
        manifest = {
            "project": project, "model": model, "agent_model": CROSS_FAMILY[model],
            "models": {m: {"agent_model": CROSS_FAMILY[m]} for m in models},
            "config": {
                "top_k": cfg.retrieval.top_k, "similarity_threshold": cfg.retrieval.similarity_threshold,
                "rerank_candidates": RERANK_CANDIDATES, "embedding_model": cfg.embedding.model_name,
                "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            },
            "docs_dir": str(item.docs_dir), "repo_dir": str(item.repo_dir), "n_chunks_ingested": n_chunks,
            "vectordb": str(persist), "stories": sorted(stories),
            "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED"),
            "ollama_version": ollama, "python": platform.python_version(),
            "started_utc": started, "ended_utc": rt.utc_now(),
        }
        out = frozen_path(project, model, xroot)
        _write_new(out, json.dumps(entries, indent=1))
        _write_new(out.with_name("manifest.json"), json.dumps(manifest, indent=2) + "\n")
        print(f"  written: {out}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    p = argparse.ArgumentParser(prog="xproject_retrieve.py", description=__doc__.splitlines()[0])
    p.add_argument("--project", required=True, choices=sorted(ALLOWED_PRODUCTS))
    p.add_argument("--models", default="qwen3.8:27b,gemma4:26b")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0, 2 (refused) or 3 (registered files changed)."""
    args = parse_args(argv)
    set_offline_env()
    try:
        models = models_list(args.models)
    except XProjectError as exc:
        print(f"[FAIL] --models: {exc}")
        return 2
    checks = run_checks(args.project, models, dry_run=args.dry_run)
    rt.print_checks(checks)
    if any(c.status == "FAIL" for c in checks):
        print("refused: a check failed. Nothing was written.")
        return 2
    if args.dry_run:
        n = len(load_xstories(args.project))
        print(f"dry run: {n} stories x {len(models)} models; no model load, no Ollama call, no write")
        for m in models:
            print(f"  {m}: agent {CROSS_FAMILY[m]}, output {frozen_path(args.project, m)}")
        return 0
    try:
        code = real_run(args.project, models)
    except (XProjectError, GuardError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 2
    after = rt.check_all_hash_lists()
    bad = [n for n, r in after.items() if not r.ok]
    print("[PASS] hash lists (after)" if not bad else f"[FAIL] hash lists (after): {', '.join(bad)}")
    return 3 if bad else code


if __name__ == "__main__":
    sys.exit(main())
