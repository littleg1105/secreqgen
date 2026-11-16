"""Measures of the tuning runner: "name use" as the registered analysis computes it.

Name use of a requirement is 1 when it cites a fact-sheet identifier that the
prompt did not already hold. A record value is the share of such requirements.
A story value is the mean over runs. An arm rate is the mean over stories.

The registered definition (`found`, `req_text`) is loaded by file path from
`scripts/analyze_study2.py`. This module reads files only. It never writes.
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Iterable, Mapping, Sequence

from tuning_lib.paths import ANALYSIS_S2B, MODELS, PROJECTS, SCRIPTS, STUDY2

# Drift limits against the local values, in points (analyze_study2b.py DRIFT_LIMIT, times 100).
LIMIT_POINTS: dict[str, float] = {"B2": 8.0, "A2": 5.0}
REFERENCE_ARMS: tuple[str, ...] = ("A2", "B2")

_REGISTERED: ModuleType | None = None


class MeasureError(Exception):
    """A record, a fact sheet or a reference file is not in the expected form."""


def registered_module() -> ModuleType:
    """Load scripts/analyze_study2.py by path (once). It has stdlib imports only."""
    global _REGISTERED
    if _REGISTERED is None:
        path = SCRIPTS / "analyze_study2.py"
        if not path.is_file():
            raise MeasureError(f"the registered analysis file is missing: {path.name}")
        old = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("_registered_analyze_study2", path)
            if spec is None or spec.loader is None:
                raise MeasureError("cannot load analyze_study2.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = old
        _REGISTERED = module
    return _REGISTERED


def load_fact_sheets(
    projects: Sequence[str] = PROJECTS, study2_root: Path = STUDY2
) -> dict[str, tuple[str, ...]]:
    """Map story id to its fact-sheet identifiers. Duplicate id, missing key or empty list raises."""
    out: dict[str, tuple[str, ...]] = {}
    for project in projects:
        path = study2_root / project / "stories.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stories = data["stories"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise MeasureError(f"cannot read stories of {project}: {type(exc).__name__}") from exc
        for story in stories:
            try:
                sid = story["id"]
                ids = story["fact_sheet"]["identifiers"]
            except (KeyError, TypeError) as exc:
                raise MeasureError(f"a story of {project} has no id or no fact_sheet.identifiers") from exc
            if sid in out:
                raise MeasureError(f"duplicate story id {sid}")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                raise MeasureError(f"story {sid}: identifiers must be a non-empty list of strings")
            out[sid] = tuple(ids)
    return out


@dataclass(frozen=True)
class RecordScore:
    n_requirements: int
    n_with_name: int
    name_use: float
    prompt_given: tuple[str, ...]
    cited: tuple[str, ...]
    attributable: tuple[str, ...]


def _need(rec: Mapping, name: str, kind: type):
    if name not in rec:
        raise MeasureError(f"record has no field '{name}'")
    value = rec[name]
    if not isinstance(value, kind):
        raise MeasureError(f"record field '{name}' must be {kind.__name__}")
    return value


def score_record(rec: Mapping, identifiers: Sequence[str]) -> RecordScore:
    """Score one record. A missing or mistyped field raises MeasureError."""
    reg = registered_module()
    system = _need(rec, "prompt_system", str)
    non_doc = _need(rec, "prompt_user_non_doc", str)
    reqs = _need(rec, "requirements", list)
    ids = list(identifiers)
    given = reg.found(system + "\n" + non_doc, ids)
    cited_all: set[str] = set()
    attr_all: set[str] = set()
    flags = 0
    for req in reqs:
        if not isinstance(req, dict):
            raise MeasureError("a requirement is not an object")
        cited = reg.found(reg.req_text(req), ids)
        attributable = cited - given
        flags += 1 if attributable else 0
        cited_all |= cited
        attr_all |= attributable
    n = len(reqs)
    return RecordScore(
        n_requirements=n,
        n_with_name=flags,
        name_use=(flags / n) if n else 0.0,
        prompt_given=tuple(sorted(given)),
        cited=tuple(sorted(cited_all)),
        attributable=tuple(sorted(attr_all)),
    )


def _key(rec: Mapping) -> tuple:
    try:
        return (rec.get("config_id"), rec["model"], rec["story_id"], rec["arm"], rec["run"])
    except KeyError as exc:
        raise MeasureError(f"record has no field {exc}") from exc


def latest_records(records: Iterable[Mapping]) -> dict[tuple, Mapping]:
    """The last line for each (config_id, model, story_id, arm, run)."""
    out: dict[tuple, Mapping] = {}
    for rec in records:
        out[_key(rec)] = rec
    return out


@dataclass(frozen=True)
class StoryValue:
    config_id: str | None
    model: str
    arm: str
    story_id: str
    n_runs: int
    name_use: float
    n_with_name_mean: float
    n_requirements_mean: float


def _sheet(fact_sheets: Mapping[str, Sequence[str]], sid: str) -> Sequence[str]:
    if sid not in fact_sheets:
        raise MeasureError(f"no fact sheet for story {sid}")
    return fact_sheets[sid]


def _scored_final(records: Iterable[Mapping], fact_sheets) -> list[tuple[tuple, RecordScore]]:
    out = []
    for key, rec in latest_records(records).items():
        if rec.get("error") is not None:
            continue
        out.append((key, score_record(rec, _sheet(fact_sheets, key[2]))))
    return out


def story_values(
    records: Iterable[Mapping], fact_sheets: Mapping[str, Sequence[str]]
) -> dict[tuple, StoryValue]:
    """Key (config_id, model, arm, story_id). Error records are left out."""
    groups: dict[tuple, list[RecordScore]] = defaultdict(list)
    for (config, model, sid, arm, _run), score in _scored_final(records, fact_sheets):
        groups[(config, model, arm, sid)].append(score)
    out: dict[tuple, StoryValue] = {}
    for key in sorted(groups, key=lambda k: (str(k[0]), k[1], k[2], k[3])):
        scores = groups[key]
        out[key] = StoryValue(
            config_id=key[0], model=key[1], arm=key[2], story_id=key[3],
            n_runs=len(scores),
            name_use=statistics.fmean(s.name_use for s in scores),
            n_with_name_mean=statistics.fmean(s.n_with_name for s in scores),
            n_requirements_mean=statistics.fmean(s.n_requirements for s in scores),
        )
    return out


@dataclass(frozen=True)
class ArmRate:
    config_id: str | None
    model: str
    arm: str
    rate: float
    n_stories: int
    n_records: int
    n_failed: int
    n_stories_without_value: int


def arm_rates(
    records: Iterable[Mapping], fact_sheets: Mapping[str, Sequence[str]]
) -> dict[tuple, ArmRate]:
    """Key (config_id, model, arm). Stories are summed in story-id order."""
    recs = list(records)
    final = latest_records(recs)
    if not final:
        raise MeasureError("no records: cannot compute an arm rate")
    values = story_values(recs, fact_sheets)
    counts: dict[tuple, list] = defaultdict(lambda: [0, 0, set()])  # records, failed, all stories
    for (config, model, sid, arm, _run), rec in final.items():
        c = counts[(config, model, arm)]
        c[0] += 1
        c[1] += 1 if rec.get("error") is not None else 0
        c[2].add(sid)
    out: dict[tuple, ArmRate] = {}
    for key in sorted(counts, key=lambda k: (str(k[0]), k[1], k[2])):
        n_rec, n_fail, all_stories = counts[key]
        vals = sorted(
            (v.story_id, v.name_use) for k, v in values.items() if k[:3] == key
        )
        if not vals:
            raise MeasureError(f"{key}: no story has a good record, so there is no rate")
        out[key] = ArmRate(
            config_id=key[0], model=key[1], arm=key[2],
            rate=sum(v for _s, v in vals) / len(vals),
            n_stories=len(vals), n_records=n_rec, n_failed=n_fail,
            n_stories_without_value=len(all_stories) - len(vals),
        )
    return out


def repeat_rates(records: Iterable[Mapping], fact_sheets: Mapping[str, Sequence[str]]) -> dict[tuple, float]:
    """Key (config_id, model, arm, run): the arm rate of one repeat alone."""
    groups: dict[tuple, list[tuple[str, float]]] = defaultdict(list)
    for (config, model, sid, arm, run), score in _scored_final(records, fact_sheets):
        groups[(config, model, arm, run)].append((sid, score.name_use))
    return {
        k: sum(v for _s, v in sorted(groups[k])) / len(groups[k])
        for k in sorted(groups, key=lambda k: (str(k[0]), k[1], k[2], k[3]))
    }


@dataclass(frozen=True)
class RunTotals:
    n_records: int
    n_attempt_lines_failed: int
    n_parse_retries: int
    n_requirements_failed_validation: int
    n_parsed_by_fallback: int
    n_old_parser_different: int
    n_old_parser_failed: int
    n_finish_length: int
    n_reasoning_assumed_zero: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float


def _f(rec: Mapping, name: str):
    if name not in rec:
        raise MeasureError(f"hosted record has no field '{name}'")
    return rec[name]


def run_totals(records: Iterable[Mapping]) -> RunTotals:
    """Totals over all lines (attempts included). None in a numeric field counts as 0 (no reply)."""
    n = failed = fallback = diff = old_failed = length = assumed = validation = 0
    prompt = completion = reasoning = 0
    cost = 0.0
    lines_per_key: dict[tuple, int] = defaultdict(int)
    for rec in records:
        n += 1
        lines_per_key[_key(rec)] += 1
        failed += 1 if _f(rec, "error") is not None else 0
        validation += _f(rec, "n_requirements_failed_validation") or 0
        fallback += 1 if _f(rec, "parsed_by_fallback") is True else 0
        old = _f(rec, "old_parser_result")
        diff += 1 if old == "different" else 0
        old_failed += 1 if old == "failed" else 0
        length += 1 if _f(rec, "finish_reason") == "length" else 0
        assumed += 1 if _f(rec, "reasoning_tokens_source") == "absent_assumed_zero" else 0
        prompt += _f(rec, "prompt_tokens") or 0
        completion += _f(rec, "completion_tokens") or 0
        reasoning += _f(rec, "reasoning_tokens") or 0
        cost += _f(rec, "cost_usd") or 0.0
    return RunTotals(
        n_records=n, n_attempt_lines_failed=failed,
        n_parse_retries=sum(1 for c in lines_per_key.values() if c > 1),
        n_requirements_failed_validation=validation, n_parsed_by_fallback=fallback,
        n_old_parser_different=diff, n_old_parser_failed=old_failed,
        n_finish_length=length, n_reasoning_assumed_zero=assumed,
        prompt_tokens=prompt, completion_tokens=completion, reasoning_tokens=reasoning,
        cost_usd=cost,
    )


def load_local_reference(path: Path = ANALYSIS_S2B) -> dict[tuple[str, str], float]:
    """(model, arm) to the local 'rate' in analysis_s2b.json, arms A2 and B2, both models."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        models = data["models"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MeasureError(f"cannot read the local reference file: {type(exc).__name__}") from exc
    out: dict[tuple[str, str], float] = {}
    for model in MODELS:
        for arm in REFERENCE_ARMS:
            try:
                rate = models[model]["arms"][arm]["rate"]
            except (KeyError, TypeError) as exc:
                raise MeasureError(f"reference has no rate for {model} {arm}") from exc
            if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                raise MeasureError(f"reference rate for {model} {arm} is not a number")
            out[(model, arm)] = float(rate)
    return out
