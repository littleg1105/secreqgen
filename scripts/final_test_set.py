"""Final test: fix which stories are in the run, from the key check of the two outside AI raters.

Rule (thesis/analysis_lock.md, final test): a story with more than 20 of 100 disputed identifiers is removed.
The standard set of a product is its first 5 standard stories in pick order that were not removed (4 at least).
The barebones texts are those of the same stories. The script reads sealed/key_check/summary.json, prints counts
and codes only, and writes real_test_cases/final_test/run_stories.json one time (it never replaces the file).

Run from coded_project/secreqgen:
  PYTHONPATH=src:scripts .venv/bin/python scripts/final_test_set.py
"""

from __future__ import annotations

import hashlib
import json
import sys

from tuning_lib.final_test import RUN_SET_FILE, SEALED, kinds, run_story_ids
from tuning_lib.guard import guarded_write_text
from tuning_lib.paths import PROJECTS

MIN_STANDARD = 4


def main() -> int:
    summary_path = SEALED / "key_check" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    kind_of = kinds()
    no_answer = [(sid, rater) for sid, row in summary.items() for rater, r in row["raters"].items() if r["status"] != "answer"]
    if no_answer:
        print(f"[FAIL] {len(no_answer)} rater call(s) gave no answer: {no_answer}. The set is not fixed.")
        return 1
    removed = sorted(sid for sid, row in summary.items() if row["removed"])
    n_ids = sum(row["n_identifiers"] for row in summary.values())
    n_disputed = sum(len(row["disputed_identifiers"]) for row in summary.values())
    n_facts = sum(row["n_facts"] for row in summary.values())
    n_facts_disputed = sum(len(row["disputed_facts"]) for row in summary.values())
    per_rater = {
        rater: sum(row["raters"][rater]["identifiers_not_ok"] for row in summary.values()) for rater in ("gemini", "gpt")
    }
    print(f"stories checked: {len(summary)}; identifiers {n_ids}, disputed by one rater or both {n_disputed} "
          f"(Gemini {per_rater['gemini']}, GPT {per_rater['gpt']}); facts {n_facts}, disputed {n_facts_disputed}")
    print(f"stories removed: {len(removed)} {[(sid, kind_of[sid]) for sid in removed]}")
    stories, ok = {}, True
    for project in PROJECTS:
        per = run_story_ids(project, removed)
        stories[project] = per
        print(f"{project:10s} " + ", ".join(f"{kind} {len(ids)}" for kind, ids in per.items()) + f" | standard: {per['standard']}")
        if len(per["standard"]) < MIN_STANDARD:
            ok = False
    if not ok:
        print("[FAIL] a product has fewer than 4 standard stories: the final test did not run.")
        return 1
    data = {
        "rule": "first 5 standard stories of each product in pick order that passed the key check; more than 20 of 100 "
                "disputed identifiers removes a story",
        "key_check_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "removed": removed,
        "stories": stories,
    }
    text = json.dumps(data, indent=1) + "\n"
    if RUN_SET_FILE.is_file():
        same = RUN_SET_FILE.read_text(encoding="utf-8") == text
        print("run_stories.json exists " + ("with the same content" if same else "WITH DIFFERENT CONTENT; it is not replaced"))
        return 0 if same else 1
    guarded_write_text(RUN_SET_FILE, text)
    total = {kind: sum(len(stories[p][kind]) for p in PROJECTS) for kind in ("standard", "barebones", "trivial", "new_feature")}
    print(f"written run_stories.json: {total} = {sum(total.values())} story texts; sha256 {hashlib.sha256(text.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
