"""Other file types: the form, the run spec, the tags and the file names."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

pytestmark = pytest.mark.anyio

PAGE = "# Page\n\nUsers must log in.\n"
HINT = "Files converted to Markdown. Not in the thesis tests."


def _folder(tmp_path, name, markdown=(), others=()):
    folder = tmp_path / name
    folder.mkdir()
    for file in markdown:
        (folder / file).write_text(PAGE, encoding="utf-8")
    for file in others:
        (folder / file).write_text("Title\n=====\n\nText.\n", encoding="utf-8")
    return folder


def _stories(tmp_path):
    item = {"id": "S-1", "title": "T", "story": "As a user."}
    path = tmp_path / "stories.json"
    path.write_text(json.dumps({"user_stories": [item]}), encoding="utf-8")
    return path


def _button(user, label):
    found = [
        e for e in user.find(kind=ui.button).elements if getattr(e, "text", "") == label
    ]
    assert found, f"no button {label}"
    return found[0]


def _switch(user):
    return user.find(kind=ui.switch, content="Read other file types too")


# the six lines of the form


@pytest.mark.parametrize(
    ("markdown", "others", "convert", "line"),
    [
        (3, {}, True, "3 Markdown files found."),
        (1, {}, True, "1 Markdown file found."),
        (
            2,
            {".rst": 2, ".txt": 1},
            True,
            "2 Markdown files found. 3 files of other types are converted "
            "(.rst, .txt).",
        ),
        (
            2,
            {".rst": 2, ".txt": 1},
            False,
            "2 Markdown files found. 3 files of other types are not read "
            "(.rst, .txt).",
        ),
        (
            1,
            {".rst": 1},
            True,
            "1 Markdown file found. 1 file of another type is converted (.rst).",
        ),
        (
            0,
            {".rst": 4},
            True,
            "No Markdown files. 4 files of other types are converted (.rst).",
        ),
        (
            0,
            {".rst": 4},
            False,
            "This folder has no Markdown files. "
            'Turn on "Read other file types too" to read the 4 other files.',
        ),
        (
            0,
            {},
            True,
            "No documentation files in this folder.",
        ),
        (
            1,
            {".a": 1, ".b": 2, ".c": 3, ".d": 4},
            True,
            "1 Markdown file found. 10 files of other types are converted "
            "(.d, .c, .b).",
        ),
    ],
)
def test_the_line_under_the_folder_field(markdown, others, convert, line):
    from secreqgen_gui.pages._setup_parts import docs_line

    assert docs_line(markdown, others, convert) == line


async def test_the_form_counts_files_of_other_types(user, tmp_path):
    docs = _folder(tmp_path, "docs", ["a.md"], ["b.rst", "c.txt", "d.txt"])
    await user.open(f"/threats/new?docs={quote(str(docs))}")
    await user.should_see(
        "1 Markdown file found. 3 files of other types are converted (.txt, .rst)."
    )
    props = {e.props.get("label"): e.props for e in user.find(kind=ui.input).elements}
    assert "hint" not in props["Documentation folder"]
    _switch(user).click()
    await user.should_see(
        "1 Markdown file found. 3 files of other types are not read (.txt, .rst)."
    )


async def test_a_folder_with_only_other_types_can_start_with_the_switch_on(
    user, tmp_path
):
    docs = _folder(tmp_path, "only_rst", [], ["b.rst"])
    stories = _stories(tmp_path)
    url = f"/threats/new?stories={quote(str(stories))}&docs={quote(str(docs))}"
    await user.open(url)
    await user.should_see(
        "No Markdown files. 1 file of another type is converted (.rst)."
    )
    assert _button(user, "Start threat list").enabled
    _switch(user).click()
    await user.should_see(
        'Turn on "Read other file types too" to read the 1 other file.'
    )
    assert not _button(user, "Start threat list").enabled


async def test_the_switch_is_remembered(user, tmp_path):
    from secreqgen_gui import form_memory

    docs = _folder(tmp_path, "docs", ["a.md"], ["b.rst"])
    await user.open(f"/requirements/new?docs={quote(str(docs))}")
    await user.should_see("converted")
    assert form_memory.recall("common", "other_formats", True) is True
    _switch(user).click()
    await user.should_see("not read")
    assert form_memory.recall("common", "other_formats", True) is False
    await user.open("/threats/new")
    assert _switch(user).elements.pop().value is False


# the run


class Recorder:
    def __init__(self):
        self.threats = []
        self.requirements = []

    def busy(self):
        return False

    def current(self):
        return None

    def start_threats(self, spec):
        self.threats.append(spec)
        return "20261117-100000-threats"

    def start_requirements(self, spec):
        self.requirements.append(spec)
        return "20261117-100000-requirements"


@pytest.mark.parametrize(
    ("route", "start", "switch_off"),
    [
        ("/threats/new", "Start threat list", False),
        ("/threats/new", "Start threat list", True),
        ("/requirements/new", "Start requirements", True),
    ],
)
async def test_the_spec_holds_the_switch(
    user, tmp_path, monkeypatch, route, start, switch_off
):
    from secreqgen_gui import jobs

    fake = Recorder()
    monkeypatch.setattr(jobs, "manager", fake)
    docs = _folder(tmp_path, "docs", ["a.md"], ["b.rst"])
    stories = _stories(tmp_path)
    await user.open(f"{route}?stories={quote(str(stories))}&docs={quote(str(docs))}")
    await user.should_see("1 story found.")
    if switch_off:
        _switch(user).click()
    user.find(start).click()
    for _ in range(50):
        specs = fake.threats + fake.requirements
        if specs:
            break
        await asyncio.sleep(0.1)
    assert specs[0].other_formats is (not switch_off)


def test_a_spec_from_a_start_file_accepts_the_switch(tmp_path):
    from secreqgen_gui import jobs

    spec = jobs.spec_from_dict(
        {
            "kind": "requirements",
            "stories_file": "a.json",
            "docs": "d",
            "other_formats": True,
        }
    )
    assert spec.other_formats is True
    assert jobs.ThreatRunSpec(tmp_path, tmp_path).other_formats is False


def _report(converted, skipped=()):
    return SimpleNamespace(converted=converted, skipped=list(skipped))


def test_the_manifest_gets_docs_formats_only_with_a_report():
    from secreqgen_gui.jobs import JobManager

    manifest = {}
    JobManager._docs_formats(manifest, SimpleNamespace(docs_report=None))
    assert "docs_formats" not in manifest
    JobManager._docs_formats(
        manifest,
        SimpleNamespace(docs_report=_report({".rst": 10}, [("x.pdf", "empty")])),
    )
    assert manifest["docs_formats"] == {"converted": {".rst": 10}, "skipped": 1}


def test_the_run_passes_the_option_and_the_cache(tmp_path, monkeypatch):
    from secreqgen_gui import storage
    from secreqgen_gui.jobs import JobManager, ThreatRunSpec

    seen = {}

    def fake_run(options, **_kw):
        seen["options"] = options
        return SimpleNamespace(
            skipped=[], aborted=None, stopped=False, docs_report=_report({".rst": 2})
        )

    stories = _stories(tmp_path)
    manager = JobManager(
        run_threats=fake_run,
        check_server=lambda url: SimpleNamespace(
            reachable=True,
            models=(SimpleNamespace(name="w"), SimpleNamespace(name="c")),
            error=None,
        ),
    )
    spec = ThreatRunSpec(
        stories, tmp_path, writer_model="w", checker_model="c", other_formats=True
    )
    run_id = manager.start_threats(spec)
    manager.join(10)
    assert seen["options"].other_formats is True
    assert seen["options"].docs_cache == storage.docs_cache_dir()
    assert storage.docs_cache_dir().name == "doc_cache"
    manifest = storage.read_manifest(run_id)
    assert manifest["docs_formats"] == {"converted": {".rst": 2}, "skipped": 0}


# the pages


def _threat_run(run_id, docs_formats):
    from secreqgen_gui import storage

    story = "MM-US-007"
    folder = storage.run_dir(run_id) / "stories" / story
    folder.mkdir(parents=True)
    row = {
        "id": "T1", "type": "attack", "threat": "Users may phish.", "harm": "",
        "story_part": "x", "source": "P1", "quote": "Users must log in.",
        "requirement": "R.", "from_extra_group": False, "actor": "", "stride": "",
        "weak_signs": [], "second_model": "accepts",
    }  # fmt: skip
    model = {
        "draft_notice": "Draft.", "prompt_set": "threats_v5b_labels",
        "product": "p", "model": "m",
        "story": {"id": story, "title": "T", "story": "S", "acceptance_criteria": []},
        "seconds": 1,
        "calls": [
            {"group": 1, "passages": 1, "attempts": 1, "ok": True, "seconds": 1.0}
        ],
        "label_step": True, "about": None, "checker": "c", "extra_passages": 0,
        "passages": [
            {
                "ref": "P1",
                "header": "guide/install.rst.md > Login",
                "text": "### [1] guide/install.rst.md > Login\n" + PAGE,
                "group": 1,
            }
        ],
        "rows": [row], "dropped": [],
    }  # fmt: skip
    (folder / "threats.json").write_text(json.dumps(model), encoding="utf-8")
    (folder / "threats.md").write_text("# page\n", encoding="utf-8")
    manifest = {
        "run_id": run_id, "kind": "threats", "status": "finished",
        "created_at": "2026-11-17T10:00:00Z", "finished_at": "2026-11-17T10:03:05Z",
        "product": "p", "writer": "w", "checker": "c", "check": True,
        "story_ids": [story], "stories_total": 1,
        "prompt_set": {"kind": "threats", "name": "x", "tested": True},
        "per_story": [{"story_id": story, "folder": story, "status": "ok", "rows": 1}],
        "failures": [], "not_run": [], "error": None,
    }  # fmt: skip
    if docs_formats:
        manifest["docs_formats"] = {"converted": {".rst": 1}, "skipped": 0}
    storage.write_manifest(run_id, manifest)


async def test_the_run_page_shows_the_tag_and_its_hint(user):
    run_id = "20261117-100000-threats"
    _threat_run(run_id, True)
    await user.open(f"/runs/{run_id}")
    await user.should_see("untested file types")
    tooltips = [e.text for e in user.find(kind=ui.tooltip).elements]
    assert HINT in tooltips


async def test_a_run_without_docs_formats_has_no_tag(user):
    run_id = "20261117-100000-threats"
    _threat_run(run_id, False)
    await user.open(f"/runs/{run_id}")
    await user.should_see("Threat list run")
    await user.should_not_see("untested file types")
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_not_see("untested file types")


async def test_the_threat_list_page_shows_the_tag_and_the_real_file_name(user):
    run_id = "20261117-100000-threats"
    _threat_run(run_id, True)
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_see("untested file types")
    await user.should_see("guide/install.rst")
    await user.should_not_see("install.rst.md")


async def test_the_requirements_page_shows_the_tag_and_the_real_file_name(user):
    from secreqgen_gui import storage

    run_id = "20261117-214500-requirements"
    folder = storage.run_dir(run_id)
    folder.mkdir(parents=True)
    data = {
        "results": [
            {
                "user_story": {"id": "S-1", "title": "T", "story": "As a user."},
                "requirements": [],
                "passages": [
                    {
                        "n": 1,
                        "header": "### [1] guide/install.rst.md > Login",
                        "text": "Text.",
                    }
                ],
            }
        ],
        "failures": [], "model": "m", "tested_prompts": True, "stopped": False,
        "not_run": [],
    }  # fmt: skip
    (folder / "requirements.json").write_text(json.dumps(data), encoding="utf-8")
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id, "kind": "requirements", "status": "finished",
            "docs_formats": {"converted": {".rst": 1}, "skipped": 0},
            "per_story": [],
        },
    )  # fmt: skip
    await user.open(f"/runs/{run_id}/requirements")
    await user.should_see("untested file types")
    await user.should_see("guide/install.rst > Login")


def test_a_passage_name_shows_the_real_file():
    from secreqgen.application.documentation import shown_name

    assert shown_name("guide/install.rst.md") == "guide/install.rst"
    assert shown_name("guide/readme.md") == "guide/readme.md"
