"""Per-story dump of adversarial probe outputs for qualitative reading."""
import json
import re
from pathlib import Path

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/mastodon_adversarial")
OUT = Path("/tmp/adversarial_probe_qual.md")

# Probe terms — config keys / endpoint names the docs use. If a req cites
# these, the generator is grounded in real Mastodon-specific knowledge.
PROBE_TERMS = [
    r"CAS_UID_KEY", r"CAS_SECURITY_ASSUME_EMAIL_IS_VERIFIED", r"CAS_CA_PATH",
    r"CAS_DISABLE_SSL_VERIFICATION", r"CAS_DISPLAYNAME_KEY",
    r"LIMITED_FEDERATION_MODE",
    r"S3_HOSTNAME", r"S3_ALIAS_HOST", r"S3_BUCKET",
    r"ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH",
    r"followers_synchronization", r"followers_synchronisation",
    r"HTTP Signature[s]?", r"signature\.keyId", r"keyId",
    r"@uid@",
]
PROBE_RE = re.compile("|".join(PROBE_TERMS), re.IGNORECASE)


def latest_run(d: Path) -> Path | None:
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
    return rcs[-1] if rcs else None


def main():
    lines = []
    def w(*a): lines.append(" ".join(str(x) for x in a))

    stories = json.load(open(ROOT / "user_stories.json"))["user_stories"]
    gt_blob = json.load(open(ROOT / "ground_truth.json"))
    gt_by_id = {s["story_id"]: s["requirements"] for s in gt_blob["ground_truth"]}

    cells = []
    for d in sorted(ROOT.glob("results_compare_runs_adversarial_*")):
        rd = latest_run(d)
        if not rd: continue
        slug = d.name.replace("results_compare_runs_adversarial_", "").rsplit("_n", 1)[0]
        r1 = json.load(open(rd / "raw_runs" / "run_001.json"))
        cells.append((slug, r1, rd))

    w("# Adversarial probe — qualitative dump")
    w("")
    w(f"Stories probe Mastodon-specific knowledge (CAS_*, LIMITED_FEDERATION_MODE, S3_HOSTNAME/S3_ALIAS_HOST, ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH, followers_synchronization).")
    w(f"For each story × model, this dumps GT, baseline reqs, RAG reqs, and counts how many reqs cite project-specific terminology.")
    w("")

    # Overall probe-term count
    w("## Probe-term citation count (across 5 stories, run_001)")
    w("")
    w(f"{'model':<35} {'baseline cites':<16} {'rag cites':<12}")
    w("-" * 70)
    for slug, r1, _ in cells:
        bl_hits = 0; rg_hits = 0; bl_total = 0; rg_total = 0
        for s in r1["per_story_results"]:
            for r in (s.get("baseline_result") or {}).get("requirements", []) or []:
                bl_total += 1
                txt = (r.get("title","") or "") + " " + (r.get("description","") or "")
                if PROBE_RE.search(txt):
                    bl_hits += 1
            for r in (s.get("rag_result") or {}).get("requirements", []) or []:
                rg_total += 1
                txt = (r.get("title","") or "") + " " + (r.get("description","") or "")
                if PROBE_RE.search(txt):
                    rg_hits += 1
        w(f"{slug:<35} {bl_hits}/{bl_total:<14} {rg_hits}/{rg_total}")

    w("")
    w("## Per-story content (GT vs baseline vs RAG, run_001)")
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

        for slug, r1, _ in cells:
            s = next((x for x in r1["per_story_results"] if x["story_id"] == sid), None)
            if not s: continue
            w(f"#### {slug}")
            bl_reqs = (s.get("baseline_result") or {}).get("requirements", []) or []
            rg_reqs = (s.get("rag_result") or {}).get("requirements", []) or []
            w(f"  baseline ({len(bl_reqs)} reqs):")
            for r in bl_reqs:
                txt = (r.get("title","") or "") + " " + (r.get("description","") or "")
                cited = "✓" if PROBE_RE.search(txt) else " "
                w(f"    BL [{cited}] [{r.get('asvs_category','?')}] {r.get('title','?')}")
                w(f"           {(r.get('description','') or '')[:300]}")
            w(f"  RAG ({len(rg_reqs)} reqs):")
            for r in rg_reqs:
                txt = (r.get("title","") or "") + " " + (r.get("description","") or "")
                cited = "✓" if PROBE_RE.search(txt) else " "
                w(f"    RG [{cited}] [{r.get('asvs_category','?')}] {r.get('title','?')}")
                w(f"           {(r.get('description','') or '')[:300]}")
            w("")
        w("")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
