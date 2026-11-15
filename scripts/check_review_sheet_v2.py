"""Key-aware check of the blinded review sheet, version 2 (14 November 2026).

This script opens the review key INSIDE the code. It never prints the key, an arm, a model, a run
or a rate for an arm. The screen shows one PASS or FAIL line for each check. The details go to the
sealed file:

    final_week/reports/_sealed/review_sheet_v2_leak_check.md   (section "Key-aware checks (script)")

If the sealed file exists, the script replaces only its own section and keeps the other sections.
The script only reads the sheet, the key and the raw records. It changes nothing but the sealed file.

Checks
    K1  The key has 60 items. Each points to a real record and requirement. No item is double.
    K2  The text of each item equals the five shown fields of its record (white space normalised).
    K3  Order tests (permutation, 20,000 permutations, seed 20261114) of three position features
        against the arm and against the generator model. PASS when each p-value is above 0.01.
    K4  One evidence file for each story, the same for each item of the story.
    K5  Hidden fields (source_references of 12+ characters, id, category, priority) do not appear in
        the item text unless the same string is inside a shown field.
    K6  No model name, arm word or run number in the files that the rater reads.
    K7  Guess of the arm from the number of items in each story: not better than the most frequent
        arm by more than 5 percentage points.
    K8  Content cues (written to the sealed file only).

Usage (from the thesis root):
    coded_project/secreqgen/.venv/bin/python coded_project/secreqgen/scripts/check_review_sheet_v2.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

THESIS = Path("/Users/georgeg/Documents/Uni Docs/Thesis")
STUDY2 = THESIS / "coded_project/real_test_cases/study2"
V2 = STUDY2 / "review_v2"
KEY = V2 / "key_do_not_open/review_key.json"
GUESSES = V2 / "key_do_not_open/guesses_text_only.json"
SEALED = THESIS / "final_week/reports/_sealed/review_sheet_v2_leak_check.md"
FIRST_LINE = "Sealed until the review labels are locked."
SECTION = "Key-aware checks (script)"
PROJECTS = ["mastodon", "keycloak", "bitwarden"]
SEED = 20261114
N_PERM = 20000
SHOWN = ["title", "description", "rationale", "acceptance_criteria", "implementation_notes"]

details: dict[str, list[str]] = defaultdict(list)
results: dict[str, bool | None] = {}


def norm(s) -> str:
    return " ".join(str(s or "").split())


def unescape_md(text: str) -> str:
    """The builder writes a backslash before "<", "*" and a free "_" outside code spans in the .md
    files, so that a Markdown viewer does not hide text such as <realm>_<alias>. The file
    sheet.json keeps the plain characters."""
    out = []
    for part in re.split(r"(`[^`\n]*`)", text):  # the builder does not escape inside code spans
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(part)
        else:
            out.append(part.replace("\\<", "<").replace("\\*", "*").replace("\\_", "_")
                       .replace("\\$", "$").replace("!\\[", "!["))
    return "".join(out)


def shown_text(req: dict) -> str:
    """The five shown fields of a requirement, white space normalised."""
    crit = [norm(a) for a in (req.get("acceptance_criteria") or []) if norm(a)]
    return norm(" ".join([norm(req.get("title")), norm(req.get("description")), norm(req.get("rationale")),
                          " ".join(crit), norm(req.get("implementation_notes"))]))


def strip_labels(text: str) -> str:
    """Remove the labels that the sheet adds around the fields, then normalise."""
    t = re.sub(r"\*\*(Title|Description|Rationale|Acceptance criteria|Implementation notes)\.\*\*", " ", text)
    t = re.sub(r"(?m)^- ", " ", t)
    t = t.replace("(none)", "(none)")
    return norm(t)


def report(name: str, ok: bool | None, line: str) -> None:
    results[name] = ok
    word = "NOT RUN" if ok is None else ("PASS" if ok else "FAIL")
    print(f"{name} {word}: {line}")


# ---------------------------------------------------------------- load
sheet = json.loads((V2 / "sheet.json").read_text())
sheet_md = (V2 / "review_sheet.md").read_text()
manifest = json.loads((V2 / "manifest.json").read_text())
key = json.loads(KEY.read_text())

story_info = {}
for p in PROJECTS:
    data = json.loads((STUDY2 / p / "stories.json").read_text())
    for s in data["stories"]:
        story_info[s["id"]] = {"project": p, "fact_sheet": s["fact_sheet"]}

records: dict[tuple, list] = {}
for p in PROJECTS:
    for rdir in sorted((STUDY2 / p / "results").glob("*/")):
        f = rdir / "records.jsonl"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("error"):
                continue
            records[(p, r["model"], r["arm"], str(r["run"]), r["story_id"])] = r["requirements"]

items = []  # in sheet order: dict(item, text, story_id, pos_in_story, story_pos, no)
for sp, st in enumerate(sheet["stories"], start=1):
    for ip, it in enumerate(st["items"], start=1):
        items.append({"item": it["item"], "text": it["text"], "story_id": st["story_id"],
                      "pos": ip, "story_pos": sp, "no": int(it["item"][1:])})
by_item = {i["item"]: i for i in items}


def record_for(k: dict):
    reqs = records.get((k["project"], k["model"], k["arm"], str(k["run"]), k["story_id"]))
    if reqs is None or not (0 <= k["req_index"] < len(reqs)):
        return None
    return reqs[k["req_index"]]


# ---------------------------------------------------------------- K1
try:
    problems = []
    if len(key) != 60:
        problems.append(f"key has {len(key)} items")
    if len(items) != 60 or set(key) != set(by_item):
        problems.append("item ids of the key and of the sheet differ")
    seen = Counter()
    for item, k in key.items():
        if record_for(k) is None:
            problems.append(f"{item}: no record or requirement")
        seen[(k["project"], k["model"], k["arm"], str(k["run"]), k["story_id"], k["req_index"])] += 1
        if item in by_item and by_item[item]["story_id"] != k["story_id"]:
            problems.append(f"{item}: story differs between key and sheet")
    doubles = [x for x, c in seen.items() if c > 1]
    if doubles:
        problems.append(f"{len(doubles)} double items")
    details["K1"] = [f"Items in key: {len(key)}. Items in sheet: {len(items)}. Records found: "
                     f"{sum(record_for(k) is not None for k in key.values())}. Doubles: {len(doubles)}."] + problems
    report("K1", not problems, "key size, records and doubles" + ("" if not problems else f" ({len(problems)} problems)"))
except Exception as e:  # noqa: BLE001
    report("K1", False, f"error {type(e).__name__}")
    details["K1"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K2
try:
    bad_json, bad_md = [], []
    md_blocks = {}
    for m in re.finditer(r"(?ms)^### (R\d\d)\n\n(.*?)(?=^### R\d\d\n|^---\n|\Z)", sheet_md):
        md_blocks[m.group(1)] = m.group(2)
    for item, k in key.items():
        rec = record_for(k)
        want = shown_text(rec)
        if strip_labels(by_item[item]["text"]) != want:
            bad_json.append(item)
        if item not in md_blocks or strip_labels(unescape_md(md_blocks[item])) != want:
            bad_md.append(item)
    # The names sheet shows the same item texts, each followed by the table of its names.
    bad_names = []
    names_file = V2 / "names_sheet.md"
    if names_file.exists():
        names_md = names_file.read_text()
        for m in re.finditer(r"(?ms)^### (R\d\d)\n\n(.*?)(?=^### R\d\d\n|^---\n|\Z)", names_md):
            body = m.group(2).split("**Names in this requirement.**")[0]
            rec = record_for(key[m.group(1)]) if m.group(1) in key else None
            if rec is None or strip_labels(unescape_md(body)) != shown_text(rec):
                bad_names.append(m.group(1))
        if len(re.findall(r"(?m)^### R\d\d$", names_md)) != 60:
            bad_names.append("number of items in names_sheet.md")
    bad_md = bad_md + bad_names
    ok = not bad_json and not bad_md and len(md_blocks) == 60
    details["K2"] = [f"Items checked: {len(key)}. Differences in sheet.json: {len(bad_json)}. "
                     f"Differences in review_sheet.md: {len(bad_md)}. Item blocks found in review_sheet.md: {len(md_blocks)}.",
                     "Items with a difference: " + ", ".join(sorted(set(bad_json + bad_md))) if (bad_json or bad_md) else ""]
    report("K2", ok, "item text equals the five shown fields" + ("" if ok else f" ({len(set(bad_json + bad_md))} items differ)"))
except Exception as e:  # noqa: BLE001
    report("K2", False, f"error {type(e).__name__}")
    details["K2"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K3
try:
    rng = np.random.default_rng(SEED)
    order = sorted(by_item)
    feats = {
        "item number": np.array([by_item[i]["no"] for i in order], float),
        "position of the item inside its story": np.array([by_item[i]["pos"] for i in order], float),
        "position of the story in the sheet": np.array([by_item[i]["story_pos"] for i in order], float),
    }
    groups = {
        "arm (4 groups)": [key[i]["arm"] for i in order],
        "model (2 groups)": [key[i]["model"] for i in order],
    }

    def stat(x, codes, counts, ng):
        means = np.bincount(codes, weights=x, minlength=ng) / counts
        return float(np.max(np.abs(means - x.mean())))

    pvals = {}
    for fname, x in feats.items():
        for gname, labs in groups.items():
            levels = sorted(set(labs))
            codes = np.array([levels.index(g) for g in labs])
            counts = np.bincount(codes, minlength=len(levels)).astype(float)
            obs = stat(x, codes, counts, len(levels))
            ge = 0
            for _ in range(N_PERM):
                if stat(x, rng.permutation(codes), counts, len(levels)) >= obs - 1e-12:
                    ge += 1
            pvals[(fname, gname)] = (ge + 1) / (N_PERM + 1)
    ok = all(p > 0.01 for p in pvals.values())
    for (fname, gname), p in pvals.items():
        print(f"    K3 {fname} vs {gname.split(' ')[0]}: p = {p:.3f}")
    details["K3"] = [f"Permutations: {N_PERM}. Seed: {SEED}. Statistic: largest absolute difference between a group mean and the overall mean."]
    details["K3"] += [f"{fname} vs {gname}: p = {p:.4f}" for (fname, gname), p in pvals.items()]
    report("K3", ok, "order tests, all p-values above 0.01" if ok else "order tests, at least one p-value is 0.01 or lower")
except Exception as e:  # noqa: BLE001
    report("K3", False, f"error {type(e).__name__}")
    details["K3"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K4
try:
    problems = []
    ev_dir = V2 / "evidence"
    story_ids = [s["story_id"] for s in sheet["stories"]]
    if len(set(story_ids)) != len(story_ids):
        problems.append("a story appears two times in sheet.json")
    # A reference link has the form [file.md:12](../../../path#L12). Excerpts can hold other brackets.
    link = re.compile(r"\[([^\[\]]+)\]\(\.\./[^)]*\)")

    def parts_a_to_c(text: str) -> str:
        """Part D differs by design: the .md file lists links, sheet.json names the files."""
        return text.split("### D. The source files of this story")[0]

    for st in sheet["stories"]:
        sid = st["story_id"]
        f = ev_dir / f"{sid}.md"
        if not f.exists():
            problems.append(f"{sid}: evidence file missing")
            continue
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        if manifest["sha256"]["evidence"].get(f"{sid}.md") != h:
            problems.append(f"{sid}: hash differs from manifest")
        body = f.read_text().split("\n\n", 2)[2] if f.read_text().count("\n\n") >= 2 else ""
        if norm(link.sub(r"\1", unescape_md(parts_a_to_c(body)))) != norm(parts_a_to_c(st["evidence"])):
            problems.append(f"{sid}: evidence in sheet.json differs from the file")
        for it in st["items"]:
            if set(it) != {"item", "text", "names_table", "names_rows"}:
                problems.append(f"{it['item']}: item keys are {sorted(set(it))}, not item, text, names_table and names_rows")
        if len(re.findall(re.escape(f"evidence/{sid}.md"), sheet_md)) != 2:
            problems.append(f"{sid}: link count in review_sheet.md is not one section link")
    files_on_disk = {p.name for p in ev_dir.glob("*.md")}  # 15 story files and the practice files
    if files_on_disk != set(manifest["sha256"]["evidence"]):
        problems.append("evidence files on disk and in the manifest differ")
    for fname in ("review_sheet.md", "sheet.json"):
        if manifest["sha256"][fname] != hashlib.sha256((V2 / fname).read_bytes()).hexdigest():
            problems.append(f"{fname}: hash differs from manifest")
    details["K4"] = [f"Stories with items: {len(story_ids)}. Evidence files on disk: {len(files_on_disk)}."] + problems
    report("K4", not problems, "one evidence file for each story, same for each item" + ("" if not problems else f" ({len(problems)} problems)"))
except Exception as e:  # noqa: BLE001
    report("K4", False, f"error {type(e).__name__}")
    details["K4"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K5
try:
    collisions = []
    n_strings = 0
    for item, k in key.items():
        rec = record_for(k)
        hidden = [str(rec.get(f)) for f in ("id", "asvs_category", "priority") if rec.get(f) not in (None, "")]
        refs = rec.get("source_references") or []
        if isinstance(refs, (str, dict)):
            refs = [refs]
        for r in refs:
            s = norm(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))
            if len(s) >= 12:
                hidden.append(s)
        shown = shown_text(rec)
        text = norm(by_item[item]["text"])
        for h in hidden:
            h = norm(h)
            n_strings += 1
            if h and h in text and h not in shown:
                collisions.append(item)
    details["K5"] = [f"Hidden strings tested: {n_strings}. Collisions: {len(collisions)}."]
    if collisions:
        details["K5"].append("Items with a collision: " + ", ".join(sorted(set(collisions))))
    report("K5", not collisions, f"hidden-field collisions: {len(collisions)}")
except Exception as e:  # noqa: BLE001
    report("K5", False, f"error {type(e).__name__}")
    details["K5"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K6
try:
    terms = {
        "gemma": r"gemma", "qwen": r"qwen", "arm A-D": r"\barm\s+[A-D]\b", "zero-shot": r"zero[-_ ]shot",
        "run_00": r"run_00", "run 1-3": r"\brun\s+[1-3]\b", "token list": r"token list",
        "entire corpus": r"entire corpus", "whole corpus": r"whole corpus",
    }
    files = [V2 / "review_sheet.md", V2 / "practice.md", V2 / "sheet.json"] + sorted((V2 / "evidence").glob("*.md"))
    files += [f for f in (V2 / "names_sheet.md", V2 / "names_practice.md", V2 / "names_rules.md", V2 / "names_practice_answers.md",
                          V2 / "labelling_rules.md", V2 / "name_classes.json") if f.exists()]
    hits = []
    for f in files:
        lines = f.read_text().splitlines()
        for tname, rx in terms.items():
            for ln, line in enumerate(lines, start=1):
                for m in re.finditer(rx, line, flags=re.I):
                    a = max(0, m.start() - 40)
                    hits.append(f"{f.name} line {ln} term '{tname}': ...{line[a:m.end() + 40]}...")
    details["K6"] = [f"Files searched: {len(files)}. Hits: {len(hits)}."] + hits[:40]
    report("K6", not hits, f"forbidden words in the rater files: {len(hits)}")
except Exception as e:  # noqa: BLE001
    report("K6", False, f"error {type(e).__name__}")
    details["K6"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K7
try:
    story_count = Counter(i["story_id"] for i in items)
    arms = [key[i["item"]]["arm"] for i in items]
    n = len(arms)
    base_arm, base_n = Counter(arms).most_common(1)[0]
    baseline = base_n / n

    def best_in_sample(feature_of) -> float:
        groups_ = defaultdict(list)
        for i, a in zip(items, arms):
            groups_[feature_of(i)].append(a)
        return sum(Counter(v).most_common(1)[0][1] for v in groups_.values()) / n

    def best_loso(feature_of) -> float:
        """Leave one story out: learn the rule on the other stories, guess for the held-out story."""
        correct = 0
        for held in story_count:
            tr = defaultdict(list)
            for i, a in zip(items, arms):
                if i["story_id"] != held:
                    tr[feature_of(i)].append(a)
            allarms = Counter(a for v in tr.values() for a in v)
            for i, a in zip(items, arms):
                if i["story_id"] == held:
                    pool = tr.get(feature_of(i)) or list(allarms.elements())
                    correct += Counter(pool).most_common(1)[0][0] == a
        return correct / n

    feat_count = lambda i: story_count[i["story_id"]]  # noqa: E731
    feat_pc = lambda i: (story_info[i["story_id"]]["project"], story_count[i["story_id"]])  # noqa: E731
    acc = {
        "count only, in sample (upper bound)": best_in_sample(feat_count),
        "project and count, in sample (upper bound)": best_in_sample(feat_pc),
        "count only, leave one story out": best_loso(feat_count),
        "project and count, leave one story out": best_loso(feat_pc),
    }
    best = max(acc.values())
    # Overfit reference: how high is the in-sample "project and count" rule when the arms are shuffled?
    rng7 = np.random.default_rng(SEED)
    arms_arr = np.array(arms)
    feats_pc = [feat_pc(i) for i in items]
    null = []
    for _ in range(2000):
        sh = rng7.permutation(arms_arr)
        g = defaultdict(list)
        for f_, a in zip(feats_pc, sh):
            g[f_].append(a)
        null.append(sum(Counter(v).most_common(1)[0][1] for v in g.values()) / n)
    null = np.array(null)
    p_pc = (1 + int((null >= acc["project and count, in sample (upper bound)"] - 1e-12).sum())) / 2001
    # PASS rule (corrected on 14 November 2026 by the orchestrator). The in-sample rules use the key to
    # choose their map, so they overfit and are only an upper bound. The check passes when
    #   (a) no leave-one-story-out rule is more than 5 points better than the constant guess, and
    #   (b) the in-sample rule is not better than the same rule with shuffled arms (p above 0.05).
    best_loso_acc = max(v for k_, v in acc.items() if "leave one story out" in k_)
    ok = best_loso_acc <= baseline + 0.05 and p_pc > 0.05
    details["K7"] = [
        "Method: a rater who knows the design (60 items, 4 arms, 3 projects) and the number of items in each story "
        "can map (project, count) to the most frequent arm of the stories with that pair. The in-sample rule uses the key "
        "to choose the best map, so it is an upper bound (it overfits: 15 stories give many small groups). "
        "The leave-one-story-out rule learns the map without the held-out story.",
        f"Baseline, the guess 'the most frequent arm': {baseline:.3f}. PASS limit: baseline + 0.05 = {baseline + 0.05:.3f}.",
        f"Items per story: {sorted(story_count.values())}.",
    ] + [f"{k_}: {v:.3f}" for k_, v in acc.items()] + [
        f"Shuffle reference for the in-sample project-and-count rule (arms shuffled, 2000 times): mean {null.mean():.3f}, "
        f"95th percentile {np.percentile(null, 95):.3f}; p-value of the observed rule {p_pc:.3f}.",
        f"First PASS rule of the task (too strict, it counts overfit): best of the four rules <= baseline + 0.05. Best rule: {best:.3f}.",
        "PASS rule in use: the best leave-one-story-out rule is within 5 points of the constant guess, and the in-sample rule "
        f"is not better than the same rule with shuffled arms (p above 0.05). Best leave-one-story-out rule: {best_loso_acc:.3f}.",
    ]
    report("K7", ok, "the number of items in a story does not help to guess the arm (held-out stories, shuffle reference)" if ok
           else "the number of items in a story helps to guess the arm on held-out stories, or beats the shuffle reference")
except Exception as e:  # noqa: BLE001
    report("K7", False, f"error {type(e).__name__}")
    details["K7"].append(f"Error: {e!r}")

# ---------------------------------------------------------------- K8
cues: list[str] = []
try:
    def has_docs(item):
        return key[item]["arm"] in ("B", "C", "D")

    def share(subset, pred):
        return (sum(pred(i) for i in subset), len(subset))

    def fmt(t):
        return f"{t[0]}/{t[1]} = {t[0] / t[1]:.3f}" if t[1] else "0/0"

    with_docs = [i["item"] for i in items if has_docs(i["item"])]
    without = [i["item"] for i in items if not has_docs(i["item"])]

    def ids_in(item):
        idents = story_info[by_item[item]["story_id"]]["fact_sheet"]["identifiers"]
        return any(x in by_item[item]["text"] for x in idents)

    nf_names: dict[str, list[str]] = {}
    for st in sheet["stories"]:
        txt = (V2 / "evidence" / f"{st['story_id']}.md").read_text()
        nf_names[st["story_id"]] = [m.group(1) for m in re.finditer(r"(?m)^\| `(.+?)` \| \*\*not found\*\*", txt)]

    def nf_in(item):
        return any(x in by_item[item]["text"] for x in nf_names[by_item[item]["story_id"]])

    cues.append("(i) Share of items whose text contains one or more identifiers of the fact sheet of the story:")
    cues.append(f"    items with documents: {fmt(share(with_docs, ids_in))}; items without documents: {fmt(share(without, ids_in))}")
    cues.append("(ii) Share of items whose text contains a name that the table of names reports as 'not found':")
    cues.append(f"    items with documents: {fmt(share(with_docs, nf_in))}; items without documents: {fmt(share(without, nf_in))}")
    cues.append(f"    (rows 'not found' in the evidence files: {sum(len(v) for v in nf_names.values())} over {len(nf_names)} stories)")

    if GUESSES.exists():
        g = {x["item"]: x for x in json.loads(GUESSES.read_text())}
        if set(g) != set(key):
            cues.append("(iii) The guess file does not cover the same 60 items. Not scored.")
        else:
            def yes(v):
                s = str(v).strip().lower()
                if s in ("yes", "y", "true", "1"):
                    return True
                if s in ("no", "n", "false", "0"):
                    return False
                raise ValueError("unknown value in the docs guess")

            truth_docs = {i: has_docs(i) for i in key}
            acc_docs = sum(yes(g[i]["docs"]) == truth_docs[i] for i in key) / 60
            const_docs = max(sum(truth_docs.values()), 60 - sum(truth_docs.values())) / 60
            acc_arm = sum(str(g[i]["arm"]).strip().upper()[:1] == key[i]["arm"] for i in key) / 60
            const_arm = Counter(key[i]["arm"] for i in key).most_common(1)[0][1] / 60
            cues.append("(iii) Guess from the text alone (an AI model, no key):")
            cues.append(f"    'documents: yes or no' accuracy {acc_docs:.3f}; best constant guess {const_docs:.3f}")
            cues.append(f"    arm accuracy {acc_arm:.3f}; best constant guess {const_arm:.3f}")
    else:
        cues.append("(iii) The file guesses_text_only.json does not exist. Not scored.")
    report("K8", True, "K8 written to the sealed file")
except Exception as e:  # noqa: BLE001
    report("K8", False, f"error {type(e).__name__}")
    cues.append(f"Error: {e!r}")

# ---------------------------------------------------------------- sealed file
names_ = {"K1": "Key and records", "K2": "Item text equals shown fields", "K3": "Order tests",
          "K4": "Evidence equality", "K5": "Hidden fields", "K6": "Forbidden words",
          "K7": "Arm guess from item counts", "K8": "Content cues"}
sec = [f"## {SECTION}", "", "Written by scripts/check_review_sheet_v2.py. Contains arm information. Do not show to the rater.", ""]
sec.append("| Check | Name | Result |")
sec.append("|---|---|---|")
for k_ in names_:
    r = results.get(k_)
    sec.append(f"| {k_} | {names_[k_]} | {'NOT RUN' if r is None else ('PASS' if r else 'FAIL')} |")
for k_ in names_:
    if k_ == "K8":
        continue
    sec += ["", f"### {k_}. {names_[k_]}", ""] + [f"- {d}" if d else "" for d in details.get(k_, [])]
sec += ["", "### Content cues (measured)", ""] + [f"- {c}" if not c.startswith("    ") else f"  {c.strip()}" for c in cues]
section_text = "\n".join(sec).rstrip() + "\n"

SEALED.parent.mkdir(parents=True, exist_ok=True)
if SEALED.exists():
    old = SEALED.read_text()
    lines = old.split("\n")
    start = next((n for n, ln in enumerate(lines) if ln.strip() == f"## {SECTION}"), None)
    if start is None:
        new = old.rstrip("\n") + "\n\n" + section_text
    else:
        end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
        new = "\n".join(lines[:start]) + "\n" + section_text + ("\n" + "\n".join(lines[end:]) if end < len(lines) else "")
    if not new.startswith(FIRST_LINE):
        new = FIRST_LINE + "\n\n" + new
else:
    new = FIRST_LINE + "\n\n" + section_text
SEALED.write_text(new)
print("Details written to the sealed file.")
sys.exit(0 if all(v is not False for v in results.values()) else 1)
