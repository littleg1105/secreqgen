"""The forms keep their values when the person changes the page."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen_gui import form_memory  # noqa: E402


def _stories(tmp_path, count=2, name="stories.json"):
    items = [
        {
            "id": f"US-00{i}",
            "title": f"Title {i}",
            "story": f"As a user, I want thing {i}.",
            "acceptance_criteria": ["One"],
        }
        for i in range(1, count + 1)
    ]
    path = tmp_path / name
    path.write_text(json.dumps({"project": "Demo", "user_stories": items}))
    return path


def _docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir(exist_ok=True)
    (folder / "page.md").write_text("# Page\nText")
    return folder


def _url(path, stories=None, docs=None):
    parts = []
    if stories is not None:
        parts.append("stories=" + quote(str(stories)))
    if docs is not None:
        parts.append("docs=" + quote(str(docs)))
    return path + ("?" + "&".join(parts) if parts else "")


def _button(user, label):
    found = [
        e for e in user.find(kind=ui.button).elements if getattr(e, "text", "") == label
    ]
    assert found, f"no button {label}"
    return found[0]


def _inputs(user):
    return {e.value for e in user.find(kind=ui.input).elements}


def _check(user, label):
    return [
        e for e in user.find(kind=ui.checkbox).elements if label in e.text
    ][0]


def _switch(user, label):
    return [e for e in user.find(kind=ui.switch).elements if e.text == label][0]


def test_memory_basics(gui_home):
    assert form_memory.recall("common", "docs") is None
    assert form_memory.recall("common", "docs", "x") == "x"
    form_memory.remember("common", "docs", "~/docs")
    form_memory.remember("threats", "check", False)
    assert form_memory.recall("common", "docs") == "~/docs"
    assert form_memory.recall("threats", "check", True) is False
    form_memory.remember("common", "docs", "")
    assert form_memory.recall("common", "docs") is None
    form_memory.clear()
    assert form_memory.recall("threats", "check") is None


def test_broken_file_gives_an_empty_memory(gui_home):
    form_memory.remember("common", "docs", "a")
    (gui_home / "forms.json").write_text("{ broken")
    assert form_memory.recall("common", "docs") is None
    (gui_home / "forms.json").write_text("[1, 2]")
    assert form_memory.recall("common", "docs") is None
    form_memory.remember("common", "docs", "b")
    assert form_memory.recall("common", "docs") == "b"


def test_equal_value_writes_nothing(gui_home, monkeypatch):
    form_memory.remember("common", "docs", "a")
    calls = []
    monkeypatch.setattr(form_memory, "_save", lambda data: calls.append(data))
    form_memory.remember("common", "docs", "a")
    form_memory.remember("common", "missing", None)
    assert calls == []
    form_memory.remember("common", "docs", "b")
    assert len(calls) == 1


def test_no_permission_does_not_break(gui_home, monkeypatch):
    def refuse(*_a, **_k):
        raise PermissionError("no")

    monkeypatch.setattr(form_memory.os, "replace", refuse)
    form_memory.remember("common", "docs", "a")  # no exception
    assert (json.loads((gui_home / "forms.json").read_text()) if (
        gui_home / "forms.json"
    ).exists() else {}) == {}


@pytest.mark.anyio
async def test_common_values_follow_the_person(user, tmp_path):
    stories = _stories(tmp_path)
    docs = _docs(tmp_path)
    await user.open("/threats/new")
    ins = {e.label: e for e in user.find(kind=ui.input).elements}
    ins["Stories file"].set_value(str(stories))
    ins["Documentation folder"].set_value(str(docs))
    ins["Product name (optional)"].set_value("Acme")
    await user.should_see("2 stories found.")
    await user.open("/requirements/new")
    await user.should_see("2 stories found.")
    values = _inputs(user)
    assert {str(stories), str(docs), "Acme"} <= values
    await user.open("/threats/new")
    assert {str(stories), str(docs), "Acme"} <= _inputs(user)


@pytest.mark.anyio
async def test_form_options_stay_in_their_form(user, tmp_path):
    await user.open("/threats/new")
    about = [e for e in user.find(kind=ui.textarea).elements
             if e.label == "About the product (optional)"][0]
    about.set_value("A tool for tests.")
    _switch(user, "Use a checker").set_value(False)
    await user.open("/requirements/new")
    await user.should_see("New requirements")
    assert "A tool for tests." not in {
        e.value for e in user.find(kind=ui.textarea).elements
    }
    # only the threat form has this switch
    assert "Use a checker" not in {e.text for e in user.find(kind=ui.switch).elements}
    await user.open("/threats/new")
    about = [e for e in user.find(kind=ui.textarea).elements
             if e.label == "About the product (optional)"][0]
    assert about.value == "A tool for tests."
    assert _switch(user, "Use a checker").value is False


@pytest.mark.anyio
async def test_query_wins_over_memory(user, tmp_path):
    form_memory.remember("common", "product", "Old")
    form_memory.remember("common", "docs", "~/old")
    await user.open("/threats/new?product=New")
    values = _inputs(user)
    assert "New" in values and "Old" not in values
    assert "~/old" in values  # no query for the docs: the memory is used
    assert form_memory.recall("common", "product") == "New"


@pytest.mark.anyio
async def test_chosen_stories_are_kept(user, tmp_path):
    stories, docs = _stories(tmp_path, 3), _docs(tmp_path)
    await user.open(_url("/threats/new", stories, docs))
    await user.should_see("3 stories found.")
    _check(user, "US-002").set_value(False)
    await user.should_see("2 stories, about 2 minutes")
    await user.open("/requirements/new")
    await user.should_see("3 stories found.")
    assert _check(user, "US-001").value is True
    assert _check(user, "US-002").value is False
    assert _check(user, "US-003").value is True
    # a different file forgets the chosen stories
    other = _stories(tmp_path, 3, name="other.json")
    ins = {e.label: e for e in user.find(kind=ui.input).elements}
    ins["Stories file"].set_value(str(other))
    await user.should_see("3 stories, about 3 minutes")
    assert form_memory.recall("common", "story_ids") is None
    await user.open("/threats/new")
    await user.should_see("3 stories found.")
    assert _check(user, "US-002").value is True


@pytest.mark.anyio
async def test_pick_none_is_kept(user, tmp_path):
    await user.open(_url("/threats/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("2 stories found.")
    user.find("Pick none").click()
    await user.should_see("0 stories, about 0 minutes")
    await user.open("/requirements/new")
    await user.should_see("0 stories, about 0 minutes")
    assert not _button(user, "Start requirements").enabled


@pytest.mark.anyio
async def test_stale_ids_are_dropped(user, tmp_path):
    stories = _stories(tmp_path, 2)
    form_memory.remember("common", "stories_file", str(stories))
    form_memory.remember("common", "story_ids", ["US-009"])
    await user.open(_url("/threats/new", None, _docs(tmp_path)))
    await user.should_see("2 stories found.")
    assert _check(user, "US-001").value and _check(user, "US-002").value


@pytest.mark.anyio
async def test_tab_and_typed_story_are_kept(user):
    await user.open("/threats/new")
    story = [e for e in user.find(kind=ui.textarea).elements
             if e.label == "Story"][0]
    story.set_value("As a user, I want x.")
    tabs = user.find(kind=ui.tabs).elements.pop()
    tabs.set_value("type")
    await user.open("/requirements/new")
    story = [e for e in user.find(kind=ui.textarea).elements
             if e.label == "Story"][0]
    assert story.value == "As a user, I want x."
    assert user.find(kind=ui.tabs).elements.pop().value == "type"


@pytest.mark.anyio
async def test_clear_the_form_gives_the_defaults(user, tmp_path):
    stories, docs = _stories(tmp_path), _docs(tmp_path)
    await user.open(_url("/threats/new", stories, docs))
    await user.should_see("2 stories found.")
    _switch(user, "Use a checker").set_value(False)
    ins = {e.label: e for e in user.find(kind=ui.input).elements}
    ins["Product name (optional)"].set_value("Acme")
    assert form_memory.recall("threats", "check") is False
    user.find("Clear the form").click()
    await user.should_not_see("2 stories found.")
    assert form_memory.recall("common", "docs") is None
    assert form_memory.recall("threats", "check") is None
    values = _inputs(user)
    assert str(docs) not in values and "Acme" not in values
    assert _switch(user, "Use a checker").value is True
    await user.open("/requirements/new")
    assert "Acme" not in _inputs(user)


@pytest.mark.anyio
async def test_clean_folder_gives_todays_defaults(user):
    await user.open("/threats/new")
    values = _inputs(user)
    assert {"gemma4:26b", "qwen3.8:27b", "US-001"} <= values
    assert _switch(user, "Use a checker").value is True
