"""Per-cell sanity inspection for the cross-family experiment.

Walks all 20 (project × model) cells and emits a compact summary per
cell + a flagged-issues list. Designed to be run once and read.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
MODELS = [
    ("qwen3",      "qwen3_30b-a3b-instruct-2507-q4_K_M"),
    ("qwen3.6",    "qwen3_6_latest"),
    ("gemma4",     "gemma4_26b"),
    ("mistral-sm", "mistral-small3_2_24b"),
]
PROJECTS = ["mastodon", "keycloak", "bitwarden", "gitlab", "mattermost"]


def latest_run(d: Path) -> Path | None:
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
    return rcs[-1] if rcs else None


def inspect_cell(proj: str, model_label: str, model_slug: str) -> dict:
    d = ROOT / proj / f"results_compare_runs_winning_{model_slug}_n10"
    if not d.exists():
        return {"proj": proj, "model": model_label, "missing": True}
    run = latest_run(d)
    if not run:
        return {"proj": proj, "model": model_label, "missing": True}

    rep = json.load(open(run / "multi_run_report.json"))
    cfg = rep.get("config", {})
    ba = rep["baseline_aggregate"]; ra = rep["rag_aggregate"]
    ps = rep["paired_test_per_story_f1"]; pr = rep["paired_test_per_run_f1"]

    raw_files = sorted((run / "raw_runs").glob("run_*.json"))
    if not raw_files:
        return {"proj": proj, "model": model_label, "no_raw": True}

    # Use run_001 for per-story status — all 10 runs see the same stories
    r1 = json.load(open(raw_files[0]))
    psr = r1["per_story_results"]

    bl_none = sum(1 for s in psr if s.get("baseline_result") is None)
    rg_none = sum(1 for s in psr if s.get("rag_result") is None)
    bl_reqs = [len(s["baseline_result"]["requirements"]) for s in psr if s.get("baseline_result")]
    rg_reqs = [len(s["rag_result"]["requirements"]) for s in psr if s.get("rag_result")]

    # Match-type distribution across all stories, RAG side
    rag_match_types = Counter()
    bl_match_types = Counter()
    for s in psr:
        for m in s.get("rag_matches", []):
            rag_match_types[m.get("match_type", "?")] += 1
        for m in s.get("baseline_matches", []):
            bl_match_types[m.get("match_type", "?")] += 1

    # Doc vs code chunk count in story 0's retrieval context
    s0 = psr[0]
    ctx0 = (s0.get("rag_result") or {}).get("retrieval_context") or ""
    n_chunks0 = ctx0.count("### [")
    n_code0 = ctx0.count("### code search hit")
    n_doc0 = n_chunks0 - n_code0

    # Sample first RAG requirement and first baseline requirement for story 0
    bl_sample = ""
    rg_sample = ""
    if s0.get("baseline_result", {}).get("requirements"):
        r = s0["baseline_result"]["requirements"][0]
        bl_sample = f"{r.get('title','')[:50]} → {(r.get('asvs_category','') or '')[:25]}"
    if s0.get("rag_result", {}).get("requirements"):
        r = s0["rag_result"]["requirements"][0]
        rg_sample = f"{r.get('title','')[:50]} → {(r.get('asvs_category','') or '')[:25]}"

    return {
        "proj": proj, "model": model_label,
        "n_chunks_ingested": cfg.get("n_chunks_ingested", "?"),
        "n_stories": len(psr),
        "bl_none": bl_none, "rg_none": rg_none,
        "bl_reqs_mean": sum(bl_reqs) / max(1, len(bl_reqs)),
        "rg_reqs_mean": sum(rg_reqs) / max(1, len(rg_reqs)),
        "bl_reqs_range": (min(bl_reqs) if bl_reqs else 0, max(bl_reqs) if bl_reqs else 0),
        "rg_reqs_range": (min(rg_reqs) if rg_reqs else 0, max(rg_reqs) if rg_reqs else 0),
        "bl_match": dict(bl_match_types),
        "rg_match": dict(rag_match_types),
        "bl_f1": ba["f1_score"]["mean"],
        "rg_f1": ra["f1_score"]["mean"],
        "dz_story": ps["d_z"],
        "p_story": ps["primary_p_value"],
        "dz_run": pr["d_z"],
        "p_run": pr["primary_p_value"],
        "n_chunks_story0": n_chunks0,
        "n_doc_story0": n_doc0,
        "n_code_story0": n_code0,
        "bl_sample": bl_sample,
        "rg_sample": rg_sample,
    }


def verdict(dz, p):
    if p is None or p >= 0.05:
        if abs(dz) > 0.5: return "trend"
        return "null"
    if dz > 0.3: return "STRONG +"
    if dz < -0.3: return "STRONG -"
    return "marg"


def main():
    issues = []
    rows = []
    for proj in PROJECTS:
        for label, slug in MODELS:
            row = inspect_cell(proj, label, slug)
            rows.append(row)

    print("=" * 130)
    print(f"{'project':<11} {'model':<11} {'chunks':<7} {'bl/rg none':<11} {'bl_reqs':<10} {'rg_reqs':<10} {'bl_f1':<7} {'rg_f1':<7} {'verdict':<9} {'doc/code':<8}")
    print("-" * 130)
    for r in rows:
        if r.get("missing"):
            print(f"{r['proj']:<11} {r['model']:<11} MISSING")
            issues.append(f"MISSING: {r['proj']} × {r['model']}")
            continue
        if r.get("no_raw"):
            print(f"{r['proj']:<11} {r['model']:<11} NO_RAW_RUNS")
            issues.append(f"NO_RAW: {r['proj']} × {r['model']}")
            continue
        v_story = verdict(r["dz_story"], r["p_story"])
        v_run = verdict(r["dz_run"], r["p_run"])
        verdict_combo = f"{v_story}/{v_run}"
        bl_rg_none = f"{r['bl_none']}/{r['rg_none']}"
        bl_reqs_str = f"{r['bl_reqs_mean']:.1f} [{r['bl_reqs_range'][0]}-{r['bl_reqs_range'][1]}]"
        rg_reqs_str = f"{r['rg_reqs_mean']:.1f} [{r['rg_reqs_range'][0]}-{r['rg_reqs_range'][1]}]"
        doc_code = f"{r['n_doc_story0']}/{r['n_code_story0']}"
        print(f"{r['proj']:<11} {r['model']:<11} {r['n_chunks_ingested']:<7} {bl_rg_none:<11} {bl_reqs_str:<10} {rg_reqs_str:<10} {r['bl_f1']:<7.3f} {r['rg_f1']:<7.3f} {verdict_combo:<9} {doc_code:<8}")

        # Flag issues
        if r['bl_none'] > 0 or r['rg_none'] > 0:
            issues.append(f"{r['proj']} × {r['model']}: bl_none={r['bl_none']} rg_none={r['rg_none']}")
        if r['bl_reqs_range'][0] == 0 or r['rg_reqs_range'][0] == 0:
            issues.append(f"{r['proj']} × {r['model']}: at least one story produced 0 requirements")
        if r['n_chunks_story0'] == 0:
            issues.append(f"{r['proj']} × {r['model']}: story 0 retrieved 0 chunks (RAG context empty)")
        if r['n_code_story0'] == 0:
            issues.append(f"{r['proj']} × {r['model']}: story 0 has 0 code chunks (idea-19 code agent may have errored)")

    print()
    print("=" * 130)
    print("Story-0 sample requirements (illustrates few-shot prompt effect)")
    print("-" * 130)
    for r in rows:
        if r.get("missing") or r.get("no_raw"):
            continue
        print(f"\n{r['proj']:<11} {r['model']:<11}")
        print(f"  baseline req0: {r['bl_sample']}")
        print(f"  rag req0:      {r['rg_sample']}")

    print()
    print("=" * 130)
    print("Match-type distribution (across all 8 stories, RAG side, run_001)")
    print("-" * 130)
    print(f"{'project':<11} {'model':<11} {'matches':<10} {'partial':<10} {'no_match':<10} (out of all candidate-gt pairs)")
    for r in rows:
        if r.get("missing") or r.get("no_raw"):
            continue
        rm = r["rg_match"]
        print(f"  {r['proj']:<9} {r['model']:<11} {rm.get('match',0):<10} {rm.get('partial',0):<10} {rm.get('no_match',0):<10}")

    print()
    print("=" * 130)
    print(f"FLAGGED ISSUES ({len(issues)}):")
    print("-" * 130)
    for iss in issues:
        print(f"  • {iss}")


if __name__ == "__main__":
    main()
