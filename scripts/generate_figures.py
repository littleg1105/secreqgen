"""Generate PNG figures from pilot v2/v3 results.

Outputs to docs/figures/. Run from secreqgen/:
    .venv/bin/python scripts/generate_figures.py
"""
import glob
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen")
TEST_CASES = REPO.parent / "real_test_cases"
FIG_DIR = REPO / "docs" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)


def latest_report(proj: str, version_dir: str) -> dict | None:
    pattern = str(TEST_CASES / proj / version_dir / "run_comparison_*" / "multi_run_report.json")
    hits = sorted(glob.glob(pattern))
    if not hits:
        return None
    with open(hits[-1]) as fh:
        return json.load(fh)


def load_all(version_dir: str) -> list[dict]:
    out = []
    for d in sorted(TEST_CASES.iterdir()):
        if not d.is_dir() or d.name.endswith("-test-data") or d.name in {"extracted_docs", "extracted_docs_md", "repos"}:
            continue
        rpt = latest_report(d.name, version_dir)
        if rpt is None:
            continue
        b = rpt["baseline_aggregate"]["f1_score"]
        r = rpt["rag_aggregate"]["f1_score"]
        ps = rpt["paired_test_per_story_f1"]
        pr = rpt["paired_test_per_run_f1"]
        out.append({
            "project": d.name,
            "baseline_mean": b["mean"], "baseline_std": b["std"], "baseline_values": b["values"],
            "rag_mean": r["mean"], "rag_std": r["std"], "rag_values": r["values"],
            "delta": r["mean"] - b["mean"],
            "ps_p": ps["primary_p_value"], "ps_dz": ps["d_z"],
            "pr_p": pr["primary_p_value"], "pr_dz": pr["d_z"],
        })
    return out


def fig1_v2_baseline_vs_rag():
    """Figure 1: hand-curated v2 results — F1 baseline vs RAG with error bars."""
    rows = load_all("results_compare_runs_pilot_v2_kb_fixed")
    rows.sort(key=lambda r: r["project"])
    if not rows:
        return
    projects = [r["project"] for r in rows]
    b_means = [r["baseline_mean"] for r in rows]
    b_stds = [r["baseline_std"] for r in rows]
    r_means = [r["rag_mean"] for r in rows]
    r_stds = [r["rag_std"] for r in rows]

    x = np.arange(len(projects))
    width = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(x - width/2, b_means, width, yerr=b_stds, capsize=4, label="baseline (zero-shot)", color="#888")
    ax.bar(x + width/2, r_means, width, yerr=r_stds, capsize=4, label="RAG", color="#1f77b4")
    ax.set_xticks(x)
    ax.set_xticklabels(projects, fontsize=11)
    ax.set_ylabel("F1 score (mean ± std across 10 runs)")
    ax.set_title("Pilot v2 — hand-curated 4 projects, qwen3-30b-instruct-2507-q4_K_M")
    ax.legend(loc="upper left")
    # Annotate significant per-run results
    for i, r in enumerate(rows):
        if r["pr_p"] < 0.05:
            ax.annotate(
                f"per-run\np={r['pr_p']:.3f}\nd_z={r['pr_dz']:+.2f}",
                xy=(i + width/2, r_means[i] + r_stds[i]),
                xytext=(i + width/2, max(r_means[i] + r_stds[i] + 0.04, 0.16)),
                ha="center", fontsize=8,
                arrowprops=dict(arrowstyle="->", color="#1f77b4", lw=0.8),
            )
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig1_v2_baseline_vs_rag.png", dpi=150)
    plt.close()
    print(f"  wrote fig1_v2_baseline_vs_rag.png — {len(rows)} projects")


