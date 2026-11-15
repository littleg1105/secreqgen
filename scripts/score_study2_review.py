"""Scoring tool for the blinded review of Study 2 (version 2, two parts, 14 November 2026).

The review has two parts on the same 60 items (thesis/analysis_lock.md, section 7):
    Part 1, the names. Labels P, N, G. One human rater (the author) and two AI raters.
    Part 2, the support. Labels S, G, U. Two AI raters only. There is no human file in part 2.

Sub-commands (each takes --dir, default: real_test_cases/study2/review_v2):
    lock <file>   Check one label file, then record its SHA-256 and the UTC time in locks.json.
                  The human file is locked before any AI file (of part 1 or part 2).
    status        Show for each of the nine label files: missing, present and not locked, locked, or CHANGED.
    score         Needs all nine files locked and unchanged, the human lock first, no file with an old name,
                  and sheet.json equal to the hash in the visible manifest.json (which must hold no key hash).
                  Then it records the opening in locks.json, opens the key, checks the key against
                  review_key_sha256 in key_do_not_open/manifest_private.json, and writes
                  result/review_result.json and result/review_result.md (verdicts EE2 (a), EE2 (b)
                  and EE3 level 2: analysis_lock.md 11.3 and 11.5).
                  --again: a recorded escape. Without it, score refuses when a result exists or when
                  locks.json already shows an opening of the key. Each opening is written into locks.json.
    self-test     Runs lock, status and score through the command line on an invented key, an invented
                  sheet.json and invented labels in a temporary folder.

The nine label files (columns item,label,note; 60 rows R01 to R60 in the order of sheet.json):
    Part 1: labels_names_george.csv (human: P, N or G), labels_names_gemini_run1.csv,
            labels_names_gemini_run2.csv, labels_names_gpt_run1.csv, labels_names_gpt_run2.csv (P, N, G or X).
    Part 2: labels_support_gemini_run1.csv, labels_support_gemini_run2.csv,
            labels_support_gpt_run1.csv, labels_support_gpt_run2.csv (S, G, U or X).
X: the AI rater gave no valid label (analysis_lock.md 7.5). X is never a label.
The file names of the old design with one part (labels_george.csv, labels_gemini_run1.csv, ...) are refused.

Rules (fixed before the labels exist, see thesis/analysis_lock.md and merge_rule.md):
    - The human file is locked before any AI file is locked.
    - All nine files are locked before the key is opened. The key is opened one time.
    - For each AI rater, run 1 is the label of record. Run 2 is used only for self-agreement.
    - An item with X for a rater is left out of each statistic that uses that rater.
    - All statistics come from thesis/numbers/stats.py (loaded by path, not edited).
    - review_result.md rounds as analysis_lock.md 3.5. review_result.json keeps full precision.

No sub-command prints a label, a label count, an arm, a model or a rate, except self-test (invented data only).
Python standard library and thesis/numbers/stats.py only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # do not write __pycache__ next to stats.py

THESIS = Path(__file__).resolve().parents[3]
STATS_PATH = THESIS / "thesis" / "numbers" / "stats.py"
DEFAULT_DIR = THESIS / "coded_project" / "real_test_cases" / "study2" / "review_v2"

NAMES_LABELS = ("P", "N", "G")  # part 1
SUPPORT_LABELS = ("S", "G", "U")  # part 2
MISSING = "X"  # an AI answer with no valid label for an item (analysis_lock.md 7.5). X is never a label.
ARMS = ("A", "B", "C", "D")
DOC_ARMS = ("B", "C", "D")

HUMAN = "labels_names_george.csv"
NAMES_AI = ("labels_names_gemini_run1.csv", "labels_names_gemini_run2.csv",
            "labels_names_gpt_run1.csv", "labels_names_gpt_run2.csv")
SUPPORT_AI = ("labels_support_gemini_run1.csv", "labels_support_gemini_run2.csv",
              "labels_support_gpt_run1.csv", "labels_support_gpt_run2.csv")
AI_FILES = NAMES_AI + SUPPORT_AI
EXPECTED = (HUMAN,) + AI_FILES
# File names of the old design with one part. They are no longer valid.
OLD_NAMES = ("labels_george.csv", "labels_gemini_run1.csv", "labels_gemini_run2.csv",
             "labels_gpt_run1.csv", "labels_gpt_run2.csv")
# (part, rater, run) for each file. Run 1 of an AI rater is its label of record.
FILES = {
    HUMAN: (1, "human", 1),
    "labels_names_gemini_run1.csv": (1, "gemini", 1), "labels_names_gemini_run2.csv": (1, "gemini", 2),
    "labels_names_gpt_run1.csv": (1, "gpt", 1), "labels_names_gpt_run2.csv": (1, "gpt", 2),
    "labels_support_gemini_run1.csv": (2, "gemini", 1), "labels_support_gemini_run2.csv": (2, "gemini", 2),
    "labels_support_gpt_run1.csv": (2, "gpt", 1), "labels_support_gpt_run2.csv": (2, "gpt", 2),
}
PAIRS_PART1 = (("human", "gemini"), ("human", "gpt"), ("gemini", "gpt"))
AI_RATERS = ("gemini", "gpt")
PRETTY = {"human": "Human", "gemini": "Gemini", "gpt": "GPT"}
SEED = 20261114
RESAMPLES = 10000
N_ITEMS = 60
PRIVATE_MANIFEST = "manifest_private.json"  # in key_do_not_open; holds review_key_sha256
NO_HUMAN = "no human expert checked these labels"
# The only sentences of part 2 in review_result.md that do not hold NO_HUMAN (analysis_lock.md 7.12):
# they state a method or a rule, not a result. The self-test checks each other sentence of part 2.
PART2_METHOD_SENTENCES = (
    "Each table in this part comes from AI labels only.",
    "Each rate uses the valid items of that rater in the group.",
    "\"Left out\" counts the other items of the group.",
    "Fisher's exact test, two-sided, for each AI rater.",
    "It compares arm A with arms B, C and D together.",
    "The p-values are not corrected for several tests.",
    "The rule (analysis lock, section 11, point ",
    "In each other case it is \"",
    "This verdict uses AI labels only.",
)


def file_of(part: int, rater: str, run: int) -> str:
    return next(f for f, v in FILES.items() if v == (part, rater, run))


class Refuse(Exception):
    """A plain-English reason why a command stops. Nothing is changed when it is raised."""


def load_stats():
    if not STATS_PATH.is_file():
        raise Refuse(f"The statistics file is missing: {STATS_PATH}")
    spec = importlib.util.spec_from_file_location("study2_stats", STATS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def old_name_message(name: str) -> str:
    return (
        f"'{name}' is a file name of the old design with one part. It is not valid now. "
        f"The review has two parts. Part 1 (the names, P, N or G): {HUMAN}, " + ", ".join(NAMES_AI) + ". "
        "Part 2 (the support, S, G or U): " + ", ".join(SUPPORT_AI) + ". "
        "Save the labels in the file with the new name, and use that name in the command."
    )


# ---------------------------------------------------------------------------
# locks.json
# ---------------------------------------------------------------------------
def locks_path(d: Path) -> Path:
    return d / "locks.json"


def read_locks(d: Path) -> dict:
    p = locks_path(d)
    if not p.exists():
        return {"version": 2, "locks": {}}
    try:
        data = json.loads(p.read_text())
        if not isinstance(data.get("locks"), dict):
            raise ValueError("no 'locks' object")
    except (ValueError, OSError) as e:
        raise Refuse(f"locks.json cannot be read ({e}). Do not edit it by hand. Stop and ask for help.")
    return data


def foreign_locks(locks: dict) -> list[str]:
    """Lock entries for a file name that is not one of the nine files (for example a lock of the old design)."""
    return sorted(n for n in locks["locks"] if n not in EXPECTED)


def refuse_foreign_locks(locks: dict) -> None:
    bad = foreign_locks(locks)
    if bad:
        raise Refuse(
            "locks.json holds a lock of a file that is not one of the nine label files of the two-part design ("
            + ", ".join(bad) + "). This can be a lock of the old design. Nothing was changed. Stop and ask for help."
        )


def write_locks(d: Path, data: dict) -> None:
    p = locks_path(d)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, p)


def lock_state(d: Path, locks: dict, name: str) -> str:
    """One of: missing, unlocked, locked_ok, changed, locked_missing."""
    f = d / name
    entry = locks["locks"].get(name)
    if entry is None:
        return "unlocked" if f.is_file() else "missing"
    if not f.is_file():
        return "locked_missing"
    return "locked_ok" if sha256_file(f) == entry["sha256"] else "changed"


# ---------------------------------------------------------------------------
# sheet and label files
# ---------------------------------------------------------------------------
def load_sheet(d: Path) -> tuple[list[str], dict[str, list[dict]]]:
    """The 60 item ids in the order of sheet.json, and the names_rows of each item."""
    p = d / "sheet.json"
    if not p.is_file():
        raise Refuse(f"sheet.json is missing in {d}")
    try:
        sheet = json.loads(p.read_text(encoding="utf-8"))
        ids: list[str] = []
        rows: dict[str, list[dict]] = {}
        for st in sheet["stories"]:
            for it in st["items"]:
                ids.append(it["item"])
                nr = it["names_rows"]
                if not isinstance(nr, list):
                    raise TypeError(f"names_rows of {it['item']} is not a list")
                for r in nr:
                    if not (isinstance(r["kind"], str) and isinstance(r["found"], bool) and isinstance(r["given"], bool)):
                        raise TypeError(f"a row of names_rows of {it['item']} has no valid kind, found or given")
                rows[it["item"]] = nr
    except KeyError as e:
        raise Refuse(f"sheet.json has an unexpected form (the field {e} is missing). "
                     "Each item needs item and names_rows, and each row of names_rows needs kind, found and given. "
                     "Nothing was locked and the key stays closed.")
    except (ValueError, TypeError) as e:
        raise Refuse(f"sheet.json has an unexpected form ({e}). Nothing was locked and the key stays closed.")
    if len(ids) != N_ITEMS or len(set(ids)) != N_ITEMS:
        raise Refuse(f"sheet.json must hold {N_ITEMS} different items. It holds {len(ids)}.")
    return ids, rows


def allowed_labels(name: str) -> tuple[str, ...]:
    """Human file: P, N or G. Part 1 AI file: P, N, G or X. Part 2 AI file: S, G, U or X."""
    part, rater, _ = FILES[name]
    base = NAMES_LABELS if part == 1 else SUPPORT_LABELS
    return base if rater == "human" else base + (MISSING,)


def letters_text(allowed: tuple[str, ...]) -> str:
    return ", ".join(allowed[:-1]) + " or " + allowed[-1]


def parse_label_file(path: Path, ids: list[str]) -> list[tuple[str, str, str]]:
    """Check a label file and return (item, label, note) rows. Raise Refuse with a plain reason."""
    name = path.name
    allowed = allowed_labels(name)
    part = FILES[name][0]
    is_human = name == HUMAN
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refuse(f"{name}: the file is not UTF-8 text. Save it again as CSV (UTF-8).")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise Refuse(f"{name}: the file is empty.")
    header = [h.strip() for h in rows[0]]
    if header != ["item", "label", "note"]:
        raise Refuse(f"{name}: the first line must be exactly: item,label,note")
    body = []
    for lineno, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # a fully empty line (for example at the end of the file)
        if len(row) != 3:
            raise Refuse(
                f"{name}: line {lineno} has {len(row)} fields. It must have 3. "
                "Put a note that holds a comma inside double quotes."
            )
        body.append((lineno, row[0].strip(), row[1], row[2]))
    if len(body) != N_ITEMS:
        raise Refuse(f"{name}: the file must have {N_ITEMS} rows of items. It has {len(body)}.")
    out = []
    for (lineno, item, label, note), expected in zip(body, ids):
        if item != expected:
            raise Refuse(f"{name}: line {lineno} holds item {item or '(empty)'}. The sheet expects {expected} here.")
        if label.strip() == "":
            hint = " (X: no valid label)" if not is_human else ""
            raise Refuse(f"{name}: item {item} has no label. Every item needs {letters_text(allowed)}{hint}.")
        if label not in allowed:
            # Exact match only: no lower case, no spaces. The file is locked as it is, so it must hold exactly one letter.
            if is_human and label.strip().upper() == MISSING:
                raise Refuse(
                    f"{name}: item {item} has X. X (missing) is allowed only in an AI file. "
                    "The human file needs exactly P, N or G for each item."
                )
            other = SUPPORT_LABELS if part == 1 else NAMES_LABELS
            hint = ""
            if label in other:
                hint = (" S and U are labels of part 2 (the support), not of part 1 (the names)." if part == 1
                        else " P and N are labels of part 1 (the names), not of part 2 (the support).")
            raise Refuse(
                f"{name}: item {item} has a label that is not exactly {letters_text(allowed)} "
                f"(one capital letter, no space before or after it).{hint} Correct the file before the lock."
            )
        out.append((item, label, note))
    return out


def read_labels(d: Path, name: str, ids: list[str]) -> dict[str, str]:
    return {item: label for item, label, _ in parse_label_file(d / name, ids)}


# ---------------------------------------------------------------------------
# sub-commands: lock and status
# ---------------------------------------------------------------------------
def cmd_lock(args) -> None:
    d = Path(args.dir)
    name = args.file
    if name in OLD_NAMES:
        raise Refuse(old_name_message(name))
    if name not in EXPECTED:
        raise Refuse(f"'{name}' is not a known label file. Use one of: " + ", ".join(EXPECTED))
    locks = read_locks(d)
    refuse_foreign_locks(locks)
    if locks.get("key_opened_at_utc") or locks.get("key_openings"):
        raise Refuse("The key was already opened. No file can be locked now.")
    if name in locks["locks"]:
        print(f"already locked: {name} (nothing changed)")
        raise SystemExit(1)
    if not (d / name).is_file():
        raise Refuse(f"{name} does not exist in {d}")
    if name != HUMAN:
        st = lock_state(d, locks, HUMAN)
        if st in ("changed", "locked_missing"):
            raise Refuse(f"{HUMAN} changed after its lock. Do not lock an AI file. Restore the file first.")
        if st != "locked_ok":
            raise Refuse(f"{HUMAN} is not locked. Lock the human file first: score_study2_review.py lock {HUMAN}")
    ids, _ = load_sheet(d)
    rows = parse_label_file(d / name, ids)
    digest = sha256_file(d / name)
    stamp = now_utc()
    locks["locks"][name] = {"file": name, "part": FILES[name][0], "sha256": digest, "locked_at_utc": stamp, "rows": len(rows)}
    write_locks(d, locks)
    print(f"locked: {name}\nsha256: {digest}\nlocked at (UTC): {stamp}")


STATE_TEXT = {
    "missing": "missing",
    "unlocked": "present and not locked",
    "locked_ok": "locked (hash check ok)",
    "changed": "CHANGED AFTER LOCK",
    "locked_missing": "CHANGED AFTER LOCK (the file is gone)",
}


def cmd_status(args) -> None:
    d = Path(args.dir)
    locks = read_locks(d)
    for name in EXPECTED:
        print(f"{name}: {STATE_TEXT[lock_state(d, locks, name)]}")
    for name in OLD_NAMES:
        if (d / name).exists():
            print(f"{name}: OLD FILE NAME, not valid in the two-part design (score refuses while this file is in the folder)")
    bad = foreign_locks(locks)
    if bad:
        print("locks.json: holds a lock of a file that is not one of the nine label files (" + ", ".join(bad) + "). Stop and ask for help.")


# ---------------------------------------------------------------------------
# computation
# ---------------------------------------------------------------------------
GROUPS = [("A", ("A",)), ("B", ("B",)), ("C", ("C",)), ("D", ("D",)),
          ("With documents (B+C+D)", DOC_ARMS), ("All arms", ARMS)]
DOCS = "With documents (B+C+D)"


def model_family(model: str) -> str:
    m = model.lower()
    if "gemma" in m:
        return "Gemma"
    if "qwen" in m:
        return "Qwen"
    return model


def prop_row(st, group: str, labels: list[str], cats: tuple[str, ...]) -> dict:
    n = len(labels)
    c = Counter(labels)
    row = {"group": group, "n": n, "counts": {}, "proportions": {}, "ci95": {}}
    for lab in cats:
        k = c[lab]
        row["counts"][lab] = k
        row["proportions"][lab] = (k / n) if n else None
        # A group with no item has no rate, so its interval is "not defined" (None), not [0, 1].
        row["ci95"][lab] = list(st.clopper_pearson(k, n)) if n else None
    return row


def table_rows(st, items, labels: dict, key: dict, subset, cats) -> list[dict]:
    """Rows for each arm, for the documents group and for all arms.
    An item whose label is not one of `cats` (X, or no majority) is left out and counted in n_left_out."""
    rows = []
    for gname, arms in GROUPS:
        group = [i for i in items if subset(key[i]) and key[i]["arm"] in arms]
        sel = [labels[i] for i in group if labels[i] in cats]
        r = prop_row(st, gname, sel, cats)
        r["n_left_out"] = len(group) - len(sel)
        rows.append(r)
    return rows


def views(st, items, labels, key, cats, with_projects: bool) -> dict:
    families = sorted({model_family(key[i]["model"]) for i in items})
    out = {
        "all": table_rows(st, items, labels, key, lambda k: True, cats),
        "by_model": {f: table_rows(st, items, labels, key, lambda k, f=f: model_family(k["model"]) == f, cats) for f in families},
    }
    if with_projects:
        projects = sorted({key[i]["project"] for i in items})
        out["by_project"] = {p: table_rows(st, items, labels, key, lambda k, p=p: k["project"] == p, cats) for p in projects}
    return out


def valid_pair_lists(items, x, y, cats) -> tuple[list[str], list[str], int]:
    """The two label lists on the items where both labels are valid, and the number of items left out (X)."""
    keep = [i for i in items if x[i] in cats and y[i] in cats]
    return [x[i] for i in keep], [y[i] for i in keep], len(items) - len(keep)


def majority_of_record(st, labels: list[str], cats=NAMES_LABELS) -> str | None:
    """analysis_lock.md 7.5 and 7.7: stats.majority_label on the valid labels, and at least 2 raters give it.
    P, X, P -> P.  P, X, G -> None.  P, X, X -> None.  P, N, G -> None."""
    v = [x for x in labels if x in cats]
    if not v:
        return None
    m = st.majority_label(v)
    return m if m is not None and v.count(m) >= 2 else None


def pair_stats(st, a: list[str], b: list[str], cats, n_left_out: int = 0) -> dict:
    """Raw agreement, kappa, bootstrap interval (with the count of resamples with no kappa), band, 3x3 table.
    `a` and `b` hold only the items where both labels are valid; `n_left_out` counts the items with X."""
    if not a:
        return {
            "n": 0, "n_left_out_missing": n_left_out, "agreement": None, "agreement_count": 0, "kappa": None,
            "kappa_ci95": None, "bootstrap_resamples": RESAMPLES, "bootstrap_resamples_no_kappa": RESAMPLES,
            "band": st.landis_koch(None), "table_rows_first_rater_cols_second": {x: {y: 0 for y in cats} for x in cats},
        }
    k = st.cohen_kappa(a, b)
    bs = st.kappa_bootstrap(a, b, resamples=RESAMPLES, seed=SEED)
    table = {x: {y: sum(1 for p, q in zip(a, b) if p == x and q == y) for y in cats} for x in cats}
    return {
        "n": k["n"],
        "n_left_out_missing": n_left_out,
        "agreement": k["agreement"],
        "agreement_count": sum(1 for p, q in zip(a, b) if p == q),
        "kappa": k["kappa"],
        "kappa_ci95": None if bs["lo"] is None else [bs["lo"], bs["hi"]],
        "bootstrap_resamples": bs["n_resamples"],
        "bootstrap_resamples_no_kappa": bs["n_skipped"],
        "band": st.landis_koch(k["kappa"]),
        "table_rows_first_rater_cols_second": table,
    }


def script_label(names_rows: list[dict]) -> str:
    """analysis_lock.md 7.8: the letter of the names rules when step 2 removes no row.
    Rows with kind "product" and given false. None: G. One or more with found false: N. Else P."""
    keep = [r for r in names_rows if r["kind"] == "product" and r["given"] is False]
    if not keep:
        return "G"
    if any(r["found"] is False for r in keep):
        return "N"
    return "P"


def per_name_rows(items, key, names_rows: dict) -> list[dict]:
    """analysis_lock.md 7.8, second sentence (a description): for each group, the rows with kind "product"
    and given false, and the share of these rows that the search did not find (a rate for each name)."""
    out = []
    for gname, arms in GROUPS:
        rr = [r for i in items if key[i]["arm"] in arms for r in names_rows[i] if r["kind"] == "product" and r["given"] is False]
        nf = sum(1 for r in rr if r["found"] is False)
        out.append({"group": gname, "n_rows": len(rr), "n_not_found": nf, "share_not_found": (nf / len(rr)) if rr else None})
    return out


def group_row(rows: list[dict], name: str) -> dict:
    return next(r for r in rows if r["group"] == name)


def fisher_block(st, rows: list[dict], lab: str) -> dict:
    """analysis_lock.md 7.9: a, b = this label and the other labels with documents; c, d = the same in arm A."""
    a = group_row(rows, "A")
    docs = group_row(rows, DOCS)
    out = {
        "label": lab,
        "arm_A": {"n": a["n"], "count": a["counts"][lab], "rate": a["proportions"][lab]},
        "with_documents": {"n": docs["n"], "count": docs["counts"][lab], "rate": docs["proportions"][lab]},
        "note": "Fisher exact, two-sided. a, b = this label and the other labels with documents; c, d = the same in arm A.",
    }
    if a["n"] == 0 or docs["n"] == 0:
        out["p"] = None
    else:
        out["p"] = st.fisher_exact_2x2(
            docs["counts"][lab], docs["n"] - docs["counts"][lab], a["counts"][lab], a["n"] - a["counts"][lab]
        )
    return out


def lower(docs_rate, a_rate) -> bool:
    return docs_rate is not None and a_rate is not None and docs_rate < a_rate


def higher(docs_rate, a_rate) -> bool:
    return docs_rate is not None and a_rate is not None and docs_rate > a_rate


def p_below(p) -> bool:
    return p is not None and p < 0.05


def label_x_counts(items, lab: dict, part: int) -> dict:
    return {file_of(part, r, run): sum(1 for i in items if lab[(r, run)][i] == MISSING)
            for r in AI_RATERS for run in (1, 2)}


def compute_part1(st, items, key, lab: dict, names_rows: dict) -> dict:
    """lab: {("human", 1): {...}, ("gemini", 1): ..., ("gemini", 2): ..., ("gpt", 1): ..., ("gpt", 2): ...}."""
    cats = NAMES_LABELS
    human = lab[("human", 1)]
    rec = {"human": human, "gemini": lab[("gemini", 1)], "gpt": lab[("gpt", 1)]}
    primary = views(st, items, human, key, cats, with_projects=True)
    ai = {}
    for r in AI_RATERS:
        ai[r] = {"file": file_of(1, r, 1), "status": "descriptive",
                 "n_valid": sum(1 for i in items if rec[r][i] in cats),
                 "n_missing_X": sum(1 for i in items if rec[r][i] == MISSING),
                 "views": views(st, items, rec[r], key, cats, with_projects=True)}
    pairs = []
    for x, y in PAIRS_PART1:
        a, b, left = valid_pair_lists(items, rec[x], rec[y], cats)
        s = pair_stats(st, a, b, cats, left)
        s["pair"] = [x, y]
        pairs.append(s)
    three = [[rec[r][i] for r in ("human", "gemini", "gpt")] for i in items]
    three = [row for row in three if all(v in cats for v in row)]
    fk = st.fleiss_kappa(three) if three else None
    self_ag = {}
    for r in AI_RATERS:
        a, b, left = valid_pair_lists(items, lab[(r, 1)], lab[(r, 2)], cats)
        self_ag[r] = pair_stats(st, a, b, cats, left)
    maj = {i: majority_of_record(st, [rec[r][i] for r in ("human", "gemini", "gpt")], cats) for i in items}
    no_maj = [i for i in items if maj[i] is None]
    majority = views(st, items, maj, key, cats, with_projects=False)
    majority.update({
        "n_with_majority": len(items) - len(no_maj),
        "n_no_majority": len(no_maj),
        "n_no_majority_with_missing_X": sum(1 for i in no_maj if any(rec[r][i] == MISSING for r in AI_RATERS)),
        "no_majority_items": no_maj,
    })
    script = {i: script_label(names_rows[i]) for i in items}
    same = sum(1 for i in items if script[i] == human[i])
    fisher = {"N": fisher_block(st, primary["all"], "N"), "P": fisher_block(st, primary["all"], "P")}
    return {
        "raters": "one human rater and two AI raters",
        "labels": list(cats),
        "primary_human": primary,
        "ai_raters_descriptive": ai,
        "missing_labels_X": label_x_counts(items, lab, 1),
        "agreement": {
            "pairs_of_record": pairs,
            "fleiss_kappa_three_raters": {"kappa": fk, "band": st.landis_koch(fk), "n": len(three),
                                          "n_left_out_missing": len(items) - len(three)},
            "self_agreement_run1_vs_run2": self_ag,
            "bootstrap": {"resamples": RESAMPLES, "seed": SEED, "method": "percentile, items resampled"},
        },
        "majority": majority,
        "script_label": {
            "status": "description",
            "rule": "rows with kind 'product' and given false; none: G; one or more with found false: N; else P",
            "by_item": script,
            "rows": table_rows(st, items, script, key, lambda k: True, cats),
            "agreement_with_human": {"n": len(items), "same": same, "agreement": same / len(items)},
            "per_name_rows": per_name_rows(items, key, names_rows),
        },
        "fisher_tests_human_labels": fisher,
    }


def compute_part2(st, items, key, lab: dict) -> dict:
    """lab: {("gemini", 1): ..., ("gemini", 2): ..., ("gpt", 1): ..., ("gpt", 2): ...}. No human labels."""
    cats = SUPPORT_LABELS
    rec = {r: lab[(r, 1)] for r in AI_RATERS}
    ai = {}
    for r in AI_RATERS:
        ai[r] = {"file": file_of(2, r, 1),
                 "n_valid": sum(1 for i in items if rec[r][i] in cats),
                 "n_missing_X": sum(1 for i in items if rec[r][i] == MISSING),
                 "views": views(st, items, rec[r], key, cats, with_projects=False)}
    a, b, left = valid_pair_lists(items, rec["gemini"], rec["gpt"], cats)
    pair = pair_stats(st, a, b, cats, left)
    pair["pair"] = ["gemini", "gpt"]
    self_ag = {}
    for r in AI_RATERS:
        x, y, lo = valid_pair_lists(items, lab[(r, 1)], lab[(r, 2)], cats)
        self_ag[r] = pair_stats(st, x, y, cats, lo)
    both = {i: (rec["gemini"][i] if rec["gemini"][i] == rec["gpt"][i] and rec["gemini"][i] in cats else None) for i in items}
    fisher = {r: {"U": fisher_block(st, ai[r]["views"]["all"], "U"), "S": fisher_block(st, ai[r]["views"]["all"], "S")}
              for r in AI_RATERS}
    return {
        "raters": "two AI raters and no human rater",
        "note": "No human expert checked these labels.",
        "labels": list(cats),
        "ai_raters": ai,
        "missing_labels_X": label_x_counts(items, lab, 2),
        "agreement": {
            "pair_of_record": pair,
            "self_agreement_run1_vs_run2": self_ag,
            "bootstrap": {"resamples": RESAMPLES, "seed": SEED, "method": "percentile, items resampled"},
        },
        "both_agree": {"n": sum(1 for i in items if both[i] is not None),
                       "rows": table_rows(st, items, both, key, lambda k: True, cats)},
        "fisher_tests": fisher,
    }


def verdict_ee2_a(p1: dict) -> dict:
    """analysis_lock.md 11.3 (a)."""
    f = p1["fisher_tests_human_labels"]["N"]
    ma = group_row(p1["majority"]["all"], "A")["proportions"]["N"]
    md = group_row(p1["majority"]["all"], DOCS)["proportions"]["N"]
    c = [
        {"condition": "The N rate with documents is lower than in arm A (human labels)",
         "arm_A": f["arm_A"]["rate"], "with_documents": f["with_documents"]["rate"],
         "holds": lower(f["with_documents"]["rate"], f["arm_A"]["rate"])},
        {"condition": "The Fisher p for N (human labels) is below 0.05", "p": f["p"], "holds": p_below(f["p"])},
        {"condition": "The N rate with documents is lower than in arm A also under the majority labels (items with no majority left out)",
         "arm_A": ma, "with_documents": md, "holds": lower(md, ma)},
    ]
    return {
        "question": "EE2 (a), invented names (part 1)",
        "rule": "shown needs all three conditions; otherwise not shown",
        "conditions": c,
        "verdict": "shown" if all(x["holds"] for x in c) else "not shown",
    }


def verdict_ai(p2: dict, lab: str, question: str, good: str, bad: str, direction) -> dict:
    """analysis_lock.md 11.3 (b) (lab U, lower) and 11.5 (lab S, higher): each of the two AI raters must pass."""
    word = "lower" if direction is lower else "higher"
    conds, raters = [], {}
    for r in AI_RATERS:
        f = p2["fisher_tests"][r][lab]
        c1 = direction(f["with_documents"]["rate"], f["arm_A"]["rate"])
        c2 = p_below(f["p"])
        conds.append({"condition": f"{PRETTY[r]}: the {lab} rate with documents is {word} than in arm A",
                      "arm_A": f["arm_A"]["rate"], "with_documents": f["with_documents"]["rate"], "holds": c1})
        conds.append({"condition": f"{PRETTY[r]}: the Fisher p for {lab} is below 0.05", "p": f["p"], "holds": c2})
        raters[r] = {"passes": c1 and c2}
    return {
        "question": question,
        "rule": f"{good} needs, for each of the two AI raters, a {word} {lab} rate with documents than in arm A "
                f"and p below 0.05 in its Fisher test for {lab}; otherwise {bad}",
        "note": "No human expert checked these labels.",
        "conditions": conds,
        "raters": raters,
        "verdict": good if all(x["holds"] for x in conds) else bad,
    }


def compute(st, items, key, lab1: dict, lab2: dict, names_rows: dict) -> dict:
    p1 = compute_part1(st, items, key, lab1, names_rows)
    p2 = compute_part2(st, items, key, lab2)
    return {
        "n_items": len(items),
        "part1_names": p1,
        "part2_support": p2,
        "verdicts": {
            "EE2_a": verdict_ee2_a(p1),
            "EE2_b": verdict_ai(p2, "U", "EE2 (b), unsupported or wrong statements (part 2, AI raters only)",
                                "shown by two AI raters", "not shown", lower),
            "EE3_level2": verdict_ai(p2, "S", "EE3 level 2, correct project-specific requirements (part 2, AI raters only)",
                                     "shown by two AI raters", "not shown for correctness", higher),
        },
        "registered_validation_by_two_human_reviewers": "not delivered",
    }


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------
# Rounding for the display only (analysis_lock.md 3.5). review_result.json keeps full precision.
NOT_DEFINED = "not defined"


def pct(x) -> str:
    """A rate or an interval limit: percent with one decimal."""
    return NOT_DEFINED if x is None else f"{100 * x:.1f}%"


def pct_ci(ci) -> str:
    return NOT_DEFINED if ci is None else f"[{pct(ci[0])}, {pct(ci[1])}]"


def kap(x) -> str:
    """Kappa: two decimals. A value that rounds to zero shows as 0.00, not -0.00."""
    if x is None:
        return NOT_DEFINED
    s = f"{x:.2f}"
    return "0.00" if s == "-0.00" else s


def kap_ci(ci) -> str:
    return NOT_DEFINED if ci is None else f"[{kap(ci[0])}, {kap(ci[1])}]"


def p_txt(p) -> str:
    """p: four decimals, and "p < 0.0001" below that."""
    if p is None:
        return NOT_DEFINED
    return "p < 0.0001" if p < 0.0001 else f"p = {p:.4f}"


def yes_no(b: bool) -> str:
    return "yes" if b else "no"


def rate_cell(r: dict, lab: str) -> str:
    """Rate and interval. When the group has no item, both are "not defined"."""
    p = r["proportions"][lab]
    return NOT_DEFINED if p is None else f"{pct(p)} {pct_ci(r['ci95'][lab])}"


def prop_table(rows: list[dict], cats, left_out: bool) -> str:
    head = "| Group | n |" + "".join(f" {c} | {c} rate (95% CI) |" for c in cats)
    rule = "|---|---|" + "---|---|" * len(cats)
    if left_out:
        head, rule = head + " Left out |", rule + "---|"
    out = [head, rule]
    for r in rows:
        cells = [r["group"], str(r["n"])]
        for lab in cats:
            cells += [str(r["counts"][lab]), rate_cell(r, lab)]
        if left_out:
            cells.append(str(r["n_left_out"]))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def views_md(title: str, v: dict, cats, left_out: bool) -> list[str]:
    out = [f"#### {title}: all generators", "", prop_table(v["all"], cats, left_out), ""]
    for f, rows in v["by_model"].items():
        out += [f"#### {title}: generator {f}", "", prop_table(rows, cats, left_out), ""]
    for p, rows in v.get("by_project", {}).items():
        out += [f"#### {title}: project {p} (description only)", "", prop_table(rows, cats, left_out), ""]
    return out


AGREE_HEAD = ["| {first} | n | Left out (X) | Same label | Raw agreement | Kappa | Kappa 95% CI | Resamples with no kappa | Band |",
              "|---|---|---|---|---|---|---|---|---|"]


def agree_row(label: str, s: dict) -> str:
    return (f"| {label} | {s['n']} | {s['n_left_out_missing']} | {s['agreement_count']} | {pct(s['agreement'])} | "
            f"{kap(s['kappa'])} | {kap_ci(s['kappa_ci95'])} | {s['bootstrap_resamples_no_kappa']} | {s['band']} |")


def agree_table(first: str, rows: list[tuple[str, dict]]) -> list[str]:
    return [AGREE_HEAD[0].format(first=first), AGREE_HEAD[1]] + [agree_row(lab, s) for lab, s in rows]


def cross_table(title: str, s: dict, cats) -> list[str]:
    t = s["table_rows_first_rater_cols_second"]
    out = [title, "", "| |" + "".join(f" {c} |" for c in cats), "|---|" + "---|" * len(cats)]
    for a in cats:
        out.append(f"| {a} | " + " | ".join(str(t[a][b]) for b in cats) + " |")
    return out + [""]


def fisher_row(first: str, b: dict) -> str:
    return (f"| {first} | {b['arm_A']['count']} / {b['arm_A']['n']} | {pct(b['arm_A']['rate'])} | "
            f"{b['with_documents']['count']} / {b['with_documents']['n']} | {pct(b['with_documents']['rate'])} | {p_txt(b['p'])} |")


def condition_rows(v: dict) -> list[str]:
    out = ["| Condition | Values | Holds |", "|---|---|---|"]
    for c in v["conditions"]:
        if "p" in c:
            vals = p_txt(c["p"])
        else:
            vals = f"A {pct(c['arm_A'])}, with documents {pct(c['with_documents'])}"
        out.append(f"| {c['condition']} | {vals} | {yes_no(c['holds'])} |")
    return out


def make_markdown(res: dict, hashes: dict, date: str) -> str:
    p1, p2, vd = res["part1_names"], res["part2_support"], res["verdicts"]
    parts = ", ".join(f"{n}={hashes[n]}" for n in EXPECTED)
    L = [f"Date: {date} (UTC, Coordinated Universal Time). SHA-256 (a file fingerprint) of the label files: {parts}. "
         f"SHA-256 of the key: {hashes['key']}.", ""]
    L += [
        "# Result of the blinded review of Study 2",
        "",
        "The scoring tool score_study2_review.py wrote this file after it opened the key. It holds tables and verdicts only.",
        "The review has two parts on the same 60 generated requirements (items). "
        "Part 1 (the names) has one human rater and two AI raters. Part 2 (the support) has two AI raters and no human rater.",
        "The registered validation by two human reviewers is not delivered.",
        "AI means artificial intelligence. The two AI raters are Gemini and GPT. For each AI rater, run 1 gives its labels of record. "
        "Run 2 gives only the agreement of that rater with itself (self-agreement).",
        "A is the arm with no documents. B is retrieved passages. C is passages plus extracted tokens. D is the whole corpus. "
        "\"With documents\" means arms B, C and D together.",
        "Part 1 labels (after the five steps of the names rules): P means that one or more names of the product are left, "
        "and the search found each of them. N means that the search did not find one or more of the names that are left. "
        "G means that no name of the product is left. A name is left when it is not in the story or the system description "
        "and step 2 of the rules did not remove it. \"Not found\" means that the local documentation folder and the local "
        "repository do not contain the name. It does not prove that the name is invented.",
        "Part 2 labels: S means specific and supported. G means generic and correct. U means unsupported or wrong.",
        "X means missing: an AI rater gave no valid label for the item. X is never a label. "
        "An item with X for a rater is left out of each statistic that uses that rater. \"Left out\" counts these items.",
        "n is the number of items that a row uses. CI means confidence interval. "
        "Each interval of a rate is an exact Clopper-Pearson 95% interval.",
        "Rounding (analysis lock, section 3, point 5): rates and interval limits in percent with one decimal, "
        "p-values with four decimals (\"p < 0.0001\" below that), kappa with two decimals. "
        "\"not defined\" means that the value does not exist. review_result.json keeps the full values, "
        "and each verdict uses the full values.",
        "The 60 items were not sized for the tests, so the tests have low power. "
        "A verdict \"not shown\" is not proof that there is no difference.",
        "",
        "## Part 1. The names (one human rater and two AI raters)",
        "",
        "### 1.1 Primary result: human labels",
        "",
        "Each row shows the count and the share of P, N and G in one group of items, under the labels of the human rater.",
        "",
    ]
    L += views_md("Human labels", p1["primary_human"], NAMES_LABELS, left_out=False)
    mx = p1["missing_labels_X"]
    ag = p1["agreement"]
    L += [
        "### 1.2 Agreement between the raters",
        "",
        "Missing labels (X) in each AI file of part 1: " + ", ".join(f"{f} {mx[f]}" for f in NAMES_AI) + ".",
        "",
        "Raters of record: the human, Gemini run 1 and GPT run 1. Kappa is Cohen's kappa without weights. "
        f"The interval is a percentile bootstrap with {RESAMPLES} resamples and seed {SEED}. "
        "The band is the Landis and Koch band. \"Resamples with no kappa\" counts the resamples that the interval leaves out. "
        "Each pair uses the items where both raters have a valid label. \"Left out (X)\" counts the other items.",
        "",
    ]
    L += agree_table("Pair", [(f"{PRETTY[p['pair'][0]]} and {PRETTY[p['pair'][1]]}", p) for p in ag["pairs_of_record"]])
    L.append("")
    for p in ag["pairs_of_record"]:
        x, y = p["pair"]
        L += cross_table(f"#### Table of labels: rows {PRETTY[x]}, columns {PRETTY[y]}", p, NAMES_LABELS)
    fl = ag["fleiss_kappa_three_raters"]
    L += [
        f"Fleiss' kappa for the three raters of record: {kap(fl['kappa'])} ({fl['band']}). "
        f"It uses the {fl['n']} items with three valid labels. Left out (X): {fl['n_left_out_missing']}.",
        "",
        "#### Self-agreement of each AI rater (run 1 against run 2)",
        "",
        "Each row uses the items that have a valid label in both runs.",
        "",
    ]
    L += agree_table("Rater", [(PRETTY[r], s) for r, s in ag["self_agreement_run1_vs_run2"].items()])
    L.append("")
    m = p1["majority"]
    L += [
        "### 1.3 Majority label (a sensitivity row)",
        "",
        "The majority label is the label that at least 2 of the three raters of record give. X is not a label. "
        "So an item has no majority when its three labels are all different, or when X leaves fewer than two equal valid labels.",
        f"Items with a majority: {m['n_with_majority']}. Items with no majority: {m['n_no_majority']} "
        f"(items with an X among them: {m['n_no_majority_with_missing_X']}). An item with no majority is left out of these rows. "
        "Items with no majority: " + (", ".join(m["no_majority_items"]) if m["no_majority_items"] else "none") + ".",
        "",
    ]
    L += views_md("Majority label", m, NAMES_LABELS, left_out=True)
    sl = p1["script_label"]
    L += [
        "### 1.4 Script label (a description)",
        "",
        "The script label is the letter that the names rules give when step 2 of the rules removes no row. "
        "The tool computes it from the table of names of each item in sheet.json. It takes the rows with the kind "
        "\"name of the product\" that are not in the story or the system description. If there is no such row, the letter is G. "
        "If one or more of these rows was not found, the letter is N. In each other case it is P.",
        f"Items where the script label is the same as the human label: {sl['agreement_with_human']['same']} of "
        f"{sl['agreement_with_human']['n']} (raw agreement {pct(sl['agreement_with_human']['agreement'])}).",
        "",
        prop_table(sl["rows"], NAMES_LABELS, left_out=False),
        "",
        "#### Names of the product, one name at a time (a description)",
        "",
        "This table counts names, not items (analysis lock, section 7, point 8). It uses the rows of the tables of names "
        "with the kind \"name of the product\" that are not in the story or the system description. "
        "An item with more names has more chances of a name that is not found, so this rate is for each name. "
        "No test and no verdict uses it.",
        "",
        "| Group | Rows | Not found | Share not found |",
        "|---|---|---|---|",
    ]
    L += [f"| {r['group']} | {r['n_rows']} | {r['n_not_found']} | {pct(r['share_not_found'])} |" for r in sl["per_name_rows"]]
    L += [
        "",
        "### 1.5 Labels of each AI rater (descriptive)",
        "",
        "Labels of record (run 1) of each AI rater. Each rate uses the valid items of that rater in the group. "
        "These rates are descriptive. No test and no verdict uses them.",
        "",
    ]
    for r, a in p1["ai_raters_descriptive"].items():
        L += [f"{PRETTY[r]} run 1 ({a['file']}): valid items {a['n_valid']}, items with X {a['n_missing_X']}.", ""]
        L += views_md(f"{PRETTY[r]} run 1, descriptive", a["views"], NAMES_LABELS, left_out=True)
    L += [
        "### 1.6 Two tests (human labels)",
        "",
        "Fisher's exact test, two-sided. It compares arm A with arms B, C and D together, under the human labels. "
        "The p-values are not corrected for several tests.",
        "",
        "| Label | A: count / n | A rate | With documents: count / n | With documents rate | Fisher p |",
        "|---|---|---|---|---|---|",
    ]
    for lab, b in p1["fisher_tests_human_labels"].items():
        L.append(fisher_row(lab, b))
    L.append("")

    # ---- part 2: each sentence about a result says that no human expert checked these labels (lock 7.12)
    # PART2_METHOD_SENTENCES (below) lists the only sentences of part 2 that do not hold NO_HUMAN: they state a method, not a result.
    mx2 = p2["missing_labels_X"]
    L += [
        "## Part 2. The support (two AI raters and no human rater)",
        "",
        f"Part 2 has two AI raters and no human rater, so {NO_HUMAN}. Each table in this part comes from AI labels only.",
        "",
        f"### 2.1 Labels of each AI rater ({NO_HUMAN})",
        "",
        f"Missing labels (X) in each AI file of part 2, and {NO_HUMAN}: " + ", ".join(f"{f} {mx2[f]}" for f in SUPPORT_AI) + ".",
        "",
        f"The tables use the labels of record of each AI rater, and {NO_HUMAN}. "
        "Each rate uses the valid items of that rater in the group.",
        "",
    ]
    for r, a in p2["ai_raters"].items():
        L += [f"{PRETTY[r]} run 1 ({a['file']}): valid items {a['n_valid']}, items with X {a['n_missing_X']}, "
              f"and {NO_HUMAN}.", ""]
        L += views_md(f"{PRETTY[r]} run 1, AI labels only", a["views"], SUPPORT_LABELS, left_out=True)
    ag2 = p2["agreement"]
    L += [
        f"### 2.2 Agreement between the two AI raters ({NO_HUMAN})",
        "",
        f"The measures are the same as for the names, on the items where both labels are valid, and {NO_HUMAN}.",
        "",
    ]
    L += agree_table("Pair", [("Gemini and GPT", ag2["pair_of_record"])])
    L.append("")
    L += cross_table("#### Table of labels: rows Gemini, columns GPT", ag2["pair_of_record"], SUPPORT_LABELS)
    L += ["#### Self-agreement of each AI rater (run 1 against run 2)", "",
          f"Each row uses the items that have a valid label in both runs, and {NO_HUMAN}.", ""]
    L += agree_table("Rater", [(PRETTY[r], s) for r, s in ag2["self_agreement_run1_vs_run2"].items()])
    L.append("")
    ba = p2["both_agree"]
    L += [
        f"### 2.3 Both agree ({NO_HUMAN})",
        "",
        f"These rows use the {ba['n']} items where the two labels of record are equal and valid, and {NO_HUMAN}. "
        "\"Left out\" counts the other items of the group.",
        "",
        prop_table(ba["rows"], SUPPORT_LABELS, left_out=True),
        "",
        f"### 2.4 Four tests ({NO_HUMAN})",
        "",
        "Fisher's exact test, two-sided, for each AI rater. It compares arm A with arms B, C and D together. "
        f"The p-values are not corrected for several tests. The tests use AI labels, and {NO_HUMAN}.",
        "",
        "| Rater and label | A: count / n | A rate | With documents: count / n | With documents rate | Fisher p |",
        "|---|---|---|---|---|---|",
    ]
    for r in AI_RATERS:
        for lab in ("U", "S"):
            L.append(fisher_row(f"{PRETTY[r]}, {lab}", p2["fisher_tests"][r][lab]))
    L.append("")

    # ---- verdicts
    a_, b_, e_ = vd["EE2_a"], vd["EE2_b"], vd["EE3_level2"]
    L += [
        "## Verdicts",
        "",
        "Each verdict uses the full values in review_result.json, not the rounded values in this file. "
        "The tests have low power: a verdict \"not shown\" is not proof that there is no difference.",
        "",
        "### EE2 (a): invented names (part 1, one human rater and two AI raters)",
        "",
        "The rule (analysis lock, section 11, point 3): the verdict is \"shown\" only when all three conditions hold. "
        "In each other case it is \"not shown\".",
        "",
    ]
    L += condition_rows(a_)
    L += ["", f"**Verdict EE2 (a): {a_['verdict']}.**", ""]
    L += [
        f"### EE2 (b): unsupported or wrong statements (part 2, {NO_HUMAN})",
        "",
        "The rule (analysis lock, section 11, point 3): the verdict is \"shown by two AI raters\" only when, for each of the two AI raters, "
        "the U rate with documents is lower than in arm A and the p of its Fisher test for U is below 0.05. "
        f"In each other case it is \"not shown\". This verdict uses AI labels, and {NO_HUMAN}.",
        "",
    ]
    L += condition_rows(b_)
    L += ["", f"Gemini passes: {yes_no(b_['raters']['gemini']['passes'])}, and GPT passes: {yes_no(b_['raters']['gpt']['passes'])}, "
              f"and {NO_HUMAN}.",
          "", f"**Verdict EE2 (b): {b_['verdict']} ({NO_HUMAN}).** This verdict uses AI labels only.", ""]
    L += [
        f"### EE3 level 2: correct project-specific requirements (part 2, {NO_HUMAN})",
        "",
        "The rule (analysis lock, section 11, point 5): the verdict is \"shown by two AI raters\" only when, for each of the two AI raters, "
        "the S rate with documents is higher than in arm A and the p of its Fisher test for S is below 0.05. "
        f"In each other case it is \"not shown for correctness\". This verdict uses AI labels, and {NO_HUMAN}.",
        "",
    ]
    L += condition_rows(e_)
    L += ["", f"Gemini passes: {yes_no(e_['raters']['gemini']['passes'])}, and GPT passes: {yes_no(e_['raters']['gpt']['passes'])}, "
              f"and {NO_HUMAN}.",
          "", f"**Verdict EE3 level 2: {e_['verdict']} ({NO_HUMAN}).** This verdict uses AI labels only.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------
def cmd_score(args) -> None:
    d = Path(args.dir)
    locks = read_locks(d)
    refuse_foreign_locks(locks)
    problems = []
    for name in EXPECTED:
        st_ = lock_state(d, locks, name)
        if st_ != "locked_ok":
            problems.append(f"  {name}: {STATE_TEXT[st_]}")
    if problems:
        raise Refuse(
            "Scoring needs all nine label files locked and unchanged. The key stays closed. Problems:\n"
            + "\n".join(problems)
        )
    h_time = locks["locks"][HUMAN].get("locked_at_utc", "")
    if any(locks["locks"][n].get("locked_at_utc", "") < h_time for n in AI_FILES):
        raise Refuse(f"locks.json shows an AI file locked before {HUMAN}. The key stays closed. Stop and ask for help.")
    old = [n for n in OLD_NAMES if (d / n).exists()]
    if old:
        raise Refuse(
            "The folder holds a file with a name of the old design (" + ", ".join(old) + "). "
            "Move it out of the folder before scoring, so that no label is in a wrong file. The key stays closed."
        )
    result_dir = d / "result"
    if (result_dir / "review_result.json").exists() and not args.again:
        raise Refuse("A result already exists. The key is opened one time. Use --again only if you must. The key stays closed.")
    if locks.get("key_openings") and not args.again:
        raise Refuse(
            "locks.json shows that the key was already opened. The key is opened one time. "
            "Use --again only if you must: each opening is recorded in locks.json. The key stays closed."
        )
    items, names_rows = load_sheet(d)
    # The sheet must be the file that the builder recorded in the visible manifest.json.
    # The hash of the key is in key_do_not_open/manifest_private.json. It is read only after the opening is recorded.
    try:
        man_visible = json.loads((d / "manifest.json").read_text())
        man_sheet = man_visible["sha256"]["sheet.json"]
        old_key_fields = [k for k in man_visible["sha256"] if "review_key" in k] + [k for k in man_visible if "review_key" in k]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise Refuse(f"manifest.json is missing or has an unexpected form ({e}). The key stays closed.")
    if old_key_fields:
        # Old layout: the hash of the key must be only in key_do_not_open/manifest_private.json. Stop before the opening.
        raise Refuse(
            "manifest.json still holds a hash of the key (" + ", ".join(old_key_fields) + "). This is the old layout. "
            f"The hash of the key belongs only in key_do_not_open/{PRIVATE_MANIFEST}. The key stays closed. Stop and ask for help."
        )
    if sha256_file(d / "sheet.json") != man_sheet:
        raise Refuse("sheet.json is not the file that manifest.json records. The key stays closed. Stop and ask for help.")
    lab1, lab2 = {}, {}
    for name, (part, rater, run) in FILES.items():
        (lab1 if part == 1 else lab2)[(rater, run)] = read_labels(d, name, items)
    key_path = d / "key_do_not_open" / "review_key.json"
    if not key_path.is_file():
        raise Refuse(f"The key file is missing: {key_path}")
    st = load_stats()

    # Open the key now. Record the opening first, so that it is on record even if a later step fails.
    stamp = now_utc()
    locks.setdefault("key_openings", []).append(stamp)
    locks.setdefault("key_opened_at_utc", stamp)
    write_locks(d, locks)
    priv_path = d / "key_do_not_open" / PRIVATE_MANIFEST
    if not priv_path.is_file():
        raise Refuse(
            f"The private manifest is missing: {priv_path}. It holds the hash of the key. "
            "The opening of the key is recorded in locks.json. Nothing was scored. Stop and ask for help."
        )
    try:
        man_key = json.loads(priv_path.read_text())["review_key_sha256"]
        if not (isinstance(man_key, str) and len(man_key) == 64):
            raise ValueError("review_key_sha256 is not a SHA-256 value")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refuse(
            f"{PRIVATE_MANIFEST} has no valid field review_key_sha256 ({type(e).__name__}). "
            "The opening of the key is recorded in locks.json. Nothing was scored. Stop and ask for help."
        )
    key_bytes = key_path.read_bytes()
    if hashlib.sha256(key_bytes).hexdigest() != man_key.lower():
        raise Refuse(
            f"The key file is not the file that {PRIVATE_MANIFEST} records. Nothing was scored. Stop and ask for help."
        )
    try:
        key = json.loads(key_bytes.decode("utf-8"))
    except ValueError as e:  # UnicodeDecodeError is a ValueError
        raise Refuse(f"The key file cannot be read ({type(e).__name__}).")
    missing = [i for i in items if i not in key]
    if missing or len(key) != len(items):
        raise Refuse("The key and sheet.json do not hold the same items.")
    for i in items:
        if not all(f in key[i] for f in ("project", "model", "arm")) or key[i]["arm"] not in ARMS:
            raise Refuse(f"The key entry for item {i} has no valid project, model and arm.")

    res = compute(st, items, key, lab1, lab2, names_rows)
    hashes = {n: locks["locks"][n]["sha256"] for n in EXPECTED}
    hashes["key"] = hashlib.sha256(key_bytes).hexdigest()
    res["created_utc"] = stamp
    res["sha256"] = hashes
    res["key_hash_checked_against"] = f"key_do_not_open/{PRIVATE_MANIFEST}, field review_key_sha256"
    res["files"] = {n: {"part": p, "rater": r, "run": u, "of_record": u == 1} for n, (p, r, u) in FILES.items()}
    res["key_opened_at_utc"] = locks["key_opened_at_utc"]
    result_dir.mkdir(exist_ok=True)
    jp, mp = result_dir / "review_result.json", result_dir / "review_result.md"
    jp.write_text(json.dumps(res, indent=1) + "\n")
    mp.write_text(make_markdown(res, hashes, stamp[:10]))
    print(f"written: {jp} {mp}")


# ---------------------------------------------------------------------------
# self-test (invented data only, in a temporary folder)
# ---------------------------------------------------------------------------
_SELFTEST_ROOT: Path | None = None


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run this script through the real command line (a new Python process).
    Each call must name a --dir inside the temporary folder of the self-test, so the real folder is never used."""
    assert _SELFTEST_ROOT is not None and "--dir" in argv, "self-test: each call needs --dir"
    target = Path(argv[argv.index("--dir") + 1]).resolve()
    assert _SELFTEST_ROOT in target.parents, f"self-test: --dir must be inside the temporary folder ({target})"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), *argv], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


