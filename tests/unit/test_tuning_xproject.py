"""Tests of the outside-product test: tuning_lib/xproject.py, scripts/xproject_retrieve.py and
scripts/xproject_run.py. No network, no key, no model. Everything is written below tmp_path."""

from __future__ import annotations

import dataclasses
import hashlib
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
import xproject_retrieve as xr  # noqa: E402
import xproject_run as xrun  # noqa: E402
from tuning_lib import judging as J  # noqa: E402
from tuning_lib import passages as P  # noqa: E402
from tuning_lib import xproject as X  # noqa: E402
from tuning_lib.guard import GuardError, HashListResult, WriteGuard  # noqa: E402
from tuning_lib.openrouter_client import HOSTED_MODELS, HostedReply  # noqa: E402
from tuning_lib.passages import SEPARATOR  # noqa: E402
from tuning_lib.paths import FALLBACK_CONTEXT, model_slug  # noqa: E402
from tuning_lib.prompts import VERSIONS_ROOT, PromptBuilder, load_stories, new_version  # noqa: E402
from tuning_lib.records import read_jsonl, validate_record  # noqa: E402

MODEL_A, MODEL_B = "qwen3.8:27b", "gemma4:26b"
IDS = ("XP-001", "XP-002")
BLOCK = (
    "### [1] guide.md (Relevance: 0.81)\nSessions end after thirty minutes of silence."
    + SEPARATOR
    + "### [2] setup.md (Relevance: 0.64)\nAdmins set the limit in the console."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(text: str) -> dict:
    return {"query_text": "q", "n_chunks": 2, "context": text, "context_sha256": _sha(text)}


def _answer() -> str:
    req = {
        "id": "SR-001", "user_story_id": "XP-001", "title": "T", "description": "d", "rationale": "r",
        "acceptance_criteria": ["a"], "asvs_category": "V2: Authentication", "priority": "high",
        "implementation_notes": None, "source_references": ["x"],
    }
    return json.dumps({"requirements": [req, dict(req, id="SR-002")]})


def _reply(text: str) -> HostedReply:
    return HostedReply(
        text=text, generation_id="g", response_model="x/y", provider="P", provider_source="metadata",
        finish_reason="stop", prompt_tokens=100, completion_tokens=50, reasoning_tokens=0,
        reasoning_tokens_source="usage", cached_tokens=0, cost_usd=0.01, latency_ms=5.0, transport_retries=0)


class FakeClient:
    """Answers with a fixed valid answer. It keeps every request."""

    requests: list[tuple[str | None, str]] = []

    def generate_reply(self, prompt, system_prompt=None):
        FakeClient.requests.append((system_prompt, prompt))
        return _reply(_answer())

    def close(self) -> None:
        pass


@pytest.fixture()
def synth(tmp_path, monkeypatch):
    """A synthetic product 'synth': stories, system context, folders, frozen files, versions."""
    rtc = tmp_path / "rtc"
    pdir = rtc / "synth"
    (pdir / "docs").mkdir(parents=True)
    (rtc / "repos" / "synth").mkdir(parents=True)
    stories = [
        {"id": sid, "title": f"Title number {n}", "story": f"As a user number {n} I want the portal to keep me safe today.",
         "acceptance_criteria": [f"Criterion alpha {n}", f"Criterion beta {n}"]}
        for n, sid in enumerate(IDS, start=1)
    ]
    (pdir / "user_stories.json").write_text(json.dumps({"project": "synth", "user_stories": stories}), encoding="utf-8")
    (pdir / "system_context.yaml").write_text("project:\n  name: Synth\n", encoding="utf-8")
    xroot = tmp_path / "tuning" / "_xproject"
    for model in (MODEL_A, MODEL_B):
        cell = xroot / "synth" / model_slug(model)
        cell.mkdir(parents=True)
        (cell / "retrieval_frozen.json").write_text(json.dumps({sid: _entry(BLOCK) for sid in IDS}), encoding="utf-8")
    pv = tmp_path / "pv"
    pv.mkdir()
    shutil.copytree(VERSIONS_ROOT / "v0_old", pv / "v0_old")
    folder = new_version("v1", "v0_old", "Adds one general sentence about checkable requirements.", root=pv,
                         guard=WriteGuard(allowed_roots=[pv], forbidden_roots=[], forbidden_names=[]))
    system = folder / "system_prompt.txt"
    system.write_text(system.read_text(encoding="utf-8") + "\nWrite each requirement so that a tester can check it.\n",
                      encoding="utf-8")
    product = X.XProduct("synth", pdir / "docs", rtc / "repos" / "synth")
    monkeypatch.setitem(X.ALLOWED_PRODUCTS, "synth", product)
    builder = X.XProjectBuilder(("synth",), versions_root=pv, xroot=xroot, rtc_root=rtc)
    return {"rtc": rtc, "xroot": xroot, "pv": pv, "builder": builder, "product": product, "tmp": tmp_path}


@pytest.fixture()
def guard(tmp_path):
    return WriteGuard(allowed_roots=[tmp_path], forbidden_roots=[], forbidden_names=[])


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


def run_x(synth, guard, *extra, name="X1", factory=None):
    argv = ["--project", "synth", "--run-name", name, *extra]
    return xrun.main(argv, guard=guard, runs_root=synth["tmp"] / "runs", client_factory=factory,
                     sleep=lambda s: None, builder=synth["builder"], xroot=synth["xroot"])


# ---------------------------------------------------------------- stories


def test_story_loader_reads_the_stories(synth):
    stories = X.load_xstories("synth", rtc_root=synth["rtc"])
    assert sorted(stories) == list(IDS)
    s = stories["XP-001"]
    assert (s.project, s.title) == ("synth", "Title number 1")
    assert s.prompt_text().splitlines()[-1] == "- Criterion beta 1"


def test_story_loader_refuses_a_lab_story_id(synth):
    path = synth["rtc"] / "synth" / "user_stories.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["user_stories"][0]["id"] = sorted(X.lab_story_ids())[0]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(X.XProjectError, match="story of the lab"):
        X.load_xstories("synth", rtc_root=synth["rtc"])


def test_stored_block_loses_the_wrapper_line_with_a_local_path_and_keeps_other_text(monkeypatch):
    block = (
        "### [1] a.md > A (Relevance: 0.45)\nSection: A\n"
        '<div class="included"\npath="/Users/someone/extracted/a.adoc">\ntext of a\n</div>\n<div class="x">kept</div>'
    )
    builder = X.XProjectBuilder(("mattermost",))
    monkeypatch.setattr(builder, "story", lambda story_id: type("S", (), {"project": "mattermost"})())
    monkeypatch.setattr(X, "read_xfrozen", lambda *a, **k: type("C", (), {"text": block})())
    out = builder.stored_block("MM-X", "qwen3.8:27b")
    assert "/Users/" not in out and "text of a" in out and '<div class="x">kept</div>' in out
    plain = "### [1] a.md > A (Relevance: 0.45)\nSection: A\ntext with <div>one</div> element"
    monkeypatch.setattr(X, "read_xfrozen", lambda *a, **k: type("C", (), {"text": plain})())
    assert builder.stored_block("MM-Y", "qwen3.8:27b") == plain


def test_unknown_product_is_refused(synth):
    with pytest.raises(X.XProjectError, match="not allowed"):
        X.load_xstories("grafana", rtc_root=synth["rtc"])


def test_the_allow_list_holds_the_two_products_of_the_search_checks():
    assert sorted(X.ALLOWED_PRODUCTS) == ["bitwarden", "gitlab", "keycloak", "mastodon", "mattermost"]
    assert X.ALLOWED_PRODUCTS["gitlab"].docs_dir.name == "docs"
    assert X.ALLOWED_PRODUCTS["gitlab"].docs_dir.parent.name == "gitlab"


def test_mattermost_stories_are_not_lab_stories():
    stories = X.load_xstories("mattermost")
    assert len(stories) == 8
    assert not set(stories) & X.lab_story_ids()


# ---------------------------------------------------------------- frozen block


def test_frozen_reader_accepts_a_good_block(synth):
    ctx = X.read_xfrozen("synth", MODEL_A, "XP-001", synth["xroot"])
    assert ctx.text == BLOCK and ctx.sha256 == _sha(BLOCK) and ctx.n_chunks_stored == 2


def _write_frozen(synth, entry: dict) -> None:
    path = X.frozen_path("synth", MODEL_A, synth["xroot"])
    path.write_text(json.dumps({"XP-001": entry}), encoding="utf-8")


def test_frozen_reader_refuses_a_wrong_hash(synth):
    _write_frozen(synth, dict(_entry(BLOCK), context_sha256="0" * 64))
    with pytest.raises(X.XProjectError, match="hash"):
        X.read_xfrozen("synth", MODEL_A, "XP-001", synth["xroot"])


def test_frozen_reader_refuses_the_fallback_text(synth):
    _write_frozen(synth, _entry(FALLBACK_CONTEXT))
    with pytest.raises(X.XProjectError, match="fallback"):
        X.read_xfrozen("synth", MODEL_A, "XP-001", synth["xroot"])


def test_frozen_reader_refuses_a_block_without_documentation(synth):
    code_only = "### [1] src (Relevance: 0.50)\n### code search hit (pattern: 'abc')\nFile: a.go:3\nline"
    _write_frozen(synth, _entry(code_only))
    with pytest.raises(X.XProjectError, match="no documentation passage"):
        X.read_xfrozen("synth", MODEL_A, "XP-001", synth["xroot"])


def test_frozen_reader_refuses_a_missing_story(synth):
    with pytest.raises(X.XProjectError, match="no frozen block"):
        X.read_xfrozen("synth", MODEL_A, "XP-099", synth["xroot"])


# ---------------------------------------------------------------- builder


def test_builder_gives_the_same_prompt_as_promptbuilder(synth, monkeypatch):
    """Same story, block, system context and version: PromptBuilder and XProjectBuilder agree."""
    tmp = synth["tmp"]
    # Lay the same data out in the Study 2 and Study 2b shapes for PromptBuilder.
    study2 = tmp / "study2"
    (study2 / "synth").mkdir(parents=True)
    raw = json.loads((synth["rtc"] / "synth" / "user_stories.json").read_text(encoding="utf-8"))["user_stories"]
    for item in raw:
        item["fact_sheet"] = {"identifiers": ["zzqq"]}
    (study2 / "synth" / "stories.json").write_text(json.dumps({"stories": raw}), encoding="utf-8")
    monkeypatch.setattr(P, "STUDY2B_ROOT", synth["xroot"])  # same folder shape: <root>/<project>/<slug>/
    old = PromptBuilder(versions_root=synth["pv"], study2_root=study2, rtc_root=synth["rtc"])
    old._stories = load_stories(projects=("synth",), study2_root=study2)
    for version in ("v0_old", "v1"):
        a = old.build("XP-001", MODEL_B, version)
        b = synth["builder"].build("XP-001", MODEL_B, version)
        assert (a.system, a.user, a.user_non_doc, a.block_sha256) == (b.system, b.user, b.user_non_doc, b.block_sha256)
        assert (a.n_doc_passages, a.n_code_hits) == (b.n_doc_passages, b.n_code_hits) == (2, 0)
    assert BLOCK in b.user and b.user.count(BLOCK) == 1


def test_builder_cannot_verify_v0_old(synth):
    with pytest.raises(X.XProjectError, match="no stored prompt"):
        synth["builder"].verify_v0_old("XP-001", MODEL_A)


# ---------------------------------------------------------------- vector store folder guard


def test_persist_dir_guard(tmp_path):
    tuning, data, outside = tmp_path / "tuning", tmp_path / "tuning" / "data_like", tmp_path / "outside"
    for d in (tuning, data, outside):
        d.mkdir(parents=True, exist_ok=True)
    ok = X.check_persist_dir(tuning / "_xproject" / "p" / "vectordb", tuning_root=tuning, data_root=data)
    assert ok.is_relative_to(tuning.resolve())
    with pytest.raises(GuardError):  # inside the data folder
        X.check_persist_dir(data / "vectordb", tuning_root=tuning, data_root=data)
    with pytest.raises(GuardError):  # outside both
        X.check_persist_dir(outside / "vectordb", tuning_root=tuning, data_root=data)
    with pytest.raises(GuardError):  # not absolute
        X.check_persist_dir(Path("vectordb"), tuning_root=tuning, data_root=data)


def test_persist_dir_guard_refuses_a_link_that_leaves_tuning(tmp_path):
    tuning, data, outside = tmp_path / "tuning", tmp_path / "data", tmp_path / "outside"
    for d in (tuning, data, outside):
        d.mkdir()
    (tuning / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(GuardError):
        X.check_persist_dir(tuning / "link" / "vectordb", tuning_root=tuning, data_root=data)


def test_persist_dir_of_mattermost_is_allowed_and_not_in_data():
    path = X.check_persist_dir(X.persist_dir_of("mattermost"))
    assert "secreqgen/data" not in path.as_posix()


# ---------------------------------------------------------------- retrieve script rules


def test_hash_seed_rule():
    assert xr.hashseed_ok({"PYTHONHASHSEED": "0"}, 0)
    assert not xr.hashseed_ok({"PYTHONHASHSEED": "1"}, 0)
    assert not xr.hashseed_ok({}, 0)
    assert not xr.hashseed_ok({"PYTHONHASHSEED": "0"}, 1)  # the process was not started with the seed


def test_real_run_checks_fail_without_the_hash_seed(monkeypatch):
    ok = {n: HashListResult(n, f"{n}.sha256", True, 1, 1, 0, (), (), "ok") for n in ("study2", "study2b", "vectordb")}
    monkeypatch.setattr(rt, "check_all_hash_lists", lambda: ok)
    monkeypatch.setattr(rt, "list_processes", lambda: [])
    monkeypatch.setattr(xr, "hashseed_ok", lambda *a, **k: False)
    real = xr.run_checks("mattermost", [MODEL_B], dry_run=False)
    dry = xr.run_checks("mattermost", [MODEL_B], dry_run=True)
    status = lambda checks: next(c.status for c in checks if c.name == "PYTHONHASHSEED")  # noqa: E731
    assert status(real) == "FAIL" and status(dry) == "WARN"


def test_existing_frozen_file_is_found_and_never_overwritten(synth, tmp_path, monkeypatch):
    found = xr.existing_frozen("synth", [MODEL_A, MODEL_B], synth["xroot"])
    assert len(found) == 2
    target = X.frozen_path("synth", MODEL_A, synth["xroot"])
    with pytest.raises(X.XProjectError, match="never overwritten"):
        xr._write_new(target, "{}")
    assert json.loads(target.read_text(encoding="utf-8"))["XP-001"]["context"] == BLOCK


def test_models_list_needs_an_agent_model():
    assert xr.models_list("qwen3.8:27b,gemma4:26b") == [MODEL_A, MODEL_B]
    with pytest.raises(X.XProjectError):
        xr.models_list("llama3:8b")
    assert xr.CROSS_FAMILY == {MODEL_A: MODEL_B, MODEL_B: MODEL_A}


def test_count_passages():
    assert xr.count_passages(BLOCK) == (2, 0, False)
    assert xr.count_passages(FALLBACK_CONTEXT)[2] is True


# ---------------------------------------------------------------- evidence files


def test_evidence_files(synth, guard):
    out = synth["tmp"] / "evidence_x"
    paths = X.write_evidence_files("synth", out_dir=out, rtc_root=synth["rtc"], guard=guard)
    assert [p.name for p in paths] == ["XP-001.md", "XP-002.md"]
    text = (out / "XP-001.md").read_text(encoding="utf-8")
    assert text.startswith("# Evidence for story XP-001\n")
    assert str(synth["product"].docs_dir) in text and str(synth["product"].repo_dir) in text
    for part in ("## Story block", "## System description", "1. Criterion alpha 1", "2. Criterion beta 1", "name: Synth"):
        assert part in text
    assert X.EVIDENCE_NOTE in text


def test_evidence_files_go_through_the_guard(synth):
    closed = WriteGuard(allowed_roots=[synth["tmp"] / "elsewhere"], forbidden_roots=[], forbidden_names=[])
    with pytest.raises(GuardError):
        X.write_evidence_files("synth", out_dir=synth["tmp"] / "evidence_x", rtc_root=synth["rtc"], guard=closed)


# ---------------------------------------------------------------- the run script


def test_dry_run_makes_no_write_no_key_no_network(synth, guard, safe_env, capsys):
    code = run_x(synth, guard, "--versions", "v0_old,v1", "--repeats", "2", "--dry-run")
    out = capsys.readouterr().out
    assert code == 0
    assert "key: not read, no network call made" in out
    for line in ("run folder", "hash lists (before)", "other processes and lock", "prompt versions",
                 "frozen passages", "request bodies", "prompt size", "cost estimate"):
        assert f"[PASS] {line}" in out
    assert not (synth["tmp"] / "runs").exists()
    assert "v0_old rebuild" not in out


def test_dry_run_fails_when_a_frozen_file_is_missing(synth, guard, safe_env, capsys):
    X.frozen_path("synth", MODEL_B, synth["xroot"]).unlink()
    assert run_x(synth, guard, "--versions", "v0_old", "--dry-run") == 2
    assert "[FAIL] plan" in capsys.readouterr().out


def test_run_refuses_an_existing_run_folder(synth, guard, safe_env, capsys):
    (synth["tmp"] / "runs" / "X1").mkdir(parents=True)
    assert run_x(synth, guard, "--versions", "v0_old", "--dry-run") == 2
    assert "[FAIL] run folder" in capsys.readouterr().out


def test_fake_run_writes_lab_records_and_judging_works(synth, guard, safe_env, capsys):
    FakeClient.requests = []
    code = run_x(synth, guard, "--versions", "v0_old,v1", "--repeats", "2", "--workers", "2",
                 factory=lambda pin: FakeClient())
    assert code == 0, capsys.readouterr().out
    run_dir = synth["tmp"] / "runs" / "X1"
    assert len(FakeClient.requests) == 2 * 2 * 2 * 2  # stories x models x versions x repeats
    from tuning_lib.records import REQUIRED_RECORD_FIELDS

    for model in (MODEL_A, MODEL_B):
        recs = read_jsonl(run_dir / "synth" / model_slug(model) / "records.jsonl")
        assert len(recs) == 2 * 2 * 2
        for rec in recs:
            validate_record(rec)
            assert set(rec) == set(REQUIRED_RECORD_FIELDS)
            assert (rec["step"], rec["arm"], rec["project"], rec["error"]) == ("lab", "B2", "synth", None)
            assert rec["config_id"] in ("v0_old", "v1") and rec["n_requirements"] == 2
            assert rec["source_study"] == "xproject" and BLOCK in rec["prompt_user_sent"]
            assert "/Users/" not in json.dumps(rec)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "complete" and manifest["step"] == "lab"
    assert set(manifest["configuration"]["prompt_versions"]) == {"v0_old", "v1"}
    assert manifest["jobs"]["planned"] == 16 and manifest["hash_lists"]["after"]
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "| v0_old | qwen3.8:27b | 4 of 4 |" in report and "name measure" not in report.lower().replace("no name measures", "")
    design = J.build_batches(run_dir, versions=["v0_old", "v1"], stories=list(IDS), per_cell=2, seed=3,
                             round_name="r1", guard=guard)
    assert design.n_items == 2 * 2 * 2 * 2  # stories x versions x models x per_cell
    assert rt.run_job.__module__ == "run_tuning" and rt.run_job.__name__ == "run_job"  # patch removed


def test_run_job_is_restored_after_an_error(synth, guard, safe_env):
    original = rt.run_job
    with pytest.raises(RuntimeError):
        with xrun.outside_product_records():
            raise RuntimeError("x")
    assert rt.run_job is original


def test_plan_orders_versions_and_counts(synth):
    jobs = xrun.plan_x_jobs("synth", ["v0_old", "v1"], (MODEL_A, MODEL_B), 3, synth["builder"])
    assert len(jobs) == 2 * 2 * 2 * 3
    assert len({j.key for j in jobs}) == len(jobs)
    assert all(j.arm == "B2" and j.step == "lab" and j.n_doc_passages == 2 for j in jobs)


def test_an_extra_model_uses_the_stored_block_of_the_source_model():
    assert X.block_model("mistral-small3.2:24b") == "qwen3.8:27b"
    assert X.block_model("nemotron3-nano:30b") == "qwen3.8:27b"
    assert X.block_model("gemma4:26b") == "gemma4:26b"
