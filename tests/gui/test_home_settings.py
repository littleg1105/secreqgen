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
        "Recent runs",
        "New threat list",
        "New requirements",
        "Draft threats, each with a quote",
        "Security requirements with ASVS category",
        "Threat lists and security requirements from your user stories.",
        "Check again",
    ):
        await user.should_see(text)
    await user.should_not_see("Model server")


async def test_home_shows_one_status_row_with_all_models(user, monkeypatch):
    models = (*TESTED, "llama3:8b")
    patch_check(monkeypatch, stand_in(models=models))
    await user.open("/")
    await user.should_see("Ollama OK")
    await user.should_see("Models: gemma4:26b, qwen3.8:27b, llama3:8b")
    await user.should_not_see("is ready.")
    await user.should_not_see("Not in Ollama")
    await user.should_not_see("Ollama is running.")


async def test_home_with_no_model_says_none(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=()))
    await user.open("/")
    await user.should_see("Ollama OK")
    await user.should_see("Models: none")


async def test_home_server_down_names_the_address(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=(), reachable=False))
    await user.open("/")
    await user.should_see(
        "Ollama is not running at http://localhost:11434. "
        "Start Ollama, then check again."
    )


async def test_home_missing_checker_names_the_role(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=("gemma4:26b",)))
    await user.open("/")
    await user.should_see("Models: gemma4:26b")
    await user.should_see(
        "Not in Ollama: qwen3.8:27b (checker). Run: ollama pull qwen3.8:27b"
    )
    await user.should_not_see("Not in Ollama: gemma4:26b")


async def test_home_model_with_two_roles_gets_both_roles(user, monkeypatch, gui_home):
    from secreqgen_gui.settings import GuiSettings, save_settings

    save_settings(
        GuiSettings(
            writer_model="gemma4:26b",
            checker_model="qwen3.8:27b",
            requirements_model="gemma4:26b",
        )
    )
    patch_check(monkeypatch, stand_in(models=("qwen3.8:27b",)))
    await user.open("/")
    await user.should_see(
        "Not in Ollama: gemma4:26b (writer, requirements). "
        "Run: ollama pull gemma4:26b"
    )
    await user.should_not_see("Not in Ollama: qwen3.8:27b")


async def test_check_again_runs_the_check_again(user, monkeypatch):
    calls = []
    inner = stand_in()

    def check(*args, **kwargs):
        calls.append(1)
        return inner()

    patch_check(monkeypatch, check)
    await user.open("/")
    await user.should_see("Ollama OK")
    user.find("Check again").click()
    await asyncio.sleep(0.5)
    await user.should_see("Ollama OK")
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


async def test_run_in_progress_shows_one_line(user, monkeypatch, quiet_manager):
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
    await user.should_see("A threat list run is in progress.")
    await user.should_see("Open the run")


async def test_settings_show_the_parts(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    for text in (
        "Ollama",
        "Models",
        "Data folder",
        "About",
        "Check Ollama",
        "Save",
        "SecReqGen 0.1.0",
    ):
        await user.should_see(text)


async def test_settings_have_no_sentence_under_data_folder_and_about(user, monkeypatch):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    for old in (
        "Runs, uploads and your prompts are stored here.",
        "SECREQGEN_GUI_HOME",
        "The threat list is a draft, not a complete threat model.",
        "Tested setup:",
        "The ingest and evaluate commands are on the command line only.",
    ):
        await user.should_not_see(old)


async def test_settings_show_the_two_tested_models(user, monkeypatch):
    from secreqgen.threats import TESTED_WRITER
    from secreqgen.threats.pipeline import TESTED_CHECKER

    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    await user.should_see(f"Tested: {TESTED_WRITER}")
    await user.should_see(f"Tested: {TESTED_CHECKER}")
    assert (TESTED_WRITER, TESTED_CHECKER) == TESTED


async def test_settings_check_shows_lines(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=("gemma4:26b",)))
    await user.open("/settings")
    user.find("Check Ollama").click()
    await user.should_see("Ollama OK")
    await user.should_see("Models: gemma4:26b")
    await user.should_see(
        "Not in Ollama: qwen3.8:27b (checker). Run: ollama pull qwen3.8:27b"
    )


async def test_settings_check_with_all_models_shows_one_row(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=(*TESTED, "llama3:8b")))
    await user.open("/settings")
    user.find("Check Ollama").click()
    await user.should_see("Ollama OK")
    await user.should_see("Models: gemma4:26b, qwen3.8:27b, llama3:8b")
    await user.should_not_see("Not in Ollama")


async def test_settings_check_down_names_the_address(user, monkeypatch):
    patch_check(monkeypatch, stand_in(models=(), reachable=False))
    await user.open("/settings")
    user.find(marker="address").elements.pop().set_value("http://nowhere:1")
    user.find("Check Ollama").click()
    await user.should_see("Ollama is not running at http://nowhere:1.")
    await user.should_see("then check again.")


async def test_save_writes_the_file(user, monkeypatch, gui_home):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    user.find(marker="address").elements.pop().set_value("http://box:11434")
    user.find(marker="writer").elements.pop().set_options(
        ["my-model:7b"], value="my-model:7b"
    )
    user.find("Save").click()
    await user.should_see("Saved.")
    data = json.loads((gui_home / "settings.json").read_text(encoding="utf-8"))
    assert data["base_url"] == "http://box:11434"
    assert data["writer_model"] == "my-model:7b"
    assert data["checker_model"] == "qwen3.8:27b"


async def test_empty_address_is_refused(user, monkeypatch, gui_home):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    user.find(marker="address").elements.pop().set_value("  ")
    user.find("Save").click()
    await user.should_see("Enter the address of Ollama.")
    assert not (gui_home / "settings.json").exists()


async def test_empty_model_name_is_refused(user, monkeypatch, gui_home):
    patch_check(monkeypatch, stand_in())
    await user.open("/settings")
    user.find(marker="checker").elements.pop().set_value(None)
    user.find("Save").click()
    await user.should_see("Enter the checker model.")
    assert not (gui_home / "settings.json").exists()
