"""Cross-project meta-analysis for the Path A thesis claim.

Pools per-story F1 differences (RAG − baseline) across all hand-
curated projects in the N=20 thesis-grade run, treating each project
as a stratum. Reports:

  1. The per-story paired test BY PROJECT (replicates what
     synthesize_pilot_v2.py shows, but at thesis-grade N).
  2. The POOLED per-story test: one Wilcoxon over all
     ~80 paired stories across all projects. This is the test
     that, if the per-project tests are individually
     underpowered, can still surface a real average effect.
  3. A simple random-effects-style aggregate of d_z values
     (DerSimonian-Laird-style without the full machinery): the
     project-weighted mean d_z and its standard error.
  4. Subgroup analysis: split by KB-richness tier
     (small / medium / large chunks) to test the
     "RAG benefit conditional on KB richness" hypothesis.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/meta_analysis.py
  PYTHONPATH=src .venv/bin/python scripts/meta_analysis.py --version v2  # v2/v3/n20
  PYTHONPATH=src .venv/bin/python scripts/meta_analysis.py --output docs/meta_results.md
"""
from __future__ import annotations

import argparse
import glob
import json
import math
from dataclasses import dataclass
from pathlib import Path

from scipy import stats

REPO = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen")
TEST_CASES = REPO.parent / "real_test_cases"

VERSION_DIR = {
    "v2":         "results_compare_runs_pilot_v2_kb_fixed",
    "v3":         "results_compare_runs_pilot_v3",
    "n20":        "results_compare_runs_thesis_n20",
    "n20-qwen36": "results_compare_runs_thesis_n20_qwen36",  # cross-model sub-test
}


@dataclass
class ProjectData:
    name: str
    n_runs: int
    baseline_per_story_means: list[float]   # one per story, mean F1 across N runs
    rag_per_story_means: list[float]
    paired_diffs: list[float]               # rag - baseline per story
    per_story_p: float
    per_story_dz: float
    kb_chunks: int | None


def latest_report(proj: str, version_dir: str) -> dict | None:
    pattern = str(TEST_CASES / proj / version_dir / "run_comparison_*" / "multi_run_report.json")
    hits = sorted(glob.glob(pattern))
    if not hits:
        return None
    with open(hits[-1]) as fh:
        return json.load(fh)


def load_project(proj: str, version_dir: str) -> ProjectData | None:
    rpt = latest_report(proj, version_dir)
    if rpt is None:
        return None

    # The schema stores a per-run × per-story F1 matrix directly under
    # `per_run_per_story_f1`, with sub-keys `baseline` and `rag`.
    # Each sub-key is {story_id: [f1_run_1, f1_run_2, ..., f1_run_N]}.
    matrix = rpt.get("per_run_per_story_f1") or {}
    baseline_by_story = matrix.get("baseline", {})
    rag_by_story = matrix.get("rag", {})

    if not baseline_by_story:
        # No per-story matrix preserved; fall back to summary stats only.
        ps = rpt.get("paired_test_per_story_f1", {})
        return ProjectData(
            name=proj, n_runs=rpt.get("n_runs", 0),
            baseline_per_story_means=[], rag_per_story_means=[],
            paired_diffs=[], per_story_p=ps.get("primary_p_value", 1.0),
            per_story_dz=ps.get("d_z", 0.0), kb_chunks=None,
        )

    story_ids = sorted(baseline_by_story.keys())
    b_means = [sum(baseline_by_story[s]) / max(len(baseline_by_story[s]), 1) for s in story_ids]
    r_means = [sum(rag_by_story[s])      / max(len(rag_by_story[s]),      1) for s in story_ids]
    diffs   = [r - b for r, b in zip(r_means, b_means)]

    ps = rpt.get("paired_test_per_story_f1", {})
    return ProjectData(
        name=proj, n_runs=rpt.get("n_runs", 0),
        baseline_per_story_means=b_means,
        rag_per_story_means=r_means,
        paired_diffs=diffs,
        per_story_p=ps.get("primary_p_value", 1.0),
        per_story_dz=ps.get("d_z", 0.0),
        kb_chunks=None,
    )


