"""Cross-model comparison (qwen3 vs qwen3.6) — thesis sub-test.

For each project in the qwen3.6 sub-test subset, compare:
  (A) baseline F1 under qwen3 vs baseline F1 under qwen3.6
        — does the stronger base model produce stronger zero-shot output?
  (B) RAG F1 under qwen3 vs RAG F1 under qwen3.6
        — does RAG benefit transfer or compress under the stronger model?
  (C) RAG-vs-baseline d_z under each model
        — does the per-project helps/hurts pattern survive the model swap?

This is the analysis that lets the thesis answer:
  "Does the multi-run methodology's per-project verdict generalise
   across base-model variants?"

Run from secreqgen/:
    PYTHONPATH=src .venv/bin/python scripts/cross_model_comparison.py
    PYTHONPATH=src .venv/bin/python scripts/cross_model_comparison.py --output docs/sub_test_qwen36_results.md
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

REPO = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen")
TEST_CASES = REPO.parent / "real_test_cases"

BASELINE_VERSION = "results_compare_runs_thesis_n20"           # qwen3:30b-a3b-instruct-2507-q4_K_M (N=20)
SUBTEST_VERSION  = "results_compare_runs_qwen36_latest_n10"    # qwen3.6:latest (N=10, think:false)

# Subset projects (must match run_thesis_n20_qwen36.sh)
# All 10 hand-curated projects covered by the qwen3.6 extension.
# Initial 4 (mastodon, mattermost, bitwarden, keycloak) were run first;
# the remaining 6 (gitlab, nextcloud, strapi, discourse, joomla, grafana)
# were added in the 2026-05-16 extension. Order kept for table readability.
SUBSET = [
    "mastodon", "mattermost", "bitwarden", "keycloak",
    "gitlab", "nextcloud", "strapi", "discourse", "joomla", "grafana",
]


def latest_report(proj: str, version_dir: str) -> dict | None:
    pattern = str(TEST_CASES / proj / version_dir / "run_comparison_*" / "multi_run_report.json")
    hits = sorted(glob.glob(pattern))
    if not hits:
        return None
    with open(hits[-1]) as fh:
        return json.load(fh)


def fmt(x: float, places: int = 3) -> str:
    return f"{x:.{places}f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", help="Write Markdown report to this file")
    args = ap.parse_args()

    lines: list[str] = []
    lines.append("# Cross-model comparison — qwen3.6 sub-test\n")
    lines.append("**Question:** does the per-project RAG-helps-vs-hurts pattern observed under `qwen3:30b-a3b-instruct-2507-q4_K_M` (the baseline thesis model, N=20) generalise to a different base model — `qwen3.6:latest` (newer generation, A3B MoE, 24GB, think:false, N=10)?")
    lines.append("")

    # Per-project comparison table
    lines.append("## Per-project: same-condition F1 across models")
    lines.append("")
    lines.append("| project | model | baseline F1 | RAG F1 | Δ F1 (RAG-baseline) | per-run d_z | per-run p |")
    lines.append("|---|---|---|---|---|---|---|")
    rows_for_summary = []
    for proj in SUBSET:
        for version_dir, model_label in [
            (BASELINE_VERSION, "qwen3"),
            (SUBTEST_VERSION,  "qwen3.6"),
        ]:
            d = latest_report(proj, version_dir)
            if d is None:
                lines.append(f"| {proj} | {model_label} | — | — | — | — | (no report) |")
                continue
            b = d["baseline_aggregate"]["f1_score"]
            r = d["rag_aggregate"]["f1_score"]
            pr = d["paired_test_per_run_f1"]
            delta = r["mean"] - b["mean"]
            lines.append(
                f"| {proj} | **{model_label}** | "
                f"{fmt(b['mean'])} ± {fmt(b['std'])} | "
                f"{fmt(r['mean'])} ± {fmt(r['std'])} | "
                f"{delta:+.4f} | "
                f"{pr['d_z']:+.3f} | "
                f"{pr['primary_p_value']:.4f} |"
            )
            rows_for_summary.append({
                "project": proj, "model": model_label,
                "baseline_mean": b["mean"], "rag_mean": r["mean"],
                "delta": delta, "dz": pr["d_z"], "p": pr["primary_p_value"],
            })
    lines.append("")

    # Direction-of-effect comparison
    lines.append("## Direction-of-effect comparison")
    lines.append("")
    lines.append("Did the qwen3.6 sub-test reach the **same per-project verdict** as the qwen3 baseline?")
    lines.append("")
    lines.append("| project | qwen3 verdict | qwen3.6 verdict | agree? |")
    lines.append("|---|---|---|---|")

    def verdict(dz: float, p: float) -> str:
        if p < 0.05 and dz > 0.5:
            return "STRONG +"
        if p < 0.05 and dz > 0.2:
            return "moderate +"
        if p < 0.05 and dz < -0.5:
            return "STRONG −"
        if p < 0.05 and dz < -0.2:
            return "moderate −"
        return "null"

    for proj in SUBSET:
        d_q3 = latest_report(proj, BASELINE_VERSION)
        d_q36 = latest_report(proj, SUBTEST_VERSION)
        v3_str = "—"
        v36_str = "—"
        if d_q3 is not None:
            v3_str = verdict(d_q3["paired_test_per_run_f1"]["d_z"],
                              d_q3["paired_test_per_run_f1"]["primary_p_value"])
        if d_q36 is not None:
            v36_str = verdict(d_q36["paired_test_per_run_f1"]["d_z"],
                               d_q36["paired_test_per_run_f1"]["primary_p_value"])
        if d_q3 is None or d_q36 is None:
            agree = "(sub-test pending)" if d_q36 is None else "(baseline missing)"
        else:
            agree = ("✓" if v3_str.split()[0] == v36_str.split()[0]
                     else ("partial" if v3_str.split()[-1] == v36_str.split()[-1] else "✗"))
        lines.append(f"| {proj} | {v3_str} | {v36_str} | **{agree}** |")
    lines.append("")

    # Baseline-only comparison (does the stronger model raise the floor?)
    lines.append("## Baseline-only F1 across models")
    lines.append("")
    lines.append("Tests the hypothesis that a stronger base model produces stronger zero-shot output, leaving less RAG headroom.")
    lines.append("")
    lines.append("| project | baseline F1 (qwen3) | baseline F1 (qwen3.6) | Δ baseline (q3.6 - q3) |")
    lines.append("|---|---|---|---|")
    for proj in SUBSET:
        d_q3 = latest_report(proj, BASELINE_VERSION)
        d_q36 = latest_report(proj, SUBTEST_VERSION)
        if d_q3 is None or d_q36 is None:
            lines.append(f"| {proj} | — | — | — |")
            continue
        b3 = d_q3["baseline_aggregate"]["f1_score"]["mean"]
        b36 = d_q36["baseline_aggregate"]["f1_score"]["mean"]
        lines.append(f"| {proj} | {fmt(b3)} | {fmt(b36)} | {b36 - b3:+.4f} |")
    lines.append("")

    out = "\n".join(lines)
    print(out)
    if args.output:
        Path(args.output).write_text(out)
        print(f"\n# Wrote report to {args.output}", flush=True)


if __name__ == "__main__":
    main()
