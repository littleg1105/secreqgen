"""The requirements result page and the history page."""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("nicegui")

pytestmark = pytest.mark.anyio

RUN = "20261117-214500-requirements"
OLD = "20261116-100000-threats"
EVIL = "<script>alert(1)</script> must not run"


def _req(number: int, description: str = "The system must log it.") -> dict:
    return {
        "id": f"SR-00{number}",
        "user_story_id": "MM-US-007",
        "title": f"Identify webhook posts {number}",
        "description": description,
        "rationale": "The documentation says so (passage 1).",
        "acceptance_criteria": ["Posts carry a BOT tag."],
        "asvs_category": "V11: Business Logic",
        "priority": "high",
        "implementation_notes": "Use a flag.",
        "source_references": ["configure_integrations.md"],
    }


def _result(story_id: str, requirements: list[dict]) -> dict:
    return {
        "user_story": {"id": story_id, "title": "Bot setup", "story": "As a user."},
        "requirements": requirements,
        "passages": [
            {"n": 1, "header": "### [1] a.md > Bots", "text": "Passage text here."}
        ],
    }


def _write_requirements(description: str = "The system must log it.") -> None:
    from secreqgen_gui import storage

    folder = storage.run_dir(RUN)
    folder.mkdir(parents=True)
    data = {
        "results": [
            _result("MM-US-007", [_req(1, description), _req(2)]),
            _result("MM-US-008", [_req(3)]),
        ],
        "failures": [{"story_id": "MM-US-009", "error": {"message": "Model broke."}}],
        "model": "gemma4:26b",
        "tested_prompts": True,
        "stopped": False,
        "not_run": [],
    }
    (folder / "requirements.json").write_text(json.dumps(data), encoding="utf-8")
    storage.write_manifest(
        RUN,
        {
            "run_id": RUN,
            "kind": "requirements",
            "status": "finished",
            "created_at": "2026-11-17T21:45:00Z",
            "product": "mattermost",
            "model": "gemma4:26b",
            "stories_total": 3,
            "per_story": [{"story_id": "MM-US-007", "status": "ok", "requirements": 3}],
        },
    )


async def test_requirements_show_headers_fields_and_passages(user):
    from nicegui import ui

    _write_requirements()
    await user.open(f"/runs/{RUN}/requirements")
    await user.should_see("Requirements")
    await user.should_see("3 requirements for 2 stories.")
    await user.should_see("mattermost")
    await user.should_see("Model: gemma4:26b")
    await user.should_see("MM-US-007")
    found = user.find(kind=ui.expansion).elements
    captions = [e.props.get("caption") or "" for e in found]
    assert sorted(c for c in captions if c) == ["1 requirement", "2 requirements"]
    await user.should_see("Identify webhook posts 1")
    await user.should_see("Priority: high")
    await user.should_see("V11: Business Logic")
    await user.should_see("Why:")
    await user.should_see("Acceptance criteria")
    await user.should_see("Posts carry a BOT tag.")
    await user.should_see("Sources")
    await user.should_see("Implementation notes")
    await user.should_see("Documentation that the model read (1 passage)")
    await user.should_see("Passage text here.")
    await user.should_see("Stories with no result")
    await user.should_see("Model broke.")
    await user.should_not_see("Not the tested setup")


async def test_script_text_is_shown_as_text(user):
    from nicegui import ui

    _write_requirements(EVIL)
    await user.open(f"/runs/{RUN}/requirements")
    await user.should_see(EVIL)
    labels = [e for e in user.find(kind=ui.label).elements if EVIL in e.text]
    assert labels
    await user.should_not_see(kind=ui.html)


async def test_the_two_downloads_exist(user):
    _write_requirements()
    await user.open(f"/runs/{RUN}/requirements")
    user.find("Download requirements.json").click()
    first = await user.download.next()
    assert json.loads(first.text)["model"] == "gemma4:26b"
    user.find("Download as Markdown").click()
    second = await user.download.next()
    assert "## MM-US-007 Bot setup" in second.text
    assert "### SR-001 Identify webhook posts 1" in second.text
    assert "- Priority: high" in second.text
    assert "Acceptance criteria:" in second.text


