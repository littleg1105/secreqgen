"""Build the blinded human-review sheet for Study 2 (pre-registration §14).

Samples 60 generated requirements stratified by project × arm (both models and
all stories represented as evenly as the counts allow), hides arm, model and
run from the reviewer, and writes:

  real_test_cases/study2/review/review_sheet.md   what the reviewers see
  real_test_cases/study2/review/review_key.json   item -> provenance (keep closed)
  real_test_cases/study2/review/labels_<name>.csv  one empty answer file per reviewer

Each item shows the story, its fact sheet (documented identifiers + facts) and
the documentation the model was given (frozen passages for the retrieval arms,
a pointer to the corpus file for the whole-corpus arm, nothing for the
no-documents arm), then the requirement. Reviewers assign ONE label:

  S  supported & correct project-specific constraint — relies on a documented
     fact/identifier and states it correctly
  G  generic but correct — sound security requirement, not project-specific
  U  unsupported or wrong — misstates the docs, invents a setting/identifier,
     or is not a real requirement

Usage: PYTHONPATH=src .venv/bin/python scripts/build_study2_review_sheet.py [--n 60] [--seed 20261102] [--reviewers george,colleague]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

STUDY2 = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases/study2")
PROJECTS = ["mastodon", "keycloak", "bitwarden"]
LABELS = "S = supported & correct project-specific · G = generic but correct · U = unsupported or wrong"


def load_records() -> list[dict]:
    recs = []
    for project in PROJECTS:
        stories = {s["id"]: s for s in json.loads((STUDY2 / project / "stories.json").read_text())["stories"]}
        for rdir in sorted((STUDY2 / project / "results").glob("*/")):
            frozen_p = rdir / "retrieval_frozen.json"
            frozen = json.loads(frozen_p.read_text()) if frozen_p.exists() else {}
            for line in (rdir / "records.jsonl").read_text().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("error"):
                    continue
                for i, req in enumerate(r["requirements"]):
                    recs.append({
                        "project": project, "model": r["model"], "arm": r["arm"], "run": r["run"],
                        "story_id": r["story_id"], "story": stories[r["story_id"]], "req_index": i,
                        "requirement": req,
                        "doc_context": (frozen.get(r["story_id"], {}).get("context", "") if r["arm"] in "BC" else None),
                        "extracted_tokens": r.get("extracted_tokens") if r["arm"] == "C" else None,
                        "corpus_file": str(rdir / "corpus_D.txt") if r["arm"] == "D" else None,
                    })
    return recs


def stratified_sample(recs: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    cells = defaultdict(list)
    for r in recs:
        cells[(r["project"], r["arm"])].append(r)
    keys = sorted(cells)
    base, extra = divmod(n, len(keys))
    chosen = []
    for k_i, key in enumerate(keys):
        quota = base + (1 if k_i < extra else 0)
        pool = cells[key]
        # even over model, then story, then random
        by_model = defaultdict(list)
        for r in pool:
            by_model[r["model"]].append(r)
        picked = []
        models = sorted(by_model)
        while len(picked) < quota and any(by_model.values()):
            for m in models:
                if len(picked) >= quota or not by_model[m]:
                    continue
                by_story = defaultdict(list)
                for r in by_model[m]:
                    by_story[r["story_id"]].append(r)
                used = {p["story_id"] for p in picked if p["model"] == m}
                candidates = [s for s in sorted(by_story) if s not in used] or sorted(by_story)
                sid = rng.choice(candidates)
                r = rng.choice(by_story[sid])
                picked.append(r)
                by_model[m].remove(r)
        chosen.extend(picked)
    rng.shuffle(chosen)
    return chosen


def fmt_requirement(req: dict) -> str:
    ac = "\n".join(f"    - {a}" for a in req.get("acceptance_criteria") or [])
    return (f"**{req.get('title','')}**\n\n{req.get('description','')}\n\n"
            f"*Rationale:* {req.get('rationale','')}\n\n*Acceptance criteria:*\n{ac}\n\n"
            f"*Implementation notes:* {req.get('implementation_notes') or '—'}\n\n"
            f"*Sources cited:* {', '.join(req.get('source_references') or []) or '—'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--reviewers", default="george,colleague")
    args = ap.parse_args()

    out = STUDY2 / "review"
    out.mkdir(exist_ok=True)
    recs = load_records()
    sample = stratified_sample(recs, args.n, args.seed)

    key, lines = {}, []
    lines.append("# Study 2 — blinded requirement review\n")
    lines.append(f"{len(sample)} generated security requirements. For each item read the story, the fact sheet and the documentation the generator was given, then label the requirement:\n\n> {LABELS}\n")
    lines.append("Record labels in your own `labels_<name>.csv` (columns: item,label,note). Do not discuss items with the other reviewer until both files are complete. Which system produced each item is hidden.\n")
    for i, r in enumerate(sample, start=1):
        item = f"R{i:02d}"
        key[item] = {k: r[k] for k in ("project", "model", "arm", "run", "story_id", "req_index")}
        s = r["story"]; fs = s["fact_sheet"]
        lines.append(f"\n---\n\n## {item} · {r['project']} · story {s['id']}: {s['title']}\n")
        lines.append(f"**Story.** {s['story']}\n\n**Acceptance criteria.**\n" + "\n".join(f"- {a}" for a in s["acceptance_criteria"]) + "\n")
        lines.append("**Fact sheet — documented identifiers:** " + ", ".join(f"`{t}`" for t in fs["identifiers"]) + "\n")
        lines.append("**Fact sheet — documented facts:**\n" + "\n".join(f"- {f}" for f in fs["facts"]) + "\n")
        if r["doc_context"] is not None:
            ctx = r["doc_context"]
            if r["extracted_tokens"] is not None:
                ctx = "EXTRACTED TOKEN LIST GIVEN TO THE GENERATOR: " + ", ".join(r["extracted_tokens"]) + "\n\n" + ctx
            lines.append("<details><summary><b>Documentation given to the generator</b> (click to expand)</summary>\n\n```\n" + ctx + "\n```\n</details>\n")
        elif r["corpus_file"]:
            lines.append(f"**Documentation given to the generator:** the project's entire documentation set (`{Path(r['corpus_file']).name}` in the results folder).\n")
        else:
            lines.append("**Documentation given to the generator:** none.\n")
        lines.append("### Requirement to label\n\n" + fmt_requirement(r["requirement"]) + "\n\n**Label (S / G / U):** ______  **Note:** ______\n")

    (out / "review_sheet.md").write_text("\n".join(lines))
    (out / "review_key.json").write_text(json.dumps(key, indent=1))
    for name in [n.strip() for n in args.reviewers.split(",") if n.strip()]:
        p = out / f"labels_{name}.csv"
        if not p.exists():
            with p.open("w", newline="") as fh:
                w = csv.writer(fh); w.writerow(["item", "label", "note"])
                for item in key:
                    w.writerow([item, "", ""])
    cells = defaultdict(int)
    for v in key.values():
        cells[(v["project"], v["arm"], v["model"])] += 1
    print(f"{len(sample)} items → {out/'review_sheet.md'}; key kept in review_key.json")
    for k in sorted(cells):
        print(f"  {k[0]:10s} arm {k[1]} {k[2]:12s} {cells[k]}")


if __name__ == "__main__":
    main()
