"""Tests of the prompt lab runner: scripts/run_tuning.py, subcommand "run" and its report.

No network, no key, no model. A fake client answers. Output goes to tmp_path.
Prompt versions for the tests live in a temporary versions root.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_tuning as rt  # noqa: E402
from tuning_lib import lab_measures  # noqa: E402
from tuning_lib.guard import GuardError, HashListResult, WriteGuard  # noqa: E402
from tuning_lib.openrouter_client import (  # noqa: E402
    HOSTED_MODELS,
    HostedReply,
    TransientHostedError,
)
from tuning_lib.paths import MODELS, STUDY2B, model_slug  # noqa: E402
from tuning_lib.prompts import VERSIONS_ROOT, PromptBuilder, new_version  # noqa: E402
from tuning_lib.records import read_jsonl, validate_record  # noqa: E402

DEV_STORY = "BW-S2-001"
HOLDOUT_STORY = "BW-S2-003"


# ---------------------------------------------------------------- helpers


def _good_answer() -> str:
    path = STUDY2B / "bitwarden" / model_slug("gemma4:26b") / "records.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("error") is None and rec.get("requirements"):
            return json.dumps({"requirements": rec["requirements"][:3]})
    raise AssertionError("no stored record with requirements")


GOOD = _good_answer()


def make_reply(text: str = GOOD, cost: float = 0.01) -> HostedReply:
    return HostedReply(
        text=text, generation_id="gen-1", response_model="x/y", provider="Parasail",
        provider_source="metadata", finish_reason="stop", prompt_tokens=100, completion_tokens=50,
        reasoning_tokens=0, reasoning_tokens_source="usage", cached_tokens=0, cost_usd=cost,
        latency_ms=12.5, transport_retries=0)


class FakeClient:
    """Answers with a valid reply. It keeps every request (system, prompt)."""

    requests: list[tuple[str | None, str]] = []

    def __init__(self, script=None, cost: float = 0.01) -> None:
        self.script = list(script) if script is not None else None
        self.cost = cost

    def generate_reply(self, prompt, system_prompt=None):
        FakeClient.requests.append((system_prompt, prompt))
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return make_reply(text=item, cost=self.cost)
        return make_reply(cost=self.cost)

    def close(self) -> None:
        pass


@pytest.fixture()
def guard(tmp_path):
    return WriteGuard(allowed_roots=[tmp_path], forbidden_roots=[], forbidden_names=[])


@pytest.fixture()
def pv_root(tmp_path):
    """A temporary versions root with v0_old (a byte copy) and v1 (one added general sentence)."""
    root = tmp_path / "pv"
    root.mkdir()
    shutil.copytree(VERSIONS_ROOT / "v0_old", root / "v0_old")
    g = WriteGuard(allowed_roots=[root], forbidden_roots=[], forbidden_names=[])
    folder = new_version("v1", "v0_old", "Adds one general sentence about checkable requirements.",
                         root=root, guard=g)
    system = folder / "system_prompt.txt"
    system.write_text(system.read_text(encoding="utf-8") + "\nWrite each requirement so that a tester can check it.\n",
                      encoding="utf-8")
    return root


@pytest.fixture()
def builder(pv_root):
    return PromptBuilder(versions_root=pv_root)


@pytest.fixture()
def safe_env(monkeypatch):
    ok = {n: HashListResult(n, f"{n}.sha256", True, 1, 1, 0, (), (), "ok") for n in ("study2", "study2b", "vectordb")}
    monkeypatch.setattr(rt, "check_all_hash_lists", lambda: ok)
    monkeypatch.setattr(rt, "list_processes", lambda: [])
    monkeypatch.setattr(rt, "HOSTED_MODELS", {k: dataclasses.replace(v, confirmed=True) for k, v in HOSTED_MODELS.items()})
    monkeypatch.setattr(rt, "git_info", lambda: {"commit": "abc", "dirty": False, "error": None})

    def boom(*a, **k):
        pytest.fail("forbidden call")

    monkeypatch.setattr(rt, "OpenRouterClient", boom)
    monkeypatch.setattr(rt, "load_api_key", boom)
    monkeypatch.setattr(socket, "socket", boom)
    return ok


def lab(tmp_path, guard, builder, *extra, name="L1", factory=None, sleep=lambda s: None, **kw):
    argv = ["run", "--run-name", name, *extra]
    return rt.main(argv, guard=guard, runs_root=tmp_path / "runs", client_factory=factory,
                   sleep=sleep, lab_builder=builder, **kw)


SMALL = ["--versions", "v0_old,v1", "--stories", DEV_STORY, "--repeats", "2", "--workers", "2"]


# ---------------------------------------------------------------- plan


def test_plan_has_120_jobs_and_mixed_versions(builder):
    stories = rt.load_splits().dev
    sel = rt.Selection(("mastodon", "keycloak", "bitwarden"), MODELS, ("B2",), frozenset(stories), 3)
    jobs = rt.plan_lab_jobs(sel, ["v0_old", "v1"], order_seed=1, builder=builder)
    assert len(jobs) == 120 and len({j.key for j in jobs}) == 120
    assert {j.step for j in jobs} == {"lab"} and {j.arm for j in jobs} == {"B2"}
    assert {j.config_id for j in jobs} == {"v0_old", "v1"}
    # the two versions of one (repeat, story, model) are neighbours
    groups = [(jobs[i].repeat, jobs[i].story_id, jobs[i].model) for i in range(0, 120, 2)]
    for i in range(0, 120, 2):
        assert (jobs[i].repeat, jobs[i].story_id, jobs[i].model) == (jobs[i + 1].repeat, jobs[i + 1].story_id, jobs[i + 1].model)
        assert {jobs[i].version, jobs[i + 1].version} == {"v0_old", "v1"}
    assert [g[0] for g in groups] == sorted(g[0] for g in groups)  # repeats come in order
    assert [j.repeat for j in jobs[:40]] == [1] * 40  # a stop leaves complete repeats
    # the version order depends on the seed and is the same for the same seed
    again = rt.plan_lab_jobs(sel, ["v0_old", "v1"], order_seed=1, builder=builder)
    other = rt.plan_lab_jobs(sel, ["v0_old", "v1"], order_seed=2, builder=builder)
    assert [j.key for j in jobs] == [j.key for j in again]
    assert [j.key for j in jobs] != [j.key for j in other]
    first = [j.version for j in jobs[0::2]]
    assert "v0_old" in first and "v1" in first  # not a fixed order


def test_v0_old_jobs_equal_the_stored_prompt(builder):
    sel = rt.Selection(("bitwarden",), MODELS, ("B2",), frozenset({DEV_STORY}), 1)
    jobs = rt.plan_lab_jobs(sel, ["v0_old", "v1"], builder=builder)
    for j in jobs:
        if j.version == "v0_old":
            assert j.system == j.stored.system and j.user_sent == j.stored.user_sent
            assert j.user_non_doc == j.stored.user_non_doc
        else:
            assert j.system != j.stored.system and j.user_sent == j.stored.user_sent


def test_passage_change_version_has_no_code_hits(pv_root):
    meta = pv_root / "v1" / "version.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["passage_changes"] = ["drop_code_hits"]
    meta.write_text(json.dumps(data), encoding="utf-8")
    b = PromptBuilder(versions_root=pv_root)
    sel = rt.Selection(("keycloak",), MODELS, ("B2",), frozenset({"KC-S2-001"}), 1)
    jobs = rt.plan_lab_jobs(sel, ["v0_old", "v1"], builder=b)
    new = [j for j in jobs if j.version == "v1"]
    old = [j for j in jobs if j.version == "v0_old"]
    assert all(j.n_code_hits == 0 for j in new)
    assert any(o.n_code_hits > 0 and o.doc_context_sha256 != n.doc_context_sha256
               for o, n in zip(sorted(old, key=lambda j: j.model), sorted(new, key=lambda j: j.model)))


# ---------------------------------------------------------------- splits and options


def test_parse_defaults_and_required(guard):
    a = rt.parse_args(["run", "--run-name", "x1", "--versions", "v0_old", "--stories", "@dev"])
    assert (a.repeats, a.workers, a.max_cost_usd, a.max_attempts, a.order_seed, a.models) == (3, 6, 1.0, 3, 1, ["both"])
    for bad in (["--versions", "v0_old"], ["--stories", "@dev"],
                ["--versions", "a,a", "--stories", "@dev"],
                ["--versions", "v0_old", "--stories", "@dev", "--skip-hash-lists"],
                ["--versions", "v0_old", "--stories", "@dev", "--models", "nope"],
                ["--versions", "v0_old", "--stories", "@dev", "--repeats", "0"]):
        with pytest.raises(SystemExit) as e:
            rt.parse_args(["run", "--run-name", "x1", *bad])
        assert e.value.code == 2


def test_holdout_needs_final_check_also_in_dry_run(tmp_path, guard, builder, safe_env, capsys):
    for stories in ("@holdout", f"{DEV_STORY},{HOLDOUT_STORY}"):
        code = lab(tmp_path, guard, builder, "--versions", "v0_old", "--stories", stories, "--dry-run")
        assert code == 2
        assert "need --final-check" in capsys.readouterr().out
    code = lab(tmp_path, guard, builder, "--versions", "v0_old", "--stories", DEV_STORY, "--final-check", "--dry-run")
    assert code == 2 and "only for runs that use hold-out" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()


def test_holdout_with_final_check_is_written_to_manifest_and_log(tmp_path, guard, builder, safe_env, capsys):
    argv = ["--versions", "v0_old,v1", "--stories", HOLDOUT_STORY, "--final-check", "--repeats", "1",
            "--models", "gemma4:26b"]
    dry = lab(tmp_path, guard, builder, *argv, "--dry-run")
    out = capsys.readouterr().out
    assert dry == 0 and "HOLD-OUT STORIES USED: BW-S2-003" in out
    assert not (tmp_path / "runs").exists()  # a dry run writes nothing
    code = lab(tmp_path, guard, builder, *argv, factory=lambda pin: FakeClient())
    assert code == 0, capsys.readouterr().out
    run_dir = tmp_path / "runs" / "L1"
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["options"]["final_check"] is True and m["options"]["holdout_used"] == [HOLDOUT_STORY]
    assert any("hold-out" in n for n in m["notes"])
    lines = (tmp_path / "runs" / rt.HOLDOUT_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["run_name"] == "L1"


def test_second_holdout_use_is_refused_without_a_reason(tmp_path, guard, builder, safe_env, capsys):
    argv = ["--versions", "v0_old,v1", "--stories", HOLDOUT_STORY, "--final-check", "--repeats", "1",
            "--models", "gemma4:26b"]
    assert lab(tmp_path, guard, builder, *argv, factory=lambda pin: FakeClient()) == 0
    capsys.readouterr()
    # another run name: refused, also in a dry run, nothing written
    for extra in (["--dry-run"], []):
        code = lab(tmp_path, guard, builder, *argv, *extra, name="L2", factory=lambda pin: FakeClient())
        out = capsys.readouterr().out
        assert code == 2 and "hold-out used before by run L1" in out
    assert not (tmp_path / "runs" / "L2").exists()
    # an empty reason is refused
    assert lab(tmp_path, guard, builder, *argv, "--holdout-reuse-reason", " ", name="L2") == 2
    capsys.readouterr()
    # a reason allows it; it goes to the log and the manifest
    code = lab(tmp_path, guard, builder, *argv, "--holdout-reuse-reason", "tool fault in L1", name="L2",
               factory=lambda pin: FakeClient())
    assert code == 0, capsys.readouterr().out
    lines = [json.loads(x) for x in (tmp_path / "runs" / rt.HOLDOUT_LOG_NAME).read_text(encoding="utf-8").splitlines()]
    assert [x["run_name"] for x in lines] == ["L1", "L2"] and lines[1]["reuse_reason"] == "tool fault in L1"
    m = json.loads((tmp_path / "runs" / "L2" / "manifest.json").read_text(encoding="utf-8"))
    assert any("tool fault in L1" in n for n in m["notes"])


def test_reuse_reason_needs_holdout(tmp_path, guard, builder, safe_env, capsys):
    code = lab(tmp_path, guard, builder, *SMALL, "--holdout-reuse-reason", "x", "--dry-run")
    assert code == 2 and "only for runs that use hold-out" in capsys.readouterr().out


def test_dev_run_has_final_check_false(tmp_path, guard, builder, safe_env):
    code = lab(tmp_path, guard, builder, *SMALL, "--models", "gemma4:26b", factory=lambda pin: FakeClient())
    assert code == 0
    m = json.loads((tmp_path / "runs" / "L1" / "manifest.json").read_text(encoding="utf-8"))
    assert m["options"]["final_check"] is False and m["options"]["holdout_used"] == []
    assert not (tmp_path / "runs" / rt.HOLDOUT_LOG_NAME).exists()


def test_splits_rules(tmp_path):
    s = rt.load_splits()
    assert len(s.dev) == 10 and len(s.holdout) == 5
    ids, used = rt.resolve_stories("@dev", s, final_check=False)
    assert ids == s.dev and not used
    ids, used = rt.resolve_stories(f"{s.holdout[1]},{s.dev[3]}", s, final_check=True)
    assert ids == (s.dev[3], s.holdout[1]) and used
    for spec, fc in (("@holdout", False), ("nope", False), ("a,,b", False), (f"{s.dev[0]},{s.dev[0]}", False)):
        with pytest.raises(rt.SplitError):
            rt.resolve_stories(spec, s, final_check=fc)
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"dev": ["a"], "holdout": [], "rule": "x"}), encoding="utf-8")
    with pytest.raises(rt.SplitError):
        rt.load_splits(bad)


# ---------------------------------------------------------------- dry run


def test_dry_run_writes_nothing_and_reads_no_key(tmp_path, guard, builder, safe_env, capsys):
    code = lab(tmp_path, guard, builder, "--versions", "v0_old,v1", "--stories", "@dev", "--dry-run")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "120 generations" in out and "key: not read, no network call made" in out
    assert "order of the first 12 jobs" in out and "estimated cost" in out
    assert list(tmp_path.iterdir()) == [tmp_path / "pv"]


def test_dry_run_without_baseline_warns(tmp_path, guard, builder, safe_env, capsys):
    code = lab(tmp_path, guard, builder, "--versions", "v1", "--stories", DEV_STORY, "--dry-run")
    assert code == 0 and "no baseline in this run" in capsys.readouterr().out


# ---------------------------------------------------------------- refusals


def test_version_with_a_product_word_is_refused(tmp_path, guard, pv_root, safe_env, capsys):
    f = pv_root / "v1" / "system_prompt.txt"
    f.write_text(f.read_text(encoding="utf-8") + "\nThink about Keycloak realms.\n", encoding="utf-8")
    b = PromptBuilder(versions_root=pv_root)
    code = lab(tmp_path, guard, b, *SMALL, factory=lambda pin: FakeClient())
    assert code == 2 and "generality" in capsys.readouterr().out.lower()
    assert not (tmp_path / "runs").exists()


def test_tampered_baseline_is_refused(tmp_path, guard, pv_root, safe_env, capsys):
    f = pv_root / "v0_old" / "rag_user_prompt.txt"
    f.write_text(f.read_text(encoding="utf-8").replace("security", "safety", 1), encoding="utf-8")
    b = PromptBuilder(versions_root=pv_root)
    code = lab(tmp_path, guard, b, *SMALL, factory=lambda pin: FakeClient())
    assert code == 2 and "PromptMismatch" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()


def test_unknown_version_and_existing_folder(tmp_path, guard, builder, safe_env, capsys):
    assert lab(tmp_path, guard, builder, "--versions", "v0_old,v9", "--stories", DEV_STORY) == 2
    assert lab(tmp_path, guard, builder, *SMALL, factory=lambda pin: FakeClient()) == 0
    assert lab(tmp_path, guard, builder, *SMALL, factory=lambda pin: FakeClient()) == 2  # folder exists, no --resume


# ---------------------------------------------------------------- execution


def test_full_small_run_records_and_manifest(tmp_path, guard, builder, safe_env, capsys):
    FakeClient.requests.clear()
    code = lab(tmp_path, guard, builder, *SMALL, factory=lambda pin: FakeClient())
    out = capsys.readouterr().out
    assert code == 0, out
    run_dir = tmp_path / "runs" / "L1"
    recs = read_jsonl(rt.cell_records_path(run_dir, "bitwarden", "gemma4:26b")) + \
        read_jsonl(rt.cell_records_path(run_dir, "bitwarden", "qwen3.8:27b"))
    assert len(recs) == 8  # 1 story x 2 models x 2 versions x 2 repeats
    sent = {(s, u) for s, u in FakeClient.requests}
    for r in recs:
        validate_record(r)
        assert r["step"] == "lab" and r["arm"] == "B2" and r["config_id"] in ("v0_old", "v1")
        assert (r["prompt_system"], r["prompt_user_sent"]) in sent
        facts = json.loads(r["arm_description"].split("lab facts: ", 1)[1])
        assert facts["version"] == r["config_id"] and facts["n_doc_passages"] >= 1
        assert set(facts["template_sha256"]) == {"system_prompt.txt", "rag_user_prompt.txt", "zero_shot_user_prompt.txt"}
        assert r["prompt_check"].startswith("built from the template folder")
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["step"] == "lab" and m["state"] == "complete" and m["options"]["versions"] == ["v0_old", "v1"]
    assert set(m["configuration"]["prompt_versions"]) == {"v0_old", "v1"}
    assert "python" in m["versions"] and m["jobs"]["per_model_arm"]["gemma4:26b/v1"] == 2
    assert "splits_sha256" in m["inputs"] and "analysis_s2b_sha256" not in m["inputs"]
    assert (run_dir / "report.json").is_file() and (run_dir / "report.md").is_file()
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert rep["baseline"] == "v0_old" and len(rep["cells"]) == 4
    assert {c["metric"] for c in rep["comparisons"]} == set(rt.LAB_METRICS)
    # the lock is gone at the end
    assert not (run_dir / "run_tuning.lock").exists()


def test_lock_is_held_until_manifest_and_report_are_written(tmp_path, guard, builder, safe_env, monkeypatch):
    seen: dict[str, bool] = {}
    real_write, real_report = rt.write_manifest, rt.cmd_report

    def write_manifest(run_dir, manifest, g):
        if manifest["state"] != "running":
            seen["final_manifest"] = (run_dir / rt.LOCK_NAME).exists()
        return real_write(run_dir, manifest, g)

    def cmd_report(name, **kw):
        seen["report"] = ((tmp_path / "runs" / name) / rt.LOCK_NAME).exists()
        return real_report(name, **kw)

    monkeypatch.setattr(rt, "write_manifest", write_manifest)
    monkeypatch.setattr(rt, "cmd_report", cmd_report)
    code = lab(tmp_path, guard, builder, *SMALL, "--models", "gemma4:26b", factory=lambda pin: FakeClient())
    assert code == 0
    assert seen == {"final_manifest": True, "report": True}
    assert not (tmp_path / "runs" / "L1" / rt.LOCK_NAME).exists()


def test_resume_skips_finished_jobs_and_checks_edits(tmp_path, guard, builder, safe_env, pv_root, capsys):
    argv = ["--versions", "v0_old,v1", "--stories", DEV_STORY, "--repeats", "2", "--models", "gemma4:26b",
            "--workers", "1", "--max-cost-usd", "0.025"]
    code = lab(tmp_path, guard, builder, *argv, factory=lambda pin: FakeClient(cost=0.01))
    assert code == 3  # the cost cap stopped it after some generations
    run_dir = tmp_path / "runs" / "L1"
    done = read_jsonl(rt.cell_records_path(run_dir, "bitwarden", "gemma4:26b"))
    assert 0 < len(done) < 4
    FakeClient.requests.clear()
    argv2 = [a if a != "0.025" else "1.0" for a in argv]
    code = lab(tmp_path, guard, builder, *argv2, "--resume", factory=lambda pin: FakeClient())
    assert code == 0, capsys.readouterr().out
    assert len(FakeClient.requests) == 4 - len(done)
    final = read_jsonl(rt.cell_records_path(run_dir, "bitwarden", "gemma4:26b"))
    assert len({(r["config_id"], r["run"]) for r in final}) == 4
    # a version edited after its first records cannot be resumed
    f = pv_root / "v1" / "system_prompt.txt"
    f.write_text(f.read_text(encoding="utf-8") + "\nAnother general sentence for the writer.\n", encoding="utf-8")
    b2 = PromptBuilder(versions_root=pv_root)
    assert lab(tmp_path, guard, b2, *argv2, "--resume", factory=lambda pin: FakeClient()) == 2
    # other repeats cannot be resumed either
    assert lab(tmp_path, guard, builder, *[("3" if a == "2" else a) for a in argv2], "--resume",
               factory=lambda pin: FakeClient()) == 2


def test_parse_retry_and_failed_key_in_the_report(tmp_path, guard, builder, safe_env):
    script = ["not json", GOOD]  # first job: one parse retry, then ok
    calls = {"n": 0}

    def factory(pin):
        calls["n"] += 1
        return FakeClient(script=script if calls["n"] == 1 else None)

    code = lab(tmp_path, guard, builder, "--versions", "v0_old", "--stories", DEV_STORY, "--repeats", "1",
               "--models", "gemma4:26b", "--workers", "1", factory=factory)
    assert code == 0
    rep = json.loads((tmp_path / "runs" / "L1" / "report.json").read_text(encoding="utf-8"))
    cell = rep["cells"][0]
    assert cell["parse_failures"]["attempt_lines_failed"] == 1 and cell["n_final_ok"] == 1
    assert rep["totals"]["n_parse_retries"] == 1


# ---------------------------------------------------------------- transport retries


def test_transport_retries_are_stored_and_not_parse_retries():
    sp = rt.StoredPrompt(
        study="study2b", project="bitwarden", model="gemma4:26b", story_id=DEV_STORY, arm="B2", run=1,
        attempt=1, system="s", user_sent="u", user_non_doc="u", doc_context_ref=None,
        doc_context_sha256=None, system_sha256=None, user_sha256=None, local_prompt_tokens=10,
        local_completion_tokens=5, local_n_requirements=1, source_file="x", source_line=1)
    job = rt.Job("v0_old", "lab", "bitwarden", "gemma4:26b", "B2", DEV_STORY, 1, "s", "u", "u", None, None, sp)
    pin = HOSTED_MODELS["gemma4:26b"]
    out = rt.run_job(job, FakeClient([TransientHostedError("timeout after 3 retries")]), pin,
                     run_name="t1", sleep=lambda s: None)
    rec = out.records[0]
    assert rec["error_kind"] == "transport" and rec["transport_retries"] == 3
    validate_record(rec)
    ok = rt.run_job(job, FakeClient(), pin, run_name="t1", start_attempt=2, sleep=lambda s: None).records[0]
    totals = rt._totals_dict([rec, ok])
    assert totals["n_parse_retries"] == 0 and totals["n_transport_failed_lines"] == 1
    assert totals["n_transport_retries"] == 3
    bad = dict(rec, error_kind="unreadable_json", transport_retries=0)
    assert rt._totals_dict([bad, ok])["n_parse_retries"] == 1


# ---------------------------------------------------------------- report arithmetic (invented records)

IDENT = "IDENT"
FACTS = {"S1": [IDENT], "S2": [IDENT], "S3": [IDENT]}


def _req(text: str) -> dict:
    return {"title": "T", "description": text, "acceptance_criteria": [], "source_references": []}


def _rec(version, model, story, run, texts, *, error=None, cost=0.01):
    reqs = [_req(t) for t in texts]
    return {"config_id": version, "model": model, "arm": "B2", "step": "lab", "story_id": story, "run": run,
            "attempt": 1, "error": error, "error_kind": None if error is None else "unreadable_json",
            "prompt_system": "s", "prompt_user_non_doc": "u", "requirements": [] if error else reqs,
            "prompt_tokens": 100, "completion_tokens": 10, "n_requirements": 0 if error else len(reqs),
            "latency_ms": 1.0, "finish_reason": "stop", "reasoning_tokens": 0,
            "reasoning_tokens_source": "usage", "cost_usd": cost, "parsed_by_fallback": False,
            "old_parser_result": "same", "n_requirements_failed_validation": 0, "transport_retries": 0}


def _write(run_dir: Path, records: list[dict]) -> None:
    by: dict[tuple, list] = {}
    for r in records:
        by.setdefault((r["model"]), []).append(r)
    for model, rs in by.items():
        cell = run_dir / "bitwarden" / model_slug(model)
        cell.mkdir(parents=True, exist_ok=True)
        (cell / "records.jsonl").write_text("\n".join(json.dumps(r) for r in rs) + "\n", encoding="utf-8")


def _fixed_reference(monkeypatch, seen=None):
    def build_reference(story_id, model, *, builder=None):
        if seen is not None:
            seen.append((story_id, model))
        return lab_measures.Reference(story_id=story_id, model=model, text="uses alpha_key only",
                                      sha256="0" * 64, vocab=frozenset({"alpha_key", "beta_key"}))

    monkeypatch.setattr(lab_measures, "build_reference", build_reference)
    monkeypatch.setattr(lab_measures, "baseline_prompt_text", lambda sid, model, builder=None: "s\nu")


def _fixture(tmp_path):
    """v0_old: 2 requirements with the name IDENT in 1 of them; v1: 2 of 2. 3 stories, 2 models, 2 repeats."""
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for story in ("S1", "S2", "S3"):
            for run in (1, 2):
                recs.append(_rec("v0_old", model, story, run, ["uses IDENT here", "nothing"]))
                recs.append(_rec("v1", model, story, run, ["uses IDENT here", "also IDENT"]))
    run_dir = tmp_path / "rep"
    _write(run_dir, recs)
    return run_dir


def test_report_cells_and_comparison_arithmetic(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    rep = rt.build_lab_report(_fixture(tmp_path), fact_sheets=FACTS, builder=object())
    cells = {(c["version"], c["model"]): c for c in rep["cells"]}
    assert len(cells) == 4 and rep["baseline"] == "v0_old"
    c0, c1 = cells[("v0_old", "gemma4:26b")], cells[("v1", "gemma4:26b")]
    assert c0["name_use"] == pytest.approx(0.5) and c1["name_use"] == pytest.approx(1.0)
    assert c0["requirements_per_answer"] == 2.0 and c0["n_final_ok"] == 6 and c0["n_planned"] == 6
    assert c0["n_with_name_per_story"] == {"S1": 1.0, "S2": 1.0, "S3": 1.0}
    assert c0["cost_usd"] == pytest.approx(0.06)
    assert c0["parse_failures"] == {"attempt_lines_failed": 0, "transport_lines": 0, "final_failed_keys": 0}
    comp = {c["metric"]: c for c in rep["comparisons"]}
    nu = comp["name_use"]
    assert nu["n_stories"] == 3 and nu["stories_missing"] == []
    assert nu["per_story"] == {s: pytest.approx(0.5) for s in ("S1", "S2", "S3")}
    assert nu["mean"] == pytest.approx(0.5) and nu["se"] == pytest.approx(0.0)
    assert (nu["up"], nu["equal"], nu["down"]) == (3, 0, 0)
    assert nu["per_model"]["gemma4:26b"]["sign"] == "+" and nu["per_model"]["qwen3.8:27b"]["sign"] == "+"
    assert comp["n_requirements"]["mean"] == pytest.approx(0.0) and comp["n_requirements"]["equal"] == 3
    assert comp["n_requirements"]["per_model"]["gemma4:26b"]["sign"] == "0"


def test_report_standard_error_by_hand(tmp_path, monkeypatch):
    """Story differences 0.5, 0.0 and -0.5 in name use: mean 0, sd 0.5, se 0.5/sqrt(3); one up, one equal, one down."""
    _fixed_reference(monkeypatch)
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for story, (a, b) in {"S1": (["IDENT", "x"], ["IDENT", "IDENT"]), "S2": (["IDENT", "x"], ["IDENT", "x"]),
                              "S3": (["IDENT", "IDENT"], ["IDENT", "x"])}.items():
            recs.append(_rec("v0_old", model, story, 1, a))
            recs.append(_rec("v1", model, story, 1, b))
    run_dir = tmp_path / "se"
    _write(run_dir, recs)
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    nu = {c["metric"]: c for c in rep["comparisons"]}["name_use"]
    assert nu["per_story"] == {"S1": pytest.approx(0.5), "S2": pytest.approx(0.0), "S3": pytest.approx(-0.5)}
    assert nu["mean"] == pytest.approx(0.0, abs=1e-12) and nu["se"] == pytest.approx(0.5 / 3 ** 0.5)
    assert (nu["up"], nu["equal"], nu["down"]) == (1, 1, 1)
    assert nu["per_model"]["gemma4:26b"]["sign"] == "0"


def test_report_uses_one_fixed_reference_and_two_measures(tmp_path, monkeypatch):
    seen: list = []
    _fixed_reference(monkeypatch, seen)
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for story in ("S1", "S2", "S3"):
            # v0_old: one name inside the reference, one requirement with no name
            recs.append(_rec("v0_old", model, story, 1, ["use `alpha_key` now", "no name here"]))
            # v1: one name inside, one name outside the reference
            recs.append(_rec("v1", model, story, 1, ["use `alpha_key` now", "use `beta_key` now"]))
    run_dir = tmp_path / "fx"
    _write(run_dir, recs)
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    cells = {(c["version"], c["model"]): c for c in rep["cells"]}
    a, b = cells[("v0_old", "gemma4:26b")], cells[("v1", "gemma4:26b")]
    assert (a["outside_share"], a["all_inside_share"], a["no_name_share"]) == (0.0, 0.5, 0.5)
    assert (b["outside_share"], b["all_inside_share"], b["no_name_share"]) == (0.5, 0.5, 0.0)
    # the reference is asked for each story and model, never for each version
    assert sorted(seen) == sorted({(s, m) for s in ("S1", "S2", "S3") for m in ("gemma4:26b", "qwen3.8:27b")})
    out = {c["metric"]: c for c in rep["comparisons"]}["outside_share"]
    assert out["mean"] == pytest.approx(0.5) and out["up"] == 3


def test_failed_share_is_a_metric_and_a_dead_story_is_not_dropped(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for story in ("S1", "S2"):
            recs.append(_rec("v0_old", model, story, 1, ["uses IDENT", "nothing"]))
            # v1 breaks the JSON on S2 for gemma on all 3 attempts (final failed)
            if model == "gemma4:26b" and story == "S2":
                for attempt in (1, 2, 3):
                    r = _rec("v1", model, story, 1, [], error="UnreadableAnswer: bad")
                    r["attempt"] = attempt
                    recs.append(r)
            else:
                recs.append(_rec("v1", model, story, 1, ["uses IDENT", "uses IDENT"]))
    run_dir = tmp_path / "ff"
    _write(run_dir, recs)
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    assert rt.LAB_METRICS[0] == "failed_share"
    cells = {(c["version"], c["model"]): c for c in rep["cells"]}
    assert cells[("v1", "gemma4:26b")]["failed_share"] == pytest.approx(0.5)
    assert cells[("v0_old", "gemma4:26b")]["failed_share"] == 0.0
    comp = {c["metric"]: c for c in rep["comparisons"]}
    fs = comp["failed_share"]
    assert fs["n_stories"] == 2 and fs["stories_missing"] == []
    assert fs["per_story"] == {"S1": 0.0, "S2": pytest.approx(0.5)}  # mean of gemma 1.0 and qwen 0.0
    assert fs["per_model"]["gemma4:26b"]["up"] == 1
    assert comp["name_use"]["stories_missing"] == ["S2"]  # the other measures still leave S2 out
    assert any("Failed generations: v1 on gemma4:26b" in n for n in rep["notes"])
    md = rt.render_lab_report_md(rep)
    assert "failed share %" in md and "| failed_share |" in md


def _given_run(tmp_path, v0_system="s", v1_system="s"):
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        r0 = _rec("v0_old", model, "S1", 1, ["uses IDENT here", "nothing"])
        r0["prompt_system"] = v0_system
        r1 = _rec("v1", model, "S1", 1, ["uses IDENT here", "also IDENT"])
        r1["prompt_system"] = v1_system
        recs += [r0, r1]
    run_dir = tmp_path / "gv"
    _write(run_dir, recs)
    return run_dir


def test_given_difference_is_reported_and_the_reference_is_v0_old(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    # v0_old gives IDENT in its examples; v1 does not. The name measure must use the v0_old set for both.
    monkeypatch.setattr(lab_measures, "baseline_prompt_text", lambda sid, model, builder=None: "s with IDENT\nu")
    run_dir = _given_run(tmp_path, v0_system="s with IDENT", v1_system="s")
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    cells = {(c["version"], c["model"]): c for c in rep["cells"]}
    for key in cells:
        assert cells[key]["name_use"] == 0.0  # IDENT is given by v0_old: never name use
    gd = rep["given_differences"]
    assert {(d["version"], d["story"], d["model"], d["identifier"]) for d in gd} == {
        ("v1", "S1", m, IDENT) for m in ("gemma4:26b", "qwen3.8:27b")}
    assert all(d["direction"] == "v0_old gives it, this version does not" for d in gd)
    assert rt.GIVEN_NOTE in rep["notes"]
    md = rt.render_lab_report_md(rep)
    assert "Identifiers that the prompt of a version gives differently from v0_old" in md and rt.GIVEN_NOTE in md


def test_given_difference_reverse_direction(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    # v1 gives IDENT in its own prompt, v0_old does not: the reference stays v0_old, so IDENT counts as name use.
    run_dir = _given_run(tmp_path, v0_system="s", v1_system="s with IDENT")
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    cells = {(c["version"], c["model"]): c for c in rep["cells"]}
    assert cells[("v1", "gemma4:26b")]["name_use"] == 1.0
    assert {d["direction"] for d in rep["given_differences"]} == {"this version gives it, v0_old does not"}


def test_no_given_difference_gives_empty_list(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    rep = rt.build_lab_report(_fixture(tmp_path), fact_sheets=FACTS, builder=object())
    assert rep["given_differences"] == [] and rt.GIVEN_NOTE not in rep["notes"]


def test_v0_old_records_with_a_wrong_prompt_still_raise(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    monkeypatch.setattr(lab_measures, "baseline_prompt_text", lambda sid, model, builder=None: "s\nu with IDENT")
    with pytest.raises(GuardError, match="'given' identifiers.*v0_old.*IDENT"):
        rt.build_lab_report(_fixture(tmp_path), fact_sheets=FACTS, builder=object())


def test_report_splits_outside_by_the_code_hits(tmp_path, monkeypatch):
    def build_reference(story_id, model, *, builder=None):
        return lab_measures.Reference(story_id, model, "uses alpha_key only", "0" * 64, frozenset({"alpha_key"}),
                                      code_text="hit_key lives here")

    _fixed_reference(monkeypatch)
    monkeypatch.setattr(lab_measures, "build_reference", build_reference)
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        recs.append(_rec("v0_old", model, "S1", 1, ["use `alpha_key`", "no name"]))
        recs.append(_rec("v1", model, "S1", 1, ["use `hit_key`", "use `nowhere_key`"]))
    run_dir = tmp_path / "oc"
    _write(run_dir, recs)
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    c = {(x["version"], x["model"]): x for x in rep["cells"]}[("v1", "gemma4:26b")]
    assert (c["outside_share"], c["outside_code_share"], c["outside_none_share"]) == (1.0, 0.5, 0.5)
    assert any("code-search hits" in n for n in rep["notes"])


def test_report_missing_story_and_no_baseline(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    run_dir = _fixture(tmp_path)
    # remove every v1 record of S3 for one model: S3 leaves the comparison
    cell = run_dir / "bitwarden" / model_slug("qwen3.8:27b") / "records.jsonl"
    lines = [json.loads(x) for x in cell.read_text(encoding="utf-8").splitlines()]
    cell.write_text("\n".join(json.dumps(r) for r in lines if not (r["config_id"] == "v1" and r["story_id"] == "S3")) + "\n",
                    encoding="utf-8")
    rep = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object())
    nu = {c["metric"]: c for c in rep["comparisons"]}["name_use"]
    assert nu["stories_missing"] == ["S3"] and nu["n_stories"] == 2 and set(nu["per_story"]) == {"S1", "S2"}
    assert any("Incomplete cell" in n and "v1" in n for n in rep["notes"])
    none = rt.build_lab_report(run_dir, fact_sheets=FACTS, builder=object(), baseline="v7")
    assert none["baseline"] is None and none["comparisons"] == []


def test_report_with_a_cell_without_a_good_record_does_not_crash(tmp_path, monkeypatch):
    _fixed_reference(monkeypatch)
    recs = []
    for story in ("S1", "S2"):
        recs.append(_rec("v0_old", "gemma4:26b", story, 1, ["uses IDENT"]))
        recs.append(_rec("v1", "gemma4:26b", story, 1, [], error="UnreadableAnswer: bad"))
        recs.append(_rec("v0_old", "qwen3.8:27b", story, 1, ["uses IDENT"]))
        recs.append(_rec("v1", "qwen3.8:27b", story, 1, ["uses IDENT"]))
    run_dir = tmp_path / "nog"
    _write(run_dir, recs)
    rep = rt.build_lab_report(run_dir, fact_sheets={"S1": [IDENT], "S2": [IDENT]}, builder=object())
    bad = {(c["version"], c["model"]): c for c in rep["cells"]}[("v1", "gemma4:26b")]
    assert bad["name_use"] is None and bad["requirements_per_answer"] is None
    assert bad["n_final_ok"] == 0 and bad["n_final_failed"] == 0 and bad["n_not_final"] == 2
    assert bad["parse_failures"]["attempt_lines_failed"] == 2 and bad["parse_failures"]["final_failed_keys"] == 2
    nu = {c["metric"]: c for c in rep["comparisons"]}["name_use"]
    assert nu["n_stories"] == 0 and nu["mean"] is None and sorted(nu["stories_missing"]) == ["S1", "S2"]
    md = rt.render_lab_report_md(rep)
    assert "n/a" in md


def test_anchor_report_with_a_cell_without_a_good_record_does_not_crash(tmp_path):
    run_dir = tmp_path / "anch"
    recs = []
    for model in ("gemma4:26b", "qwen3.8:27b"):
        for arm in ("A2", "B2"):
            for story in ("S1", "S2"):
                bad = model == "gemma4:26b" and arm == "B2"
                r = _rec("old", model, story, 1, ["uses IDENT"], error="UnreadableAnswer: bad" if bad else None)
                r["arm"], r["step"] = arm, "anchor"
                recs.append(r)
    _write(run_dir, recs)
    ref = {(m, a): 0.5 for m in ("gemma4:26b", "qwen3.8:27b") for a in ("A2", "B2")}
    rep = rt.build_report(run_dir, reference=ref, fact_sheets={"S1": [IDENT], "S2": [IDENT]})
    row = {(r["model"], r["arm"]): r for r in rep["rows"]}[("gemma4:26b", "B2")]
    assert row["hosted_rate"] is None and row["n_failed"] == 2 and row["inside_limit"] is None
    assert "n/a" in rt.render_report_md(rep)


def test_report_md_has_tables_notes_and_no_private_text(tmp_path, monkeypatch):
    from tuning_lib.guard import assert_no_private_text

    _fixed_reference(monkeypatch)
    rep = rt.build_lab_report(_fixture(tmp_path), fact_sheets=FACTS, builder=object())
    md = rt.render_lab_report_md(rep)
    for text in ("## Cells", "## Requirements with an attributable name", "## v1 against v0_old",
                 "Difference by story, name_use", "Difference by story, outside_share",
                 "Script measures only", "One fixed reference for every version",
                 "The standard error is over stories, not over requirements"):
        assert text in md
    assert "better than" not in md and "is better" not in md.replace("a version is better.", "")
    assert_no_private_text(md, what="report.md")
    assert_no_private_text(json.dumps(rep), what="report.json")


def test_cmd_report_takes_the_lab_path_and_refuses_mixed_steps(tmp_path, guard, monkeypatch):
    _fixed_reference(monkeypatch)
    run_dir = _fixture(tmp_path)
    assert rt.cmd_report("rep", guard=guard, runs_root=tmp_path, fact_sheets=FACTS, builder=object()) == 0
    assert json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["baseline"] == "v0_old"
    # a record of another step in the same folder is an error
    cell = run_dir / "bitwarden" / model_slug("gemma4:26b") / "records.jsonl"
    extra = _rec("old", "gemma4:26b", "S1", 1, ["x"])
    extra["step"] = "anchor"
    with open(cell, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(extra) + "\n")
    with pytest.raises(GuardError, match="mixes steps"):
        rt.cmd_report("rep", guard=guard, runs_root=tmp_path, fact_sheets=FACTS, builder=object())


def test_old_stub_option_still_raises_for_the_old_test():
    with pytest.raises(NotImplementedError, match="^run:"):
        rt.main(["run", "--run-name", "x1", "--config", "c"])


def test_source_scan_of_the_lab_code():
    import re

    src = (ROOT / "scripts" / "run_tuning.py").read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"#.*", "", line) for line in src.splitlines())
    for bad in ("write_text(", "mkdir(", "os.makedirs", "shutil", "os.replace", "os.rename", "write_bytes("):
        assert bad not in code, bad
    assert not re.search(r"open\([^)]*['\"][wax]", code)
