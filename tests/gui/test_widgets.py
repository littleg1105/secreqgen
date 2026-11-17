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
