"""Shared widgets show untrusted text as text."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

pytestmark = pytest.mark.anyio

SCRIPT = "<script>alert(1)</script>"
IMG = '<img src=x onerror="document.title=1">'


async def _no_html_elements(user) -> None:
    """The page holds no ui.html, ui.markdown or ui.code element."""
    for kind in (ui.html, ui.markdown, ui.code):
        await user.should_not_see(kind=kind)


async def test_text_shows_script_and_img_as_text(user):
    from secreqgen_gui.widgets import text

    @ui.page("/probe_text")
    def probe() -> None:
        text(SCRIPT)
        text(IMG, "extra")

    await user.open("/probe_text")
    await user.should_see(SCRIPT)
    await user.should_see(IMG)
    await _no_html_elements(user)
    labels = [e for e in user.find(kind=ui.label).elements]
    assert all(e.tag != "script" and e.tag != "img" for e in labels)


async def test_marked_passage_shows_script_and_img_as_text(user):
    from secreqgen_gui.widgets import marked_passage

    body = f"before {SCRIPT} middle {IMG} end"
    start = body.index(IMG)
    spans = [(start, start + len(IMG)), (999, 1200), (3, 3)]

    @ui.page("/probe_passage")
    def probe() -> None:
        marked_passage(body, spans)

    await user.open("/probe_passage")
    await user.should_see(SCRIPT)
    await user.should_see(IMG)
    await _no_html_elements(user)
    pieces = [e.text for e in user.find(kind=ui.label).elements]
    assert sorted(pieces) == sorted(
        [body[: body.index(IMG)], IMG, body[body.index(IMG) + len(IMG) :]]
    )
    marked = [e for e in user.find(kind=ui.label).elements if "sr-mark" in e.classes]
    assert [e.text for e in marked] == [IMG]


def test_marked_spans_are_cleaned():
    from secreqgen_gui.widgets import _clean_spans

    assert _clean_spans("abcdef", [(4, 9), (1, 3), (2, 5)]) == [(1, 3), (3, 5), (5, 6)]
    assert _clean_spans("abc", [(5, 6), (2, 1)]) == []


async def test_tag_empty_state_and_title(user):
    from secreqgen_gui.widgets import empty_state, page_title, tag

    @ui.page("/probe_misc")
    def probe() -> None:
        page_title("A title", "A purpose")
        tag("attack", "caution")
        tag("odd", "unknown kind")
        empty_state("No runs yet.", "Start a threat list", "/threats/new")

    await user.open("/probe_misc")
    for seen in ("A title", "A purpose", "attack", "odd", "No runs yet."):
        await user.should_see(seen)
    await user.should_see("Start a threat list")


async def test_confirm_runs_the_action_only_after_yes(user):
    from secreqgen_gui.widgets import confirm

    done = []

    @ui.page("/probe_confirm")
    def probe() -> None:
        def ask() -> None:
            confirm("Delete it?", "Delete run", lambda: done.append(1))

        ui.button("Ask", on_click=ask)

    await user.open("/probe_confirm")
    user.find("Ask").click()
    await user.should_see("Delete it?")
    assert done == []
    user.find("Delete run").click()
    assert done == [1]


def test_list_folder_error_uses_the_new_text(tmp_path):
    from secreqgen_gui.widgets import _list_folder

    entries, error = _list_folder(tmp_path / "missing", True)
    assert entries == []
    assert error.startswith("This folder cannot be read: ")


def test_the_os_dialog_is_used_only_on_a_local_mac(monkeypatch):
    from secreqgen_gui import settings, widgets

    monkeypatch.delenv(settings.HOST_ENV, raising=False)
    monkeypatch.setattr(widgets.sys, "platform", "darwin")
    monkeypatch.setattr(widgets.shutil, "which", lambda name: "/usr/bin/" + name)
    assert widgets.os_picker_available()
    # Not on another system.
    monkeypatch.setattr(widgets.sys, "platform", "linux")
    assert not widgets.os_picker_available()
    # Not when the GUI listens on a network address: the dialog would open on
    # the screen of the server, not of the person.
    monkeypatch.setattr(widgets.sys, "platform", "darwin")
    monkeypatch.setenv(settings.HOST_ENV, "0.0.0.0")
    assert not widgets.os_picker_available()


def test_the_os_dialog_gets_its_values_as_arguments(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from secreqgen_gui import widgets

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=str(tmp_path / "docs") + "/\n")

    monkeypatch.setattr(widgets.subprocess, "run", fake_run)
    start = tmp_path / 'a "name" with quotes'
    picked = widgets.pick_with_os_dialog(start, "Pick the documentation folder", True)
    assert picked == tmp_path / "docs"
    command = calls[0]
    # No shell. The script is the fixed text. The values follow as arguments.
    assert command[:3] == ["osascript", "-e", widgets.PICK_SCRIPT]
    assert command[3:] == [str(start), "Pick the documentation folder", "folder"]
    assert str(start) not in widgets.PICK_SCRIPT


def test_a_cancelled_os_dialog_gives_nothing(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from secreqgen_gui import widgets

    cancelled = SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(widgets.subprocess, "run", lambda *a, **k: cancelled)
    assert widgets.pick_with_os_dialog(tmp_path, "Pick the stories file", False) is None

    def missing(*args, **kwargs):
        raise OSError("no such program")

    monkeypatch.setattr(widgets.subprocess, "run", missing)
    assert widgets.pick_with_os_dialog(tmp_path, "Pick the stories file", False) is None


def test_a_dialog_starts_in_a_folder_that_exists(tmp_path):
    from secreqgen_gui import widgets

    file = tmp_path / "stories.json"
    file.write_text("{}", encoding="utf-8")
    assert widgets._start_place(str(file), None) == tmp_path
    assert widgets._start_place("", tmp_path) == tmp_path
    assert widgets._start_place(str(tmp_path / "missing"), tmp_path) == tmp_path
    assert widgets._start_place("", None).is_dir()
