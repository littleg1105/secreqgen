"""Small shared parts of the pages.

Text from a model or from the documentation is untrusted. It goes through
text() or marked_passage(). Both build plain text elements. They never use
ui.markdown, ui.code or raw HTML.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from nicegui import run, ui

from secreqgen_gui.settings import (
    expand_path,
    listens_on_this_computer_only,
    show_path,
)

# The file dialog of macOS. The text is fixed. The start folder, the prompt
# and the kind go in as arguments, never as script text.
PICK_SCRIPT = """
on run argv
    set startPath to item 1 of argv
    set promptText to item 2 of argv
    set wantFolder to (item 3 of argv is "folder")
    try
        set startPlace to (POSIX file startPath) as alias
    on error
        set startPlace to (path to home folder)
    end try
    tell me to activate
    if wantFolder then
        set picked to choose folder with prompt promptText default location startPlace
    else
        set picked to choose file with prompt promptText default location startPlace
    end if
    return POSIX path of picked
end run
"""

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


def os_picker_available() -> bool:
    """True when Browse can open the file dialog of the operating system.

    The dialog opens on the screen of the computer that runs the GUI. So it
    is used only on macOS and only when the GUI listens on this computer.
    In every other case Browse shows a list of folders in the page.
    """
    return (
        sys.platform == "darwin"
        and shutil.which("osascript") is not None
        and listens_on_this_computer_only()
    )


def pick_with_os_dialog(start: Path, prompt: str, folder: bool) -> Path | None:
    """Open the file dialog of macOS. Return the path that the person picked.

    None means that the person cancelled, or that the dialog did not open.
    The call waits for the dialog, so a page runs it off the event loop.
    """
    command = ["osascript", "-e", PICK_SCRIPT, str(start), prompt]
    command.append("folder" if folder else "file")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return None
    picked = result.stdout.strip()
    if result.returncode != 0 or not picked:
        return None
    return Path(picked)


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
    """Show an input for a path with a Browse button. Return the input.

    Browse opens the file dialog of the operating system when that is
    possible (see os_picker_available). In every other case it shows a list
    of folders in the page. With no path in the field, the dialog starts in
    the folder start. A hint shows under the field.
    """
    prompt = "Pick the " + label.replace(" (optional)", "").lower()

    async def browse() -> None:
        if not os_picker_available():
            _browse(field, folder, start)
            return
        button.disable()
        try:
            place = _start_place(field.value, start)
            picked = await run.io_bound(pick_with_os_dialog, place, prompt, folder)
        finally:
            button.enable()
        if picked is not None:
            field.set_value(show_path(picked))

    with ui.row().classes("w-full items-start gap-2 no-wrap"):
        field = ui.input(label, value=value).props("outlined dense").classes("grow")
        if hint:
            field.props(f'hint="{hint}"')
        button = ui.button("Browse", on_click=browse).props(
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
