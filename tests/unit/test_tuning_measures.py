"""Tests of scripts/tuning_lib/measures.py. No network, no model. Reads registered data only."""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from tuning_lib import measures as m  # noqa: E402
from tuning_lib.paths import ANALYSIS_S2B, MODELS, PROJECTS, STUDY2, STUDY2B, model_slug  # noqa: E402

SHEETS = {"S1": ("alpha_fn", "BetaClass"), "S2": ("gamma.key",)}


def rec(story="S1", arm="A2", run=1, model="m1", reqs=None, error=None, system="sys", non_doc="doc", **extra):
    base = {
        "model": model, "story_id": story, "arm": arm, "run": run, "error": error,
        "prompt_system": system, "prompt_user_non_doc": non_doc,
        "requirements": [] if reqs is None else reqs,
    }
    base.update(extra)
    return base


def req(**kw):
    return {"title": "t", "description": "d", **kw}


# ---------------------------------------------------------------- score_record
def test_name_in_prompt_not_counted():
    r = rec(non_doc="uses alpha_fn", reqs=[req(description="call alpha_fn")])
    s = m.score_record(r, SHEETS["S1"])
    assert s.n_with_name == 0 and s.name_use == 0.0
    assert s.prompt_given == ("alpha_fn",) and s.cited == ("alpha_fn",) and s.attributable == ()


def test_name_only_in_answer_counted():
    s = m.score_record(rec(reqs=[req(description="use BetaClass"), req()]), SHEETS["S1"])
    assert (s.n_requirements, s.n_with_name, s.name_use) == (2, 1, 0.5)
    assert s.attributable == ("BetaClass",)


def test_source_references_count():
    s = m.score_record(rec(reqs=[req(source_references=["see gamma.key"])]), SHEETS["S2"])
    assert s.n_with_name == 1


def test_no_requirements():
    s = m.score_record(rec(reqs=[]), SHEETS["S1"])
    assert s.n_requirements == 0 and s.name_use == 0.0


@pytest.mark.parametrize("field", ["prompt_system", "prompt_user_non_doc", "requirements"])
def test_missing_field_raises(field):
    r = rec()
    del r[field]
    with pytest.raises(m.MeasureError):
        m.score_record(r, SHEETS["S1"])


def test_bad_requirement_raises():
    with pytest.raises(m.MeasureError):
        m.score_record(rec(reqs=["text"]), SHEETS["S1"])


# ------------------------------------------------------ latest / story / arm
def test_latest_keeps_last_and_error_removes_value():
    ok = rec(reqs=[req(description="BetaClass")], attempt=1)
    bad = rec(error="X: y", attempt=2)
    assert m.latest_records([ok, bad])[(None, "m1", "S1", "A2", 1)]["error"] == "X: y"
    assert m.story_values([ok, bad], SHEETS) == {}


def test_story_values_runs_and_counts():
    r1 = rec(run=1, reqs=[req(description="BetaClass"), req()])
    r2 = rec(run=2, reqs=[req(description="BetaClass"), req(title="alpha_fn"), req(), req()])
    r3 = rec(run=3, error="E: x")
    v = m.story_values([r1, r2, r3], SHEETS)[(None, "m1", "A2", "S1")]
    assert v.n_runs == 2
    assert v.name_use == pytest.approx((0.5 + 0.5) / 2)
    assert v.n_with_name_mean == 1.5 and v.n_requirements_mean == 3.0


def test_unknown_story_raises():
    with pytest.raises(m.MeasureError):
        m.story_values([rec(story="ZZ")], SHEETS)


def test_arm_rate_order_independent_and_counts():
    recs = [
        rec(story="S1", reqs=[req(description="BetaClass")]),
        rec(story="S2", reqs=[req(), req(description="gamma.key")]),
    ]
    base = m.arm_rates(recs, SHEETS)
    for _ in range(5):
        random.shuffle(recs)
        assert m.arm_rates(recs, SHEETS) == base
    a = base[(None, "m1", "A2")]
    assert a.rate == pytest.approx((1.0 + 0.5) / 2) and a.n_stories == 2 and a.n_failed == 0
    with pytest.raises(m.MeasureError):  # the arm B2 has no good record
        m.arm_rates([rec(arm="B2", error="E: x")], SHEETS)


