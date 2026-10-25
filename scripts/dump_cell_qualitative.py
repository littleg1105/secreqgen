"""Dump generated requirements alongside ground truth for one (project, model) cell.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/dump_cell_qualitative.py PROJECT MODEL_SLUG STORY_INDEX

  PYTHONPATH=src .venv/bin/python scripts/dump_cell_qualitative.py \\
    mastodon qwen3_30b-a3b-instruct-2507-q4_K_M 0
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")


def load_gt_for_story(project: str, story_id: str) -> list[dict]:
    gt = json.load(open(ROOT / project / "ground_truth.json"))
    items = gt.get("ground_truth", gt) if isinstance(gt, dict) else gt
    for s in items:
        if s.get("story_id") == story_id:
            return s.get("requirements", [])
    return []


def fmt_req(r: dict, max_len: int = 220) -> str:
    title = r.get("title", "?")
    desc = r.get("description", "")[:max_len]
    asvs = r.get("asvs_category", "")
    return f"    title: {title}\n    asvs:  {asvs}\n    desc:  {desc}"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    project = sys.argv[1]
    model_slug = sys.argv[2]
    story_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    d = ROOT / project / f"results_compare_runs_winning_{model_slug}_n10"
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
    run_dir = rcs[-1]
    r1 = json.load(open(run_dir / "raw_runs" / "run_001.json"))
    psr = r1["per_story_results"]
    if story_idx >= len(psr):
        print(f"story_idx {story_idx} out of range (max {len(psr)-1})")
        return
    s = psr[story_idx]
    sid = s["story_id"]
    title = s.get("story_title", "?")

    print(f"\n{'='*100}")
    print(f"  {project} × {model_slug}")
    print(f"  Story {story_idx}: [{sid}] {title}")
    print('='*100)

    # Ground truth
    gt_reqs = load_gt_for_story(project, sid)
    print(f"\n  GROUND TRUTH ({len(gt_reqs)} requirements):")
    for i, r in enumerate(gt_reqs):
        print(f"  GT-{i+1}.")
        print(fmt_req(r))

    # Baseline generated
    bl = s.get("baseline_result", {}).get("requirements", [])
    print(f"\n  BASELINE GENERATED ({len(bl)} requirements):")
    for i, r in enumerate(bl):
        print(f"  BL-{i+1}.")
        print(fmt_req(r))

    # RAG generated
    rg = s.get("rag_result", {}).get("requirements", [])
    print(f"\n  RAG GENERATED ({len(rg)} requirements):")
    for i, r in enumerate(rg):
        print(f"  RG-{i+1}.")
        print(fmt_req(r))

    # Match decisions
    print(f"\n  MATCH DECISIONS:")
    print(f"  Baseline matches:")
    for m in s.get("baseline_matches", []):
        gen_t = (m.get("generated_text") or "")[:70]
        gt_t = (m.get("ground_truth_text") or "")[:70]
        sim = m.get("similarity_score", 0)
        mt = m.get("match_type", "?")
        print(f"    [{mt}] sim={sim:.2f}  gen={gen_t!r}  ↔  gt={gt_t!r}")
    print(f"  RAG matches:")
    for m in s.get("rag_matches", []):
        gen_t = (m.get("generated_text") or "")[:70]
        gt_t = (m.get("ground_truth_text") or "")[:70]
        sim = m.get("similarity_score", 0)
        mt = m.get("match_type", "?")
        print(f"    [{mt}] sim={sim:.2f}  gen={gen_t!r}  ↔  gt={gt_t!r}")


if __name__ == "__main__":
    main()
