"""Study 2b: the prompt folders, the driver and the analyzer.

Fast tests. No model is called and there is no network: a fake Ollama answers
through httpx.MockTransport. The tests write only into temporary folders.

Blinding: the tests print nothing when they pass. A failure message names an
arm, a story or a key, never a result value of Study 2.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve()
SECREQGEN = HERE.parents[2]
SCRIPTS = SECREQGEN / "scripts"
STUDY2 = SECREQGEN.parent / "real_test_cases" / "study2"
STUDY2B = SECREQGEN.parent / "real_test_cases" / "study2b"
FIRST_STORY = {"mastodon": "MT-ADV-001", "keycloak": "KC-S2-001", "bitwarden": "BW-S2-001"}

needs_data = pytest.mark.skipif(not (STUDY2 / "mastodon" / "stories.json").is_file(),
                                reason="the registered Study 2 data are not on this machine")


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module in sys.modules
    assert spec.loader is not None
    spec.loader.exec_module(module)  # a script's main() runs only as __main__
    return module


@pytest.fixture(scope="module")
def chk():
    return _load("check_s2b_prompts")


@pytest.fixture(scope="module")
def r2b():
    return _load("run_study2b")


@pytest.fixture(scope="module")
def a2b():
    return _load("analyze_study2b")


@pytest.fixture(scope="module")
def st(a2b):
    return a2b.load_stats()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def listing(root: Path) -> list[tuple]:
    """Each folder and file under root with size and time. The sealed folders are not entered."""
    out: list[tuple] = []
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d != "key_do_not_open" and not (Path(dirpath) == STUDY2 and d == "review")]
        out.append((dirpath, "<dir>"))
        for f in filenames:
            s = os.stat(os.path.join(dirpath, f))
            out.append((dirpath, f, s.st_size, s.st_mtime_ns))
    return sorted(out)


# ---------------------------------------------------------------------------
# A fake Ollama
# ---------------------------------------------------------------------------
class FakeOllama:
    """Answers /api/generate with two parsed requirements.

    It can fail the next calls with HTTP 500 (fail_next), or answer the next calls with text that is
    not JSON (bad_json_next). One failed generation with text that is not JSON takes 3 calls, because
    the generator tries to parse 3 times.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail_next = 0
        self.bad_json_next = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.8:27b"}, {"name": "gemma4:26b"}]})
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fail_next > 0:
            self.fail_next -= 1
            return httpx.Response(500, text="invented failure")
        if self.bad_json_next > 0:
            self.bad_json_next -= 1
            return httpx.Response(200, json={"response": "invented text that is not JSON",
                                             "prompt_eval_count": 100, "eval_count": 5, "model": body["model"]})
        req = {"id": "SR-001", "user_story_id": "X", "title": "An invented requirement", "description": "text",
               "rationale": "text", "acceptance_criteria": ["a"], "asvs_category": "V2: Authentication",
               "priority": "high", "implementation_notes": None, "source_references": []}
        return httpx.Response(200, json={"response": json.dumps({"requirements": [req, dict(req, id="SR-002")]}),
                                         "prompt_eval_count": 100, "eval_count": 20, "model": body["model"]})


def install_fake(monkeypatch, r2b, fake: FakeOllama) -> None:
    real = r2b.make_client

    def make(model, options):
        client = real(model, options)  # the real client and options; only the transport is fake
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        return client

    monkeypatch.setattr(r2b, "make_client", make)


def offline(monkeypatch, r2b) -> None:
    """Network and process list replaced by the registered state; git not called."""
    manifests = r2b.registered_manifests()
    version = manifests[0]["ollama"]["version"]
    digests = dict(manifests[0]["ollama"]["digests"])
    monkeypatch.setattr(r2b, "ollama_state", lambda base_url: (version, digests))
    monkeypatch.setattr(r2b, "list_processes", lambda: [])
    monkeypatch.setattr(r2b, "git_porcelain", lambda path: "")


def make_state(r2b, sel, out_root: Path) -> dict:
    manifests = r2b.registered_manifests()
    return {"cells": r2b.load_cells(sel, out_root), "builders": r2b.make_builders(),
            "options": r2b.generation_options(r2b.SecReqGenConfig(), manifests),
            "ollama": {"version": "test", "digests": {}, "drift": []}, "manifests": manifests}


def selection(r2b, arms=None, models=None, projects=None, stories=None, max_runs=None):
    arms = arms or list(r2b.ARM_BY_NAME)
    return r2b.Selection(arms=[a for a in r2b.ARMS if a.name in arms], models=list(models or r2b.MODELS),
                         projects=list(projects or r2b.PROJECTS), stories=stories, max_runs=max_runs)


# ---------------------------------------------------------------------------
# Prompt folders
# ---------------------------------------------------------------------------
def test_prompt_check_passes(chk):
    results = chk.run_checks()
    bad = [name for ok, name, _ in results if not ok]
    assert not bad, f"prompt checks fail: {bad}"
    assert len(results) >= 40
    for f in chk.FILES:
        assert chk.TARGET_SENTENCE not in (chk.S2B / "n0" / f).read_text()


def test_prompt_check_finds_a_changed_file(chk, tmp_path):
    copy = tmp_path / "s2b"
    shutil.copytree(chk.S2B, copy)
    p = copy / "n1" / "rag_user_prompt.txt"
    p.write_text(p.read_text().replace("## Project Documentation", "## Project Documentation (use it)"))
    bad = [name for ok, name, _ in chk.run_checks(s2b=copy) if not ok]
    assert any("plus the one block" in n for n in bad)
    assert any("SHA256SUMS" in n for n in bad)


def test_residue_lists_the_cite_rule_in_n1_but_not_in_n0(chk):
    rows = chk.residue()
    n1 = [line for f, _, _, line in rows if f == "n1/system_prompt.txt"]
    n0 = [line for f, _, _, line in rows if f == "n0/system_prompt.txt"]
    assert any(chk.TARGET_SENTENCE in line for line in n1)
    assert not any(chk.TARGET_SENTENCE in line for line in n0)
    assert not any(line.strip().startswith('"user_story_id"') for _, _, _, line in rows)


