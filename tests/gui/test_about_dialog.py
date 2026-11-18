"""The dialog that writes the paragraph about the product, and its tag.

No model runs. The job has a stand-in. The tool makes the paragraph from the
canned result with plain code.
"""

from __future__ import annotations

import asyncio
import json
import threading
from urllib.parse import quote

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from test_context_dialog import RESULT, make_stand_in  # noqa: E402

pytestmark = pytest.mark.anyio

HINT = "Paragraph facts written by a model. Not in the thesis tests."
LONG = "Feature number {n} has ten plain words in this line."


def big_result(count=12, description_path="README.md", dropped=()):
    """Return a result with so many features that some do not fit."""
    items = [
        {"text": LONG.format(n=n), "source": "F1", "path": f"src/f{n}.py"}
        for n in range(1, count + 1)
    ]
    result = json.loads(json.dumps(RESULT))
    result["context"]["name"] = "Acme"
    result["context"]["description"] = "A shop for tools."
    result["context"]["description_path"] = description_path
    result["context"]["features"] = [
        {"group": "orders", "items": items[: count // 2]},
        {"group": "billing_tools", "items": items[count // 2 :]},
    ]
    result["dropped"] = list(dropped)
    return result


@pytest.fixture
def job(monkeypatch):
    """Put a job with a stand-in in place of the shared job."""
    from secreqgen_gui import context_job

    def build(seen=None, **kw):
        inner = make_stand_in(**kw)

        def run_context(options, **more):
            if seen is not None:
                seen.append(options)
            return inner(options, **more)

        fresh = context_job.ContextJob(run_context)
        monkeypatch.setattr(context_job, "writer", fresh)
        return fresh

    return build


class Recorder:
    """A stand-in for the job manager that records the spec of a start."""

    def __init__(self):
        self.threats = []

    def busy(self):
        return False

    def current(self):
        return None

    def get(self, run_id):
        return None

    def start_threats(self, spec):
        self.threats.append(spec)
        return "20261117-100000-threats"


@pytest.fixture
def recorder(monkeypatch):
    from secreqgen_gui import jobs

    fake = Recorder()
    monkeypatch.setattr(jobs, "manager", fake)
    return fake


def _found(user, marker):
    """Return the elements with a marker, in the order of their making."""
    try:
        elements = user.find(marker=marker).elements
    except AssertionError:  # the simulated user fails when it finds nothing
        return []
    return sorted(elements, key=lambda e: e.id)


def _field(user, marker):
    found = _found(user, marker)
    assert found, f"nothing with the marker {marker}"
    return found[0]


def _texts(user, marker):
    return [e.text for e in _found(user, marker)]


def _form_about(user):
    """Return the text field "About the product (optional)" of the form."""
    found = [
        e
        for e in user.find(kind=ui.textarea).elements
        if e.props.get("label") == "About the product (optional)"
    ]
    assert found
    return found[0]


def _stories(tmp_path):
    items = [
        {"id": f"US-00{i}", "title": f"Title {i}", "story": f"As a user, I want {i}."}
        for i in (1, 2)
    ]
    path = tmp_path / "stories.json"
    path.write_text(json.dumps({"project": "Demo", "user_stories": items}))
    return path


def _docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir(exist_ok=True)
    (folder / "page.md").write_text("# Page\nText")
    return folder


async def _open_form(user, tmp_path, remembered=None, about=None):
    """Open the threat list form with stories and docs. Return the code folder."""
    from secreqgen_gui import form_memory

    code = tmp_path / "code"
    code.mkdir(exist_ok=True)
    form_memory.remember("requirements", "context_folder", str(code))
    if about is not None:
        form_memory.remember("threats", "about", about)
    for name, value in (remembered or {}).items():
        form_memory.remember("threats", name, value)
    url = (
        f"/threats/new?stories={quote(str(_stories(tmp_path)))}"
        f"&docs={quote(str(_docs(tmp_path)))}"
    )
    await user.open(url)
    await user.should_see("2 stories found.")
    await user.should_see("1 Markdown file found.")
    return code


async def _open_dialog(user, tmp_path, **kw):
    code = await _open_form(user, tmp_path, **kw)
    user.find(marker="about-write-open").click()
    await user.should_see("Write a paragraph about the product")
    return code


async def _write(user, running):
    user.find(marker="about-write").click()
    for _ in range(50):
        if running.state()["state"] != "running":
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.2)  # one tick of the timer


async def _done(user, tmp_path, job, result=None, **kw):
    running = job(result=result) if result is not None else job()
    await _open_dialog(user, tmp_path, **kw)
    await _write(user, running)
    await user.should_see("About the product")
    return running


def _dialog_open(user):
    return any(d.value for d in user.find(kind=ui.dialog).elements)


# the button and the dialog


async def test_the_button_opens_the_dialog_with_the_folder_and_the_product(
    user, tmp_path, job
):
    job()
    code = await _open_dialog(user, tmp_path)
    await user.should_not_see(
        "The model reads the key files of a codebase and writes a system context: "
        "facts about the product."
    )
    await user.should_not_see(
        "SecReqGen makes the paragraph from these facts, with no model."
    )
    await user.should_see("Codebase folder")
    assert _field(user, "about-folder").props["hint"] == "Top folder of the code"
    await user.should_see("Browse")
    assert _field(user, "about-folder").value == str(code)
    assert _field(user, "about-product").value == "Demo"  # from the form


async def test_the_button_has_the_text_of_the_other_form(user, tmp_path, job):
    job()
    await _open_form(user, tmp_path)
    button = _field(user, "about-write-open")
    assert button.text == "Write one with the model"


async def test_write_with_no_folder_shows_the_message(user, tmp_path, job):
    running = job()
    await _open_dialog(user, tmp_path)
    _field(user, "about-folder").set_value("")
    user.find(marker="about-write").click()
    await user.should_see("Enter the codebase folder.")
    assert running.state()["state"] == "idle"


async def test_write_starts_the_job_with_folder_product_and_writer(user, tmp_path, job):
    from secreqgen_gui.settings import load_settings

    seen = []
    running = job(seen=seen)
    code = await _open_dialog(user, tmp_path)
    _field(user, "about-product").set_value("Acme Chat")
    await _write(user, running)
    assert len(seen) == 1
    assert seen[0].folder == code
    assert seen[0].product == "Acme Chat"
    assert seen[0].writer_model == load_settings().writer_model


async def test_the_folder_goes_to_the_memory_of_the_other_dialog(user, tmp_path, job):
    from secreqgen_gui import form_memory

    job()
    await _open_dialog(user, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _field(user, "about-folder").set_value(str(other))
    assert form_memory.recall("requirements", "context_folder") == str(other)


async def test_the_four_steps_and_their_numbers(user, tmp_path, job):
    gate = threading.Event()
    running = job(gate=gate)
    await _open_dialog(user, tmp_path)
    user.find(marker="about-write").click()
    await asyncio.sleep(1.2)
    for text in (
        "Scan the folder",
        "Write the context",
        "Check each line",
        "Make the paragraph",
        "42 files",
        "try 2 of 3",
    ):
        await user.should_see(text)
    assert _field(user, "about-state-make").text == "waiting"
    assert _field(user, "about-numbers-make").text == ""
    assert _field(user, "about-state-scan").text == "done"
    assert _field(user, "about-state-write").text == "running"
    assert not _field(user, "about-write").enabled
    gate.set()
    running.join(10)
    await asyncio.sleep(1.2)
    paragraph = _field(user, "about-text").value
    assert _field(user, "about-state-check").text == "done"
    assert _field(user, "about-numbers-check").text == "5 of 6 passed"
    assert _field(user, "about-state-make").text == "done"
    assert _field(user, "about-numbers-make").text == f"{len(paragraph.split())} words"


# the draft


async def test_the_paragraph_is_the_rule_for_the_canned_result(user, tmp_path, job):
    await _done(user, tmp_path, job)
    assert (
        _field(user, "about-text").value
        == "Demo: A demo. Main parts: messaging. Features: Users send messages."
    )


async def test_the_paragraph_of_a_long_result_is_the_rule(user, tmp_path, job):
    from secreqgen.context.about import about_paragraph

    result = big_result()
    await _done(user, tmp_path, job, result)
    groups = [
        (g["group"], [i["text"] for i in g["items"]])
        for g in result["context"]["features"]
    ]
    expected, used = about_paragraph("Acme", "A shop for tools.", groups)
    assert used == 7
    assert _field(user, "about-text").value == expected
    assert expected.startswith(
        "Acme: A shop for tools. Main parts: orders, billing tools. Features: "
        "Feature number 1 has ten plain words in this line;"
    )


async def test_the_notes_the_notice_and_the_title(user, tmp_path, job):
    await _done(user, tmp_path, job)
    await user.should_see("2 files that can hold secrets are not read.")
    await user.should_see("Draft. Check each line against its source.")
    await user.should_see("Lines and their sources")
    assert _field(user, "about-counter").text == "10 of 120 words"


async def test_the_list_holds_the_description_and_the_features_of_the_paragraph(
    user, tmp_path, job
):
    result = big_result()
    await _done(user, tmp_path, job, result)
    await user.should_see("description")
    await user.should_see("features: orders")
    await user.should_see("features: billing_tools")
    texts = _texts(user, "about-line-text")
    sources = _texts(user, "about-line-source")
    first = [LONG.format(n=n).rstrip(".") for n in range(1, 8)]
    assert texts == ["A shop for tools."] + [t + "." for t in first]
    assert sources == ["README.md"] + [f"src/f{n}.py" for n in range(1, 8)]
    paragraph = _field(user, "about-text").value
    for text in first:
        assert text in paragraph
    assert LONG.format(n=8).rstrip(".") not in paragraph


async def test_a_description_with_no_source_shows_the_line(user, tmp_path, job):
    await _done(user, tmp_path, job)  # the canned result names no source
    await user.should_see("No source file named")
    assert _texts(user, "about-line-source")[0] == "No source file named"
    assert _texts(user, "about-line-source")[1] == "README.md"


async def test_a_description_with_a_source_shows_the_file(user, tmp_path, job):
    await _done(user, tmp_path, job, big_result())
    await user.should_not_see("No source file named")


async def test_the_left_out_part_shows_only_when_a_feature_did_not_fit(
    user, tmp_path, job
):
    await _done(user, tmp_path, job, big_result())
    await user.should_see("Left out for length (5)")
    texts = _texts(user, "about-left-text")
    assert texts == [LONG.format(n=n) for n in range(8, 13)]
    assert _texts(user, "about-left-source") == [f"src/f{n}.py" for n in range(8, 13)]


async def test_no_left_out_part_when_all_features_fit(user, tmp_path, job):
    await _done(user, tmp_path, job)
    await user.should_not_see("Left out for length")
    assert _found(user, "about-left-out") == []


async def test_the_failed_part_shows_only_when_the_check_removed_a_line(
    user, tmp_path, job
):
    await _done(user, tmp_path, job)  # the canned result has one removed line
    await user.should_see("Failed the check (1)")
    await user.should_see("Kafka")
    await user.should_see("not in the files")
    await user.should_not_see("Not in the draft")


async def test_no_failed_part_when_nothing_was_removed(user, tmp_path, job):
    await _done(user, tmp_path, job, big_result(dropped=()))
    await user.should_not_see("Failed the check")


# the button "Use this paragraph" and the counter


async def test_the_button_is_off_for_an_empty_box_and_for_121_words(
    user, tmp_path, job
):
    await _done(user, tmp_path, job)
    use = _field(user, "about-use")
    assert use.enabled
    area = _field(user, "about-text")
    area.set_value("")
    assert not use.enabled
    assert _field(user, "about-counter").text == "0 of 120 words"
    area.set_value(" ".join(["word"] * 121))
    assert not use.enabled
    assert _field(user, "about-counter").text == (
        "121 of 120 words. Shorten the paragraph."
    )
    assert "text-negative" in _field(user, "about-counter").classes
    area.set_value(" ".join(["word"] * 120))
    assert use.enabled
    assert _field(user, "about-counter").text == "120 of 120 words"
    assert "text-negative" not in _field(user, "about-counter").classes


async def test_the_line_under_the_button_shows_only_when_the_form_has_text(
    user, tmp_path, job
):
    await _done(user, tmp_path, job)
    assert _found(user, "about-replaces") == []
    await user.should_not_see("Replaces the text in the form.")


async def test_the_line_under_the_button_shows_when_the_form_has_text(
    user, tmp_path, job
):
    await _done(user, tmp_path, job, about="Old text of the form.")
    await user.should_see("Replaces the text in the form.")
    await user.should_see("Close")
    await user.should_see("Use this paragraph")


async def test_use_puts_the_folded_text_into_the_form_and_closes_the_dialog(
    user, tmp_path, job
):
    from secreqgen_gui import form_memory, storage

    await _done(user, tmp_path, job)
    _field(user, "about-text").set_value("  The paragraph\n  was   changed. ")
    user.find(marker="about-use").click()
    await asyncio.sleep(1.0)  # the form writes the file of the paragraph
    assert _form_about(user).value == "The paragraph was changed."
    await user.should_see("4 of 120 words")
    assert not _dialog_open(user)
    assert form_memory.recall("threats", "about") == "The paragraph was changed."
    assert form_memory.recall("threats", "about_generated") is True
    assert storage.about_path("The paragraph was changed.").is_file()
    # The record keeps the text as the person left it, and the result.
    files = list(storage.paragraphs_dir().iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("Demo-") and files[0].suffix == ".json"
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["paragraph"] == "  The paragraph\n  was   changed. "
    assert record["result"]["folder_name"] == "demo"
    assert record["result"]["context"]["description"] == "A demo."


async def test_a_record_that_cannot_be_written_does_not_stop_use(
    user, tmp_path, job, monkeypatch
):
    from secreqgen_gui import storage

    def refuse(*_args, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "save_about_draft", refuse)
    await _done(user, tmp_path, job)
    user.find(marker="about-use").click()
    await asyncio.sleep(1.0)
    assert (
        _form_about(user).value
        == "Demo: A demo. Main parts: messaging. Features: Users send messages."
    )
    assert not _dialog_open(user)


async def test_a_job_that_fails_shows_its_message(user, tmp_path, job):
    running = job(error=ValueError("boom"))
    await _open_dialog(user, tmp_path)
    await _write(user, running)
    await user.should_see("Unexpected error. ValueError: boom")
    assert _field(user, "about-write").enabled
    assert _found(user, "about-text") == []


async def test_a_result_with_no_context_shows_a_message(user, tmp_path, job):
    result = {"folder_name": "demo", "facts": {}, "dropped": [], "yaml": ""}
    running = job(result=result)
    await _open_dialog(user, tmp_path)
    await _write(user, running)
    await user.should_see("The result has no system context.")
    assert _found(user, "about-text") == []
    assert _field(user, "about-state-make").text == "failed"
    await asyncio.sleep(1.2)  # the next tick keeps the message
    await user.should_see("The result has no system context.")


async def test_the_dialog_says_when_a_run_is_in_progress(user, tmp_path, job):
    from secreqgen_gui import jobs

    job()
    jobs.manager._state = jobs.RunState(
        id="20261117-100000-threats", kind="threats", state="running", message=""
    )
    await _open_dialog(user, tmp_path)
    await asyncio.sleep(1.2)
    await user.should_see("Another run is in progress. Wait for it to end.")
    assert not _field(user, "about-write").enabled


async def test_a_new_dialog_shows_no_old_job(user, tmp_path, job):
    running = job()
    await _open_dialog(user, tmp_path)
    await _write(user, running)
    await user.should_see("Use this paragraph")
    old_boxes = len(_found(user, "about-text"))
    user.find("Close").click()
    await asyncio.sleep(0.3)
    user.find(marker="about-write-open").click()
    await asyncio.sleep(1.2)
    assert len(_found(user, "about-text")) == old_boxes  # no new draft
    assert _found(user, "about-write")[-1].enabled


# the command block


def _command_texts(user):
    box = _found(user, "about-command")
    if not box:
        return []
    return [
        e.text
        for e in box[0].descendants()
        if isinstance(e, ui.label) and "generate about" in e.text
    ]


async def test_the_dialog_shows_the_command_for_its_fields(user, tmp_path, job):
    job()
    code = await _open_dialog(user, tmp_path)
    await asyncio.sleep(0.3)
    product = _field(user, "about-product")
    product.set_value("")
    await asyncio.sleep(0.3)
    # The model is always in the command, also the tested one.
    assert _command_texts(user) == [
        f"secreqgen generate about {code} --model gemma4:26b"
    ]
    product.set_value("Acme Chat")
    await asyncio.sleep(0.3)
    assert _command_texts(user) == [
        f"secreqgen generate about {code} --model gemma4:26b --product 'Acme Chat'"
    ]
    _field(user, "about-folder").set_value("")
    await asyncio.sleep(0.3)
    assert _command_texts(user) == []


async def test_the_command_follows_the_settings(user, tmp_path, job):
    import dataclasses

    from secreqgen_gui import jobs
    from secreqgen_gui.settings import save_settings

    job()
    code = await _open_dialog(user, tmp_path)
    await asyncio.sleep(0.3)
    # The default address: no variable in front.
    assert _command_texts(user)[0].startswith("secreqgen generate about ")
    save_settings(
        dataclasses.replace(jobs.load_settings(), base_url="http://10.1.1.1:11434")
    )
    await asyncio.sleep(1.3)  # the dialog looks two times a second
    assert _command_texts(user)[0].startswith(
        "SECREQGEN_LLM_BASE_URL=http://10.1.1.1:11434 secreqgen generate about "
    )
    assert str(code) in _command_texts(user)[0]


# the mark and the tag


async def _use(user, tmp_path, job, text=None):
    await _done(user, tmp_path, job)
    if text is not None:
        _field(user, "about-text").set_value(text)
    user.find(marker="about-use").click()
    await asyncio.sleep(1.0)


async def _start(user, recorder):
    user.find("Start threat list").click()
    await asyncio.sleep(0.4)
    return recorder.threats[-1]


async def test_a_run_after_use_has_the_mark_also_when_the_person_changed_a_word(
    user, tmp_path, job, recorder
):
    await _use(user, tmp_path, job)
    area = _form_about(user)
    area.set_value(area.value + " One more word.")
    await asyncio.sleep(0.4)
    spec = await _start(user, recorder)
    assert spec.about.endswith("One more word.")
    assert spec.about_generated is True


async def test_a_paragraph_that_the_person_typed_has_no_mark(
    user, tmp_path, job, recorder
):
    await _open_form(user, tmp_path)
    _form_about(user).set_value("A paragraph that I typed.")
    await asyncio.sleep(0.4)
    spec = await _start(user, recorder)
    assert spec.about == "A paragraph that I typed."
    assert spec.about_generated is False


async def test_the_mark_goes_when_the_field_is_empty(user, tmp_path, job, recorder):
    from secreqgen_gui import form_memory

    await _use(user, tmp_path, job)
    assert form_memory.recall("threats", "about_generated") is True
    area = _form_about(user)
    area.set_value("")
    await asyncio.sleep(0.4)
    assert form_memory.recall("threats", "about_generated") is None
    area.set_value("A text that I typed after I emptied the field.")
    await asyncio.sleep(0.4)
    spec = await _start(user, recorder)
    assert spec.about_generated is False


async def test_the_mark_survives_a_reload_of_the_form(user, tmp_path, job, recorder):
    await _use(user, tmp_path, job)
    await user.open(
        f"/threats/new?stories={quote(str(tmp_path / 'stories.json'))}"
        f"&docs={quote(str(tmp_path / 'docs'))}"
    )
    await user.should_see("2 stories found.")
    await user.should_see("1 Markdown file found.")
    await asyncio.sleep(0.6)
    spec = await _start(user, recorder)
    assert spec.about.startswith("Demo: A demo.")
    assert spec.about_generated is True


async def test_clear_the_form_removes_the_mark(user, tmp_path, job, recorder):
    from secreqgen_gui import form_memory

    await _use(user, tmp_path, job)
    user.find("Clear the form").click()
    await asyncio.sleep(0.4)
    assert form_memory.recall("threats", "about_generated") is None
    assert _form_about(user).value == ""
    _form_about(user).set_value("A new text of the person.")
    await asyncio.sleep(0.4)
    assert form_memory.recall("threats", "about_generated") is None


async def test_a_stale_mark_with_no_text_is_dropped(user, tmp_path, job, recorder):
    from secreqgen_gui import form_memory

    await _open_form(user, tmp_path, remembered={"about_generated": True})
    assert form_memory.recall("threats", "about_generated") is None
    _form_about(user).set_value("A text that I typed.")
    await asyncio.sleep(0.4)
    assert (await _start(user, recorder)).about_generated is False


def _spec(tmp_path, **kw):
    from secreqgen_gui.jobs import ThreatRunSpec

    return ThreatRunSpec(tmp_path / "s.json", tmp_path, **kw)


def _manifest(tmp_path, spec):
    from secreqgen_gui.jobs import JobManager
    from secreqgen_gui.settings import load_settings

    manager = JobManager()
    manager._settings = load_settings()
    return manager._base_manifest("20261117-100000-threats", "threats", spec)


def test_the_manifest_has_the_flag_for_a_generated_paragraph(tmp_path):
    flag = _manifest(tmp_path, _spec(tmp_path, about="A text.", about_generated=True))
    assert flag["generated_about"] is True
    own = _manifest(tmp_path, _spec(tmp_path, about="A text."))
    assert "generated_about" not in own
    none = _manifest(tmp_path, _spec(tmp_path, about_generated=True))
    assert "generated_about" not in none  # no paragraph: no flag
    assert "generated_about" not in _manifest(tmp_path, _spec(tmp_path))


def test_the_command_does_not_change_with_the_mark(tmp_path):
    from secreqgen_gui import jobs

    def command(generated):
        return jobs.command_for_threats(
            _spec(tmp_path, about="A text.", about_generated=generated),
            total_stories=2,
            default_product=None,
            about_file=tmp_path / "about.txt",
            base_url="http://127.0.0.1:11434",
        ).text

    assert command(True) == command(False)


def _threat_run(run_id, **extra):
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
                "ref": "P1", "header": "guide/page.md > Login",
                "text": "### [1] guide/page.md > Login\nUsers must log in.\n",
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
    manifest.update(extra)
    storage.write_manifest(run_id, manifest)


async def test_the_run_page_and_the_threat_list_page_show_the_tag(user):
    run_id = "20261117-100000-threats"
    _threat_run(run_id, generated_about=True)
    await user.open(f"/runs/{run_id}")
    await user.should_see("generated paragraph")
    assert HINT in [e.text for e in user.find(kind=ui.tooltip).elements]
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_see("generated paragraph")
    assert HINT in [e.text for e in user.find(kind=ui.tooltip).elements]


async def test_the_tag_sits_in_the_row_of_the_other_tags(user):
    run_id = "20261117-100000-threats"
    _threat_run(run_id, generated_about=True, docs_formats={"converted": {".rst": 1}})
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_see("generated paragraph")
    await user.should_see("untested file types")
    tags = [
        _field(user, "tag-file-types").parent_slot.parent,
        _field(user, "tag-generated-about").parent_slot.parent,
    ]
    assert tags[0] is tags[1]


async def test_a_run_without_the_flag_has_no_tag(user):
    run_id = "20261117-100000-threats"
    _threat_run(run_id)
    await user.open(f"/runs/{run_id}")
    await user.should_see("Threat list run")
    await user.should_not_see("generated paragraph")
    await user.open(f"/runs/{run_id}/stories/MM-US-007")
    await user.should_not_see("generated paragraph")


# the pieces


def test_the_record_goes_to_the_folder_paragraphs():
    from secreqgen_gui import storage

    first = storage.save_about_draft("A text.", {"a": 1}, "../../x")
    assert first.parent == storage.paragraphs_dir()
    assert storage.paragraphs_dir().name == "paragraphs"
    assert "/" not in first.name
    assert first.name.startswith("x-") and first.suffix == ".json"
    second = storage.save_about_draft("Another.", {"a": 2}, "../../x")
    assert second != first  # a file that exists is never replaced
    assert json.loads(first.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "paragraph": "A text.",
        "result": {"a": 1},
    }
    nameless = storage.save_about_draft("A text.", {}, "")
    assert nameless.name.startswith("paragraph-")


def test_the_counter_text_is_the_text_of_the_form():
    from secreqgen_gui.pages.about_dialog import count_words, counter_text

    assert counter_text(72) == "72 of 120 words"
    assert counter_text(120) == "120 of 120 words"
    assert counter_text(131) == "131 of 120 words. Shorten the paragraph."
    assert count_words(None) == 0
    assert count_words("  a  b\nc ") == 3


def test_the_tag_function_needs_the_flag():
    from secreqgen_gui.widgets import about_tag

    # Nothing is drawn without the flag, so no page context is needed.
    about_tag(None)
    about_tag({})
    about_tag({"generated_about": False})


# one job, two dialogs: a job of the other dialog is not shown as a draft


async def test_a_job_of_the_other_dialog_is_not_shown_as_a_paragraph_draft(
    user, tmp_path, job
):
    from secreqgen_gui import form_memory

    gate = threading.Event()
    running = job(gate=gate)
    code = tmp_path / "code"
    code.mkdir()
    form_memory.remember("requirements", "context_folder", str(code))
    # The other dialog starts the job (the call that it makes).
    running.start(code, None, "m:1")
    await asyncio.sleep(0.3)
    assert running.state()["state"] == "running"
    await _open_dialog(user, tmp_path)
    gate.set()
    running.join(10)
    await asyncio.sleep(1.4)
    assert running.state()["state"] == "done"
    assert not _found(user, "about-text")
    assert not _found(user, "about-use")


def test_the_job_knows_which_dialog_started_it(tmp_path, job):
    from secreqgen_gui.pages import about_dialog, context_dialog

    running = job()
    assert running.state()["owner"] == ""
    running.start(tmp_path, None, "m:1")  # the call of the other dialog
    assert running.state()["owner"] == context_dialog.OWNER
    running.join(10)
    running.start(tmp_path, None, "m:1", about_dialog.OWNER)
    assert running.state()["owner"] == about_dialog.OWNER
    running.join(10)
    assert about_dialog.OWNER != context_dialog.OWNER


async def test_a_paragraph_job_is_not_shown_as_a_system_context_draft(
    user, tmp_path, job
):
    # The other direction: the paragraph dialog started the job. The dialog
    # "Write a system context" opens while it runs and must stay empty.
    from secreqgen_gui.pages import about_dialog

    gate = threading.Event()
    running = job(gate=gate)
    running.start(tmp_path, None, "m:1", about_dialog.OWNER)
    await asyncio.sleep(0.3)
    assert running.state()["state"] == "running"
    await user.open("/requirements/new")
    user.find("Write one with the model").click()
    await user.should_see("Write a system context")
    gate.set()
    running.join(10)
    await asyncio.sleep(1.4)
    assert running.state()["state"] == "done"
    assert not _found(user, "context-yaml")
    assert not _found(user, "context-save")
