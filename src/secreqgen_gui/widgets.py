"""Small shared parts of the pages.

Text from a model or from the documentation is untrusted. It goes through
text() or marked_passage(). Both build plain text elements. They never use
ui.markdown, ui.code or raw HTML.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
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


FORMATS_HINT = "Files converted to Markdown. Not in the thesis tests."


def converted_files(manifest: dict | None) -> int:
    """Return the number of files that the run converted or will convert."""
    formats = manifest.get("docs_formats") if isinstance(manifest, dict) else None
    converted = formats.get("converted") if isinstance(formats, dict) else None
    if not isinstance(converted, dict):
        return 0
    total = 0
    for value in converted.values():
        try:
            total += int(value)
        except (TypeError, ValueError):
            continue
    return total


def has_mirror_names(manifest: dict | None) -> bool:
    """Say if a run can be stored with the names of mirror files (x.rst.md).

    Only a run that converted files can have such names. A run that converted
    nothing shows every file name as it is.
    """
    return converted_files(manifest) > 0


def formats_tag(manifest: dict | None) -> None:
    """Show the tag "untested file types" when the run converted files."""
    if not manifest or not converted_files(manifest):
        return
    with ui.element("span").mark("tag-file-types"):
        tag("untested file types", "caution")
        ui.tooltip(FORMATS_HINT)


CONTEXT_HINT = "System context written by a model. Not in the thesis tests."


def context_tag(manifest: dict | None) -> None:
    """Show the tag "generated context" when a model wrote the context."""
    if not manifest or not manifest.get("generated_context"):
        return
    with ui.element("span").mark("tag-generated-context"):
        tag("generated context", "caution")
        ui.tooltip(CONTEXT_HINT)


ABOUT_HINT = "Paragraph facts written by a model. Not in the thesis tests."


def about_tag(manifest: dict | None) -> None:
    """Show the tag "generated paragraph" when a model wrote the facts."""
    if not manifest or not manifest.get("generated_about"):
        return
    with ui.element("span").mark("tag-generated-about"):
        tag("generated paragraph", "caution")
        ui.tooltip(ABOUT_HINT)


COMMAND_LABEL = "Command"
COMMAND_HINT = "The same run, for a terminal"
COPY_LABEL = "Copy"
COPIED = "Copied."


class CommandBlock:
    """Show the command of a run, a link that copies it and its differences.

    The command and the lines are plain text elements. Call show() each time
    the command can change. With titled False the block has no label: the
    caller shows the title, for example as the head of an expansion.
    """

    def __init__(self, *, titled: bool = True, mark: str = "command-block") -> None:
        self.text = ""
        self._lines: tuple[str, ...] = ()
        self.box = ui.column().classes("w-full gap-1 min-w-0").mark(mark)
        with self.box:
            with ui.row().classes("items-baseline gap-3"):
                if titled:
                    with ui.label(COMMAND_LABEL).classes("sr-small"):
                        ui.tooltip(COMMAND_HINT)
                ui.button(COPY_LABEL, on_click=self.copy).props(
                    "flat dense no-caps"
                ).classes("sr-link-button").mark("command-copy")
            self.code = (
                ui.label()
                # A line breaks at a space. Only a word that is longer than the
                # line breaks inside, so the page never gets wider.
                .classes("sr-code whitespace-pre-wrap w-full")
                .style("overflow-wrap:anywhere;min-width:0")
                .mark("command-text")
            )
            self.notes = ui.column().classes("w-full gap-0")
        self.box.set_visibility(False)

    def copy(self) -> None:
        """Copy the text of the command and say so."""
        if self.text:
            ui.clipboard.write(self.text)
            ui.notify(COPIED)

    def show(self, command, lines: Sequence[str] = ()) -> None:
        """Show a command with its lines. None hides the block."""
        if command is None:
            self.text = ""
            self.box.set_visibility(False)
            return
        text = str(command.text)
        if text != self.text:
            self.text = text
            self.code.set_text(text)
        shown = tuple(lines)
        if shown != self._lines:
            self._lines = shown
            self.notes.clear()
            with self.notes:
                for line in shown:
                    ui.label(line).classes("sr-small").style(
                        "color: var(--sr-caution)"
                    ).mark("command-note")
        self.box.set_visibility(True)


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


def _start_place(value: str, fallback: Path | None) -> Path:
    """Return the folder where a dialog starts."""
    place = expand_path(value) if (value or "").strip() else fallback
    if place is not None and place.is_file():
        place = place.parent
    if place is None or not place.is_dir():
        place = fallback if fallback is not None and fallback.is_dir() else None
    return place or Path.home()


def path_field(
    label: str,
    value: str = "",
    *,
    folder: bool = False,
    hint: str = "",
    start: Path | None = None,
):
    """Show an input for a path with a Browse dialog. Return the input.

    The dialog lists the folders of the computer that runs the GUI. So it
    works the same way on every system and in a container. Its Pick button
    gives the current folder when folder is True. Otherwise a click on a
    file gives that file. With no path in the field, the dialog starts in
    the folder start. A hint shows under the field.
    """
    with ui.row().classes("w-full items-start gap-2 no-wrap"):
        field = ui.input(label, value=value).props("outlined dense").classes("grow")
        if hint:
            field.props(f'hint="{hint}"')
        ui.button("Browse", on_click=lambda: _browse(field, folder, start)).props(
            "outline no-caps unelevated"
        )
    return field


def _browse(field, folder_mode: bool, start: Path | None = None) -> None:
    state = {"path": _start_place(field.value, start).resolve()}

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