# ---------------------------------------------------------------------------
# The prompts that are sent (one story of each project, both models)
# ---------------------------------------------------------------------------
def _registered_record_prompts(project: str, model_slug: str, story_id: str, arm: str) -> dict:
    path = STUDY2 / project / "results" / model_slug / "records.jsonl"
    with path.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec["story_id"] == story_id and rec["arm"] == arm:
                return {"system": rec["prompt_system"], "non_doc": rec["prompt_user_non_doc"]}
    raise AssertionError(f"no registered record for {project}/{model_slug}/{story_id}/{arm}")


@needs_data
def test_sent_prompts_equal_the_design(r2b, chk, tmp_path, monkeypatch, capsys):
    from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder

    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)
    out = tmp_path / "out"
    sel = selection(r2b, stories=set(FIRST_STORY.values()), max_runs=1)
    state = make_state(r2b, sel, out)
    code = r2b.execute(sel, out, state, hard_stop_hours=1.0, allow_drift=False)
    capsys.readouterr()
    assert code == 0
    assert len(fake.requests) == 5 * 2 * 3  # arms x models x stories, one run each

    manifests = r2b.registered_manifests()
    want_options = {"temperature": manifests[0]["config"]["temperature"],
                    "num_predict": manifests[0]["config"]["max_tokens"], "num_ctx": manifests[0]["config"]["num_ctx"]}
    for body in fake.requests:
        assert body["options"] == want_options
        assert body["think"] is False and body["stream"] is False and "format" not in body

    reg_pb = SecurityRequirementsPromptBuilder(prompts_dir=r2b.PROMPTS_DIR)
    n0_system = (r2b.PROMPTS_S2B / "n0" / "system_prompt.txt").read_text()
    sent_pairs = {(b["prompt"], b["system"]) for b in fake.requests}
    n_checked = 0
    for project, sid in FIRST_STORY.items():
        _, stories = r2b.load_stories(project, {sid})
        story = stories[0]
        system_context = (r2b.REAL_TEST_CASES / project / "system_context.yaml").read_text()
        reg_a = reg_pb.build_zero_shot_prompt(user_story=story, system_context=system_context)
        reg_system = reg_pb.build_system_prompt()
        for model in r2b.MODELS:
            ms = r2b._slug(model)
            ctx = json.loads(r2b.frozen_path(project, model).read_text())[sid]["context"]
            recs = {r["arm"]: r for r in r2b.read_records(out / project / ms / "records.jsonl") if r["story_id"] == sid}
            assert set(recs) == set(r2b.ARM_BY_NAME)
            for arm, rec in recs.items():
                assert (rec["prompt_user_sent"], rec["prompt_system"]) in sent_pairs, f"{arm} {sid}: not what was sent"
                assert rec["prompt_user_sha256"] == sha(rec["prompt_user_sent"])
                assert rec["prompt_system_sha256"] == sha(rec["prompt_system"])
                assert rec["error"] is None and rec["n_requirements"] == 2
            # N1: the registered arm A prompt plus the block with the exact stored context.
            i = reg_a.index(chk.ANCHOR)
            want_n1 = reg_a[:i] + chk.BLOCK.replace("$retrieved_context", ctx) + reg_a[i:]
            assert recs["N1"]["prompt_user_sent"] == want_n1, f"N1 {sid} {model}"
            assert recs["N1"]["prompt_system"] == reg_system
            assert recs["N1"]["prompt_user_sent"].count(ctx) == 1
            # I0: the registered arm B prompt with the context replaced by the fixed line.
            reg_b = reg_pb.build_rag_prompt(user_story=story, retrieved_context=ctx, system_context=system_context)
            assert recs["I0"]["prompt_user_sent"] == reg_b.replace(ctx, r2b.FIXED_LINE)
            assert recs["I0"]["prompt_user_sent"] == reg_pb.build_rag_prompt(
                user_story=story, retrieved_context=r2b.FIXED_LINE, system_context=system_context)
            assert recs["I0"]["prompt_system"] == reg_system
            assert ctx not in recs["I0"]["prompt_user_sent"]
            # B2 and A2: what the registered driver builds for B and A.
            assert recs["B2"]["prompt_user_sent"] == reg_b and recs["B2"]["prompt_system"] == reg_system
            assert recs["A2"]["prompt_user_sent"] == reg_a and recs["A2"]["prompt_system"] == reg_system
            # ... and what the registered records hold.
            stored_a = _registered_record_prompts(project, ms, sid, "A")
            stored_b = _registered_record_prompts(project, ms, sid, "B")
            assert recs["A2"]["prompt_user_sent"] == stored_a["non_doc"] and recs["A2"]["prompt_system"] == stored_a["system"]
            assert recs["B2"]["prompt_user_non_doc"] == stored_b["non_doc"]
            assert recs["B2"]["prompt_system"] == stored_b["system"]
            # N0: none of the removed sentences, the stored context once.
            n0 = recs["N0"]
            assert n0["prompt_system"] == n0_system
            for text in r2b.removed_texts():
                assert text not in n0["prompt_system"] and text not in n0["prompt_user_non_doc"], f"N0 {sid}: {text}"
            assert n0["prompt_user_sent"].count(ctx) == 1
            # The non-documentation prompt is the sent prompt minus the exact context.
            for arm in ("N1", "B2", "N0"):
                assert recs[arm]["prompt_user_non_doc"] == recs[arm]["prompt_user_sent"].replace(ctx, "", 1)
            assert recs["A2"]["prompt_user_non_doc"] == recs["A2"]["prompt_user_sent"]
            assert recs["A2"]["doc_context_ref"] is None and recs["I0"]["doc_context_ref"] == "fixed_line"
            n_checked += 1
    assert n_checked == 6
    # The copied stored passages are byte copies.
    for project in FIRST_STORY:
        for model in r2b.MODELS:
            copy = out / project / r2b._slug(model) / "retrieval_frozen.json"
            assert copy.read_bytes() == r2b.frozen_path(project, model).read_bytes()
            m = json.loads((out / project / r2b._slug(model) / "manifest_s2b.json").read_text())
            assert m["generation_options"]["num_ctx"] == want_options["num_ctx"]
            assert set(m["prompt_sha256"]) == {f"{d}/{f}" for d in chk.FOLDERS for f in chk.FILES}
            assert m["finished_at_utc"] and m["stop_reason"] == "the plan is done"
    assert not (out / r2b.LOCK_NAME).exists()


