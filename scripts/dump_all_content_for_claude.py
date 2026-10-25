"""Dump every generated requirement + ground truth, organized by project,
into one file per project for thorough manual reading.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
OUT = Path("/tmp/cross_family_content_for_review")
OUT.mkdir(exist_ok=True)

MODELS = [
    ("qwen3",      "qwen3_30b-a3b-instruct-2507-q4_K_M"),
    ("qwen3.6",    "qwen3_6_latest"),
    ("gemma4",     "gemma4_26b"),
    ("mistral-sm", "mistral-small3_2_24b"),
]
PROJECTS = ["mastodon", "keycloak", "bitwarden", "gitlab", "mattermost"]


def latest_run(d: Path) -> Path | None:
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_") and not x.name.endswith("_bug_pure_doc_only")])
    return rcs[-1] if rcs else None


def load_gt(project: str) -> dict[str, list[dict]]:
    gt = json.load(open(ROOT / project / "ground_truth.json"))
    items = gt.get("ground_truth", gt) if isinstance(gt, dict) else gt
    return {s.get("story_id"): s.get("requirements", []) for s in items}


def load_stories(project: str) -> dict[str, dict]:
    s = json.load(open(ROOT / project / "user_stories.json"))
    stories = s.get("user_stories", s) if isinstance(s, dict) else s
    return {x["id"]: x for x in stories}


def fmt_req(r: dict, idx: int, prefix: str) -> list[str]:
    lines = []
    title = r.get("title", "?")
    asvs = r.get("asvs_category", "?")
    desc = r.get("description", "") or ""
    rationale = r.get("rationale", "") or ""
    lines.append(f"  {prefix}-{idx}. [{asvs}] {title}")
    lines.append(f"    desc: {desc[:400]}")
    if rationale:
        lines.append(f"    why:  {rationale[:200]}")
    return lines


def main():
    for proj in PROJECTS:
        lines = []
        def w(*args): lines.append(" ".join(str(a) for a in args))

        gt_by_story = load_gt(proj)
        stories = load_stories(proj)

        w(f"# All generated content — {proj}")
        w("")
        w(f"4 models × 8 stories = 32 (model, story) pairs. Each pair has:")
        w(f"- Ground truth (hand-curated, ~3-4 reqs)")
        w(f"- Baseline generated (no RAG, 4-6 reqs)")
        w(f"- RAG generated (idea 19 retrieval + idea 15 few-shot, 5-6 reqs)")
        w("")

        # Iterate by story → all 4 models in sequence
        story_ids = sorted(stories.keys())
        for sid in story_ids:
            story = stories[sid]
            gt = gt_by_story.get(sid, [])
            w("=" * 100)
            w(f"## Story {sid}: {story.get('title','?')}")
            w("=" * 100)
            w(f"Story text: {story.get('story','?')}")
            w(f"Acceptance criteria:")
            for ac in story.get('acceptance_criteria', []):
                w(f"  - {ac}")
            w("")
            w(f"### Ground truth ({len(gt)} reqs):")
            for i, r in enumerate(gt):
                w(*fmt_req(r, i+1, "GT"))
            w("")

            for label, slug in MODELS:
                d = ROOT / proj / f"results_compare_runs_winning_{slug}_n10"
                if not d.exists():
                    w(f"### {label}: MISSING")
                    w("")
                    continue
                rd = latest_run(d)
                if not rd:
                    w(f"### {label}: no run_comparison")
                    w("")
                    continue
                r1_path = rd / "raw_runs" / "run_001.json"
                if not r1_path.exists():
                    w(f"### {label}: run_001.json not yet written (cell may be in-progress)")
                    w("")
                    continue
                r1 = json.load(open(r1_path))
                # find this story
                s = next((x for x in r1["per_story_results"] if x["story_id"] == sid), None)
                if not s:
                    w(f"### {label}: story not in run_001")
                    w("")
                    continue
                bl = (s.get("baseline_result") or {}).get("requirements", []) or []
                rg = (s.get("rag_result") or {}).get("requirements", []) or []
                w(f"### {label}")
                w(f"#### baseline ({len(bl)} reqs):")
                for i, r in enumerate(bl):
                    w(*fmt_req(r, i+1, "BL"))
                w(f"#### RAG ({len(rg)} reqs):")
                for i, r in enumerate(rg):
                    w(*fmt_req(r, i+1, "RG"))
                w("")

        (OUT / f"{proj}.md").write_text("\n".join(lines))
        print(f"Wrote {OUT / f'{proj}.md'} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
