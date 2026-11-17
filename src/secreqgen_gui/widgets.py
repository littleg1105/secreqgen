"""Small shared parts of the pages.

Text from a model or from the documentation is untrusted. It goes through
text() or marked_passage(). Both build plain text elements. They never use
ui.markdown, ui.code or raw HTML.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from nicegui import ui

from secreqgen_gui.settings import expand_path, show_path

TAG_KINDS = ("plain", "keep", "delete", "caution")


def page_title(title: str, purpose: str = "") -> None:
    """Show the title of a page and, under it, one line of purpose."""
    ui.label(title).classes("sr-page-title")
    if purpose:
        ui.label(purpose).classes("sr-purpose")


def tag(text: str, kind: str = "plain") -> None:
    """Show a small plain tag. The kind is plain, keep, delete or caution."""
    if kind not in TAG_KINDS:
        kind = "plain"
    ui.label(text).classes(f"sr-tag sr-tag-{kind}")


def text(value: str, classes: str = "") -> None:
    """Show untrusted text as text. Line breaks stay."""
    ui.label(str(value)).classes(f"whitespace-pre-wrap break-words {classes}".strip())


def _clean_spans(body: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Keep the spans that lie in the text, in order, with no overlap."""
    clean: list[tuple[int, int]] = []
    end = 0
    for start, stop in sorted(spans):
        start = max(start, end)
        stop = min(stop, len(body))
        if start < stop:
            clean.append((start, stop))
            end = stop
    return clean


def marked_passage(body: str, spans: list[tuple[int, int]]) -> None:
    """Show a passage in the source voice with the spans marked.

    The passage is cut into plain text pieces. No piece goes through HTML.
    """
    body = str(body)
    with ui.element("div").classes("sr-source"):
        position = 0
        for start, stop in _clean_spans(body, spans):
            if start > position:
                ui.label(body[position:start])
            ui.label(body[start:stop]).classes("sr-mark")
            position = stop
        if position < len(body) or not body:
            ui.label(body[position:])


def _list_folder(folder: Path, folders_only: bool) -> tuple[list[Path], str]:
    """Return the entries of a folder, folders first, and an error text."""
    try:
        entries = [p for p in folder.iterdir() if not p.name.startswith(".")]
    except OSError as exc:
        return [], f"This folder cannot be read: {exc.strerror or exc}"
    entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    if folders_only:
        entries = [p for p in entries if p.is_dir()]
    return entries, ""


def path_field(
    label: str, value: str = "", *, folder: bool = False, hint: str = ""
):
    """Show an input for a path with a Browse dialog. Return the input.

    The dialog lists the folders of this computer. Its Pick button gives
    the current folder when folder is True. Otherwise a click on a file
    gives that file. A hint shows under the field.
    """
    with ui.row().classes("w-full items-start gap-2 no-wrap"):
        field = ui.input(label, value=value).props("outlined dense").classes("grow")
        if hint:
            field.props(f'hint="{hint}"')
        ui.button("Browse", on_click=lambda: _browse(field, folder)).props(
            "outline no-caps unelevated"
        )
    return field


def _browse(field, folder_mode: bool) -> None:
    start = expand_path(field.value) if (field.value or "").strip() else Path.home()
    if start.is_file():
        start = start.parent
    if not start.is_dir():
        start = Path.home()
    state = {"path": start.resolve()}

    with ui.dialog() as dialog, ui.card().classes("w-[560px] max-w-full"):
        where = ui.label().classes("sr-code break-all")
        problem = ui.label().classes("text-negative")
        box = ui.column().classes("w-full gap-0 h-[320px] overflow-auto")

        def refresh() -> None:
            where.set_text(show_path(state["path"]))
            entries, error = _list_folder(state["path"], folder_mode)
            problem.set_text(error)
            box.clear()
            with box:
                if state["path"].parent != state["path"]:
                    _entry("..", lambda: go(state["path"].parent))
                for entry in entries:
                    if entry.is_dir():
                        _entry(entry.name + os.sep, lambda e=entry: go(e))
                    else:
                        _entry(entry.name, lambda e=entry: choose(e))

        def go(path: Path) -> None:
            state["path"] = path
            refresh()

        def choose(path: Path) -> None:
            field.set_value(show_path(path))
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            if folder_mode:
                ui.button(
                    "Pick this folder", on_click=lambda: choose(state["path"])
                ).props("unelevated no-caps color=primary")
        refresh()
    dialog.open()


def _entry(name: str, on_click: Callable[[], None]) -> None:
    with ui.item(on_click=on_click).props("clickable dense").classes("w-full"):
        with ui.item_section():
            ui.item_label(name)


def confirm(question: str, yes_label: str, on_yes: Callable[[], None]) -> None:
    """Ask a question in a dialog. Run on_yes when the person agrees."""
    with ui.dialog() as dialog, ui.card().classes("w-[420px] max-w-full"):
        ui.label(question)
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")

            def agree() -> None:
                dialog.close()
                on_yes()

            ui.button(yes_label, on_click=agree).props(
                "unelevated no-caps color=primary"
            )
    dialog.open()


def empty_state(message: str, action_label: str = "", target: str = "") -> None:
    """Show a calm message for an empty screen, with one optional action."""
    with ui.column().classes("sr-empty gap-3"):
        ui.label(message)
        if action_label and target:
            ui.button(action_label, on_click=lambda: ui.navigate.to(target)).props(
                "outline no-caps unelevated color=primary"
            )
