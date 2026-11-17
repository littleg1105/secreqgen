"""The start page and the settings page."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

pytestmark = pytest.mark.anyio

TESTED = ("gemma4:26b", "qwen3.8:27b")


def stand_in(models=TESTED, reachable=True):
    """Return a stand-in for check_model_server."""

    def check(*_args, **_kwargs):
        infos = tuple(SimpleNamespace(name=m) for m in models)
        return SimpleNamespace(
            base_url="x", reachable=reachable, models=infos, error=None, seconds=0.0
        )

    return check


def patch_check(monkeypatch, check):
    from secreqgen_gui.pages import home, settings

    monkeypatch.setattr(home, "check_model_server", check)
    monkeypatch.setattr(settings, "check_model_server", check)


def write_run(run_id="20261117-143012-threats", **extra):
    from secreqgen_gui import storage

    manifest = {
        "kind": "threats",
        "status": "finished",
        "created_at": "2026-11-17T14:30:12Z",
        "product": "mattermost",
        "stories_total": 8,
    }
    manifest.update(extra)
    storage.write_manifest(run_id, manifest)


async def test_home_shows_the_three_parts(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    await user.open("/")
    for text in (
        "Model server",
        "Start",
        "Newest runs",
        "Start a threat list",
        "Start requirements",
        "A draft list of threats for each story.",
        "Security requirements for each story, from the documentation.",
        "Check again",
    ):
        await user.should_see(text)


async def test_home_lines_say_ready(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    await user.open("/")
    await user.should_see("gemma4:26b: ready")
    await user.should_see("qwen3.8:27b: ready")
    await user.should_see("2 models.")
    await user.should_see("The model server answers at http://localhost:11434.")


async def test_home_server_down_names_the_address(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=(), reachable=False))
    await user.open("/")
    await user.should_see(
        "The model server does not answer at http://localhost:11434. "
        "Start Ollama, then choose Check again."
    )


async def test_home_missing_model_gives_the_pull_command(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=("gemma4:26b",)))
    await user.open("/")
    await user.should_see("gemma4:26b: ready")
    await user.should_see("qwen3.8:27b is missing. Run: ollama pull qwen3.8:27b")


async def test_check_again_runs_the_check_again(user, monkeypatch):
    calls = []
    inner = stand_in()

    def check(*args, **kwargs):
        calls.append(1)
        return inner()

    patch_check(monkeypatch, check)
    await user.open("/")
    await user.should_see("gemma4:26b: ready")
    user.find("Check again").click()
    await asyncio.sleep(0.5)
    await user.should_see("gemma4:26b: ready")
    assert len(calls) == 2


async def test_newest_runs_show_a_stored_run(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    write_run()
    await user.open("/")
    await user.should_see("Threat list")
    await user.should_see("mattermost")
    await user.should_see("8 stories")
    await user.should_see("Done")
    await user.should_see("All runs")
    await user.should_not_see("No runs yet.")


async def test_newest_runs_list_only_five(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    for day in range(1, 8):
        write_run(f"2026110{day}-100000-threats", product=f"product{day}")
    await user.open("/")
    await user.should_see("product7")
    await user.should_see("product3")
    await user.should_not_see("product2")


async def test_no_run_shows_the_empty_state(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    await user.open("/")
    await user.should_see("No runs yet.")
    await user.should_see("Start a threat list")


async def test_run_in_work_shows_one_line(user, monkeypatch, quiet_manager):
    from secreqgen_gui.jobs import RunState, StoryState

    patch_check(monkeypatch, stand_in())
    state = RunState(
        id="20261117-143012-threats",
        kind="threats",
        state="running",
        message="",
        stories=[
            StoryState("A", "a", state="done"),
            StoryState("B", "b", state="running"),
            StoryState("C", "c"),
        ],
        started=0.0,
        finished=None,
        stop_requested=False,
        version=1,
    )
    monkeypatch.setattr(quiet_manager, "_state", state)
    await user.open("/")
    await user.should_see("A run is in work: threat list, story 2 of 3.")
    await user.should_see("Open the run")


async def test_settings_show_the_parts(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    for text in (
        "Model server",
        "Models",
        "Data folder",
        "About",
        "Check the model server",
        "Save settings",
        "Uploads, runs and your prompt sets are in this folder.",
        "The threat list is a draft for a person to check.",
        "The tested setup: gemma4:26b writes the rows and qwen3.8:27b checks them.",
        "(ingest, evaluate) are on the command line only.",
    ):
        await user.should_see(text, retries=3) if False else await user.should_see(text)


async def test_settings_check_shows_lines(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=("gemma4:26b",)))
    await user.open("/settings")
    user.find("Check the model server").click()
    await user.should_see(
        "The model server answers at http://localhost:11434. 1 model."
    )
    await user.should_see("qwen3.8:27b is missing. Run: ollama pull qwen3.8:27b")


async def test_settings_check_down_names_the_address(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=(), reachable=False))
    await user.open("/settings")
    user.find(marker="address").elements.pop().set_value("http://nowhere:1")
    user.find("Check the model server").click()
    await user.should_see("The model server does not answer at http://nowhere:1.")
    await user.should_see("choose Check again")


async def test_save_writes_the_file(user, monkeypatch, gui_home):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    user.find(marker="address").elements.pop().set_value("http://box:11434")
    user.find(marker="writer").elements.pop().set_options(
        ["my-model:7b"], value="my-model:7b"
    )
    user.find("Save settings").click()
    await user.should_see("Settings saved")
    data = json.loads((gui_home / "settings.json").read_text(encoding="utf-8"))
    assert data["base_url"] == "http://box:11434"
    assert data["writer_model"] == "my-model:7b"
    assert data["checker_model"] == "qwen3.8:27b"


async def test_empty_address_is_refused(user, monkeypatch, gui_home):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    user.find(marker="address").elements.pop().set_value("  ")
    user.find("Save settings").click()
    await user.should_see("Give the address of the model server.")
    assert not (gui_home / "settings.json").exists()


async def test_empty_model_name_is_refused(user, monkeypatch, gui_home):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    user.find(marker="checker").elements.pop().set_value(None)
    user.find("Save settings").click()
    await user.should_see("Give the name of the second model.")
    assert not (gui_home / "settings.json").exists()