@needs_data
def test_preflight_prompt_build_check_passes_for_all_prompts(r2b):
    sel = selection(r2b)
    cells = r2b.load_cells(sel, Path("/nonexistent-out"))
    c = r2b.check_prompt_build(cells, sel.arms, r2b.make_builders())
    assert c.status == "PASS", c.detail
    assert len(r2b.build_plan(sel, cells)) == 330


@needs_data
def test_preflight_finds_a_changed_system_context(r2b):
    """system_context.yaml is not on the hash list. A changed one must fail the A2 and B2 comparison with the
    prompts in the registered records (and so stop the run)."""
    sel = selection(r2b, arms=["B2", "A2"], projects=["bitwarden"])
    cells = r2b.load_cells(sel, Path("/nonexistent-out"))
    for cell in cells:
        cell.system_context = (cell.system_context or "") + "\nextra: invented line\n"
    c = r2b.check_prompt_build(cells, sel.arms, r2b.make_builders())
    assert c.status == "FAIL" and "registered records" in c.detail


# ---------------------------------------------------------------------------
# Arms, options, fixed line
# ---------------------------------------------------------------------------
def test_arms_order_runs_and_total(r2b, a2b):
    assert [a.name for a in r2b.ARMS] == ["N1", "B2", "A2", "I0", "N0"]
    assert {a.name: a.runs for a in r2b.ARMS} == a2b.NEW_ARMS
    assert list(a2b.NEW_ARMS) == [a.name for a in r2b.ARMS]
    assert {a.name: r2b.DOC_CONTEXT_REF[a.context] for a in r2b.ARMS} == a2b.CONTEXT_OF_ARM
    assert sum(a.runs for a in r2b.ARMS) * 15 * 2 == 330
    assert r2b.MODELS == ("qwen3.8:27b", "gemma4:26b") == a2b.MODELS
    assert r2b.PROJECTS == a2b.PROJECTS
    for m in r2b.MODELS:
        assert a2b.slug(m) == r2b._slug(m)


@needs_data
def test_plan_order_is_arm_model_run_project_story(r2b):
    sel = selection(r2b)
    plan = r2b.build_plan(sel, r2b.load_cells(sel, Path("/nonexistent-out")))
    arm_rank = {a.name: i for i, a in enumerate(r2b.ARMS)}
    keys = [(arm_rank[s.arm.name], r2b.MODELS.index(s.model), s.run, r2b.PROJECTS.index(s.project)) for s in plan]
    assert keys == sorted(keys)
    assert [s.arm.name for s in plan[:90]] == ["N1"] * 90


def test_generation_options_come_from_the_registered_code(r2b):
    src = (SCRIPTS / "run_study2.py").read_text()
    assert "timeout=900.0, num_ctx=num_ctx, structured_output=False)" in src
    assert 'ap.add_argument("--num-ctx", type=int, default=16384' in src
    assert r2b.GEN_TIMEOUT_S == 900.0 and r2b.STRUCTURED_OUTPUT is False


@needs_data
def test_generation_options_equal_the_registered_manifests(r2b):
    manifests = r2b.registered_manifests()
    options = r2b.generation_options(r2b.SecReqGenConfig(), manifests)
    assert r2b.check_generation_options(options, manifests).status == "PASS"
    assert options["generator_max_retries"] == 3 and options["generator_retry_delay_s"] == 1.0
    assert set(options) == set(r2b.OPTION_SOURCES)


@needs_data
def test_fixed_line_is_not_the_fallback_and_holds_no_fact_sheet_name(r2b, a2b):
    assert r2b.FIXED_LINE == a2b.FIXED_LINE
    assert r2b.FIXED_LINE != r2b.FALLBACK and len(r2b.FIXED_LINE) != 41 and len(r2b.FALLBACK) == 41
    for project in r2b.PROJECTS:
        for s in json.loads((STUDY2 / project / "stories.json").read_text())["stories"]:
            assert not [i for i in s["fact_sheet"]["identifiers"] if i in r2b.FIXED_LINE]


@needs_data
def test_stored_contexts_check_passes(r2b):
    c = r2b.check_contexts()
    assert c.status == "PASS", c.detail


# ---------------------------------------------------------------------------
# Dry run writes nothing
# ---------------------------------------------------------------------------
@needs_data
def test_dry_run_writes_nothing(r2b, tmp_path, monkeypatch, capsys):
    offline(monkeypatch, r2b)
    out = tmp_path / "s2b_out"
    before = (listing(tmp_path), listing(STUDY2), STUDY2B.exists(), listing(STUDY2B))
    r2b.main(["--dry-run", "--out-root", str(out)])
    r2b.main(["--dry-run"])  # the real output folder
    text = capsys.readouterr().out
    after = (listing(tmp_path), listing(STUDY2), STUDY2B.exists(), listing(STUDY2B))
    assert before == after, "the dry run changed a folder"
    assert not out.exists()
    assert "Nothing was written" in text and "330 generations planned" in text


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_guard_output_folder(r2b, tmp_path):
    assert r2b.check_out_root(STUDY2).status == "FAIL"
    assert r2b.check_out_root(STUDY2 / "mastodon" / "new").status == "FAIL"
    assert r2b.check_out_root(tmp_path / "results" / "x").status == "FAIL"
    assert r2b.check_out_root(tmp_path / "Results").status == "FAIL"
    assert r2b.check_out_root(tmp_path / "study2b_scratch").status == "PASS"
    # A symbolic link or ".." that leads into study2.
    link = tmp_path / "link"
    link.symlink_to(STUDY2.parent)
    assert r2b.check_out_root(link / "study2" / "x").status == "FAIL"
    assert r2b.check_out_root(STUDY2B / ".." / "study2" / "x").status == "FAIL"
    # The file system does not see a difference between upper and lower case.
    assert r2b.check_out_root(STUDY2.parent / "STUDY2" / "x").status == "FAIL"
    assert r2b.check_out_root(STUDY2.parent / "Study2").status == "FAIL"
    assert r2b.check_out_root(SECREQGEN / "DATA" / "vectordb").status == "FAIL"
    # A folder inside the real output folder; the real folder itself in another case is the real folder.
    assert r2b.check_out_root(STUDY2B / "mastodon").status == "FAIL"
    assert r2b.check_out_root(STUDY2B).status == "PASS"
    assert r2b.is_real_root(STUDY2.parent / "STUDY2B") and not r2b.is_real_root(tmp_path / "study2b")


