"""Final test: the author checks the code-like names in 30 requirements (round "std"), blind to the version.

Same kind of check as the names sheet of 15 November (scripts/build_study2_review_sheet_v2.py), for 30
requirements of the final test. These stories have no fact sheet, so "found" means: the documentation,
the system description or the source code of the product contains the name.

  sample   draws 30 of the 336 judged requirements (5 for each product and version), finds their
           code-like names with study2_names, writes key_do_not_open/key.json, work/items.json and
           work/names_to_classify.json.
  render   needs work/name_classes.json ({project: {name: "product"|"standard"|"not_a_name"}}); writes
           names_sheet.md, labels_names_george.csv, names_rules.md, README.md, manifest.json.
  check    looks for words that show the version, a model or a judge label in the files the person can open.
  score    compares the 30 letters with the final labels of the AI judges (counts only).

Run from coded_project/secreqgen with PYTHONPATH=src:scripts. Every command prints counts only.
Option --dir sets the output folder (default: real_test_cases/final_test/names_check).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import study2_names as names
from tuning_lib import judging as J
from tuning_lib.guard import WriteGuard, guarded_mkdir, guarded_write_text
from tuning_lib.paths import FINAL_TEST_ROOT, PROJECTS, RTC, TUNING_ROOT, WORKSPACE

ROOT = WORKSPACE.resolve()
SEED_DATE = "20261119"
SEED_TEXT = f"{SEED_DATE}|names_check"
VERSIONS = ("v0_old", "s1_word_3000")
PER_CELL = 5
ROUND = "std"
DEFAULT_DIR = FINAL_TEST_ROOT / "names_check"
EVIDENCE = TUNING_ROOT / "_judging" / "evidence_x"
SHOWN_FIELDS = ["title", "description", "rationale", "acceptance_criteria", "implementation_notes"]
KIND_TEXT = {"product": "name of the product", "standard": "standard name", "not_a_name": "not a name"}
CLASS_VALUES = set(KIND_TEXT)
RULES_SOURCE = RTC / "study2" / "review_v2" / "names_rules.md"
RULES_NOTE = (
    "Note for the final test (17 November 2026): the stories of the final test have no fact sheet for you. "
    "Where these rules name the fact sheet, read: the documentation, the system description or the source "
    "code of the product. The rest of the rules is unchanged."
)
SHEET_HEADER = (
    "# Names in 30 requirements of the final test (your own check)\n\n"
    "1. Read `names_rules.md` first. The rules are the rules of 15 November. One difference: these stories have no "
    "fact sheet. So the column \"Kind\" comes from AI classifiers only, and \"found\" means that the documentation, "
    "the system description or the source code of the product contains the name.\n"
    "2. Open this file in the Markdown preview.\n"
    "3. Each requirement has a table of the code-like names in its text. Below the table, one line for each found "
    "name shows where the search found it.\n"
    "4. Give each requirement one letter, P, N or G, in `labels_names_george.csv` (columns: item, label, note).\n"
    "5. The file does not show which version of the tool made a requirement. Do not try to find out. Do not let the "
    "style, the length or a passage number in a requirement change your letter.\n"
    "6. Do not open the folder `key_do_not_open` or the folder `work`.\n"
    "7. When all 30 letters are in the file, tell the AI session. It runs the comparison.\n"
)
README_TEXT = (
    "# Names check of the final test\n"
    "This folder holds your own check of the code-like names in 30 requirements of the final test.\n"
    "Open the files in this order: 1. `names_rules.md`  2. `names_sheet.md` (in the Markdown preview)  3. `labels_names_george.csv`.\n"
    "Write one letter (P, N or G) for each item in `labels_names_george.csv`. Do not change its first line.\n"
    "For each N, write the name that was not found in the column note. Do not use a comma in a note.\n"
    "Do not open the folders `key_do_not_open` and `work`. They hold the key and the working files.\n"
    "The sheet does not show which version of the tool wrote a requirement. Do not try to find out.\n"
    "The comparison with the AI judges checks names only. It does not check \"a real name used for the wrong feature\".\n"
    "When all 30 letters are in the file, tell the AI session.\n"
)


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------
def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Out:
    """The output folder and the write guard. A folder outside the default roots is allowed only when
    the person names it with --dir (the guard still refuses the folders of Study 2 and Study 2b)."""

    def __init__(self, folder: Path) -> None:
        self.dir = Path(folder).resolve()
        self.guard = WriteGuard(allowed_roots=(TUNING_ROOT, FINAL_TEST_ROOT, self.dir))

    def write(self, rel: str, text: str) -> None:
        guarded_mkdir((self.dir / rel).parent, guard=self.guard)
        guarded_write_text(self.dir / rel, text, guard=self.guard)


def code_span(text: str) -> str:
    """A Markdown code span that holds the text safely, also when the text holds backticks."""
    if "`" not in text:
        return f"`{text}`"
    return "`` " + text + " ``"


def tidy_line(text: str, limit: int = 160) -> str:
    text = text.replace("\\<", "<").replace("\\>", ">")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------------------------
# Evidence files (story text, acceptance criteria, system description)
# ---------------------------------------------------------------------------------------------
def read_evidence(story_id: str) -> dict:
    text = (EVIDENCE / f"{story_id}.md").read_text(encoding="utf-8")
    m = re.search(r"## Story block\n(.*?)\n## System description\n(.*)\Z", text, re.S)
    if not m:
        raise SystemExit(f"STOP: the evidence file of {story_id} has an unexpected form")
    block, system = m.group(1).strip(), m.group(2)
    if "Acceptance criteria:" in block:
        story, crit = block.split("Acceptance criteria:", 1)
    else:
        story, crit = block, ""
    criteria = [re.sub(r"^\d+\.\s+", "", ln).strip() for ln in crit.splitlines() if re.match(r"^\d+\.\s+", ln)]
    fence = re.search(r"```[a-z]*\n(.*)```", system, re.S)
    system_text = fence.group(1) if fence else system
    return {"story": " ".join(story.split()), "criteria": criteria, "system": system_text}


# ---------------------------------------------------------------------------------------------
# ProjectSources, pointed to this copy
# ---------------------------------------------------------------------------------------------
def bind_study2_names() -> None:
    """study2_names has the constants RTC and STUDY2 with a hard-coded path. ProjectSources reads them
    at call time, so they are set here to the folders of this copy. study2_names.py is not changed."""
    names.RTC = RTC
    names.STUDY2 = RTC / "study2"


def open_sources(project: str) -> names.ProjectSources:
    bind_study2_names()
    src = names.ProjectSources(project)
    # Vocabulary of the documentation only: the Study 2 fact sheets must not take part.
    vocab: set[str] = set()
    for lines in src.docs.values():
        for line in lines:
            for span in names._BACKTICK.findall(line):
                span = span.strip(names._STRIP)
                if span and not re.search(r"\s", span):
                    vocab.add(span)
                    vocab.add(span.lstrip("-"))
            for m in names._FLAG.finditer(line):
                vocab.add(m.group(0).lstrip("-"))
    src.vocab = vocab
    used = {
        "names.RTC": names.RTC,
        "names.STUDY2": names.STUDY2,
        "docs_dir": src.docs_dir,
        "repo_dir": src.repo_dir,
        "system_context": names.RTC / project / "system_context.yaml",
    }
    for label, path in used.items():
        if not str(Path(path).resolve()).startswith(str(ROOT)):
            raise SystemExit(f"STOP: a path of ProjectSources is outside the copy ({label}); nothing was written")
    return src


# ---------------------------------------------------------------------------------------------
# Names of one requirement (second half of item_names of build_study2_review_sheet_v2.py)
# ---------------------------------------------------------------------------------------------
def occurs(name: str, text: str) -> bool:
    if name[-1:].isalnum():
        return names.whole_rx(name).search(text) is not None
    return name in text


def is_part(short: str, long: str) -> bool:
    return short != long and re.search(r"(?<![A-Za-z0-9_-])" + re.escape(short) + r"(?![A-Za-z0-9_-])", long) is not None


def place_of(src: names.ProjectSources, res: dict) -> str:
    status = res["status"]
    if status == "documentation":
        fname, line = res["doc_hits"][0]
        return f"documentation, {fname}, line {line}: {tidy_line(src.docs[fname][line - 1])}"
    if status == "documentation file":
        return f"documentation, file {res['doc_files'][0]}: the file name is the name"
    if status == "system description":
        return "system description"
    if status == "source code":
        first = res["repo_lines"][0]
        if first.startswith("file: "):
            return f"source code, {first[6:]}: the file name is the name"
        path, line, text = first.split(":", 2)
        return f"source code, {path}, line {line}: {tidy_line(text)}"
    return ""


def item_names(src: names.ProjectSources, story_id: str, req_text: str, story_text: str) -> list[dict]:
    src.system_text = read_evidence(story_id)["system"]  # the system description of this story
    found = names.extract_names(req_text, src.vocab)
    looked = {n: src.lookup(n, story_id, use_frozen=False) for n in found}
    rows: list[dict] = []
    for name, res in looked.items():
        if "=" in name and res["status"] == "not found":
            continue
        if any(is_part(name, other) and looked[other]["status"] != "not found" for other in looked):
            continue
        if res["status"] == "not found" and any(is_part(name, other) and looked[other]["status"] == "not found" for other in looked):
            continue
        given = occurs(name, story_text) or occurs(name, src.system_text) or res["status"] == "system description"
        rows.append({"name": name, "where": res["status"], "found": res["status"] != "not found", "given": given,
                     "place": place_of(src, res)})
    return rows


def context_of(req_text: str, name: str) -> str:
    """One line or sentence of the requirement that holds the name, at most 200 characters."""
    pieces = []
    for line in req_text.splitlines():
        pieces.extend(re.split(r"(?<=[.;])\s+", line))
    for piece in pieces:
        if name in piece:
            piece = " ".join(piece.split())
            if len(piece) <= 200:
                return piece
            i = piece.find(name)
            lo = max(0, min(i - 80, len(piece) - 200))
            return piece[lo : lo + 200]
    return " ".join(req_text.split())[:200]


# ---------------------------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------------------------
def cmd_sample(args, out: Out) -> int:
    key_path = out.dir / "key_do_not_open" / "key.json"
    if key_path.exists():
        print("[FAIL] key_do_not_open/key.json exists already; nothing is replaced")
        return 1
    population: dict[str, dict] = {}
    judgments: dict[str, dict] = {}
    for project in PROJECTS:
        key = read_json(TUNING_ROOT / f"ft_a_{project}" / "judging" / ROUND / "private" / "key.json")
        for qid, e in key.items():
            population[qid] = e
        for sid, block in read_json(FINAL_TEST_ROOT / "judging" / f"judgments_ft_a_{project}_{ROUND}.json").items():
            for row in block["final"]:
                judgments[row["qid"]] = row
    print(f"population: {len(population)} key entries, {len(judgments)} final labels")
    missing = [q for q in population if q not in judgments]
    if missing:
        print(f"[FAIL] {len(missing)} key entries have no final label")
        return 1

    picked: list[str] = []
    for project in PROJECTS:
        for version in VERSIONS:
            pool = sorted(q for q, e in population.items() if e["project"] == project and e["version"] == version)
            picked.extend(random.Random(f"{SEED_TEXT}|{project}|{version}").sample(pool, PER_CELL))
    assert len(picked) == 30 and len(set(picked)) == 30

    # Group by story; random order of the stories and of the items inside a story.
    by_story: dict[str, list[str]] = {}
    for q in sorted(picked):
        by_story.setdefault(population[q]["story_id"], []).append(q)
    rng = random.Random(f"{SEED_TEXT}|order")
    story_order = sorted(by_story)
    rng.shuffle(story_order)
    order: list[str] = []
    for sid in story_order:
        items = by_story[sid]
        rng.shuffle(items)
        order.extend(items)

    # Resolve each item to exactly one requirement.
    kept_by_run: dict[str, dict] = {}
    for project in PROJECTS:
        kept, _h = J._load_records(TUNING_ROOT / f"ft_a_{project}")
        kept_by_run[project] = kept
    sources = {p: open_sources(p) for p in PROJECTS}
    print("paths of ProjectSources: all inside the copy")
    items_out, key_out, classify = [], {}, {}
    n_names = Counter()
    for n, qid in enumerate(order, start=1):
        e = population[qid]
        matches = [rec for (cfg, mdl, sid, _arm, repeat), rec in kept_by_run[e["project"]].items()
                   if (cfg, mdl, sid, repeat) == (e["version"], e["model"], e["story_id"], e["repeat"])]
        if len(matches) != 1:
            print(f"[FAIL] an item resolves to {len(matches)} records (expected 1)")
            return 1
        reqs = matches[0].get("requirements") or []
        if not 0 <= e["requirement_index"] < len(reqs):
            print("[FAIL] a requirement index is outside the list")
            return 1
        req = reqs[e["requirement_index"]]
        item_id = f"R{n:02d}"
        req_text = names.requirement_text(req)
        ev = read_evidence(e["story_id"])
        story_text = ev["story"] + "\n" + "\n".join(ev["criteria"])
        rows = item_names(sources[e["project"]], e["story_id"], req_text, story_text)
        for r in rows:
            n_names["rows"] += 1
            n_names["found" if r["found"] else "not found"] += 1
            k = (e["project"], r["name"])
            if k not in classify:
                classify[k] = {"project": e["project"], "name": r["name"], "found": r["found"],
                               "context": context_of(req_text, r["name"])}
        items_out.append({
            "id": item_id, "project": e["project"], "story_id": e["story_id"],
            "text": J.item_text(req), "text_md": J.fmt_requirement(dict(req), md=True),
            "names": sorted(rows, key=lambda x: (x["name"].lower(), x["name"])),
        })
        j = judgments[qid]
        key_out[item_id] = {"qid": qid, **e, "judge_label": j["label"], "judge_kind": j.get("kind")}
    out.write("key_do_not_open/key.json", dump(key_out))
    out.write("work/items.json", dump(items_out))
    out.write("work/names_to_classify.json", dump(list(classify.values())))
    per_project = Counter(i["project"] for i in items_out)
    print("items for each product:", dict(per_project))
    print(f"stories: {len(story_order)}")
    print(f"name rows: {n_names['rows']}; different (product, name): {len(classify)}")
    print(f"rows found: {n_names['found']}; not found: {n_names['not found']}")
    print(f"different names found: {sum(1 for c in classify.values() if c['found'])}; "
          f"not found: {sum(1 for c in classify.values() if not c['found'])}")
    print(f"items with no code-like name: {sum(1 for i in items_out if not i['names'])}")
    return 0


# ---------------------------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------------------------
def names_table(rows: list[dict], classes: dict, project: str) -> str:
    if not rows:
        return "The script found no code-like name in this requirement.\n"
    out = ["| Name | Kind | Did the search find it? | In the story or the system description? |", "|---|---|---|---|"]
    for r in rows:
        name = r["name"].replace("|", "\\|")
        out.append(f"| `{name}` | {KIND_TEXT[classes[project][r['name']]]} | {'found' if r['found'] else 'not found'} | "
                   f"{'yes' if r['given'] else 'no'} |")
    return "\n".join(out) + "\n"


def where_lines(rows: list[dict]) -> str:
    found = [r for r in rows if r["found"]]
    if not found:
        return ""
    out = ["**Where the search found each name.**\n"]
    for r in found:
        place = r["place"]
        if ": " in place and not place.startswith("system"):
            head, line = place.split(": ", 1)
            if line.endswith("the file name is the name"):
                out.append(f"- {code_span(r['name'])}: {place}")
            else:
                out.append(f"- {code_span(r['name'])}: {head}: {code_span(line)}")
        else:
            out.append(f"- {code_span(r['name'])}: {place}")
    return "\n".join(out) + "\n"


def cmd_render(args, out: Out) -> int:
    work = out.dir / "work"
    items = read_json(work / "items.json")
    to_class = read_json(work / "names_to_classify.json")
    cls_path = work / "name_classes.json"
    if not cls_path.is_file():
        print("[FAIL] work/name_classes.json is missing")
        return 1
    classes = read_json(cls_path)
    for e in to_class:
        v = (classes.get(e["project"]) or {}).get(e["name"])
        if v is None:
            print(f"[FAIL] name_classes.json has no entry for a name of {e['project']} (name: {e['name']})")
            return 1
        if v not in CLASS_VALUES:
            print(f"[FAIL] name_classes.json has a bad value for a name of {e['project']} (name: {e['name']})")
            return 1
    csv_path = out.dir / "labels_names_george.csv"
    if csv_path.exists():
        with open(csv_path, encoding="utf-8", newline="") as fh:
            for row in list(csv.DictReader(fh)):
                if (row.get("label") or "").strip() or (row.get("note") or "").strip():
                    print("[FAIL] labels_names_george.csv holds a label or a note; the sheet is frozen")
                    return 1

    stories: dict[str, list[dict]] = {}
    for it in items:
        stories.setdefault(it["story_id"], []).append(it)
    md = [SHEET_HEADER]
    n_stories = len(stories)
    for n, (sid, its) in enumerate(stories.items(), start=1):
        ev = read_evidence(sid)
        md.append("\n---\n")
        md.append(f"\n## Story {n} of {n_stories}: {sid}\n")
        md.append(f"**Project.** {its[0]['project']}\n")
        md.append(f"**Story.** {J.md_safe(ev['story'].replace(' — ', ': ').replace('—', ', '))}\n")
        if ev["criteria"]:
            md.append("**Acceptance criteria of the story.**\n\n" + "\n".join(
                f"{i}. {J.md_safe(a.replace(' — ', ': ').replace('—', ', '))}" for i, a in enumerate(ev["criteria"], start=1)) + "\n")
        for it in its:
            block = f"\n### {it['id']}\n\n{it['text_md']}\n**Names in this requirement.**\n\n"
            block += names_table(it["names"], classes, it["project"])
            wl = where_lines(it["names"])
            if wl:
                block += "\n" + wl
            md.append(block)
    sheet = "\n".join(md) + "\n"
    out.write("names_sheet.md", sheet)
    out.write("labels_names_george.csv", "item,label,note\n" + "".join(f"{it['id']},,\n" for it in items))
    rules = RULES_SOURCE.read_text(encoding="utf-8")
    first, rest = rules.split("\n", 1)
    if not first.startswith("# "):
        print("[FAIL] names_rules.md of 15 November has no first heading")
        return 1
    out.write("names_rules.md", first + "\n\n" + RULES_NOTE + "\n" + rest)
    out.write("README.md", README_TEXT)
    n_rows = sum(len(i["names"]) for i in items)
    n_found = sum(1 for i in items for r in i["names"] if r["found"])
    manifest = {
        "items": len(items), "stories": n_stories,
        "items_for_each_product": dict(Counter(i["project"] for i in items)),
        "name_rows": n_rows, "different_names": len(to_class),
        "name_rows_found": n_found, "name_rows_not_found": n_rows - n_found,
        "seed_text": SEED_TEXT,
        "sha256": {f: sha256_file(out.dir / f) for f in ("names_sheet.md", "labels_names_george.csv", "names_rules.md", "README.md")},
    }
    out.write("manifest.json", dump(manifest))
    print(f"rendered: {len(items)} items, {n_stories} stories, {n_rows} name rows")
    return 0


# ---------------------------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------------------------
def cmd_check(args, out: Out) -> int:
    key = read_json(out.dir / "key_do_not_open" / "key.json")
    qids = sorted({e["qid"] for e in key.values()})
    fixed = ["v0_old", "s1_word_3000", "v3_pn", "ft_a_", "ft_b_", "qwen", "gemma", "judge_label", "judge_kind"]
    bad: list[tuple[str, str]] = []
    benign_whole_words = 0
    files = [p for p in sorted(out.dir.rglob("*")) if p.is_file()
             and not any(part in ("key_do_not_open", "work") for part in p.relative_to(out.dir).parts)]
    for p in files:
        rel = p.relative_to(out.dir).as_posix()
        text = p.read_text(encoding="utf-8", errors="replace")
        low = text.casefold()
        for w in fixed:
            if w in low:
                bad.append((rel, w))
        for q in qids:
            if q in text:
                bad.append((rel, "a qid of round std"))
                break
        if rel == "names_sheet.md":
            script_text, body = text.split("\n---\n", 1) if "\n---\n" in text else (text, "")
        elif rel in ("names_rules.md", "README.md"):
            script_text, body = text, ""
        else:
            script_text, body = "", text if rel.endswith(".md") else ""
        for w in ("NEW", "OLD"):
            if re.search(rf"\b{w}\b", script_text):
                bad.append((rel, f"the word {w} in text written by the script"))
            benign_whole_words += len(re.findall(rf"\b{w}\b", body))
    print(f"files read: {len(files)}")
    print(f"whole words NEW or OLD in requirement texts or quoted lines (allowed): {benign_whole_words}")

    problems = []
    sheet = (out.dir / "names_sheet.md").read_text(encoding="utf-8")
    ids = re.findall(r"^### (R\d\d)$", sheet, re.M)
    expect = [f"R{i:02d}" for i in range(1, 31)]
    if ids != expect:
        problems.append("the sheet does not hold R01..R30 in order")
    with open(out.dir / "labels_names_george.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != ["item", "label", "note"] or [r[0] for r in rows[1:]] != ids:
        problems.append("the CSV does not match the sheet (header or item order)")
    parts = re.split(r"^### R\d\d$", sheet, flags=re.M)[1:]
    for item_id, part in zip(ids, parts):
        if "| Name | Kind |" not in part and "The script found no code-like name in this requirement." not in part:
            problems.append(f"{item_id} has no names table and no sentence")
    man = read_json(out.dir / "manifest.json")
    for f, h in man["sha256"].items():
        if not (out.dir / f).is_file() or sha256_file(out.dir / f) != h:
            problems.append(f"the hash of {f} does not match the manifest")
    for rel, w in bad:
        print(f"[FAIL] {rel}: forbidden word ({w})")
    for p in problems:
        print(f"[FAIL] {p}")
    if bad or problems:
        return 1
    print("check: ok")
    return 0


# ---------------------------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------------------------
def cmd_score(args, out: Out) -> int:
    with open(out.dir / "labels_names_george.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    mine = {r["item"]: (r["label"] or "").strip() for r in rows}
    key = read_json(out.dir / "key_do_not_open" / "key.json")
    if len(mine) != 30 or set(mine) != set(key) or any(v not in ("P", "N", "G") for v in mine.values()):
        print("[FAIL] each of the 30 rows must hold P, N or G")
        return 1

    def table(ids) -> dict:
        t = {s: {l: 0 for l in "PNG"} for s in "SGU"}
        for i in ids:
            t[key[i]["judge_label"]][mine[i]] += 1
        return t

    def show(title: str, t: dict) -> None:
        print(title)
        print("  judges \\ you    P   N   G")
        for s in "SGU":
            print(f"  {s:12s} {t[s]['P']:4d}{t[s]['N']:4d}{t[s]['G']:4d}")

    result = {"all": table(key)}
    show("all 30 items", result["all"])
    for v in sorted({e["version"] for e in key.values()}):
        result[v] = table(i for i in key if key[i]["version"] == v)
        show(f"version {v}", result[v])
    u_kind: dict[str, dict] = {}
    for i in key:
        if key[i]["judge_label"] == "U":
            u_kind.setdefault(str(key[i]["judge_kind"]), {l: 0 for l in "PNG"})[mine[i]] += 1
    result["judge_U_by_kind"] = u_kind
    print("judges' U items, judge kind against your letter:")
    for k, t in sorted(u_kind.items()):
        print(f"  {k:14s} P {t['P']}  N {t['N']}  G {t['G']}")
    out.write("key_do_not_open/result.json", dump(result))
    print("result written: key_do_not_open/result.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="output folder (default: real_test_cases/final_test/names_check)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sample", "render", "check", "score"):
        sub.add_parser(name)
    args = ap.parse_args()
    out = Out(Path(args.dir))
    return {"sample": cmd_sample, "render": cmd_render, "check": cmd_check, "score": cmd_score}[args.cmd](args, out)


if __name__ == "__main__":
    sys.exit(main())
