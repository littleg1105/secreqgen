"""Generate a stratified hand-labeling sample for LLM-judge calibration.

Reads judged-cell JSON files produced by `rag_ablation_judge.py` and
emits a hand-labeling worksheet plus a sidecar containing the LLM
verdicts. After hand-labeling, run `score_judge_calibration.py` to
compute Cohen's κ.

Inputs (the script auto-discovers what exists in /tmp/):
  /tmp/rag_ablation_judged.json
  /tmp/rag_ablation_llm_query_judged.json
  /tmp/rag_ablation_agents_judged.json
  /tmp/rag_ablation_idea19_enriched_judged.json

Outputs:
  docs/judge_calibration_sample.md     — labeling worksheet (one block / pair)
  docs/judge_calibration_sample.json   — sidecar with LLM verdicts (kept private)

Sampling:
  Stratified by verdict class (relevant / partial / irrelevant) so the
  labelled set tests the judge's *discrimination*, not just its modal
  prediction. Default: 10 per class. Within each class, pairs are drawn
  uniformly across (project, idea) cells.

Usage:
  cd secreqgen && PYTHONPATH=src .venv/bin/python \
    scripts/generate_judge_calibration_sample.py [--per-class 10]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

DEFAULT_INPUTS = [
    "/tmp/rag_ablation_judged.json",
    "/tmp/rag_ablation_llm_query_judged.json",
    "/tmp/rag_ablation_agents_judged.json",
    "/tmp/rag_ablation_idea19_enriched_judged.json",
]


def load_all_pairs(inputs: list[Path]) -> list[dict]:
    """Flatten all judged JSONs into a list of (story, chunk, verdict) records."""
    pairs = []
    for p in inputs:
        if not p.exists():
            print(f"  skipping (missing): {p}")
            continue
        cells = json.loads(p.read_text())
        for cell in cells:
            if "per_story_judged" not in cell:
                continue
            for s in cell["per_story_judged"]:
                for cv in s.get("chunk_verdicts", []):
                    pairs.append({
                        "source_file": p.name,
                        "idea_id": cell.get("idea_id"),
                        "idea_name": cell.get("idea_name"),
                        "project": cell.get("project"),
                        "story_id": s.get("story_id"),
                        "story_title": s.get("story_title"),
                        "chunk_index": cv.get("chunk_index"),
                        "chunk_source": cv.get("source", ""),
                        "relevance_score": cv.get("relevance_score"),
                        "llm_verdict": cv.get("verdict"),
                        "llm_reason": cv.get("reason", ""),
                    })
    return pairs


def load_story_lookup(real_test_cases: Path) -> dict[str, dict]:
    """Walk real_test_cases/<proj>/user_stories.json into {story_id: story}."""
    lookup = {}
    for p in real_test_cases.glob("*/user_stories.json"):
        try:
            raw = json.loads(p.read_text())
            stories = raw.get("user_stories", raw) if isinstance(raw, dict) else raw
            for s in stories:
                if isinstance(s, dict) and "id" in s:
                    lookup[s["id"]] = s
        except Exception:
            continue
    return lookup


def load_chunk_content(judged_inputs: list[Path], retrieval_inputs: list[Path]) -> dict:
    """Map (idea_id, project, story_id, chunk_index) → chunk content from retrieval JSONs.

    Keyed by idea_id too because chunk_index N means a different chunk
    across different ideas (each retrieval method returns different chunks
    at the same rank slot).
    """
    chunk_lookup = {}
    for p in retrieval_inputs:
        if not p.exists():
            continue
        cells = json.loads(p.read_text())
        for cell in cells:
            proj = cell.get("project")
            idea_id = cell.get("idea_id")
            for s in cell.get("per_story_results", []):
                sid = s.get("story_id")
                ctx = (s.get("rag_result") or {}).get("retrieval_context") or ""
                # Format from format_context: "### [N] source > section (Relevance: X.XX)\n<body>"
                blocks = []
                cur = []
                for line in ctx.split("\n"):
                    if line.startswith("### ["):
                        if cur:
                            blocks.append("\n".join(cur))
                        cur = [line]
                    elif cur:
                        cur.append(line)
                if cur:
                    blocks.append("\n".join(cur))
                for idx, blk in enumerate(blocks, start=1):
                    chunk_lookup[(idea_id, proj, sid, idx)] = blk
    return chunk_lookup


def stratify(pairs: list[dict], per_class: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    by_verdict = {"relevant": [], "partial": [], "irrelevant": []}
    for p in pairs:
        if p["llm_verdict"] in by_verdict:
            by_verdict[p["llm_verdict"]].append(p)
    sample = []
    for v, items in by_verdict.items():
        if not items:
            print(f"  WARNING: no items with verdict={v}")
            continue
        rng.shuffle(items)
        sample.extend(items[:per_class])
        print(f"  sampled {min(per_class, len(items))} of {len(items)} '{v}'")
    rng.shuffle(sample)
    for i, p in enumerate(sample):
        p["sample_id"] = f"S{i+1:03d}"
    return sample


def write_worksheet(sample: list[dict],
                    story_lookup: dict,
                    chunk_lookup: dict,
                    out_md: Path) -> None:
    lines = [
        "# Judge Calibration — Hand-Labeling Worksheet",
        "",
        "**Task**: For each (story, chunk) pair below, decide whether the chunk's",
        "content is **relevant** to the story's security requirements work, ",
        "**partial** (useful background but doesn't directly answer), or ",
        "**irrelevant**.",
        "",
        "Fill in the `Human verdict` line under each block with one of:",
        "`relevant`, `partial`, `irrelevant`.",
        "",
        "**DO NOT** look at `judge_calibration_sample.json` while labeling — that",
        "contains the LLM's predictions which would bias you. After all blocks",
        "are filled, run `scripts/score_judge_calibration.py` to compute Cohen's κ.",
        "",
        "Guidelines for the `partial` boundary:",
        "- **relevant** = the chunk directly addresses one or more of the story's",
        "  security requirements (e.g. enumerates auth requirements for an auth",
        "  story, specifies threat-model items for a threat-model story).",
        "- **partial** = the chunk is on a related topic (admin config, an",
        "  adjacent feature's docs) and would inform some requirements but is not",
        "  itself a requirements specification.",
        "- **irrelevant** = the chunk is off-topic (covers a different feature,",
        "  generic boilerplate, project metadata, unrelated security topic).",
        "",
        "---",
        "",
    ]
    for i, s in enumerate(sample):
        sid = s["story_id"]
        story = story_lookup.get(sid, {})
        chunk_content = chunk_lookup.get(
            (s["idea_id"], s["project"], sid, s["chunk_index"]),
            "(chunk content not found in retrieval JSON — likely idea_id missing from lookup)"
        )
        # Truncate chunk content if too long
        if len(chunk_content) > 2000:
            chunk_content = chunk_content[:2000] + "\n\n... [truncated] ..."
        ac = story.get("acceptance_criteria", [])
        ac_block = "\n".join(f"  - {x}" for x in ac) if ac else "  (none provided)"
        lines.extend([
            f"## {s['sample_id']} — {s['project']}/{sid}",
            "",
            f"**Story**: {s['story_title']}",
            "",
            f"**Story text**: {story.get('story', '(text not found)')}",
            "",
            "**Acceptance criteria**:",
            ac_block,
            "",
            "**Retrieved chunk source**:",
            f"`{s['chunk_source']}`",
            "",
            "**Retrieved chunk content**:",
            "```",
            chunk_content,
            "```",
            "",
            "**Human verdict**: `____________`  *(relevant / partial / irrelevant)*",
            "",
            "---",
            "",
        ])
    out_md.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=10,
                    help="Number of pairs to sample per verdict class (default: 10).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-md", type=Path,
                    default=Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/docs/judge_calibration_sample.md"))
    ap.add_argument("--out-json", type=Path,
                    default=Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/secreqgen/docs/judge_calibration_sample.json"))
    args = ap.parse_args()

    print("Loading judged-pair pool from:")
    judged_inputs = [Path(p) for p in DEFAULT_INPUTS]
    pairs = load_all_pairs(judged_inputs)
    print(f"Total judged (story, chunk) pairs: {len(pairs)}")

    print("Loading user_stories.json lookup...")
    real_test_cases = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
    story_lookup = load_story_lookup(real_test_cases)
    print(f"  loaded {len(story_lookup)} stories from {len(list(real_test_cases.glob('*/user_stories.json')))} project files")

    # Match retrieval JSONs to their judged counterparts (judged JSONs strip the chunk content)
    retrieval_inputs = [
        Path("/tmp/rag_ablation_retrieval.json"),
        Path("/tmp/rag_ablation_llm_query.json"),
        Path("/tmp/rag_ablation_agents.json"),
        Path("/tmp/rag_ablation_idea19_enriched.json"),
    ]
    print("Loading chunk-content lookup from retrieval JSONs...")
    chunk_lookup = load_chunk_content(judged_inputs, retrieval_inputs)
    print(f"  indexed {len(chunk_lookup)} (project, story, chunk_index) entries")

    print(f"\nStratifying sample (per_class={args.per_class}, seed={args.seed})...")
    sample = stratify(pairs, per_class=args.per_class, seed=args.seed)
    print(f"Total sample size: {len(sample)}")

    print(f"\nWriting worksheet → {args.out_md}")
    write_worksheet(sample, story_lookup, chunk_lookup, args.out_md)

    # Sidecar with LLM verdicts (kept private from the labeller)
    print(f"Writing sidecar → {args.out_json}")
    sidecar = [
        {
            "sample_id": s["sample_id"],
            "project": s["project"],
            "story_id": s["story_id"],
            "chunk_index": s["chunk_index"],
            "chunk_source": s["chunk_source"],
            "llm_verdict": s["llm_verdict"],
            "llm_reason": s["llm_reason"],
            "idea_id": s["idea_id"],
            "idea_name": s["idea_name"],
            "source_file": s["source_file"],
        }
        for s in sample
    ]
    args.out_json.write_text(json.dumps(sidecar, indent=2))
    print(f"\n✓ Done. Open {args.out_md.name} and fill in `Human verdict` lines.")
    print(f"  Then run scripts/score_judge_calibration.py to compute Cohen's κ.")


if __name__ == "__main__":
    main()
