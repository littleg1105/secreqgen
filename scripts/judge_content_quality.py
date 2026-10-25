"""LLM-as-judge content-quality scorer for generated security requirements.

Independent of the F1 phrasing-matcher. For each generated requirement,
ask llama3:8b: "Does this address a real security concern raised by the
user story?" The verdict is `relevant`, `partial`, or `irrelevant`, plus
a one-line reason.

This metric is phrasing-independent — a generated req with novel
wording but on-target content scores `relevant`, where F1's
0.85-cosine-similarity matcher would call it no_match.

Per cell (run_001 of each of the 20 cells), score:
  - all baseline generated reqs
  - all RAG generated reqs

Output:
  /tmp/cross_family_content_quality.json — full structured results
  docs/CROSS_FAMILY_CONTENT_QUALITY_2026-05-23.md — readable summary

Per-cell metric: content-quality "relevance rate" = (relevant + 0.5*partial) / total_reqs.
Per-cell comparison: rag_relevance - baseline_relevance, paired d_z across
the 8 stories of the cell.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/judge_content_quality.py
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from statistics import mean, stdev

from secreqgen.generation.llm_client import OllamaClient

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
OUT_JSON = Path("/tmp/cross_family_content_quality.json")
OUT_MD = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/docs/CROSS_FAMILY_CONTENT_QUALITY_2026-05-23.md")

MODELS = [
    ("qwen3",      "qwen3_30b-a3b-instruct-2507-q4_K_M"),
    ("qwen3.6",    "qwen3_6_latest"),
    ("gemma4",     "gemma4_26b"),
    ("mistral-sm", "mistral-small3_2_24b"),
]
PROJECTS = ["mastodon", "keycloak", "bitwarden", "gitlab", "mattermost"]


JUDGE_SYSTEM = """You are an OWASP ASVS-trained security reviewer. For each generated
security requirement, decide whether it addresses a security concern raised by the
user story.

Verdict scale (return EXACTLY one):
- "relevant": the requirement directly addresses a security concern of the story
  (auth for an auth story, validation for an input story, etc.) AND is specific
  enough to be actionable
- "partial": the requirement touches an adjacent or secondary security concern of
  the story but is not centrally on-topic, OR is on-topic but vague/generic
- "irrelevant": the requirement is off-topic (wrong feature area, hallucinated
  context, generic boilerplate unrelated to the story)

Output STRICTLY:
{"verdict": "relevant"|"partial"|"irrelevant", "reason": "<one short sentence>"}
No prose before or after the JSON."""

JUDGE_USER_TEMPLATE = """USER STORY [{story_id}] {story_title}
{story_text}

Acceptance criteria:
{acceptance_criteria}

GENERATED SECURITY REQUIREMENT:
Title: {req_title}
ASVS category: {req_asvs}
Description: {req_desc}

Output JSON only."""


def judge_one(client: OllamaClient, story: dict, req: dict) -> tuple[str, str]:
    ac = "\n".join(f"  - {x}" for x in story.get("acceptance_criteria", []))
    user_prompt = JUDGE_USER_TEMPLATE.format(
        story_id=story.get("id", "?"),
        story_title=story.get("title", "?"),
        story_text=story.get("story", "?"),
        acceptance_criteria=ac or "  (none)",
        req_title=req.get("title", "?"),
        req_asvs=req.get("asvs_category", "?"),
        req_desc=(req.get("description", "") or "")[:1500],
    )
    try:
        resp = client.generate(prompt=user_prompt, system_prompt=JUDGE_SYSTEM, format="json")
        obj = json.loads(resp.content.strip())
        v = str(obj.get("verdict", "")).strip().lower()
        if v in ("relevant", "partial", "irrelevant"):
            return v, str(obj.get("reason", ""))
        return "parse_error", f"bad verdict {v!r}"
    except Exception as e:
        return "parse_error", f"{type(e).__name__}: {e}"


def relevance_rate(verdicts: list[str]) -> float:
    if not verdicts:
        return 0.0
    n_rel = sum(1 for v in verdicts if v == "relevant")
    n_par = sum(1 for v in verdicts if v == "partial")
    return (n_rel + 0.5 * n_par) / len(verdicts)


def paired_d_z(diffs: list[float]) -> tuple[float, float | None]:
    """Paired Cohen's d_z and 2-sided p-value via approximation."""
    if not diffs:
        return 0.0, None
    n = len(diffs)
    mu = mean(diffs)
    if n < 2:
        return 0.0, None
    sd = stdev(diffs)
    if sd == 0:
        return 0.0, None
    dz = mu / sd
    # Use t-test approximation for p-value
    import math
    t = dz * math.sqrt(n)
    # 2-sided p via Student's t survival (approximate via normal for n>=8)
    # Skip importing scipy; use rough cutoffs
    return dz, None  # We'll just report d_z; the cell d_z directionality is the signal


def latest_run(d: Path) -> Path | None:
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
    return rcs[-1] if rcs else None


