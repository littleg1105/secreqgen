"""The two setup forms: stories, documentation, setup, and the start."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

pytestmark = pytest.mark.anyio


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


def _docs(tmp_path, files=2):
    folder = tmp_path / "docs"
    folder.mkdir()
    for i in range(files):
        (folder / f"page{i}.md").write_text("# Page\nText")
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


class Recorder:
    """A stand-in for the job manager."""

    def __init__(self, busy=False):
        self._busy = busy
        self.threats = []
        self.requirements = []

    def busy(self):
        return self._busy

    def current(self):
        return None

    def start_threats(self, spec):
        self.threats.append(spec)
        return "20261117-100000-threats"

    def start_requirements(self, spec):
        self.requirements.append(spec)
        return "20261117-100000-requirements"


@pytest.fixture
def recorder(monkeypatch):
    from secreqgen_gui import jobs

    fake = Recorder()
    monkeypatch.setattr(jobs, "manager", fake)
    return fake


async def _ready(user, tmp_path, count=2, files=2, route="/threats/new"):
    stories = _stories(tmp_path, count)
    docs = _docs(tmp_path, files)
    await user.open(_url(route, stories, docs))
    return stories, docs


async def test_story_file_by_path_shows_count_and_stories(user, tmp_path):
    await _ready(user, tmp_path)
    await user.should_see("Start a threat list")
    await user.should_see("2 stories in the file.")
    await user.should_see("US-001")
    await user.should_see("Title 2")
    await user.should_see("2 Markdown files in the folder.")
    await user.should_see("2 stories, about 2 minutes.")


async def test_select_none_and_all(user, tmp_path, recorder):
    await _ready(user, tmp_path)
    await user.should_see("2 stories in the file.")
    user.find("Select none").click()
    await user.should_see("0 stories, about 0 minutes.")
    assert not _button(user, "Start threat list").enabled
    user.find("Select all").click()
    await user.should_see("2 stories, about 2 minutes.")
    assert _button(user, "Start threat list").enabled


async def test_broken_file_shows_error_and_keeps_start_off(user, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    docs = _docs(tmp_path)
    await user.open(_url("/threats/new", bad, docs))
    await user.should_see("Error parsing input file")
    assert not _button(user, "Start threat list").enabled


async def test_missing_file_shows_error(user, tmp_path):
    await user.open(_url("/threats/new", tmp_path / "nothing.json", _docs(tmp_path)))
    await user.should_see("nothing.json")
    assert not _button(user, "Start threat list").enabled


async def test_folder_without_markdown_shows_its_line(user, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    await user.open(_url("/threats/new", _stories(tmp_path), folder))
    await user.should_see("The folder holds no Markdown file.")
    assert not _button(user, "Start threat list").enabled


async def test_missing_folder_shows_its_line(user, tmp_path):
    await user.open(_url("/threats/new", _stories(tmp_path), tmp_path / "none"))
    await user.should_see(
        "Folder not found. Choose the folder that holds the Markdown files "
        "of the product."
    )


async def test_product_name_comes_from_the_story_file(user, tmp_path):
    await _ready(user, tmp_path)
    await user.should_see("2 stories in the file.")
    inputs = {e.props.get("label"): e for e in user.find(kind=ui.input).elements}
    assert inputs["Product name"].value == "Demo"


async def _about(user, count):
    area = user.find(kind=ui.textarea, content="Paragraph about the product (optional)")
    area.type(" ".join(["word"] * count))


async def test_121_words_block_the_start_and_120_do_not(user, tmp_path):
    await _ready(user, tmp_path)
    await user.should_see("2 stories in the file.")
    await user.should_see("2 Markdown files in the folder.")
    await _about(user, 121)
    await user.should_see("121 of 120 words")
    assert not _button(user, "Start threat list").enabled
    area = user.find(kind=ui.textarea, content="Paragraph about the product (optional)")
    area.clear()
    await _about(user, 120)
    await user.should_see("120 of 120 words")
    assert _button(user, "Start threat list").enabled


async def test_same_model_two_times_shows_line_and_blocks_start(user, tmp_path):
    await _ready(user, tmp_path)
    await user.should_see("2 Markdown files in the folder.")
    assert _button(user, "Start threat list").enabled
    second = user.find(kind=ui.input, content="Second model")
    second.clear()
    second.type("gemma4:26b")
    await user.should_see("The second model must be a different model.")
    assert not _button(user, "Start threat list").enabled


async def test_second_model_off_when_the_switch_is_off(user, tmp_path, recorder):
    await _ready(user, tmp_path)
    await user.should_see("2 Markdown files in the folder.")
    user.find("A second model checks each row").click()
    await asyncio.sleep(0.1)
    elements = {e.props.get("label"): e for e in user.find(kind=ui.input).elements}
    assert not elements["Second model"].enabled


async def test_click_starts_a_threat_run_with_the_right_spec(
    user, tmp_path, recorder
):
    stories, docs = await _ready(user, tmp_path)
    await user.should_see("2 Markdown files in the folder.")
    await user.should_see("2 stories in the file.")
    user.find("Start threat list").click()
    await asyncio.sleep(0.3)
    assert user.back_history[-1] == "/runs/20261117-100000-threats"
    assert len(recorder.threats) == 1
    spec = recorder.threats[0]
    assert spec.stories_file == stories
    assert spec.docs == docs
    assert spec.product == "Demo"
    assert spec.story_ids is None
    assert spec.writer_model == "gemma4:26b"
    assert spec.check is True
    assert spec.checker_model == "qwen3.8:27b"
    assert spec.labels is True
    assert spec.about is None
    assert spec.prompt_set is None


async def test_chosen_stories_go_into_the_spec(user, tmp_path, recorder):
    await _ready(user, tmp_path)
    await user.should_see("2 stories in the file.")
    boxes = user.find(kind=ui.checkbox).elements
    first = next(b for b in boxes if "US-002" in b.text)
    first.set_value(False)
    await user.should_see("1 story, about 1 minute.")
    user.find("Start threat list").click()
    await asyncio.sleep(0.3)
    assert recorder.threats[0].story_ids == ("US-001",)


async def test_busy_manager_turns_the_start_off(user, tmp_path, monkeypatch):
    from secreqgen_gui import jobs

    monkeypatch.setattr(jobs, "manager", Recorder(busy=True))
    await _ready(user, tmp_path)
    await user.should_see("A run is in work.")
    await user.should_see("Open the run")
    assert not _button(user, "Start threat list").enabled


async def test_user_prompt_set_shows_the_caution_line(user, tmp_path):
    from secreqgen_gui import storage

    folder = storage.prompt_sets_dir() / "threats" / "mine"
    folder.mkdir(parents=True)
    await _ready(user, tmp_path)
    await user.should_not_see("Not the tested setup")
    select = user.find(kind=ui.select)
    select.click()
    user.find("mine").click()
    await user.should_see("Not the tested setup")


async def test_type_one_story_writes_a_file_and_reads_it(user, tmp_path):
    await user.open("/threats/new")
    user.find("Type one story").click()
    user.find(marker="story_text").type("As a user, I want to sign in.")
    user.find("Use this story").click()
    await user.should_see("1 story in the file.")
    await user.should_see("US-001")


async def test_upload_of_a_wrong_file_type_is_refused(user, tmp_path):
    from nicegui.elements.upload_files import SmallFileUpload

    await user.open("/threats/new")
    up = user.find(kind=ui.upload).elements.pop()
    await up.handle_uploads(
        [SmallFileUpload(name="x.exe", content_type="a/b", _data=b"abc")]
    )
    await user.should_see("The file must be a .json, .yaml or .yml file.")


async def test_upload_of_a_story_file_shows_its_stories(user, tmp_path):
    from nicegui.elements.upload_files import SmallFileUpload

    data = _stories(tmp_path, 3).read_bytes()
    await user.open("/threats/new")
    up = user.find(kind=ui.upload).elements.pop()
    await up.handle_uploads(
        [SmallFileUpload(name="s.json", content_type="application/json", _data=data)]
    )
    await user.should_see("3 stories in the file.")


async def test_requirements_form_starts_with_its_spec(user, tmp_path, recorder):
    stories, docs = await _ready(user, tmp_path, route="/requirements/new")
    await user.should_see("Start requirements")
    await user.should_see("Model")
    await user.should_see("System context file (optional)")
    await user.should_see("2 Markdown files in the folder.")
    user.find("Start requirements").click()
    await asyncio.sleep(0.3)
    assert len(recorder.requirements) == 1
    spec = recorder.requirements[0]
    assert (spec.stories_file, spec.docs) == (stories, docs)
    assert spec.model == "gemma4:26b"
    assert spec.system_context is None
    assert spec.prompt_set is None


async def test_requirements_start_is_off_without_a_folder(user, tmp_path):
    await user.open(_url("/requirements/new", _stories(tmp_path)))
    await user.should_see("2 stories in the file.")
    assert not _button(user, "Start requirements").enabled