@needs_data
def test_real_output_folder_gets_the_full_plan_only(r2b, monkeypatch):
    offline(monkeypatch, r2b)
    for argv in (["--arms", "N1"], ["--models", "gemma4:26b"], ["--projects", "mastodon"],
                 ["--stories", "MT-ADV-001"], ["--max-runs", "1"]):
        sel = r2b.parse_selection(r2b.parse_args(argv))
        checks, _ = r2b.preflight(sel, r2b.STUDY2B, allow_drift=False, hard_stop_hours=5.0)
        assert any(c.condition == "-" and c.status == "FAIL" and "only" in c.name for c in checks), argv
    sel = r2b.parse_selection(r2b.parse_args([]))
    checks, _ = r2b.preflight(sel, r2b.STUDY2B, allow_drift=False, hard_stop_hours=5.0)
    assert not any(c.condition == "-" and c.status == "FAIL" for c in checks)


@needs_data
def test_main_refuses_output_inside_study2_and_writes_nothing(r2b, monkeypatch, capsys):
    offline(monkeypatch, r2b)
    target = STUDY2 / "study2b_never_created"
    before = listing(STUDY2)
    assert r2b.main(["--out-root", str(target), "--stories", "MT-ADV-001", "--max-runs", "1"]) == 1
    capsys.readouterr()
    assert not target.exists() and listing(STUDY2) == before


def test_guard_missing_prompt_file(r2b, chk, tmp_path, monkeypatch):
    copy = tmp_path / "s2b"
    shutil.copytree(chk.S2B, copy)
    (copy / "n1" / "rag_user_prompt.txt").unlink()
    monkeypatch.setattr(r2b, "PROMPTS_S2B", copy)
    checks = r2b.check_prompt_files()
    assert [c.status for c in checks] == ["FAIL", "FAIL"]
    assert "n1/rag_user_prompt.txt" in checks[0].detail


@needs_data
def test_main_refuses_missing_prompt_file(r2b, chk, tmp_path, monkeypatch, capsys):
    offline(monkeypatch, r2b)
    copy = tmp_path / "s2b"
    shutil.copytree(chk.S2B, copy)
    (copy / "i0" / "system_prompt.txt").unlink()
    monkeypatch.setattr(r2b, "PROMPTS_S2B", copy)
    out = tmp_path / "out"
    assert r2b.main(["--out-root", str(out), "--stories", "MT-ADV-001", "--max-runs", "1"]) == 1
    capsys.readouterr()
    assert not out.exists()


def test_guard_label_file_not_locked(r2b, tmp_path):
    review = _load("score_study2_review")
    d = tmp_path / "review"
    d.mkdir()
    assert r2b.check_label_lock(d).status == "FAIL"  # missing
    (d / review.HUMAN).write_text("item,label,note\n")
    c = r2b.check_label_lock(d)
    assert c.status == "FAIL" and "unlocked" in c.detail
    digest = hashlib.sha256((d / review.HUMAN).read_bytes()).hexdigest()
    (d / "locks.json").write_text(json.dumps({"version": 2, "locks": {review.HUMAN: {"sha256": digest}}}))
    assert r2b.check_label_lock(d).status == "PASS"
    (d / review.HUMAN).write_text("item,label,note\nR01,P,\n")
    c = r2b.check_label_lock(d)
    assert c.status == "FAIL" and "changed" in c.detail


def test_guard_notebook_section(r2b, tmp_path, monkeypatch):
    real = tmp_path / "study2b"
    monkeypatch.setattr(r2b, "STUDY2B", real)
    nb = tmp_path / "notebook.md"
    monkeypatch.setattr(r2b, "git_porcelain", lambda path: "")
    assert r2b.check_notebook(tmp_path / "scratch", nb).status == "SKIP"
    nb.write_text("## §15 — Study 2 results\ntext\n")
    assert r2b.check_notebook(real, nb).status == "FAIL"
    nb.write_text("## §15 — x\n\n## §16 — Study 2b\nApproved by the author on ____\n")
    assert r2b.check_notebook(real, nb).status == "FAIL"
    nb.write_text("## §15 — x\n\n## 16. Study 2b\nApproved by the author on 15 November 2026\n")
    assert r2b.check_notebook(real, nb).status == "PASS"
    nb.write_text("## §16 — Study 2b\nApproved by the author on 15 November 2026\n")
    assert r2b.check_notebook(real, nb).status == "PASS"
    nb.write_text("## 160 — wrong\nApproved by the author on 15 November 2026\n")
    assert r2b.check_notebook(real, nb).status == "FAIL"
    nb.write_text("## §16 — Study 2b\nApproved by the author on 15 November 2026\n")
    monkeypatch.setattr(r2b, "git_porcelain", lambda path: " M docs/RAG_ABLATION_LAB_NOTEBOOK.md\n")
    assert r2b.check_notebook(real, nb).status == "FAIL"
    monkeypatch.setattr(r2b, "git_porcelain", lambda path: None)
    assert r2b.check_notebook(real, nb).status == "FAIL"


