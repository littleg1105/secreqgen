"""Tests of scripts/run_tuning.py. No network, no key, no model. Output goes to tmp_path."""

from __future__ import annotations

import dataclasses
import json
import re
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_tuning as rt  # noqa: E402
from tuning_lib import measures  # noqa: E402
from tuning_lib.guard import (  # noqa: E402
    GuardError,
    HashListResult,
    ProcessRow,
    WriteGuard,
)
from tuning_lib.openrouter_client import (  # noqa: E402
    HOSTED_MODELS,
    ContractViolation,
    FatalHostedError,
    HostedReply,
    TransientHostedError,
)
from tuning_lib.passages import StoredContext, StoredPrompt  # noqa: E402
from tuning_lib.paths import FALLBACK_CONTEXT, STUDY2B, model_slug  # noqa: E402
from tuning_lib.records import read_jsonl, validate_record  # noqa: E402

SMOKE = ["--stories", "BW-S2-001"]


# ---------------------------------------------------------------- helpers


def _good_answer() -> str:
    """A valid answer text built from requirements stored by the old code (read only)."""
    path = STUDY2B / "bitwarden" / model_slug("gemma4:26b") / "records.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("error") is None and rec.get("requirements"):
            return json.dumps({"requirements": rec["requirements"][:3]})
    raise AssertionError("no stored record with requirements")


GOOD = _good_answer()


def make_reply(text: str = GOOD, cost: float = 0.01, finish: str | None = "stop",
               reasoning: int = 0) -> HostedReply:
    return HostedReply(
        text=text, generation_id="gen-1", response_model="google/gemma-4-26b-a4b-it",
        provider="Parasail", provider_source="metadata", finish_reason=finish,
        prompt_tokens=100, completion_tokens=50, reasoning_tokens=reasoning,
        reasoning_tokens_source="usage", cached_tokens=0, cost_usd=cost, latency_ms=12.5,
        transport_retries=0)


class FakeClient:
    """Returns stored replies. Each call takes the next item: a reply, text or an exception."""

    created: list["FakeClient"] = []

    def __init__(self, script=None, cost: float = 0.01) -> None:
        self.script = list(script) if script is not None else None
        self.cost = cost
        self.thread_ids: set[int] = set()
        self.calls = 0
        self.closed = False
        FakeClient.created.append(self)

    def generate_reply(self, prompt, system_prompt=None):
        self.thread_ids.add(threading.get_ident())
        self.calls += 1
        if self.script is None:
            return make_reply(cost=self.cost)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, HostedReply):
            return item
        return make_reply(text=item, cost=self.cost)

    def close(self) -> None:
        self.closed = True


def make_job(i: int = 1, model: str = "gemma4:26b", arm: str = "A2", project: str = "bitwarden",
             repeat: int = 1) -> rt.Job:
    sp = StoredPrompt(
        study="study2b", project=project, model=model, story_id=f"BW-S2-{i:03d}", arm=arm, run=1,
        attempt=1, system="sys", user_sent="user", user_non_doc="user", doc_context_ref=None,
        doc_context_sha256=None, system_sha256=None, user_sha256=None, local_prompt_tokens=100,
        local_completion_tokens=50, local_n_requirements=3, source_file="x", source_line=1)
    return rt.Job("old", "anchor", project, model, arm, sp.story_id, repeat, "sys", "user", "user",
                  None, None, sp)


def make_jobs(n: int = 8) -> list[rt.Job]:
    return [make_job(i + 1, model=("gemma4:26b" if i % 2 == 0 else "qwen3.8:27b")) for i in range(n)]


@pytest.fixture()
def guard(tmp_path):
    return WriteGuard(allowed_roots=[tmp_path])


@pytest.fixture()
def pin():
    return HOSTED_MODELS["gemma4:26b"]


def run_one(job, client, pin, **kw):
    sleeps: list[float] = []
    out = rt.run_job(job, client, pin, run_name="t1", sleep=sleeps.append, **kw)
    return out, sleeps


# ---------------------------------------------------------------- parse_args


