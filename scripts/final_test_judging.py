"""Final test, measure 2 (labels of the judges): the blind batches, and the summary of the final labels.

batches   For each product: (a) one evidence file for each story text of the run (the story, its criteria and
          the system description; no answer key) in tuning/_judging/evidence_x/; (b) two judging rounds in the
          run folder ft_a_<project>: "std" (the standard stories, 6 requirements for each story, arm and model)
          and "oth" (the other kinds, 3 for each). It prints counts only and writes the job list of the judges.
labels    Reads the final label of each drawn requirement (final_test/judging/judgments_<run>_<round>.json,
          seat "final") and the private keys of the rounds. For each kind of story and each arm it gives the
          shares of S, G and U, and the story differences NEW minus OLD of the score (S share minus U share).

The judges never get an answer key: an evidence file holds none, and the folder final_test/ is closed for them.
Run from coded_project/secreqgen with PYTHONPATH=src:scripts.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import sys
from pathlib import Path

from tuning_lib import judging as J
from tuning_lib import xproject as X
from tuning_lib.final_test import KINDS, kinds, load_final_stories, read_run_set
from tuning_lib.guard import guarded_mkdir, guarded_write_text
from tuning_lib.paths import FINAL_TEST_ROOT, MODELS, PROJECTS, RTC, TUNING_ROOT, model_slug
from tuning_lib.records import read_jsonl

VERSIONS = ("v0_old", "s1_word_3000")
ARM = {"v0_old": "OLD", "s1_word_3000": "NEW"}
ROUNDS = {"std": (("standard",), 6), "oth": (("barebones", "trivial", "new_feature"), 3)}
SEED = 20261117
JUDGING = FINAL_TEST_ROOT / "judging"
REPO_HINT = {
    "keycloak": "The documentation source of the product is in its folder docs. The server code is in the folders quarkus, services, "
                "server-spi, server-spi-private, core, model, federation and authz. Search these first, and the full repository only "
                "if the name is not there. Do not search the folders testsuite and tests.",
    "bitwarden": "The server code is in the folders src and bitwarden_license. Search these first, and the full repository only if the "
                 "name is not there. Do not search the folder test. In the documentation folder, search the Markdown files only.",
    "mastodon": "The code is in the folders app, lib, config and db. Search these first, and the full repository only if the name is "
                "not there. Do not search the folders spec, vendor and node_modules.",
}


def run_name(project: str) -> str:
    return f"ft_a_{project}"


def cmd_batches(args: argparse.Namespace) -> int:
    run_set = read_run_set()
    stories = load_final_stories()
    groups = []
    for project in ([args.project] if args.project else PROJECTS):
        item = X.check_product_folders(project)
        context = (RTC / project / "system_context.yaml").read_text(encoding="utf-8")
        guarded_mkdir(X.EVIDENCE_X_ROOT)
        ids = [sid for kind in KINDS for sid in run_set[project][kind]]
        for sid in ids:
            guarded_write_text(X.EVIDENCE_X_ROOT / f"{sid}.md", X.evidence_text(stories[sid], item, context))
        n_md = sum(1 for _ in item.docs_dir.glob("**/*.md"))
        for round_name, (round_kinds, per_cell) in ROUNDS.items():
            sids = [sid for kind in round_kinds for sid in run_set[project][kind]]
            d = J.build_batches(TUNING_ROOT / run_name(project), versions=list(VERSIONS), stories=sids, per_cell=per_cell,
                                seed=SEED, round_name=round_name, allow_short=True)
            print(f"{project:10s} round {round_name}: {len(sids)} story texts, {d.n_items} items, {len(d.deviations)} cells with fewer "
                  f"requirements than {per_cell}")
            groups.append({
                "run": run_name(project), "round": round_name, "project": project,
                "docs_dir": item.docs_dir.relative_to(RTC).as_posix(), "repo_name": item.repo_dir.name,
                "n_doc_files": n_md, "repo_hint": REPO_HINT[project],
                "stories": [{"story_id": s, "n_items": n} for s, n in d.n_items_by_story.items()],
            })
        print(f"{project:10s} evidence files written: {len(ids)}")
    total = sum(s["n_items"] for g in groups for s in g["stories"])
    print(f"items in all rounds: {total}")
    Path(args.args_out).write_text(json.dumps({"groups": groups}, indent=1), encoding="utf-8")
    print(f"job list of the judges written: {args.args_out}")
    return 0


def _se(values: list[float]) -> float | None:
    return statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else None


def cmd_labels(args: argparse.Namespace) -> int:
    kind_of = kinds()
    cells: dict[tuple[str, str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    u_kinds: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    n_req: dict[tuple[str, str, str], list[int]] = collections.defaultdict(list)  # (kind, version, model) -> requirements of an answer
    for project in PROJECTS:
        run_dir = TUNING_ROOT / run_name(project)
        for model in MODELS:
            for rec in read_jsonl(run_dir / project / model_slug(model) / "records.jsonl"):
                if rec.get("error") is None:
                    n_req[(kind_of[rec["story_id"]], rec["config_id"], model)].append(len(rec.get("requirements") or []))
        for round_name in ROUNDS:
            key = json.loads((run_dir / "judging" / round_name / "private" / "key.json").read_text(encoding="utf-8"))
            judgments = json.loads((JUDGING / f"judgments_{run_name(project)}_{round_name}.json").read_text(encoding="utf-8"))
            rows = [r for seats in judgments.values() for r in seats["final"]]
            if {r["qid"] for r in rows} != set(key):
                print(f"[FAIL] {project} {round_name}: the labels do not cover the drawn items")
                return 1
            for r in rows:
                k = key[r["qid"]]
                cells[(k["story_id"], k["version"], k["model"])][r["label"]] += 1
                if r["label"] == "U":
                    u_kinds[(kind_of[k["story_id"]], k["version"])][r.get("kind") or "none"] += 1

    def score(story: str, version: str) -> float:
        vals = [(c["S"] - c["U"]) / sum(c.values()) for (s, v, _m), c in cells.items() if s == story and v == version]
        return statistics.fmean(vals)

    out: dict = {"kinds": {}}
    print("Kind          stories  arm   items   S     G     U   (of 100) | for each answer: requirements, S, G, U")
    for kind in KINDS:
        stories = sorted({s for (s, _v, _m) in cells if kind_of[s] == kind})
        if not stories:
            continue
        entry: dict = {"n_stories": len(stories), "arms": {}}
        for version in VERSIONS:
            total = collections.Counter()
            for (s, v, _m), c in cells.items():
                if v == version and kind_of[s] == kind:
                    total.update(c)
            n = sum(total.values())
            per_answer = statistics.fmean(statistics.fmean(n_req[(kind, version, m)]) for m in MODELS)
            yields = collections.Counter()
            for m in MODELS:
                cm = collections.Counter()
                for (s, v, mm), c in cells.items():
                    if v == version and mm == m and kind_of[s] == kind:
                        cm.update(c)
                for label in "SGU":
                    yields[label] += statistics.fmean(n_req[(kind, version, m)]) * cm[label] / sum(cm.values()) / len(MODELS)
            entry["arms"][ARM[version]] = {
                "n_items": n, **{label: total[label] / n for label in "SGU"}, "requirements_per_answer": per_answer,
                **{f"{label}_per_answer": yields[label] for label in "SGU"}, "u_kinds": dict(u_kinds[(kind, version)]),
            }
            print(f"{kind:12s}  {len(stories):3d}     {ARM[version]:4s}  {n:4d}  {100 * total['S'] / n:5.1f} {100 * total['G'] / n:5.1f} {100 * total['U'] / n:5.1f}"
                  f"            | {per_answer:.2f}, {yields['S']:.2f}, {yields['G']:.2f}, {yields['U']:.2f}")
        diffs = [score(s, "s1_word_3000") - score(s, "v0_old") for s in stories]
        se = _se(diffs)
        up, down = sum(d > 1e-9 for d in diffs), sum(d < -1e-9 for d in diffs)
        entry["score_new_minus_old"] = {"mean": statistics.fmean(diffs), "se": se, "up": up, "equal": len(diffs) - up - down, "down": down}
        u_old, u_new = entry["arms"]["OLD"]["U"], entry["arms"]["NEW"]["U"]
        entry["u_share_new_minus_old_points"] = 100 * (u_new - u_old)
        print(f"{kind:12s}  score (S share minus U share), NEW minus OLD: mean {100 * statistics.fmean(diffs):+.1f} points"
              f" (SE {'n/a' if se is None else f'{100 * se:.1f}'}); stories up {up}, equal {len(diffs) - up - down}, down {down}"
              f" | U share NEW minus OLD: {100 * (u_new - u_old):+.1f} points")
        print(f"{kind:12s}  kinds of U: OLD {dict(u_kinds[(kind, 'v0_old')])} | NEW {dict(u_kinds[(kind, 's1_word_3000')])}")
        out["kinds"][kind] = entry
    std = out["kinds"]["standard"]
    out["guard_u_share"] = {"new_minus_old_points": std["u_share_new_minus_old_points"], "limit_points": 3.0,
                            "new_not_higher_by_more_than_3": std["u_share_new_minus_old_points"] <= 3.0}
    print(f"\nRule of the lock (standard stories): U share of NEW minus U share of OLD = {std['u_share_new_minus_old_points']:+.1f} points; "
          f"limit +3.0; {'inside the limit' if out['guard_u_share']['new_not_higher_by_more_than_3'] else 'ABOVE THE LIMIT'}")
    guarded_write_text(FINAL_TEST_ROOT / "measure2.json", json.dumps(out, indent=1) + "\n")
    print("written: final_test/measure2.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("batches")
    b.add_argument("--args-out", required=True, help="file for the job list of the judging workflow (scratch folder)")
    b.add_argument("--project", choices=PROJECTS, default=None, help="one product only (default: each product)")
    b.set_defaults(fn=cmd_batches)
    sub.add_parser("labels").set_defaults(fn=cmd_labels)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
