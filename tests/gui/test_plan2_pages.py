"""Corrections of plan 2: the dialog, the jobs and the pages. No model runs."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from test_context_dialog import (  # noqa: E402
    RESULT,
    YAML_TEXT,
    make_stand_in,
)

pytestmark = pytest.mark.anyio

HINT = (
    "This run read files that were converted to Markdown. "
    "The thesis tests used Markdown files only."
)


@pytest.fixture
def job(monkeypatch):
    """Put a job with a stand-in in place of the shared job."""
    from secreqgen_gui import context_job

    def build(**kw):
        fresh = context_job.ContextJob(make_stand_in(**kw))
        monkeypatch.setattr(context_job, "writer", fresh)
        return fresh

    return build


def _field(user, marker):
    return user.find(marker=marker).elements.pop()


def _on_screen(user, text):
    """Say if a label with this text is shown, with all its parents shown."""
    for element in user.find(text).elements:
        item = element
        while item is not None and item.visible:
            parent = item.parent_slot.parent if item.parent_slot else None
            if parent is None or parent is item:
                return True
            item = parent
    return False


def _labels(user):
    return [e.text for e in user.find(kind=ui.label).elements]


async def _open_dialog(user, tmp_path):
    from secreqgen_gui import form_memory

    folder = tmp_path / "code"
    folder.mkdir(exist_ok=True)
    form_memory.remember("requirements", "context_folder", str(folder))
    await user.open("/requirements/new")
    await user.should_see("System context file (optional)")
    user.find("Write one with the model").click()
    await user.should_see("Write a system context")
    return folder


async def _wait_for(check, seconds=5.0):
    stop = time.time() + seconds
    while time.time() < stop:
        if check():
            return True
        await asyncio.sleep(0.05)
    return check()


# B1: the two start checks never wait for each other


def test_two_starts_at_the_same_time_never_block_each_other(tmp_path, monkeypatch):
    from secreqgen_gui import context_job, jobs

    monkeypatch.setattr(
        jobs.storage, "new_run_id", lambda kind: f"20261118-000000-{kind}"
    )

    def finish(self, target, run_id, spec, manifest):
        with self._lock:
            self._state.state = "done"

    monkeypatch.setattr(jobs.JobManager, "_guarded", finish)
    manager = jobs.JobManager(check_server=lambda url: None)
    monkeypatch.setattr(jobs, "manager", manager)
    context = context_job.ContextJob(
        lambda options, **_kw: SimpleNamespace(writer="m", result={}, output=None)
    )
    monkeypatch.setattr(context_job, "writer", context)
    spec = jobs.RequirementsRunSpec(tmp_path / "s.json", tmp_path)

    def slow(original):
        """Keep the lock a short time, so that the wrong order shows at once."""

        def busy(self):
            with self._lock:
                answer = original(self)
                time.sleep(0.002)
                return answer

        return busy

    monkeypatch.setattr(jobs.JobManager, "busy", slow(jobs.JobManager.busy))
    monkeypatch.setattr(
        context_job.ContextJob, "busy", slow(context_job.ContextJob.busy)
    )
    counts = {"context": 0, "run": 0}
    stop = threading.Event()

    def loop_context():
        while not stop.is_set():
            try:
                context.start(tmp_path, None, "m")
            except RuntimeError:
                pass
            counts["context"] += 1
            time.sleep(0.001)  # let the other thread take the lock

    def loop_run():
        while not stop.is_set():
            try:
                manager._start("requirements", spec)
            except RuntimeError:
                pass
            counts["run"] += 1
            time.sleep(0.001)

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    threads = [
        threading.Thread(target=loop_context, daemon=True),
        threading.Thread(target=loop_run, daemon=True),
    ]
    try:
        for thread in threads:
            thread.start()
        last = (0, 0)
        for _ in range(8):  # 2 seconds, one look each 0.25 seconds
            time.sleep(0.25)
            now = (counts["context"], counts["run"])
            assert now[0] > last[0] and now[1] > last[1], f"stuck: {now}"
            last = now
    finally:
        stop.set()
        for thread in threads:
            thread.join(5)
        sys.setswitchinterval(old_interval)
    assert not any(thread.is_alive() for thread in threads)


# B2: a new dialog starts empty


async def test_a_new_dialog_shows_no_old_draft_and_no_old_steps(user, tmp_path, job):
    running = job()
    running.start(tmp_path, None, "m")
    running.join(10)
    assert running.state()["state"] == "done"
    await _open_dialog(user, tmp_path)
    await asyncio.sleep(1.2)  # one tick of the timer
    await user.should_not_see("Lines and their sources")
    assert not _on_screen(user, "Scan the folder")
    await user.should_not_see(marker="context-yaml")
    assert _field(user, "context-write").enabled
    # A new job in this dialog shows its own result.
    user.find(marker="context-write").click()
    await user.should_see("Lines and their sources")


async def test_a_new_dialog_shows_no_old_error(user, tmp_path, job):
    running = job(error=ValueError("boom"))
    running.start(tmp_path, None, "m")
    running.join(10)
    assert running.state()["state"] == "failed"
    await _open_dialog(user, tmp_path)
    await asyncio.sleep(1.2)
    assert _field(user, "context-error").text == ""
    await user.should_not_see("boom")
    assert not _on_screen(user, "Scan the folder")


async def test_a_job_in_progress_shows_its_progress(user, tmp_path, job):
    gate = threading.Event()
    running = job(gate=gate)
    running.start(tmp_path, None, "m")
    try:
        await _open_dialog(user, tmp_path)
        await asyncio.sleep(1.2)
        assert _on_screen(user, "Scan the folder")
        await user.should_see("42 files")
        assert not _field(user, "context-write").enabled
    finally:
        gate.set()
        running.join(10)
    await asyncio.sleep(1.2)
    await user.should_see("Lines and their sources")


async def test_write_is_off_at_once_and_a_second_click_does_nothing(
    user, tmp_path, job
):
    running = job()
    release = threading.Event()
    calls = []
    real_start = running.start

    def slow_start(*args):
        calls.append(args)
        release.wait(10)
        real_start(*args)

    running.start = slow_start
    await _open_dialog(user, tmp_path)
    user.find(marker="context-write").click()
    await asyncio.sleep(0.2)  # the start call is still waiting
    assert not _field(user, "context-write").enabled
    user.find(marker="context-write").click()
    await asyncio.sleep(0.2)
    assert len(calls) == 1
    release.set()
    assert await _wait_for(lambda: running.state()["state"] == "done")
    await asyncio.sleep(1.2)
    assert _field(user, "context-error").text == ""
    await user.should_see("Lines and their sources")


async def test_the_error_line_is_empty_when_a_job_starts_and_ends(user, tmp_path, job):
    from secreqgen_gui import context_job

    running = job(error=ValueError("boom"))
    await _open_dialog(user, tmp_path)
    user.find(marker="context-write").click()
    assert await _wait_for(lambda: running.state()["state"] == "failed")
    await asyncio.sleep(1.2)
    assert _field(user, "context-error").text == ("Unexpected error. ValueError: boom")
    # The next job works. The old error goes away when it starts and ends.
    running._run_context = make_stand_in()
    assert isinstance(running, context_job.ContextJob)
    user.find(marker="context-write").click()
    assert await _wait_for(lambda: running.state()["state"] == "done")
    await asyncio.sleep(1.2)
    assert _field(user, "context-error").text == ""
    await user.should_see("Lines and their sources")


# B3: the texts of the dialog


def test_the_numbers_beside_the_steps():
    from secreqgen_gui.pages.context_dialog import numbers_text

    assert numbers_text("scan", {"files": 10116, "parts": 4}) == "10,116 files"
    assert numbers_text("scan", {"files": 1, "parts": 1}) == "1 file"
    assert numbers_text("scan", {}) == ""
    assert numbers_text("write", {"attempt": 1}) == ""
    assert numbers_text("write", {"attempt": 2}) == "try 2 of 3"
    assert numbers_text("write", {"attempt": 3}) == "try 3 of 3"
    assert numbers_text("check", {"kept": 21, "dropped": 1}) == "21 of 22 passed"
    assert (
        numbers_text("check", {"kept": 1200, "dropped": 0}) == "1,200 of 1,200 passed"
    )
    assert numbers_text("check", {"kept": 1}) == ""


def test_the_three_step_names():
    from secreqgen_gui import context_job

    assert [context_job.STAGE_NAMES[n] for n in context_job.STAGES] == [
        "Scan the folder",
        "Write the context",
        "Check each line",
    ]


def test_the_lines_above_the_draft():
    from secreqgen_gui.pages.context_dialog import notes_lines

    def lines(secret=0, stopped=False, seen=42):
        facts = {
            "files_seen": seen,
            "secret_files_skipped": secret,
            "stopped_early": stopped,
        }
        return notes_lines({"facts": facts})

    assert lines() == []
    assert lines(secret=1) == ["1 file that can hold secrets is not read."]
    assert lines(secret=7) == ["7 files that can hold secrets are not read."]
    assert lines(stopped=True, seen=20000) == [
        "The scan stopped at 20,000 files. Pick a smaller folder to scan all files."
    ]
    assert len(lines(secret=7, stopped=True, seen=20000)) == 2


def _big_result():
    data = json.loads(json.dumps(RESULT))
    data["facts"] = {
        "files_seen": 20000,
        "secret_files_skipped": 1,
        "stopped_early": True,
    }
    data["context"]["features"][0]["items"] += [
        {"text": f"Feature number {n}.", "source": "F1", "path": f"docs/f{n}.md"}
        for n in range(12)
    ]
    data["context"]["data_sensitivity"]["critical"] = [
        {"text": "Passwords", "source": "F1", "path": "README.md"}
    ]
    return data


async def test_the_draft_shows_every_line_under_one_heading_for_each_place(
    user, tmp_path, job
):
    running = job(result=_big_result())
    await _open_dialog(user, tmp_path)
    user.find(marker="context-write").click()
    assert await _wait_for(lambda: running.state()["state"] == "done")
    await asyncio.sleep(1.2)
    await user.should_see("1 file that can hold secrets is not read.")
    await user.should_see(
        "The scan stopped at 20,000 files. Pick a smaller folder to scan all files."
    )
    labels = _labels(user)
    for place in (
        "tech_stack",
        "features: messaging",
        "data_sensitivity: critical",
        "security_profile",
    ):
        assert labels.count(place) == 1
    for number in range(12):
        assert f"Feature number {number}." in labels
        assert f"docs/f{number}.md" in labels
    assert "High" in labels
    assert "Chosen by the model, not taken from a file." in labels
    # The list has no box of its own that cuts a row.
    classes = [" ".join(e.classes) for e in user.find(kind=ui.column).elements]
    assert not any("max-h-" in c for c in classes)


async def test_the_part_that_opens_lists_each_removed_item(user, tmp_path, job):
    data = json.loads(json.dumps(RESULT))
    data["dropped"] = [
        {"what": "Kafka", "why": "not in the files"},
        {"what": "Redis", "why": "only in a comment"},
    ]
    running = job(result=data)
    await _open_dialog(user, tmp_path)
    user.find(marker="context-write").click()
    assert await _wait_for(lambda: running.state()["state"] == "done")
    await asyncio.sleep(1.2)
    await user.should_see("Not in the draft (2)")
    for text in ("Kafka", "not in the files", "Redis", "only in a comment"):
        await user.should_see(text)
    await user.should_not_see("Removed by the check")


async def test_a_save_that_fails_names_the_reason_and_the_folder(
    user, tmp_path, job, monkeypatch
):
    from secreqgen_gui import storage
    from secreqgen_gui.settings import show_path

    running = job()
    await _open_dialog(user, tmp_path)
    user.find(marker="context-write").click()
    assert await _wait_for(lambda: running.state()["state"] == "done")
    await asyncio.sleep(1.2)

    def refuse(*_args, **_kw):
        raise PermissionError(13, "Permission denied", "x.json.tmp")

    monkeypatch.setattr(storage, "save_context", refuse)
    user.find(marker="context-save").click()
    folder = show_path(storage.contexts_dir())
    await user.should_see(
        f"The system context cannot be saved: Permission denied. "
        f"Check the folder {folder}."
    )
    await user.should_not_see("x.json.tmp")
    assert YAML_TEXT in _field(user, "context-yaml").value


# B4: the tag and the manifest of a run with other file types


def _stories(tmp_path):
    item = {"id": "S-1", "title": "T", "story": "As a user."}
    path = tmp_path / "stories.json"
    path.write_text(json.dumps({"user_stories": [item]}), encoding="utf-8")
    return path


def _docs(tmp_path, rst=2):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.md").write_text("# A\n\nText.\n", encoding="utf-8")
    for number in range(rst):
        (folder / f"f{number}.rst").write_text("T\n=\n\nText.\n", encoding="utf-8")
    return folder


def _present(names):
    models = tuple(SimpleNamespace(name=n) for n in names)
    return lambda url: SimpleNamespace(reachable=True, models=models, error=None)


def _report(converted, skipped=()):
    return SimpleNamespace(converted=converted, skipped=list(skipped))


def _run(tmp_path, switch, result, gate=None, docs=None):
    from secreqgen_gui import storage
    from secreqgen_gui.jobs import JobManager, ThreatRunSpec

    def fake_run(options, **_kw):
        if gate is not None:
            gate.wait(10)
        return result

    manager = JobManager(run_threats=fake_run, check_server=_present(["w", "c"]))
    spec = ThreatRunSpec(
        _stories(tmp_path),
        docs or _docs(tmp_path),
        writer_model="w",
        checker_model="c",
        other_formats=switch,
    )
    run_id = manager.start_threats(spec)
    return manager, run_id, storage


def _ok_result(report):
    return SimpleNamespace(skipped=[], aborted=None, stopped=False, docs_report=report)


def test_the_manifest_has_the_switch_and_the_expected_files_during_the_run(tmp_path):
    gate = threading.Event()
    manager, run_id, storage = _run(tmp_path, True, _ok_result(None), gate)
    try:
        assert _wait_for_manifest(storage, run_id)
        manifest = storage.read_manifest(run_id)
        assert manifest["status"] == "running"
        assert manifest["other_formats"] is True
        assert manifest["docs_formats"] == {"converted": {".rst": 2}, "skipped": 0}
    finally:
        gate.set()
        manager.join(10)


def _wait_for_manifest(storage, run_id):
    stop = time.time() + 5
    while time.time() < stop:
        manifest = storage.read_manifest(run_id)
        if manifest and manifest.get("stories_total"):
            return True
        time.sleep(0.02)
    return False


def test_the_real_report_replaces_the_expected_files_at_the_end(tmp_path):
    skipped = [(f"x{n}.pdf", f"reason {n}") for n in range(7)]
    result = _ok_result(_report({".rst": 1}, skipped))
    manager, run_id, storage = _run(tmp_path, True, result)
    manager.join(10)
    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "finished"
    assert manifest["other_formats"] is True
    assert manifest["docs_formats"] == {"converted": {".rst": 1}, "skipped": 7}
    assert manifest["skipped_files"] == [[f"x{n}.pdf", f"reason {n}"] for n in range(5)]


def test_a_run_that_converted_nothing_drops_the_expected_files(tmp_path):
    manager, run_id, storage = _run(tmp_path, True, _ok_result(None))
    manager.join(10)
    manifest = storage.read_manifest(run_id)
    assert manifest["other_formats"] is True
    assert "docs_formats" not in manifest


def test_a_run_with_the_switch_off_has_neither_key(tmp_path):
    manager, run_id, storage = _run(tmp_path, False, _ok_result(None))
    manager.join(10)
    manifest = storage.read_manifest(run_id)
    assert "other_formats" not in manifest
    assert "docs_formats" not in manifest


def test_no_expected_files_when_the_folder_has_none(tmp_path):
    gate = threading.Event()
    docs = _docs(tmp_path, rst=0)
    manager, run_id, storage = _run(tmp_path, True, _ok_result(None), gate, docs)
    try:
        assert _wait_for_manifest(storage, run_id)
        manifest = storage.read_manifest(run_id)
        assert manifest["other_formats"] is True
        assert "docs_formats" not in manifest
    finally:
        gate.set()
        manager.join(10)


def _requirements_run(run_id, formats):
    from secreqgen_gui import storage

    folder = storage.run_dir(run_id)
    folder.mkdir(parents=True)
    data = {
        "results": [],
        "failures": [],
        "model": "m",
        "tested_prompts": True,
        "stopped": False,
        "not_run": [],
    }
    (folder / "requirements.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "kind": "requirements",
        "status": "running",
        "per_story": [],
    }
    if formats is not None:
        manifest["docs_formats"] = formats
    storage.write_manifest(run_id, manifest)


@pytest.mark.parametrize(
    ("formats", "shown"),
    [
        ({"converted": {".rst": 3}, "skipped": 0}, True),
        ({"converted": {".rst": 3, ".txt": 1}, "skipped": 2}, True),
        ({"converted": {}, "skipped": 1}, False),
        ({"converted": {".rst": 0}, "skipped": 0}, False),
        ({"converted": "x", "skipped": 0}, False),
        (None, False),
    ],
)
async def test_the_tag_shows_only_when_files_were_converted(user, formats, shown):
    run_id = "20261118-100000-requirements"
    _requirements_run(run_id, formats)
    await user.open(f"/runs/{run_id}/requirements")
    await user.should_see("Requirements")
    await asyncio.sleep(0.3)
    if not shown:
        await user.should_not_see(marker="tag-file-types")
    else:
        await user.should_see(marker="tag-file-types")
        assert HINT in [e.text for e in user.find(kind=ui.tooltip).elements]


async def test_the_tag_shows_on_the_page_of_a_run_in_progress(user):
    run_id = "20261118-100000-requirements"
    _requirements_run(run_id, {"converted": {".rst": 2}, "skipped": 0})
    await user.open(f"/runs/{run_id}")
    await user.should_see("untested file types")


# B5: real file names only for a run that is stored with mirror names


def _threat_run(run_id, header, docs_formats):
    from secreqgen_gui import storage

    story = "MM-US-007"
    folder = storage.run_dir(run_id) / "stories" / story
    folder.mkdir(parents=True)
    row = {
        "id": "T1", "type": "attack", "threat": "Users may phish.", "harm": "",
        "story_part": "x", "source": "P1", "quote": "Users must log in.",
        "requirement": "R.", "from_extra_group": False, "actor": "", "stride": "",
        "weak_signs": [], "second_model": "accepts",
    }  # fmt: skip
    model = {
        "draft_notice": "Draft.", "prompt_set": "threats_v5b_labels",
        "product": "p", "model": "m",
        "story": {"id": story, "title": "T", "story": "S", "acceptance_criteria": []},
        "seconds": 1,
        "calls": [
            {"group": 1, "passages": 1, "attempts": 1, "ok": True, "seconds": 1.0}
        ],
        "label_step": True, "about": None, "checker": "c", "extra_passages": 0,
        "passages": [
            {
                "ref": "P1",
                "header": header,
                "text": f"### [1] {header}\n# Page\n\nUsers must log in.\n",
                "group": 1,
            }
        ],
        "rows": [row], "dropped": [],
    }  # fmt: skip
    (folder / "threats.json").write_text(json.dumps(model), encoding="utf-8")
    (folder / "threats.md").write_text("# page\n", encoding="utf-8")
    manifest = {
        "run_id": run_id, "kind": "threats", "status": "finished",
        "created_at": "2026-11-17T10:00:00Z", "product": "p", "writer": "w",
        "checker": "c", "check": True, "story_ids": [story], "stories_total": 1,
        "prompt_set": {"kind": "threats", "name": "x", "tested": True},
        "per_story": [{"story_id": story, "folder": story, "status": "ok", "rows": 1}],
        "failures": [], "not_run": [], "error": None,
    }  # fmt: skip
    if docs_formats:
        manifest["docs_formats"] = {"converted": {".rst": 1}, "skipped": 0}
    storage.write_manifest(run_id, manifest)


async def _export(user, run_id, marker):
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_see("1 threat")
    user.find(marker="row-keep-T1").click()
    await user.should_see("You kept 1 and deleted 0.")
    user.find("Export kept threats").click()
    user.find(marker=marker).click()
    return (await user.download.next()).text


@pytest.mark.parametrize(
    ("marker", "real", "mirror"),
    [
        ("export-markdown", "guide/install.rst > Login", "install.rst.md"),
        ("export-json", '"page": "guide/install.rst"', "install.rst.md"),
    ],
)
async def test_the_export_of_a_converted_run_names_the_real_file(
    user, marker, real, mirror
):
    run_id = "20261118-100000-threats"
    _threat_run(run_id, "guide/install.rst.md > Login", True)
    text = await _export(user, run_id, marker)
    assert real in text
    assert mirror not in text


async def test_a_run_without_docs_formats_keeps_every_name(user):
    run_id = "20261118-100000-threats"
    _threat_run(run_id, "notes.txt.md > Login", False)
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_see("notes.txt.md")
    text = await _export(user, run_id, "export-markdown")
    assert "notes.txt.md > Login" in text
    other = "20261118-110000-threats"
    _threat_run(other, "notes.txt.md > Login", False)
    text = await _export(user, other, "export-json")
    assert '"page": "notes.txt.md"' in text


async def test_the_requirements_page_keeps_the_name_of_a_run_without_docs_formats(
    user,
):
    from secreqgen_gui import storage

    run_id = "20261118-100000-requirements"
    folder = storage.run_dir(run_id)
    folder.mkdir(parents=True)
    result = {
        "user_story": {"id": "S-1", "title": "T", "story": "As a user."},
        "requirements": [],
        "passages": [{"n": 1, "header": "### [1] notes.txt.md > Login", "text": "T."}],
    }
    data = {
        "results": [result], "failures": [], "model": "m", "tested_prompts": True,
        "stopped": False, "not_run": [],
    }  # fmt: skip
    (folder / "requirements.json").write_text(json.dumps(data), encoding="utf-8")
    manifest = {"run_id": run_id, "kind": "requirements", "per_story": []}
    storage.write_manifest(run_id, manifest)
    await user.open(f"/runs/{run_id}/requirements")
    await user.should_see("notes.txt.md > Login")


# the tool gives the lines that the dialog uses


def test_the_dialog_uses_the_lines_of_the_tool():
    from secreqgen.application.system_context import context_lines
    from secreqgen_gui.pages.context_dialog import grouped_lines

    lines = context_lines(RESULT)
    flat = [line for _place, group in grouped_lines(RESULT) for line in group]
    assert flat == lines
    assert Path(lines[0]["path"]).name == "pyproject.toml"
