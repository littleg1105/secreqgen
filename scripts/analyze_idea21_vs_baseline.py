"""Compare idea-21 grounded-citation generation against the original
adversarial-probe RAG (idea-15 + idea-19) and the baseline.

For each (model, story), this dumps:
  - n_reqs per arm
  - distinct docs-only Mastodon-specific tokens cited
  - For idea-21: tokens extracted (Stage A) and the utilization
    ratio (cited / extracted)

Headline output: a side-by-side table comparing BL / RG-15 / RG-21
on the same probe.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(
    "/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/mastodon_adversarial"
)

# Same DOC_TERMS list as analyze_adversarial_probe.py — Mastodon-specific
# tokens that the docs use and that should appear in grounded requirements.
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
    """Case-sensitive substring search across terms."""
    return {t for t in terms if t in text}


def latest_run(d: Path) -> Path | None:
    rcs = sorted([x for x in d.iterdir() if x.name.startswith("run_comparison_")])
    return rcs[-1] if rcs else None


# Regex to recover Stage-A extracted token list from idea-21 retrieval_context.
# The header line we emit is:
#   ## IDEA-21 EXTRACTED TOKENS (N items, stage_a_ms=...)
#   <json list>
EXTRACTED_HEADER_RE = re.compile(
    r"##\s*IDEA-21\s*EXTRACTED\s*TOKENS\s*\([^)]*\)\s*\n(\[[^\n]*\])",
    re.IGNORECASE,
)


def parse_extracted_tokens(retrieval_context: str) -> list[str]:
    """Recover the Stage A token list dumped at the top of retrieval_context."""
    if not retrieval_context:
        return []
    m = EXTRACTED_HEADER_RE.search(retrieval_context)
    if not m:
        return []
    try:
        arr = json.loads(m.group(1))
        return [t for t in arr if isinstance(t, str)]
    except Exception:
        return []


def load_stories_and_in_prompt_terms():
    stories = json.load(open(ROOT / "user_stories.json"))["user_stories"]
    story_in_prompt: dict[str, set[str]] = {}
    for s in stories:
        text = s["title"] + " " + s["story"] + " " + " ".join(s.get("acceptance_criteria", []))
        story_in_prompt[s["id"]] = find_terms(text, DOC_TERMS)
    return stories, story_in_prompt


def collect_cells(label_prefix: str,
                  exclude_substrings: tuple[str, ...] = ()) -> list[tuple[str, dict, Path]]:
    """Find one run_001.json per (label_prefix, model) cell."""
    out: list[tuple[str, dict, Path]] = []
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
        out.append((slug, json.load(open(run_path)), rd))
    return out


def summarize_arm(per_story: list[dict], result_key: str,
                  story_in_prompt: dict[str, set[str]],
                  is_idea21: bool = False) -> dict:
    """Aggregate citation metrics across all stories for one arm of one cell."""
    n_reqs = 0
    in_prompt_hits = 0
    docs_only_set: set[str] = set()
    per_story_breakdown: dict[str, dict] = {}

    for s in per_story:
        sid = s["story_id"]
        in_prompt = story_in_prompt.get(sid, set())
        out_of_prompt_pool = set(DOC_TERMS) - in_prompt
        result = s.get(result_key) or {}
        reqs = result.get("requirements", []) or []

        story_n = len(reqs)
        story_docs_only: set[str] = set()
        story_in_prompt_hits = 0
        for r in reqs:
            text = (r.get("title", "") or "") + " " + (r.get("description", "") or "")
            text += " " + " ".join(r.get("acceptance_criteria", []) or [])
            text += " " + (r.get("implementation_notes", "") or "")
            found = find_terms(text, DOC_TERMS)
            if found & in_prompt:
                story_in_prompt_hits += 1
            story_docs_only.update(found & out_of_prompt_pool)
        n_reqs += story_n
        in_prompt_hits += story_in_prompt_hits
        docs_only_set.update(story_docs_only)

        breakdown = {
            "n_reqs": story_n,
            "docs_only_cited": sorted(story_docs_only),
            "in_prompt_hits": story_in_prompt_hits,
        }
        if is_idea21:
            tokens = parse_extracted_tokens(result.get("retrieval_context") or "")
            # Count Stage-A tokens actually cited by any generated req.
            joined = " ".join(
                (r.get("title", "") or "") + " " +
                (r.get("description", "") or "") + " " +
                " ".join(r.get("acceptance_criteria", []) or []) + " " +
                (r.get("implementation_notes", "") or "")
                for r in reqs
            )
            cited_tokens = [t for t in tokens if t in joined]
            breakdown["extracted_tokens"] = tokens
            breakdown["extracted_n"] = len(tokens)
            breakdown["cited_extracted_n"] = len(cited_tokens)
            breakdown["utilization"] = (
                round(len(cited_tokens) / len(tokens), 3) if tokens else None
            )
        per_story_breakdown[sid] = breakdown

    return {
        "n_reqs": n_reqs,
        "in_prompt_hits": in_prompt_hits,
        "docs_only_set": docs_only_set,
        "per_story": per_story_breakdown,
    }


def main():
    stories, story_in_prompt = load_stories_and_in_prompt_terms()
    print("Stories and which DOC_TERMS already appear in their prompts:")
    for s in stories:
        sid = s["id"]
        print(f"  {sid}: {sorted(story_in_prompt[sid])}")
    print()

    # Old adversarial probe data (idea-15 + idea-19 RAG)
    adv_cells = collect_cells("adversarial")
    # Smoke + main idea-21 data
    idea21_cells = (
        collect_cells("idea21_smoke")
        + collect_cells("idea21", exclude_substrings=("idea21_smoke",))
    )

    # Index by model slug so we can pair them.
    adv_by_slug = {slug: r1 for slug, r1, _ in adv_cells}
    idea21_by_slug = {slug: r1 for slug, r1, _ in idea21_cells}

    all_slugs = sorted(set(adv_by_slug) | set(idea21_by_slug))
    if not all_slugs:
        print("No cells found.")
        return

    # ────────────────────────────────────────────────────────────────
    # Headline table
    # ────────────────────────────────────────────────────────────────
    print("Per-arm summary (across 5 stories, run_001):")
    print(f"  {'model':<38} {'arm':<8} {'n_reqs':<8} {'docs_only_terms_cited':<30} {'count':<6}")
    print("-" * 100)
    for slug in all_slugs:
        if slug in adv_by_slug:
            r1 = adv_by_slug[slug]
            ps = r1["per_story_results"]
            bl = summarize_arm(ps, "baseline_result", story_in_prompt)
            rg15 = summarize_arm(ps, "rag_result", story_in_prompt)
            print(f"  {slug[:37]:<38} {'BL':<8} {bl['n_reqs']:<8} "
                  f"{','.join(sorted(bl['docs_only_set']))[:29]:<30} {len(bl['docs_only_set']):<6}")
            print(f"  {slug[:37]:<38} {'RG-15':<8} {rg15['n_reqs']:<8} "
                  f"{','.join(sorted(rg15['docs_only_set']))[:29]:<30} {len(rg15['docs_only_set']):<6}")
        if slug in idea21_by_slug:
            r1 = idea21_by_slug[slug]
            ps = r1["per_story_results"]
            rg21 = summarize_arm(ps, "rag_result", story_in_prompt, is_idea21=True)
            print(f"  {slug[:37]:<38} {'RG-21':<8} {rg21['n_reqs']:<8} "
                  f"{','.join(sorted(rg21['docs_only_set']))[:29]:<30} {len(rg21['docs_only_set']):<6}")
        print()

    # ────────────────────────────────────────────────────────────────
    # Per-cell, per-story idea-21 utilization
    # ────────────────────────────────────────────────────────────────
    print()
    print("Idea-21 Stage-A extraction + utilization (per story):")
    print(f"  {'model':<38} {'story':<14} {'extracted':<10} {'cited':<8} {'utilization':<12}")
    print("-" * 90)
    for slug in all_slugs:
        if slug not in idea21_by_slug:
            continue
        r1 = idea21_by_slug[slug]
        for s in r1["per_story_results"]:
            sid = s["story_id"]
            rg21_summary = summarize_arm(
                [s], "rag_result", story_in_prompt, is_idea21=True,
            )
            br = rg21_summary["per_story"][sid]
            util = (f"{br['utilization']:.1%}" if br.get("utilization") is not None
                    else "n/a")
            print(f"  {slug[:37]:<38} {sid:<14} {br['extracted_n']:<10} "
                  f"{br['cited_extracted_n']:<8} {util:<12}")
        print()

    # ────────────────────────────────────────────────────────────────
    # Headline comparison: docs-only term coverage RG-15 vs RG-21
    # ────────────────────────────────────────────────────────────────
    print()
    print("Docs-only term coverage delta (RG-21 - RG-15) per cell:")
    print(f"  {'model':<38} {'BL':<8} {'RG-15':<8} {'RG-21':<8} {'gain':<10}")
    print("-" * 80)
    for slug in all_slugs:
        bl_n = rg15_n = rg21_n = 0
        bl_terms: set[str] = set(); rg15_terms: set[str] = set(); rg21_terms: set[str] = set()
        if slug in adv_by_slug:
            ps = adv_by_slug[slug]["per_story_results"]
            bl = summarize_arm(ps, "baseline_result", story_in_prompt)
            rg15 = summarize_arm(ps, "rag_result", story_in_prompt)
            bl_n = len(bl["docs_only_set"]); rg15_n = len(rg15["docs_only_set"])
            bl_terms = bl["docs_only_set"]; rg15_terms = rg15["docs_only_set"]
        if slug in idea21_by_slug:
            ps = idea21_by_slug[slug]["per_story_results"]
            rg21 = summarize_arm(ps, "rag_result", story_in_prompt, is_idea21=True)
            rg21_n = len(rg21["docs_only_set"])
            rg21_terms = rg21["docs_only_set"]
        gain = rg21_n - rg15_n
        print(f"  {slug[:37]:<38} {bl_n:<8} {rg15_n:<8} {rg21_n:<8} {gain:+}")
        new_terms = rg21_terms - rg15_terms
        lost_terms = rg15_terms - rg21_terms
        if new_terms:
            print(f"  {'':<38} new in RG-21: {sorted(new_terms)}")
        if lost_terms:
            print(f"  {'':<38} dropped in RG-21: {sorted(lost_terms)}")
        print()


if __name__ == "__main__":
    main()