def _frac_fisher(a: int, b: int, c: int, d: int):
    """Independent two-sided Fisher p with exact fractions (for the self-test)."""
    from fractions import Fraction
    from math import comb

    r1, r2, c1, n = a + b, c + d, a + c, a + b + c + d
    pr = lambda x: Fraction(comb(r1, x) * comb(r2, c1 - x), comb(n, c1))  # noqa: E731
    po = pr(a)
    return float(sum(pr(x) for x in range(max(0, c1 - r2), min(r1, c1) + 1) if pr(x) <= po))


def _pat(a: str, d: str) -> dict[str, str]:
    """Items R01..R60. Arm A = R01-R15 gets the 15 letters of `a`; each of B (R16-R30), C (R31-R45), D (R46-R60) gets `d`."""
    assert len(a) == 15 and len(d) == 15
    out = {}
    for k in range(4):
        for j, ch in enumerate(a if k == 0 else d):
            out[f"R{k * 15 + j + 1:02d}"] = ch
    return out


def _rows_for(letter: str) -> list[dict]:
    """A simple names table that gives `letter` as the script label."""
    if letter == "G":
        return [{"name": "TLS", "kind": "standard", "where": "fact sheet", "found": True, "given": False}]
    if letter == "P":
        return [{"name": "app-setting", "kind": "product", "where": "documentation", "found": True, "given": False}]
    return [{"name": "app-invented", "kind": "product", "where": "not found", "found": False, "given": False}]


