"""Build the blinded review sheet of Study 2, version 2 (14 November 2026).

Why a version 2
    The first sheet (scripts/build_study2_review_sheet.py, seed 20261102) showed for each item the
    documentation that the generator received: "none", the passages, a token list, or the whole
    corpus. That reveals the study arm. Nobody labelled that sheet. It stays on disk as a record.

What is different in version 2
    1. Each requirement of one story shows the same evidence:
         - the story and the fact sheet, with the corrections of the cross-check below it;
         - part A: excerpts of the project documentation for the fact-sheet identifiers;
         - part B: a table of the other code-like names in the requirements of that story, with
           the places where a text search found them (documentation, system description,
           repository, not found);
         - part C: the system description that each generator received (system_context.yaml);
         - part D: the source files of the story in the documentation folder.
       The search does not read the passages that the retrieval step stored, because only some
       arms received them. So the evidence does not depend on the arm, the model or the run.
    2. The field "source_references" of a requirement is not shown. Its form depends on the arm.
    3. A new seed (20261114) gives a new sample and a new key. The sampling function is the one of
       the first builder, imported without change (60 items, stratified by project and arm).
    4. The items are grouped by story. The order of the stories and of the items is random.
    5. Six practice items that are not in the sample, each with its own evidence file: three at
       random, and three that the orchestrator chose to show three cases of the names rules.

Outputs in real_test_cases/study2/review_v2/
    review_sheet.md               what the human rater reads
    evidence/<story>.md           the evidence of one story (parts A to D)
    evidence/practice_P<n>.md     the same for each practice item
    sheet.json                    the same content for the AI raters (no arm, no model, no run, no seed),
                                  with the full text of the source files of each story
    practice.md                   three practice items (not in the sample, never scored)
    practice_labels.txt           where the human rater writes the practice labels (never overwritten)
    names_sheet.md                part 1 of the review: each requirement with a table of its own names
    names_practice.md             the practice items in the form of part 1
    labels_names_george.csv       the empty answer file of the human rater for part 1 (never overwritten)
    manifest.json                 counts and SHA-256 of each file that a rater can open
    key_do_not_open/review_key.json        item -> project, model, arm, run, story, index
    key_do_not_open/manifest_private.json  seed, totals for each arm, model and project, hash of the key

Text rules
    The .md files escape "<", "*" and a free "_" outside code spans, so that a Markdown viewer does
    not hide text such as <realm>_<alias>. The file sheet.json keeps the text as it is.
    In the text of the stories and fact sheets an em-dash is shown as a colon or a comma.
    The text of a generated requirement is never changed (only white space is normalised).

The script prints no arm and no item text. It stops when a label file already holds a label or a
note, because the sheet is frozen from the first label on.

Usage: .venv/bin/python scripts/build_study2_review_sheet_v2.py [--seed 20261114] [--n 60] [--out DIR]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import random
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import study2_names as names  # noqa: E402

STUDY2 = names.STUDY2
OUT = STUDY2 / "review_v2"
CORRECTIONS = STUDY2 / "story_check" / "corrections.json"
RULES = OUT / "labelling_rules.md"
SEED = 20261114
N_PRACTICE = 3
PRACTICE_EXTRA_POSITIONS = (28, 15, 21)
SHOWN_FIELDS = ["title", "description", "rationale", "acceptance_criteria", "implementation_notes"]
HIDDEN_FIELDS = ["id", "user_story_id", "asvs_category", "priority", "source_references"]
UTF8 = "utf-8"
REVIEW_TOOLS = {
    "scoring_tool": HERE / "score_study2_review.py",
    "leak_check": HERE / "check_review_sheet_v2.py",
    "ai_rater_runner": HERE / "run_study2_ai_raters.py",
    "stats_module": HERE.parents[2] / "thesis" / "numbers" / "stats.py",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_first_builder():
    """Import the first builder so that the sampling is the registered code, not a copy."""
    path = HERE / "build_study2_review_sheet.py"
    spec = importlib.util.spec_from_file_location("review_sheet_v1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


def rid(r: dict) -> tuple:
    return (r["project"], r["model"], r["arm"], r["run"], r["story_id"], r["req_index"])


def clean(value) -> str:
    return " ".join(str(value or "").split())


def plain(text: str) -> str:
    """For the text of stories and fact sheets only: show an em-dash as a colon or a comma."""
    return text.replace(" — ", ": ").replace("—", ", ")


def md_safe(text: str) -> str:
    """Escape "<", "*", "$", "![" and a free "_" outside code spans, so that a Markdown viewer shows
    them. An underscore inside a word (CAS_UID_KEY) needs no escape."""
    out = []
    for part in re.split(r"(`[^`\n]*`)", text):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(part)
        else:
            part = part.replace("<", "\\<").replace("*", "\\*").replace("$", "\\$").replace("![", "!\\[")
            out.append(re.sub(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])", r"\\_", part))
    return "".join(out)


def fmt_requirement(req: dict, md: bool) -> str:
    """Only the fields in SHOWN_FIELDS. No source references, no category, no priority, no id."""
    safe = md_safe if md else (lambda s: s)
    crit = [safe(clean(a)) for a in (req.get("acceptance_criteria") or []) if clean(a)]
    ac = "\n".join(f"- {a}" for a in crit) or "- (none)"
    return (
        f"**Title.** {safe(clean(req.get('title')))}\n\n"
        f"**Description.** {safe(clean(req.get('description')))}\n\n"
        f"**Rationale.** {safe(clean(req.get('rationale')))}\n\n"
        f"**Acceptance criteria.**\n{ac}\n\n"
        f"**Implementation notes.** {safe(clean(req.get('implementation_notes'))) or '(none)'}\n"
    )


def cell(text: str, limit: int, md: bool) -> str:
    # The documentation files already escape "<" and ">" with a backslash: show the plain characters.
    text = text.replace("\\<", "<").replace("\\>", ">")
    text = " ".join(text.split()).replace("|", "\\|")
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return md_safe(text) if md else text


def doc_link(src: names.ProjectSources, fname: str, line: int | None, md: bool) -> str:
    label = f"{Path(fname).name}:{line}" if line else fname
    if not md:
        return label
    target = f"../../../{src.docs_rel}/{fname}".replace(" ", "%20")
    return f"[{label}]({target}#L{line})" if line else f"[{label}]({target})"


def pick_spread(hits: list[tuple[str, int]], k: int, gap: int) -> list[tuple[str, int]]:
    chosen: list[tuple[str, int]] = []
    for fname, n in hits:
        if all(not (fname == cf and abs(n - cn) < gap) for cf, cn in chosen):
            chosen.append((fname, n))
        if len(chosen) == k:
            break
    return chosen


def source_files(src: names.ProjectSources, story: dict) -> list[str]:
    """The documentation files of a story: the files that its fact sheet names. When the fact sheet
    gives descriptions and not file names (Mastodon), the files that hold one of its identifiers."""
    fs = story["fact_sheet"]
    if all(f in src.docs for f in fs["source_files"]):
        return list(fs["source_files"])
    return [f for f, lines in src.docs.items() if any(i in line for i in fs["identifiers"] for line in lines)]


def story_block(story: dict, project: str, corr: list[dict], md: bool) -> str:
    safe = md_safe if md else (lambda s: s)
    fs = story["fact_sheet"]
    fixed_facts = {c["n"] for c in corr if c["kind"] == "fact"}
    fixed_crit = {c["n"] for c in corr if c["kind"] == "criterion"}
    mark = " (corrected below)"
    story_fixed = any(c["kind"] == "story" for c in corr)
    lines = [
        f"**Project.** {project}\n",
        f"**Story.** {safe(plain(story['story']))}{mark if story_fixed else ''}\n",
        "**Acceptance criteria of the story.**\n\n"
        + "\n".join(f"{i}. {safe(plain(a))}{mark if i in fixed_crit else ''}" for i, a in enumerate(story["acceptance_criteria"], start=1)) + "\n",
        "**Fact sheet: identifiers that the documentation gives for this story.** "
        + ", ".join(f"`{t}`" for t in fs["identifiers"]) + "\n",
        "**Fact sheet: standard names (they are not specific to the project).** "
        + (", ".join(f"`{t}`" for t in fs["generic_terms"]) or "(none)") + "\n",
        "**Fact sheet: documented facts.**\n\n"
        + "\n".join(f"{i}. {safe(plain(f))}{mark if i in fixed_facts else ''}" for i, f in enumerate(fs["facts"], start=1)) + "\n",
    ]
    if corr:
        lines.append(
            "**Corrections to this fact sheet (cross-check of the documentation by AI models, approved by the author before the labels).**\n\n"
            + "\n".join(f"- {safe(c['text'])}" for c in corr) + "\n"
        )
    return "\n".join(lines)


def evidence_block(src: names.ProjectSources, story: dict, texts: list[str], md: bool, classes: dict | None = None) -> str:
    """The evidence of one story, parts A to D.

    `texts` holds the shown text of the requirements. Only part B uses it: it takes the code-like
    names from that text and looks them up. Nothing here reads the arm, the model, the run or the
    stored passages.
    """
    fs = story["fact_sheet"]
    files = source_files(src, story)
    named = all(f in src.docs for f in fs["source_files"])
    out = ["### A. Documentation excerpts for the identifiers of the fact sheet\n"]
    out.append(
        "For each identifier: up to three places in the documentation, each with two lines before and after. "
        "The search is a plain text search.\n"
    )
    for ident in fs["identifiers"]:
        hits = (src.doc_hits(ident, files) if named else []) or src.doc_hits(ident)
        if not hits:
            out.append(f"- `{ident}`: no line in the documentation contains this text.")
            continue
        out.append(f"- `{ident}` ({len(hits)} {'line' if len(hits) == 1 else 'lines'} in the documentation):")
        for fname, n in pick_spread(hits, 3, 6):
            out.append(f"  - {doc_link(src, fname, n, md)}: \"{cell(src.window(fname, n, 2, 2), 420, md)}\"")

    out.append("\n### B. Other code-like names in the requirements\n")
    out.append(
        "A script took each word that looks like a setting, an option, a command, a file, an endpoint or a class "
        "from the requirements. It searched for the whole name in the documentation folder of the project "
        "(file contents and file names), in the system description (part C), and in the source repository of "
        "the project (file contents and file names). The repository also holds source files of documentation "
        "that are not in the documentation folder. Names that are on the fact sheet are not repeated here. "
        "A note after a name says when it is a standard name (a programming language, a library, a framework, "
        "a vendor or a standard defines it) or not a name. AI models made these notes from general knowledge. "
        "The table is an aid. It can miss a name, and it can list a word that is not a name of the product.\n"
    )
    on_sheet = set(fs["identifiers"]) | set(fs["generic_terms"])
    story_text = story["story"] + "\n" + "\n".join(story["acceptance_criteria"])
    found: set[str] = set()
    for text in texts:
        found.update(names.extract_names(text, src.vocab))

    def in_story(name: str) -> bool:
        return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", story_text) is not None

    # Look up each name one time. Then remove rows that only repeat another row:
    #  - "--option=value" when the text with the value is not found (the row of "--option" stays);
    #  - a part of a longer name ("keycloak.conf" inside "conf/keycloak.conf") when the longer name is found.
    looked: dict[str, dict] = {}
    for name in sorted(found, key=lambda s: (s.lower(), s)):
        if name in on_sheet or name.lstrip("-") in on_sheet:
            continue
        looked[name] = src.lookup(name, story["id"], use_frozen=False)
        looked[name]["in_story"] = in_story(name)

    def is_part(short: str, long: str) -> bool:
        return short != long and re.search(
            r"(?<![A-Za-z0-9_-])" + re.escape(short) + r"(?![A-Za-z0-9_-])", long
        ) is not None

    keep = []
    for name, res in looked.items():
        if "=" in name and res["status"] == "not found":
            continue
        if any(is_part(name, other) and looked[other]["status"] != "not found" for other in looked):
            continue
        keep.append(name)

    rows = []
    for name in keep:
        res = looked[name]
        shown_name = f"`{cell(name, 80, False)}`"
        kind = (classes or {}).get(name, {})
        if kind.get("class") == "standard":
            shown_name += f" (standard name: {kind.get('owner') or 'a standard or a library'})"
        elif kind.get("class") == "not_a_name":
            shown_name += " (not a name: a placeholder, an example value or an ordinary word)"
        also = " It is also in the system description (part C)." if res.get("in_system") else ""
        if res.get("in_story"):
            also += " The story text also contains it."
        if res["status"] == "documentation":
            hits = res["doc_hits"]
            shown = "; ".join(
                f"{doc_link(src, f, n, md)}: \"{cell(src.window(f, n, 1, 1), 260, md)}\"" for f, n in pick_spread(hits, 3, 4)
            )
            more = f" ({len(hits)} lines in total)" if len(hits) > 3 else ""
            rows.append(f"| {shown_name} | documentation.{also} | {shown}{more} |")
        elif res["status"] == "documentation file":
            shown = "; ".join(doc_link(src, f, None, md) for f in res["doc_files"][:3])
            rows.append(f"| {shown_name} | documentation: it is the name of a file in the documentation folder.{also} | {shown} |")
        elif res["status"] == "system description":
            rows.append(f"| {shown_name} | system description (part C). Each generator received this text.{' The story text also contains it.' if res.get('in_story') else ''} | See part C. |")
        elif res["status"] == "source code":
            shown = "; ".join(f"\"{cell(line, 200, md)}\"" for line in res["repo_lines"])
            rows.append(
                f"| {shown_name} | repository only ({res['repo_files']} {'file' if res['repo_files'] == 1 else 'files'}). The documentation folder does not contain it.{also} | {shown} |"
            )
        elif res.get("in_story"):
            rows.append(f"| {shown_name} | in the story text only. The documentation folder, the system description and the repository do not contain it. | The story itself contains this text. |")
        else:
            rows.append(f"| {shown_name} | **not found** in the documentation folder, the system description and the repository | |")
    if rows:
        out.append("| Name | Where the search found it | Lines |")
        out.append("|---|---|---|")
        out.extend(rows)
    else:
        out.append("The script found no other code-like name.")

    out.append("\n### C. The system description that each generator received\n")
    out.append(
        "Each generator received this description of the system, in each arm of the study. "
        "So a name or a fact from it does not show that documentation was used.\n"
    )
    out.append("```yaml\n" + src.system_text.strip() + "\n```")

    out.append("\n### D. The source files of this story\n")
    if md:
        out.append(
            "These files of the documentation folder belong to the evidence. Read them when the excerpts are not sufficient. "
            "Do not use other files.\n"
        )
        out.extend(f"- {doc_link(src, f, None, True)} ({len(src.docs[f])} lines)" for f in files)
    else:
        out.append("The full text of these files follows after the requirements: " + ", ".join(files) + ".")
    return "\n".join(out) + "\n"


def item_names(src: names.ProjectSources, story: dict, req_text: str, corr: list[dict], classes: dict | None) -> list[dict]:
    """The code-like names of ONE requirement, each with its kind and the place where a search found it.

    It uses only the text of the requirement, the fact sheet with its corrections, the search and the
    classes of the names. It does not use the arm, the model, the run or the stored passages.
    Each row: name, kind (product, standard, not a name), where, given (the story text or the system
    description contains the name, so each generator received it).
    """
    fs = story["fact_sheet"]
    story_text = story["title"] + "\n" + story["story"] + "\n" + "\n".join(story["acceptance_criteria"])
    not_doc = {n: c["text"] for c in corr if c["kind"] == "not_documented" for n in c["names"]}
    std_corr = {n for c in corr if c["kind"] == "standard" for n in c["names"]}
    proj_corr = {n for c in corr if c["kind"] == "project" for n in c["names"]}

    def occurs(name: str, text: str) -> bool:
        # A name that ends with a letter or a digit must stand as a whole name. A prefix form
        # such as "OIDC_" or "${vault." is searched as plain text.
        if name[-1:].isalnum():
            return names.whole_rx(name).search(text) is not None
        return name in text

    def given(name: str) -> bool:
        return occurs(name, story_text) or src.in_system_description(name)

    rows: list[dict] = []
    done: set[str] = set()
    for ident in fs["identifiers"]:
        if occurs(ident, req_text):
            done.add(ident)
            if ident in not_doc:
                rows.append({"name": ident, "kind": "product", "where": "not found (a correction of the fact sheet says so)", "found": False, "given": given(ident)})
            else:
                rows.append({"name": ident, "kind": "standard" if ident in std_corr else "product",
                             "where": "fact sheet (the documentation gives this name)", "found": True, "given": given(ident)})
    for term in fs["generic_terms"]:
        if occurs(term, req_text):
            done.add(term)
            rows.append({"name": term, "kind": "product" if term in proj_corr else "standard",
                         "where": "fact sheet", "found": True, "given": given(term)})
    found = [n for n in names.extract_names(req_text, src.vocab) if n not in done and n.lstrip("-") not in done]
    looked = {n: src.lookup(n, story["id"], use_frozen=False) for n in found}

    def is_part(short: str, long: str) -> bool:
        return short != long and re.search(r"(?<![A-Za-z0-9_-])" + re.escape(short) + r"(?![A-Za-z0-9_-])", long) is not None

    for name, res in looked.items():
        if "=" in name and res["status"] == "not found":
            continue
        if any(is_part(name, other) and looked[other]["status"] != "not found" for other in looked) or any(is_part(name, d) for d in done):
            continue
        if res["status"] == "not found" and any(is_part(name, other) and looked[other]["status"] == "not found" for other in looked):
            continue  # the inner part of a longer name that is also not found: one row is sufficient
        if classes is not None and name not in classes:
            raise SystemExit(f"STOP: the name '{name}' of story {story['id']} has no entry in name_classes.json. "
                             "Classify it first: a name with no class must not become a name of the product by default.")
        cls = (classes or {}).get(name, {}).get("class", "product")
        owner = (classes or {}).get(name, {}).get("owner", "")
        where = {"documentation": "documentation", "documentation file": "documentation (a file of the documentation folder)",
                 "system description": "system description", "source code": "repository only", "not found": "not found"}[res["status"]]
        rows.append({"name": name, "kind": {"standard": "standard", "not_a_name": "not a name"}.get(cls, "product"), "owner": owner,
                     "where": where, "found": res["status"] != "not found", "given": given(name) or res["status"] == "system description"})
    return rows


def names_table(rows: list[dict]) -> str:
    if not rows:
        return "The script found no code-like name in this requirement.\n"
    kind_text = {"product": "name of the product", "standard": "standard name", "not a name": "not a name"}
    # The table shows only "found" or "not found". The place of a find (fact sheet, documentation,
    # repository) is not shown: some study arms received passages of the repository, so the place
    # could hint at the arm.
    # The rows are sorted by name, and a standard name shows no owner: the order and the form of a
    # row must not show which names are on the fact sheet.
    out = ["| Name | Kind | Did the search find it? | In the story or the system description? |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["name"].lower(), x["name"])):
        kind = kind_text[r["kind"]]
        name = r["name"].replace("|", "\\|")
        out.append(f"| `{name}` | {kind} | {'found' if r['found'] else 'not found'} | {'yes' if r['given'] else 'no'} |")
    return "\n".join(out) + "\n"


def labels_exist(out_dir: Path) -> list[str]:
    """Label files that hold any text after the item number (a label or a note).
    Works for comma, semicolon and tab files, with or without a byte order mark."""
    busy = []
    for p in sorted(out_dir.glob("labels_*.csv")):
        lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()[1:]
        for line in lines:
            rest = re.split(r"[,;\t]", line, maxsplit=1)[1:]
            if rest and re.sub(r"[\s,;\"]", "", rest[0]):
                busy.append(p.name)
                break
    return busy


def docs_hash(src: names.ProjectSources) -> str:
    h = hashlib.sha256()
    for fname in sorted(src.docs):
        h.update(fname.encode(UTF8))
        h.update((src.docs_dir / fname).read_bytes())
    return h.hexdigest()


def repo_head(src: names.ProjectSources) -> str:
    res = subprocess.run(["git", "-C", str(src.repo_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    return res.stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=Path, default=None, help="another output folder (for tests only)")
    args = ap.parse_args()

    global OUT, RULES
    if args.out is not None:
        OUT = args.out.resolve()
        RULES = OUT / "labelling_rules.md"
    OUT.mkdir(parents=True, exist_ok=True)
    busy = labels_exist(OUT)
    if busy:
        sys.exit(f"STOP: {', '.join(busy)} already holds a label or a note. The sheet is frozen. Nothing was written.")

    v1, v1_path = load_first_builder()
    recs = v1.load_records()
    sample = v1.stratified_sample(recs, args.n, args.seed)
    assert len(sample) == args.n and len({rid(r) for r in sample}) == args.n, "sample size or duplicate"

    # Practice items: not in the sample, three different stories, own random stream.
    in_sample = {rid(r) for r in sample}
    rng = random.Random(args.seed + 1)
    pool = sorted((r for r in recs if rid(r) not in in_sample), key=rid)
    practice = []
    for sid in rng.sample(sorted({r["story_id"] for r in pool}), N_PRACTICE):
        practice.append(rng.choice([r for r in pool if r["story_id"] == sid]))
    # Three more practice items. The orchestrator chose them by hand from the first candidates of a
    # second seeded order of the pool, to show three cases of the names rules: a name that the search
    # did not find, a name that is the example of the requirement, and a name that the requirement
    # asks to create. Practice items are never scored and have no key.
    extra_order = pool[:]
    random.Random(args.seed + 2).shuffle(extra_order)
    for position in PRACTICE_EXTRA_POSITIONS:
        practice.append(extra_order[position])
    assert len({r["story_id"] for r in practice}) == len(practice), "practice items must come from different stories"

    raw_corr = json.loads(CORRECTIONS.read_text(encoding=UTF8)) if CORRECTIONS.exists() else {}
    corrections = {k: v for k, v in raw_corr.items() if not k.startswith("_")}
    sources = {p: names.ProjectSources(p) for p in names.PROJECTS}
    classes_file = OUT / "name_classes.json"
    name_classes = json.loads(classes_file.read_text(encoding=UTF8)) if classes_file.exists() else {}

    # Display order: stories in the order of their first item in the shuffled sample.
    order: list[str] = []
    for r in sample:
        if r["story_id"] not in order:
            order.append(r["story_id"])
    by_story = {sid: [r for r in sample if r["story_id"] == sid] for sid in order}

    ev_dir = OUT / "evidence"
    ev_dir.mkdir(exist_ok=True)
    for old in ev_dir.glob("*.md"):
        old.unlink()
    key: dict[str, dict] = {}
    sheet_json = {"version": 2, "labels_names": ["P", "N", "G"], "labels_support": ["S", "G", "U"], "stories": []}
    md = [
        "# Blinded review of Study 2: 60 requirements (sheet version 2)\n",
        "1. Read `labelling_rules.md` first.\n"
        "2. Open this file in the Markdown preview. In the source view some characters have a backslash before them.\n"
        "3. The sheet has one section for each story. Read the story and its fact sheet. "
        "Open the evidence file of the story beside the sheet.\n"
        "4. This sheet is for part 2 of the review (the label S, G or U). Two AI raters use it. "
        "The human rater uses `names_sheet.md` (part 1).\n"
        "5. The sheet does not show which system made a requirement. Do not try to find out.\n"
        "6. Do not open the folder `key_do_not_open`, the folder `story_check`, the `results` folders, a `records.jsonl` file "
        "or the old `review` folder. Do not open `deviation_note_2026-11-14.md` before the labels are locked. "
        "Do not run a script that builds or checks the review sheet.\n"
        "7. The Keycloak excerpts are template source. `<@kc.CMD parameters=\"X\"/>` means the command `bin/kc.sh CMD X` "
        "(`bootstrapadmin` is `bootstrap-admin`). `${r\"TEXT\"}` means TEXT.\n",
    ]
    names_md = [
        "# Names in the 60 requirements (part 1 of the review)\n",
        "1. Read `names_rules.md` first.\n"
        "2. Open this file in the Markdown preview.\n"
        "3. Each requirement has a table of the code-like names in its text. A script found the names in the text of that requirement. "
        "The kind comes from the fact sheet and from AI classifiers. The column \"Did the search find it?\" comes from a text search.\n"
        "4. Give each requirement one letter, P, N or G, in `labels_names_george.csv` (columns: item, label, note).\n"
        "5. The file does not show which system made a requirement. Do not try to find out.\n"
        "6. Do not open the folder `key_do_not_open`, the folder `story_check`, the `results` folders, a `records.jsonl` file "
        "or the old `review` folder. Do not open `review_sheet.md`, `sheet.json` or the folder `evidence`: they belong to part 2. "
        "Do not open `deviation_note_2026-11-14.md` before your labels are locked. "
        "You can read `merge_rule.md`: it holds the steps and the commands of the day, and no result. "
        "Do not run a script that builds or checks the review sheet.\n",
    ]
    all_sids = sorted({sid for src in sources.values() for sid in src.stories})
    evidence_hashes = {}
    item_no = 0
    story_no = 0
    for sid in order + [s for s in all_sids if s not in order]:
        project = next(p for p, src in sources.items() if sid in src.stories)
        src = sources[project]
        story = src.stories[sid]
        corr = corrections.get(sid, [])
        items = by_story.get(sid, [])
        texts = [names.requirement_text(r["requirement"]) for r in items]  # scored items only
        head = f"# Evidence for {sid}: {md_safe(plain(story['title']))}\n\nThis file is the same for each requirement of this story.\n\n"
        ev_path = ev_dir / f"{sid}.md"
        ev_path.write_text(head + evidence_block(src, story, texts, md=True, classes=name_classes.get(sid)), encoding=UTF8)
        evidence_hashes[f"{sid}.md"] = sha256(ev_path)
        if not items:
            continue
        story_no += 1
        md.append(f"\n---\n\n## Story {story_no} of {len(order)}: {sid}. {md_safe(plain(story['title']))}\n")
        md.append(story_block(story, project, corr, md=True))
        md.append(f"**Evidence file:** [evidence/{sid}.md](evidence/{sid}.md) (documentation excerpts, the table of names, the system description, the source files)\n")
        entry = {
            "story_id": sid, "project": project, "title": plain(story["title"]),
            "names_story": (f"Story {sid}. {plain(story['title'])}\n\nProject: {project}\n\nStory: {plain(story['story'])}\n\nAcceptance criteria of the story:\n"
                            + "\n".join(f"{i}. {plain(a)}" for i, a in enumerate(story["acceptance_criteria"], start=1))),
            "story_block": story_block(story, project, corr, md=False),
            "evidence": evidence_block(src, story, texts, md=False, classes=name_classes.get(sid)),
            "source_files": [{"name": f, "text": "\n".join(src.docs[f])} for f in source_files(src, story)],
            "items": [],
        }
        names_md.append(f"\n---\n\n## Story {story_no} of {len(order)}: {sid}. {md_safe(plain(story['title']))}\n")
        names_md.append(f"**Project.** {project}\n\n**Story.** {md_safe(plain(story['story']))}\n\n**Acceptance criteria of the story.**\n\n"
                        + "\n".join(f"{i}. {md_safe(plain(a))}" for i, a in enumerate(story["acceptance_criteria"], start=1)) + "\n")
        for r in items:
            item_no += 1
            item = f"R{item_no:02d}"
            key[item] = {k: r[k] for k in ("project", "model", "arm", "run", "story_id", "req_index")}
            md.append(f"\n### {item}\n\n{fmt_requirement(r['requirement'], md=True)}")
            name_rows = item_names(src, story, names.requirement_text(r["requirement"]), corr, name_classes.get(sid))
            table = names_table(name_rows)
            names_md.append(f"\n### {item}\n\n{fmt_requirement(r['requirement'], md=True)}\n**Names in this requirement.**\n\n{table}")
            entry["items"].append({"item": item, "text": fmt_requirement(r["requirement"], md=False), "names_table": table,
                                   "names_rows": [{k: x[k] for k in ("name", "kind", "found", "given")} for x in name_rows]})
        sheet_json["stories"].append(entry)
    assert item_no == args.n

    sheet_path = OUT / "review_sheet.md"
    sheet_path.write_text("\n".join(md) + "\n", encoding=UTF8)
    names_path = OUT / "names_sheet.md"
    names_path.write_text("\n".join(names_md) + "\n", encoding=UTF8)
    json_path = OUT / "sheet.json"
    json_path.write_text(json.dumps(sheet_json, indent=1, ensure_ascii=False), encoding=UTF8)

    # Practice file. The practice items have no key: nobody scores them.
    pn = [
        "# Practice items for the names (not in the sample)\n",
        "Use these six items to test `names_rules.md` before you start. They are not scored. "
        "Write your six letters, each with one line of reason, in the file `practice_labels.txt`. "
        "Then open `names_practice_answers.md` and compare.\n",
    ]
    pm = [
        "# Practice items (not in the sample)\n",
        "These six items are in the form of part 2 of the review (the label S, G or U). No human rater labels part 2. "
        "The file is used to test the instruction text of the AI raters. The items are not scored.\n",
    ]
    for i, r in enumerate(practice, start=1):
        src = sources[r["project"]]
        story = src.stories[r["story_id"]]
        corr = corrections.get(story["id"], [])
        ev_name = f"practice_P{i}.md"
        head = f"# Evidence for practice item P{i} ({story['id']}: {md_safe(plain(story['title']))})\n\n"
        ev_path = ev_dir / ev_name
        ev_path.write_text(head + evidence_block(src, story, [names.requirement_text(r["requirement"])], md=True,
                                                 classes=name_classes.get(story["id"])), encoding=UTF8)
        evidence_hashes[ev_name] = sha256(ev_path)
        pm.append(f"\n---\n\n## Practice story {i}: {story['id']}. {md_safe(plain(story['title']))}\n")
        pm.append(story_block(story, r["project"], corr, md=True))
        pm.append(f"**Evidence file:** [evidence/{ev_name}](evidence/{ev_name})\n")
        pm.append(f"\n### P{i}\n\n{fmt_requirement(r['requirement'], md=True)}")
        ptable = names_table(item_names(src, story, names.requirement_text(r["requirement"]), corr, name_classes.get(story["id"])))
        pn.append(f"\n---\n\n## Practice story {i}: {story['id']}. {md_safe(plain(story['title']))}\n\n**Project.** {r['project']}\n\n"
                  f"**Story.** {md_safe(plain(story['story']))}\n\n**Acceptance criteria of the story.**\n\n"
                  + "\n".join(f"{k}. {md_safe(plain(a))}" for k, a in enumerate(story["acceptance_criteria"], start=1))
                  + f"\n\n### P{i}\n\n{fmt_requirement(r['requirement'], md=True)}\n"
                  f"**Names in this requirement.**\n\n{ptable}")
    (OUT / "names_practice.md").write_text("\n".join(pn) + "\n", encoding=UTF8)
    practice_path = OUT / "practice.md"
    practice_path.write_text("\n".join(pm) + "\n", encoding=UTF8)
    practice_labels = OUT / "practice_labels.txt"
    if not practice_labels.exists():
        practice_labels.write_text("".join(f"P{i}: \n" for i in range(1, len(practice) + 1)), encoding=UTF8)

    key_dir = OUT / "key_do_not_open"
    key_dir.mkdir(exist_ok=True)
    key_path = key_dir / "review_key.json"
    key_path.write_text(json.dumps(key, indent=1), encoding=UTF8)

    names_labels = OUT / "labels_names_george.csv"
    if not names_labels.exists():
        with names_labels.open("w", newline="", encoding=UTF8) as fh:
            w = csv.writer(fh)
            w.writerow(["item", "label", "note"])
            for item in key:
                w.writerow([item, "", ""])

    built = datetime.now(timezone.utc).isoformat(timespec="seconds")
    visible = {"review_sheet.md": sha256(sheet_path), "names_sheet.md": sha256(names_path), "sheet.json": sha256(json_path),
               "practice.md": sha256(practice_path), "names_practice.md": sha256(OUT / "names_practice.md")}
    for extra in ("ai_rater_prompt.md", "ai_rater_prompt_names.md", "ai_rater_procedure.md", "merge_rule.md", "names_rules.md",
                  "names_practice_answers.md"):
        if (OUT / extra).exists():
            visible[extra] = sha256(OUT / extra)
    manifest = {
        "sheet_version": 2,
        "n_items": len(key),
        "n_stories_with_items": len(order),
        "built_at_utc": built,
        "shown_fields": SHOWN_FIELDS,
        "hidden_fields": HIDDEN_FIELDS,
        "corrections_file_sha256": sha256(CORRECTIONS) if CORRECTIONS.exists() else None,
        "labelling_rules_sha256": sha256(RULES) if RULES.exists() else None,
        "name_classes_sha256": sha256(classes_file) if classes_file.exists() else None,
        "sha256": {
            **visible,
            "evidence": evidence_hashes,
            "builder": sha256(Path(__file__)),
            "names_module": sha256(HERE / "study2_names.py"),
            "sampler_first_builder": sha256(v1_path),
            # The tools that lock, score and check the review, and the tool that runs the AI raters.
            # Their hashes are recorded here so that the commit before the first label fixes them.
            **{label: sha256(path) for label, path in REVIEW_TOOLS.items() if path.is_file()},
        },
        "documentation_sha256": {p: docs_hash(s) for p, s in sources.items()},
        "system_description_sha256": {p: hashlib.sha256(s.system_text.encode(UTF8)).hexdigest() for p, s in sources.items()},
        "repository_head": {p: repo_head(s) for p, s in sources.items()},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding=UTF8)
    private = {
        "seed": args.seed,
        "built_at_utc": built,
        "items_by_project": dict(sorted(Counter(v["project"] for v in key.values()).items())),
        "items_by_arm": dict(sorted(Counter(v["arm"] for v in key.values()).items())),
        "items_by_model": dict(sorted(Counter(v["model"] for v in key.values()).items())),
        "review_key_sha256": sha256(key_path),
    }
    (key_dir / "manifest_private.json").write_text(json.dumps(private, indent=1), encoding=UTF8)
    print(f"review sheet v2: {len(key)} items in {len(order)} stories")
    print(f"  written to {OUT}")
    print(f"  corrections file used: {'yes' if corrections else 'no (not there yet)'}")


if __name__ == "__main__":
    main()