def test_parse_defaults():
    a = rt.parse_args(["anchor", "--run-name", "x1"])
    assert (a.repeats, a.workers, a.max_cost_usd, a.max_attempts, a.max_minutes) == (2, 6, 1.0, 3, 60.0)
    assert not a.dry_run and not a.resume


@pytest.mark.parametrize("name", ["../x", "study2", "STUDY2B", "", "a" * 65])
def test_bad_run_name_exits_2(name):
    with pytest.raises(SystemExit) as e:
        rt.parse_args(["anchor", "--run-name", name])
    assert e.value.code == 2


def test_skip_hash_lists_needs_dry_run_and_bad_arms():
    for extra in (["--skip-hash-lists"], ["--arms", "A"], ["--models", "nope"], ["--workers", "17"]):
        with pytest.raises(SystemExit) as e:
            rt.parse_args(["anchor", "--run-name", "x1", *extra])
        assert e.value.code == 2
    assert rt.parse_args(["anchor", "--run-name", "x1", "--dry-run", "--skip-hash-lists"]).skip_hash_lists


# ---------------------------------------------------------------- the plan


def _sel(**kw):
    a = rt.parse_args(["anchor", "--run-name", "x1", *kw.get("extra", [])])
    return rt.build_selection(a)


def test_anchor_plan_has_120_jobs_in_order():
    jobs = rt.OldConfigurationAnchor().jobs(_sel())
    assert len(jobs) == 120
    assert len({j.key for j in jobs}) == 120
    order = {m: i for i, m in enumerate(("qwen3.8:27b", "gemma4:26b"))}
    keys = [(j.repeat, order[j.model], j.arm, j.project, j.story_id) for j in jobs]
    assert keys == sorted(keys)
    assert {j.arm for j in jobs} == {"A2", "B2"}
    assert all(j.doc_context_ref == ("frozen" if j.arm == "B2" else None) for j in jobs)


def test_stories_filter_and_unknown_story():
    jobs = rt.OldConfigurationAnchor().jobs(_sel(extra=SMOKE + ["--repeats", "1"]))
    assert len(jobs) == 4 and {j.story_id for j in jobs} == {"BW-S2-001"}
    with pytest.raises(rt.SelectionError):
        rt.OldConfigurationAnchor().jobs(_sel(extra=["--stories", "NOPE-1"]))


# ---------------------------------------------------------------- dry run


@pytest.fixture()
def safe_env(monkeypatch):
    """Patch everything that could touch the outside world."""
    ok = {n: HashListResult(n, f"{n}.sha256", True, 1, 1, 0, (), (), "ok") for n in ("study2", "study2b", "vectordb")}
    monkeypatch.setattr(rt, "check_all_hash_lists", lambda: ok)
    monkeypatch.setattr(rt, "list_processes", lambda: [])
    monkeypatch.setattr(rt, "HOSTED_MODELS", {k: dataclasses.replace(v, confirmed=True) for k, v in HOSTED_MODELS.items()})

    def boom(*a, **k):
        pytest.fail("forbidden call")

    monkeypatch.setattr(rt, "OpenRouterClient", boom)
    monkeypatch.setattr(rt, "load_api_key", boom)
    monkeypatch.setattr(socket, "socket", boom)
    return ok


def dry(tmp_path, guard, extra=(), capsys=None):
    root = tmp_path / "runs"
    code = rt.main(["anchor", "--run-name", "d1", "--dry-run", *extra], guard=guard, runs_root=root)
    return code, root


