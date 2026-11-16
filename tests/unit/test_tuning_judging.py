"""Tests for tuning_lib.judging. Invented run records in a temporary folder. No network, no model."""
import ast
import copy
import shutil
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from tuning_lib import judging as J  # noqa: E402
from tuning_lib.guard import GuardError, WriteGuard  # noqa: E402
from tuning_lib.paths import MODELS, STUDY2B, model_slug  # noqa: E402

QWEN, GEMMA = MODELS
STORIES = ("S1", "S2", "S3")
VERSIONS = ("v0_old", "v1")


def make_guard(tmp_path):
    return WriteGuard(allowed_roots=[tmp_path], forbidden_roots=[], forbidden_names=[])


def req(tag, **over):
    d = {
        "title": f"Title {tag}", "description": f"Description {tag}", "rationale": f"Rationale {tag}",
        "acceptance_criteria": [f"Criterion {tag} a", f"Criterion {tag} b"],
        "implementation_notes": f"Notes {tag}", "source_references": ["SECRET-REF"],
        "asvs_category": "V1", "priority": "high",
    }
    d.update(over)
    return d


def rec(version, model, story, repeat, reqs, *, error=None, step="lab"):
    return {
        "step": step, "config_id": version, "model": model, "story_id": story, "arm": "B2",
        "run": repeat, "project": "proj", "error": error, "requirements": reqs, "attempt": 1,
    }