def fig2_v3_dz_distribution():
    """Figure 2: per-run d_z across all v3 projects, sorted."""
    rows = load_all("results_compare_runs_pilot_v3")
    if not rows:
        return
    # Clamp the absurd-magnitude artifacts so the chart is readable; mark them
    dz_clamped = []
    notes = []
    for r in rows:
        dz = r["pr_dz"]
        if abs(dz) > 5:
            dz_clamped.append(np.sign(dz) * 5)
            notes.append(f"|d_z|={abs(dz):.1f}")
        else:
            dz_clamped.append(dz)
            notes.append("")
    rows_sorted = sorted(zip(rows, dz_clamped, notes), key=lambda t: t[1])
    projects = [r["project"] for r, _, _ in rows_sorted]
    dz_vals = [d for _, d, _ in rows_sorted]
    notes2 = [n for _, _, n in rows_sorted]
    colors = ["#d62728" if d < 0 else ("#2ca02c" if d > 0 else "#888") for d in dz_vals]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(np.arange(len(projects)), dz_vals, color=colors)
    ax.set_yticks(np.arange(len(projects)))
    ax.set_yticklabels(projects, fontsize=10)
    ax.axvline(0, color="black", lw=0.5)
    ax.axvline(0.5, color="gray", linestyle=":", alpha=0.5)
    ax.axvline(-0.5, color="gray", linestyle=":", alpha=0.5)
    ax.set_xlabel("Cohen's d_z (per-run paired) — clamped to ±5 for readability")
    ax.set_title("Pilot v3 — per-run effect size by project (12 of 36 done before pause)")
    for i, (n, d) in enumerate(zip(notes2, dz_vals)):
        if n:
            ax.annotate(n, xy=(d, i), xytext=(d + np.sign(d)*0.2, i),
                        fontsize=8, va="center",
                        ha="left" if d > 0 else "right", color="gray")
    ax.grid(axis="x", linestyle=":", alpha=0.5)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig2_v3_dz_distribution.png", dpi=150)
    plt.close()
    print(f"  wrote fig2_v3_dz_distribution.png — {len(rows)} projects")


def fig3_kb_size_vs_effect():
    """Figure 3: KB chunk count vs F1 delta, hand-curated v2 only (where chunks are known)."""
    # Hand-coded chunk counts from the v2 pilot runs (printed at ingest time)
    kb_chunks = {"bitwarden": 133, "keycloak": 548, "nextcloud": 9, "strapi": 23}
    rows = load_all("results_compare_runs_pilot_v2_kb_fixed")
    rows = [r for r in rows if r["project"] in kb_chunks]
    if not rows:
        return
    xs = [kb_chunks[r["project"]] for r in rows]
    ys = [r["delta"] for r in rows]
    labels = [r["project"] for r in rows]
    sigs = [r["pr_p"] < 0.05 for r in rows]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    for x, y, lab, sig in zip(xs, ys, labels, sigs):
        ax.scatter(x, y, s=120, color="#1f77b4" if sig else "#888",
                   edgecolor="black", lw=0.6, zorder=3)
        ax.annotate(lab, xy=(x, y), xytext=(8, 6), textcoords="offset points",
                    fontsize=10, fontweight="bold" if sig else "normal")
    ax.axhline(0, color="black", lw=0.5)
    ax.set_xscale("log")
    ax.set_xlabel("KB chunks ingested (log scale)")
    ax.set_ylabel("Δ F1  (RAG mean − baseline mean)")
    ax.set_title("KB richness vs RAG benefit — hand-curated v2 (significant projects bold)")
    ax.grid(linestyle=":", alpha=0.5)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig3_kb_size_vs_effect.png", dpi=150)
    plt.close()
    print(f"  wrote fig3_kb_size_vs_effect.png — {len(rows)} projects")