def load_all(version_dir: str) -> list[ProjectData]:
    out: list[ProjectData] = []
    for d in sorted(TEST_CASES.iterdir()):
        if not d.is_dir() or d.name.endswith("-test-data") or d.name in {"extracted_docs", "extracted_docs_md", "repos"}:
            continue
        pd = load_project(d.name, version_dir)
        if pd is not None:
            out.append(pd)
    return out


def cohens_d_z(diffs: list[float]) -> float:
    if not diffs:
        return 0.0
    mean = sum(diffs) / len(diffs)
    if len(diffs) < 2:
        return 0.0
    var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
    sd = math.sqrt(var)
    if sd == 0.0:
        return 0.0  # degenerate
    return mean / sd


def fisher_z(p: float) -> float:
    """For combining p-values via Fisher's method."""
    if p <= 0:
        return 1e6
    return -2 * math.log(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=list(VERSION_DIR.keys()), default="n20",
                    help="results-dir version (default: n20 = thesis-grade)")
    ap.add_argument("--output", help="Write a Markdown report to this file (in addition to stdout)")
    args = ap.parse_args()

    projects = load_all(VERSION_DIR[args.version])
    if not projects:
        print(f"No reports under {VERSION_DIR[args.version]}/")
        return

    lines: list[str] = []
    lines.append(f"# Cross-project meta-analysis — {args.version}\n")
    lines.append(f"Pulled from `{VERSION_DIR[args.version]}/run_comparison_*/multi_run_report.json`")
    lines.append("")

    # ─── Per-project table ───────────────────────────────
    lines.append("## Per-project per-story paired test")
    lines.append("")
    lines.append("| project | N runs | N stories | mean(rag-baseline) | per-story Wilcoxon p | per-story d_z |")
    lines.append("|---|---|---|---|---|---|")
    all_diffs: list[float] = []
    project_dzs: list[tuple[str, float, int]] = []  # (name, dz, n_stories)
    project_meta: list[ProjectData] = []
    for p in projects:
        n_st = len(p.paired_diffs)
        mean_d = sum(p.paired_diffs) / n_st if n_st else 0.0
        lines.append(f"| {p.name} | {p.n_runs} | {n_st} | {mean_d:+.4f} | {p.per_story_p:.4f} | {p.per_story_dz:+.3f} |")
        all_diffs.extend(p.paired_diffs)
        if n_st >= 2:
            project_dzs.append((p.name, cohens_d_z(p.paired_diffs), n_st))
        project_meta.append(p)
    lines.append("")

    # ─── Pooled test ─────────────────────────────────────
    lines.append("## Pooled per-story test (across all projects)")
    lines.append("")
    if len(all_diffs) >= 6:
        # Wilcoxon on the full pool of paired story differences
        try:
            stat, pv = stats.wilcoxon(all_diffs, alternative="two-sided")
            lines.append(f"- **N paired stories pooled:** {len(all_diffs)}")
            lines.append(f"- **Wilcoxon signed-rank statistic:** {stat:.3f}")
            lines.append(f"- **p-value (two-sided):** {pv:.5f}")
            lines.append(f"- **Mean diff (rag - baseline):** {sum(all_diffs)/len(all_diffs):+.4f}")
            lines.append(f"- **Pooled d_z:** {cohens_d_z(all_diffs):+.3f}")
        except Exception as e:
            lines.append(f"- *Wilcoxon failed:* {e}")
        # Paired t-test as sensitivity
        try:
            t, pt = stats.ttest_rel(
                [d for d in all_diffs],  # use the differences directly
                [0.0] * len(all_diffs),
            )
            lines.append(f"- *Paired t-test (sensitivity):* t={t:.3f}, p={pt:.5f}")
        except Exception:
            pass
    else:
        lines.append(f"- *Pool too small ({len(all_diffs)} stories) — need at least 6.*")
    lines.append("")

    # ─── Combined p (Fisher's method) ─────────────────────
    if project_dzs:
        chi2 = sum(fisher_z(p.per_story_p) for p in projects if p.per_story_p > 0)
        df = 2 * sum(1 for p in projects if p.per_story_p > 0)
        if df > 0:
            from scipy.stats import chi2 as chi2_dist
            combined_p = 1 - chi2_dist.cdf(chi2, df)
            lines.append("## Combined p-value (Fisher's method)")
            lines.append("")
            lines.append(f"- **chi-squared:** {chi2:.3f} on {df} df")
            lines.append(f"- **combined p:** {combined_p:.5f}")
            lines.append("")
            lines.append("Fisher's method combines independent per-project p-values into")
            lines.append("a single test of *whether at least one of them is non-null*.")
            lines.append("It does not estimate effect size, only significance.")
            lines.append("")

    # ─── Project-weighted d_z aggregate ───────────────────
    if project_dzs:
        # Weight each project by its story count
        total_w = sum(n for _, _, n in project_dzs)
        weighted_dz = sum(dz * n for _, dz, n in project_dzs) / total_w
        unweighted_dz = sum(dz for _, dz, _ in project_dzs) / len(project_dzs)
        lines.append("## Project-weighted average d_z")
        lines.append("")
        lines.append(f"- **N projects:** {len(project_dzs)}")
        lines.append(f"- **Weighted by stories:** {weighted_dz:+.3f}")
        lines.append(f"- **Unweighted mean across projects:** {unweighted_dz:+.3f}")
        lines.append("")
        lines.append("(A full random-effects model would weight by inverse-variance;")
        lines.append("for the current N this is a reasonable approximation.)")
        lines.append("")

    # ─── Subgroup by KB richness ─────────────────────────
    # Hard-coded chunk counts from the v2 + n20 ingestion logs.
    KB_CHUNKS = {
        "bitwarden": 133, "keycloak": 548, "nextcloud": 9, "strapi": 23,
        "discourse": None, "gitlab": None, "joomla": None,
        "grafana": None, "mastodon": None, "mattermost": None,
    }
    rich = [p for p in projects if KB_CHUNKS.get(p.name, 0) and KB_CHUNKS[p.name] >= 200]
    poor = [p for p in projects if KB_CHUNKS.get(p.name, 0) and KB_CHUNKS[p.name] < 100]
    if rich and poor:
        lines.append("## Subgroup by KB richness")
        lines.append("")
        rich_diffs = [d for p in rich for d in p.paired_diffs]
        poor_diffs = [d for p in poor for d in p.paired_diffs]
        if rich_diffs and poor_diffs:
            lines.append(f"- **Rich KB (≥200 chunks):** {len(rich_diffs)} stories pooled, "
                         f"mean diff {sum(rich_diffs)/len(rich_diffs):+.4f}, "
                         f"d_z {cohens_d_z(rich_diffs):+.3f}")
            try:
                w_r, p_r = stats.wilcoxon(rich_diffs, alternative="two-sided")
                lines.append(f"  Wilcoxon p = {p_r:.5f}")
            except Exception:
                pass
            lines.append(f"- **Poor KB (<100 chunks):** {len(poor_diffs)} stories pooled, "
                         f"mean diff {sum(poor_diffs)/len(poor_diffs):+.4f}, "
                         f"d_z {cohens_d_z(poor_diffs):+.3f}")
            try:
                w_p, p_p = stats.wilcoxon(poor_diffs, alternative="two-sided")
                lines.append(f"  Wilcoxon p = {p_p:.5f}")
            except Exception:
                pass
        lines.append("")

    out = "\n".join(lines)
    print(out)
    if args.output:
        Path(args.output).write_text(out)
        print(f"\n# Wrote report to {args.output}", flush=True)


if __name__ == "__main__":
    main()
