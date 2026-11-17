"""The page "How it works": the step diagram and its replay."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen.application.steps import threat_trace  # noqa: E402
from secreqgen_gui import pipeline_boards as boards  # noqa: E402

pytestmark = pytest.mark.anyio

STORY = "MM-US-007"
RUN = "20261117-214500-threats"
PASSAGE = "Review the settings. By enabling this feature, users may phish."


def _row(row_id, source="P1"):
    return {
        "id": row_id,
        "type": "attack",
        "threat": f"Threat {row_id}.",
        "harm": "",
        "story_part": "Webhooks",
        "source": source,
        "quote": "users may phish",
        "requirement": "A requirement.",
        "from_extra_group": False,
        "actor": "trusted_insider",
        "stride": "S",
        "weak_signs": [],
        "second_model": "accepts",
    }


def _model():
    return {
        "draft_notice": "DRAFT for a person to check.",
        "prompt_set": "threats_v5b_labels",
        "product": "mattermost",
        "model": "gemma4:26b",
        "story": {"id": STORY, "title": "Bot setup", "story": "As an admin."},
        "seconds": 42,
        "calls": [
            {"group": 1, "passages": 1, "attempts": 1, "ok": True, "seconds": 3.0}
        ],
        "label_step": True,
        "checker": "qwen3.8:27b",
        "extra_passages": 0,
        "passages": [{"ref": "P1", "header": "a.md > b", "text": PASSAGE, "group": 1}],
        "rows": [_row("T1"), _row("T2")],
        "dropped": [{"group": 1, "why": "a second row for the same quote"}],
    }


def _store(run_id=RUN, status="finished"):
    from secreqgen_gui import storage

    folder = storage.run_dir(run_id) / "stories" / STORY
    folder.mkdir(parents=True)
    (folder / "threats.json").write_text(json.dumps(_model()), encoding="utf-8")
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "kind": "threats",
            "status": status,
            "created_at": "2026-11-17T21:45:00Z",
            "writer": "gemma4:26b",
            "checker": "qwen3.8:27b",
            "check": True,
            "server": "http://localhost:11434",
            "per_story": [{"story_id": STORY, "folder": STORY, "status": "ok"}],
        },
    )
    return run_id


def _frames():
    return boards.replay_frames("threats", threat_trace(_model()))


def _texts(user):
    return [e.text for e in user.find(kind=ui.label).elements]


async def test_no_run_shows_the_steps_with_no_numbers(user):
    await user.open("/how-it-works")
    await user.should_see("How it works")
    await user.should_see("No finished run yet.")
    await user.should_see("New threat list")
    await user.should_see("Step 1 of 8")
    user.find("Next").click()
    await user.should_see("Step 2 of 8")
    await user.should_see("SecReqGen searches the documentation")
    user.find("Previous").click()
    await user.should_see("Step 1 of 8")


async def test_a_stored_story_is_replayed_with_its_numbers(user):
    _store()
    frames = _frames()
    await user.open("/how-it-works")
    await user.should_see(f"Replay of {STORY} from the run of")
    await user.should_see("17 November 2026")
    await user.should_see("The real run took 42 seconds.")
    await user.should_see("Open the result")
    await user.should_see(f"Step 1 of {len(frames)}")
    await user.should_see("Your story and the documentation")
    index = frames.index(("check", 1))
    for _ in range(index):
        user.find("Next").click()
    await user.should_see(f"Step {index + 1} of {len(frames)}")
    await user.should_see("In total 2 passed and 1 failed.")
    user.find("Previous").click()
    await user.should_see(f"Step {index} of {len(frames)}")
    await user.should_see("The writer writes threats")


async def test_frame_parameter_opens_a_frame(user):
    await user.open("/how-it-works?frame=3")
    await user.should_see("Step 3 of 8")
    await user.open("/how-it-works?frame=99")
    await user.should_see("Step 1 of 8")
    await user.open("/how-it-works?frame=abc")
    await user.should_see("Step 1 of 8")


async def test_requirements_kind_has_five_steps(user):
    await user.open("/how-it-works?kind=requirements")
    await user.should_see("Step 1 of 5")
    await user.should_see("New requirements")
    for _ in range(3):
        user.find("Next").click()
    await user.should_see("Step 4 of 5")
    await user.should_see("SecReqGen parses the answer")
    await user.should_not_see("checks each quote")


async def test_story_without_a_result_says_so(user):
    _store()
    await user.open(f"/how-it-works?run={RUN}&story=NOPE-1")
    await user.should_see("This story has no stored result.")
    await user.should_see("Step 1 of 8")
    await user.open("/how-it-works?run=20200101-000000-threats&story=X")
    await user.should_see("This story has no stored result.")


async def test_play_and_pause_change_the_button(user):
    await user.open("/how-it-works")
    user.find("Play").click()
    await user.should_see("Pause")
    user.find("Pause").click()
    await user.should_see("Play")


async def test_parts_section_is_gone_and_rail_entry_shows(user):
    await user.open("/how-it-works")
    await user.should_not_see("The parts")
    await user.should_see("Arrow keys: next and previous step.")
    await user.should_see("Threat list")
    await user.should_see("Requirements")
    await user.should_see("How it works", kind=ui.label, retries=3)
    assert _texts(user).count("How it works") >= 2


async def test_the_link_quotes_a_story_id_with_a_slash(user):
    from secreqgen_gui import storage

    slashed = "MM/US 7"
    run_id = _store()
    manifest = storage.read_manifest(run_id)
    manifest["per_story"][0]["story_id"] = slashed
    storage.write_manifest(run_id, manifest)
    model = _model()
    model["story"]["id"] = slashed
    folder = storage.run_dir(run_id) / "stories" / STORY
    (folder / "threats.json").write_text(json.dumps(model), encoding="utf-8")
    await user.open(f"/how-it-works?run={run_id}&story=MM%2FUS%207")
    await user.should_see("Open the result")
    links = [e for e in user.find(kind=ui.link).elements if e.text == "Open the result"]
    assert links[0].props["href"] == f"/runs/{run_id}/stories/MM%2FUS%207"


async def test_a_requirements_replay_takes_its_time_from_the_manifest(user):
    from secreqgen_gui import storage

    run_id = "20261117-214500-requirements"
    folder = storage.run_dir(run_id)
    folder.mkdir(parents=True)
    result = {
        "user_story": {"id": "MM-US-007", "title": "Bot", "story": "As a user."},
        "requirements": [],
        "passages": [],
    }
    (folder / "requirements.json").write_text(
        json.dumps({"results": [result], "model": "gemma4:26b"}), encoding="utf-8"
    )
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "kind": "requirements",
            "status": "finished",
            "created_at": "2026-11-17T21:45:00Z",
            "model": "gemma4:26b",
            "per_story": [
                {"story_id": "OTHER", "status": "ok", "seconds": 9},
                {"story_id": "MM-US-007", "status": "ok", "seconds": 37.4},
            ],
        },
    )
    await user.open("/how-it-works?kind=requirements")
    await user.should_see("The real run took 37 seconds.")
