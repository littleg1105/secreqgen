"""The dialog that writes a system context, and its job. No model runs."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from urllib.parse import quote

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

pytestmark = pytest.mark.anyio

HINT = (
    "A model wrote the system context of this run. "
    "The thesis tests used files written by a person."
)
YAML_TEXT = "project:\n  name: Demo\n"
RESULT = {
    "draft_notice": "Draft.",
    "folder_name": "demo",
    "model": "m",
    "seconds": 3,
    "calls": [{"attempts": 2, "ok": True, "seconds": 1.0}],
    "facts": {"files_seen": 42, "secret_files_skipped": 2, "languages": {}},
    "context": {
        "name": "Demo",
        "description": "A demo.",
        "tech_stack": [
            {
                "kind": "language",
                "value": "Python",
                "source": "F2",
                "path": "pyproject.toml",
            }
        ],
        "security_profile": "High",
        "features": [
            {
                "group": "messaging",
                "items": [
                    {
                        "text": "Users send messages.",
                        "source": "F1",
                        "path": "README.md",
                    }
                ],
            }
        ],
        "data_sensitivity": {"critical": [], "high": [], "medium": []},
    },
    "dropped": [{"what": "Kafka", "why": "not in the files"}],
    "yaml": YAML_TEXT,
}  # fmt: skip


def make_stand_in(result=None, error=None, gate=None):
    """Return a stand-in for run_system_context. It sends step events."""
    from secreqgen.application.steps import StepEvent

    def run_context(options, *, cfg=None, on_step=None, **_kw):
        def send(step, state, **numbers):
            on_step(StepEvent("demo", step, state, numbers=numbers))

        send("scan", "start")
        send("scan", "done", files=42, parts=7)
        send("write", "start", attempt=1)
        send("write", "done", attempt=1, answered=0)
        send("write", "start", attempt=2)
        if gate is not None:
            gate.wait(10)
        if error is not None:
            raise error
        send("write", "done", attempt=2, answered=1)
        send("check", "done", kept=5, dropped=1)
        return SimpleNamespace(writer="m", result=dict(result or RESULT), output=None)

    return run_context


@pytest.fixture
def job(monkeypatch):
    """Put a job with a stand-in in place of the shared job."""
    from secreqgen_gui import context_job

    def build(**kw):
        fresh = context_job.ContextJob(make_stand_in(**kw))
        monkeypatch.setattr(context_job, "writer", fresh)
        return fresh

    return build


def _open(user, tmp_path):
    folder = tmp_path / "code"
    folder.mkdir(exist_ok=True)
    return folder


async def _open_dialog(user, folder):
    from secreqgen_gui import form_memory

    form_memory.remember("requirements", "context_folder", str(folder))
    await user.open("/requirements/new")
    await user.should_see("System context file (optional)")
    user.find("Write one with the model").click()
    await user.should_see("Write a system context")


def _field(user, marker):
    return user.find(marker=marker).elements.pop()


async def _write_and_wait(user, job_object):
    user.find(marker="context-write").click()
    for _ in range(50):
        if job_object.state()["state"] != "running":
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.2)  # one tick of the timer


# the dialog


async def test_the_button_opens_the_dialog(user, tmp_path, job):
    job()
    folder = _open(user, tmp_path)
    await _open_dialog(user, folder)
    await user.should_see("The model reads the key files of a codebase")
    await user.should_see("You check it before you use it.")
    await user.should_see("Codebase folder")
    await user.should_see("Product name (optional)")
    assert _field(user, "context-folder").value == str(folder)


async def test_the_product_name_of_the_form_is_the_first_value(user, tmp_path, job):
    job()
    folder = _open(user, tmp_path)
    await user.open(f"/requirements/new?product=Acme&docs={quote(str(folder))}")
    user.find("Write one with the model").click()
    await user.should_see("Write a system context")
    assert _field(user, "context-product").value == "Acme"


async def test_write_shows_the_three_lines_and_then_the_result(user, tmp_path, job):
    gate = threading.Event()
    running = job(gate=gate)
    folder = _open(user, tmp_path)
    await _open_dialog(user, folder)
    user.find(marker="context-write").click()
    await asyncio.sleep(1.2)  # the timer of the dialog ticks every 0.5 s
    await user.should_see("Scan the folder")
    await user.should_see("Write the context")
    await user.should_see("Check each line")
    await user.should_see("42 files")
    await user.should_see("try 2 of 3")
    assert not _field(user, "context-write").enabled
    gate.set()
    running.join(10)
    await asyncio.sleep(1.2)
    await user.should_see("2 files that can hold secrets are not read.")
    await user.should_see("5 of 6 passed")
    await user.should_see(
        "This is a draft. Check each line against its source before you use it."
    )
    await user.should_see("Lines and their sources")
    await user.should_see("tech_stack")
    await user.should_see("language: Python")
    await user.should_see("pyproject.toml")
    await user.should_see("features: messaging")
    await user.should_see("Users send messages.")
    await user.should_see("Not in the draft (1)")
    assert _field(user, "context-yaml").value == YAML_TEXT


async def test_no_secret_sentence_when_no_secret_file(user, tmp_path, job):
    data = json.loads(json.dumps(RESULT))
    data["facts"]["secret_files_skipped"] = 0
    running = job(result=data)
    await _open_dialog(user, _open(user, tmp_path))
    await _write_and_wait(user, running)
    await user.should_not_see("can hold secrets")
    await user.should_not_see("The scan stopped")


async def test_save_and_use_writes_the_files_and_fills_the_field(user, tmp_path, job):
    from secreqgen_gui import form_memory, storage

    running = job()
    await _open_dialog(user, _open(user, tmp_path))
    await _write_and_wait(user, running)
    _field(user, "context-yaml").set_value("project:\n  name: Changed\n")
    user.find(marker="context-save").click()
    await user.should_see("Saved.")
    files = sorted(storage.contexts_dir().iterdir(), key=lambda f: f.suffix[::-1])
    names = [f.name for f in files]
    assert len(files) == 2
    assert names[0].startswith("demo-") and names[0].endswith(".yaml")
    assert names[1] == names[0][:-5] + ".json"
    assert files[0].read_text(encoding="utf-8") == "project:\n  name: Changed\n"
    assert json.loads(files[1].read_text(encoding="utf-8"))["folder_name"] == "demo"
    field = [
        e
        for e in user.find(kind=ui.input).elements
        if e.props.get("label") == "System context file (optional)"
    ][0]
    assert field.value.endswith(names[0])
    assert form_memory.recall("requirements", "context_file", "").endswith(names[0])
    assert not any(d.value for d in user.find(kind=ui.dialog).elements)


async def test_yaml_that_does_not_load_is_refused(user, tmp_path, job):
    from secreqgen_gui import storage

    running = job()
    await _open_dialog(user, _open(user, tmp_path))
    await _write_and_wait(user, running)
    for bad in ("a: [unclosed", "just a text line"):
        _field(user, "context-yaml").set_value(bad)
        user.find(marker="context-save").click()
        await user.should_see("This is not valid YAML. Correct it, then save again.")
    assert list(storage.contexts_dir().iterdir()) == []


async def test_an_error_shows_one_line_and_write_works_again(user, tmp_path, job):
    from secreqgen_gui.context_job import ContextJob

    running = job(error=ValueError("boom"))
    await _open_dialog(user, _open(user, tmp_path))
    await _write_and_wait(user, running)
    await user.should_see("Unexpected error. ValueError: boom")
    assert _field(user, "context-write").enabled
    assert isinstance(running, ContextJob)
    assert running.state()["state"] == "failed"


async def test_a_model_server_error_uses_the_run_text(user, tmp_path, job):
    from secreqgen.application.errors import ModelServerError

    running = job(error=ModelServerError("Ollama does not answer.", model="m"))
    await _open_dialog(user, _open(user, tmp_path))
    await _write_and_wait(user, running)
    await user.should_see("Ollama does not answer. Check Ollama at")
    await user.should_see("and the model m.")


async def test_the_dialog_says_when_a_run_is_in_progress(user, tmp_path, job):
    from secreqgen_gui import jobs

    job()
    jobs.manager._state = jobs.RunState(
        id="20261117-100000-threats", kind="threats", state="running", message=""
    )
    await _open_dialog(user, _open(user, tmp_path))
    await asyncio.sleep(1.2)
    await user.should_see("Another run is in progress. Wait for it to end, or stop it.")
    assert not _field(user, "context-write").enabled


# the job and the runs


def test_the_job_refuses_while_a_run_is_in_progress(tmp_path):
    from secreqgen_gui import jobs
    from secreqgen_gui.context_job import ContextJob

    jobs.manager._state = jobs.RunState(
        id="20261117-100000-threats", kind="threats", state="running", message=""
    )
    context = ContextJob(make_stand_in())
    with pytest.raises(RuntimeError, match="Another run is in progress"):
        context.start(tmp_path, None, "m")
    assert not context.busy()


def test_a_run_refuses_while_the_job_works(tmp_path, monkeypatch):
    from secreqgen_gui import context_job
    from secreqgen_gui.jobs import JobManager, ThreatRunSpec

    gate = threading.Event()
    context = context_job.ContextJob(make_stand_in(gate=gate))
    monkeypatch.setattr(context_job, "writer", context)
    context.start(tmp_path, None, "m")
    try:
        assert context.busy()
        with pytest.raises(RuntimeError, match="Another run is in progress"):
            JobManager(check_server=lambda url: None).start_threats(
                ThreatRunSpec(tmp_path / "s.json", tmp_path)
            )
        with pytest.raises(RuntimeError):
            context.start(tmp_path, None, "m")
    finally:
        gate.set()
        context.join(10)
    assert not context.busy()
    assert context.state()["state"] == "done"


def test_a_hostile_product_name_stays_inside_the_contexts_folder():
    from secreqgen_gui import storage

    path = storage.save_context(YAML_TEXT, RESULT, "../../x")
    assert path.parent == storage.contexts_dir()
    assert "/" not in path.name
    assert path.with_suffix(".json").is_file()


def test_the_state_is_a_copy(tmp_path):
    from secreqgen_gui.context_job import ContextJob

    context = ContextJob(make_stand_in())
    context.start(tmp_path, None, "m")
    context.join(10)
    first = context.state()
    first["stages"]["scan"]["state"] = "changed"
    assert context.state()["stages"]["scan"]["state"] == "done"
    assert first["result"]["folder_name"] == "demo"


def test_a_run_with_a_generated_file_gets_the_flag(tmp_path):
    from secreqgen_gui import storage
    from secreqgen_gui.jobs import JobManager, RequirementsRunSpec
    from secreqgen_gui.settings import load_settings

    saved = storage.save_context(YAML_TEXT, RESULT, "demo")
    own = tmp_path / "mine.yaml"
    own.write_text(YAML_TEXT, encoding="utf-8")
    manager = JobManager()
    manager._settings = load_settings()

    def manifest(path):
        spec = RequirementsRunSpec(tmp_path / "s.json", tmp_path, system_context=path)
        run_id = "20261117-100000-requirements"
        return manager._base_manifest(run_id, "requirements", spec)

    assert manifest(str(saved))["generated_context"] is True
    assert "generated_context" not in manifest(str(own))
    assert "generated_context" not in manifest(None)
    saved.with_suffix(".json").unlink()
    assert "generated_context" not in manifest(str(saved))


async def test_the_pages_show_the_generated_context_tag(user):
    from secreqgen_gui import storage

    run_id = "20261117-214500-requirements"
    folder = storage.run_dir(run_id)
    folder.mkdir(parents=True)
    data = {
        "results": [
            {
                "user_story": {"id": "S-1", "title": "T", "story": "As a user."},
                "requirements": [],
                "passages": [],
            }
        ],
        "failures": [], "model": "m", "tested_prompts": True, "stopped": False,
        "not_run": [],
    }  # fmt: skip
    (folder / "requirements.json").write_text(json.dumps(data), encoding="utf-8")
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id, "kind": "requirements", "status": "finished",
            "generated_context": True, "per_story": [],
        },
    )  # fmt: skip
    await user.open(f"/runs/{run_id}")
    await user.should_see("generated context")
    assert HINT in [e.text for e in user.find(kind=ui.tooltip).elements]
    await user.open(f"/runs/{run_id}/requirements")
    await user.should_see("generated context")
    storage.write_manifest(
        run_id,
        {"run_id": run_id, "kind": "requirements", "status": "finished",
         "per_story": []},
    )  # fmt: skip
    await user.open(f"/runs/{run_id}/requirements")
    await user.should_not_see("generated context")


def test_grouped_lines_use_the_lines_of_the_tool():
    from secreqgen_gui.pages.context_dialog import grouped_lines

    result = json.loads(json.dumps(RESULT))
    result["context"]["data_sensitivity"]["high"] = [
        {"text": "Passwords", "source": "F1", "path": "README.md"}
    ]
    groups = grouped_lines(result)
    assert [place for place, _lines in groups] == [
        "tech_stack",
        "features: messaging",
        "data_sensitivity: high",
        "security_profile",
    ]
    assert groups[0][1][0] == {
        "where": "tech_stack",
        "text": "language: Python",
        "path": "pyproject.toml",
    }
    assert groups[3][1][0]["path"] == "Chosen by the model, not taken from a file."
