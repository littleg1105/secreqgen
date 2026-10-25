"""Distinguish env-vars-from-the-prompt vs env-vars-only-from-docs.

The original probe stories listed some Mastodon env vars in the story text
(e.g. CAS_SECURITY_ASSUME_EMAIL_IS_VERIFIED). A baseline LLM can echo
those back without any genuine retrieval — that's just reading the
prompt. The interesting signal is whether the generator cites Mastodon
terms that DO appear in the docs but DON'T appear in the story prompt.
"""
import json
import re
from pathlib import Path
from collections import defaultdict

ROOT = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/mastodon_adversarial")
DOCS_DIR = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/mastodon/docs")

# All Mastodon-specific tokens we care about
DOC_TERMS = [
    "CAS_UID_KEY", "CAS_SECURITY_ASSUME_EMAIL_IS_VERIFIED", "CAS_CA_PATH",
    "CAS_DISABLE_SSL_VERIFICATION", "CAS_DISPLAYNAME_KEY", "CAS_EMAIL_KEY",
    "CAS_IMAGE_KEY", "CAS_SSL", "CAS_CALLBACK_URL",
    "LIMITED_FEDERATION_MODE",
    "S3_HOSTNAME", "S3_ALIAS_HOST", "S3_BUCKET", "S3_PROTOCOL", "S3_ENDPOINT",
    "ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH",
    "followers_synchronization", "followers_synchronisation",
    "@uid@",
]


def find_terms(text: str, terms: list[str]) -> set[str]:
    """Return set of terms found in text (case-sensitive substring)."""
    found = set()
    for t in terms:
        if t in text:
            found.add(t)
    return found


def main():
    # 1. For each probe story, find which DOC_TERMS appear in the story text
    stories = json.load(open(ROOT / "user_stories.json"))["user_stories"]
    story_terms = {}  # story_id -> set of terms IN THE PROMPT
    for s in stories:
        text = s["title"] + " " + s["story"] + " " + " ".join(s.get("acceptance_criteria", []))
        story_terms[s["id"]] = find_terms(text, DOC_TERMS)
        print(f"STORY {s['id']} prompt contains: {sorted(story_terms[s['id']])}")
    print()

    # 2. For each cell, count: cites-of-terms-IN-prompt vs cites-of-terms-NOT-in-prompt
    cells = []
    for d in sorted(ROOT.glob("results_compare_runs_adversarial_*")):
        rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
        if not rcs: continue
        slug = d.name.replace("results_compare_runs_adversarial_", "").rsplit("_n", 1)[0]
        # Use run_001 (representative)
        r1 = json.load(open(rcs[-1] / "raw_runs" / "run_001.json"))
        cells.append((slug, r1))

    print(f"{'model':<35} mode    terms-in-prompt-echoed   terms-from-docs-only")
    print("-" * 90)
    summary = defaultdict(list)
    for slug, r1 in cells:
        for mode in ("baseline", "rag"):
            in_prompt_hits = 0; out_of_prompt_hits = set()
            n_reqs = 0
            for s in r1["per_story_results"]:
                sid = s["story_id"]
                in_prompt = story_terms[sid]
                out_of_prompt_pool = set(DOC_TERMS) - in_prompt
                key = f"{mode}_result"
                reqs = (s.get(key) or {}).get("requirements", []) or []
                n_reqs += len(reqs)
                for r in reqs:
                    text = (r.get("title","") or "") + " " + (r.get("description","") or "")
                    found = find_terms(text, DOC_TERMS)
                    # Echoed from prompt
                    if found & in_prompt:
                        in_prompt_hits += 1
                    # Cited from docs (i.e., from project knowledge or retrieval)
                    out_only = found & out_of_prompt_pool
                    if out_only:
                        out_of_prompt_hits.update(out_only)
            label = "BL" if mode == "baseline" else "RG"
            summary[slug].append((label, n_reqs, in_prompt_hits, sorted(out_of_prompt_hits)))
            print(f"{slug[:34]:<35} {label:<6} {in_prompt_hits}/{n_reqs}{'':<22} {sorted(out_of_prompt_hits)}")
    print()
    print("Legend:")
    print("- terms-in-prompt-echoed: how many reqs cite a term that was already in the user story (low-effort echo)")
    print("- terms-from-docs-only: distinct Mastodon-specific terms cited that did NOT appear in any story prompt")
    print("  (these can only come from the model's training data or the retrieved docs)")
    print()

    # 3. The key contrast: out-of-prompt term *coverage*
    print("Out-of-prompt term coverage (across all 5 stories combined, run_001):")
    print(f"  model{'':<35} baseline-found{'':<10} rag-found")
    for slug in [c[0] for c in cells]:
        bl = next(t for t in summary[slug] if t[0] == "BL")
        rg = next(t for t in summary[slug] if t[0] == "RG")
        bl_terms = set(bl[3]); rg_terms = set(rg[3])
        gained = rg_terms - bl_terms
        lost = bl_terms - rg_terms
        print(f"  {slug[:38]:<40} BL={sorted(bl_terms)}")
        print(f"  {'':<40} RG={sorted(rg_terms)}")
        print(f"  {'':<40} RG gains vs BL: {sorted(gained)}")
        if lost: print(f"  {'':<40} RG drops vs BL: {sorted(lost)}")
        print()


if __name__ == "__main__":
    main()
