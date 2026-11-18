"""Plan 2, part A: the setup forms after the review of 18 November."""

from __future__ import annotations

import asyncio
import json
import threading
from urllib.parse import quote

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen_gui import form_memory  # noqa: E402

pytestmark = pytest.mark.anyio


def _stories(tmp_path, name="stories.json", project="alpha", count=1):
    items = [
        {"id": f"S-{i}", "title": f"T{i}", "story": "As a user, I want x."}
        for i in range(1, count + 1)
    ]
    data = {"user_stories": items}
    if project:
        data["project"] = project
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _docs(tmp_path, name="docs", files=2):
    folder = tmp_path / name
    folder.mkdir()
    for i in range(files):
        (folder / f"page{i}.md").write_text("# Page\nText", encoding="utf-8")
    return folder


def _url(route, stories=None, docs=None):
    parts = []
    if stories is not None:
        parts.append("stories=" + quote(str(stories)))
    if docs is not None:
        parts.append("docs=" + quote(str(docs)))
    return route + ("?" + "&".join(parts) if parts else "")


def _button(user, label):
    found = [
        e for e in user.find(kind=ui.button).elements if getattr(e, "text", "") == label
    ]
    assert found, f"no button {label}"
    return found[0]


def _input(user, label):
    found = [
        e for e in user.find(kind=ui.input).elements if e.props.get("label") == label
    ]
    assert found, f"no input {label}"
    return found[0]


def _texts(user):
    return [e.text for e in user.find(kind=ui.label).elements]


def _switch(user, label="Read other file types too"):
    return [e for e in user.find(kind=ui.switch).elements if e.text == label][0]


class Recorder:
    """A stand-in for the job manager."""

    def __init__(self):
        self.threats = []
        self.requirements = []

    def busy(self):
        return False

    def current(self):
        return None

    def start_threats(self, spec):
        self.threats.append(spec)
        return "20261118-100000-threats"

    def start_requirements(self, spec):
        self.requirements.append(spec)
        return "20261118-100000-requirements"


@pytest.fixture
def recorder(monkeypatch):
    from secreqgen_gui import jobs

    fake = Recorder()
    monkeypatch.setattr(jobs, "manager", fake)
    return fake


@pytest.fixture
def slow_scan(monkeypatch):
    """Hold every scan of a folder until the test opens the gate."""
    from secreqgen_gui.pages import _setup_parts as parts

    gate = threading.Event()
    real = parts.count_documentation

    def held(folder):
        gate.wait(10)
        return real(folder)

    monkeypatch.setattr(parts, "count_documentation", held)
    yield gate
    gate.set()


# A1: Clear the form


