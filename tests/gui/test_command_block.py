"""The command of a run: the block under the forms, in the dialog and on the run page.

No model runs. The job manager is a stand-in that records the spec.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import shlex
from pathlib import Path

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen_gui import jobs, storage  # noqa: E402
from secreqgen_gui.settings import GuiSettings, save_settings  # noqa: E402

pytestmark = pytest.mark.anyio

ABOUT_TEXT = "Demo is a chat tool for teams."


class Recorder:
    """A stand-in for the job manager."""

    def __init__(self):
        self.threats = []
        self.requirements = []

    def busy(self):
        return False

    def current(self):
        return None

    def get(self, run_id):
        return None

    def start_threats(self, spec):
        self.threats.append(spec)
        return "20261117-100000-threats"

    def start_requirements(self, spec):
        self.requirements.append(spec)
        return "20261117-100000-requirements"


@pytest.fixture
def recorder(monkeypatch):
    fake = Recorder()
    monkeypatch.setattr(jobs, "manager", fake)
    return fake


def _stories(tmp_path, count=8, name="stories.json", project="Demo"):
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
    path.write_text(json.dumps({"project": project, "user_stories": items}))
    return path


def _docs(tmp_path, name="docs"):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "page.md").write_text("# Page\nText")
    return folder


def _url(route, stories, docs):
    from urllib.parse import quote

    return f"{route}?stories={quote(str(stories))}&docs={quote(str(docs))}"


async def _open(user, tmp_path, route="/threats/new", count=8):
    stories = _stories(tmp_path, count)
    docs = _docs(tmp_path)
    await user.open(_url(route, stories, docs))
    await user.should_see(f"{count} stories found.")
    await user.should_see("1 Markdown file found.")
    await asyncio.sleep(0.3)
    return stories, docs


def _shown(element) -> bool:
    """True when the element and all its parents are visible."""
    while element is not None:
        if not element.visible:
            return False
        parent = getattr(element, "parent_slot", None)
        element = parent.parent if parent is not None else None
    return True


def _found(user, marker):
    """Return the elements with a marker, in the order of their making."""
    try:
        elements = user.find(marker=marker).elements
    except AssertionError:  # the simulated user fails when it finds nothing
        return []
    return sorted(elements, key=lambda e: e.id)


def _texts(user):
    """Return the texts of the command blocks that a person can see."""
    return [e.text for e in _found(user, "command-text") if _shown(e)]


def _command(user) -> str:
    """Return the one command that the page shows."""
    texts = _texts(user)
    assert len(texts) == 1, texts
    return texts[0]


def _notes(user):
    """Return the lines under the block. There are none any more."""
    return [e.text for e in _found(user, "command-note")]


def _parse(argv):
    """Give the arguments of a command to the real parser. Return its values."""
    import secreqgen.cli
    import typer.main

    root = typer.main.get_command(secreqgen.cli.app)
    words = list(argv)
    start = words.index("secreqgen")  # variables may stand in front
    name = words[start + 2]
    sub = root.commands["generate"].commands[name]
    values = dict(sub.make_context(name, words[start + 3 :]).params)
    if values.get("prompts_dir") is not None:
        values["prompts_dir"] = Path(values["prompts_dir"])
    return values


def _parsed(text):
    """Return the values that the command line reads from the text of a command."""
    return _parse(shlex.split(text))


def _make_set(kind, name="mine"):
    """Copy the tested prompts into a set of the person. Return its folder."""
    from secreqgen.application.prompt_sets import copy_prompt_set

    return copy_prompt_set(kind, name, storage.prompt_sets_dir())


def _input(user, label):
    found = [
        e for e in user.find(kind=ui.input).elements if e.props.get("label") == label
    ]
    assert found, f"no input {label}"
    return found[0]


def _area(user):
    return user.find(kind=ui.textarea, content="About the product (optional)")


async def _settle(seconds=0.3):
    await asyncio.sleep(seconds)


# the form shows the block only when it is complete


async def test_the_block_is_hidden_on_an_empty_form_and_shows_on_a_complete_one(
    user, tmp_path, recorder
):
    await user.open("/threats/new")
    await user.should_see("New threat list")
    assert _texts(user) == []
    await user.open("/requirements/new")
    await user.should_see("New requirements")
    assert _texts(user) == []
    stories, docs = await _open(user, tmp_path)
    text = _command(user)
    assert text.startswith("secreqgen generate threats ")
    assert str(stories) in text and "--docs" in text and str(docs) in text
    assert _found(user, "command-block")[0].visible
    await user.should_see("Copy")


async def test_a_form_that_is_not_complete_hides_the_block_again(
    user, tmp_path, recorder
):
    await _open(user, tmp_path)
    assert len(_texts(user)) == 1
    user.find("Pick none").click()
    await _settle()
    assert _texts(user) == []
    user.find("Pick all").click()
    await _settle()
    assert len(_texts(user)) == 1


# the text changes with each input


async def test_the_stories_file_changes_the_command(user, tmp_path, recorder):
    stories, _docs_folder = await _open(user, tmp_path)
    other = _stories(tmp_path, 3, name="other.json")
    _input(user, "Stories file").set_value(str(other))
    await user.should_see("3 stories found.")
    await _settle(0.5)
    text = _command(user)
    assert str(other) in text and str(stories) not in text


async def test_the_picked_stories_change_the_command(user, tmp_path, recorder):
    await _open(user, tmp_path)
    user.find("Pick none").click()
    boxes = user.find(kind=ui.checkbox).elements
    next(b for b in boxes if "US-003" in b.text).set_value(True)
    await _settle()
    assert "--story-id US-003" in _command(user)
    next(b for b in boxes if "US-004" in b.text).set_value(True)
    await _settle()
    # The command names each picked story.
    assert "--story-id US-003 --story-id US-004" in _command(user)
    assert _parsed(_command(user))["story_id"] == ("US-003", "US-004")


async def test_the_docs_folder_changes_the_command(user, tmp_path, recorder):
    _stories_file, docs = await _open(user, tmp_path)
    other = _docs(tmp_path, "docs2")
    _input(user, "Documentation folder").set_value(str(other))
    await user.should_see("1 Markdown file found.")
    await _settle(0.6)
    text = _command(user)
    assert str(other) in text and f"{docs} " not in text + " "


async def test_the_switch_for_other_file_types_changes_the_command(
    user, tmp_path, recorder
):
    await _open(user, tmp_path)
    assert "--other-formats" in _command(user)  # the form starts with it on
    user.find("Read other file types too").click()
    await _settle()
    assert "--other-formats" not in _command(user)


async def test_the_product_name_changes_the_command(user, tmp_path, recorder):
    await _open(user, tmp_path)
    assert "--product" not in _command(user)  # the name of the file is the default
    product = _input(user, "Product name (optional)")
    product.set_value("Acme Chat")
    await _settle()
    assert "--product 'Acme Chat'" in _command(user)
    product.set_value("")
    await _settle()
    assert "--product" not in _command(user)


async def test_the_models_and_the_checker_switch_change_the_command(
    user, tmp_path, recorder
):
    await _open(user, tmp_path)
    # The models are always in the command, also the tested ones.
    text = _command(user)
    assert "--model gemma4:26b" in text and "--checker-model qwen3.8:27b" in text
    _input(user, "Writer").set_value("llama3:8b")
    await _settle()
    assert "--model llama3:8b" in _command(user)
    _input(user, "Checker").set_value("mistral:7b")
    await _settle()
    assert "--checker-model mistral:7b" in _command(user)
    user.find("Use a checker").click()
    await _settle()
    text = _command(user)
    assert "--no-check" in text and "--checker-model" not in text


async def test_the_labels_switch_changes_the_command(user, tmp_path, recorder):
    await _open(user, tmp_path)
    assert "--no-labels" not in _command(user)
    user.find("Add a threat actor and a STRIDE category").click()
    await _settle()
    assert "--no-labels" in _command(user)


async def test_the_paragraph_changes_the_command(user, tmp_path, recorder):
    await _open(user, tmp_path)
    assert "--about-file" not in _command(user)
    _area(user).type(ABOUT_TEXT)
    await _settle(0.8)
    text = _command(user)
    assert f"--about-file {storage.about_path(ABOUT_TEXT)}" in text
    _area(user).clear()
    await _settle(0.8)
    assert "--about-file" not in _command(user)


async def test_the_prompt_set_changes_the_command(user, tmp_path, recorder):
    folder = _make_set("threats")
    await _open(user, tmp_path)
    assert "--prompts-dir" not in _command(user)
    user.find(kind=ui.select).click()
    user.find("mine").click()
    await _settle()
    # The folder is the one that the run itself gets.
    assert _parsed(_command(user))["prompts_dir"] == folder
    assert f"--prompts-dir {folder}" in _command(user)
    assert _notes(user) == []


async def test_the_context_file_changes_the_requirements_command(
    user, tmp_path, recorder
):
    await _open(user, tmp_path, route="/requirements/new")
    assert "--context" not in _command(user)
    context = tmp_path / "context.yaml"
    context.write_text("project:\n  name: Demo\n")
    _input(user, "System context file (optional)").set_value(str(context))
    await _settle(0.6)
    assert f"--context {context}" in _command(user)
    _input(user, "System context file (optional)").set_value(str(tmp_path / "no.yaml"))
    await _settle(0.6)
    assert _texts(user) == []  # the form is not complete


async def test_the_requirements_model_changes_the_command(user, tmp_path, recorder):
    await _open(user, tmp_path, route="/requirements/new")
    # The model is always in the command, as an option. No variable.
    text = _command(user)
    assert text.startswith("secreqgen generate requirements ")
    assert _parsed(text)["model"] == "gemma4:26b"
    _input(user, "Model").set_value("llama3:8b")  # the default of the tool
    await _settle()
    assert _parsed(_command(user))["model"] == "llama3:8b"
    _input(user, "Model").set_value("mistral:7b")
    await _settle()
    text = _command(user)
    assert "SECREQGEN_LLM_MODEL_NAME" not in text
    assert _parsed(text)["model"] == "mistral:7b"


async def test_the_settings_of_the_gui_count(user, tmp_path, recorder):
    save_settings(
        GuiSettings(
            base_url="http://10.0.0.5:11434",
            writer_model="llama3:8b",
            checker_model="qwen3.8:27b",
            requirements_model="mistral:7b",
        )
    )
    await _open(user, tmp_path)
    text = _command(user)
    assert text.startswith("SECREQGEN_LLM_BASE_URL=http://10.0.0.5:11434 secreqgen")
    assert "--model llama3:8b" in text
    stories = _stories(tmp_path, 2, name="again.json")
    docs = tmp_path / "docs"
    await user.open(_url("/requirements/new", stories, docs))
    await user.should_see("2 stories found.")
    await _settle()
    text = _command(user)
    assert "SECREQGEN_LLM_BASE_URL=http://10.0.0.5:11434" in text
    assert "SECREQGEN_LLM_MODEL_NAME" not in text
    assert _parsed(text)["model"] == "mistral:7b"


# the text of the block is the command of the spec of the click


async def test_the_block_equals_the_command_of_the_threat_spec(
    user, tmp_path, recorder
):
    stories, docs = await _open(user, tmp_path)
    _input(user, "Writer").set_value("llama3:8b")
    _input(user, "Product name (optional)").set_value("Acme")
    _area(user).type(ABOUT_TEXT)
    boxes = user.find(kind=ui.checkbox).elements
    next(b for b in boxes if "US-002" in b.text).set_value(False)
    await _settle(0.9)
    shown = _command(user)
    assert _notes(user) == []
    assert shown.count("--story-id") == 7 and "--story-id US-002" not in shown
    user.find("Start threat list").click()
    await _settle()
    spec = recorder.threats[0]
    expected = jobs.command_for_threats(
        spec,
        total_stories=8,
        default_product="Demo",
        about_file=storage.about_path(ABOUT_TEXT),
        base_url=jobs.load_settings().base_url,
    )
    assert shown == expected.text
    assert shlex.split(shown)[:3] == ["secreqgen", "generate", "threats"]
    assert "--model llama3:8b" in shown
    assert spec.stories_file == stories and spec.docs == docs


async def test_the_block_equals_the_command_of_the_requirements_spec(
    user, tmp_path, recorder
):
    await _open(user, tmp_path, route="/requirements/new")
    _input(user, "Model").set_value("llama3:8b")
    context = tmp_path / "ctx.yaml"
    context.write_text("project:\n  name: Demo\n")
    _input(user, "System context file (optional)").set_value(str(context))
    await _settle(0.6)
    shown = _command(user)
    user.find("Start requirements").click()
    await _settle()
    spec = recorder.requirements[0]
    expected = jobs.command_for_requirements(
        spec,
        total_stories=8,
        default_product="Demo",
        base_url=jobs.load_settings().base_url,
    )
    assert shown == expected.text
    assert spec.model == "llama3:8b" and spec.system_context == str(context)
    assert f"--context {context}" in shown


async def _fill_random_form(user, rng, count=8):
    """Fill a form with random picks. Return the picked ids and the prompt set."""
    user.find("Pick none").click()
    boxes = {
        b.text.split()[0]: b
        for b in user.find(kind=ui.checkbox).elements
        if b.text.startswith("US-")
    }
    ids = sorted(boxes)
    picked = sorted(rng.sample(ids, rng.randint(1, len(ids))))
    for story_id in picked:
        boxes[story_id].set_value(True)
    return picked


def _set_select(user, value):
    user.find(kind=ui.select).elements.pop().set_value(value)


async def test_the_block_equals_the_command_of_the_spec_for_random_threat_forms(
    user, tmp_path, recorder
):
    rng = random.Random(20261118)
    sets = {
        "mine": _make_set("threats", "mine"),
        "other-set": _make_set("threats", "other-set"),
    }
    seen: set = set()
    for turn in range(8):
        work = tmp_path / f"turn{turn}"
        work.mkdir()
        await _open(user, work)
        picked = await _fill_random_form(user, rng)
        writer, checker = rng.sample(
            ["gemma4:26b", "llama3:8b", "mistral:7b", "qwen3.8:27b", "phi4:14b"], 2
        )
        _input(user, "Writer").set_value(writer)
        # The form keeps its values from the turn before: set the switch.
        use_checker = rng.random() >= 0.3
        user.find("Use a checker").elements.pop().set_value(use_checker)
        if use_checker:
            _input(user, "Checker").set_value(checker)
        else:
            checker = None
        product = rng.choice(["", "Acme Chat"])
        _input(user, "Product name (optional)").set_value(product)
        chosen = rng.choice([None, "mine", "other-set"])
        _set_select(user, chosen or "__tested__")
        await _settle(0.5)
        shown = _command(user)
        assert _notes(user) == []
        user.find("Start threat list").click()
        await _settle()
        spec = recorder.threats[-1]
        expected = jobs.command_for_threats(
            spec,
            total_stories=8,
            default_product="Demo",
            about_file=None,
            base_url=jobs.load_settings().base_url,
        )
        assert shown == expected.text, turn
        # The command, read by the real parser, holds what the form held.
        parsed = _parsed(shown)
        assert set(parsed["story_id"]) == (set(picked) if len(picked) < 8 else set())
        assert parsed["writer_model"] == writer
        assert parsed["checker_model"] == checker
        assert parsed["check"] is (checker is not None)
        assert parsed["prompts_dir"] == (sets[chosen] if chosen else None)
        assert parsed["product"] == (product or None)
        assert spec.prompt_set == chosen
        assert set(spec.story_ids or ()) == (set(picked) if len(picked) < 8 else set())
        seen.update({("set", chosen), ("checker", checker is None)})
    # The seed must reach each kind of form: a set of the person, the tested
    # prompts, a form with the checker and a form without it.
    assert {
        ("set", None),
        ("set", "mine"),
        ("checker", True),
        ("checker", False),
    } <= seen


async def test_the_block_equals_the_command_of_the_spec_for_random_requirements_forms(
    user, tmp_path, recorder
):
    rng = random.Random(20261119)
    sets = {
        "mine": _make_set("requirements", "mine"),
        "other-set": _make_set("requirements", "other-set"),
    }
    seen: set = set()
    for turn in range(8):
        work = tmp_path / f"turn{turn}"
        work.mkdir()
        await _open(user, work, route="/requirements/new")
        picked = await _fill_random_form(user, rng)
        model = rng.choice(["gemma4:26b", "llama3:8b", "mistral:7b", "qwen3:30b"])
        _input(user, "Model").set_value(model)
        chosen = rng.choice([None, "mine", "other-set"])
        _set_select(user, chosen or "__tested__")
        await _settle(0.5)
        shown = _command(user)
        assert _notes(user) == []
        user.find("Start requirements").click()
        await _settle()
        spec = recorder.requirements[-1]
        expected = jobs.command_for_requirements(
            spec,
            total_stories=8,
            default_product="Demo",
            base_url=jobs.load_settings().base_url,
        )
        assert shown == expected.text, turn
        parsed = _parsed(shown)
        assert set(parsed["story_id"]) == (set(picked) if len(picked) < 8 else set())
        assert parsed["model"] == model
        assert parsed["prompts_dir"] == (sets[chosen] if chosen else None)
        assert "SECREQGEN_LLM_MODEL_NAME" not in shown
        assert spec.model == model and spec.prompt_set == chosen
        seen.add(("set", chosen))
    assert {("set", None), ("set", "mine")} <= seen


# the picked stories, the prompt set and the models are in the command


async def test_two_of_eight_stories_give_two_story_options_and_no_line(
    user, tmp_path, recorder
):
    await _open(user, tmp_path)
    assert "--story-id" not in _command(user)  # all stories: no option
    user.find("Pick none").click()
    boxes = user.find(kind=ui.checkbox).elements
    for number in ("US-002", "US-007"):
        next(b for b in boxes if number in b.text).set_value(True)
    await _settle()
    text = _command(user)
    assert text.count("--story-id") == 2
    assert _parsed(text)["story_id"] == ("US-002", "US-007")
    assert _notes(user) == []
    # One story: one option.
    next(b for b in boxes if "US-007" in b.text).set_value(False)
    await _settle()
    assert _parsed(_command(user))["story_id"] == ("US-002",)
    assert _notes(user) == []


async def test_the_requirements_form_names_the_picked_stories_too(
    user, tmp_path, recorder
):
    await _open(user, tmp_path, route="/requirements/new")
    user.find("Pick none").click()
    boxes = user.find(kind=ui.checkbox).elements
    for number in ("US-005", "US-001"):
        next(b for b in boxes if number in b.text).set_value(True)
    await _settle()
    text = _command(user)
    assert text.count("--story-id") == 2
    # The command line takes the stories in the order of the file.
    assert sorted(_parsed(text)["story_id"]) == ["US-001", "US-005"]
    assert _notes(user) == []


async def test_a_prompt_set_of_the_person_names_its_folder(user, tmp_path, recorder):
    for route, kind in (
        ("/threats/new", "threats"),
        ("/requirements/new", "requirements"),
    ):
        folder = _make_set(kind, "mine-" + kind)
        stories = _stories(tmp_path, 3, name=kind + ".json")
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "page.md").write_text("# Page\nText")
        await user.open(_url(route, stories, docs))
        await user.should_see("3 stories found.")
        await _settle()
        assert "--prompts-dir" not in _command(user)
        user.find(kind=ui.select).click()
        user.find("mine-" + kind).click()
        await _settle()
        text = _command(user)
        assert _parsed(text)["prompts_dir"] == folder
        assert shlex.split(text)[shlex.split(text).index("--prompts-dir") + 1] == str(
            folder
        )
        assert _notes(user) == []


async def test_no_page_has_a_line_under_the_block(user, tmp_path, recorder):
    _make_set("threats")
    _run_folder(THREAT_ID, "threats", story_ids=["S-1"])
    await _open(user, tmp_path)
    assert _found(user, "command-note") == []
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Command")
    assert _found(user, "command-note") == []


async def test_the_block_always_names_the_models(user, tmp_path, recorder):
    await _open(user, tmp_path)
    parsed = _parsed(_command(user))
    assert parsed["writer_model"] == "gemma4:26b"
    assert parsed["checker_model"] == "qwen3.8:27b"
    user.find("Use a checker").click()
    await _settle()
    parsed = _parsed(_command(user))
    assert parsed["writer_model"] == "gemma4:26b" and parsed["check"] is False


# the paragraph file


async def test_the_paragraph_file_is_written_and_removed(user, tmp_path, recorder):
    await _open(user, tmp_path)
    folded = "Demo is a chat tool."
    path = storage.about_path(folded)
    assert not path.exists()
    _area(user).type("  Demo is a chat   tool.  ")
    await _settle(0.8)
    assert path.read_text(encoding="utf-8") == folded + "\n"
    assert f"--about-file {path}" in _command(user)
    _area(user).clear()
    await _settle(0.8)
    assert not path.exists()


async def test_a_remembered_paragraph_is_written_when_the_form_opens(
    user, tmp_path, recorder
):
    from secreqgen_gui import form_memory

    form_memory.remember("threats", "about", ABOUT_TEXT)
    await _open(user, tmp_path)
    await _settle(0.8)
    path = storage.about_path(ABOUT_TEXT)
    assert path.read_text(encoding="utf-8") == ABOUT_TEXT + "\n"
    assert f"--about-file {path}" in _command(user)


async def test_the_block_waits_for_the_file_of_the_paragraph(user, tmp_path, recorder):
    # A moment after a change, the file of the new text is not there yet. A
    # command that names it would not be true, so the block is hidden.
    await _open(user, tmp_path)
    _area(user).type(ABOUT_TEXT)
    await _settle(0.1)
    assert _texts(user) == []
    await _settle(0.8)
    assert f"--about-file {storage.about_path(ABOUT_TEXT)}" in _command(user)


async def test_a_paragraph_file_that_cannot_be_written_gives_no_command(
    user, tmp_path, recorder, monkeypatch
):
    await _open(user, tmp_path)

    def broken(text, old=None):
        raise OSError("the disk is full")

    monkeypatch.setattr(storage, "swap_about", broken)
    _area(user).type(ABOUT_TEXT)
    await _settle(0.8)
    assert _texts(user) == []
    # Start still works: the run gets the text, not the file.
    user.find("Start threat list").click()
    await _settle()
    assert recorder.threats[0].about == ABOUT_TEXT


async def test_two_pages_have_two_paragraph_files(user, tmp_path, recorder):
    # Each text has its own file. So the command of a page stays true when a
    # second page of the form has a different paragraph.
    stories, docs = await _open(user, tmp_path)
    area_one = _area(user).elements.pop()
    area_one.set_value("The paragraph of page one.")
    await _settle(0.8)
    blocks_one = _found(user, "command-text")
    path_one = storage.about_path("The paragraph of page one.")
    assert f"--about-file {path_one}" in blocks_one[0].text
    await user.open(_url("/threats/new", stories, docs))
    await user.should_see("8 stories found.")
    await _settle(0.8)
    area_two = [e for e in _area(user).elements if e is not area_one][-1]
    area_two.set_value("The paragraph of page two.")
    await _settle(0.8)
    path_two = storage.about_path("The paragraph of page two.")
    assert path_one.read_text("utf-8") == "The paragraph of page one.\n"
    assert path_two.read_text("utf-8") == "The paragraph of page two.\n"
    assert f"--about-file {path_one}" in blocks_one[0].text


def test_each_paragraph_has_its_own_file(tmp_path):
    one = storage.swap_about("The first text.")
    two = storage.swap_about("The second text.")
    assert one != two
    assert one.read_text("utf-8") == "The first text.\n"
    assert two.read_text("utf-8") == "The second text.\n"
    # A form lets go of its file when its paragraph changes.
    three = storage.swap_about("A third text.", one)
    assert not one.exists() and two.exists() and three.exists()
    assert storage.swap_about(None, three) is None
    assert not three.exists()


def test_a_paragraph_file_stays_while_another_form_uses_it(tmp_path):
    first = storage.swap_about("The same text.")  # page 1
    second = storage.swap_about("The same text.")  # page 2
    assert first == second
    storage.swap_about("Another text.", first)  # page 1 changes its paragraph
    assert second.exists()  # the command of page 2 still names it
    storage.swap_about(None, second)  # page 2 clears its paragraph
    assert not second.exists()


def test_the_start_of_the_gui_removes_the_paragraph_files(tmp_path):
    path = storage.swap_about("A text of a form that was closed.")
    other = path.with_name("notes.txt")
    other.write_text("not a paragraph file")
    storage.clear_about()
    assert not path.exists()
    assert other.exists()  # only the files that the GUI made


async def test_settings_saved_on_another_page_change_the_command(
    user, tmp_path, recorder
):
    await _open(user, tmp_path)
    # The default address: no variable in front.
    assert _command(user).startswith("secreqgen ")
    changed = dataclasses.replace(
        jobs.load_settings(), base_url="http://10.1.1.1:11434"
    )
    save_settings(changed)  # what the page Settings does in another tab
    await _settle(1.3)  # the foot looks two times a second
    assert _command(user).startswith("SECREQGEN_LLM_BASE_URL=http://10.1.1.1:11434 ")


# the dialog


async def test_the_dialog_shows_the_command_for_its_fields(user, tmp_path, recorder):
    folder = tmp_path / "code"
    folder.mkdir()
    await user.open("/requirements/new")
    user.find("Write one with the model").click()
    await user.should_see("Write a system context")
    assert _found(user, "context-command") == []  # no folder yet: hidden
    folder_field = _found(user, "context-folder")[0]
    folder_field.set_value(str(folder))
    await _settle()
    boxes = _found(user, "context-command")
    assert len(boxes) == 1
    block = [
        e.text
        for e in boxes[0].descendants()
        if isinstance(e, ui.label) and e.text.startswith("secreqgen ")
    ]
    assert shlex.split(block[0]) == [
        "secreqgen",
        "generate",
        "context",
        str(folder),
        "--model",
        "gemma4:26b",
    ]
    _found(user, "context-product")[0].set_value("Acme Chat")
    await _settle()
    block = [
        e.text
        for e in boxes[0].descendants()
        if isinstance(e, ui.label) and e.text.startswith("secreqgen ")
    ]
    assert len(block) == 1 and "--product 'Acme Chat'" in block[0]
    parsed = _parsed(block[0])
    assert parsed["product"] == "Acme Chat" and parsed["writer_model"] == "gemma4:26b"
    folder_field.set_value("")
    await _settle()
    assert _found(user, "context-command") == []


async def test_the_dialog_command_holds_an_absolute_folder(
    user, tmp_path, recorder, monkeypatch
):
    # The GUI reads a relative folder from its own working folder. A terminal
    # can be in another folder, so the command names the whole path.
    (tmp_path / "code").mkdir()
    monkeypatch.chdir(tmp_path)
    await user.open("/requirements/new")
    user.find("Write one with the model").click()
    await user.should_see("Write a system context")
    _found(user, "context-folder")[0].set_value("code")
    await _settle()
    boxes = _found(user, "context-command")
    assert len(boxes) == 1
    block = [
        e.text
        for e in boxes[0].descendants()
        if isinstance(e, ui.label) and e.text.startswith("secreqgen ")
    ]
    assert shlex.split(block[0])[3] == str(tmp_path / "code")


# Copy


async def test_copy_gives_the_text_to_the_clipboard_and_says_so(
    user, tmp_path, recorder, monkeypatch
):
    calls = []
    monkeypatch.setattr(ui.clipboard, "write", lambda text: calls.append(text))
    await _open(user, tmp_path)
    text = _command(user)
    user.find(marker="command-copy").click()
    await user.should_see("Copied.")
    assert calls == [text]


# the run page


def _run_folder(run_id, kind, stories=None, **manifest):
    """Write a stored run: its folder, its copy of the stories and its manifest."""
    folder = storage.run_dir(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    items = stories or [
        {"id": f"S-{n}", "title": f"Story {n}", "story": "As a user."}
        for n in (1, 2, 3)
    ]
    (folder / "stories.json").write_text(
        json.dumps({"project": "demo", "user_stories": items})
    )
    data = {
        "run_id": run_id,
        "kind": kind,
        "status": "finished",
        "created_at": "2026-11-17T10:00:00Z",
        "finished_at": "2026-11-17T10:03:05Z",
        "product": "demo",
        "docs": "/data/docs",
        "stories_file": "stories.json",
        "story_ids": [],
        "stories_total": len(items),
        "prompt_set": {"kind": kind, "name": "tested", "tested": True},
        "server": "http://localhost:11434",
        "per_story": [],
        "failures": [],
        "not_run": [],
        "error": None,
    }
    if kind == "threats":
        data.update(
            writer="gemma4:26b", checker="qwen3.8:27b", check=True, about_words=0
        )
    else:
        data.update(model="gemma4:26b", system_context=None)
    data.update(manifest)
    storage.write_manifest(run_id, data)
    return folder


def _drop(run_id, *keys):
    """Take keys out of a stored manifest: an older GUI did not write them."""
    manifest = storage.read_manifest(run_id)
    for key in keys:
        manifest.pop(key, None)
    storage.write_manifest(run_id, manifest)


THREAT_ID = "20261117-100000-threats"
REQ_ID = "20261117-110000-requirements"


async def test_the_run_page_shows_the_command_of_a_threat_run(user, tmp_path, recorder):
    folder = _run_folder(THREAT_ID, "threats", about=ABOUT_TEXT, about_words=7)
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Threat list run")
    await user.should_see("Command")
    text = _command(user)
    assert shlex.split(text) == [
        "secreqgen",
        "generate",
        "threats",
        str(folder / "stories.json"),
        "--docs",
        "/data/docs",
        "--model",
        "gemma4:26b",
        "--checker-model",
        "qwen3.8:27b",
        "--about-file",
        str(folder / "about.txt"),
    ]
    assert (folder / "about.txt").read_text(encoding="utf-8") == ABOUT_TEXT + "\n"
    assert _notes(user) == []


async def test_the_part_is_under_the_head_and_above_the_steps(user, tmp_path, recorder):
    _run_folder(THREAT_ID, "threats")
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Command")
    labels = [
        e.text
        for e in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)
        if e.text in ("Threat list run", "Command", "Steps") and _shown(e)
    ]
    assert labels.index("Threat list run") < labels.index("Command")


async def test_the_run_page_shows_the_command_of_a_requirements_run(
    user, tmp_path, recorder
):
    folder = _run_folder(
        REQ_ID,
        "requirements",
        model="mistral:7b",
        system_context="/data/ctx.yaml",
        server="http://10.0.0.5:11434",
    )
    await user.open(f"/runs/{REQ_ID}")
    await user.should_see("Requirements run")
    words = shlex.split(_command(user))
    assert words[0] == "SECREQGEN_LLM_BASE_URL=http://10.0.0.5:11434"
    assert "SECREQGEN_LLM_MODEL_NAME=mistral:7b" not in words
    parsed = _parse(words)
    assert parsed["model"] == "mistral:7b"
    assert str(parsed["context"]) == "/data/ctx.yaml"
    assert str(parsed["input_file"]) == str(folder / "stories.json")
    assert parsed["story_id"] == ()


async def test_a_run_with_a_prompt_set_names_its_folder(user, tmp_path, recorder):
    folder = _make_set("threats")
    _run_folder(
        THREAT_ID,
        "threats",
        prompt_set={"kind": "threats", "name": "mine", "tested": False},
    )
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Command")
    assert _notes(user) == []
    assert _parsed(_command(user))["prompts_dir"] == folder


async def test_a_run_with_a_prompt_set_that_was_deleted_has_no_command(
    user, tmp_path, recorder
):
    _make_set("threats")
    _run_folder(
        THREAT_ID,
        "threats",
        prompt_set={"kind": "threats", "name": "mine", "tested": False},
    )
    assert jobs.stored_run_command(THREAT_ID, storage.read_manifest(THREAT_ID))
    from secreqgen.application.prompt_sets import delete_user_prompt_set

    delete_user_prompt_set("threats", "mine", storage.prompt_sets_dir())
    assert jobs.stored_run_command(THREAT_ID, storage.read_manifest(THREAT_ID)) is None
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Threat list run")
    assert _texts(user) == []


async def test_the_run_page_shows_the_option_story_id_of_an_old_one_story_run(
    user, tmp_path, recorder
):
    # An old run kept the whole story file, so the command names the story.
    _run_folder(THREAT_ID, "threats", story_ids=["S-2"])
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Command")
    assert "--story-id S-2" in _command(user)


async def test_an_old_run_of_several_stories_with_the_whole_file_has_a_command(
    user, tmp_path, recorder
):
    # The copy holds 3 stories. The run had 2 of them. Before, the page showed
    # no command for this run. Now the command names the stories.
    _run_folder(THREAT_ID, "threats", story_ids=["S-1", "S-2"])
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Command")
    assert _parsed(_command(user))["story_id"] == ("S-1", "S-2")
    assert _notes(user) == []


async def test_a_run_of_today_with_a_copy_of_only_its_stories_needs_no_story_option(
    user, tmp_path, recorder
):
    items = [
        {"id": f"S-{n}", "title": f"Story {n}", "story": "As a user."} for n in (1, 2)
    ]
    _run_folder(THREAT_ID, "threats", stories=items, story_ids=["S-1", "S-2"])
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Command")
    assert "--story-id" not in _command(user)
    assert _parsed(_command(user))["story_id"] == ()


def test_a_stored_run_names_an_id_that_is_in_the_copy_only(tmp_path):
    _run_folder(THREAT_ID, "threats", story_ids=["S-2"])
    found = jobs.stored_run_command(THREAT_ID, storage.read_manifest(THREAT_ID))
    assert _parse(found.argv)["story_id"] == ("S-2",)
    # An id that the copy lacks would make the command fail: no command.
    _run_folder(THREAT_ID, "threats", story_ids=["S-2", "S-9"])
    assert jobs.stored_run_command(THREAT_ID, storage.read_manifest(THREAT_ID)) is None


async def test_an_old_manifest_builds_what_is_known(user, tmp_path, recorder):
    # No labels, no server, no check and no stories total: an old file.
    folder = _run_folder(THREAT_ID, "threats")
    manifest = storage.read_manifest(THREAT_ID)
    for key in ("labels", "server", "check", "stories_total", "prompt_set"):
        manifest.pop(key, None)
    storage.write_manifest(THREAT_ID, manifest)
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Threat list run")
    await user.should_see("Command")
    assert shlex.split(_command(user)) == [
        "secreqgen",
        "generate",
        "threats",
        str(folder / "stories.json"),
        "--docs",
        "/data/docs",
        "--model",
        "gemma4:26b",
        "--checker-model",
        "qwen3.8:27b",
    ]


@pytest.mark.parametrize(
    "manifest",
    [
        {"kind": "threats", "status": "finished"},
        {"kind": "threats", "status": "finished", "docs": 5, "writer": 3},
        {"kind": "requirements", "status": "failed", "per_story": "x"},
        {"status": "finished", "docs": "/d", "writer": "w"},
    ],
)
async def test_an_old_manifest_that_lacks_a_value_never_fails_the_page(
    user, tmp_path, recorder, manifest
):
    kind = manifest.get("kind", "threats")
    run_id = THREAT_ID if kind == "threats" else REQ_ID
    storage.run_dir(run_id).mkdir(parents=True)
    storage.write_manifest(run_id, manifest)
    await user.open(f"/runs/{run_id}")
    # The page opens: the menu is there. (The old check, "run", matched the
    # foot of the menu, "Ollama is running". The foot reads "Ollama OK" now.)
    await user.should_see("Ollama")
    assert _texts(user) == []


async def test_an_old_run_with_a_paragraph_but_no_text_has_no_block(
    user, tmp_path, recorder
):
    _run_folder(THREAT_ID, "threats", about_words=7)
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Threat list run")
    assert _texts(user) == []


async def test_an_old_run_with_a_written_context_has_no_block(user, tmp_path, recorder):
    _run_folder(REQ_ID, "requirements", generated_context=True)
    _drop(REQ_ID, "system_context")
    await user.open(f"/runs/{REQ_ID}")
    await user.should_see("Requirements run")
    assert _texts(user) == []


async def test_an_old_requirements_run_has_no_block(user, tmp_path, recorder):
    # An older GUI did not store if the run had a system context file. A
    # command with no --context could be untrue, so the page shows none.
    _run_folder(REQ_ID, "requirements")
    _drop(REQ_ID, "system_context")
    assert jobs.stored_run_command(REQ_ID, storage.read_manifest(REQ_ID)) is None
    await user.open(f"/runs/{REQ_ID}")
    await user.should_see("Requirements run")
    assert _texts(user) == []


async def test_a_new_requirements_run_with_no_context_has_a_block(
    user, tmp_path, recorder
):
    folder = _run_folder(REQ_ID, "requirements")
    assert storage.read_manifest(REQ_ID)["system_context"] is None
    await user.open(f"/runs/{REQ_ID}")
    await user.should_see("Command")
    assert shlex.split(_command(user)) == [
        "secreqgen",
        "generate",
        "requirements",
        str(folder / "stories.json"),
        "--docs",
        "/data/docs",
        "--model",
        "gemma4:26b",
    ]


def test_a_threat_manifest_that_does_not_say_if_it_had_a_paragraph_has_no_command(
    tmp_path,
):
    _run_folder(THREAT_ID, "threats")
    assert jobs.stored_run_command(THREAT_ID, storage.read_manifest(THREAT_ID))
    _drop(THREAT_ID, "about_words")
    assert jobs.stored_run_command(THREAT_ID, storage.read_manifest(THREAT_ID)) is None


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        ("threats", {"docs": "docs"}),
        ("requirements", {"docs": "docs"}),
        ("requirements", {"system_context": "ctx.yaml"}),
    ],
)
def test_a_stored_run_with_a_relative_path_has_no_command(tmp_path, kind, changes):
    # The folder that the GUI ran in at that time is not known now.
    run_id = THREAT_ID if kind == "threats" else REQ_ID
    _run_folder(run_id, kind)
    assert jobs.stored_run_command(run_id, storage.read_manifest(run_id))
    _run_folder(run_id, kind, **changes)
    assert jobs.stored_run_command(run_id, storage.read_manifest(run_id)) is None


# the job keeps what the command needs


def _good_server(url):
    from types import SimpleNamespace

    infos = tuple(SimpleNamespace(name=n) for n in ("gemma4:26b", "qwen3.8:27b"))
    return SimpleNamespace(reachable=True, models=infos, error=None)


def _recording_run(seen):
    from types import SimpleNamespace

    def run(options, *, cfg=None, on_progress=None, **_kw):
        seen["file"] = options.input_file
        seen["text"] = options.input_file.read_text(encoding="utf-8")
        return SimpleNamespace(
            lists=[], failures=[], skipped=[], stopped=False, aborted=None, not_run=[]
        )

    return run


def _job_spec(tmp_path, **kwargs):
    from secreqgen_gui.jobs import ThreatRunSpec

    stories = [
        {"id": "S-1", "title": "One", "story": "As a user, I act."},
        {"title": "Two", "story": "As a user, I act."},  # no id: US-002
        {"id": "S-3", "title": "Three", "story": "As a user, I act."},
    ]
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"project": "demo", "user_stories": stories}))
    docs = tmp_path / "d"
    docs.mkdir()
    return ThreatRunSpec(stories_file=path, docs=docs, product="demo", **kwargs)


def _start(spec):
    manager = jobs.JobManager(check_server=_good_server)
    seen = {}
    manager._run_threats = _recording_run(seen)
    run_id = manager.start_threats(spec)
    manager.join(10)
    return run_id, seen


def test_a_run_writes_the_paragraph_file_and_keeps_the_text(tmp_path):
    run_id, _seen = _start(_job_spec(tmp_path, about=ABOUT_TEXT))
    assert storage.run_about_path(run_id).read_text("utf-8") == ABOUT_TEXT + "\n"
    manifest = storage.read_manifest(run_id)
    assert manifest["about"] == ABOUT_TEXT and manifest["about_words"] == 7


def test_a_run_without_a_paragraph_writes_no_file(tmp_path):
    run_id, _seen = _start(_job_spec(tmp_path))
    assert not storage.run_about_path(run_id).exists()
    assert "about" not in storage.read_manifest(run_id)


def test_a_run_of_some_stories_keeps_the_whole_file(tmp_path):
    spec = _job_spec(tmp_path, story_ids=("US-002", "S-3"))
    run_id, seen = _start(spec)
    manifest = storage.read_manifest(run_id)
    copy = storage.run_dir(run_id) / manifest["stories_file"]
    # The copy is the file as it is, byte for byte. The run reads that copy.
    assert copy.read_bytes() == Path(spec.stories_file).read_bytes()
    assert seen["file"] == copy
    assert manifest["story_ids"] == ["US-002", "S-3"]
    assert manifest["stories_total"] == 2  # the stories of the run
    # The command of the page names the stories of the run.
    found = jobs.stored_run_command(run_id, manifest)
    assert _parse(found.argv)["story_id"] == ("US-002", "S-3")


def test_a_run_of_all_stories_keeps_the_file_as_it_is(tmp_path):
    spec = _job_spec(tmp_path)
    run_id, seen = _start(spec)
    copy = storage.run_dir(run_id) / "stories.json"
    assert copy.read_bytes() == Path(spec.stories_file).read_bytes()
    assert seen["text"] == copy.read_text("utf-8")


def test_a_yaml_file_of_some_stories_stays_a_yaml_file(tmp_path):
    spec = _job_spec(tmp_path, story_ids=("S-1",))
    yaml_path = tmp_path / "s.yaml"
    yaml_path.write_text(
        "project: demo\nuser_stories:\n"
        "  - {id: S-1, title: One, story: As a user, I act.}\n"
        "  - {id: S-3, title: Three, story: As a user, I act.}\n"
    )
    spec.stories_file = yaml_path
    run_id, seen = _start(spec)
    manifest = storage.read_manifest(run_id)
    assert manifest["stories_file"] == "stories.yaml"
    copy = storage.run_dir(run_id) / "stories.yaml"
    assert copy.read_bytes() == yaml_path.read_bytes()
    assert seen["file"] == copy
    assert not (storage.run_dir(run_id) / "stories.json").exists()
    found = jobs.stored_run_command(run_id, manifest)
    assert _parse(found.argv)["story_id"] == ("S-1",)


def test_a_requirements_run_keeps_the_context_path(tmp_path):
    spec = _job_spec(tmp_path)
    context = tmp_path / "ctx.yaml"
    req = jobs.RequirementsRunSpec(
        stories_file=spec.stories_file, docs=spec.docs, system_context=str(context)
    )
    manager = jobs.JobManager()
    manager._settings = jobs.load_settings()
    manifest = manager._base_manifest(
        "20261117-100000-requirements", "requirements", req
    )
    assert manifest["system_context"] == str(context)
    plain = jobs.RequirementsRunSpec(stories_file=spec.stories_file, docs=spec.docs)
    manager = jobs.JobManager()
    manager._settings = jobs.load_settings()
    manifest = manager._base_manifest(
        "20261117-100000-requirements", "requirements", plain
    )
    # The key is always there: None says that the run had no context file.
    assert "system_context" in manifest and manifest["system_context"] is None


def test_the_job_has_no_copy_of_only_the_chosen_stories():
    # The whole file is the copy. The helpers of the old copy are gone.
    assert not hasattr(jobs.JobManager, "_chosen_copy")
    assert not hasattr(jobs, "_plain")


def test_a_run_of_some_stories_notes_the_skipped_entries_of_the_run_function(tmp_path):
    from types import SimpleNamespace

    def run(options, *, cfg=None, on_progress=None, **_kw):
        return SimpleNamespace(
            lists=[],
            failures=[],
            skipped=[(2, ValueError("the entry is not valid"))],
            stopped=False,
            aborted=None,
            not_run=[],
        )

    spec = _job_spec(tmp_path, story_ids=("S-1",))
    manager = jobs.JobManager(check_server=_good_server)
    manager._run_threats = run
    run_id = manager.start_threats(spec)
    manager.join(10)
    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "finished", manifest.get("error")
    assert manifest["skipped"] == [{"position": 2, "message": "the entry is not valid"}]


def test_a_date_as_product_name_keeps_the_whole_file(tmp_path):
    # JSON would turn the date into text. Then a run of some stories would
    # read another product name than a run of all stories.
    spec = _job_spec(tmp_path, story_ids=("S-1",))
    yaml_path = tmp_path / "s.yaml"
    yaml_path.write_text(
        "project: 2024-01-01\nuser_stories:\n"
        "  - {id: S-1, title: One, story: As a user, I act.}\n"
        "  - {id: S-3, title: Three, story: As a user, I act.}\n"
    )
    spec.stories_file = yaml_path
    run_id, seen = _start(spec)
    manifest = storage.read_manifest(run_id)
    assert manifest["stories_file"] == "stories.yaml"
    copy = storage.run_dir(run_id) / "stories.yaml"
    assert copy.read_bytes() == yaml_path.read_bytes()
    assert seen["file"] == copy


# faults found by a checker


def test_a_yaml_file_with_dates_and_some_stories_runs(tmp_path):
    """F1: a YAML file with dates runs, and the copy is the file as it is."""
    spec = _job_spec(tmp_path, story_ids=("S-1",))
    yaml_path = tmp_path / "s.yaml"
    yaml_path.write_text(
        "project: demo\nreviewed: 2026-01-05\nuser_stories:\n"
        "  - {id: S-1, title: One, story: As a user, I act., created: 2026-01-01}\n"
        "  - {id: S-3, title: Three, story: As a user, I act.}\n"
    )
    spec.stories_file = yaml_path
    run_id, _seen = _start(spec)
    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "finished", manifest.get("error")
    copy = storage.run_dir(run_id) / manifest["stories_file"]
    assert copy.read_bytes() == yaml_path.read_bytes()
    from secreqgen.application.inputs import read_story_file

    assert len(read_story_file(copy).stories) == 2


def test_a_command_holds_absolute_paths(tmp_path, monkeypatch):
    """F2: a relative path is made absolute against the folder of the GUI."""
    monkeypatch.chdir(tmp_path)
    spec = jobs.ThreatRunSpec(stories_file=Path("stories.json"), docs=Path("docs"))
    found = jobs.command_for_threats(
        spec,
        total_stories=3,
        default_product=None,
        about_file=None,
        base_url="http://localhost:11434",
    )
    assert found.argv[3] == str(tmp_path / "stories.json")
    assert found.argv[5] == str(tmp_path / "docs")
    req = jobs.RequirementsRunSpec(
        stories_file=Path("s.json"), docs=Path("d"), system_context="ctx.yaml"
    )
    found = jobs.command_for_requirements(
        req,
        total_stories=3,
        default_product=None,
        base_url="http://localhost:11434",
    )
    assert str(tmp_path / "ctx.yaml") in found.argv
    assert all(not a.startswith("s.json") for a in found.argv[3:4])


async def test_a_relative_path_in_the_form_gives_an_absolute_command(
    user, tmp_path, recorder, monkeypatch
):
    """F2: the block is true in a terminal that is in another folder."""
    _stories(tmp_path, 3)
    _docs(tmp_path)
    monkeypatch.chdir(tmp_path)
    await user.open("/threats/new")
    _input(user, "Stories file").set_value("stories.json")
    _input(user, "Documentation folder").set_value("docs")
    await user.should_see("3 stories found.")
    await _settle(0.8)
    tokens = shlex.split(_command(user))
    assert tokens[3] == str(tmp_path / "stories.json")
    assert tokens[tokens.index("--docs") + 1] == str(tmp_path / "docs")


async def test_a_relative_docs_path_of_a_stored_run_gives_no_block(
    user, tmp_path, recorder, monkeypatch
):
    """F2: the manifest of a run can hold a relative docs path.

    The folder that the GUI ran in at that time is not known, so the page
    shows no command.
    """
    monkeypatch.chdir(tmp_path)
    _run_folder(THREAT_ID, "threats", docs="docs")
    await user.open(f"/runs/{THREAT_ID}")
    await user.should_see("Threat list run")
    assert _texts(user) == []


@pytest.mark.parametrize(
    ("writer", "checker"),
    [("gemma4:26b", "gemma4"), ("gemma4:26b", "GEMMA4:26B"), ("gemma4", "gemma4:26b")],
)
async def test_the_tool_counts_a_checker_as_the_writer(
    user, tmp_path, recorder, writer, checker
):
    """F3: the form says so and hides the block, as for the same text."""
    await _open(user, tmp_path)
    assert len(_texts(user)) == 1
    _input(user, "Writer").set_value(writer)
    _input(user, "Checker").set_value(checker)
    await _settle(0.5)
    assert _texts(user) == []
    await user.should_see("The checker must be a different model from the writer.")
    start = user.find(marker="start").elements.pop()
    assert not start.enabled
    _input(user, "Checker").set_value("mistral:7b")
    await _settle(0.5)
    assert "--checker-model mistral:7b" in _command(user)


async def test_the_block_is_hidden_while_a_new_stories_file_is_read(
    user, tmp_path, recorder, monkeypatch
):
    """F4: the old file must not stay in the command while the new one is read."""
    import time

    from secreqgen_gui.pages import _setup_parts as parts

    stories, _docs_folder = await _open(user, tmp_path)
    other = _stories(tmp_path, 3, name="other.json")
    real = parts.read_story_file

    def slow(path):
        time.sleep(1.2)
        return real(path)

    monkeypatch.setattr(parts, "read_story_file", slow)
    _input(user, "Stories file").set_value(str(other))
    await _settle(0.8)  # the field has the new file, the read is not done
    assert _texts(user) == []
    start = user.find(marker="start").elements.pop()
    assert not start.enabled
    await _settle(1.5)
    text = _command(user)
    assert str(other) in text and str(stories) not in text


def _number_project_file(tmp_path, project=123):
    path = tmp_path / "num.json"
    path.write_text(
        json.dumps(
            {
                "project": project,
                "user_stories": [
                    {"id": "US-1", "title": "One", "story": "As a user, I act."}
                ],
            }
        )
    )
    return path


def test_a_project_key_that_is_not_text_is_found(tmp_path):
    """F5: only a text key (or no key) is the same for the GUI and the command."""
    assert jobs.project_key_is_text(_stories(tmp_path, 2))
    assert not jobs.project_key_is_text(_number_project_file(tmp_path))
    assert not jobs.project_key_is_text(_number_project_file(tmp_path, True))
    no_key = tmp_path / "none.json"
    no_key.write_text(json.dumps({"user_stories": []}))
    assert jobs.project_key_is_text(no_key)
    assert jobs.project_key_is_text(tmp_path / "missing.json")


async def test_a_number_as_project_gives_the_option_product(user, tmp_path, recorder):
    """F5: the command names the product, so the tool gets text as the GUI does."""
    stories = _number_project_file(tmp_path)
    docs = _docs(tmp_path)
    await user.open(_url("/requirements/new", stories, docs))
    await user.should_see("1 story found.")
    await _settle(0.5)
    text = _command(user)
    assert "--product 123" in text
    parsed = _parsed(text)
    assert parsed["product"] == "123"


def test_the_run_page_command_names_a_number_product(tmp_path):
    """F5: the same for a stored run."""
    folder = _run_folder(REQ_ID, "requirements", product="123")
    (folder / "stories.json").write_text(
        json.dumps(
            {
                "project": 123,
                "user_stories": [{"id": "S-1", "title": "One", "story": "As a user."}],
            }
        )
    )
    found = jobs.stored_run_command(REQ_ID, storage.read_manifest(REQ_ID))
    assert found is not None
    assert "--product" in found.argv
    assert _parse(found.argv)["product"] == "123"