def test_empty_input_raises():
    with pytest.raises(m.MeasureError):
        m.arm_rates([], SHEETS)


def test_repeat_rates():
    recs = [rec(run=1, reqs=[req(description="BetaClass")]), rec(run=2, reqs=[req()])]
    rr = m.repeat_rates(recs, SHEETS)
    assert rr[(None, "m1", "A2", 1)] == 1.0 and rr[(None, "m1", "A2", 2)] == 0.0


# ---------------------------------------------------------------- run_totals
def hosted(**kw):
    base = dict(
        error=None, n_requirements_failed_validation=0, parsed_by_fallback=False,
        old_parser_result="same", finish_reason="stop", reasoning_tokens_source="usage",
        prompt_tokens=10, completion_tokens=5, reasoning_tokens=0, cost_usd=0.01,
    )
    base.update(kw)
    return rec(**base)


def test_run_totals():
    lines = [
        hosted(attempt=1, error="E: x", prompt_tokens=None, completion_tokens=None,
               reasoning_tokens=None, cost_usd=None, finish_reason=None, old_parser_result=None,
               parsed_by_fallback=None, reasoning_tokens_source=None),
        hosted(attempt=2, parsed_by_fallback=True, old_parser_result="different",
               n_requirements_failed_validation=2, reasoning_tokens_source="absent_assumed_zero"),
        hosted(story="S2", finish_reason="length", old_parser_result="failed"),
    ]
    t = m.run_totals(lines)
    assert t.n_records == 3 and t.n_attempt_lines_failed == 1 and t.n_parse_retries == 1
    assert t.n_requirements_failed_validation == 2 and t.n_parsed_by_fallback == 1
    assert t.n_old_parser_different == 1 and t.n_old_parser_failed == 1
    assert t.n_finish_length == 1 and t.n_reasoning_assumed_zero == 1
    assert (t.prompt_tokens, t.completion_tokens, t.reasoning_tokens) == (20, 10, 0)
    assert t.cost_usd == pytest.approx(0.02)


def test_run_totals_missing_field_raises():
    r = hosted()
    del r["cost_usd"]
    with pytest.raises(m.MeasureError):
        m.run_totals([r])