# Three hand-made tables with more than one row (analysis_lock.md 7.8).
HAND_TABLES = {
    # N: one product row not given and found, one product row not given and not found, a standard row not found.
    "R02": ([{"name": "a-real", "kind": "product", "where": "documentation", "found": True, "given": False},
             {"name": "a-fake", "kind": "product", "where": "not found", "found": False, "given": False},
             {"name": "SHA-1", "kind": "standard", "where": "not found", "found": False, "given": False}], "N"),
    # G: a product row that was given (and not found), a standard row, a 'not a name' row not found.
    "R12": ([{"name": "Redis", "kind": "product", "where": "not found", "found": False, "given": True},
             {"name": "OAuth", "kind": "standard", "where": "fact sheet", "found": True, "given": False},
             {"name": "<bucket>", "kind": "not a name", "where": "not found", "found": False, "given": False}], "G"),
    # P: two product rows not given and found (one in the repository only), a given product row not found, a placeholder.
    "R44": ([{"name": "b-real", "kind": "product", "where": "fact sheet", "found": True, "given": False},
             {"name": "c-real", "kind": "product", "where": "repository only", "found": True, "given": False},
             {"name": "d-given", "kind": "product", "where": "not found", "found": False, "given": True},
             {"name": "example", "kind": "not a name", "where": "not found", "found": False, "given": False}], "P"),
}