def test_guard_other_experiment_script(r2b, monkeypatch):
    me = os.getpid()
    monkeypatch.setattr(r2b, "list_processes", lambda: [
        (me, 1, "python scripts/run_study2b.py --dry-run"),
        (424242, 1, "python scripts/run_cross_family_winning_config.py --n 2")])
    assert r2b.check_processes().status == "FAIL"
    for cmd in ("python scripts/run_study2.py --project mastodon", "python scripts/run_idea21_grounded_citation.py",
                "python scripts/run_adversarial_probe.py", "python scripts/run_threat_mapping.py",
                "python scripts/run_study2b.py"):
        monkeypatch.setattr(r2b, "list_processes", lambda cmd=cmd: [(424242, 1, cmd)])
        assert r2b.check_processes().status == "FAIL", cmd
    monkeypatch.setattr(r2b, "list_processes", lambda: [
        (me, 4242, "python scripts/run_study2b.py"), (4242, 1, "zsh -c 'python scripts/run_study2b.py'"),
        (5151, 1, "python scripts/run_study2_ai_raters.py"),
        (5152, 1, "/opt/homebrew/opt/ollama/bin/ollama serve"),
        (5153, 5152, "/opt/homebrew/opt/ollama/bin/ollama runner --model /x/blobs/sha256-0 --port 50000")])
    assert r2b.check_processes().status == "PASS"
    for cmd in ("python scripts/rag_ablation_judge.py", "python scripts/judge_content_quality.py",
                "python scripts/judge_retrieval_quality.py", "python scripts/bootstrap_dataset.py",
                "python -m secreqgen ingest docs/", "/x/.venv/bin/secreqgen generate story.json",
                "ollama run gemma4:26b"):
        monkeypatch.setattr(r2b, "list_processes", lambda cmd=cmd: [(424242, 1, cmd)])
        assert r2b.check_processes().status == "FAIL", cmd


@needs_data
def test_guard_lock_file(r2b, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / r2b.LOCK_NAME).write_text("{}")
    assert r2b.check_lock_file(out).status == "FAIL"
    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)
    sel = selection(r2b, arms=["N1"], models=["gemma4:26b"], projects=["mastodon"], stories={"MT-ADV-001"},
                    max_runs=1)
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 1
    capsys.readouterr()
    assert fake.requests == [] and (out / r2b.LOCK_NAME).read_text() == "{}"


@needs_data
def test_guard_ollama_drift(r2b, monkeypatch):
    manifests = r2b.registered_manifests()
    version = manifests[0]["ollama"]["version"]
    digests = dict(manifests[0]["ollama"]["digests"])
    monkeypatch.setattr(r2b, "ollama_state", lambda base_url: (version, digests))
    assert r2b.check_ollama("http://x", False, manifests)[0].status == "PASS"
    monkeypatch.setattr(r2b, "ollama_state", lambda base_url: ("0.99.0", digests))
    assert r2b.check_ollama("http://x", False, manifests)[0].status == "FAIL"
    c, info = r2b.check_ollama("http://x", True, manifests)
    assert c.status == "WARN" and info["drift"]
    changed = dict(digests, **{"gemma4:26b": "0" * 64})
    monkeypatch.setattr(r2b, "ollama_state", lambda base_url: (version, changed))
    assert r2b.check_ollama("http://x", False, manifests)[0].status == "FAIL"
    monkeypatch.setattr(r2b, "ollama_state", lambda base_url: ("unreachable", {"error": "down"}))
    assert r2b.check_ollama("http://x", True, manifests)[0].status == "FAIL"


def test_guard_hard_stop_and_max_runs(r2b, tmp_path):
    assert r2b.check_hard_stop(5.0, r2b.STUDY2B).status == "PASS"
    assert r2b.check_hard_stop(6.0, r2b.STUDY2B).status == "FAIL"
    assert r2b.check_hard_stop(0.0, tmp_path).status == "FAIL"
    assert r2b.check_hard_stop(0.01, tmp_path).status == "PASS"


# ---------------------------------------------------------------------------
# Resume, retry and hard stop
# ---------------------------------------------------------------------------
@needs_data
def test_resume_skips_records_that_exist(r2b, tmp_path, monkeypatch, capsys):
    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)
    out = tmp_path / "out"
    kw = dict(arms=["N1"], models=["qwen3.8:27b"], projects=["mastodon"], stories={"MT-ADV-001"})
    sel = selection(r2b, max_runs=2, **kw)
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 0
    assert len(fake.requests) == 2
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 0
    assert len(fake.requests) == 2, "a record that exists was generated again"
    sel3 = selection(r2b, max_runs=3, **kw)
    assert r2b.execute(sel3, out, make_state(r2b, sel3, out), 1.0, False) == 0
    capsys.readouterr()
    assert len(fake.requests) == 3
    recs = r2b.read_records(out / "mastodon" / "qwen3_8_27b" / "records.jsonl")
    assert sorted(r["run"] for r in recs) == [1, 2, 3]


@needs_data
def test_one_retry_then_a_recorded_failure(r2b, tmp_path, monkeypatch, capsys):
    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)
    monkeypatch.setattr("time.sleep", lambda s: None)  # the generator waits between its parse attempts
    out = tmp_path / "out"
    sel = selection(r2b, arms=["I0"], models=["gemma4:26b"], projects=["keycloak"], stories={"KC-S2-001"},
                    max_runs=2)
    fake.bad_json_next = 9  # run 1 fails twice (final failure); run 2 fails once, then passes
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 0
    recs = r2b.read_records(out / "keycloak" / "gemma4_26b" / "records.jsonl")
    assert [(r["run"], r["attempt"], bool(r["error"])) for r in recs] == [
        (1, 1, True), (1, 2, True), (2, 1, True), (2, 2, False)]
    assert [len(r["llm_calls"]) for r in recs] == [3, 3, 3, 1]
    n = len(fake.requests)
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 0
    capsys.readouterr()
    assert len(fake.requests) == n, "a recorded failure after the retry was tried again"


@needs_data
def test_http_error_from_ollama_stops_the_run(r2b, tmp_path, monkeypatch, capsys):
    """Ollama answers /api/tags, but /api/generate gives HTTP 500 (for example a model that cannot load).
    The run must stop at once. It must not turn the rest of the plan into recorded failures."""
    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)
    out = tmp_path / "out"
    sel = selection(r2b, arms=["N1", "B2"], models=["qwen3.8:27b"], projects=["mastodon"], stories=None, max_runs=None)
    fake.fail_next = 10_000
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 3
    assert len(fake.requests) == 1
    recs = r2b.read_records(out / "mastodon" / "qwen3_8_27b" / "records.jsonl")
    assert [(r["attempt"], r["error"].startswith(r2b.SERVER_ERROR_PREFIX)) for r in recs] == [(1, True)]
    m = json.loads((out / "mastodon" / "qwen3_8_27b" / "manifest_s2b.json").read_text())
    assert "HTTP error" in m["stop_reason"] and m["arms_complete"] == []
    # After Ollama is fixed, a resume gives the key its second attempt and finishes the plan.
    fake.fail_next = 0
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 0
    capsys.readouterr()
    recs = r2b.read_records(out / "mastodon" / "qwen3_8_27b" / "records.jsonl")
    assert len(recs) == 1 + 15 + 5 and recs[1]["attempt"] == 2 and recs[1]["error"] is None
    assert not (out / r2b.LOCK_NAME).exists()