def test_dry_run_writes_nothing(tmp_path, guard, safe_env, monkeypatch, capsys):
    n = {"c": 0}
    real = rt.build_request_body

    def counting(*a, **k):
        n["c"] += 1
        return real(*a, **k)

    monkeypatch.setattr(rt, "build_request_body", counting)
    # Mastodon prompts hold public URLs with "/users/" that the privacy guard refuses (see the
    # test below), so this test uses the two other projects.
    code, root = dry(tmp_path, guard, ["--projects", "keycloak,bitwarden"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert not root.exists() and list(tmp_path.iterdir()) == []
    assert n["c"] == 80
    assert "80 generations" in out and "estimated cost" in out and "key: not read, no network call made" in out
    for name in ("run folder", "hash lists", "stored prompts", "request bodies", "prompt size", "cost estimate"):
        assert name in out


def test_dry_run_full_anchor_passes_privacy_guard_on_mastodon(tmp_path, guard, safe_env, capsys):
    """Stored Mastodon prompts hold public URLs with '/users/'. They must pass the guard (120 jobs)."""
    code, _ = dry(tmp_path, guard)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "120 generations" in out and "[FAIL]" not in out


def test_dry_run_wrong_hash(tmp_path, guard, safe_env, monkeypatch):
    small = SMOKE + ["--repeats", "1"]
    monkeypatch.setattr(rt, "verify_stored_prompt", lambda sp, ctx: (_ for _ in ()).throw(rt.PassagesError("bad hash")))
    assert dry(tmp_path, guard, small)[0] == 2


def test_dry_run_fail_cases_each(tmp_path, guard, safe_env, monkeypatch):
    small = SMOKE + ["--repeats", "1"]
    assert dry(tmp_path, guard, small)[0] == 0
    # fallback context
    real_ctx = rt.read_study2b_context
    monkeypatch.setattr(rt, "read_study2b_context", lambda p, m, s: dataclasses.replace(real_ctx(p, m, s), text=FALLBACK_CONTEXT))
    monkeypatch.setattr(rt, "verify_stored_prompt", lambda sp, ctx: None)
    assert dry(tmp_path, guard, small)[0] == 2
    monkeypatch.setattr(rt, "read_study2b_context", real_ctx)
    # private text in a body
    monkeypatch.setattr(rt, "build_request_body", lambda *a, **k: (_ for _ in ()).throw(GuardError("private")))
    assert dry(tmp_path, guard, small)[0] == 2
    monkeypatch.undo()


def test_dry_run_cost_cap_processes_and_pins(tmp_path, guard, safe_env, monkeypatch):
    small = SMOKE + ["--repeats", "1"]
    assert dry(tmp_path, guard, small + ["--max-cost-usd", "0.00001"])[0] == 2
    rows = [ProcessRow(999991, 1, "python scripts/run_study2b.py"), ProcessRow(999992, 1, "python run_tuning.py anchor")]
    monkeypatch.setattr(rt, "list_processes", lambda: rows)
    assert dry(tmp_path, guard, small)[0] == 2
    monkeypatch.setattr(rt, "list_processes", lambda: [])
    # The pins of the module were confirmed by the run smoke1 on 16 November 2026.
    # So this test makes its own unconfirmed and confirmed copies.
    from dataclasses import replace

    unconfirmed = {k: replace(v, confirmed=False) for k, v in HOSTED_MODELS.items()}
    monkeypatch.setattr(rt, "HOSTED_MODELS", unconfirmed)
    assert dry(tmp_path, guard, small)[0] == 0  # 4 jobs are allowed
    assert dry(tmp_path, guard, ["--repeats", "1"])[0] == 2  # 60 jobs are not
    confirmed = {k: replace(v, confirmed=True) for k, v in HOSTED_MODELS.items()}
    monkeypatch.setattr(rt, "HOSTED_MODELS", confirmed)
    assert dry(tmp_path, guard, ["--repeats", "1"])[0] == 0  # with confirmed pins, 60 jobs pass


# ---------------------------------------------------------------- run_job


def test_run_job_ok_first_attempt(pin):
    out, sleeps = run_one(make_job(), FakeClient([GOOD]), pin)
    assert out.ok and len(out.records) == 1 and sleeps == []
    rec = out.records[0]
    assert rec["error"] is None and rec["attempt"] == 1 and rec["n_requirements"] == 3
    validate_record(rec)


def test_run_job_retries_unreadable_then_ok(pin):
    out, sleeps = run_one(make_job(), FakeClient(["not json", "still not", GOOD], cost=0.02), pin)
    assert out.ok and [r["attempt"] for r in out.records] == [1, 2, 3]
    assert out.records[0]["raw_reply"] == "not json" and out.records[0]["error_kind"] == "unreadable_json"
    assert out.records[2]["error"] is None
    assert sleeps == [1.0, 2.0]
    assert out.cost_usd == pytest.approx(0.06)
    for r in out.records:
        validate_record(r)


def test_run_job_three_failures(pin):
    out, _ = run_one(make_job(), FakeClient(["x", "", '{"a": 1}']), pin)
    assert not out.ok and len(out.records) == 3
    assert [r["error_kind"] for r in out.records] == ["unreadable_json", "unreadable_json", "no_requirements_list"]


def test_run_job_length_cut_is_retried(pin):
    cut = make_reply(text=GOOD[:40], finish="length")
    out, _ = run_one(make_job(), FakeClient([cut, GOOD]), pin)
    assert out.ok and out.records[0]["finish_reason"] == "length" and len(out.records) == 2


def test_run_job_transport_and_fatal_and_contract(pin):
    out, _ = run_one(make_job(), FakeClient([TransientHostedError("down", status=503)]), pin)
    assert out.transport_failed and not out.ok and len(out.records) == 1
    assert out.records[0]["error_kind"] == "transport" and out.records[0]["provider"] is None
    validate_record(out.records[0])

    out, _ = run_one(make_job(), FakeClient([FatalHostedError("no credit", status=402)]), pin)
    assert isinstance(out.fatal, FatalHostedError) and out.records[0]["error_kind"] == "fatal"

    paid = make_reply(cost=0.05, reasoning=12)
    out, _ = run_one(make_job(), FakeClient([ContractViolation("reasoning", reply=paid)]), pin)
    assert out.records[0]["error_kind"] == "contract" and out.cost_usd == pytest.approx(0.05)
    assert out.records[0]["reasoning_tokens"] == 12 and out.records[0]["raw_reply"] == GOOD
    validate_record(out.records[0])


# ---------------------------------------------------------------- execute


def _exec(jobs, run_dir, guard, factory, **kw):
    args = dict(workers=3, max_cost_usd=1.0, max_minutes=60, max_attempts=3, client_factory=factory,
                guard=guard, sleep=lambda s: None)
    args.update(kw)
    return rt.execute_detailed(jobs, run_dir, **args)


def test_execute_full_run_and_threads(tmp_path, guard):
    FakeClient.created.clear()
    run_dir = tmp_path / "r1"
    s = _exec(make_jobs(8), run_dir, guard, lambda pin: FakeClient(), workers=3)
    assert s.exit_code == 0 and s.final_ok == 8 and s.records_written == 8
    n = 0
    for model in ("gemma4:26b", "qwen3.8:27b"):
        recs = read_jsonl(rt.cell_records_path(run_dir, "bitwarden", model))
        assert len(recs) == 4 and all(r["error"] is None for r in recs)
        n += len(recs)
    assert n == 8
    for c in FakeClient.created:
        assert len(c.thread_ids) <= 1 and c.closed
    assert len(FakeClient.created) <= 6
    assert not (run_dir / "run_tuning.lock").exists()
    assert rt.execute(make_jobs(8), run_dir, workers=2, max_cost_usd=1.0, max_minutes=60, max_attempts=3,
                      client_factory=lambda p: FakeClient(), guard=guard) == 0  # all final: nothing to do


def test_cost_cap_stops(tmp_path, guard):
    s = _exec(make_jobs(8), tmp_path / "r2", guard, lambda pin: FakeClient(cost=0.30), workers=1)
    assert s.exit_code == 3 and "cost cap" in s.stop_reason
    assert s.records_written == 4 and s.not_final == 4


def test_time_cap_stops(tmp_path, guard):
    ticks = iter(range(0, 10**6, 600))
    s = _exec(make_jobs(8), tmp_path / "r3", guard, lambda pin: FakeClient(), workers=1,
              max_minutes=25, clock=lambda: next(ticks))
    assert s.exit_code == 3 and "time cap" in s.stop_reason and 1 <= s.records_written < 8


def test_fatal_stops_and_failures_do_not_stop_others(tmp_path, guard):
    # one job fails after 3 unreadable attempts: the others still run
    jobs = make_jobs(4)

    # a client that gives garbage for the first model only
    factory = lambda pin: FakeClient(cost=0.01) if pin.local_name == "qwen3.8:27b" else FakeClient(["x"] * 3 + [GOOD] * 5)  # noqa: E731
    s = _exec(jobs, tmp_path / "r4", guard, factory, workers=1)
    assert s.exit_code == 0 and s.final_failed >= 1 and s.final_ok >= 1

    s = _exec(make_jobs(6), tmp_path / "r5", guard,
              lambda pin: FakeClient([FatalHostedError("no credit", status=402)]), workers=1)
    assert s.exit_code == 3 and "fatal" in s.stop_reason


def test_four_failures_in_a_row_stop(tmp_path, guard):
    s = _exec(make_jobs(8), tmp_path / "r6", guard, lambda pin: FakeClient(["x"] * 99), workers=1)
    assert s.exit_code == 3 and "in a row" in s.stop_reason
    assert s.final_failed == 4


def test_resume_skips_final_and_continues_failed(tmp_path, guard):
    run_dir = tmp_path / "r7"
    jobs = make_jobs(4)
    # first pass: one job gets a transport error (not final), the rest are ok
    first = lambda pin: FakeClient([TransientHostedError("net")] + [GOOD] * 9)  # noqa: E731
    s1 = _exec(jobs, run_dir, guard, first, workers=1)
    assert s1.exit_code == 3 and s1.not_final >= 1
    before = sum(len(read_jsonl(rt.cell_records_path(run_dir, "bitwarden", m))) for m in ("gemma4:26b", "qwen3.8:27b"))
    s2 = _exec(jobs, run_dir, guard, lambda pin: FakeClient(), workers=1)
    assert s2.exit_code == 0 and s2.final_ok == 4 and s2.skipped_final == 4 - s1.not_final
    assert s2.records_written == s1.not_final
    allrecs = []
    for m in ("gemma4:26b", "qwen3.8:27b"):
        allrecs += read_jsonl(rt.cell_records_path(run_dir, "bitwarden", m))
    assert len(allrecs) == before + s1.not_final
    assert max(r["attempt"] for r in allrecs) == 2


def test_lock_and_guard(tmp_path, guard):
    run_dir = tmp_path / "r8"
    from tuning_lib.guard import RunLock

    with RunLock(run_dir, guard=guard):
        with pytest.raises(GuardError):
            _exec(make_jobs(2), run_dir, guard, lambda pin: FakeClient())
    outside = tmp_path.parent / "outside_run"
    with pytest.raises(GuardError):
        _exec(make_jobs(2), outside, guard, lambda pin: FakeClient())
    assert not outside.exists()


# ---------------------------------------------------------------- main (full run with fake client)


def full_run(tmp_path, guard, extra=(), factory=None):
    root = tmp_path / "runs"
    code = rt.main(["anchor", "--run-name", "f1", "--workers", "2", *SMOKE, *extra], guard=guard,
                   runs_root=root, client_factory=factory or (lambda pin: FakeClient()), sleep=lambda s: None)
    return code, root / "f1"


def test_main_run_manifest_and_report(tmp_path, guard, safe_env, monkeypatch, capsys):
    monkeypatch.setattr(rt, "git_info", lambda: {"commit": None, "dirty": None, "error": "git failed: test"})
    code, run_dir = full_run(tmp_path, guard)
    out = capsys.readouterr().out
    assert code == 0, out
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["state"] == "complete" and m["code"]["git"]["error"] and m["code"]["git"]["commit"] is None
    assert m["hash_lists"]["before"] and m["hash_lists"]["after"]
    assert m["jobs"]["planned"] == 8 and m["jobs"]["final_ok"] == 8
    text = (run_dir / "manifest.json").read_text(encoding="utf-8")
    assert "/Users/" not in text and "Bearer" not in text
    assert (run_dir / "report.json").is_file() and (run_dir / "report.md").is_file()
    # a second run in the same folder without --resume is refused
    code2, _ = full_run(tmp_path, guard)
    assert code2 == 2
    # resume with another configuration is refused
    code3, _ = full_run(tmp_path, guard, extra=["--resume", "--repeats", "3"])
    assert code3 == 2
    # resume with the same configuration works and has nothing to do
    code4, _ = full_run(tmp_path, guard, extra=["--resume"])
    assert code4 == 0


def test_main_changed_registered_file_after_run(tmp_path, guard, safe_env, monkeypatch):
    calls = {"n": 0}
    good = safe_env
    bad = dict(good)
    bad["study2b"] = HashListResult("study2b", "x", False, 1, 1, 0, ("coded_project/x",), (), "changed")

    def lists():
        calls["n"] += 1
        return good if calls["n"] == 1 else bad

    monkeypatch.setattr(rt, "check_all_hash_lists", lists)
    monkeypatch.setattr(rt, "git_info", lambda: {"commit": "abc", "dirty": True, "error": None})
    code, run_dir = full_run(tmp_path, guard)
    assert code == 3
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["state"] == "stopped" and m["stop_reason"] == "registered files changed"


def test_writes_stay_in_run_folder(tmp_path, guard, safe_env, monkeypatch):
    monkeypatch.setattr(rt, "git_info", lambda: {"commit": "abc", "dirty": True, "error": None})
    code, run_dir = full_run(tmp_path, guard)
    assert code == 0
    assert [p.name for p in tmp_path.iterdir()] == ["runs"]
    assert [p.name for p in (tmp_path / "runs").iterdir()] == ["f1"]


def test_cmd_run_is_a_stub():
    with pytest.raises(NotImplementedError, match="^run:"):
        rt.main(["run", "--run-name", "x1", "--config", "c"])


# ---------------------------------------------------------------- report


def _rec(model, arm, story, run, text_hits, error=None):
    """A minimal record for the scorer. text_hits: list of bool, one per requirement."""
    reqs = [{"title": "T", "description": ("uses IDENT here" if h else "nothing"), "acceptance_criteria": [], "source_references": []}
            for h in text_hits]
    return {"config_id": "old", "model": model, "arm": arm, "story_id": story, "run": run, "attempt": 1,
            "error": error, "prompt_system": "s", "prompt_user_non_doc": "u", "requirements": reqs,
            "prompt_tokens": 100, "completion_tokens": 10, "n_requirements": len(reqs),
            "latency_ms": 1.0, "finish_reason": "stop", "reasoning_tokens": 0,
            "reasoning_tokens_source": "usage", "cost_usd": 0.01, "parsed_by_fallback": False,
            "old_parser_result": "same", "n_requirements_failed_validation": 0}


def _fixture(tmp_path, hosted_hits_b2, stories=("S1", "S2"), skip=()):
    run_dir = tmp_path / "rep"
    cell = run_dir / "bitwarden" / "gemma4_26b"
    cell.mkdir(parents=True)
    lines = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for arm in ("A2", "B2"):
            for s in stories:
                for run in (1, 2):
                    if (model, arm, s, run) in skip:
                        continue
                    hits = [True, False, False, False] if arm == "A2" else hosted_hits_b2
                    lines.append(_rec(model, arm, s, run, hits))
    (cell / "records.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return run_dir


FACTS = {"S1": ["IDENT"], "S2": ["IDENT"]}


def _ref(b2_local):
    return {(m, a): (0.25 if a == "A2" else b2_local) for m in ("gemma4:26b", "qwen3.8:27b") for a in ("A2", "B2")}


def test_report_arithmetic_and_border(tmp_path):
    # the scorer counts a name that is not in the prompt: hosted B2 rate 0.5 (2 of 4)
    run_dir = _fixture(tmp_path, [True, True, False, False])
    rep = rt.build_report(run_dir, reference=_ref(0.42), fact_sheets=FACTS)
    rows = {(r["model"], r["arm"]): r for r in rep["rows"]}
    assert len(rows) == 4
    b2 = rows[("gemma4:26b", "B2")]
    assert b2["hosted_rate"] == pytest.approx(0.5) and b2["difference_points"] == pytest.approx(8.0)
    assert b2["inside_limit"] is True and b2["complete"] and b2["n_stories"] == 2
    assert b2["repeat_gap_points"] == pytest.approx(0.0)
    a2 = rows[("gemma4:26b", "A2")]
    assert a2["hosted_rate"] == pytest.approx(0.25) and a2["limit_points"] == 5.0
    rep2 = rt.build_report(run_dir, reference=_ref(0.4199), fact_sheets=FACTS)
    assert {(r["model"], r["arm"]): r for r in rep2["rows"]}[("gemma4:26b", "B2")]["inside_limit"] is False
    assert rep2["all_inside_limits"] is False
    assert rep["all_inside_limits"] is True


def test_report_incomplete_has_no_verdict_and_md_rows(tmp_path):
    run_dir = _fixture(tmp_path, [True, True, False, False], skip={("gemma4:26b", "B2", "S2", 2)})
    rep = rt.build_report(run_dir, reference=_ref(0.5), fact_sheets=FACTS)
    row = {(r["model"], r["arm"]): r for r in rep["rows"]}[("gemma4:26b", "B2")]
    assert row["complete"] is False and row["inside_limit"] is None
    assert rep["all_inside_limits"] is None
    md = rt.render_report_md(rep)
    assert md.count("| gemma4:26b |") == 2 and md.count("| qwen3.8:27b |") == 2
    assert "no verdict" in md


def test_cmd_report_writes_files(tmp_path, guard):
    run_dir = _fixture(tmp_path, [True, True, False, False])
    assert rt.cmd_report("rep", guard=guard, runs_root=tmp_path, reference=_ref(0.5), fact_sheets=FACTS) == 0
    assert json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["run_name"] == "rep"
    assert (run_dir / "report.md").is_file()
    assert rt.cmd_report("missing", guard=guard, runs_root=tmp_path) == 2


def test_report_on_copied_study2b_records_is_zero_difference(tmp_path):
    run_dir = tmp_path / "real"
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for project in ("mastodon", "keycloak", "bitwarden"):
            src = STUDY2B / project / model_slug(model) / "records.jsonl"
            lines = []
            for line in src.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec["arm"] not in ("A2", "B2"):
                    continue
                hosted = {"n_requirements_failed_validation": 0, "parsed_by_fallback": False,
                          "old_parser_result": "same", "finish_reason": "stop", "reasoning_tokens": 0,
                          "reasoning_tokens_source": "usage", "cost_usd": 0.0}
                for run in (1, 2):
                    lines.append(json.dumps({**hosted, **rec, "run": run, "config_id": "old"}))
            dst = run_dir / project / model_slug(model)
            dst.mkdir(parents=True)
            (dst / "records.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rep = rt.build_report(run_dir)
    assert len(rep["rows"]) == 4
    for r in rep["rows"]:
        assert r["difference_points"] == pytest.approx(0.0, abs=1e-6)
        assert r["inside_limit"] is True and r["complete"]
    assert rep["all_inside_limits"] is True
    assert measures.load_local_reference()


# ---------------------------------------------------------------- source scan


def test_source_scan():
    src = (ROOT / "scripts" / "run_tuning.py").read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"#.*", "", line) for line in src.splitlines())
    for bad in ("run_study2b", "import run_study2", "chromadb", "ingest", "ollama",
                "secreqgen.retrieval", "secreqgen.ingestion"):
        assert bad not in code.lower(), bad
    for bad in ("write_text(", "mkdir(", "os.makedirs", "shutil", "os.replace", "os.rename", "write_bytes("):
        assert bad not in code, bad
    assert not re.search(r"open\([^)]*['\"][wax]", code)


# ---------------------------------------------------------------- review round 2


def test_subset_of_stories_gives_no_verdict(tmp_path):
    run_dir = _fixture(tmp_path, [True, True, False, False], stories=("S1",))
    manifest = {"state": "complete", "options": {"stories": ["S1"], "repeats": 2}, "models": {}}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # a reference far from the hosted rate: a full run would be outside the limit
    rep = rt.build_report(run_dir, reference=_ref(0.9), fact_sheets={"S1": ["IDENT"]})
    assert all(r["inside_limit"] is None for r in rep["rows"])
    assert rep["all_inside_limits"] is None
    assert any("subset of stories" in n for n in rep["notes"])
    assert "subset of stories" in rt.render_report_md(rep)


def test_resume_counts_cost_of_earlier_sessions(tmp_path, guard):
    run_dir = tmp_path / "rc"
    jobs = make_jobs(8)
    shared = [GOOD] * 3 + [TransientHostedError("net")] * 60

    class Shared(FakeClient):
        def generate_reply(self, prompt, system_prompt=None):
            item = shared.pop(0)
            if isinstance(item, Exception):
                raise item
            return make_reply(text=item, cost=0.3)

    s1 = _exec(jobs, run_dir, guard, lambda pin: Shared(), workers=1)
    assert s1.exit_code == 3 and s1.cost_usd == pytest.approx(0.9)
    s2 = _exec(jobs, run_dir, guard, lambda pin: FakeClient(cost=0.3), workers=1)
    assert s2.exit_code == 3 and "cost cap" in s2.stop_reason
    assert s2.submitted <= 1
    assert s2.cost_usd >= 0.9


def test_resume_refuses_other_stories_or_max_attempts(tmp_path, guard, safe_env, monkeypatch, capsys):
    monkeypatch.setattr(rt, "git_info", lambda: {"commit": None, "dirty": None, "error": "git failed: test"})
    code, _ = full_run(tmp_path, guard)
    assert code == 0
    capsys.readouterr()
    code2, _ = full_run(tmp_path, guard, extra=["--resume", "--stories", "BW-S2-002"])
    out2 = capsys.readouterr().out
    assert code2 == 2 and "differs from the manifest" in out2 and "stories" in out2
    code3, _ = full_run(tmp_path, guard, extra=["--resume", "--max-attempts", "2"])
    out3 = capsys.readouterr().out
    assert code3 == 2 and "max_attempts" in out3


def test_key_never_reaches_files_or_output(tmp_path, guard, safe_env, monkeypatch, capsys):
    import httpx

    from tuning_lib import openrouter_client as oc

    secret = "sk-or-v1-LEAKTEST-0123456789"
    monkeypatch.setattr(rt, "git_info", lambda: {"commit": None, "dirty": None, "error": "git failed: test"})
    pins = {p.model_id: p for p in oc.HOSTED_MODELS.values()}
    calls = {"n": 0}

    def good(model_id: str) -> httpx.Response:
        pin = pins[model_id]
        body = {
            "id": f"gen-{calls['n']}", "model": pin.model_id + "-20260101",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": GOOD}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
                      "completion_tokens_details": {"reasoning_tokens": 0}},
            "openrouter_metadata": {"endpoints": {"available": [
                {"provider": pin.expected_provider_name, "selected": True}]}},
        }
        return httpx.Response(200, json=body)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 3:
            return httpx.Response(500, json={"error": {"message": f"boom {secret}"}})
        if calls["n"] == 5:
            return httpx.Response(400, json={"error": {"message": f"bad request, key {secret}"}})
        return good(json.loads(request.content)["model"])

    root = tmp_path / "runs"
    code = rt.main(
        ["anchor", "--run-name", "k1", "--workers", "1", *SMOKE], guard=guard, runs_root=root,
        client_factory=lambda pin: oc.OpenRouterClient(
            pin, oc.ApiKey(secret), transport=httpx.MockTransport(handler), sleep=lambda s: None),
        sleep=lambda s: None,
    )
    captured = capsys.readouterr()
    assert code == 3  # the 400 stops the run
    assert calls["n"] >= 5
    files = [p for p in (root / "k1").rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert secret not in text and "sk-or-" not in text, path
    assert secret not in captured.out + captured.err and "sk-or-" not in captured.out + captured.err