def write_run(tmp_path, n_reqs=5, extra=()):
    run = tmp_path / "run1"
    cells: dict[str, list] = {}
    for model in MODELS:
        for story in STORIES:
            for version in VERSIONS:
                for repeat in (1, 2, 3):
                    reqs = [req(f"{story}.{version}.{model[:4]}.{repeat}.{i}") for i in range(n_reqs)]
                    cells.setdefault(model, []).append(rec(version, model, story, repeat, reqs))
    for r in extra:
        cells.setdefault(r["model"], []).append(r)
    for model, rows in cells.items():
        d = run / "proj" / model_slug(model)
        d.mkdir(parents=True, exist_ok=True)
        (d / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return run


def build(run, tmp_path, name="r1", per_cell=3, seed=1, **kw):
    return J.build_batches(run, list(VERSIONS), list(STORIES), per_cell, seed, name,
                           guard=make_guard(tmp_path), **kw)


def read_key(d):
    return json.loads(d.key_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- item text
def test_copies_equal_the_sheet_builder():
    src = (ROOT / "scripts" / "build_study2_review_sheet_v2.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    segs = {n.name: ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    for fn in (J.clean, J.md_safe, J.fmt_requirement):
        assert inspect.getsource(fn).rstrip("\n") == segs[fn.__name__]


def test_item_text_real_requirements():
    files = sorted(STUDY2B.glob("*/*/records.jsonl"))
    assert files
    n = 0
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                for r in json.loads(line).get("requirements") or []:
                    text = J.item_text(r)
                    assert J.ITEM_FORM.match(text)
                    n += 1
                    if n >= 20:
                        return
    assert n >= 20


def test_item_text_hides_fields():
    t = J.item_text(req("x"))
    assert "SECRET-REF" not in t and "high" not in t and "V1" not in t
    assert J.ITEM_FORM.match(t)
    assert J.item_text({}).count("(none)") == 2


# ---------------------------------------------------------------------------- batches
def test_batch_structure(tmp_path):
    d = build(write_run(tmp_path), tmp_path)
    key = read_key(d)
    assert d.n_items == 3 * 2 * 2 * 3 == 36 and sum(d.n_items_by_story.values()) == 36
    assert [p.name for p in d.batch_paths] == [f"{s}.json" for s in STORIES]
    seen = set()
    for p in d.batch_paths:
        b = json.loads(p.read_text())
        assert list(b) == ["story_id", "project", "n_items", "items"]
        assert b["project"] == "proj" and b["n_items"] == len(b["items"]) == 12
        for n, it in enumerate(b["items"], start=1):
            assert list(it) == ["qid", "text"]
            assert it["qid"] == f"{b['story_id']}-q{n:02d}"
            e = key[it["qid"]]
            assert list(e) == ["story_id", "project", "version", "model", "repeat", "requirement_index"]
            tag = f"{e['story_id']}.{e['version']}.{e['model'][:4]}.{e['repeat']}.{e['requirement_index']}"
            assert it["text"] == J.item_text(req(tag))
            seen.add(it["qid"])
    assert set(key) == seen
    assert d.design_path.parent.name == "private" and d.key_path.parent.name == "private"


def test_batches_hold_no_version_model_or_repeat(tmp_path):
    d = build(write_run(tmp_path), tmp_path)
    for p in d.batch_paths:
        b = json.loads(p.read_text())
        skeleton = json.dumps({**b, "items": [{"qid": i["qid"], "text": ""} for i in b["items"]]}).lower()
        for needle in ("v0", "v1_", "version", "qwen", "gemma", "arm", "repeat", "model", "b2", "run"):
            assert needle not in skeleton


def test_draw_per_cell_three_spreads_over_repeats(tmp_path):
    d = build(write_run(tmp_path), tmp_path, per_cell=3)
    key = read_key(d)
    cells: dict[tuple, list] = {}
    for e in key.values():
        cells.setdefault((e["story_id"], e["version"], e["model"]), []).append(e)
    assert len(cells) == 12
    for es in cells.values():
        assert sorted(e["repeat"] for e in es) == [1, 2, 3]
    units = [(e["story_id"], e["version"], e["model"], e["repeat"], e["requirement_index"]) for e in key.values()]
    assert len(units) == len(set(units))


def test_draw_per_cell_four_gives_2_1_1_and_seed_decides(tmp_path):
    run = write_run(tmp_path)
    doubles = set()
    for seed in range(8):
        d = build(run, tmp_path, name=f"r{seed}", per_cell=4, seed=seed)
        key = read_key(d)
        cell = [e for e in key.values() if (e["story_id"], e["version"], e["model"]) == ("S1", "v1", QWEN)]
        counts = sorted(sum(1 for e in cell if e["repeat"] == r) for r in (1, 2, 3))
        assert counts == [1, 1, 2]
        doubles.add(next(r for r in (1, 2, 3) if sum(1 for e in cell if e["repeat"] == r) == 2))
    assert len(doubles) > 1


def test_shortfall_goes_to_other_repeats(tmp_path):
    # repeat 1 of one cell has only one requirement; per_cell 6 over 3 repeats wants 2 each.
    short = rec("v1", QWEN, "S1", 1, [req("S1.v1.qwen.1.0")])
    run = write_run(tmp_path)
    f = run / "proj" / model_slug(QWEN) / "records.jsonl"
    f.write_text(f.read_text() + json.dumps(short) + "\n", encoding="utf-8")  # last line wins
    d = build(run, tmp_path, per_cell=6)
    key = read_key(d)
    cell = [e for e in key.values() if (e["story_id"], e["version"], e["model"]) == ("S1", "v1", QWEN)]
    by = {r: sum(1 for e in cell if e["repeat"] == r) for r in (1, 2, 3)}
    assert by[1] == 1 and sum(by.values()) == 6
    units = {(e["repeat"], e["requirement_index"]) for e in cell}
    assert len(units) == 6


def test_too_few_requirements(tmp_path):
    run = write_run(tmp_path, n_reqs=1)  # 3 repeats x 1 requirement = 3 per cell
    with pytest.raises(J.JudgingError, match="allow_short"):
        build(run, tmp_path, per_cell=4)
    d = build(run, tmp_path, name="r2", per_cell=4, allow_short=True)
    assert d.n_items == 3 * 2 * 2 * 3
    assert len(d.deviations) == 12 and "wanted 4, got 3" in d.deviations[0]
    design = json.loads(d.design_path.read_text())
    assert design["deviations"] == list(d.deviations)
    assert design["per_cell"] == 4


def test_per_cell_larger_than_all_takes_all_and_says_so(tmp_path):
    run = write_run(tmp_path, n_reqs=2)  # 6 per cell
    d = build(run, tmp_path, per_cell=50, allow_short=True)
    assert d.n_items == 6 * 12 and d.deviations


def test_determinism_and_seed_and_round_name(tmp_path):
    run = write_run(tmp_path)
    shutil.copytree(run, tmp_path / "run_copy")
    a = build(run, tmp_path, name="ra", seed=5)
    b = build(run, tmp_path, name="rb", seed=5)
    c = build(run, tmp_path, name="ra2", seed=6)
    e = build(run, tmp_path, name="rc", seed=5)

    def mix(d):
        return [(v["version"], v["model"], v["repeat"], v["requirement_index"]) for v in read_key(d).values()]

    assert mix(a) != mix(b)  # the round name is part of the seed
    assert mix(a) != mix(c)  # another seed gives another mix
    # same arguments, same files (a copy of the run, same round name)
    d1 = J.build_batches(tmp_path / "run_copy", list(VERSIONS), list(STORIES), 3, 5, "ra", guard=make_guard(tmp_path))
    assert d1.key_path.read_bytes() == a.key_path.read_bytes()
    for p1, p0 in zip(d1.batch_paths, a.batch_paths):
        assert p1.read_bytes() == p0.read_bytes()
    assert e.n_items == a.n_items


def test_versions_are_mixed(tmp_path):
    d = build(write_run(tmp_path), tmp_path, seed=3)
    key = read_key(d)
    seq = [key[f"S1-q{n:02d}"]["version"] for n in range(1, d.n_items_by_story["S1"] + 1)]
    assert seq != sorted(seq) and len(set(seq)) == 2


def test_text_hit_is_listed_not_raised(tmp_path):
    extra = rec("v1", GEMMA, "S1", 1, [req("x", description="This is a gemma word")] * 5)
    run = write_run(tmp_path, extra=[extra])
    d = build(run, tmp_path, per_cell=15, allow_short=True)
    design = json.loads(d.design_path.read_text())
    hits = next(c for c in design["checks"] if c["name"] == "text_hits")
    assert hits["ok"] and "gemma" in hits["detail"] and "S1-q" in hits["detail"]


def test_exclude_rounds(tmp_path):
    run = write_run(tmp_path)
    first = build(run, tmp_path, name="r1", per_cell=3)
    second = build(run, tmp_path, name="r2", per_cell=3, exclude_rounds=["r1"])
    one = {tuple(e.values())[2:] + (e["story_id"],) for e in read_key(first).values()}
    two = {tuple(e.values())[2:] + (e["story_id"],) for e in read_key(second).values()}
    assert not (one & two)
    assert json.loads(second.design_path.read_text())["exclude_rounds"] == ["r1"]
    with pytest.raises(J.JudgingError):
        build(run, tmp_path, name="r3", exclude_rounds=["nope"])


def test_refusals(tmp_path):
    run = write_run(tmp_path)
    build(run, tmp_path, name="r1")
    with pytest.raises(J.JudgingError, match="exists"):
        build(run, tmp_path, name="r1")
    with pytest.raises(J.JudgingError, match="round name"):
        build(run, tmp_path, name="bad name!")
    with pytest.raises(J.JudgingError, match="per_cell"):
        build(run, tmp_path, name="r2", per_cell=0)
    with pytest.raises(J.JudgingError, match="no record"):
        J.build_batches(run, ["vX"], list(STORIES), 1, 1, "r3", guard=make_guard(tmp_path))
    with pytest.raises(J.JudgingError, match="no record"):
        J.build_batches(run, list(VERSIONS), ["S9"], 1, 1, "r4", guard=make_guard(tmp_path))
    with pytest.raises(J.JudgingError, match="no record"):
        J.build_batches(run, list(VERSIONS), list(STORIES), 1, 1, "r5", models=["nope"], guard=make_guard(tmp_path))


def test_anchor_record_refused(tmp_path):
    run = write_run(tmp_path, extra=[rec("old", QWEN, "S1", 1, [req("a")], step="anchor")])
    with pytest.raises(J.JudgingError, match="anchor"):
        build(run, tmp_path)


def test_error_lines(tmp_path):
    bad_last = rec("v1", QWEN, "S2", 1, [], error="parse")
    ok_after = rec("v1", QWEN, "S3", 1, [req("S3.v1.qwen.1.0")])
    err_first = rec("v1", QWEN, "S3", 1, [], error="parse")
    run = write_run(tmp_path)
    f = run / "proj" / model_slug(QWEN) / "records.jsonl"
    f.write_text(f.read_text() + json.dumps(bad_last) + "\n" + json.dumps(err_first) + "\n" + json.dumps(ok_after) + "\n")
    d = build(run, tmp_path, per_cell=6, allow_short=True)
    key = read_key(d)
    s2 = [e for e in key.values() if (e["story_id"], e["version"], e["model"]) == ("S2", "v1", QWEN)]
    assert {e["repeat"] for e in s2} == {2, 3}  # repeat 1 ended with an error: not drawn
    s3 = [e for e in key.values() if (e["story_id"], e["version"], e["model"], e["repeat"]) == ("S3", "v1", QWEN, 1)]
    assert len(s3) == 1 and s3[0]["requirement_index"] == 0  # drawn once, from the ok line


def test_guard_refuses_outside_roots(tmp_path):
    run = write_run(tmp_path)
    other = WriteGuard(allowed_roots=[tmp_path / "elsewhere"], forbidden_roots=[], forbidden_names=[])
    with pytest.raises(GuardError):
        J.build_batches(run, list(VERSIONS), list(STORIES), 3, 1, "r1", guard=other)
    assert not (run / "judging").exists()


# ---------------------------------------------------------------------------- score
def make_key(tmp_path, spec):
    """spec: list of (story, version, model, n). Return key path and qids by cell."""
    key, cells, counter = {}, {}, {}
    for story, version, model, n in spec:
        for _ in range(n):
            counter[story] = counter.get(story, 0) + 1
            qid = f"{story}-q{counter[story]:02d}"
            key[qid] = {"story_id": story, "project": "proj", "version": version, "model": model,
                        "repeat": 1, "requirement_index": counter[story]}
            cells.setdefault((story, version, model), []).append(qid)
    kp = tmp_path / "judging" / "rx" / "private" / "key.json"
    kp.parent.mkdir(parents=True)
    kp.write_text(json.dumps(key))
    return kp, cells


def entry(qid, label, kind=None, doubt=False):
    if kind is None:
        kind = "none" if label != "U" else "wrong_fact"
    return {"qid": qid, "label": label, "kind": kind, "claim": "c", "why": "w", "doubt": doubt}


def write_judgments(tmp_path, data):
    p = tmp_path / "judgments.json"
    p.write_text(json.dumps(data))
    return p


def labels_for(cells, plan):
    """plan: {(story, version, model): 'SGUU'} -> entries list by story."""
    out: dict[str, list] = {}
    for (story, version, model), qids in cells.items():
        for q, lab in zip(qids, plan[(story, version, model)]):
            out.setdefault(story, []).append(entry(q, lab))
    return out


def hand_case(tmp_path):
    spec = [(s, v, m, 4) for s in ("S1", "S2") for v in ("v0_old", "v1") for m in (QWEN, GEMMA)]
    kp, cells = make_key(tmp_path, spec)
    base = {
        ("S1", "v0_old", QWEN): "SSGU", ("S1", "v0_old", GEMMA): "SGUU",
        ("S1", "v1", QWEN): "SSSU", ("S1", "v1", GEMMA): "SSGG",
        ("S2", "v0_old", QWEN): "UUUU", ("S2", "v0_old", GEMMA): "SSSS",
        ("S2", "v1", QWEN): "SUUU", ("S2", "v1", GEMMA): "SSSS",
    }
    # judge b differs from judge a at exactly 2 items of ("S1","v0_old",QWEN): SSGU -> SGGS
    other = dict(base)
    other[("S1", "v0_old", QWEN)] = "SGGS"
    ja, jb = labels_for(cells, base), labels_for(cells, other)
    data = {s: {"judge_a": ja[s], "judge_b": jb[s]} for s in ja}
    return kp, write_judgments(tmp_path, data), cells


def test_score_counts_final_failed_generations(tmp_path):
    fails = [rec("v1", QWEN, "S1", 1, [], error="parse", step="lab") | {"attempt": a} for a in (1, 2, 3)]
    fails += [rec("v1", QWEN, "S2", 1, [], error="parse") | {"run": 9, "attempt": 1}]  # not final: 1 of 3
    run = write_run(tmp_path, extra=[f | {"run": 7} if f["story_id"] == "S1" else f for f in fails])
    d = build(run, tmp_path)
    key = read_key(d)
    labels = {}
    for qid in key:
        labels.setdefault(key[qid]["story_id"], {"judge_a": []})["judge_a"].append(entry(qid, "S"))
    sc = J.score(write_judgments(tmp_path, labels), d.key_path, guard=make_guard(tmp_path))
    assert sc["failed_generations"]["v1"][QWEN] == 1
    assert sc["failed_generations"]["v0_old"][QWEN] == 0
    assert any("v1 has 1 failed generation" in n for n in sc["notes"])
    assert "Failed generations" in (d.round_dir / "scores.md").read_text(encoding="utf-8")


def test_score_numbers_by_hand(tmp_path):
    kp, jp, _ = hand_case(tmp_path)
    sc = J.score(jp, kp, guard=make_guard(tmp_path))
    cell = next(c for c in sc["cells"] if (c["story"], c["version"], c["model"]) == ("S1", "v0_old", QWEN))
    assert cell["judges"]["judge_a"] == {"n": 4, "S": 2, "G": 1, "U": 1, "s_share": 0.5, "g_share": 0.25, "u_share": 0.25}
    assert cell["judges"]["judge_b"]["S"] == 2 and cell["judges"]["judge_b"]["G"] == 2 and cell["judges"]["judge_b"]["U"] == 0
    mj = cell["mean_judges"]
    assert mj["s_share"] == pytest.approx(0.5) and mj["g_share"] == pytest.approx(0.375) and mj["u_share"] == pytest.approx(0.125)
    # u shares (judge_a = judge_b except the cell above). Mean-of-judges per cell:
    u = {("S1", "v0_old", QWEN): 0.125, ("S1", "v0_old", GEMMA): 0.5, ("S1", "v1", QWEN): 0.25, ("S1", "v1", GEMMA): 0.0,
         ("S2", "v0_old", QWEN): 1.0, ("S2", "v0_old", GEMMA): 0.0, ("S2", "v1", QWEN): 0.75, ("S2", "v1", GEMMA): 0.0}
    s = {("S1", "v0_old", QWEN): 0.5, ("S1", "v0_old", GEMMA): 0.25, ("S1", "v1", QWEN): 0.75, ("S1", "v1", GEMMA): 0.5,
         ("S2", "v0_old", QWEN): 0.0, ("S2", "v0_old", GEMMA): 1.0, ("S2", "v1", QWEN): 0.25, ("S2", "v1", GEMMA): 1.0}
    for c in sc["cells"]:
        k = (c["story"], c["version"], c["model"])
        assert c["mean_judges"]["u_share"] == pytest.approx(u[k]) and c["mean_judges"]["s_share"] == pytest.approx(s[k])
    # story value = mean over models; version value = mean over stories
    su = {(st, v): (u[(st, v, QWEN)] + u[(st, v, GEMMA)]) / 2 for st in ("S1", "S2") for v in ("v0_old", "v1")}
    assert sc["by_version"]["v0_old"]["all_models"]["u_share"] == pytest.approx((su[("S1", "v0_old")] + su[("S2", "v0_old")]) / 2)
    assert sc["by_version"]["v0_old"][QWEN]["u_share"] == pytest.approx((0.125 + 1.0) / 2)
    comp = {c["metric"]: c for c in sc["comparisons"]}
    assert [c["metric"] for c in sc["comparisons"]] == ["u_share", "s_share", "g_share", "net"]
    d1 = su[("S1", "v1")] - su[("S1", "v0_old")]
    d2 = su[("S2", "v1")] - su[("S2", "v0_old")]
    cu = comp["u_share"]
    assert cu["version"] == "v1" and cu["baseline"] == "v0_old" and cu["n_stories"] == 2 and cu["stories_missing"] == []
    assert cu["per_story"] == pytest.approx({"S1": d1, "S2": d2})
    assert cu["mean"] == pytest.approx((d1 + d2) / 2)
    sd = abs(d1 - d2) / 2 ** 0.5
    assert cu["se"] == pytest.approx(sd / 2 ** 0.5)
    assert (cu["up"], cu["equal"], cu["down"]) == (0, 0, 2)
    # net = s - u
    sn = {(st, v): (s[(st, v, QWEN)] + s[(st, v, GEMMA)]) / 2 - su[(st, v)] for st in ("S1", "S2") for v in ("v0_old", "v1")}
    assert comp["net"]["mean"] == pytest.approx(((sn[("S1", "v1")] - sn[("S1", "v0_old")]) + (sn[("S2", "v1")] - sn[("S2", "v0_old")])) / 2)
    pm = cu["per_model"]
    assert pm[GEMMA]["n"] == 2 and pm[GEMMA]["sign"] == "-" and pm[GEMMA]["down"] == 1 and pm[GEMMA]["equal"] == 1
    assert pm[QWEN]["mean"] == pytest.approx(((0.25 - 0.125) + (0.75 - 1.0)) / 2)


def test_stories_missing_when_version_judged_for_one_story_only(tmp_path):
    spec = [("S1", "v0_old", QWEN, 2), ("S1", "v1", QWEN, 2), ("S2", "v0_old", QWEN, 2)]
    kp, cells = make_key(tmp_path, spec)
    plan = {k: "SU" for k in cells}
    jl = labels_for(cells, plan)
    p = write_judgments(tmp_path, {s: {"a": e} for s, e in jl.items()})
    sc = J.score(p, kp, guard=make_guard(tmp_path))
    cu = sc["comparisons"][0]
    assert cu["stories_missing"] == ["S2"] and cu["n_stories"] == 1 and cu["se"] is None


def test_score_input_checks(tmp_path):
    spec = [("S1", "v0_old", QWEN, 2), ("S1", "v1", QWEN, 2), ("S2", "v0_old", QWEN, 2)]
    kp, cells = make_key(tmp_path, spec)
    good = labels_for(cells, {k: "SG" for k in cells})
    data = lambda: {s: {"a": copy.deepcopy(e), "b": copy.deepcopy(e)} for s, e in good.items()}  # noqa: E731

    def run(d, **kw):
        return J.score(write_judgments(tmp_path, d), kp, guard=make_guard(tmp_path))

    J.score(write_judgments(tmp_path, data()), kp, guard=make_guard(tmp_path))
    d = data(); d["S1"]["b"].pop()
    with pytest.raises(J.JudgingError, match="missing qids"):
        run(d)
    d = data(); d["S1"]["b"].append(entry("S1-q99", "S"))
    with pytest.raises(J.JudgingError, match="extra qid"):
        run(d)
    d = data(); d["S1"]["b"].append(dict(d["S1"]["b"][0]))
    with pytest.raises(J.JudgingError, match="duplicate"):
        run(d)
    d = data(); d["S1"]["b"][0]["label"] = "X"
    with pytest.raises(J.JudgingError, match="bad label"):
        run(d)
    d = data(); d["S1"]["b"][0].update(label="U", kind="bogus")
    with pytest.raises(J.JudgingError, match="bad kind"):
        run(d)
    d = data(); d["S1"]["b"][0].update(label="S", kind="wrong_fact")
    with pytest.raises(J.JudgingError, match="needs kind none"):
        run(d)
    d = data(); d["S9"] = d["S1"]
    with pytest.raises(J.JudgingError, match="not in the key"):
        run(d)
    d = data(); d["S2"]["c"] = d["S2"].pop("b")
    with pytest.raises(J.JudgingError, match="same set of judges"):
        run(d)
    d = data(); d["S1"]["b"][0]["doubt"] = "yes"
    with pytest.raises(J.JudgingError, match="doubt"):
        run(d)
    d = data(); d["S1"]["b"][0].update(label="U", kind="none")
    sc = run(d)
    assert len(sc["warnings"]) == 1 and "S1-q01" in sc["warnings"][0]


def test_agreement_by_hand_and_kappa(tmp_path):
    # two judges, 10 items: table S/S 4, G/G 2, U/U 1, S/G 2, G/U 1 -> equal 7 of 10
    spec = [("S1", "v0_old", QWEN, 10)]
    kp, cells = make_key(tmp_path, spec)
    qids = cells[("S1", "v0_old", QWEN)]
    la = list("SSSSGGUSSG")
    lb = list("SSSSGGUGGU")
    p = write_judgments(tmp_path, {"S1": {"a": [entry(q, x) for q, x in zip(qids, la)],
                                           "b": [entry(q, x) for q, x in zip(qids, lb)]}})
    sc = J.score(p, kp, guard=make_guard(tmp_path))
    ov = sc["agreement"]["overall"]
    assert ov["n_items"] == 10 and ov["n_unanimous"] == 7 and ov["share_unanimous"] == 0.7
    pair = ov["pairs"][0]
    assert pair["n_equal"] == 7 and pair["share_equal"] == 0.7
    pe = (6 / 10) * (4 / 10) + (3 / 10) * (4 / 10) + (1 / 10) * (2 / 10)
    assert pair["kappa"] == pytest.approx((0.7 - pe) / (1 - pe))
    assert ov["mean_kappa"] == pytest.approx(pair["kappa"])
    assert sc["agreement"]["by_version"]["v0_old"]["n_unanimous"] == 7
    # all labels equal: kappa None
    p = write_judgments(tmp_path, {"S1": {"a": [entry(q, "S") for q in qids], "b": [entry(q, "S") for q in qids]}})
    sc = J.score(p, kp, guard=make_guard(tmp_path))
    assert sc["agreement"]["overall"]["pairs"][0]["kappa"] is None
    assert sc["agreement"]["overall"]["share_unanimous"] == 1.0
    # one judge: no agreement
    p = write_judgments(tmp_path, {"S1": {"a": [entry(q, "S") for q in qids]}})
    sc = J.score(p, kp, guard=make_guard(tmp_path))
    assert sc["agreement"] is None and any("one judge" in n for n in sc["notes"])


def test_u_kinds_and_doubt(tmp_path):
    kp, cells = make_key(tmp_path, [("S1", "v0_old", QWEN, 2)])
    q = cells[("S1", "v0_old", QWEN)]
    a = [entry(q[0], "U", "wrong_fact", doubt=True), entry(q[1], "S")]
    b = [entry(q[0], "U", "name_not_found"), entry(q[1], "S")]
    sc = J.score(write_judgments(tmp_path, {"S1": {"a": a, "b": b}}), kp, guard=make_guard(tmp_path))
    k = sc["u_kinds"]["v0_old"]
    assert k["kinds"]["wrong_fact"] == 0.5 and k["kinds"]["name_not_found"] == 0.5 and k["items_with_doubt"] == 1


def test_output_files_and_privacy(tmp_path):
    kp, jp, _ = hand_case(tmp_path)
    sc = J.score(jp, kp, guard=make_guard(tmp_path))
    round_dir = kp.parent.parent
    on_disk = json.loads((round_dir / "scores.json").read_text())
    for k in ("schema_version", "round", "judges", "baseline", "stories_scored", "stories_not_scored", "n_items",
              "cells", "by_version", "comparisons", "agreement", "u_kinds", "warnings", "notes"):
        assert k in on_disk
    assert on_disk["round"] == "rx" and on_disk == json.loads(json.dumps(sc))
    md = (round_dir / "scores.md").read_text()
    for note in ("The judges are AI models. The numbers are exploratory.", "The standard error is over stories."):
        assert note in md
    assert md.count("better") == 1 and "is better" in md and "No sentence in this file says that a version is better." in md
    # a custom out_dir
    out = tmp_path / "out"
    J.score(jp, kp, out_dir=out, guard=make_guard(tmp_path))
    assert (out / "scores.md").is_file()
    other = WriteGuard(allowed_roots=[tmp_path / "elsewhere"], forbidden_roots=[], forbidden_names=[])
    with pytest.raises(GuardError):
        J.score(jp, kp, guard=other)


def test_story_not_in_judgments_is_reported(tmp_path):
    kp, cells = make_key(tmp_path, [("S1", "v0_old", QWEN, 1), ("S2", "v0_old", QWEN, 1)])
    jl = labels_for({k: v for k, v in cells.items() if k[0] == "S1"}, {("S1", "v0_old", QWEN): "S"})
    sc = J.score(write_judgments(tmp_path, {"S1": {"a": jl["S1"]}}), kp, guard=make_guard(tmp_path))
    assert sc["stories_scored"] == ["S1"] and sc["stories_not_scored"] == ["S2"]
    assert any("S2" in n for n in sc["notes"])


# ---------------------------------------------------------------------------- source scan
def test_no_write_call_outside_guard():
    src = (ROOT / "scripts" / "tuning_lib" / "judging.py").read_text(encoding="utf-8")
    for bad in ("write_text(", "open(", "mkdir(", "shutil", "os.replace", "unlink("):
        for line in src.splitlines():
            if bad in line and "guarded_" not in line:
                assert bad == "open(" and '"rb"' in line, line
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("write_text", "mkdir"):
            raise AssertionError("direct write call")


def test_import_has_no_heavy_modules():
    import subprocess
    code = ("import sys; sys.path[:0]=['scripts','src']; import tuning_lib.judging; "
            "print([m for m in ('chromadb','sentence_transformers','secreqgen.retrieval','httpx') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]" or "httpx" in out.stdout
