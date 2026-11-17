"""The run page shows a run in work, a run that ended and a run on disk."""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen_gui import jobs, storage  # noqa: E402
from secreqgen_gui.jobs import RunState, StoryState  # noqa: E402

pytestmark = pytest.mark.anyio


class StandIn:
    """A stand-in for the job manager."""

    def __init__(self, state=None):
        self.state = state
        self.stops = 0

    def get(self, run_id):
        if self.state is not None and self.state.id == run_id:
            return self.state
        return None

    def request_stop(self):
        self.stops += 1


def make_state(state="running", kind="threats", stories=None, message="", **extra):
    stories = stories or [
        StoryState(
            "S-1", "First story", "done", 12.0, "9 rows, 7 accepted, 2 with a fault"
        ),
        StoryState("S-2", "Second story", "running"),
        StoryState("S-3", "Third story"),
    ]
    return RunState(
        id="20261117-100000-" + kind,
        kind=kind,
        state=state,
        message=message,
        stories=stories,
        started=time.time() - 65,
        finished=None if state in ("checking", "running") else time.time(),
        stop_requested=False,
        version=1,
        **extra,
    )


def patch(monkeypatch, state=None):
    stand_in = StandIn(state)
    monkeypatch.setattr(jobs, "manager", stand_in)
    return stand_in


def write_manifest(run_id, **extra):
    manifest = {
        "run_id": run_id,
        "kind": "threats",
        "status": "finished",
        "created_at": "2026-11-17T10:00:00Z",
        "finished_at": "2026-11-17T10:03:05Z",
        "product": "mattermost",
        "writer": "gemma4:26b",
        "checker": "qwen3.8:27b",
        "check": True,
        "story_ids": ["S-1", "S-2"],
        "prompt_set": {"kind": "threats", "name": "mine", "tested": False},
        "per_story": [
            {
                "story_id": "S-1",
                "status": "ok",
                "seconds": 61.0,
                "rows": 5,
                "accepted": 4,
                "dropped": 3,  # rows that the tool code removed: not faults
                "faults": 1,
            },
        ],
        "failures": [],
        "not_run": ["S-2"],
        "error": None,
    }
    manifest.update(extra)
    storage.write_manifest(run_id, manifest)


async def test_state_words_and_head(user, monkeypatch):
    state = make_state()
    patch(monkeypatch, state)
    await user.open(f"/runs/{state.id}")
    await user.should_see("Threat list run")
    await user.should_see("Running: story 2 of 3")
    await user.should_see("Stop after this story")
    await user.should_see("First story")
    await user.should_see("9 rows, 7 accepted, 2 with a fault")
    await user.should_see("1:05")


@pytest.mark.parametrize(
    "state, message, words",
    [
        ("checking", "", "Checking the model server"),
        ("done", "", "Done"),
        ("stopped", "", "Stopped after 1 of 3 stories"),
        ("failed", "The model is missing.", "Failed: The model is missing."),
    ],
)
async def test_each_state(user, monkeypatch, state, message, words):
    value = make_state(state, message=message)
    patch(monkeypatch, value)
    await user.open(f"/runs/{value.id}")
    await user.should_see(words)
    if state == "checking":
        await user.should_see("Stop after this story")
        await user.should_not_see("Start a new run")
    else:
        await user.should_see("Start a new run")
        await user.should_not_see("Stop after this story")


async def test_failed_hint(user, monkeypatch):
    value = make_state("failed", message="Something broke.")
    patch(monkeypatch, value)
    await user.open(f"/runs/{value.id}")
    await user.should_see(
        "Check the model server on the start page, then start the run again."
    )


async def test_read_link_and_story_error(user, monkeypatch):
    stories = [
        StoryState("S-1", "One", "done", 3.0, "2 rows"),
        StoryState("S-2", "Two", "failed", 4.0, error="The model gave no answer."),
    ]
    value = make_state("done", stories=stories)
    patch(monkeypatch, value)
    await user.open(f"/runs/{value.id}")
    await user.should_see("The model gave no answer.")
    links = [e for e in user.find(kind=ui.link).elements if e.text == "Read"]
    assert len(links) == 1
    assert links[0].props["href"] == f"/runs/{value.id}/stories/S-1"


async def test_stop_button(user, monkeypatch):
    value = make_state()
    stand_in = patch(monkeypatch, value)
    await user.open(f"/runs/{value.id}")
    await user.should_see("Stop after this story")
    user.find("Stop after this story").click()
    await user.should_see("The run stops when story S-2 ends.")
    await user.should_not_see("Stop after this story")
    assert stand_in.stops == 1


async def test_requirements_button(user, monkeypatch):
    stories = [StoryState("S-1", "One", "done", 3.0, "5 requirements")]
    value = make_state("done", kind="requirements", stories=stories)
    patch(monkeypatch, value)
    await user.open(f"/runs/{value.id}")
    await user.should_see("Requirements run")
    await user.should_see("Read the requirements")
    await user.should_see("5 requirements")
    links = [e for e in user.find(kind=ui.link).elements if e.text == "Read"]
    assert links == []


async def test_page_follows_the_version(user, monkeypatch):
    value = make_state()
    patch(monkeypatch, value)
    await user.open(f"/runs/{value.id}")
    await user.should_see("Running: story 2 of 3")
    value.stories[1].state = "done"
    value.stories[1].summary = "4 rows"
    value.stories[2].state = "running"
    value.version += 1
    await asyncio.sleep(0.8)
    await user.should_see("Running: story 3 of 3")
    await user.should_see("4 rows")


async def test_run_on_disk(user, monkeypatch):
    patch(monkeypatch, None)
    run_id = "20261117-090000-threats"
    storage.run_dir(run_id).mkdir(parents=True)
    write_manifest(run_id)
    await user.open(f"/runs/{run_id}")
    await user.should_see("Done")
    await user.should_see("mattermost")
    await user.should_see("gemma4:26b writes, qwen3.8:27b checks")
    await user.should_see("Not the tested setup: prompt set mine")
    await user.should_see("5 rows, 4 accepted, 1 with a fault")
    await user.should_see("not run")
    await user.should_see("3:05")
    await user.should_not_see("Stop after this story")
    await user.should_see("Start a new run")


async def test_unknown_and_bad_id(user, monkeypatch):
    patch(monkeypatch, None)
    await user.open("/runs/20261117-090000-threats")
    await user.should_see("This run does not exist.")
    await user.should_see("Open the history")
    await user.open("/runs/..x")
    await user.should_see("This run does not exist.")
    await user.open("/runs/%2E%2E")
    await user.should_see("This run does not exist.")