def fig4_keycloak_per_run_trajectories():
    """Figure 4: keycloak v2 per-run F1 trajectories (showing 10/10 RAG > baseline)."""
    r = latest_report("keycloak", "results_compare_runs_pilot_v2_kb_fixed")
    if r is None:
        return
    b = r["baseline_aggregate"]["f1_score"]["values"]
    g = r["rag_aggregate"]["f1_score"]["values"]
    runs = np.arange(1, len(b) + 1)

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(runs, b, "o-", color="#888", label="baseline", markersize=8)
    ax.plot(runs, g, "s-", color="#1f77b4", label="RAG", markersize=8)
    for i, (bv, gv) in enumerate(zip(b, g), 1):
        ax.plot([i, i], [bv, gv], color="#d62728" if gv < bv else "#2ca02c", lw=1, alpha=0.5)
    ax.set_xlabel("run index (paired)")
    ax.set_ylabel("aggregate F1 score (across 8 stories)")
    ax.set_title(f"Keycloak v2 — RAG > baseline in 10/10 paired runs  (Wilcoxon p=0.002, d_z=+1.95)")
    ax.legend(loc="lower right")
    ax.grid(linestyle=":", alpha=0.5)
    ax.set_xticks(runs)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig4_keycloak_trajectories.png", dpi=150)
    plt.close()
    print(f"  wrote fig4_keycloak_trajectories.png")


def fig5_journey_timeline():
    """Figure 5: timeline of phases in this work."""
    fig, ax = plt.subplots(figsize=(11, 3.5))
    phases = [
        ("Build agent-council\n(PR1–PR9.6)", 0, 14, "#888"),
        ("Autopilot v1 →\nVETO (SPEC_MISMATCH)", 14, 1, "#d62728"),
        ("Methodology pivot:\n3 proposals", 15, 1, "#ffbb33"),
        ("Validation council:\n2 of 3 council sessions", 16, 0.5, "#ffbb33"),
        ("Autopilot v2 →\nNO veto", 16.5, 0.5, "#2ca02c"),
        ("Pilot v1 (broken KB)", 17, 0.3, "#d62728"),
        ("Pilot v2\n(per-project KB,\n4 projects)", 17.3, 0.5, "#2ca02c"),
        ("Pilot v3\n(36 auto-bootstrap,\n12 done, paused)", 17.8, 0.7, "#ffbb33"),
        ("Reflection\n(now)", 18.5, 0.3, "#1f77b4"),
    ]
    for label, start, dur, color in phases:
        ax.barh(0, dur, left=start, height=0.5, color=color, edgecolor="black", lw=0.5)
        ax.text(start + dur/2, 0, label, ha="center", va="center", fontsize=9, color="white" if color != "#ffbb33" else "black")
    ax.set_xlim(-0.3, 19.5)
    ax.set_ylim(-0.5, 0.5)
    ax.set_yticks([])
    ax.set_xlabel("Days (approximate)")
    ax.set_title("Journey from agent-council to thesis pilots")
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig5_journey_timeline.png", dpi=150)
    plt.close()
    print(f"  wrote fig5_journey_timeline.png")