@needs_data
def test_four_failed_generations_in_a_row_stop_the_run(r2b, tmp_path, monkeypatch, capsys):
    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = tmp_path / "out"
    sel = selection(r2b, arms=["A2"], models=["gemma4:26b"], projects=["bitwarden"], stories=None, max_runs=None)
    fake.bad_json_next = 10_000
    assert r2b.execute(sel, out, make_state(r2b, sel, out), 1.0, False) == 3
    capsys.readouterr()
    recs = r2b.read_records(out / "bitwarden" / "gemma4_26b" / "records.jsonl")
    assert len(recs) == r2b.MAX_FAILED_IN_A_ROW == 4 and all(r["error"] for r in recs)
    m = json.loads((out / "bitwarden" / "gemma4_26b" / "manifest_s2b.json").read_text())
    assert "in a row failed" in m["stop_reason"]


@needs_data
def test_hard_stop_leaves_complete_records(r2b, tmp_path, monkeypatch, capsys):
    fake = FakeOllama()
    install_fake(monkeypatch, r2b, fake)

    class Clock:
        t = 0.0

        def __call__(self):
            v = self.t
            self.t += 3600.0  # one hour for each look at the clock
            return v

    monkeypatch.setattr(r2b, "_clock", Clock())
    out = tmp_path / "out"
    sel = selection(r2b, arms=["N1"], models=["qwen3.8:27b"], projects=["mastodon"], stories=None, max_runs=1)
    code = r2b.execute(sel, out, make_state(r2b, sel, out), 2.5, False)
    capsys.readouterr()
    assert code == 3
    d = out / "mastodon" / "qwen3_8_27b"
    lines = (d / "records.jsonl").read_text().splitlines()
    assert len(lines) == len(fake.requests) == 2
    for line in lines:
        rec = json.loads(line)
        assert rec["error"] is None and rec["n_requirements"] == 2 and rec["latency_ms"] is not None
    m = json.loads((d / "manifest_s2b.json").read_text())
    assert m["stop_reason"].startswith("hard stop") and m["arms_complete"] == []
    assert not (out / r2b.LOCK_NAME).exists()


def test_complete_arm_rule_is_the_same_in_driver_and_analyzer(r2b, a2b):
    recs = [{"story_id": "S1", "arm": "B2", "run": 1, "error": None},
            {"story_id": "S2", "arm": "B2", "run": 1, "error": "x"},
            {"story_id": "S2", "arm": "B2", "run": 1, "error": "x"},
            {"story_id": "S1", "arm": "A2", "run": 1, "error": "x"}]
    assert "B2" in r2b.complete_arms(r2b.key_states(recs), ["S1", "S2"])
    assert "A2" not in r2b.complete_arms(r2b.key_states(recs), ["S1"])
    assert a2b.arm_complete(recs, ["S1", "S2"], "B2") and not a2b.arm_complete(recs, ["S1"], "A2")


# ---------------------------------------------------------------------------
# Analyzer, on invented records
# ---------------------------------------------------------------------------
FAKE_PROJECTS = ("pa", "pb", "pc")
N_REQ = 20


def _req(text: str) -> dict:
    return {"id": "SR-001", "user_story_id": "X", "title": text, "description": "", "rationale": "",
            "acceptance_criteria": [], "asvs_category": "V2: Authentication", "priority": "high",
            "implementation_notes": None, "source_references": []}


def _reqs(rate: float, ident: str) -> list[dict]:
    k = round(rate * N_REQ)
    return [_req(f"set {ident}" if i < k else "a generic control") for i in range(N_REQ)]


def _ctx(ident: str) -> str:
    return f"### [1] doc.md > Settings (Relevance: 0.90)\nThe setting {ident} controls this.\n"


def build_invented(root: Path, a2b, rates: dict, drop=(), errors=(), single_errors=()) -> tuple[Path, Path, Path]:
    """rates: {(model or '*', arm): rate}. drop/errors/single_errors: sets of (model, arm, story, run)."""
    reg = a2b.registered
    s2, s2b = root / "study2", root / "study2b"

    def rate(model, arm):
        return rates.get((model, arm), rates[("*", arm)])

    for p in FAKE_PROJECTS:
        stories = [{"id": f"{p}-{i}", "title": "t", "story": "s", "acceptance_criteria": [],
                    "fact_sheet": {"source_files": [], "identifiers": [f"ID_{p.upper()}_{i}"],
                                   "generic_terms": [], "facts": []}} for i in range(1, 6)]
        (s2 / p).mkdir(parents=True)
        (s2 / p / "stories.json").write_text(json.dumps({"project": p, "stories": stories}))
        for model in a2b.MODELS:
            d = s2 / p / "results" / a2b.slug(model)
            d.mkdir(parents=True)
            frozen = {s["id"]: {"context": _ctx(s["fact_sheet"]["identifiers"][0]),
                                "context_sha256": sha(_ctx(s["fact_sheet"]["identifiers"][0]))} for s in stories}
            (d / "retrieval_frozen.json").write_text(json.dumps(frozen))
            lines = []
            for arm in ("A", "B"):
                for s in stories:
                    for run in (1, 2, 3):
                        lines.append(json.dumps({
                            "project": p, "model": model, "story_id": s["id"], "arm": arm, "run": run,
                            "prompt_system": "SYSTEM PROMPT", "prompt_user_non_doc": "USER PROMPT",
                            "doc_context_ref": None if arm == "A" else "frozen",
                            "requirements": _reqs(rate(model, arm), s["fact_sheet"]["identifiers"][0]),
                            "error": None, "prompt_tokens": 100, "completion_tokens": 10, "latency_ms": 1.0}))
            (d / "records.jsonl").write_text("\n".join(lines) + "\n")
            d2 = s2b / p / a2b.slug(model)
            d2.mkdir(parents=True)
            (d2 / "retrieval_frozen.json").write_bytes((d / "retrieval_frozen.json").read_bytes())
            new = []
            for arm, runs in a2b.NEW_ARMS.items():
                for s in stories:
                    ident = s["fact_sheet"]["identifiers"][0]
                    ref = a2b.CONTEXT_OF_ARM[arm]
                    ctx = None if ref is None else (_ctx(ident) if ref == "frozen" else a2b.FIXED_LINE)
                    sent = "USER PROMPT" if ctx is None else f"USER PROMPT\n{ctx}\nEND"
                    for run in range(1, runs + 1):
                        key = (model, arm, s["id"], run)
                        if key in drop:
                            continue
                        base = {"study": "study2b", "project": p, "model": model, "story_id": s["id"], "arm": arm,
                                "run": run, "prompt_system": "SYSTEM PROMPT", "prompt_system_sha256": sha("SYSTEM PROMPT"),
                                "prompt_user_sent": sent, "prompt_user_sha256": sha(sent),
                                "prompt_user_non_doc": sent if ctx is None else sent.replace(ctx, "", 1),
                                "doc_context_ref": ref, "doc_context_sha256": None if ctx is None else sha(ctx),
                                "prompt_tokens": 100, "completion_tokens": 10, "latency_ms": 1.0,
                                "llm_calls": [{"prompt_tokens": 100, "completion_tokens": 10, "latency_ms": 1.0}]}
                        n_err = 2 if key in errors else (1 if key in single_errors else 0)
                        for attempt in range(1, n_err + 1):
                            new.append(json.dumps({**base, "attempt": attempt, "requirements": [], "n_requirements": 0,
                                                   "error": "GenerationError: invented"}))
                        if n_err == 0:
                            new.append(json.dumps({**base, "attempt": 1, "error": None,
                                                   "requirements": _reqs(rate(model, arm), ident)}))
            (d2 / "records.jsonl").write_text("\n".join(new) + "\n")
    old = reg.STUDY2
    reg.STUDY2 = s2
    try:
        reports = [reg.analyze(p) for p in FAKE_PROJECTS]
    finally:
        reg.STUDY2 = old
    analysis = s2 / "analysis_registered.json"
    analysis.write_text(json.dumps(reports, indent=1, default=str))
    return s2, s2b, analysis


