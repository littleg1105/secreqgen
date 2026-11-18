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
    await user.should_see("Prompts")
    await user.should_see("Copy a set to change it.")
    await user.should_see("Threat list")
    await user.should_see("Requirements")
    await user.should_see("Tested prompts")
    await user.should_see("tested")
    for info in tested_prompt_sets():
        for file in info.files:
            await user.should_see(file.name)
            await user.should_see(file.sha256[:16])


async def test_copy_to_edit_makes_folder_and_opens_set(user):
    await user.open("/prompts")
    user.find(marker="copy-threats").click()
    await user.should_see("Name of the new set")
    await user.should_see(
        "Lowercase letters, digits, hyphens, underscores. Max 40 characters."
    )
    user.find(marker="copy-name").type("my_set")
    user.find(marker="copy-go").click()
    await user.should_see("Save")
    assert (_root() / "threats" / "my_set" / "user_prompt.txt").is_file()
    await user.should_see("Run tag: untested prompts")


THREAT_WORDS = {
    "system_prompt.txt": "Role and rules of the writer",
    "user_prompt.txt": "Writer task: threats with quotes",
    "labels_prompt.txt": "Writer task: actor and STRIDE",
    "check_prompt.txt": "Checker task: accept or flag",
}
REQUIREMENT_WORDS = {
    "system_prompt.txt": "Role of the model",
    "rag_user_prompt.txt": "Task with passages",
    "zero_shot_user_prompt.txt": "Task without passages",
}


async def test_each_tested_file_row_shows_its_description(user):
    from secreqgen.application.prompt_sets import tested_prompt_sets

    await user.open("/prompts")
    for info in tested_prompt_sets():
        words = THREAT_WORDS if info.kind == "threats" else REQUIREMENT_WORDS
        for file in info.files:
            await user.should_see(words[file.name])


async def test_file_row_of_a_set_of_the_person_shows_its_description(user):
    _make("threats", "my_set")
    _make("requirements", "r_set")
    await user.open("/prompts")
    for words in (THREAT_WORDS, REQUIREMENT_WORDS):
        for description in words.values():
            await user.should_see(description)


def test_a_file_with_another_name_has_no_description():
    from secreqgen_gui.pages import prompts

    assert prompts.FILE_WORDS["threats"] == THREAT_WORDS
    assert prompts.FILE_WORDS["requirements"] == REQUIREMENT_WORDS
    assert prompts.FILE_WORDS["threats"].get("other_prompt.txt", "") == ""
    assert prompts.FILE_WORDS.get("unknown", {}).get("system_prompt.txt", "") == ""


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
    await user.should_see("Locked. Used in the thesis tests.")
    await user.should_see("Placeholders: $accep")
    await user.should_not_see("Save")


async def test_unknown_set_invites(user):
    await user.open("/prompts/threats/nothing")
    await user.should_see("This prompt set does not exist.")
    await user.open("/prompts/bogus/tested")
    await user.should_see("Open Prompts")


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
    await user.should_see("Saved.")
    assert path.read_text(encoding="utf-8") == "A new system text."


async def test_delete_removes_folder(user):
    folder = _make()
    await user.open("/prompts")
    await user.should_see("untested")
    user.find(marker="delete-threats-my_set").click()
    await user.should_see("Delete the prompt set my_set?")
    user.find(kind=ui.button, content="Delete").click()
    await user.should_see("Prompt set deleted.")
    assert not folder.exists()


async def test_delete_from_set_page(user):
    folder = _make("requirements", "r1")
    await user.open("/prompts/requirements/r1")
    user.find(marker="delete-set").click()
    user.find(kind=ui.button, content="Delete").click()
    await user.should_see("Prompts")
    assert not folder.exists()
    assert ui is not None
