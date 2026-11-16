"""Helpers for blind judging: a batch builder and a scorer.

build_batches reads the records of a lab run, draws requirements of all prompt versions, mixes
them, gives them neutral ids and writes batch files for the judges plus a private key.
score turns the labels of the judges into numbers for each version.

The module calls no model and no network. Every write goes through the guard. It does not
import run_tuning, prompts or lab_measures.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from .guard import (
    DEFAULT_GUARD,
    WriteGuard,
    assert_no_private_text,
    guarded_mkdir,
    guarded_write_text,
)
from .pairstats import paired_summary
from .paths import MODELS
from .records import is_final, key_states, read_jsonl, record_key

ROUND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
LABELS: tuple[str, ...] = ("S", "G", "U")
U_KINDS: tuple[str, ...] = (
    "unsupported_detail",
    "wrong_purpose_or_case",
    "name_not_found",
    "wrong_fact",
    "wrong_name_form",
    "not_usable",
)
NONE_KIND = "none"
BASELINE = "v0_old"
JUDGING_DIR = "judging"
METRICS: tuple[str, ...] = ("u_share", "s_share", "g_share", "net")
ITEM_TEXT_FUNCTION = "fmt_requirement(req, md=False) of scripts/build_study2_review_sheet_v2.py"
# Words that must not appear in a batch outside the item texts (compared in lower case).
FIXED_NEEDLES: tuple[str, ...] = ("v0", "arm", "version", "model", "repeat", "b2")
ITEM_FORM = re.compile(
    r"\A\*\*Title\.\*\* [^\n]*\n\n\*\*Description\.\*\* [^\n]*\n\n\*\*Rationale\.\*\* [^\n]*\n\n"
    r"\*\*Acceptance criteria\.\*\*\n(?:- [^\n]*\n)+\n\*\*Implementation notes\.\*\* [^\n]*\n\Z"
)
_QID_RE = re.compile(r"^(?P<story>.+)-q(?P<n>\d{2,})$")


class JudgingError(Exception):
    """A refusal of the judging helpers. The message says which file, story or qid."""


# ---------------------------------------------------------------------------------------------
# Copied without change from scripts/build_study2_review_sheet_v2.py (clean, md_safe,
# fmt_requirement). A test compares them with the text of that file.
# ---------------------------------------------------------------------------------------------
def clean(value) -> str:
    return " ".join(str(value or "").split())


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


def item_text(req: Mapping) -> str:
    """The text of one item: only title, description, rationale, criteria and notes."""
    return fmt_requirement(dict(req), md=False)


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------
def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) + "\n"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _read_json(path: Path, what: str) -> Any:
    p = Path(path)
    if not p.is_file():
        raise JudgingError(f"{what}: the file {p.name} does not exist")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JudgingError(f"{what}: {p.name} is not valid JSON ({exc})") from exc


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _model_order(models: Sequence[str]) -> list[str]:
    known = [m for m in MODELS if m in models]
    return known + sorted(set(models) - set(known))


# ---------------------------------------------------------------------------------------------
# Reading the records of a run
# ---------------------------------------------------------------------------------------------
def _load_records(run_dir: Path) -> tuple[dict[tuple, dict], dict[str, str]]:
    """Return (last ok record for each key, SHA-256 of each records file). Only lab records."""
    run_dir = Path(run_dir)
    files = sorted(run_dir.glob("*/*/records.jsonl"))
    if not files:
        raise JudgingError(f"no records.jsonl below the run folder {run_dir.name}")
    latest: dict[tuple, dict] = {}
    hashes: dict[str, str] = {}
    for path in files:
        rel = path.relative_to(run_dir).as_posix()
        hashes[rel] = _sha256_file(path)
        for rec in read_jsonl(path):
            if rec.get("step") != "lab":
                raise JudgingError(f"{rel}: a record has step {rec.get('step')!r}; only lab records are allowed")
            latest[record_key(rec)] = rec
    kept = {k: r for k, r in latest.items() if r.get("error") is None}
    return kept, hashes


def _excluded_units(run_dir: Path, exclude_rounds: Sequence[str]) -> set[tuple]:
    out: set[tuple] = set()
    for name in exclude_rounds:
        if not ROUND_RE.match(name):
            raise JudgingError(f"exclude_rounds: bad round name {name!r}")
        key = _read_json(Path(run_dir) / JUDGING_DIR / name / "private" / "key.json", f"exclude round {name}")
        for entry in key.values():
            out.add((entry["version"], entry["model"], entry["story_id"], entry["repeat"], entry["requirement_index"]))
    return out


# ---------------------------------------------------------------------------------------------
# The draw
# ---------------------------------------------------------------------------------------------
def _draw_cell(free: dict[int, list[int]], per_cell: int, rng: random.Random) -> list[tuple[int, int]]:
    """Draw per_cell units (repeat, index) spread over the repeats. free: repeat -> free indices."""
    total = sum(len(v) for v in free.values())
    if per_cell >= total:
        return sorted((r, i) for r, idx in free.items() for i in idx)
    repeats = sorted(r for r, idx in free.items() if idx)
    base, rest = divmod(per_cell, len(repeats))
    order = rng.sample(repeats, len(repeats))
    quota = {r: base for r in repeats}
    for r in order[:rest]:
        quota[r] += 1
    give = {r: min(quota[r], len(free[r])) for r in repeats}
    shortfall = per_cell - sum(give.values())
    while shortfall > 0:
        moved = False
        for r in order:
            if shortfall > 0 and give[r] < len(free[r]):
                give[r] += 1
                shortfall -= 1
                moved = True
        if not moved:  # cannot happen when per_cell < total; stop loudly if it does
            raise JudgingError("internal error: the draw could not place all units")
    picked: list[tuple[int, int]] = []
    for r in repeats:
        for i in rng.sample(sorted(free[r]), give[r]):
            picked.append((r, i))
    return sorted(picked)


@dataclass(frozen=True)
class BatchDesign:
    round_dir: Path
    n_items: int
    n_items_by_story: dict[str, int]
    batch_paths: tuple[Path, ...]
    key_path: Path
    design_path: Path
    deviations: tuple[str, ...]


def build_batches(
    run_dir: Path,
    versions: Sequence[str],
    stories: Sequence[str],
    per_cell: int,
    seed: int,
    round_name: str,
    *,
    models: Sequence[str] | None = None,
    exclude_rounds: Sequence[str] = (),
    allow_short: bool = False,
    guard: WriteGuard = DEFAULT_GUARD,
) -> BatchDesign:
    """Draw requirements of all versions, mix them, write batches, a private key and a design file."""
    run_dir = Path(run_dir)
    if not ROUND_RE.match(round_name or ""):
        raise JudgingError(f"bad round name {round_name!r}")
    if not isinstance(per_cell, int) or isinstance(per_cell, bool) or per_cell < 1:
        raise JudgingError("per_cell must be an integer of 1 or more")
    versions, stories = list(versions), list(stories)
    if not versions or len(set(versions)) != len(versions):
        raise JudgingError("versions must be a non-empty list without duplicates")
    if not stories or len(set(stories)) != len(stories):
        raise JudgingError("stories must be a non-empty list without duplicates")
    round_dir = run_dir / JUDGING_DIR / round_name
    if round_dir.exists():
        raise JudgingError(f"the round folder {round_name} exists already")

    kept, hashes = _load_records(run_dir)
    found_models = _model_order(sorted({k[1] for k in kept}))
    use_models = list(models) if models is not None else found_models
    if not use_models:
        raise JudgingError("no model to draw from")
    for kind, wanted, have in (
        ("version", versions, {k[0] for k in kept}),
        ("story", stories, {k[2] for k in kept}),
        ("model", use_models, {k[1] for k in kept}),
    ):
        for w in wanted:
            if w not in have:
                raise JudgingError(f"{kind} {w!r} has no record with an answer in {run_dir.name}")

    excluded = _excluded_units(run_dir, exclude_rounds)
    cells: dict[tuple[str, str, str], dict[int, list[int]]] = {}
    projects: dict[str, str] = {}
    recs: dict[tuple, dict] = {}
    for (version, model, story, _arm, repeat), rec in kept.items():
        if version not in versions or story not in stories or model not in use_models:
            continue
        projects[story] = rec["project"]
        recs[(story, version, model, repeat)] = rec
        free = [
            i for i in range(len(rec.get("requirements") or []))
            if (version, model, story, repeat, i) not in excluded
        ]
        cells.setdefault((story, version, model), {})[repeat] = free

    deviations: list[str] = []
    items_by_story: dict[str, list[tuple]] = {s: [] for s in stories}
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for story in stories:
        for version in versions:
            for model in use_models:
                free = cells.get((story, version, model), {})
                total = sum(len(v) for v in free.values())
                if total < per_cell:
                    if not allow_short:
                        raise JudgingError(
                            f"story {story}, version {version}, model {model}: {total} free requirements, "
                            f"{per_cell} wanted (use allow_short to take all of them)"
                        )
                    deviations.append(
                        f"{story} {version} {model}: wanted {per_cell}, got {total} (all free requirements taken)"
                    )
                rng = random.Random(f"{seed}|{round_name}|{story}|{version}|{model}")
                picked = _draw_cell(free, per_cell, rng) if total else []
                counts.setdefault(story, {}).setdefault(version, {})[model] = len(picked)
                for repeat, idx in picked:
                    items_by_story[story].append((version, model, repeat, idx))

    batches: dict[str, dict] = {}
    key: dict[str, dict] = {}
    for story in stories:
        units = sorted(items_by_story[story])
        if not units:
            raise JudgingError(f"story {story}: no requirement could be drawn")
        random.Random(f"{seed}|{round_name}|{story}|mix").shuffle(units)
        items = []
        for n, (version, model, repeat, idx) in enumerate(units, start=1):
            qid = f"{story}-q{n:02d}"
            rec = recs[(story, version, model, repeat)]
            items.append({"qid": qid, "text": item_text(rec["requirements"][idx])})
            key[qid] = {
                "story_id": story, "project": projects[story], "version": version,
                "model": model, "repeat": repeat, "requirement_index": idx,
            }
        batches[story] = {"story_id": story, "project": projects[story], "n_items": len(items), "items": items}

    guarded_mkdir(round_dir / "batches", guard=guard)
    guarded_mkdir(round_dir / "private", guard=guard)
    batch_paths = []
    for story in sorted(batches):
        batch_paths.append(guarded_write_text(round_dir / "batches" / f"{story}.json", _dump(batches[story]), guard=guard))
    key_path = guarded_write_text(round_dir / "private" / "key.json", _dump(key), guard=guard)

    checks = _check_round(batch_paths, key_path, kept, set(versions) | {k[0] for k in kept}, set(use_models) | {k[1] for k in kept})

    design = {
        "schema_version": 1,
        "round": round_name,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": seed,
        "per_cell": per_cell,
        "versions": versions,
        "models": use_models,
        "stories": stories,
        "exclude_rounds": list(exclude_rounds),
        "records_sha256": hashes,
        "counts": counts,
        "deviations": deviations,
        "item_text_function": ITEM_TEXT_FUNCTION,
        "checks": checks,
    }
    design_path = guarded_write_text(round_dir / "private" / "design.json", _dump(design), guard=guard)
    return BatchDesign(
        round_dir=Path(round_dir),
        n_items=sum(len(b["items"]) for b in batches.values()),
        n_items_by_story={s: len(batches[s]["items"]) for s in sorted(batches)},
        batch_paths=tuple(Path(p) for p in batch_paths),
        key_path=Path(key_path),
        design_path=Path(design_path),
        deviations=tuple(deviations),
    )


def _check_round(
    batch_paths: Sequence[Path], key_path: Path, kept: Mapping[tuple, dict],
    version_names: set[str], model_names: set[str],
) -> list[dict]:
    """Read the written files back and check them. Any failure raises JudgingError."""
    key = _read_json(key_path, "key")
    needles = {n.lower() for n in FIXED_NEEDLES}
    needles |= {v.lower() for v in version_names} | {m.lower() for m in model_names}
    for m in model_names:
        word = re.match(r"[A-Za-z]+", m)
        if word:
            needles.add(word.group(0).lower())
    problems: list[str] = []
    seen_qids: set[str] = set()
    seen_units: set[tuple] = set()
    text_hits: dict[str, list[str]] = {}
    for path in batch_paths:
        data = _read_json(path, "batch")
        name = Path(path).name
        if list(data) != ["story_id", "project", "n_items", "items"]:
            problems.append(f"{name}: the keys are {list(data)}")
            continue
        items = data["items"]
        if data["n_items"] != len(items):
            problems.append(f"{name}: n_items is not the length of items")
        if Path(path).stem != data["story_id"]:
            problems.append(f"{name}: the file name is not the story id")
        for n, it in enumerate(items, start=1):
            qid = it.get("qid")
            if list(it) != ["qid", "text"]:
                problems.append(f"{name}: item {n} has the keys {list(it)}")
                continue
            if qid != f"{data['story_id']}-q{n:02d}":
                problems.append(f"{name}: item {n} has the id {qid!r}")
            if qid in seen_qids:
                problems.append(f"{name}: qid {qid} is not unique")
            seen_qids.add(qid)
            entry = key.get(qid)
            if entry is None:
                problems.append(f"{name}: qid {qid} is not in the key")
                continue
            unit = (entry["version"], entry["model"], entry["story_id"], entry["repeat"], entry["requirement_index"])
            if unit in seen_units:
                problems.append(f"{name}: the unit of {qid} is drawn two times")
            seen_units.add(unit)
            recs = [
                r for k, r in kept.items()
                if k[0] == entry["version"] and k[1] == entry["model"] and k[2] == entry["story_id"]
                and k[4] == entry["repeat"]
            ]
            if len(recs) != 1:
                problems.append(f"{name}: {qid} points to {len(recs)} records")
            elif item_text(recs[0]["requirements"][entry["requirement_index"]]) != it["text"]:
                problems.append(f"{name}: the text of {qid} differs from its source")
            if not ITEM_FORM.match(it["text"]):
                problems.append(f"{name}: the text of {qid} does not have the item form")
            low = it["text"].lower()
            for needle in needles:
                if needle in low:
                    text_hits.setdefault(needle, []).append(qid)
        skeleton = json.dumps(
            {k: ([{kk: ("" if kk == "text" else vv) for kk, vv in it.items()} for it in v] if k == "items" else v)
             for k, v in data.items()},
            ensure_ascii=False,
        ).lower()
        for needle in sorted(needles):
            if needle in skeleton:
                problems.append(f"{name}: the word {needle!r} is in the batch outside the item texts")
        if any(needle in name.lower() for needle in needles):
            problems.append(f"{name}: a leak word is in the file name")
    if set(key) != seen_qids:
        problems.append("the key and the batches do not have the same qids")
    if problems:
        raise JudgingError("round checks failed: " + "; ".join(problems[:8]))
    hits = "; ".join(f"{n!r} in {', '.join(q)}" for n, q in sorted(text_hits.items())) or "none"
    return [
        {"name": "batch_structure", "ok": True, "detail": f"{len(batch_paths)} batch files, {len(seen_qids)} items"},
        {"name": "key_covers_every_qid", "ok": True, "detail": f"{len(key)} entries"},
        {"name": "text_equals_source", "ok": True, "detail": "each item text equals item_text of its record"},
        {"name": "no_unit_twice", "ok": True, "detail": f"{len(seen_units)} different units"},
        {"name": "no_leak_word_outside_text", "ok": True, "detail": "version, model and fixed words not in any skeleton"},
        {"name": "text_hits", "ok": True, "detail": "leak words inside a requirement text (permitted): " + hits},
    ]


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------
def _load_judgments(path: Path, key_by_story: Mapping[str, list[str]]) -> tuple[dict, list[str]]:
    """Validate the judgments file. Return ({story: {judge: {qid: entry}}}, warnings)."""
    raw = _read_json(path, "judgments")
    if not isinstance(raw, dict) or not raw:
        raise JudgingError("judgments: the file must hold a non-empty object {story_id: {judge: [...]}}")
    warnings: list[str] = []
    out: dict[str, dict[str, dict[str, dict]]] = {}
    judge_sets: dict[str, frozenset[str]] = {}
    for story, by_judge in raw.items():
        if story not in key_by_story:
            raise JudgingError(f"judgments: story {story!r} is not in the key")
        if not isinstance(by_judge, dict) or not by_judge:
            raise JudgingError(f"judgments: story {story} has no judge")
        judge_sets[story] = frozenset(by_judge)
        wanted = set(key_by_story[story])
        out[story] = {}
        for judge, entries in by_judge.items():
            if not isinstance(entries, list):
                raise JudgingError(f"judgments: story {story}, judge {judge}: not a list")
            seen: dict[str, dict] = {}
            for e in entries:
                if not isinstance(e, dict):
                    raise JudgingError(f"judgments: story {story}, judge {judge}: an entry is not an object")
                qid = e.get("qid")
                where = f"story {story}, judge {judge}, qid {qid}"
                missing = [f for f in ("qid", "label", "kind", "claim", "why", "doubt") if f not in e]
                if missing:
                    raise JudgingError(f"judgments: {where}: missing fields {missing}")
                if qid in seen:
                    raise JudgingError(f"judgments: {where}: duplicate qid")
                if qid not in wanted:
                    raise JudgingError(f"judgments: {where}: extra qid (not a qid of the story)")
                if e["label"] not in LABELS:
                    raise JudgingError(f"judgments: {where}: bad label {e['label']!r}")
                valid_kinds = (NONE_KIND, *U_KINDS)
                if e["kind"] not in valid_kinds:
                    raise JudgingError(f"judgments: {where}: bad kind {e['kind']!r}")
                if e["label"] in ("S", "G") and e["kind"] != NONE_KIND:
                    raise JudgingError(f"judgments: {where}: label {e['label']} needs kind none")
                if not isinstance(e["claim"], str) or not isinstance(e["why"], str):
                    raise JudgingError(f"judgments: {where}: claim and why must be strings")
                if not isinstance(e["doubt"], bool):
                    raise JudgingError(f"judgments: {where}: doubt must be true or false")
                if e["label"] == "U" and e["kind"] == NONE_KIND:
                    warnings.append(f"{qid} (judge {judge}): label U with kind none")
                seen[qid] = e
            lacking = sorted(wanted - set(seen))
            if lacking:
                raise JudgingError(f"judgments: story {story}, judge {judge}: missing qids {lacking[:5]}")
            out[story][judge] = seen
    if len(set(judge_sets.values())) != 1:
        raise JudgingError("judgments: the stories do not have the same set of judges")
    return out, warnings


def _kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[lab] / n) * (cb[lab] / n) for lab in LABELS)
    if pe >= 1.0 - 1e-12:
        return None
    return (po - pe) / (1.0 - pe)


def _agreement(qids: Sequence[str], labels: Mapping[str, Mapping[str, str]], judges: Sequence[str]) -> dict:
    """labels: judge -> qid -> label. Agreement of all judges over the given qids."""
    n = len(qids)
    unanimous = sum(1 for q in qids if len({labels[j][q] for j in judges}) == 1)
    pairs = []
    kappas = []
    for a, b in combinations(judges, 2):
        la, lb = [labels[a][q] for q in qids], [labels[b][q] for q in qids]
        equal = sum(1 for x, y in zip(la, lb) if x == y)
        k = _kappa(la, lb) if n else None
        if k is not None:
            kappas.append(k)
        pairs.append({
            "judges": [a, b], "n_items": n, "n_equal": equal,
            "share_equal": (equal / n) if n else None, "kappa": k,
        })
    return {
        "n_items": n, "n_unanimous": unanimous, "share_unanimous": (unanimous / n) if n else None,
        "pairs": pairs, "mean_kappa": _mean(kappas) if kappas else None,
    }


def _failed_counts(round_dir: Path, versions: Sequence[str], models: Sequence[str]) -> tuple[dict | None, list[str]]:
    """Count final failed keys for each (version, model) from the records that design.json hashes.

    A failed generation has no requirement to draw, so the draw cannot see it. Returns
    (counts or None, notes). A missing design or records file gives None and a note, never a silent skip.
    """
    design_path = round_dir / "private" / "design.json"
    if not design_path.is_file():
        return None, ["Failed generations are not counted: private/design.json is missing."]
    design = _read_json(design_path, "design")
    run_dir = round_dir.parent.parent
    notes: list[str] = []
    records: list[dict] = []
    for rel, digest in sorted((design.get("records_sha256") or {}).items()):
        path = run_dir / rel
        if not path.is_file():
            return None, [f"Failed generations are not counted: records file {rel} is missing."]
        if _sha256_file(path) != digest:
            notes.append(f"The records file {rel} changed after the draw. Failed counts use its present content.")
        records.extend(read_jsonl(path))
    max_attempts = 3
    manifest = run_dir / "manifest.json"
    if manifest.is_file():
        max_attempts = int((_read_json(manifest, "manifest").get("options") or {}).get("max_attempts") or 3)
    states = key_states(records)
    counts = {(v, m): 0 for v in versions for m in models}
    for k, st in states.items():
        if (k[0], k[1]) in counts and not st.ok and is_final(st, max_attempts):
            counts[(k[0], k[1])] += 1
    return counts, notes


def score(
    judgments_path: Path,
    key_path: Path,
    *,
    out_dir: Path | None = None,
    baseline: str = BASELINE,
    guard: WriteGuard = DEFAULT_GUARD,
) -> dict[str, Any]:
    """Turn the labels of the judges into numbers. Writes scores.json and scores.md. Returns the scores."""
    key_path = Path(key_path)
    key = _read_json(key_path, "key")
    round_dir = key_path.parent.parent
    out_dir = Path(out_dir) if out_dir is not None else round_dir
    key_by_story: dict[str, list[str]] = {}
    for qid in sorted(key):
        key_by_story.setdefault(key[qid]["story_id"], []).append(qid)

    judgments, warnings = _load_judgments(Path(judgments_path), key_by_story)
    stories = sorted(judgments)
    not_scored = sorted(set(key_by_story) - set(stories))
    judges = sorted(next(iter(judgments.values())))
    models = _model_order(sorted({key[q]["model"] for s in stories for q in key_by_story[s]}))
    versions = sorted({key[q]["version"] for s in stories for q in key_by_story[s]})

    # Cells: (story, version, model) -> qids
    cell_qids: dict[tuple[str, str, str], list[str]] = {}
    for s in stories:
        for q in key_by_story[s]:
            cell_qids.setdefault((s, key[q]["version"], key[q]["model"]), []).append(q)

    def shares(counts: Mapping[str, int], n: int) -> dict[str, float]:
        return {"s_share": counts["S"] / n, "g_share": counts["G"] / n, "u_share": counts["U"] / n}

    cells = []
    value: dict[tuple[str, str, str], dict[str, float]] = {}  # (version, model, story) -> metrics
    for (s, v, m) in sorted(cell_qids):
        qs = cell_qids[(s, v, m)]
        per_judge = {}
        for j in judges:
            c = Counter(judgments[s][j][q]["label"] for q in qs)
            counts = {lab: c.get(lab, 0) for lab in LABELS}
            per_judge[j] = {"n": len(qs), **counts, **shares(counts, len(qs))}
        mean_j = {k: _mean([per_judge[j][k] for j in judges]) for k in ("s_share", "g_share", "u_share")}
        value[(v, m, s)] = {**mean_j, "net": mean_j["s_share"] - mean_j["u_share"]}
        cells.append({"version": v, "model": m, "story": s, "judges": per_judge, "mean_judges": mean_j})

    def story_value(v: str, s: str) -> dict[str, float] | None:
        parts = [value[(v, m, s)] for m in models if (v, m, s) in value]
        return {k: _mean([p[k] for p in parts]) for k in METRICS} if parts else None

    def complete(v: str, s: str) -> bool:
        return all((v, m, s) in value for m in models)

    by_version: dict[str, dict] = {}
    for v in versions:
        entry: dict[str, dict] = {}
        for m in models:
            vals = [value[(v, m, s)] for s in stories if (v, m, s) in value]
            if vals:
                entry[m] = {k: _mean([x[k] for x in vals]) for k in METRICS}
        svals = [sv for s in stories if (sv := story_value(v, s)) is not None]
        entry["all_models"] = {k: _mean([x[k] for x in svals]) for k in METRICS}
        by_version[v] = entry

    comparisons = []
    if baseline in versions:
        for v in versions:
            if v == baseline:
                continue
            ok_stories = [s for s in stories if complete(v, s) and complete(baseline, s)]
            missing = [s for s in stories if s not in ok_stories]
            for metric in METRICS:
                diffs = {s: story_value(v, s)[metric] - story_value(baseline, s)[metric] for s in ok_stories}
                ps = paired_summary(list(diffs.values()))
                per_model = {}
                for m in models:
                    md = [
                        value[(v, m, s)][metric] - value[(baseline, m, s)][metric]
                        for s in stories if (v, m, s) in value and (baseline, m, s) in value
                    ]
                    pm = paired_summary(md)
                    per_model[m] = {"n": pm.n, "mean": pm.mean, "se": pm.se, "up": pm.up,
                                    "equal": pm.equal, "down": pm.down, "sign": pm.sign}
                comparisons.append({
                    "version": v, "baseline": baseline, "metric": metric, "n_stories": ps.n,
                    "stories_missing": missing, "per_story": diffs, "mean": ps.mean, "se": ps.se,
                    "up": ps.up, "equal": ps.equal, "down": ps.down, "per_model": per_model,
                })

    notes = [
        "The judges are AI models. The numbers are exploratory.",
        "The standard error is over stories.",
        "No sentence in this file says that a version is better.",
    ]
    if not_scored:
        notes.append("Stories of the key that are not in the judgments file, so not scored: " + ", ".join(not_scored))
    if baseline not in versions:
        notes.append(f"The baseline {baseline} is not in this round, so there is no comparison.")

    labels = {j: {q: judgments[s][j][q]["label"] for s in stories for q in key_by_story[s]} for j in judges}
    all_qids = [q for s in stories for q in key_by_story[s]]
    if len(judges) >= 2:
        agreement: dict | None = {
            "overall": _agreement(all_qids, labels, judges),
            "by_version": {
                v: _agreement([q for q in all_qids if key[q]["version"] == v], labels, judges) for v in versions
            },
        }
    else:
        agreement = None
        notes.append("Only one judge, so there is no agreement.")

    u_kinds: dict[str, dict] = {}
    for v in versions:
        qs = [q for q in all_qids if key[q]["version"] == v]
        kinds = {k: 0.0 for k in (NONE_KIND, *U_KINDS)}
        for j in judges:
            for q in qs:
                e = judgments[key[q]["story_id"]][j][q]
                if e["label"] == "U":
                    kinds[e["kind"]] += 1 / len(judges)
        doubt = sum(
            1 for q in qs if any(judgments[key[q]["story_id"]][j][q]["doubt"] for j in judges)
        )
        u_kinds[v] = {"kinds": kinds, "items_with_doubt": doubt}

    failed, failed_notes = _failed_counts(round_dir, versions, models)
    notes.extend(failed_notes)
    failed_out: dict[str, dict[str, int]] | None = None
    if failed is not None:
        failed_out = {v: {m: failed[(v, m)] for m in models} for v in versions}
        for v in versions:
            n = sum(failed_out[v].values())
            if n:
                notes.append(f"{v} has {n} failed generation(s) with no requirement to judge: " +
                             ", ".join(f"{m} {c}" for m, c in failed_out[v].items() if c))

    scores: dict[str, Any] = {
        "schema_version": 1,
        "round": round_dir.name,
        "failed_generations": failed_out,
        "judges": judges,
        "baseline": baseline if baseline in versions else None,
        "stories_scored": stories,
        "stories_not_scored": not_scored,
        "n_items": len(all_qids),
        "cells": cells,
        "by_version": by_version,
        "comparisons": comparisons,
        "agreement": agreement,
        "u_kinds": u_kinds,
        "warnings": warnings,
        "notes": notes,
    }
    md = _render_md(scores, models)
    assert_no_private_text(md, what="scores.md")
    text = _dump(scores)
    assert_no_private_text(text, what="scores.json")
    guarded_mkdir(out_dir, guard=guard)
    guarded_write_text(out_dir / "scores.json", text, guard=guard)
    guarded_write_text(out_dir / "scores.md", md, guard=guard)
    return scores


# ---------------------------------------------------------------------------------------------
# scores.md
# ---------------------------------------------------------------------------------------------
def _f(x: float | None, pct: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x * 100:.1f}%" if pct else f"{x:.3f}"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out + [""]


def _render_md(sc: Mapping[str, Any], models: Sequence[str]) -> str:
    lines = [f"# Judging scores: round {sc['round']}", ""]
    lines += [
        f"Judges: {', '.join(sc['judges'])}. Items: {sc['n_items']}. Stories scored: {len(sc['stories_scored'])}.", "",
        "## Versions", "",
    ]
    rows = []
    for v, entry in sc["by_version"].items():
        for m in (*models, "all_models"):
            if m in entry:
                e = entry[m]
                rows.append([v, m, _f(e["s_share"], True), _f(e["g_share"], True), _f(e["u_share"], True), _f(e["net"], True)])
    lines += _table(["version", "model", "S", "G", "U", "net (S - U)"], rows)
    ff = sc.get("failed_generations")
    if ff is not None:
        lines += ["## Failed generations (final, not in S, G or U)", ""]
        lines += _table(["version", *models], [[v, *[str(c.get(m, 0)) for m in models]] for v, c in ff.items()])
    lines += ["## Each version against the baseline", ""]
    if sc["comparisons"]:
        rows = []
        for c in sc["comparisons"]:
            signs = ", ".join(f"{m}: {pm['sign'] or '-'}" for m, pm in c["per_model"].items())
            rows.append([c["version"], c["metric"], str(c["n_stories"]), _f(c["mean"]), _f(c["se"]),
                         str(c["up"]), str(c["equal"]), str(c["down"]), signs])
        lines += _table(["version", "metric", "stories", "mean difference", "SE", "up", "equal", "down", "sign by model"], rows)
        lines += ["## Differences by story", ""]
        rows = []
        for c in sc["comparisons"]:
            for s, d in c["per_story"].items():
                rows.append([c["version"], c["metric"], s, _f(d)])
            for s in c["stories_missing"]:
                rows.append([c["version"], c["metric"], s, "missing"])
        lines += _table(["version", "metric", "story", "difference"], rows)
    else:
        lines += ["No comparison in this round.", ""]
    lines += ["## Agreement of the judges", ""]
    ag = sc["agreement"]
    if ag is None:
        lines += ["Only one judge: no agreement.", ""]
    else:
        rows = [["all", str(ag["overall"]["n_items"]), _f(ag["overall"]["share_unanimous"], True), _f(ag["overall"]["mean_kappa"])]]
        for v, a in ag["by_version"].items():
            rows.append([v, str(a["n_items"]), _f(a["share_unanimous"], True), _f(a["mean_kappa"])])
        lines += _table(["set", "items", "all judges equal", "mean kappa"], rows)
        rows = [[" and ".join(p["judges"]), str(p["n_items"]), _f(p["share_equal"], True), _f(p["kappa"])]
                for p in ag["overall"]["pairs"]]
        lines += _table(["pair", "items", "equal labels", "kappa"], rows)
    lines += ["## Kinds of U", ""]
    kinds = (NONE_KIND, *U_KINDS)
    rows = [[v, *[f"{e['kinds'][k]:.2f}" for k in kinds], str(e["items_with_doubt"])] for v, e in sc["u_kinds"].items()]
    lines += _table(["version", *kinds, "items with doubt"], rows)
    if sc["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in sc["warnings"]] + [""]
    lines += ["## Notes", ""] + [f"- {n}" for n in sc["notes"]] + [""]
    return "\n".join(lines)