def main():
    client = OllamaClient(model_name="llama3:8b", temperature=0.0, max_tokens=200, timeout=120.0)
    if not client.check_health():
        print("Ollama not healthy; aborting")
        return

    cells_out = []
    t0 = time.perf_counter()
    total_judge_calls = 0

    for proj in PROJECTS:
        for label, slug in MODELS:
            d = ROOT / proj / f"results_compare_runs_winning_{slug}_n10"
            if not d.exists():
                print(f"  skip (missing dir): {proj} × {label}")
                continue
            rd = latest_run(d)
            if not rd:
                continue
            r1 = json.load(open(rd / "raw_runs" / "run_001.json"))

            print(f"\n[{proj} × {label}] judging story reqs...")
            cell_t0 = time.perf_counter()
            story_results = []
            for s in r1["per_story_results"]:
                sid = s["story_id"]
                story_dict = {
                    "id": sid,
                    "title": s.get("story_title", "?"),
                    "story": (s.get("baseline_result") or {}).get("user_story", {}).get("story", "?"),
                    "acceptance_criteria": (s.get("baseline_result") or {}).get("user_story", {}).get("acceptance_criteria", []),
                }
                bl_reqs = (s.get("baseline_result") or {}).get("requirements", []) or []
                rg_reqs = (s.get("rag_result") or {}).get("requirements", []) or []

                bl_verdicts = []
                rg_verdicts = []
                for r in bl_reqs:
                    v, reason = judge_one(client, story_dict, r)
                    bl_verdicts.append({"title": r.get("title", "?")[:80], "verdict": v, "reason": reason[:150]})
                    total_judge_calls += 1
                for r in rg_reqs:
                    v, reason = judge_one(client, story_dict, r)
                    rg_verdicts.append({"title": r.get("title", "?")[:80], "verdict": v, "reason": reason[:150]})
                    total_judge_calls += 1

                bl_rate = relevance_rate([x["verdict"] for x in bl_verdicts])
                rg_rate = relevance_rate([x["verdict"] for x in rg_verdicts])
                story_results.append({
                    "story_id": sid,
                    "n_bl": len(bl_verdicts), "n_rg": len(rg_verdicts),
                    "bl_verdicts": bl_verdicts, "rg_verdicts": rg_verdicts,
                    "bl_rate": bl_rate, "rg_rate": rg_rate,
                    "delta": rg_rate - bl_rate,
                })

            # Cell-level summary
            bl_rates = [s["bl_rate"] for s in story_results]
            rg_rates = [s["rg_rate"] for s in story_results]
            deltas = [s["delta"] for s in story_results]
            cell_dz, _ = paired_d_z(deltas)
            cell_elapsed = time.perf_counter() - cell_t0
            cells_out.append({
                "project": proj, "model": label,
                "n_stories": len(story_results),
                "bl_relevance_mean": mean(bl_rates) if bl_rates else 0,
                "rg_relevance_mean": mean(rg_rates) if rg_rates else 0,
                "delta_mean": mean(deltas) if deltas else 0,
                "d_z": cell_dz,
                "story_results": story_results,
                "elapsed_sec": cell_elapsed,
            })
            print(f"  bl_rel={mean(bl_rates):.3f} rg_rel={mean(rg_rates):.3f} d_z={cell_dz:+.3f} ({cell_elapsed:.0f}s, {total_judge_calls} calls)")
            OUT_JSON.write_text(json.dumps(cells_out, indent=2))  # incremental save

    elapsed_min = (time.perf_counter() - t0) / 60
    print(f"\nTotal: {total_judge_calls} judge calls in {elapsed_min:.1f} min")

    # ─── Build readable summary ──────────────────────────────────────────
    lines = []
    def w(*args): lines.append(" ".join(str(a) for a in args))

    w("# Cross-Family Content-Quality Judge — 2026-05-23")
    w("")
    w(f"LLM-as-judge content-quality scoring for the 20 cells of the cross-family experiment.")
    w(f"Independent of F1 phrasing-matcher.")
    w("")
    w(f"- Judge: `llama3:8b` (temperature 0.0)")
    w(f"- Scope: each (story, generated_requirement) pair, for both baseline and RAG conditions, from run_001 of each of the 20 cells.")
    w(f"- Total judge calls: {total_judge_calls}")
    w(f"- Verdict scale: `relevant` (directly addresses a story security concern), `partial` (adjacent/vague), `irrelevant` (off-topic).")
    w(f"- Relevance rate = (relevant + 0.5×partial) / total_reqs")
    w("")
    w(f"## Per-cell content-quality table")
    w("")
    w("| project | model | bl relevance | rg relevance | Δ | per-story d_z |")
    w("|---|---|---|---|---|---|")
    for c in cells_out:
        sign = "+" if c["delta_mean"] >= 0 else "−"
        emph = " **+**" if c["d_z"] > 0.5 else (" **−**" if c["d_z"] < -0.5 else "")
        w(f"| {c['project']} | {c['model']} | {c['bl_relevance_mean']:.3f} | {c['rg_relevance_mean']:.3f} | {c['delta_mean']:+.3f} | {c['d_z']:+.2f}{emph} |")

    # Aggregate per model + per project
    w("")
    w("## Aggregate per-model relevance (mean across 5 projects)")
    w("")
    w("| model | mean bl relevance | mean rg relevance | mean Δ |")
    w("|---|---|---|---|")
    for label, _ in MODELS:
        ms = [c for c in cells_out if c["model"] == label]
        if not ms: continue
        bl_m = mean(c["bl_relevance_mean"] for c in ms)
        rg_m = mean(c["rg_relevance_mean"] for c in ms)
        d_m = mean(c["delta_mean"] for c in ms)
        w(f"| {label} | {bl_m:.3f} | {rg_m:.3f} | {d_m:+.3f} |")

    w("")
    w("## Aggregate per-project relevance (mean across 4 models)")
    w("")
    w("| project | mean bl relevance | mean rg relevance | mean Δ |")
    w("|---|---|---|---|")
    for proj in PROJECTS:
        ms = [c for c in cells_out if c["project"] == proj]
        if not ms: continue
        bl_m = mean(c["bl_relevance_mean"] for c in ms)
        rg_m = mean(c["rg_relevance_mean"] for c in ms)
        d_m = mean(c["delta_mean"] for c in ms)
        w(f"| {proj} | {bl_m:.3f} | {rg_m:.3f} | {d_m:+.3f} |")

    OUT_MD.write_text("\n".join(lines))
    print(f"\nWrote summary: {OUT_MD}")


if __name__ == "__main__":
    main()
