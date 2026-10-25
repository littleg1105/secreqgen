"""Cross-project synthesis of pilot results.

Aggregates the multi_run_report.json from each project's
results dir and prints a single comparison table suitable for
the thesis.

Default looks in results_compare_runs_pilot_v3/ (the GitHub-bootstrap
pipeline). Pass --version v2 to look at the original 4-project pilot.

Run from the secreqgen project root:
    PYTHONPATH=src poetry run python scripts/synthesize_pilot_v2.py
    PYTHONPATH=src poetry run python scripts/synthesize_pilot_v2.py --version v2
"""
import argparse
import glob
import json
from pathlib import Path

TEST_CASES = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")

VERSION_DIR = {
    "v2": "results_compare_runs_pilot_v2_kb_fixed",
    "v3": "results_compare_runs_pilot_v3",
}


def projects_with_reports(version_dir: str) -> list[str]:
    """All project subdirs of TEST_CASES that have a v2 or v3 report."""
    out: list[str] = []
    for d in sorted(TEST_CASES.iterdir()):
        if not d.is_dir():
            continue
        if d.name.endswith("-test-data") or d.name in {"extracted_docs", "extracted_docs_md", "repos"}:
            continue
        if list((d / version_dir).glob("run_comparison_*/multi_run_report.json")):
            out.append(d.name)
    return out


def latest_report(proj: str, version_dir: str) -> dict | None:
    pattern = str(TEST_CASES / proj / version_dir / "run_comparison_*" / "multi_run_report.json")
    hits = sorted(glob.glob(pattern))
    if not hits:
        return None
    with open(hits[-1]) as fh:
        return json.load(fh)


def num_chunks(proj: str) -> str:
    """Read the ingest count from the pilot output if available."""
    return {"bitwarden": "133", "keycloak": "548", "nextcloud": "9", "strapi": "?"}.get(proj, "?")


def get_kb_chunks(proj: str, version_dir: str) -> str:
    """Best-effort chunk count from the report's config.kb_chunks if present;
    else the hard-coded v2 lookup; else '?'."""
    d = latest_report(proj, version_dir)
    if d is not None:
        ck = d.get("config", {}).get("kb_chunks") or d.get("config", {}).get("vectorstore_chunks")
        if ck is not None:
            return str(ck)
    return num_chunks(proj)


def fmt(x: float, places: int = 3) -> str:
    return f"{x:.{places}f}"


def fmt_signed(x: float, places: int = 3) -> str:
    return f"{x:+.{places}f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=list(VERSION_DIR.keys()), default="v3",
                    help="which results dir to read (default: v3)")
    args = ap.parse_args()
    version_dir = VERSION_DIR[args.version]

    project_list = projects_with_reports(version_dir)
    if not project_list:
        print(f"no {args.version} reports found yet under {TEST_CASES}/*/{version_dir}")
        return

    rows = []
    for proj in project_list:
        d = latest_report(proj, version_dir)
        if d is None:
            rows.append({"project": proj, "status": "no report (pilot not finished)"})
            continue
        b = d["baseline_aggregate"]
        r = d["rag_aggregate"]
        ps = d["paired_test_per_story_f1"]
        pr = d["paired_test_per_run_f1"]
        rows.append({
            "project": proj,
            "n_runs": d["n_runs"],
            "n_stories": ps["n_pairs"],
            "kb_chunks": get_kb_chunks(proj, version_dir),
            "baseline_f1_mean": b["f1_score"]["mean"],
            "baseline_f1_std": b["f1_score"]["std"],
            "rag_f1_mean": r["f1_score"]["mean"],
            "rag_f1_std": r["f1_score"]["std"],
            "delta_f1": r["f1_score"]["mean"] - b["f1_score"]["mean"],
            "ps_p": ps["primary_p_value"],
            "ps_dz": ps["d_z"],
            "pr_p": pr["primary_p_value"],
            "pr_dz": pr["d_z"],
        })

    print(f"\n=== Cross-project synthesis (pilot {args.version}, qwen3-30b-a3b-instruct-2507-q4_K_M) ===\n")
    print(f"{'project':<12} {'KB':>5} {'N_st':>4} {'baseline F1':>17} {'RAG F1':>17} {'Δ F1':>8}  {'per-story':<22} {'per-run (sens)':<22}")
    print("-" * 120)
    for r in rows:
        if "status" in r:
            print(f"{r['project']:<12} {r['status']}")
            continue
        baseline = f"{fmt(r['baseline_f1_mean'])} ± {fmt(r['baseline_f1_std'])}"
        rag = f"{fmt(r['rag_f1_mean'])} ± {fmt(r['rag_f1_std'])}"
        per_story = f"p={fmt(r['ps_p'],4)} d_z={fmt(r['ps_dz'])}"
        per_run = f"p={fmt(r['pr_p'],4)} d_z={fmt(r['pr_dz'])}"
        print(f"{r['project']:<12} {r['kb_chunks']:>5} {r['n_stories']:>4} {baseline:>17} {rag:>17} {fmt_signed(r['delta_f1']):>8}  {per_story:<22} {per_run:<22}")

    print()
    print("Significance markers (per-story Wilcoxon, the PRIMARY test):")
    for r in rows:
        if "status" in r:
            continue
        sig_ps = "***" if r['ps_p'] < 0.001 else ("**" if r['ps_p'] < 0.01 else ("*" if r['ps_p'] < 0.05 else "ns"))
        sig_pr = "***" if r['pr_p'] < 0.001 else ("**" if r['pr_p'] < 0.01 else ("*" if r['pr_p'] < 0.05 else "ns"))
        print(f"  {r['project']:<12} per-story: {sig_ps:<3} (p={fmt(r['ps_p'],4)})   per-run: {sig_pr:<3} (p={fmt(r['pr_p'],4)})")


if __name__ == "__main__":
    main()