def _write_labels(path: Path, labels: dict[str, str], notes: dict[str, str] | None = None) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "label", "note"])
        for i in range(1, N_ITEMS + 1):
            item = f"R{i:02d}"
            w.writerow([item, labels[item], (notes or {}).get(item, "")])


def _make_dir(root: Path, tag: str, files: dict[str, dict[str, str]] | None, script: dict[str, str]) -> Path:
    """Invented review folder: key, sheet.json with names_rows, the two manifests, and the given label files."""
    d = root / tag
    (d / "key_do_not_open").mkdir(parents=True)
    key = {}
    for i in range(1, N_ITEMS + 1):
        key[f"R{i:02d}"] = {
            "project": ["bitwarden", "mastodon", "keycloak"][i % 3],
            "model": "gemma4:26b" if i % 2 else "qwen3.8:27b",
            "arm": ARMS[(i - 1) // 15],
            "run": 1,
            "story_id": f"S{(i - 1) // 4}",
            "req_index": 0,
        }
    (d / "key_do_not_open" / "review_key.json").write_text(json.dumps(key))
    stories = []
    for k in range(15):
        its = []
        for j in range(4):
            item = f"R{k * 4 + j + 1:02d}"
            rows = HAND_TABLES[item][0] if item in HAND_TABLES else _rows_for(script[item])
            its.append({"item": item, "text": "invented", "names_table": "invented", "names_rows": rows})
        stories.append({"story_id": f"S{k}", "items": its})
    (d / "sheet.json").write_text(json.dumps({"version": 2, "labels": list(SUPPORT_LABELS), "stories": stories}))
    # The two manifests in the layout of build_study2_review_sheet_v2.py (function main):
    # the visible one has no key hash and no totals; the private one is in the key folder.
    (d / "manifest.json").write_text(json.dumps({
        "sheet_version": 2, "n_items": N_ITEMS, "n_stories_with_items": 15, "built_at_utc": "2026-11-14T00:00:00+00:00",
        "sha256": {"sheet.json": sha256_file(d / "sheet.json"), "merge_rule.md": "0" * 64, "evidence": {}}}))
    (d / "key_do_not_open" / PRIVATE_MANIFEST).write_text(json.dumps({
        "seed": SEED, "built_at_utc": "2026-11-14T00:00:00+00:00",
        "items_by_project": dict(sorted(Counter(v["project"] for v in key.values()).items())),
        "items_by_arm": dict(sorted(Counter(v["arm"] for v in key.values()).items())),
        "items_by_model": dict(sorted(Counter(v["model"] for v in key.values()).items())),
        "review_key_sha256": sha256_file(d / "key_do_not_open" / "review_key.json")}))
    for fname, labels in (files or {}).items():
        _write_labels(d / fname, labels)
    return d


def _lock_all(d: Path) -> None:
    for name in EXPECTED:
        code, out, err = run_cli(["lock", name, "--dir", str(d)])
        assert code == 0, f"lock {name} failed: {err}{out}"


def self_test() -> int:
    global _SELFTEST_ROOT
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond, note: str = "") -> None:
        results.append((name, bool(cond), note))
        print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({note})" if note and not cond else ""))

    def approx(a, b, tol=1e-9) -> bool:
        return a is not None and b is not None and abs(a - b) < tol

    st = load_stats()

    # ---------------- invented labels. Each expected value below was computed by hand (see the comments).
    # Part 1, human: arm A = N R01-R08, G R09-R13, P R14-R15 (N8 G5 P2).
    # Each document arm (offset o = 15, 30, 45): N o+1..o+2, G o+3..o+8, P o+9..o+15 (N2 G6 P7).
    H1 = _pat("N" * 8 + "G" * 5 + "P" * 2, "N" * 2 + "G" * 6 + "P" * 7)
    G1 = {**H1, "R01": "G", "R20": "P", "R40": "N", "R50": "X", "R58": "X"}  # Gemini names run 1
    T1 = {**H1, "R01": "P", "R09": "N", "R55": "X"}  # GPT names run 1
    G2 = {**G1, "R05": "G"}  # Gemini names run 2
    T2 = {**T1, "R33": "X", "R12": "P"}  # GPT names run 2
    # Part 2, Gemini: arm A = U R01-R09, G R10-R13, S R14-R15; document arms U o+1..3, G o+4..11, S o+12..15; X at R15, R30.
    GS = {**_pat("U" * 9 + "G" * 4 + "S" * 2, "U" * 3 + "G" * 8 + "S" * 4), "R15": "X", "R30": "X"}
    # Part 2, GPT: arm A as Gemini; document arms U o+1..2, G o+3..6, S o+7..15; X at R45.
    TS = {**_pat("U" * 9 + "G" * 4 + "S" * 2, "U" * 2 + "G" * 4 + "S" * 9), "R45": "X"}
    GS2 = {**GS, "R16": "G", "R20": "X"}
    TS2 = {**TS, "R60": "G"}
    # Script labels: the human letters, except R10 (G -> P) and R25 (P -> N). R02, R12, R44 use the hand-made tables.
    SCRIPT = {**H1, "R10": "P", "R25": "N"}

    def files_for(h, g1, t1, g2, t2, gs, ts, gs2, ts2) -> dict:
        return {HUMAN: h, NAMES_AI[0]: g1, NAMES_AI[1]: g2, NAMES_AI[2]: t1, NAMES_AI[3]: t2,
                SUPPORT_AI[0]: gs, SUPPORT_AI[1]: gs2, SUPPORT_AI[2]: ts, SUPPORT_AI[3]: ts2}

    base = files_for(H1, G1, T1, G2, T2, GS, TS, GS2, TS2)

    scratch_base = os.environ.get("TMPDIR") or None
    with tempfile.TemporaryDirectory(prefix="score_selftest_", dir=scratch_base) as tmp:
        root = Path(tmp).resolve()
        _SELFTEST_ROOT = root

        # ---------------- lock, status, order of the locks
        d = _make_dir(root, "base", base, SCRIPT)
        code, out, err = run_cli(["status", "--dir", str(d)])
        check("status: nine files present, none locked, one line for each file",
              code == 0 and out.count("present and not locked") == 9 and len(out.splitlines()) == 9 and err == "", err)
        code, out, err = run_cli(["lock", NAMES_AI[0], "--dir", str(d)])
        check("lock order: a part 1 AI file is refused while the human file is not locked",
              code == 1 and "not locked" in err and not locks_path(d).exists(), err)
        code, out, err = run_cli(["lock", SUPPORT_AI[2], "--dir", str(d)])
        check("lock order: a part 2 AI file is refused while the human file is not locked",
              code == 1 and "not locked" in err and not locks_path(d).exists(), err)
        code, out, err = run_cli(["score", "--dir", str(d)])
        check("score: refused when nothing is locked; key stays closed", code == 1 and "nine" in err and not locks_path(d).exists(), err)
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d)])
        h_hash = sha256_file(d / HUMAN)
        check("lock: human file locks and prints only its name, hash and time",
              code == 0 and out.splitlines() == [f"locked: {HUMAN}", f"sha256: {h_hash}", out.splitlines()[2]]
              and out.splitlines()[2].startswith("locked at (UTC): ") and err == "", err + out)
        before = locks_path(d).read_bytes()
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d)])
        check("lock: a second lock says 'already locked' and changes nothing",
              code == 1 and "already locked" in out and locks_path(d).read_bytes() == before)
        code, out, err = run_cli(["lock", SUPPORT_AI[0], "--dir", str(d)])
        check("lock order: a part 2 AI file locks after the human file", code == 0, err)
        code, out, err = run_cli(["status", "--dir", str(d)])
        check("status: shows locked and not locked files",
              out.count("locked (hash check ok)") == 2 and out.count("present and not locked") == 7, out)
        for name in EXPECTED:
            if name not in (HUMAN, SUPPORT_AI[0]):
                run_cli(["lock", name, "--dir", str(d)])
        lk = json.loads(locks_path(d).read_text())
        check("locks.json: nine entries with part, hash, UTC time and 60 rows; key not opened",
              set(lk["locks"]) == set(EXPECTED)
              and all(len(v["sha256"]) == 64 and v["locked_at_utc"].endswith("Z") and v["rows"] == 60 for v in lk["locks"].values())
              and all(lk["locks"][n]["part"] == FILES[n][0] for n in EXPECTED) and "key_opened_at_utc" not in lk)
        code, out, err = run_cli(["status", "--dir", str(d)])
        check("status: no digit, percent sign or label count on screen (file states only)",
              out.count("locked (hash check ok)") == 9 and "%" not in out
              and all(re.fullmatch(r"labels_[a-z0-9_]+\.csv: [a-zA-Z ()]+", ln) for ln in out.splitlines()), out)

        # ---------------- old file names
        dold = _make_dir(root, "oldnames", {"labels_george.csv": H1, "labels_gemini_run1.csv": GS}, SCRIPT)
        code, out, err = run_cli(["lock", "labels_george.csv", "--dir", str(dold)])
        check("old file name: lock labels_george.csv refused with a clear message",
              code == 1 and "old design" in err and HUMAN in err and not locks_path(dold).exists(), err)
        code, out, err = run_cli(["lock", "labels_gemini_run1.csv", "--dir", str(dold)])
        check("old file name: lock labels_gemini_run1.csv refused with the new names",
              code == 1 and "old design" in err and "labels_support_gemini_run1.csv" in err, err)
        code, out, err = run_cli(["status", "--dir", str(dold)])
        check("old file name: status flags each old file in the folder",
              "labels_george.csv: OLD FILE NAME" in out and "labels_gemini_run1.csv: OLD FILE NAME" in out, out)
        dold2 = _make_dir(root, "oldplusnew", base, SCRIPT)
        _lock_all(dold2)
        _write_labels(dold2 / "labels_gpt_run2.csv", TS)
        code, out, err = run_cli(["score", "--dir", str(dold2)])
        check("old file name: score refuses while an old file is in the folder; key stays closed",
              code == 1 and "old design" in err and "key_openings" not in locks_path(dold2).read_text(), err)
        dold3 = _make_dir(root, "oldlock", base, SCRIPT)
        locks_path(dold3).write_text(json.dumps({"version": 1, "locks": {"labels_george.csv": {
            "file": "labels_george.csv", "sha256": "0" * 64, "locked_at_utc": "2026-11-14T00:00:00Z", "rows": 60}}}))
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(dold3)])
        code2, out2, err2 = run_cli(["status", "--dir", str(dold3)])
        check("old file name: a lock of the old design in locks.json is refused by lock and flagged by status",
              code == 1 and "old design" in err and HUMAN not in json.loads(locks_path(dold3).read_text())["locks"]
              and "locks.json: holds a lock" in out2, err + out2)

        # ---------------- invalid files are never locked
        d2 = _make_dir(root, "bad", base, SCRIPT)
        p = d2 / HUMAN
        for bad, word in (("X", "only in an AI file"), ("S", "part 2"), ("U", "part 2"), ("n", "exactly"),
                          (" N", "exactly"), ("P ", "exactly"), ("", "no label")):
            _write_labels(p, {**H1, "R07": bad})
            code, out, err = run_cli(["lock", HUMAN, "--dir", str(d2)])
            check(f"lock: label {bad!r} in the human file refused with the item number",
                  code == 1 and "R07" in err and word in err and not locks_path(d2).exists(), err)
        _write_labels(p, H1)
        p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d2)])
        check("lock: Windows line ends and a byte-order mark are accepted", code == 0, err)
        for fname, good, bad, word in ((NAMES_AI[2], T1, "S", "part 2"), (NAMES_AI[2], T1, "x", "P, N, G or X"),
                                       (SUPPORT_AI[1], GS2, "P", "part 1"), (SUPPORT_AI[1], GS2, "N", "part 1"),
                                       (SUPPORT_AI[1], GS2, " X", "S, G, U or X"), (SUPPORT_AI[1], GS2, "Z", "S, G, U or X")):
            _write_labels(d2 / fname, {**good, "R11": bad})
            code, out, err = run_cli(["lock", fname, "--dir", str(d2)])
            check(f"lock: label {bad!r} in {fname} refused with the item number",
                  code == 1 and "R11" in err and word in err and fname not in json.loads(locks_path(d2).read_text())["locks"], err)
        _write_labels(d2 / NAMES_AI[2], T1)
        code, out, err = run_cli(["lock", NAMES_AI[2], "--dir", str(d2)])
        check("lock: X accepted in a part 1 AI file; the output has no label count",
              code == 0 and out.startswith(f"locked: {NAMES_AI[2]}") and len(out.splitlines()) == 3 and err == "", err)
        _write_labels(d2 / SUPPORT_AI[1], GS2)
        code, out, err = run_cli(["lock", SUPPORT_AI[1], "--dir", str(d2)])
        check("lock: X accepted in a part 2 AI file", code == 0, err)
        d3 = _make_dir(root, "bad2", base, SCRIPT)
        p = d3 / HUMAN
        lines = p.read_text().splitlines()
        p.write_text("\n".join(lines[:-1]) + "\n")
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d3)])
        check("lock: 59 rows refused", code == 1 and "60" in err and "59" in err, err)
        p.write_text("\n".join(lines + ["R61,P,"]) + "\n")
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d3)])
        check("lock: 61 rows refused", code == 1 and "61" in err, err)
        sw = lines[:]
        sw[1], sw[2] = sw[2], sw[1]
        p.write_text("\n".join(sw) + "\n")
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d3)])
        check("lock: items in the wrong order refused", code == 1 and "expects" in err, err)
        p.write_text("item,label\n" + "\n".join(f"R{i:02d},P" for i in range(1, 61)) + "\n")
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d3)])
        check("lock: wrong columns refused", code == 1 and "item,label,note" in err, err)
        _write_labels(p, H1, notes={"R03": "has, a comma"})
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d3)])
        check("lock: a note with a comma in quotes is accepted", code == 0, err)
        code, out, err = run_cli(["lock", "labels_other.csv", "--dir", str(d3)])
        check("lock: unknown file name refused", code == 1 and "not a known label file" in err, err)

        # ---------------- missing file and changed file block score
        d4 = _make_dir(root, "missing", {k: v for k, v in base.items() if k != SUPPORT_AI[3]}, SCRIPT)
        code, out, err = run_cli(["lock", SUPPORT_AI[3], "--dir", str(d4)])
        check("missing file: lock refused", code == 1 and ("does not exist" in err or "not locked" in err), err)
        for name in EXPECTED[:-1]:
            run_cli(["lock", name, "--dir", str(d4)])
        code, out, err = run_cli(["status", "--dir", str(d4)])
        check("missing file: status shows 'missing'", f"{SUPPORT_AI[3]}: missing" in out, out)
        code, out, err = run_cli(["score", "--dir", str(d4)])
        check("missing file: score refused when only eight files are locked; key stays closed",
              code == 1 and SUPPORT_AI[3] in err and "key_openings" not in locks_path(d4).read_text(), err)
        d4b = _make_dir(root, "vanished", base, SCRIPT)
        _lock_all(d4b)
        (d4b / NAMES_AI[3]).unlink()
        code, out, err = run_cli(["status", "--dir", str(d4b)])
        code2, out2, err2 = run_cli(["score", "--dir", str(d4b)])
        check("missing file: a locked file that vanished is flagged, and score is refused",
              f"{NAMES_AI[3]}: CHANGED AFTER LOCK (the file is gone)" in out and code2 == 1 and NAMES_AI[3] in err2
              and "key_openings" not in locks_path(d4b).read_text(), out + err2)
        d5 = _make_dir(root, "changed", base, SCRIPT)
        _lock_all(d5)
        _write_labels(d5 / SUPPORT_AI[1], {**GS2, "R31": "S"})
        code, out, err = run_cli(["status", "--dir", str(d5)])
        check("changed file: status flags it", f"{SUPPORT_AI[1]}: CHANGED AFTER LOCK" in out and out.count("hash check ok") == 8, out)
        code, out, err = run_cli(["score", "--dir", str(d5)])
        check("changed file: score refused; key stays closed; no result",
              code == 1 and "CHANGED AFTER LOCK" in err and "key_openings" not in locks_path(d5).read_text()
              and not (d5 / "result").exists(), err)
        code, out, err = run_cli(["lock", SUPPORT_AI[1], "--dir", str(d5)])
        check("changed file: it cannot be locked again", code == 1 and "already locked" in out)
        d7 = _make_dir(root, "humanchanged", base, SCRIPT)
        run_cli(["lock", HUMAN, "--dir", str(d7)])
        _write_labels(d7 / HUMAN, {**H1, "R01": "P"})
        code, out, err = run_cli(["lock", SUPPORT_AI[2], "--dir", str(d7)])
        check("changed file: an AI file is refused when the human file changed after its lock", code == 1 and "changed" in err, err)
        d10 = _make_dir(root, "lockorder", base, SCRIPT)
        _lock_all(d10)
        lk10 = json.loads(locks_path(d10).read_text())
        lk10["locks"][SUPPORT_AI[3]]["locked_at_utc"] = "2000-01-01T00:00:00Z"
        write_locks(d10, lk10)
        code, out, err = run_cli(["score", "--dir", str(d10)])
        check("lock order: score refused when locks.json shows an AI file locked before the human file",
              code == 1 and "before" in err and "key_openings" not in locks_path(d10).read_text(), err)

        # ---------------- sheet, manifests and key
        d8 = _make_dir(root, "sheetchanged", base, SCRIPT)
        _lock_all(d8)
        sj = json.loads((d8 / "sheet.json").read_text())
        sj["stories"][0]["items"][1]["names_rows"] = []
        (d8 / "sheet.json").write_text(json.dumps(sj))
        code, out, err = run_cli(["score", "--dir", str(d8)])
        check("sheet: a changed sheet.json (names_rows) is refused against manifest.json; key stays closed",
              code == 1 and "manifest.json" in err and "key_openings" not in locks_path(d8).read_text(), err)
        d8b = _make_dir(root, "sheetbadrows", base, SCRIPT)
        sj = json.loads((d8b / "sheet.json").read_text())
        sj["stories"][0]["items"][0]["names_rows"][0]["found"] = "yes"
        (d8b / "sheet.json").write_text(json.dumps(sj))
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d8b)])
        check("sheet: a names_rows row with a found value that is not true or false is refused",
              code == 1 and "unexpected form" in err and not locks_path(d8b).exists(), err)
        d9 = _make_dir(root, "keychanged", base, SCRIPT)
        _lock_all(d9)
        kp9 = d9 / "key_do_not_open" / "review_key.json"
        kp9.write_text(kp9.read_text() + " ")
        code, out, err = run_cli(["score", "--dir", str(d9)])
        lk9 = json.loads(locks_path(d9).read_text())
        check("key hash: a wrong key is refused against manifest_private.json; opening recorded; no result",
              code == 1 and PRIVATE_MANIFEST in err and "not the file" in err and len(lk9.get("key_openings", [])) == 1
              and not (d9 / "result").exists(), err)
        code, out, err = run_cli(["score", "--dir", str(d9)])
        check("key: after a recorded opening, a new score without --again is refused",
              code == 1 and "already opened" in err and len(json.loads(locks_path(d9).read_text())["key_openings"]) == 1, err)
        d9b = _make_dir(root, "privwrong", base, SCRIPT)
        _lock_all(d9b)
        pp = d9b / "key_do_not_open" / PRIVATE_MANIFEST
        pj = json.loads(pp.read_text())
        pj["review_key_sha256"] = "0" * 64
        pp.write_text(json.dumps(pj))
        code, out, err = run_cli(["score", "--dir", str(d9b)])
        check("key hash: a wrong hash in manifest_private.json is refused; no result",
              code == 1 and "not the file" in err and not (d9b / "result").exists(), err)
        d9c = _make_dir(root, "privnofield", base, SCRIPT)
        _lock_all(d9c)
        pp = d9c / "key_do_not_open" / PRIVATE_MANIFEST
        pj = json.loads(pp.read_text())
        del pj["review_key_sha256"]
        pp.write_text(json.dumps(pj))
        code, out, err = run_cli(["score", "--dir", str(d9c)])
        check("key hash: manifest_private.json without review_key_sha256 is refused; no result",
              code == 1 and "review_key_sha256" in err and not (d9c / "result").exists(), err)
        d9d = _make_dir(root, "privmissing", base, SCRIPT)
        _lock_all(d9d)
        (d9d / "key_do_not_open" / PRIVATE_MANIFEST).unlink()
        code, out, err = run_cli(["score", "--dir", str(d9d)])
        check("key hash: a missing manifest_private.json gives a clear error; opening recorded; no result",
              code == 1 and "private manifest is missing" in err
              and len(json.loads(locks_path(d9d).read_text()).get("key_openings", [])) == 1 and not (d9d / "result").exists(), err)
        d9e = _make_dir(root, "privorder", base, SCRIPT)
        _lock_all(d9e)
        (d9e / "key_do_not_open" / PRIVATE_MANIFEST).unlink()
        (d9e / "sheet.json").write_text((d9e / "sheet.json").read_text() + " ")
        code, out, err = run_cli(["score", "--dir", str(d9e)])
        check("key hash: the sheet check comes before the private manifest; key stays closed",
              code == 1 and "sheet.json" in err and "private" not in err and "key_openings" not in locks_path(d9e).read_text(), err)
        d9f = _make_dir(root, "visoldfield", base, SCRIPT)
        _lock_all(d9f)
        mv = json.loads((d9f / "manifest.json").read_text())
        mv["sha256"]["key_do_not_open/review_key.json"] = sha256_file(d9f / "key_do_not_open" / "review_key.json")
        (d9f / "manifest.json").write_text(json.dumps(mv))
        code, out, err = run_cli(["score", "--dir", str(d9f)])
        check("old layout: a visible manifest with a key hash is refused; key stays closed",
              code == 1 and "old layout" in err and "key_openings" not in locks_path(d9f).read_text()
              and not (d9f / "result").exists(), err)
        vis = json.loads((d / "manifest.json").read_text())
        check("self-test layout: the visible manifest has no key hash and no totals",
              not any("key" in k for k in vis["sha256"]) and not any(k.startswith("items_by") for k in vis))

        # ---------------- score on the base data
        code, out, err = run_cli(["score", "--dir", str(d)])
        rp, mp = d / "result" / "review_result.json", d / "result" / "review_result.md"
        check("score: runs and prints only 'written: <paths>'",
              code == 0 and out.startswith("written: ") and out.count("\n") == 1 and err == "" and rp.is_file() and mp.is_file(), err)
        if code != 0:
            print(f"\nself-test stopped: the base score failed.\n{err}")
            return 1
        res = json.loads(rp.read_text())
        md = mp.read_text()
        lk = json.loads(locks_path(d).read_text())
        check("score: key_opened_at_utc recorded in locks.json", lk.get("key_opened_at_utc") and len(lk["key_openings"]) == 1)
        first = md.splitlines()[0]
        check("result.md: the first line has the date and the ten hashes (nine files and the key)",
              first.startswith("Date: ") and len(res["sha256"]) == 10 and all(h in first for h in res["sha256"].values()))
        check("result: the key hash is the hash of the key file", res["sha256"]["key"] == sha256_file(d / "key_do_not_open" / "review_key.json"))
        p1, p2, vd = res["part1_names"], res["part2_support"], res["verdicts"]

        # ---- part 1: shares
        pa = {r["group"]: r for r in p1["primary_human"]["all"]}
        check("part 1 shares: arm A is P 2, N 8, G 5 of 15; each document arm P 7, N 2, G 6 of 15",
              pa["A"]["n"] == 15 and pa["A"]["counts"] == {"P": 2, "N": 8, "G": 5}
              and all(pa[a]["counts"] == {"P": 7, "N": 2, "G": 6} for a in "BCD"))
        check("part 1 shares: with documents P 21, N 6, G 18 of 45 (N rate 6/45); all arms P 23, N 14, G 23 of 60",
              pa[DOCS]["counts"] == {"P": 21, "N": 6, "G": 18} and approx(pa[DOCS]["proportions"]["N"], 6 / 45)
              and pa["All arms"]["counts"] == {"P": 23, "N": 14, "G": 23} and pa["All arms"]["n"] == 60)
        lo, hi = pa["A"]["ci95"]["P"]
        # By hand: the lower limit L of 2 in 15 solves P(X >= 2 | L) = 0.025, so 1 - (1-L)^15 - 15 L (1-L)^14 = 0.025;
        # the upper limit U solves P(X <= 2 | U) = 0.025. scipy beta.ppf gives 0.0165759134 and 0.4046026966.
        eq_lo = 1 - (1 - lo) ** 15 - 15 * lo * (1 - lo) ** 14
        eq_hi = (1 - hi) ** 15 + 15 * hi * (1 - hi) ** 14 + 105 * hi ** 2 * (1 - hi) ** 13
        check("part 1 interval: Clopper-Pearson for P 2 of 15 meets its two defining equations (by hand) and the scipy values",
              approx(eq_lo, 0.025, 1e-9) and approx(eq_hi, 0.025, 1e-9)
              and approx(lo, 0.016575913440083623, 1e-8) and approx(hi, 0.40460269660337206, 1e-8), str((lo, hi)))
        bm = p1["primary_human"]["by_model"]
        gA, qA = group_row(bm["Gemma"], "A"), group_row(bm["Qwen"], "A")
        # Gemma = odd items. Arm A: N R01 R03 R05 R07, G R09 R11 R13, P R15. Qwen = even items: N 4, G 2, P 1.
        check("part 1 model rows: Gemma arm A n 8 P1 N4 G3; Qwen arm A n 7 P1 N4 G2 (by hand)",
              set(bm) == {"Gemma", "Qwen"} and gA["n"] == 8 and gA["counts"] == {"P": 1, "N": 4, "G": 3}
              and qA["n"] == 7 and qA["counts"] == {"P": 1, "N": 4, "G": 2})
        bp = p1["primary_human"]["by_project"]
        # mastodon = items with i % 3 == 1. Arm A: R01 N, R04 N, R07 N, R10 G, R13 G.
        check("part 1 project rows: mastodon arm A n 5 P0 N3 G2 (by hand)",
              set(bp) == {"bitwarden", "keycloak", "mastodon"} and group_row(bp["mastodon"], "A")["counts"] == {"P": 0, "N": 3, "G": 2})
        # AI descriptive tables. Gemini run 1: A has R01 N->G: P2 N7 G6; D has X at R50 (G) and R58 (P): P6 N2 G5, n 13.
        ga = {r["group"]: r for r in p1["ai_raters_descriptive"]["gemini"]["views"]["all"]}
        check("part 1 AI tables: Gemini A n15 P2 N7 G6; D n13 P6 N2 G5 with 2 left out (X); marked descriptive",
              ga["A"]["n"] == 15 and ga["A"]["counts"] == {"P": 2, "N": 7, "G": 6}
              and ga["D"]["n"] == 13 and ga["D"]["counts"] == {"P": 6, "N": 2, "G": 5} and ga["D"]["n_left_out"] == 2
              and p1["ai_raters_descriptive"]["gemini"]["status"] == "descriptive"
              and "by_project" in p1["ai_raters_descriptive"]["gemini"]["views"])
        check("part 1 X labels: counts for each AI file (2, 2, 1, 2)",
              p1["missing_labels_X"] == {NAMES_AI[0]: 2, NAMES_AI[1]: 2, NAMES_AI[2]: 1, NAMES_AI[3]: 2})

        # ---- part 1: agreement
        pr = {tuple(x["pair"]): x for x in p1["agreement"]["pairs_of_record"]}
        hg = pr[("human", "gemini")]
        # By hand: 58 items (R50, R58 left out). Same label on 55 (R01, R20, R40 differ). Both raters: P22 N14 G22.
        # pe = (22*22 + 14*14 + 22*22) / 58^2 = 1164/3364; po = 3190/3364; kappa = 2026/2200 = 1013/1100.
        check("part 1 kappa: human and Gemini n 58, 2 left out, 55 same, kappa 1013/1100 (by hand), band almost perfect",
              hg["n"] == 58 and hg["n_left_out_missing"] == 2 and hg["agreement_count"] == 55
              and approx(hg["agreement"], 55 / 58) and approx(hg["kappa"], 1013 / 1100, 1e-12) and hg["band"] == "almost perfect",
              str(hg["kappa"]))
        check("part 1 3x3 table human (rows) and Gemini (columns), by hand",
              hg["table_rows_first_rater_cols_second"] == {"P": {"P": 21, "N": 1, "G": 0}, "N": {"P": 0, "N": 13, "G": 1},
                                                           "G": {"P": 1, "N": 0, "G": 21}})
        blo, bhi = hg["kappa_ci95"]
        keep = [f"R{i:02d}" for i in range(1, 61) if f"R{i:02d}" not in ("R50", "R58")]
        again = st.kappa_bootstrap([H1[i] for i in keep], [G1[i] for i in keep], resamples=RESAMPLES, seed=SEED)
        check("part 1 bootstrap: interval is that of stats.kappa_bootstrap (10,000 resamples, seed 20261114), ordered, with its count of resamples with no kappa",
              [again["lo"], again["hi"]] == [blo, bhi] and blo <= hg["kappa"] <= bhi and hg["bootstrap_resamples"] == RESAMPLES
              and hg["bootstrap_resamples_no_kappa"] == again["n_skipped"])
        # Human and GPT: 59 items (R55 out), 57 same (R01, R09). pe = 1208/3481, kappa = 2155/2273.
        # Gemini and GPT: 57 items (R50, R55, R58 out), 53 same, kappa 1901/2129 (exact fractions).
        check("part 1 kappa: human and GPT 2155/2273; Gemini and GPT 1901/2129 (exact fractions)",
              approx(pr[("human", "gpt")]["kappa"], 2155 / 2273, 1e-12) and pr[("human", "gpt")]["agreement_count"] == 57
              and approx(pr[("gemini", "gpt")]["kappa"], 1901 / 2129, 1e-12) and pr[("gemini", "gpt")]["n"] == 57)
        # Fleiss by hand: 57 items with three valid labels (R50, R55, R58 out). 53 unanimous rows (agreement 1),
        # three rows split 2-1 (R09, R20, R40: 1/3 each), one row all different (R01: 0). p_bar = 54/57.
        # Totals over 171 labels: N 42, G 65, P 64. pe = (42^2 + 65^2 + 64^2) / 171^2 = 10085/29241.
        # kappa = (27702/29241 - 10085/29241) / (1 - 10085/29241) = 17617/19156.
        fl = p1["agreement"]["fleiss_kappa_three_raters"]
        check("part 1 Fleiss' kappa on 57 items with three valid labels = 17617/19156 (by hand)",
              fl["n"] == 57 and fl["n_left_out_missing"] == 3 and approx(fl["kappa"], 17617 / 19156, 1e-12), str(fl["kappa"]))
        sa = p1["agreement"]["self_agreement_run1_vs_run2"]
        check("part 1 self-agreement: Gemini 57 of 58 same, kappa 1067/1096; GPT 57 of 58 same (R33 X, R12 differs), kappa 1069/1098",
              sa["gemini"]["n"] == 58 and sa["gemini"]["agreement_count"] == 57 and approx(sa["gemini"]["kappa"], 1067 / 1096, 1e-12)
              and sa["gpt"]["n"] == 58 and sa["gpt"]["n_left_out_missing"] == 2 and approx(sa["gpt"]["kappa"], 1069 / 1098, 1e-12))

        # ---- part 1: majority
        mj = p1["majority"]
        # R01: N, G, P -> no majority. R50 (G, X, G) -> G. R58 (P, X, P) -> P. R55 (P, P, X) -> P. Others follow the human.
        check("part 1 majority: one item with no majority (R01, no X); 59 with a majority",
              mj["no_majority_items"] == ["R01"] and mj["n_no_majority"] == 1 and mj["n_with_majority"] == 59
              and mj["n_no_majority_with_missing_X"] == 0)
        ma = {r["group"]: r for r in mj["all"]}
        check("part 1 majority rows: A n 14 P2 N7 G5 (R01 left out); with documents n 45 P21 N6 G18",
              ma["A"]["n"] == 14 and ma["A"]["counts"] == {"P": 2, "N": 7, "G": 5} and ma["A"]["n_left_out"] == 1
              and ma[DOCS]["n"] == 45 and ma[DOCS]["counts"] == {"P": 21, "N": 6, "G": 18})
        check("part 1 majority rule with X: P, X, P gives P; P, X, G and P, X, X give none; P, N, G gives none",
              majority_of_record(st, ["P", "X", "P"]) == "P" and majority_of_record(st, ["P", "X", "G"]) is None
              and majority_of_record(st, ["P", "X", "X"]) is None and majority_of_record(st, ["P", "N", "G"]) is None
              and majority_of_record(st, ["N", "N", "G"]) == "N")

        # ---- part 1: script label
        sl = p1["script_label"]
        check("script label: the three hand-made tables give N (R02), G (R12) and P (R44)",
              all(sl["by_item"][i] == HAND_TABLES[i][1] for i in HAND_TABLES)
              and all(script_label(HAND_TABLES[i][0]) == HAND_TABLES[i][1] for i in HAND_TABLES)
              and script_label([]) == "G")
        sr = {r["group"]: r for r in sl["rows"]}
        # By hand: A has R10 G -> P: P3 N8 G4. B has R25 P -> N: P6 N3 G6. C and D as the human. Documents P20 N7 G18.
        check("script label shares: A P3 N8 G4; B P6 N3 G6; with documents P20 N7 G18; all P23 N15 G22 (by hand)",
              sr["A"]["counts"] == {"P": 3, "N": 8, "G": 4} and sr["B"]["counts"] == {"P": 6, "N": 3, "G": 6}
              and sr[DOCS]["counts"] == {"P": 20, "N": 7, "G": 18} and sr["All arms"]["counts"] == {"P": 23, "N": 15, "G": 22})
        check("script label: raw agreement with the human label 58 of 60",
              sl["agreement_with_human"]["same"] == 58 and approx(sl["agreement_with_human"]["agreement"], 58 / 60))
        # Lock 7.8, rate for each name. By hand: one row for each item with script letter N or P (_rows_for), none for G;
        # R02 has 2 rows (1 not found), R12 none, R44 2 found rows. A: N items R01, R03-R08 (7 not found), R02 (2, 1 not found),
        # P items R10, R14, R15 (3 found): 12 rows, 8 not found. B: R16, R17, R25 not found; R24, R26-R30 found: 9 rows, 3.
        # C: R31, R32 not found; R39-R43, R45 and two rows of R44 found: 10 rows, 2. D: R46, R47 not found; R54-R60 found: 9, 2.
        pnr = {r["group"]: r for r in sl["per_name_rows"]}
        check("script label: rows of names of the product for each arm and the share not found (by hand, lock 7.8)",
              [(pnr[g]["n_rows"], pnr[g]["n_not_found"]) for g in ("A", "B", "C", "D", DOCS, "All arms")]
              == [(12, 8), (9, 3), (10, 2), (9, 2), (28, 7), (40, 15)] and approx(pnr["A"]["share_not_found"], 8 / 12)
              and "| A | 12 | 8 | 66.7% |" in md, str([(g, r["n_rows"], r["n_not_found"]) for g, r in pnr.items()]))
        check("result.md: the label P is defined by the search, and 'not found' is not called invented",
              "and the search found each of them" in md and "It does not prove that the name is invented." in md
              and "each of them exists" not in md)

        # ---- part 1: two Fisher tests
        fN, fP = p1["fisher_tests_human_labels"]["N"], p1["fisher_tests_human_labels"]["P"]
        # Tables: N [[6, 39], [8, 7]], P [[21, 24], [2, 13]]. Exact fractions 983260357/289098310830 and 1055257463/34011565980.
        check("part 1 Fisher N: table [[6,39],[8,7]] p = 983260357/289098310830 = 0.0034011 (exact fraction, and scipy)",
              approx(fN["p"], 983260357 / 289098310830, 1e-12) and approx(fN["p"], _frac_fisher(6, 39, 8, 7), 1e-12)
              and fN["arm_A"]["count"] == 8 and fN["with_documents"]["count"] == 6, str(fN["p"]))
        check("part 1 Fisher P: table [[21,24],[2,13]] p = 1055257463/34011565980 = 0.0310264 (exact fraction, and scipy)",
              approx(fP["p"], 1055257463 / 34011565980, 1e-12) and approx(fP["p"], 0.03102642976276155, 1e-12), str(fP["p"]))
        va = vd["EE2_a"]
        check("EE2 (a): shown (N lower with documents, p < 0.05, majority N lower too)",
              va["verdict"] == "shown" and [c["holds"] for c in va["conditions"]] == [True, True, True]
              and approx(va["conditions"][2]["arm_A"], 7 / 14) and approx(va["conditions"][2]["with_documents"], 6 / 45))

        # ---- part 2
        check("part 2 X labels: counts for each AI file (2, 3, 1, 1)",
              p2["missing_labels_X"] == {SUPPORT_AI[0]: 2, SUPPORT_AI[1]: 3, SUPPORT_AI[2]: 1, SUPPORT_AI[3]: 1})
        g2 = {r["group"]: r for r in p2["ai_raters"]["gemini"]["views"]["all"]}
        t2 = {r["group"]: r for r in p2["ai_raters"]["gpt"]["views"]["all"]}
        check("part 2 Gemini shares: A n14 S1 G4 U9 (1 X); B n14 S3 G8 U3 (1 X); C n15 S4 G8 U3; documents n44 S11 G24 U9; all n58",
              g2["A"]["n"] == 14 and g2["A"]["counts"] == {"S": 1, "G": 4, "U": 9} and g2["A"]["n_left_out"] == 1
              and g2["B"]["n"] == 14 and g2["B"]["counts"] == {"S": 3, "G": 8, "U": 3}
              and g2["C"]["counts"] == {"S": 4, "G": 8, "U": 3}
              and g2[DOCS]["n"] == 44 and g2[DOCS]["counts"] == {"S": 11, "G": 24, "U": 9} and g2["All arms"]["n"] == 58
              and approx(g2["A"]["proportions"]["U"], 9 / 14))
        check("part 2 GPT shares: A n15 S2 G4 U9; C n14 S8 G4 U2 (1 X); documents n44 S26 G12 U6; all n59 S28 G16 U15",
              t2["A"]["n"] == 15 and t2["A"]["counts"] == {"S": 2, "G": 4, "U": 9}
              and t2["C"]["n"] == 14 and t2["C"]["counts"] == {"S": 8, "G": 4, "U": 2} and t2["C"]["n_left_out"] == 1
              and t2[DOCS]["n"] == 44 and t2[DOCS]["counts"] == {"S": 26, "G": 12, "U": 6}
              and t2["All arms"]["counts"] == {"S": 28, "G": 16, "U": 15})
        gg = group_row(p2["ai_raters"]["gemini"]["views"]["by_model"]["Gemma"], "A")
        # By hand: Gemma arm A for Gemini = R01 R03 R05 R07 R09 U, R11 R13 G, R15 X: S 0 of 7, interval [0, 1 - 0.025^(1/7)].
        check("part 2 model rows and interval: Gemini, Gemma arm A n 7 S0 G2 U5, S interval [0, 1 - 0.025^(1/7)] (by hand)",
              gg["n"] == 7 and gg["counts"] == {"S": 0, "G": 2, "U": 5} and gg["ci95"]["S"][0] == 0.0
              and approx(gg["ci95"]["S"][1], 1 - 0.025 ** (1 / 7), 1e-9)
              and set(p2["ai_raters"]["gemini"]["views"]["by_model"]) == {"Gemma", "Qwen"})
        pp2 = p2["agreement"]["pair_of_record"]
        # By hand: 57 items (R15, R30, R45 out). Same label on 39. Gemini S11 G28 U18, GPT S26 G16 U15.
        # pe = (11*26 + 28*16 + 18*15) / 57^2 = 1004/3249; po = 2223/3249; kappa = 1219/2245.
        check("part 2 kappa: Gemini and GPT n 57, 3 left out, 39 same, kappa 1219/2245 (by hand)",
              pp2["n"] == 57 and pp2["n_left_out_missing"] == 3 and pp2["agreement_count"] == 39
              and approx(pp2["kappa"], 1219 / 2245, 1e-12) and pp2["band"] == "moderate"
              and pp2["kappa_ci95"] is not None and pp2["bootstrap_resamples"] == RESAMPLES, str(pp2["kappa"]))
        check("part 2 3x3 table: rows Gemini, columns GPT sum to 57 with 39 on the diagonal",
              sum(v for row in pp2["table_rows_first_rater_cols_second"].values() for v in row.values()) == 57
              and sum(pp2["table_rows_first_rater_cols_second"][c][c] for c in SUPPORT_LABELS) == 39)
        sa2 = p2["agreement"]["self_agreement_run1_vs_run2"]
        check("part 2 self-agreement: Gemini n 57 (3 out), 56 same, kappa 662/681; GPT n 59, 58 same, kappa 2169/2228",
              sa2["gemini"]["n"] == 57 and sa2["gemini"]["agreement_count"] == 56 and approx(sa2["gemini"]["kappa"], 662 / 681, 1e-12)
              and sa2["gpt"]["n"] == 59 and sa2["gpt"]["agreement_count"] == 58 and approx(sa2["gpt"]["kappa"], 2169 / 2228, 1e-12))
        ba = {r["group"]: r for r in p2["both_agree"]["rows"]}
        # By hand: A agrees on 14 (U9 G4 S1, R15 X); each document arm on U o+1..2, G o+4..6, S o+12..15 (9),
        # less R30 (B) and R45 (C) that have an X: B 8, C 8, D 9. Documents n 25: S10 G9 U6.
        check("part 2 'both agree': 39 items; A n14 S1 G4 U9; B n8 S3 G3 U2; documents n25 S10 G9 U6 (by hand)",
              p2["both_agree"]["n"] == 39 and ba["A"]["n"] == 14 and ba["A"]["counts"] == {"S": 1, "G": 4, "U": 9}
              and ba["B"]["n"] == 8 and ba["B"]["counts"] == {"S": 3, "G": 3, "U": 2} and ba["B"]["n_left_out"] == 7
              and ba[DOCS]["n"] == 25 and ba[DOCS]["counts"] == {"S": 10, "G": 9, "U": 6} and ba["All arms"]["n"] == 39)
        f2 = p2["fisher_tests"]
        # Exact fractions: Gemini U [[9,35],[9,5]] 1023866/174457185; Gemini S [[11,33],[1,13]] 133337107/515786460;
        # GPT U [[6,38],[9,6]] 22680271/23330740874; GPT S [[26,18],[2,13]] 183033/69957244.
        check("part 2 Fisher, Gemini U: p = 1023866/174457185 = 0.0058689 (exact fraction)",
              approx(f2["gemini"]["U"]["p"], 1023866 / 174457185, 1e-12) and approx(f2["gemini"]["U"]["p"], _frac_fisher(9, 35, 9, 5), 1e-12))
        check("part 2 Fisher, Gemini S: p = 133337107/515786460 = 0.2585122 (exact fraction)",
              approx(f2["gemini"]["S"]["p"], 133337107 / 515786460, 1e-12))
        check("part 2 Fisher, GPT U: p = 22680271/23330740874 = 0.0009721 (exact fraction)",
              approx(f2["gpt"]["U"]["p"], 22680271 / 23330740874, 1e-12))
        check("part 2 Fisher, GPT S: p = 183033/69957244 = 0.0026164 (exact fraction)",
              approx(f2["gpt"]["S"]["p"], 183033 / 69957244, 1e-12) and approx(f2["gpt"]["S"]["p"], _frac_fisher(26, 18, 2, 13), 1e-12))
        vb, ve = vd["EE2_b"], vd["EE3_level2"]
        check("EE2 (b): shown by two AI raters (both U lower with documents, both p < 0.05)",
              vb["verdict"] == "shown by two AI raters" and all(c["holds"] for c in vb["conditions"]) and len(vb["conditions"]) == 4)
        check("EE3 level 2: not shown for correctness when only GPT passes (Gemini S higher but p 0.26)",
              ve["verdict"] == "not shown for correctness" and ve["raters"]["gpt"]["passes"] and not ve["raters"]["gemini"]["passes"]
              and [c["holds"] for c in ve["conditions"]] == [True, False, True, True])

        # ---- the Markdown result
        check("result.md: has the three verdicts",
              "**Verdict EE2 (a): shown.**" in md
              and f"**Verdict EE2 (b): shown by two AI raters ({NO_HUMAN}).**" in md
              and f"**Verdict EE3 level 2: not shown for correctness ({NO_HUMAN}).**" in md)
        check("result.md: wording of lock 7.11 and 7.12 (one human rater and two AI raters; two AI raters and no human rater; "
              "not delivered; not proof)",
              "## Part 1. The names (one human rater and two AI raters)" in md
              and "## Part 2. The support (two AI raters and no human rater)" in md
              and "The registered validation by two human reviewers is not delivered." in md
              and "is not proof that there is no difference" in md)
        part2_text = md[md.index("## Part 2."):md.index("## Verdicts")]
        ver_b = md[md.index("### EE2 (b)"):]
        p2_lines = (part2_text + "\n" + ver_b).splitlines()
        prose = [ln for ln in p2_lines if ln.strip() and not ln.startswith(("|", "#"))]
        sentences = [s for ln in prose for s in re.split(r"(?<=\.)\**\s+", ln.strip()) if s]
        no_phrase = [s for s in sentences if NO_HUMAN not in s.lower() and not s.startswith(PART2_METHOD_SENTENCES)]
        check("result.md: each sentence about a part 2 result says that no human expert checked these labels "
              "(only the listed method sentences do not)",
              len(sentences) > 20 and not no_phrase, str(no_phrase[:3]))
        heads3 = [ln for ln in p2_lines if ln.startswith(("## ", "### "))]
        check("result.md: each part 2 heading names the AI raters or says that no human expert checked these labels",
              heads3 and all(NO_HUMAN in h.lower() or "two AI raters and no human rater" in h for h in heads3), str(heads3))
        check("result.md: names the item with no majority, and the script label agreement",
              "Items with no majority: R01." in md and "the same as the human label: 58 of 60 (raw agreement 96.7%)" in md)

        # ---- rounding of review_result.md (analysis_lock.md 3.5); the JSON keeps full precision
        check("rounding: formatters (percent 1 decimal, p 4 decimals or 'p < 0.0001', kappa 2 decimals, 'not defined')",
              pct(1 / 3) == "33.3%" and pct(0.2) == "20.0%" and pct(None) == "not defined" and pct(0.0625) == "6.2%"
              and pct_ci((0.016575913440083623, 0.40460269660337206)) == "[1.7%, 40.5%]" and pct_ci(None) == "not defined"
              and p_txt(0.00340112799060314) == "p = 0.0034" and p_txt(0.00009999) == "p < 0.0001"
              and p_txt(0.0001) == "p = 0.0001" and p_txt(1.0) == "p = 1.0000" and p_txt(None) == "not defined"
              and kap(1013 / 1100) == "0.92" and kap(-0.0512) == "-0.05" and kap(-0.004) == "0.00" and kap(None) == "not defined"
              and kap_ci((0.71234, 0.98765)) == "[0.71, 0.99]" and kap_ci(None) == "not defined")
        check("rounding: result.md shows human arm A P as 13.3% [1.7%, 40.5%] and the N rate with documents as 13.3% [5.1%, 26.8%]",
              "| A | 15 | 2 | 13.3% [1.7%, 40.5%] | 8 | 53.3% [" in md and "| 6 | 13.3% [5.1%, 26.8%] |" in md)
        check("rounding: result.md shows the Fisher p values 0.0034, 0.0310, 0.0059, 0.2585, 0.0010 and 0.0026",
              all(f"| p = {v} |" in md for v in ("0.0034", "0.0310", "0.0059", "0.2585", "0.0010", "0.0026")))
        check("rounding: result.md shows the kappa row human and Gemini (58, 2, 55, 94.8%, 0.92)",
              "| Human and Gemini | 58 | 2 | 55 | 94.8% | 0.92 | [" in md and "| Gemini and GPT | 57 | 3 | 39 | 68.4% | 0.54 | [" in md)

        def number_format_bad(text: str) -> list[str]:
            """Each number with a decimal point in a table row has the locked form."""
            bad = []
            for line in text.splitlines()[1:]:
                if not line.startswith("|"):
                    continue
                for mt in re.finditer(r"(p [=<] )?(-?\d+)\.(\d+)(%?)", line):
                    pre, dec, pc = mt.group(1), len(mt.group(3)), mt.group(4)
                    if not ((pc and dec == 1) or (pre and dec == 4) or (not pc and not pre and dec == 2)):
                        bad.append(mt.group(0))
            return bad

        bad_nums = number_format_bad(md)
        check("rounding: every number in the tables of result.md has the locked form", not bad_nums, str(bad_nums[:5]))
        check("rounding: result.json keeps full precision", fN["p"] == 983260357 / 289098310830 or len(repr(fN["p"])) > 8)

        # ---- second score refused, --again works and is recorded
        snap = rp.read_bytes()
        code, out, err = run_cli(["score", "--dir", str(d)])
        check("score: a second run is refused and changes nothing",
              code == 1 and "already exists" in err and rp.read_bytes() == snap and len(json.loads(locks_path(d).read_text())["key_openings"]) == 1)
        code, out, err = run_cli(["score", "--dir", str(d), "--again"])
        lk = json.loads(locks_path(d).read_text())
        check("score --again: runs and records the new opening",
              code == 0 and len(lk["key_openings"]) == 2 and lk["key_opened_at_utc"] == lk["key_openings"][0], err)
        res_again = json.loads(rp.read_text())
        check("score --again: the same numbers", res_again["part1_names"] == p1 and res_again["part2_support"] == p2)
        code, out, err = run_cli(["lock", HUMAN, "--dir", str(d)])
        check("lock after the key was opened: refused", code == 1)

        # ---------------- verdicts in the other directions
        def scenario(tag: str, files: dict) -> dict:
            dd = _make_dir(root, tag, files, SCRIPT)
            _lock_all(dd)
            c, o, e = run_cli(["score", "--dir", str(dd)])
            assert c == 0, e
            return json.loads((dd / "result" / "review_result.json").read_text())

        REV1 = _pat("N" * 2 + "G" * 6 + "P" * 7, "N" * 8 + "G" * 5 + "P" * 2)  # N higher with documents
        TS_FULL = _pat("U" * 9 + "G" * 4 + "S" * 2, "U" * 2 + "G" * 4 + "S" * 9)  # no X
        # Scenario 1: part 1 reversed for all raters; part 2 both AI raters with the GPT pattern.
        # Gemini has X at R45 and R46; GPT has X at R45. So R45 has X for both raters.
        GS_R = {**TS_FULL, "R45": "X", "R46": "X"}
        r1 = scenario("rev", files_for(REV1, REV1, REV1, REV1, REV1, GS_R, TS, GS_R, TS))
        v = r1["verdicts"]
        check("EE2 (a): not shown when N is higher with documents (p < 0.05, wrong direction)",
              v["EE2_a"]["verdict"] == "not shown" and [c["holds"] for c in v["EE2_a"]["conditions"]] == [False, True, False]
              and approx(v["EE2_a"]["conditions"][1]["p"], _frac_fisher(24, 21, 2, 13), 1e-12))
        # By hand: Gemini documents S 26 of 43 (R45 S and R46 U are X), arm A S 2 of 15.
        check("EE3 level 2: shown by two AI raters when both raters pass",
              v["EE3_level2"]["verdict"] == "shown by two AI raters" and all(c["holds"] for c in v["EE3_level2"]["conditions"])
              and approx(r1["part2_support"]["fisher_tests"]["gemini"]["S"]["p"], _frac_fisher(26, 17, 2, 13), 1e-12))
        # By hand: the two raters agree on each item except R45 (X for both) and R46 (X for Gemini): 58 items.
        b1 = r1["part2_support"]["both_agree"]
        check("part 2 'both agree': an item with X for both raters is not an agreement (58 items, D n 14)",
              b1["n"] == 58 and group_row(b1["rows"], "C")["n"] == 14 and group_row(b1["rows"], "D")["n"] == 14
              and r1["part2_support"]["agreement"]["pair_of_record"]["n"] == 58)
        # Scenario 2: the human labels show the effect, the two AI raters agree on the reverse, so the majority labels point the other way.
        # Part 2: Gemini reversed (U higher with documents), GPT passes.
        GS_REV = _pat("U" * 3 + "G" * 6 + "S" * 6, "U" * 9 + "G" * 4 + "S" * 2)
        r2 = scenario("conflict", files_for(H1, REV1, REV1, REV1, REV1, GS_REV, TS, GS_REV, TS))
        v = r2["verdicts"]
        check("EE2 (a): not shown when the majority labels point the other way (human conditions hold)",
              v["EE2_a"]["verdict"] == "not shown" and [c["holds"] for c in v["EE2_a"]["conditions"]] == [True, True, False])
        check("EE2 (b): not shown when only GPT passes (Gemini U higher with documents)",
              v["EE2_b"]["verdict"] == "not shown" and v["EE2_b"]["raters"]["gpt"]["passes"]
              and not v["EE2_b"]["raters"]["gemini"]["passes"] and v["EE2_b"]["conditions"][0]["holds"] is False)
        check("EE3 level 2: not shown for correctness when Gemini S is lower with documents",
              v["EE3_level2"]["verdict"] == "not shown for correctness" and not v["EE3_level2"]["raters"]["gemini"]["passes"])
        # Scenario 3: the right direction, but p >= 0.05 for N (A N 4/15, documents N 6/45).
        NS1 = _pat("N" * 4 + "G" * 5 + "P" * 6, "N" * 2 + "G" * 6 + "P" * 7)
        r3 = scenario("nosig", files_for(NS1, NS1, NS1, NS1, NS1, GS, TS, GS2, TS2))
        v = r3["verdicts"]["EE2_a"]
        check("EE2 (a): not shown when the direction is right but p >= 0.05",
              v["verdict"] == "not shown" and [c["holds"] for c in v["conditions"]] == [True, False, True]
              and approx(v["conditions"][1]["p"], _frac_fisher(6, 39, 4, 11), 1e-12) and v["conditions"][1]["p"] >= 0.05)

        # ---------------- edge cases: constant labels, a part 2 rater with only X, a very small p
        allG = {f"R{i:02d}": "G" for i in range(1, 61)}
        allX = {f"R{i:02d}": "X" for i in range(1, 61)}
        TINY = _pat("U" * 15, "G" * 6 + "S" * 9)
        dd = _make_dir(root, "edge", files_for(allG, allG, allG, allG, allG, TINY, allX, TINY, allX), SCRIPT)
        _lock_all(dd)
        c, o, e = run_cli(["score", "--dir", str(dd)])
        re_ = json.loads((dd / "result" / "review_result.json").read_text()) if c == 0 else {}
        mde = (dd / "result" / "review_result.md").read_text() if c == 0 else ""
        if c == 0:
            pe1 = re_["part1_names"]["agreement"]["pairs_of_record"][0]
            g_ = re_["part2_support"]["ai_raters"]["gpt"]
            check("edge: constant labels give kappa 'not defined', Fleiss 'not defined', and EE2 (a) 'not shown'",
                  pe1["kappa"] is None and pe1["band"] == "not defined"
                  and re_["part1_names"]["agreement"]["fleiss_kappa_three_raters"]["kappa"] is None
                  and re_["verdicts"]["EE2_a"]["verdict"] == "not shown" and "| not defined | not defined |" in mde)
            check("edge: a part 2 file with only X gives n 0, 'not defined' and verdicts 'not shown', with no crash",
                  g_["n_valid"] == 0 and g_["n_missing_X"] == 60 and re_["part2_support"]["agreement"]["pair_of_record"]["n"] == 0
                  and re_["part2_support"]["fisher_tests"]["gpt"]["U"]["p"] is None
                  and re_["verdicts"]["EE2_b"]["verdict"] == "not shown"
                  and re_["verdicts"]["EE3_level2"]["verdict"] == "not shown for correctness"
                  and "| A | 0 | 0 | not defined |" in mde and re_["part2_support"]["both_agree"]["n"] == 0
                  and group_row(g_["views"]["all"], "A")["ci95"]["S"] is None)
            check("rounding: a Fisher p below 0.0001 shows as 'p < 0.0001' (the JSON keeps the value)",
                  re_["part2_support"]["fisher_tests"]["gemini"]["U"]["p"] < 0.0001 and "| p < 0.0001 |" in mde)
            check("edge: every number in the tables has the locked form", not number_format_bad(mde))
        else:
            check("edge: score runs on constant labels and an all-X file", False, e)

    _SELFTEST_ROOT = None
    failed = [r for r in results if not r[1]]
    print(f"\nself-test: {len(results) - len(failed)} of {len(results)} checks pass")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--dir", default=str(DEFAULT_DIR), help="review folder (default: review_v2)")
    ap = argparse.ArgumentParser(description="Scoring tool for the blinded review of Study 2 (two parts).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lock", parents=[parent], help="lock one label file")
    p.add_argument("file", help=f"label file name, for example {HUMAN}")
    sub.add_parser("status", parents=[parent], help="show the state of the nine label files")
    p = sub.add_parser("score", parents=[parent], help="open the key and write the result")
    p.add_argument("--again", action="store_true", help="run again although a result exists (recorded)")
    sub.add_parser("self-test", parents=[parent], help="test the tool on invented data in a temporary folder")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "self-test":
            code = self_test()
            if code:
                raise SystemExit(code)
            return
        {"lock": cmd_lock, "status": cmd_status, "score": cmd_score}[args.cmd](args)
    except Refuse as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