async def test_clear_on_the_threat_form_keeps_the_requirements_options(user, tmp_path):
    form_memory.remember("requirements", "model", "m1:7b")
    form_memory.remember("requirements", "context_file", "~/c.yaml")
    form_memory.remember("requirements", "prompts", "mine")
    form_memory.remember("requirements", "context_folder", "~/code")
    form_memory.remember("threats", "writer", "w:1")
    await user.open(_url("/threats/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("1 story found.")
    user.find("Clear the form").click()
    await user.should_not_see("1 story found.")
    assert form_memory.recall("common", "docs") is None
    assert form_memory.recall("threats", "writer") is None
    assert form_memory.recall("requirements", "model") == "m1:7b"
    assert form_memory.recall("requirements", "context_file") == "~/c.yaml"
    assert form_memory.recall("requirements", "prompts") == "mine"
    assert form_memory.recall("requirements", "context_folder") == "~/code"
    await user.open("/requirements/new")
    assert _input(user, "Model").value == "m1:7b"


async def test_clear_on_the_requirements_form_keeps_the_threat_options(user, tmp_path):
    form_memory.remember("threats", "writer", "w:1")
    form_memory.remember("threats", "checker", "c:2")
    form_memory.remember("threats", "check", False)
    form_memory.remember("threats", "labels", False)
    form_memory.remember("threats", "about", "A tool.")
    form_memory.remember("threats", "prompts", "mine")
    form_memory.remember("requirements", "model", "m1:7b")
    form_memory.remember("requirements", "context_folder", "~/code")
    await user.open(_url("/requirements/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("1 story found.")
    user.find("Clear the form").click()
    await user.should_not_see("1 story found.")
    assert form_memory.recall("requirements", "model") is None
    assert form_memory.recall("requirements", "context_folder") is None
    assert form_memory.recall("common", "stories_file") is None
    assert form_memory.recall("threats", "writer") == "w:1"
    assert form_memory.recall("threats", "checker") == "c:2"
    assert form_memory.recall("threats", "check") is False
    assert form_memory.recall("threats", "labels") is False
    assert form_memory.recall("threats", "about") == "A tool."
    assert form_memory.recall("threats", "prompts") == "mine"


@pytest.mark.parametrize("route", ["/threats/new", "/requirements/new"])
async def test_the_product_box_stays_empty_after_the_clear(user, tmp_path, route):
    await user.open(_url(route, _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("1 story found.")
    assert _input(user, "Product name (optional)").value == "alpha"
    user.find("Clear the form").click()
    await user.should_not_see("1 story found.")
    for _ in range(4):
        await asyncio.sleep(0.15)
        assert _input(user, "Product name (optional)").value == ""
    assert _input(user, "Stories file").value == ""
    assert form_memory.recall("common", "product") is None


# A2: the folder scan


async def test_a_folder_in_reading_never_shows_an_old_result(
    user, tmp_path, slow_scan, recorder
):
    old = _docs(tmp_path, "old", files=1)
    new = _docs(tmp_path, "new", files=3)
    await user.open(_url("/threats/new", _stories(tmp_path), old))
    await user.should_see("Reading the folder.")
    assert not _button(user, "Start threat list").enabled
    slow_scan.set()
    await user.should_see("1 Markdown file found.")
    assert _button(user, "Start threat list").enabled
    slow_scan.clear()
    _input(user, "Documentation folder").set_value(str(new))
    await user.should_see("Reading the folder.")
    assert "1 Markdown file found." not in _texts(user)
    assert not _button(user, "Start threat list").enabled
    # a click on the switch during the scan does not show "Folder not found"
    switch = _switch(user)
    switch.set_value(False)
    switch.set_value(True)
    await asyncio.sleep(0.1)
    texts = _texts(user)
    assert "Reading the folder." in texts
    assert "Folder not found. Check the path or click Browse." not in texts
    slow_scan.set()
    await user.should_see("3 Markdown files found.")
    assert _button(user, "Start threat list").enabled


async def test_the_first_scan_of_a_remembered_folder_shows_reading(
    user, tmp_path, slow_scan
):
    docs = _docs(tmp_path)
    await user.open(_url("/requirements/new", None, docs))
    await user.should_see("Reading the folder.")
    switch = _switch(user)
    switch.set_value(False)
    await asyncio.sleep(0.1)
    assert "Folder not found. Check the path or click Browse." not in _texts(user)
    slow_scan.set()
    await user.should_see("2 Markdown files found.")


@pytest.mark.parametrize(
    ("route", "start"),
    [
        ("/threats/new", "Start threat list"),
        ("/requirements/new", "Start requirements"),
    ],
)
async def test_start_checks_the_folder_once_more(
    user, tmp_path, recorder, route, start
):
    import shutil

    docs = _docs(tmp_path)
    await user.open(_url(route, _stories(tmp_path), docs))
    await user.should_see("2 Markdown files found.")
    assert _button(user, start).enabled
    shutil.rmtree(docs)
    user.find(start).click()
    await user.should_see("Folder not found. Check the path or click Browse.")
    await asyncio.sleep(0.3)
    assert not recorder.threats and not recorder.requirements
    assert not _button(user, start).enabled


# A3: the line under the folder field


@pytest.mark.parametrize(
    ("markdown", "inside", "folders", "others", "line"),
    [
        (
            1243, 1180, ("node_modules",), {},
            "1,243 Markdown files found. 1,180 are in node_modules.",
        ),
        (
            1243, 1180, ("node_modules", "vendor"), {},
            "1,243 Markdown files found. 1,180 are in node_modules and vendor.",
        ),
        (
            1243, 1180, ("node_modules", "vendor", "dist"), {},
            "1,243 Markdown files found. 1,180 are in node_modules, vendor "
            "and 1 more folder.",
        ),
        (
            1243, 1180, ("node_modules", "vendor", "dist", "build"), {},
            "1,243 Markdown files found. 1,180 are in node_modules, vendor "
            "and 2 more folders.",
        ),
        (5, 1, ("vendor",), {}, "5 Markdown files found. 1 is in vendor."),
        (
            1243, 1180, ("node_modules",), {".txt": 3},
            "1,243 Markdown files found. 1,180 are in node_modules. "
            "3 files of other types are converted (.txt).",
        ),
        (5, 0, (), {}, "5 Markdown files found."),
    ],
)  # fmt: skip
def test_the_dependency_sentence(markdown, inside, folders, others, line):
    from secreqgen_gui.pages._setup_parts import docs_line

    assert docs_line(markdown, others, True, inside, folders) == line


async def test_the_form_counts_files_in_dependency_folders(user, tmp_path):
    docs = _docs(tmp_path, files=2)
    for name, count in (("node_modules", 3), ("vendor", 1)):
        folder = docs / name / "pkg"
        folder.mkdir(parents=True)
        for i in range(count):
            (folder / f"r{i}.md").write_text("# R", encoding="utf-8")
    await user.open(_url("/threats/new", _stories(tmp_path), docs))
    await user.should_see("6 Markdown files found. 4 are in node_modules and vendor.")
    assert _button(user, "Start threat list").enabled


# A4: the product name follows another stories file


async def test_another_stories_file_replaces_a_typed_product(user, tmp_path):
    first = _stories(tmp_path, "a.json", "alpha")
    second = _stories(tmp_path, "b.json", "beta")
    await user.open(_url("/threats/new", first, _docs(tmp_path)))
    await user.should_see("1 story found.")
    box = _input(user, "Product name (optional)")
    box.set_value("Mastodon")
    assert box.value == "Mastodon"
    _input(user, "Stories file").set_value(str(second))
    for _ in range(40):
        if box.value == "beta":
            break
        await asyncio.sleep(0.1)
    assert box.value == "beta"
    assert form_memory.recall("common", "product") is None


async def test_a_file_without_a_project_leaves_the_product_box(user, tmp_path):
    first = _stories(tmp_path, "a.json", "alpha")
    second = _stories(tmp_path, "b.json", project=None)
    await user.open(_url("/threats/new", first, _docs(tmp_path)))
    await user.should_see("1 story found.")
    box = _input(user, "Product name (optional)")
    box.set_value("Mastodon")
    _input(user, "Stories file").set_value(str(second))
    await asyncio.sleep(0.6)
    assert box.value == "Mastodon"
    assert form_memory.recall("common", "product") == "Mastodon"


async def test_the_first_load_of_a_remembered_form_keeps_the_typed_name(user, tmp_path):
    stories = _stories(tmp_path, "a.json", "alpha")
    docs = _docs(tmp_path)
    form_memory.remember("common", "stories_file", str(stories))
    form_memory.remember("common", "docs", str(docs))
    form_memory.remember("common", "product", "Mastodon")
    await user.open("/threats/new")
    await user.should_see("1 story found.")
    await asyncio.sleep(0.3)
    assert _input(user, "Product name (optional)").value == "Mastodon"


async def test_a_name_typed_after_the_pick_wins_over_the_late_read(user, tmp_path):
    stories = _stories(tmp_path, "a.json", "alpha")
    await user.open(_url("/threats/new", None, _docs(tmp_path)))
    _input(user, "Stories file").set_value(str(stories))
    _input(user, "Product name (optional)").set_value("Acme")
    await user.should_see("1 story found.")
    await asyncio.sleep(0.3)
    assert _input(user, "Product name (optional)").value == "Acme"


# A5: a stories file that cannot be read


async def test_a_missing_stories_file_shows_the_text_of_a_missing_file(user, tmp_path):
    """The path of the file is in the field. The line does not repeat it."""
    gone = tmp_path / "gone.json"
    await user.open(_url("/threats/new", gone, _docs(tmp_path)))
    await user.should_see("File not found. Check the path or click Browse.")
    await user.should_not_see("Input file not found")


async def test_a_bad_file_shows_the_reason_and_pick_another(user, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    await user.open(_url("/requirements/new", bad, _docs(tmp_path)))
    shown = []
    for _ in range(30):
        shown = [t for t in _texts(user) if "Error parsing input file" in t]
        if shown:
            break
        await asyncio.sleep(0.1)
    assert shown and shown[0].endswith(" Pick another file.")


async def test_an_upload_that_cannot_be_read_shows_pick_another(user, tmp_path):
    from nicegui.elements.upload_files import SmallFileUpload

    await user.open("/threats/new")
    up = user.find(kind=ui.upload).elements.pop()
    await up.handle_uploads(
        [SmallFileUpload(name="x.exe", content_type="a/b", _data=b"abc")]
    )
    await user.should_see("Use a .json, .yaml or .yml file. Pick another file.")


@pytest.mark.parametrize("tab", ["upload", "type"])
async def test_a_remembered_file_that_fails_opens_the_path_tab(user, tmp_path, tab):
    gone = tmp_path / "gone.json"
    form_memory.remember("common", "stories_file", str(gone))
    form_memory.remember("common", "stories_tab", tab)
    await user.open("/threats/new")
    await user.should_see("File not found. Check the path or click Browse.")
    assert user.find(kind=ui.tabs).elements.pop().value == "path"


# A6: a value of the wrong type in forms.json

BAD_VALUES = [
    {"common": {"docs": 5}},
    {"common": {"stories_file": 5}},
    {"common": {"product": ["a"]}},
    {"common": {"stories_tab": ["x"]}},
    {"common": {"stories_tab": {"a": 1}}},
    {"common": {"story_ids": "US-001"}},
    {"common": {"typed_story": ["a"]}},
    {"common": {"other_formats": "false"}},
    {"threats": {"writer": 5}},
    {"threats": {"checker": ["a"]}},
    {"threats": {"about": 7}},
    {"threats": {"prompts": ["a"]}},
    {"threats": {"check": "no"}},
    {"threats": {"labels": 0}},
    {"requirements": {"model": 3}},
    {"requirements": {"context_file": 3}},
    {"requirements": {"context_folder": ["a"]}},
    {"requirements": {"prompts": {"a": 1}}},
]


@pytest.mark.parametrize("case", range(len(BAD_VALUES)))
@pytest.mark.parametrize("route", ["/threats/new", "/requirements/new"])
async def test_a_wrong_type_in_the_memory_never_breaks_a_page(
    user, gui_home, case, route
):
    gui_home.mkdir(parents=True, exist_ok=True)
    (gui_home / "forms.json").write_text(json.dumps(BAD_VALUES[case]))
    await user.open(route)
    await user.should_see("Clear the form")
    await user.should_see(
        "Start threat list" if "threats" in route else "Start requirements"
    )


def test_recall_gives_the_default_for_another_type(gui_home):
    form_memory.remember("threats", "check", False)
    form_memory.remember("threats", "writer", "w")
    form_memory.remember("threats", "list", ["a"])
    assert form_memory.recall("threats", "check", True, bool) is False
    assert form_memory.recall("threats", "check", "d", str) == "d"
    assert form_memory.recall("threats", "writer", "d", str) == "w"
    assert form_memory.recall("threats", "writer", "d", list) == "d"
    assert form_memory.recall("threats", "list", None, list) == ["a"]
    assert form_memory.recall("threats", "list", "d", str) == "d"
    assert form_memory.recall("threats", "writer", "d") == "w"  # no kind: as before


def test_clear_with_names_empties_only_those_blocks(gui_home):
    form_memory.remember("common", "docs", "a")
    form_memory.remember("threats", "writer", "w")
    form_memory.remember("requirements", "model", "m")
    form_memory.clear("common", "threats")
    assert form_memory.recall("common", "docs") is None
    assert form_memory.recall("threats", "writer") is None
    assert form_memory.recall("requirements", "model") == "m"
    form_memory.clear()
    assert form_memory.recall("requirements", "model") is None


# A7: the context file and the prompt set of the requirements form


async def test_a_missing_context_file_shows_its_line_and_blocks_the_start(
    user, tmp_path, recorder
):
    gone = tmp_path / "gone.yaml"
    form_memory.remember("requirements", "context_file", str(gone))
    await user.open(_url("/requirements/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("File not found. Check the path or click Browse.")
    assert not _button(user, "Start requirements").enabled
    # a file that is there takes the line away and the run can start
    there = tmp_path / "context.yaml"
    there.write_text("tech_stack: []\n")
    _input(user, "System context file (optional)").set_value(str(there))
    await user.should_not_see("File not found. Check the path or click Browse.")
    for _ in range(30):
        if _button(user, "Start requirements").enabled:
            break
        await asyncio.sleep(0.1)
    assert _button(user, "Start requirements").enabled
    user.find("Start requirements").click()
    for _ in range(30):
        if recorder.requirements:
            break
        await asyncio.sleep(0.1)
    assert recorder.requirements[0].system_context == str(there)


async def test_start_checks_the_context_file_once_more(user, tmp_path, recorder):
    there = tmp_path / "context.yaml"
    there.write_text("tech_stack: []\n")
    form_memory.remember("requirements", "context_file", str(there))
    await user.open(_url("/requirements/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("2 Markdown files found.")
    for _ in range(30):
        if _button(user, "Start requirements").enabled:
            break
        await asyncio.sleep(0.1)
    there.unlink()
    user.find("Start requirements").click()
    await user.should_see("File not found. Check the path or click Browse.")
    await asyncio.sleep(0.3)
    assert not recorder.requirements


async def test_an_empty_context_field_is_fine(user, tmp_path):
    await user.open(_url("/requirements/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("2 Markdown files found.")
    await user.should_not_see("File not found. Check the path or click Browse.")
    assert _button(user, "Start requirements").enabled


@pytest.mark.parametrize("block", ["requirements", "threats"])
async def test_a_prompt_set_that_is_gone_is_forgotten(user, block):
    form_memory.remember(block, "prompts", "gone-set")
    await user.open(f"/{block}/new")
    await user.should_see("Tested prompts")
    assert form_memory.recall(block, "prompts") is None
    select = user.find(kind=ui.select).elements.pop()
    assert select.value == "__tested__"


# A8: the foot while the model writes a system context


@pytest.mark.parametrize(
    ("route", "start"),
    [
        ("/threats/new", "Start threat list"),
        ("/requirements/new", "Start requirements"),
    ],
)
async def test_the_foot_says_when_a_system_context_is_written(
    user, tmp_path, monkeypatch, route, start
):
    from secreqgen_gui import context_job

    monkeypatch.setattr(context_job.writer, "busy", lambda: True)
    await user.open(_url(route, _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("A model is writing a system context. Wait for it to end.")
    await user.should_not_see("Another run is in progress.")
    links = [e for e in user.find(kind=ui.link).elements if e.text == "Open the run"]
    assert all(not link.parent_slot.parent.visible for link in links)
    assert not _button(user, start).enabled


async def test_the_foot_keeps_its_text_for_a_run(user, tmp_path, monkeypatch):
    from secreqgen_gui import jobs

    class Busy(Recorder):
        def busy(self):
            return True

    monkeypatch.setattr(jobs, "manager", Busy())
    await user.open(_url("/threats/new", _stories(tmp_path), _docs(tmp_path)))
    await user.should_see("Another run is in progress. Wait for it to end, or stop it.")
    await user.should_see("Open the run")
    await user.should_not_see("A model is writing a system context.")


async def test_a_folder_that_is_back_is_read_again_on_a_click_of_the_switch(
    user, tmp_path
):
    """After "Folder not found", the switch starts a new read of the folder."""
    import shutil

    docs = _docs(tmp_path)
    stories = tmp_path / "stories.json"
    story = '{"id": "S-1", "title": "T", "story": "As a user I log in."}'
    stories.write_text('{"user_stories": [' + story + "]}")
    kept = tmp_path / "kept"
    shutil.copytree(docs, kept)
    shutil.rmtree(docs)
    await user.open(_url("/threats/new", stories, docs))
    await user.should_see("Folder not found. Check the path or click Browse.")
    shutil.copytree(kept, docs)
    user.find(marker="other_formats").click()
    await user.should_not_see("Folder not found. Check the path or click Browse.")
    await user.should_see("Markdown file")