BASE_RATES = {("*", "A"): 0.0, ("*", "B"): 1.0, ("*", "N1"): 0.8, ("*", "B2"): 1.0, ("*", "A2"): 0.0,
              ("*", "I0"): 0.0, ("*", "N0"): 0.6}


def run_invented(a2b, st, tmp_path, rates=None, **kw) -> dict:
    s2, s2b, analysis = build_invented(tmp_path, a2b, {**BASE_RATES, **(rates or {})}, **kw)
    return a2b.analyze(s2, s2b, analysis, st, projects=FAKE_PROJECTS, models=a2b.MODELS)


def test_analyzer_documents_case_and_identity(a2b, st, tmp_path):
    res = run_invented(a2b, st, tmp_path)
    for model in a2b.MODELS:
        m = res["models"][model]
        k1, k2, ba = m["K1_N1_minus_A"], m["K2_B_minus_N1"], m["registered_B_minus_A"]
        assert k1["n"] == k2["n"] == ba["n"] == 15
        for a, b, c in zip(k1["diffs"], k2["diffs"], ba["diffs"]):
            assert abs(a + b - c) <= 1e-12
        assert m["K1_plus_K2_equals_B_minus_A"] is True
        assert k1["p"] == pytest.approx(2 / 2**15, abs=1e-15) and k1["n_used"] == 15
        assert m["K1_passes"] and m["K2_passes"]
        assert m["S"] == pytest.approx(0.8) and m["S_band"] == "0.70 or more"
        assert all(m["arms"][a]["complete"] for a in a2b.NEW_ARMS)
        assert not m["indicative"]
    assert res["sentences"]["main"] == a2b.SENT_DOCS
    assert res["sentences"]["i0"] == a2b.SENT_I0
    md = a2b.to_markdown(res)
    assert a2b.SENT_DOCS in md and "p < 0.0001, the smallest value that 15 stories can give" in md


def test_analyzer_instructions_case(a2b, st, tmp_path):
    res = run_invented(a2b, st, tmp_path, rates={("*", "N1"): 0.2})
    assert res["sentences"]["main"] == a2b.SENT_INSTR
    for model in a2b.MODELS:
        assert res["models"][model]["S_band"] == "below 0.40"


def test_analyzer_mixed_case_and_models_that_disagree(a2b, st, tmp_path):
    res = run_invented(a2b, st, tmp_path / "a", rates={("*", "N1"): 0.5})
    assert res["sentences"]["main"].startswith(a2b.SENT_MIXED + " (S = 0.50 for qwen3.8:27b; S = 0.50 for gemma4:26b")
    res = run_invented(a2b, st, tmp_path / "b", rates={("qwen3.8:27b", "N1"): 0.8, ("gemma4:26b", "N1"): 0.2})
    assert res["sentences"]["main"].startswith(a2b.SENT_MIXED)


def test_analyzer_b_minus_a_not_above_zero(a2b, st, tmp_path):
    res = run_invented(a2b, st, tmp_path, rates={("*", "B"): 0.0, ("*", "B2"): 0.0, ("*", "N1"): 0.0})
    for model in a2b.MODELS:
        assert res["models"][model]["S"] is None and res["models"][model]["S_band"] == "not defined"
    assert len(res["sentences"]["not_defined"]) == 2
    assert "S = not defined" in res["sentences"]["main"]


def test_analyzer_drift_flags(a2b, st, tmp_path):
    res = run_invented(a2b, st, tmp_path / "a", rates={("qwen3.8:27b", "B2"): 0.9, ("gemma4:26b", "A2"): 0.05})
    q, g = res["models"]["qwen3.8:27b"], res["models"]["gemma4:26b"]
    assert q["drift"]["B2"]["flagged"] is True and q["indicative"]
    assert g["drift"]["A2"]["flagged"] is False and not g["indicative"]  # 0.05 is not more than 0.05
    assert len(res["sentences"]["indicative"]) == 1
    res = run_invented(a2b, st, tmp_path / "b", rates={("gemma4:26b", "A2"): 0.1, ("qwen3.8:27b", "B2"): 0.95})
    assert res["models"]["gemma4:26b"]["drift"]["A2"]["flagged"] is True
    assert res["models"]["qwen3.8:27b"]["drift"]["B2"]["flagged"] is False


