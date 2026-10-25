"""Side-by-side qualitative dump: baseline vs idea-15 RAG vs idea-21 RAG.

For each (model, story), prints:
  - the story + acceptance criteria + ground truth
  - baseline requirements (idea-21 dir)
  - idea-15 RAG requirements (adversarial dir)
  - idea-21 RAG requirements (idea-21 dir)
  - extracted Stage-A token list (from idea-21 retrieval_context header)

Output: /tmp/idea21_qual.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(
    "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/mastodon_adversarial"
)
OUT = Path("/tmp/idea21_qual.md")

PROBE_TERMS = [
    r"CAS_UID_KEY", r"CAS_SECURITY_ASSUME_EMAIL_IS_VERIFIED", r"CAS_CA_PATH",
    r"CAS_DISABLE_SSL_VERIFICATION", r"CAS_DISPLAYNAME_KEY",
    r"CAS_EMAIL_KEY", r"CAS_IMAGE_KEY", r"CAS_SSL", r"CAS_CALLBACK_URL",
    r"LIMITED_FEDERATION_MODE",
    r"S3_HOSTNAME", r"S3_ALIAS_HOST", r"S3_BUCKET", r"S3_PROTOCOL", r"S3_ENDPOINT",
    r"ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH",
    r"followers_synchronization", r"followers_synchronisation",
    r"HTTP Signature[s]?", r"signature\.keyId", r"keyId",
    r"@uid@",
]
PROBE_RE = re.compile("|".join(PROBE_TERMS), re.IGNORECASE)

EXTRACTED_HEADER_RE = re.compile(
    r"##\s*IDEA-21\s*EXTRACTED\s*TOKENS\s*\([^)]*\)\s*\n(\[[^\n]*\])",
    re.IGNORECASE,
)


def latest_run(d: Path) -> Path | None:
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
    return rcs[-1] if rcs else None


def parse_tokens(rc: str) -> list[str]:
    m = EXTRACTED_HEADER_RE.search(rc or "")
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except Exception:
        return []


def collect(label_prefix: str,
            exclude_substrings: tuple[str, ...] = ()) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in sorted(ROOT.glob(f"results_compare_runs_{label_prefix}_*")):
        if any(sub in d.name for sub in exclude_substrings):
            continue
        rd = latest_run(d)
        if not rd:
            continue
        run_path = rd / "raw_runs" / "run_001.json"
        if not run_path.exists():
            continue
        slug = d.name.replace(f"results_compare_runs_{label_prefix}_", "").rsplit("_n", 1)[0]
        out[slug] = json.load(open(run_path))
    return out


def req_line(prefix: str, r: dict) -> list[str]:
    text = (r.get("title", "") or "") + " " + (r.get("description", "") or "")
    cited = "✓" if PROBE_RE.search(text) else " "
    return [
        f"    {prefix} [{cited}] [{r.get('asvs_category', '?')}] {r.get('title', '?')}",
        f"           {(r.get('description', '') or '')[:300]}",
    ]


def main():
    stories = json.load(open(ROOT / "user_stories.json"))["user_stories"]
    gt_blob = json.load(open(ROOT / "ground_truth.json"))
    gt_by_id = {s["story_id"]: s["requirements"] for s in gt_blob["ground_truth"]}

    adv = collect("adversarial")
    idea21 = {
        **collect("idea21_smoke"),
        **collect("idea21", exclude_substrings=("idea21_smoke",)),
    }

    all_slugs = sorted(set(adv) | set(idea21))
    if not all_slugs:
        print("no cells found")
        return

    lines: list[str] = []

    def w(*a):
        lines.append(" ".join(str(x) for x in a))

    w("# Idea 21 — qualitative dump (BL vs RG-15 vs RG-21)")
    w("")
    w("Each story shows ground truth, baseline, idea-15 RAG (from existing adversarial probe), and idea-21 RAG (extract-then-cite).")
    w("Citation marker (✓) means the requirement text contains a Mastodon-specific probe term.")
    w("")

    # Header table: probe-term citation count per cell
    w("## Probe-term citation count (across 5 stories, run_001)")
    w("")
    w(f"{'model':<38} {'BL':<10} {'RG-15':<10} {'RG-21':<10}")
    w("-" * 80)
    for slug in all_slugs:
        bl_hits = bl_total = 0
        rg15_hits = rg15_total = 0
        rg21_hits = rg21_total = 0

        if slug in adv:
            for s in adv[slug]["per_story_results"]:
                for r in (s.get("baseline_result") or {}).get("requirements", []) or []:
                    bl_total += 1
                    if PROBE_RE.search((r.get("title", "") or "") + " " + (r.get("description", "") or "")):
                        bl_hits += 1
                for r in (s.get("rag_result") or {}).get("requirements", []) or []:
                    rg15_total += 1
                    if PROBE_RE.search((r.get("title", "") or "") + " " + (r.get("description", "") or "")):
                        rg15_hits += 1

        if slug in idea21:
            for s in idea21[slug]["per_story_results"]:
                # idea-21 baseline is captured in idea21 too, but adv has the
                # canonical baseline numbers. Prefer adv when both exist.
                if slug not in adv:
                    for r in (s.get("baseline_result") or {}).get("requirements", []) or []:
                        bl_total += 1
                        if PROBE_RE.search((r.get("title", "") or "") + " " + (r.get("description", "") or "")):
                            bl_hits += 1
                for r in (s.get("rag_result") or {}).get("requirements", []) or []:
                    rg21_total += 1
                    if PROBE_RE.search((r.get("title", "") or "") + " " + (r.get("description", "") or "")):
                        rg21_hits += 1

        w(f"{slug[:37]:<38} {bl_hits}/{bl_total:<8} {rg15_hits}/{rg15_total:<8} {rg21_hits}/{rg21_total:<8}")

    w("")
    w("## Per-story content")
    w("")

    for story in stories:
        sid = story["id"]
        w("=" * 100)
        w(f"### {sid}: {story['title']}")
        w("=" * 100)
        w(f"story: {story['story']}")
        w("acceptance criteria:")
        for ac in story["acceptance_criteria"]:
            w(f"  - {ac}")
        w("")

        gt = gt_by_id.get(sid, [])
        w(f"#### Ground truth ({len(gt)} reqs)")
        for r in gt:
            cited = "✓" if PROBE_RE.search(r["title"] + " " + r["description"]) else " "
            w(f"  GT [{cited}] [{r['asvs_category']}] {r['title']}")
            w(f"       {r['description'][:300]}")
        w("")

        for slug in all_slugs:
            w(f"#### {slug}")

            # Stage A token list (idea-21 only)
            if slug in idea21:
                s21 = next((x for x in idea21[slug]["per_story_results"] if x["story_id"] == sid), None)
                if s21:
                    tokens = parse_tokens((s21.get("rag_result") or {}).get("retrieval_context", ""))
                    w(f"  Stage-A extracted tokens ({len(tokens)}): {tokens}")

            # Baseline (prefer adv data; fall back to idea21 data)
            s_for_baseline = None
            if slug in adv:
                s_for_baseline = next((x for x in adv[slug]["per_story_results"] if x["story_id"] == sid), None)
            elif slug in idea21:
                s_for_baseline = next((x for x in idea21[slug]["per_story_results"] if x["story_id"] == sid), None)
            if s_for_baseline:
                bl = (s_for_baseline.get("baseline_result") or {}).get("requirements", []) or []
                w(f"  baseline ({len(bl)} reqs):")
                for r in bl:
                    for line in req_line("BL", r):
                        w(line)

            # idea-15 RAG (adv data)
            if slug in adv:
                s15 = next((x for x in adv[slug]["per_story_results"] if x["story_id"] == sid), None)
                if s15:
                    rg = (s15.get("rag_result") or {}).get("requirements", []) or []
                    w(f"  RAG (idea-15) ({len(rg)} reqs):")
                    for r in rg:
                        for line in req_line("R15", r):
                            w(line)

            # idea-21 RAG
            if slug in idea21:
                s21 = next((x for x in idea21[slug]["per_story_results"] if x["story_id"] == sid), None)
                if s21:
                    rg21 = (s21.get("rag_result") or {}).get("requirements", []) or []
                    w(f"  RAG (idea-21) ({len(rg21)} reqs):")
                    for r in rg21:
                        for line in req_line("R21", r):
                            w(line)
            w("")
        w("")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