# ------------------------------------------------- equality with registered code
def _run_registered(tmp_path: Path, stories_by_project: dict, records_by_project: dict) -> dict:
    reg = m.registered_module()
    for project, data in stories_by_project.items():
        (tmp_path / project).mkdir(parents=True, exist_ok=True)
        (tmp_path / project / "stories.json").write_text(json.dumps(data))
        for model, lines in records_by_project[project].items():
            d = tmp_path / project / "results" / model_slug(model)
            d.mkdir(parents=True, exist_ok=True)
            (d / "records.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    old = reg.STUDY2
    reg.STUDY2 = tmp_path
    try:
        reports = [reg.analyze(p) for p in stories_by_project]
    finally:
        reg.STUDY2 = old
    out = {}
    for rep in reports:
        for model, mm in rep["models"].items():
            for arm, stories in mm["per_story"].items():
                for sid, e in stories.items():
                    out[(model, arm, sid)] = e["frac_reqs_citing"]
    return out


def test_equal_to_registered_on_fake_study(tmp_path):
    stories = {"p": {"stories": [
        {"id": "S1", "fact_sheet": {"identifiers": list(SHEETS["S1"])}},
        {"id": "S2", "fact_sheet": {"identifiers": list(SHEETS["S2"])}},
    ]}}
    lines = [
        rec("S1", "A2", 1, reqs=[req(description="BetaClass"), req()]),
        rec("S1", "A2", 2, reqs=[req(title="alpha_fn")]),
        rec("S2", "A2", 1, reqs=[]),
        rec("S2", "B2", 1, error="E: x"),
        rec("S1", "B2", 1, non_doc="alpha_fn", reqs=[req(description="alpha_fn"), req(description="BetaClass")]),
    ]
    expected = _run_registered(tmp_path, stories, {"p": {"m1": lines}})
    sheets = m.load_fact_sheets(["p"], tmp_path)
    mine = {(k[1], k[2], k[3]): v.name_use for k, v in m.story_values(lines, sheets).items()}
    assert mine == expected and mine  # same keys and same values, exactly


def test_load_fact_sheets_errors(tmp_path):
    def write(data):
        (tmp_path / "p").mkdir(exist_ok=True)
        (tmp_path / "p" / "stories.json").write_text(json.dumps(data))
    write({"stories": [{"id": "S1", "fact_sheet": {"identifiers": []}}]})
    with pytest.raises(m.MeasureError):
        m.load_fact_sheets(["p"], tmp_path)
    write({"stories": [{"id": "S1", "fact_sheet": {}}]})
    with pytest.raises(m.MeasureError):
        m.load_fact_sheets(["p"], tmp_path)
    dup = {"id": "S1", "fact_sheet": {"identifiers": ["a"]}}
    write({"stories": [dup, dup]})
    with pytest.raises(m.MeasureError):
        m.load_fact_sheets(["p"], tmp_path)


# ------------------------------------------------------- real Study 2b records
def _real_records() -> list[dict]:
    out = []
    for project in PROJECTS:
        for model in MODELS:
            path = STUDY2B / project / model_slug(model) / "records.jsonl"
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r["arm"] in ("A2", "B2"):
                        out.append(r)
    return out


def test_acceptance_arm_rates_equal_local_analysis():
    records = _real_records()
    sheets = m.load_fact_sheets()
    rates = m.arm_rates(records, sheets)
    ref = m.load_local_reference()
    stored = json.loads(ANALYSIS_S2B.read_text(encoding="utf-8"))
    hard = {
        ("qwen3.8:27b", "A2"): 0.07777777777777778, ("qwen3.8:27b", "B2"): 0.4755555555555555,
        ("gemma4:26b", "A2"): 0.04000000000000001, ("gemma4:26b", "B2"): 0.5533333333333333,
    }
    assert len(ref) == 4
    for (model, arm), value in ref.items():
        got = rates[(None, model, arm)]
        assert got.n_stories == 15 and got.n_failed == 0
        assert got.rate == pytest.approx(value, abs=5e-10)
        assert got.rate == pytest.approx(stored["models"][model]["arms"][arm]["rate"], abs=5e-10)
        assert got.rate == pytest.approx(hard[(model, arm)], abs=5e-10)


def test_acceptance_per_story_equals_registered_analyze(tmp_path):
    """The registered analyze() on copies of the real records gives the same story values."""
    records = _real_records()
    sheets = m.load_fact_sheets()
    stories, recs = {}, {}
    for project in PROJECTS:
        stories[project] = json.loads((STUDY2 / project / "stories.json").read_text(encoding="utf-8"))
        recs[project] = {
            model: [r for r in records if r["project"] == project and r["model"] == model] for model in MODELS
        }
    expected = _run_registered(tmp_path, stories, recs)
    mine = {(k[1], k[2], k[3]): v.name_use for k, v in m.story_values(records, sheets).items()}
    assert len(mine) == 60 and mine == expected


def test_load_local_reference_missing_arm(tmp_path):
    data = json.loads(ANALYSIS_S2B.read_text(encoding="utf-8"))
    del data["models"][MODELS[0]]["arms"]["A2"]
    p = tmp_path / "a.json"
    p.write_text(json.dumps(data))
    with pytest.raises(m.MeasureError):
        m.load_local_reference(p)


def test_limit_points_equal_registered_source():
    text = (_ROOT / "scripts" / "analyze_study2b.py").read_text(encoding="utf-8")
    found = re.search(r'DRIFT_LIMIT = \{"B2": ([0-9.]+), "A2": ([0-9.]+)\}', text)
    assert found
    assert m.LIMIT_POINTS == {"B2": float(found.group(1)) * 100, "A2": float(found.group(2)) * 100}


def test_no_bytecode_created_in_scripts():
    before = sorted(p.name for p in (_ROOT / "scripts").iterdir())
    m._REGISTERED = None
    m.registered_module()
    assert sorted(p.name for p in (_ROOT / "scripts").iterdir()) == before