def test_analyzer_uses_complete_arms_only(a2b, st, tmp_path):
    res = run_invented(a2b, st, tmp_path,
                       drop={("qwen3.8:27b", "N0", "pa-1", 3), ("gemma4:26b", "B2", "pc-5", 1)},
                       errors={("qwen3.8:27b", "I0", "pb-2", 1)},
                       single_errors={("gemma4:26b", "I0", "pb-3", 2)})
    q, g = res["models"]["qwen3.8:27b"], res["models"]["gemma4:26b"]
    assert not q["arms"]["N0"]["complete"] and q["N0_minus_A"] is None and q["N1_minus_N0"] is None
    assert q["arms"]["I0"]["complete"] and q["arms"]["I0"]["hard_failures"] == 1
    assert q["arms"]["I0"]["error_lines"] == 2 and q["I0_minus_A"]["n"] == 15
    assert not g["arms"]["I0"]["complete"] and g["I0_minus_A"] is None
    assert not g["arms"]["B2"]["complete"] and g["drift"]["B2"]["difference"] is None and g["indicative"]
    assert res["sentences"]["i0"] is None  # one model has no complete I0
    res2 = run_invented(a2b, st, tmp_path / "n1", drop={("gemma4:26b", "N1", "pa-2", 2)})
    assert res2["sentences"]["main"].startswith("not delivered")


def test_analyzer_i0_sentence_limit(a2b, st, tmp_path):
    assert run_invented(a2b, st, tmp_path / "a", rates={("*", "I0"): 0.05})["sentences"]["i0"] == a2b.SENT_I0
    assert run_invented(a2b, st, tmp_path / "b", rates={("*", "I0"): 0.1})["sentences"]["i0"] is None


def test_analyzer_stops_when_the_registered_values_differ(a2b, st, tmp_path):
    s2, s2b, analysis = build_invented(tmp_path, a2b, BASE_RATES)
    data = json.loads(analysis.read_text())
    data[0]["models"]["gemma4:26b"]["per_story"]["B"]["pa-1"]["frac_reqs_citing"] = 0.123456
    analysis.write_text(json.dumps(data))
    with pytest.raises(a2b.AnalysisStop) as e:
        a2b.analyze(s2, s2b, analysis, st, projects=FAKE_PROJECTS, models=a2b.MODELS)
    assert "per_story.B" in str(e.value) and "0.123456" not in str(e.value)


def test_analyzer_stops_on_a_changed_sent_prompt(a2b, st, tmp_path):
    s2, s2b, analysis = build_invented(tmp_path, a2b, BASE_RATES)
    p = s2b / "pa" / a2b.slug("qwen3.8:27b") / "records.jsonl"
    lines = p.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["prompt_user_sent"] += " extra"
    lines[0] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(a2b.AnalysisStop):
        a2b.analyze(s2, s2b, analysis, st, projects=FAKE_PROJECTS, models=a2b.MODELS)


def test_exact_test_known_values_and_bands(a2b, st):
    five = a2b.contrast(st, {f"s{i}": i / 10 for i in range(1, 6)}, {f"s{i}": 0.0 for i in range(1, 6)}, test=True)
    assert five["p"] == pytest.approx(0.0625, abs=1e-15) and five["n_used"] == 5
    assert a2b.p_text(five) == "p = 0.0625, the smallest value that 5 stories can give"
    fifteen = a2b.contrast(st, {f"s{i}": i / 100 for i in range(1, 16)}, {f"s{i}": 0.0 for i in range(1, 16)},
                           test=True)
    assert fifteen["p"] == pytest.approx(2 / 2**15, abs=1e-15)
    zeros = a2b.contrast(st, {"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}, test=True)
    assert a2b.p_text(zeros) == "p = 1.0000, all differences are zero" and zeros["d_z"] is None
    assert a2b.contrast(st, {"a": 1.0}, {"a": 0.0}, test=False).get("p") is None
    tol = st.TOL
    assert a2b.band(0.70, tol) == "high" and a2b.band(0.70 - 1e-12, tol) == "high"
    assert a2b.band(0.6999, tol) == "mid" and a2b.band(0.40, tol) == "mid"
    assert a2b.band(0.3999, tol) == "low" and a2b.band(-0.2, tol) == "low" and a2b.band(None, tol) is None
    assert not a2b.passes({"mean": 0.1, "p": 0.0125}, tol) and a2b.passes({"mean": 0.1, "p": 0.0124}, tol)
    assert not a2b.passes({"mean": 0.0, "p": 0.001}, tol)


def test_analyzer_refuses_before_the_labels_are_locked(a2b, tmp_path, monkeypatch, capsys):
    review = tmp_path / "review"
    review.mkdir()
    monkeypatch.setattr(a2b, "REVIEW_V2", review)
    out = tmp_path / "out"
    assert a2b.main(["--out-dir", str(out), "--study2b-root", str(tmp_path / "none")]) == 1
    assert "refused" in capsys.readouterr().out
    assert not out.exists()
    for target in (STUDY2 / "x", STUDY2.parent / "STUDY2" / "x", STUDY2.parent / "Study2"):
        assert a2b.main(["--out-dir", str(target)]) == 1  # never into study2, also not in another case
        assert "is inside" in capsys.readouterr().out, target
    assert not (STUDY2 / "x").exists()


@needs_data
def test_analyzer_reproduces_the_registered_arms_a_and_b(a2b, st):
    """The registered arms through the Study 2b path equal analysis_2026-11-02.json (prints nothing)."""
    if not a2b.REGISTERED_ANALYSIS.is_file():
        pytest.skip("analysis_2026-11-02.json not found")
    scored = a2b.score_with_registered_analyze(
        a2b.STUDY2, a2b.PROJECTS, a2b.registered_cells(a2b.STUDY2, a2b.PROJECTS, a2b.MODELS))
    n = a2b.regression_check(scored, a2b.REGISTERED_ANALYSIS, a2b.PROJECTS, a2b.MODELS, st.TOL)
    assert n == 2 * 2 * 15
    assert a2b.registered.STUDY2 == STUDY2  # the in-memory pointer is restored
