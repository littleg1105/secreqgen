"""The prompt set pages: the list, the tested set and the editor."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

pytestmark = pytest.mark.anyio


def _root():
    from secreqgen_gui import storage

    return storage.prompt_sets_dir()


def _make(kind: str = "threats", name: str = "my_set"):
    from secreqgen.application.prompt_sets import copy_prompt_set

    return copy_prompt_set(kind, name, _root())


async def test_list_shows_tested_sets_with_files_and_hashes(user):
    from secreqgen.application.prompt_sets import tested_prompt_sets

    await user.open("/prompts")
    await user.should_see("Prompt sets")
    await user.should_see("Threat list")
    await user.should_see("Requirements")
    await user.should_see("Tested setup")
    await user.should_see("tested")
    for info in tested_prompt_sets():
        for file in info.files:
            await user.should_see(file.name)
            await user.should_see(file.sha256[:16])


async def test_copy_to_edit_makes_folder_and_opens_set(user):
    await user.open("/prompts")
    user.find(marker="copy-threats").click()
    await user.should_see("Name of your prompt set")
    await user.should_see("Small letters, digits, hyphen and underscore.")
    user.find(marker="copy-name").type("my_set")
    user.find(marker="copy-go").click()
    await user.should_see("Save file")
    assert (_root() / "threats" / "my_set" / "user_prompt.txt").is_file()
    await user.should_see("A run with this prompt set gets the label")


@pytest.mark.parametrize("bad", ["my set", "../x"])
async def test_copy_with_bad_name_shows_error(user, bad):
    await user.open("/prompts")
    user.find(marker="copy-threats").click()
    user.find(marker="copy-name").type(bad)
    user.find(marker="copy-go").click()
    await user.should_see("Bad name of a prompt set")
    assert not (_root() / "threats").exists()


async def test_tested_page_is_read_only(user):
    await user.open("/prompts/threats/tested")
    await user.should_see("Read only. This is the setup that the tests of")
    await user.should_see("Placeholders: $accep")
    await user.should_not_see("Save file")


async def test_unknown_set_invites(user):
    await user.open("/prompts/threats/nothing")
    await user.should_see("This prompt set does not exist.")
    await user.open("/prompts/bogus/tested")
    await user.should_see("Open the prompt sets")


async def test_save_with_removed_placeholder_is_refused(user):
    folder = _make()
    path = folder / "user_prompt.txt"
    before = path.read_bytes()
    await user.open("/prompts/threats/my_set")
    editor = user.find(marker="editor-user_prompt.txt")
    editor.elements.pop().set_value("no placeholders here")
    user.find(marker="save-user_prompt.txt").click()
    await user.should_see("The placeholder $passages is missing.")
    assert path.read_bytes() == before


async def test_good_save_changes_file(user):
    folder = _make()
    path = folder / "system_prompt.txt"
    await user.open("/prompts/threats/my_set")
    editor = user.find(marker="editor-system_prompt.txt").elements.pop()
    editor.set_value("A new system text.")
    user.find(marker="save-system_prompt.txt").click()
    await user.should_see("File saved")
    assert path.read_text(encoding="utf-8") == "A new system text."


async def test_delete_removes_folder(user):
    folder = _make()
    await user.open("/prompts")
    await user.should_see("not tested")
    user.find(marker="delete-threats-my_set").click()
    await user.should_see("Delete the prompt set my_set?")
    user.find("Delete prompt set").click()
    await user.should_see("Prompt set deleted")
    assert not folder.exists()


async def test_delete_from_set_page(user):
    folder = _make("requirements", "r1")
    await user.open("/prompts/requirements/r1")
    user.find(marker="delete-set").click()
    user.find("Delete prompt set").click()
    await user.should_see("Prompt sets")
    assert not folder.exists()
    assert ui is not None
