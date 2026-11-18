"""The run page and the job manager show the steps of the story in work."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen.application.steps import StepEvent  # noqa: E402
from secreqgen_gui import jobs, pipeline_boards, storage  # noqa: E402
from secreqgen_gui.jobs import (  # noqa: E402
    JobManager,
    RunState,
    StoryState,
    ThreatRunSpec,
)

pytestmark = pytest.mark.anyio

WRITER = "gemma4:26b"
CHECKER = "qwen3.8:27b"
QUOTE = "Users may be able to perform phishing attacks."


def _status():
    infos = tuple(SimpleNamespace(name=n) for n in (WRITER, CHECKER))
    return SimpleNamespace(reachable=True, models=infos, error=None)


@pytest.fixture
def spec(tmp_path):
    stories = [
        {"id": f"S-{n}", "title": f"Story {n}", "story": "As a user, I act."}
        for n in (1, 2)
    ]
    path = tmp_path / "stories.json"
    path.write_text(json.dumps({"user_stories": stories}), encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    return ThreatRunSpec(stories_file=path, docs=docs, product="demo")


def _result():
    return SimpleNamespace(
        lists=[], failures=[], skipped=[], stopped=False, aborted=None, not_run=[]
    )


def _events(story_id):
    numbers = {"passages": 4, "groups": 1, "extra_passages": 0}
    return [
        StepEvent(story_id, "search", "start"),
        StepEvent(story_id, "search", "done", 0, 0, numbers),
        StepEvent(story_id, "write", "start", 1, 1, {"attempt": 1, "passages": 4}),
    ]


# ---- the job manager


def test_step_events_go_into_the_progress(spec):
    seen = {}

    def run(
        options,
        *,
        cfg=None,
        on_progress=None,
        on_story_start=None,
        should_stop=None,
        on_step=None,
    ):
        on_story_start("S-1", 1, 2)
        before = manager.current().version
        for event in _events("S-1"):
            on_step(event)
        seen["grew"] = manager.current().version - before
        seen["steps"] = manager.current().progress["steps"]
        return _result()

    manager = JobManager(run_threats=run, check_server=lambda url: _status())
    run_id = manager.start_threats(spec)
    manager.join(10)
    assert seen["grew"] == 3
    assert seen["steps"]["search"]["numbers"]["passages"] == 4
    assert seen["steps"]["write"]["state"] == "active"
    state = manager.get(run_id)
    assert state.progress["story_id"] == "S-1"
    assert state.state == "done"
    manifest = storage.read_manifest(run_id)
    assert "progress" not in manifest


def test_a_stand_in_without_the_step_callback_still_runs(spec):
    def run(
        options, *, cfg=None, on_progress=None, on_story_start=None, should_stop=None
    ):
        on_story_start("S-1", 1, 2)
        return _result()

    manager = JobManager(run_threats=run, check_server=lambda url: _status())
    run_id = manager.start_threats(spec)
    manager.join(10)
    state = manager.get(run_id)
    assert state.state == "done"
    assert state.progress is not None  # made by on_story_start


def test_a_stand_in_with_kwargs_gets_the_callback(spec):
    got = {}

    def run(options, **kwargs):
        got["on_step"] = kwargs.get("on_step")
        return _result()

    manager = JobManager(run_threats=run, check_server=lambda url: _status())
    manager.start_threats(spec)
    manager.join(10)
    assert callable(got["on_step"])


def test_a_problem_in_the_step_callback_does_not_stop_the_run(spec):
    def run(
        options,
        *,
        cfg=None,
        on_progress=None,
        on_story_start=None,
        should_stop=None,
        on_step=None,
    ):
        on_step(object())  # not an event
        on_step(None)
        on_story_start("S-1", 1, 2)
        on_step("nonsense")
        return _result()

    manager = JobManager(run_threats=run, check_server=lambda url: _status())
    run_id = manager.start_threats(spec)
    manager.join(10)
    assert manager.get(run_id).state == "done"


# ---- the run page


class StandIn:
    def __init__(self, state=None):
        self.state = state

    def get(self, run_id):
        if self.state is not None and self.state.id == run_id:
            return self.state
        return None

    def request_stop(self):
        pass


def _live_state(progress):
    return RunState(
        id="20261117-100000-threats",
        kind="threats",
        state="running",
        message="Running",
        stories=[
            StoryState("S-1", "First story", "done", 12.0, "2 threats"),
            StoryState("S-2", "Second story", "running"),
            StoryState("S-3", "Third story"),
        ],
        started=time.time() - 30,
        version=1,
        progress=progress,
    )


async def test_the_run_page_shows_the_story_in_work(user, monkeypatch):
    progress = pipeline_boards.new_progress("threats", "S-2")
    for event in _events("S-2")[:2]:
        pipeline_boards.apply_event(progress, event)
    event = StepEvent("S-2", "write", "start", 2, 3, {"attempt": 1, "passages": 5})
    pipeline_boards.apply_event(progress, event)
    state = _live_state(progress)
    monkeypatch.setattr(jobs, "manager", StandIn(state))
    await user.open(f"/runs/{state.id}")
    await user.should_see("Steps")
    await user.should_see("Story 2 of 3: S-2 Second story")
    await user.should_see("Write threats")
    await user.should_see("Group 2 of 3.")
    # Only the row of the finished story has a Replay link, not the steps line.
    replays = [e for e in user.find(kind=ui.link).elements if e.text == "Replay"]
    assert len(replays) == 1


async def test_the_run_page_before_the_first_story(user, monkeypatch):
    state = _live_state(None)
    state.state = "checking"
    state.stories = []
    monkeypatch.setattr(jobs, "manager", StandIn(state))
    await user.open(f"/runs/{state.id}")
    await user.should_see("Checking Ollama.")
    await user.should_see("No story is running")


async def test_the_run_page_when_the_run_reads_the_documentation(user, monkeypatch):
    # The check of the model server is done. No story started yet.
    state = _live_state(None)
    monkeypatch.setattr(jobs, "manager", StandIn(state))
    await user.open(f"/runs/{state.id}")
    await user.should_see("Reading the documentation.")
    await user.should_not_see("Checking Ollama.")


def _stored_run(run_id="20261117-090000-threats"):
    folder = storage.run_dir(run_id) / "stories" / "S-1"
    folder.mkdir(parents=True)
    passages = [
        {"ref": f"P{n}", "header": "a.md", "text": "text", "group": 1}
        for n in (1, 2, 3)
    ]
    row = {
        "id": "T1",
        "type": "attack",
        "threat": "Users may phish.",
        "quote": QUOTE,
        "source": "P1",
        "requirement": "A requirement.",
        "second_model": "accepts",
        "weak_signs": [],
    }
    model = {
        "story": {"id": "S-1", "title": "First story"},
        "seconds": 5,
        "calls": [
            {"group": 1, "passages": 3, "attempts": 1, "ok": True, "seconds": 2.0}
        ],
        "passages": passages,
        "rows": [row, {**row, "id": "T2"}],
        "dropped": [{"group": 1, "why": "quote not found"}],
        "checker": CHECKER,
        "model": WRITER,
        "label_step": True,
        "draft_notice": "DRAFT for a person to check.",
    }
    (folder / "threats.json").write_text(json.dumps(model), encoding="utf-8")
    (folder / "threats.md").write_text("# page\n", encoding="utf-8")
    stories = {"user_stories": [{"id": "S-1", "title": "First story"}]}
    (storage.run_dir(run_id) / "stories.json").write_text(
        json.dumps(stories), encoding="utf-8"
    )
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "kind": "threats",
            "status": "finished",
            "created_at": "2026-11-17T09:00:00Z",
            "finished_at": "2026-11-17T09:03:00Z",
            "stories_file": "stories.json",
            "writer": WRITER,
            "checker": CHECKER,
            "check": True,
            "story_ids": ["S-1"],
            "server": "http://localhost:11434",
            "per_story": [
                {
                    "story_id": "S-1",
                    "folder": "S-1",
                    "status": "ok",
                    "seconds": 5.0,
                    "rows": 2,
                    "accepted": 2,
                }
            ],
            "failures": [],
            "not_run": [],
            "error": None,
        },
    )
    return run_id


async def test_a_finished_run_from_disk_shows_the_last_story(user, monkeypatch):
    monkeypatch.setattr(jobs, "manager", StandIn(None))
    run_id = _stored_run()
    await user.open(f"/runs/{run_id}")
    await user.should_see("Steps")
    await user.should_see("Story S-1 First story")
    await user.should_see("Replay")
    address = f"/how-it-works?kind=threats&run={run_id}&story=S-1"
    links = [e for e in user.find(kind=ui.link).elements if e.text == "Replay"]
    assert [e.props["href"] for e in links] == [address, address]
    read = {e.text: e.props["href"] for e in user.find(kind=ui.link).elements}
    assert read["Read"] == f"/runs/{run_id}/stories/S-1"


async def test_a_run_with_no_stored_story_has_no_steps_section(user, monkeypatch):
    monkeypatch.setattr(jobs, "manager", StandIn(None))
    run_id = "20261117-080000-threats"
    storage.run_dir(run_id).mkdir(parents=True)
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "kind": "threats",
            "status": "failed",
            "story_ids": ["S-1"],
            "not_run": ["S-1"],
            "per_story": [],
            "error": {"message": "Ollama is not running at http://localhost:11434."},
        },
    )
    await user.open(f"/runs/{run_id}")
    await user.should_see("Failed:")
    await user.should_not_see("Replay")
    await user.should_not_see("No story is running")


# ---- the threat result page


async def test_the_threat_result_shows_the_numbers_of_the_steps(user):
    run_id = _stored_run()
    await user.open(f"/runs/{run_id}/stories/S-1")
    await user.should_see(
        "3 passages, 3 threats written, 1 failed the quote check (not shown)."
    )
    links = {e.text: e.props["href"] for e in user.find(kind=ui.link).elements}
    address = f"/how-it-works?kind=threats&run={run_id}&story=S-1"
    assert links["Replay"] == address


async def test_a_step_report_does_not_build_the_head_again(user, monkeypatch):
    from secreqgen_gui.pages import run as run_page

    progress = pipeline_boards.new_progress("threats", "S-2")
    event = StepEvent("S-2", "write", "start", 2, 3, {"attempt": 1, "passages": 5})
    pipeline_boards.apply_event(progress, event)
    state = _live_state(progress)
    monkeypatch.setattr(jobs, "manager", StandIn(state))
    heads = []
    original = run_page._head

    def counting_head(view):
        heads.append(view.version)
        original(view)

    monkeypatch.setattr(run_page, "_head", counting_head)
    await user.open(f"/runs/{state.id}")
    await user.should_see("Group 2 of 3.")
    before = len(heads)
    assert before >= 1
    next_event = StepEvent("S-2", "write", "start", 3, 3, {"attempt": 1, "passages": 5})
    pipeline_boards.apply_event(state.progress, next_event)
    state.version = 2
    await user.should_see("Group 3 of 3.", retries=20)
    assert len(heads) == before
    # A change of the story list builds the head again.
    state.version = 3
    state.story_version = 1
    state.message = "Running again"
    await user.should_see("Group 3 of 3.")
    for _ in range(20):
        if len(heads) > before:
            break
        await asyncio.sleep(0.2)
    assert len(heads) > before