def fig6_n20_per_run_dz():
    """Figure 6: per-run d_z across all 10 N=20 projects, sorted, with
    significance markers. The thesis-grade headline figure."""
    rows = load_all("results_compare_runs_thesis_n20")
    if not rows:
        return
    rows_sorted = sorted(rows, key=lambda r: r["pr_dz"])
    projects = [r["project"] for r in rows_sorted]
    dz_vals = [r["pr_dz"] for r in rows_sorted]
    p_vals = [r["pr_p"] for r in rows_sorted]
    # color: significant positive=green, significant negative=red, ns=grey
    colors = []
    sig_marks = []
    for d, p in zip(dz_vals, p_vals):
        if p < 0.05 and d > 0:
            colors.append("#2ca02c"); sig_marks.append("*" if p >= 0.01 else ("**" if p >= 0.001 else "***"))
        elif p < 0.05 and d < 0:
            colors.append("#d62728"); sig_marks.append("*" if p >= 0.01 else ("**" if p >= 0.001 else "***"))
        else:
            colors.append("#888"); sig_marks.append("")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(np.arange(len(projects)), dz_vals, color=colors)
    ax.set_yticks(np.arange(len(projects)))
    ax.set_yticklabels(projects, fontsize=10)
    ax.axvline(0, color="black", lw=0.5)
    for tx in (-0.5, 0.5, -0.8, 0.8):
        ax.axvline(tx, color="gray", linestyle=":", alpha=0.4)
    ax.set_xlabel("Cohen's d_z (per-run paired test)")
    ax.set_title("Pilot N=20 — per-run effect size by project (final, all 10 hand-curated)")
    for i, (d, mark, p) in enumerate(zip(dz_vals, sig_marks, p_vals)):
        if mark:
            ax.annotate(f"{mark} (p={p:.4f})", xy=(d, i), xytext=(d + np.sign(d)*0.05, i),
                        fontsize=9, va="center", ha="left" if d > 0 else "right")
        else:
            ax.annotate(f"p={p:.3f}", xy=(d, i), xytext=(d + np.sign(d)*0.05 if d != 0 else 0.05, i),
                        fontsize=8, va="center", ha="left" if d >= 0 else "right", color="gray")
    ax.grid(axis="x", linestyle=":", alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig6_n20_per_run_dz.png", dpi=150)
    plt.close()
    print(f"  wrote fig6_n20_per_run_dz.png — {len(rows)} projects")


def fig7_n20_per_story_paired_diffs():
    """Figure 7: stripchart of per-story (rag-baseline) means across
    all stories, all projects. Visualises the heterogeneity that the
    pooled per-story test (p=0.528) is averaging over."""
    rows = load_all("results_compare_runs_thesis_n20")
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    yticks = []
    yticklabels = []
    for i, r in enumerate(sorted(rows, key=lambda r: r["pr_dz"])):
        # We need per-story diffs; load directly
        import glob
        rpath = sorted(glob.glob(str(TEST_CASES / r["project"] / "results_compare_runs_thesis_n20" / "run_comparison_*" / "multi_run_report.json")))[-1]
        rpt = json.load(open(rpath))
        matrix = rpt.get("per_run_per_story_f1") or {}
        b = matrix.get("baseline", {}); g = matrix.get("rag", {})
        story_ids = sorted(b.keys())
        diffs = [
            (sum(g[s])/len(g[s])) - (sum(b[s])/len(b[s]))
            for s in story_ids
        ]
        # jitter the y position
        ys = np.full(len(diffs), i) + np.random.uniform(-0.15, 0.15, len(diffs))
        col = ("#2ca02c" if r["pr_dz"] > 0 and r["pr_p"] < 0.05
               else "#d62728" if r["pr_dz"] < 0 and r["pr_p"] < 0.05
               else "#888")
        ax.scatter(diffs, ys, s=36, color=col, alpha=0.7, edgecolor="black", lw=0.4, zorder=3)
        yticks.append(i)
        yticklabels.append(r["project"])
    ax.axvline(0, color="black", lw=0.5)
    ax.set_yticks(yticks)
    ax.set_yticklabels(yticklabels, fontsize=10)
    ax.set_xlabel("per-story F1 difference (RAG mean − baseline mean)")
    ax.set_title("Per-story F1 differences (8 stories per project, N=20 paired runs)")
    ax.grid(axis="x", linestyle=":", alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "fig7_n20_per_story_diffs.png", dpi=150)
    plt.close()
    print(f"  wrote fig7_n20_per_story_diffs.png")


if __name__ == "__main__":
    print(f"Generating figures into {FIG_DIR}/")
    fig1_v2_baseline_vs_rag()
    fig2_v3_dz_distribution()
    fig3_kb_size_vs_effect()
    fig4_keycloak_per_run_trajectories()
    fig5_journey_timeline()
    fig6_n20_per_run_dz()
    fig7_n20_per_story_paired_diffs()
    print("Done.")
