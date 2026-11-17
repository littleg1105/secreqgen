"""Final test, added round (planned after the result): judge labels for the run with no documentation.

Rules: thesis/analysis_lock.md, final test, step 6. For each standard story and each model the round mixes
6 requirements of the run with no documentation (run ft_b, version v3_pn_render_fact_hits, "NEW-0") and
3 requirements of the new tool (run ft_a, version s1_word_3000, "NEW") that no judge has seen (not in the
round "std"). The list of one story does not show which version wrote an item. The draw and the item text
use the functions of the lab (tuning_lib.judging).

batches  writes tuning/ft_b_<product>/judging/nd/batches/<story>.json and private/key.json; prints counts.
final    makes the final label of each item: the common label of the two Sonnet judges; for two different
         labels, the label that two of the three judges gave (the third is the Opus judge); a decision of
         the main session (file main_session_decisions.json) wins. Writes final_test/judging/.
labels   prints the shares of S, G and U for NEW-0 and for the new sample of NEW.

Run from coded_project/secreqgen with PYTHONPATH=src:scripts. Each command prints counts only.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
import sys
from pathlib import Path

from tuning_lib import judging as J
from tuning_lib import xproject as X
from tuning_lib.final_test import read_run_set
from tuning_lib.guard import guarded_mkdir, guarded_write_text
from tuning_lib.paths import FINAL_TEST_ROOT, MODELS, PROJECTS, RTC, TUNING_ROOT, model_slug
from tuning_lib.records import read_jsonl

ROUND = "nd"
SEED = 20261118
SOURCES = (  # (name in the report, run name pattern, version, requirements for each story and model)
    ("NEW-0", "ft_b_{project}", "v3_pn_render_fact_hits", 6),
    ("NEW", "ft_a_{project}", "s1_word_3000", 3),
)
ARM_OF = {version: name for name, _run, version, _n in SOURCES}
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


def round_dir(project: str) -> Path:
    return TUNING_ROOT / f"ft_b_{project}" / "judging" / ROUND


def cmd_batches(args: argparse.Namespace) -> int:
    run_set = read_run_set()
    groups = []
    for project in PROJECTS:
        rdir = round_dir(project)
        if rdir.exists():
            print(f"[FAIL] the round folder of {project} exists already; nothing is replaced")
            return 1
        stories = run_set[project]["standard"]
        seen_before = json.loads((TUNING_ROOT / f"ft_a_{project}" / "judging" / "std" / "private" / "key.json").read_text(encoding="utf-8"))
        excluded = {(e["version"], e["model"], e["story_id"], e["repeat"], e["requirement_index"]) for e in seen_before.values()}
        units: dict[str, list[tuple]] = {s: [] for s in stories}
        texts: dict[tuple, str] = {}
        counts = collections.Counter()
        for name, pattern, version, per_cell in SOURCES:
            run = pattern.format(project=project)
            kept, _hashes = J._load_records(TUNING_ROOT / run)
            for story in stories:
                for model in MODELS:
                    free: dict[int, list[int]] = {}
                    for (cfg, mdl, sid, _arm, repeat), rec in kept.items():
                        if (cfg, mdl, sid) != (version, model, story):
                            continue
                        idx = [i for i in range(len(rec.get("requirements") or [])) if (version, model, story, repeat, i) not in excluded]
                        free[repeat] = idx
                        for i in idx:
                            texts[(run, version, model, story, repeat, i)] = J.item_text(rec["requirements"][i])
                    if not free:
                        print(f"[FAIL] {run}: no stored answer for {story}, {model}, {version}; a records file can be empty or missing")
                        return 1
                    total = sum(len(v) for v in free.values())
                    rng = random.Random(f"{SEED}|{ROUND}|{story}|{version}|{model}")
                    picked = J._draw_cell(free, per_cell, rng) if total else []
                    counts[(name, "drawn")] += len(picked)
                    counts[(name, "short cells")] += 1 if len(picked) < per_cell else 0
                    for repeat, i in picked:
                        units[story].append((run, version, model, repeat, i))
        key: dict[str, dict] = {}
        guarded_mkdir(rdir / "batches")
        guarded_mkdir(rdir / "private")
        n_by_story = {}
        for story in stories:
            mixed = sorted(units[story])
            random.Random(f"{SEED}|{ROUND}|{story}|mix").shuffle(mixed)
            items = []
            for n, (run, version, model, repeat, i) in enumerate(mixed, start=1):
                qid = f"{story}-n{n:02d}"
                items.append({"qid": qid, "text": texts[(run, version, model, story, repeat, i)]})
                key[qid] = {"story_id": story, "project": project, "version": version, "model": model, "repeat": repeat,
                            "requirement_index": i, "run": run}
            guarded_write_text(rdir / "batches" / f"{story}.json",
                               json.dumps({"story_id": story, "project": project, "n_items": len(items), "items": items}, ensure_ascii=False, indent=1) + "\n")
            n_by_story[story] = len(items)
        guarded_write_text(rdir / "private" / "key.json", json.dumps(key, ensure_ascii=False, indent=1) + "\n")
        item = X.check_product_folders(project)
        groups.append({
            "run": f"ft_b_{project}", "round": ROUND, "project": project,
            "docs_dir": item.docs_dir.relative_to(RTC).as_posix(), "repo_name": item.repo_dir.name,
            "n_doc_files": sum(1 for _ in item.docs_dir.glob("**/*.md")), "repo_hint": REPO_HINT[project],
            "stories": [{"story_id": s, "n_items": n} for s, n in n_by_story.items()],
        })
        print(f"{project:10s} stories {len(stories)}, items {sum(n_by_story.values())}: "
              + ", ".join(f"{name} {counts[(name, 'drawn')]} (cells with fewer: {counts[(name, 'short cells')]})" for name, *_ in SOURCES))
    Path(args.args_out).write_text(json.dumps({"seats": 2, "chunk_size": 18, "groups": groups}), encoding="utf-8")
    print(f"job list of the judges written: {args.args_out}")
    return 0


def _journal_rows(paths: list[str]) -> dict[str, dict[str, dict]]:
    """seat -> qid -> row, from the journals of the judge workflows (a later journal wins)."""
    seats: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for path in paths:
        labels: dict[str, str] = {}
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "started":
                labels[d["agentId"]] = d.get("label") or "?"
            elif d.get("type") == "result":
                r = d.get("result")
                if isinstance(r, str):
                    try:
                        r = json.loads(r)
                    except ValueError:
                        r = None
                seat = labels.get(d.get("agentId"), "?").split(":")[0]
                for row in (r or {}).get("rows") or []:
                    seats[seat][row["qid"]] = row
    return seats


def cmd_final(args: argparse.Namespace) -> int:
    seats = _journal_rows(args.judges.split(","))
    opus = _journal_rows(args.opus.split(",")).get("judge3", {}) if args.opus != "none" else {}
    how = collections.Counter()
    for project in PROJECTS:
        key = json.loads((round_dir(project) / "private" / "key.json").read_text(encoding="utf-8"))
        decisions_path = JUDGING / "record" / f"nd_{project}" / "main_session_decisions.json"
        main = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.is_file() else {}
        out: dict[str, dict[str, list]] = collections.defaultdict(lambda: {"final": []})
        for qid in sorted(key):
            a, b = seats["judge1"].get(qid), seats["judge2"].get(qid)
            if a is None or b is None:
                print(f"[FAIL] {qid}: a label of a Sonnet judge is missing")
                return 1
            if qid in main:
                src, tag = main[qid], "main session"
            elif a["label"] == b["label"]:
                src, tag = a, "two Sonnet judges equal"
            else:
                o = opus.get(qid)
                if o is None:
                    print(f"[FAIL] {qid}: two different labels and no label of the Opus judge")
                    return 1
                if o["label"] == a["label"]:
                    src, tag = a, "Opus with Sonnet judge 1"
                elif o["label"] == b["label"]:
                    src, tag = b, "Opus with Sonnet judge 2"
                else:
                    print(f"[FAIL] {qid}: three different labels and no decision of the main session")
                    return 1
            how[tag] += 1
            out[key[qid]["story_id"]]["final"].append({
                "qid": qid, "label": src["label"], "kind": src.get("kind") or "none", "claim": src.get("claim") or "",
                "why": src.get("why") or "", "doubt": bool(src.get("doubt")),
            })
        guarded_mkdir(JUDGING)
        guarded_write_text(JUDGING / f"judgments_ft_b_{project}_{ROUND}.json", json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print("how the final label was made:", dict(how))
    return 0


def cmd_labels(_args: argparse.Namespace) -> int:
    cells: dict[tuple[str, str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    kinds: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    n_req: dict[tuple[str, str], list[int]] = collections.defaultdict(list)
    run_set = read_run_set()
    for project in PROJECTS:
        standard = set(run_set[project]["standard"])
        for name, pattern, version, _n in SOURCES:
            for model in MODELS:
                path = TUNING_ROOT / pattern.format(project=project) / project / model_slug(model) / "records.jsonl"
                for rec in read_jsonl(path):
                    if rec.get("error") is None and rec["config_id"] == version and rec["story_id"] in standard:
                        n_req[(name, model)].append(len(rec.get("requirements") or []))
        key = json.loads((round_dir(project) / "private" / "key.json").read_text(encoding="utf-8"))
        judgments = json.loads((JUDGING / f"judgments_ft_b_{project}_{ROUND}.json").read_text(encoding="utf-8"))
        rows = [r for seats in judgments.values() for r in seats["final"]]
        if {r["qid"] for r in rows} != set(key):
            print(f"[FAIL] {project}: the labels do not cover the drawn items")
            return 1
        for r in rows:
            k = key[r["qid"]]
            arm = ARM_OF[k["version"]]
            cells[(arm, k["story_id"], k["model"])][r["label"]] += 1
            if r["label"] == "U":
                kinds[arm][r.get("kind") or "none"] += 1
    out: dict = {"arms": {}}
    print("Arm    items   S     G     U   (of 100) | for each answer: requirements, S, G, U | stories with one U or more")
    for name, *_ in SOURCES:
        total = collections.Counter()
        for (arm, _s, _m), c in cells.items():
            if arm == name:
                total.update(c)
        n = sum(total.values())
        per_answer = statistics.fmean(statistics.fmean(n_req[(name, m)]) for m in MODELS)
        yields = collections.Counter()
        for m in MODELS:
            cm = collections.Counter()
            for (arm, _s, mm), c in cells.items():
                if arm == name and mm == m:
                    cm.update(c)
            for label in "SGU":
                yields[label] += statistics.fmean(n_req[(name, m)]) * cm[label] / sum(cm.values()) / len(MODELS)
        stories_u = len({s for (arm, s, _m), c in cells.items() if arm == name and c["U"] > 0})
        n_stories = len({s for (arm, s, _m) in cells if arm == name})
        out["arms"][name] = {"n_items": n, **{label: total[label] / n for label in "SGU"}, "counts": dict(total),
                             "requirements_per_answer": per_answer, **{f"{label}_per_answer": yields[label] for label in "SGU"},
                             "u_kinds": dict(kinds[name]), "stories_with_u": stories_u, "n_stories": n_stories}
        print(f"{name:5s}  {n:4d}  {100 * total['S'] / n:5.1f} {100 * total['G'] / n:5.1f} {100 * total['U'] / n:5.1f}            | "
              f"{per_answer:.2f}, {yields['S']:.2f}, {yields['G']:.2f}, {yields['U']:.2f} | {stories_u} of {n_stories}")
        print(f"       counts {dict(total)}; kinds of U {dict(kinds[name])}")
    guarded_write_text(FINAL_TEST_ROOT / "measure2_no_documentation.json", json.dumps(out, indent=1) + "\n")
    print("written: final_test/measure2_no_documentation.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("batches")
    b.add_argument("--args-out", required=True, help="file for the job list of the judging workflow (scratch folder)")
    b.set_defaults(fn=cmd_batches)
    f = sub.add_parser("final")
    f.add_argument("--judges", required=True, help="journal file(s) of the Sonnet judges, comma separated")
    f.add_argument("--opus", required=True, help="journal file(s) of the Opus judges, comma separated, or the word none")
    f.set_defaults(fn=cmd_final)
    sub.add_parser("labels").set_defaults(fn=cmd_labels)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