async def test_unknown_run_shows_the_empty_state(user):
    await user.open("/runs/20260101-000000-requirements/requirements")
    await user.should_see("This run does not exist.")
    await user.should_see("Open the history")


async def test_bad_run_id_shows_the_empty_state(user):
    await user.open("/runs/bad_id/requirements")
    await user.should_see("This run does not exist.")


async def test_run_without_requirements(user):
    from secreqgen_gui import storage

    storage.run_dir(RUN).mkdir(parents=True)
    storage.write_manifest(RUN, {"run_id": RUN, "kind": "requirements"})
    await user.open(f"/runs/{RUN}/requirements")
    await user.should_see("This run has no requirements.")
    await user.should_see("Open the run")


def _write_threat_run() -> None:
    from secreqgen_gui import storage

    storage.run_dir(OLD).mkdir(parents=True)
    storage.write_manifest(
        OLD,
        {
            "run_id": OLD,
            "kind": "threats",
            "status": "finished",
            "created_at": "2026-11-16T10:00:00Z",
            "product": "keycloak",
            "writer": "gemma4:26b",
            "checker": "qwen3.8:27b",
            "stories_total": 2,
            "per_story": [
                {"story_id": "A", "status": "ok", "rows": 9, "accepted": 7},
                {"story_id": "B", "status": "ok", "rows": 7, "accepted": 4},
            ],
        },
    )


@pytest.fixture
def athens_time(monkeypatch):
    """Give the test a known local time zone (UTC+2 in November)."""
    import time

    monkeypatch.setenv("TZ", "Europe/Athens")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


async def test_history_lists_runs_newest_first(user, athens_time):
    from nicegui import ui

    _write_threat_run()
    _write_requirements()
    await user.open("/history")
    await user.should_see("History")
    await user.should_see("All runs in the data folder.")
    table = user.find(kind=ui.table).elements.pop()
    assert [r["id"] for r in table.rows] == [RUN, OLD]
    # The manifest holds UTC. The page shows the local time.
    assert table.rows[0]["date"] == "17 November 2026, 23:45"
    assert table.rows[1]["date"] == "16 November 2026, 12:00"
    assert table.rows[1]["result"] == "16 rows, 11 accepted"
    assert table.rows[1]["models"] == "gemma4:26b, qwen3.8:27b"
    assert table.rows[1]["kind"] == "Threat list"
    assert table.rows[0]["result"] == "3 requirements"
    assert table.rows[0]["state"] == "Done"


async def test_row_click_opens_the_run(user):
    from nicegui import ui

    _write_threat_run()
    await user.open("/history")
    user.find(kind=ui.table).trigger("rowClick", args=[{}, {"id": OLD}, 0])
    await asyncio.sleep(0.3)
    assert user.back_history[-1] == f"/runs/{OLD}"


async def test_delete_run_asks_then_removes(user):
    from nicegui import ui
    from secreqgen_gui import storage

    _write_threat_run()
    _write_requirements()
    await user.open("/history")
    table = user.find(kind=ui.table).elements.pop()
    user.find(kind=ui.table).trigger("delete", args={"id": OLD, "busy": False})
    await user.should_see("Delete this run and its files?")
    assert storage.run_dir(OLD).exists()
    user.find("Delete run").click()
    await user.should_see("Run deleted")
    assert not storage.run_dir(OLD).exists()
    assert storage.run_dir(RUN).exists()
    await asyncio.sleep(0.3)
    table = user.find(kind=ui.table).elements.pop()
    assert [r["id"] for r in table.rows] == [RUN]


async def test_run_in_work_cannot_be_deleted(user):
    from nicegui import ui
    from secreqgen_gui import storage

    storage.run_dir(OLD).mkdir(parents=True)
    storage.write_manifest(OLD, {"kind": "threats", "status": "running"})
    await user.open("/history")
    table = user.find(kind=ui.table).elements.pop()
    assert table.rows[0]["busy"] is True
    user.find(kind=ui.table).trigger("delete", args=table.rows[0])
    await user.should_not_see("Delete this run and its files?")
    assert storage.run_dir(OLD).exists()


async def test_history_without_runs_shows_the_empty_state(user):
    await user.open("/history")
    await user.should_see("No runs yet.")
    await user.should_see("Start a threat list")
