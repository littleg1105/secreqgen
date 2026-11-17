"""The prompt sets: the list, and one set to view or edit."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui
from secreqgen.application.errors import InputError
from secreqgen.application.prompt_sets import (
    PromptSetInfo,
    copy_prompt_set,
    delete_user_prompt_set,
    list_user_prompt_sets,
    tested_prompt_sets,
    write_prompt_file,
)

from secreqgen_gui import storage
from secreqgen_gui.widgets import confirm, empty_state, page_title, tag, text

PARTS = (("threats", "Threat list"), ("requirements", "Requirements"))
KINDS = tuple(kind for kind, _ in PARTS)
TESTED = "tested"
NAME_RULE = (
    "Small letters, digits, hyphen and underscore. 40 characters at most."
)
NOT_TESTED_LINE = "A run with this prompt set gets the label: Not the tested setup."
TESTED_LINE = "Read only. This is the setup that the tests of the thesis used."


def _tested_info(kind: str) -> PromptSetInfo | None:
    for info in tested_prompt_sets():
        if info.kind == kind:
            return info
    return None


def _user_info(kind: str, name: str) -> PromptSetInfo | None:
    for info in list_user_prompt_sets(storage.prompt_sets_dir(), kind):
        if info.name == name:
            return info
    return None


def _placeholder_text(names: tuple[str, ...]) -> str:
    return ", ".join("$" + n for n in names) if names else "none"


def _hash_label(value: str) -> None:
    """Show the first 16 characters of a hash. The full value is a tooltip."""
    with ui.label(value[:16]).classes("sr-code"):
        ui.tooltip(value)


def _section(title: str) -> None:
    ui.label(title).classes("sr-section-title")


# ---------------------------------------------------------------- delete


def _ask_delete(kind: str, name: str, after) -> None:
    def really_delete() -> None:
        try:
            delete_user_prompt_set(kind, name, storage.prompt_sets_dir())
        except InputError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify("Prompt set deleted")
        after()

    confirm(f"Delete the prompt set {name}?", "Delete prompt set", really_delete)


# ---------------------------------------------------------------- copy


def _open_copy_dialog(kind: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-[440px] max-w-full gap-3"):
        field = ui.input("Name of your prompt set").props("outlined dense autofocus")
        field.classes("w-full")
        field.mark("copy-name")
        ui.label(NAME_RULE).classes("sr-small")
        problem = ui.label().classes("text-negative")
        problem.mark("copy-error")

        def do_copy() -> None:
            name = (field.value or "").strip()
            try:
                copy_prompt_set(kind, name, storage.prompt_sets_dir())
            except InputError as exc:
                problem.set_text(str(exc))
                return
            dialog.close()
            ui.navigate.to(f"/prompts/{kind}/{name}")

        field.on("keydown.enter", do_copy)
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Copy", on_click=do_copy).props(
                "unelevated no-caps color=primary"
            ).mark("copy-go")
    dialog.open()


# ---------------------------------------------------------------- list


def _file_rows(info: PromptSetInfo) -> None:
    with ui.column().classes("gap-1"):
        for file in info.files:
            with ui.row().classes("items-baseline gap-4 no-wrap"):
                ui.label(file.name).classes("sr-code w-[220px]")
                _hash_label(file.sha256)
                if not file.used:
                    ui.label("not used in version 1").classes("sr-small")


def _tested_row(info: PromptSetInfo) -> None:
    with ui.column().classes("w-full gap-2 py-4 border-b border-[#D8DEE4]"):
        with ui.row().classes("w-full items-center gap-3"):
            ui.label("Tested setup").classes("font-semibold")
            tag("tested", "keep")
            ui.space()
            view_target = f"/prompts/{info.kind}/{TESTED}"
            view = ui.button("View", on_click=lambda: ui.navigate.to(view_target))
            view.props("flat no-caps color=primary").mark(f"view-{info.kind}")
            ui.button(
                "Copy to edit", on_click=lambda: _open_copy_dialog(info.kind)
            ).props("outline no-caps color=primary").mark(f"copy-{info.kind}")
        _file_rows(info)


def _user_row(info: PromptSetInfo, refresh) -> None:
    with ui.row().classes("w-full items-center gap-3 py-3 border-b border-[#D8DEE4]"):
        text(info.name, "font-semibold")
        tag("not tested", "caution")
        ui.space()
        ui.button(
            "Edit", on_click=lambda: ui.navigate.to(f"/prompts/{info.kind}/{info.name}")
        ).props("flat no-caps color=primary").mark(f"edit-{info.kind}-{info.name}")
        ui.button(
            "Delete", on_click=lambda: _ask_delete(info.kind, info.name, refresh)
        ).props("flat no-caps color=negative").mark(f"delete-{info.kind}-{info.name}")


def page() -> None:
    page_title(
        "Prompt sets",
        "The texts that the models get. The tested sets cannot be changed. "
        "Copy one to make your own.",
    )

    @ui.refreshable
    def parts() -> None:
        for kind, title in PARTS:
            with ui.column().classes("w-full gap-0 mt-8"):
                _section(title)
                tested = _tested_info(kind)
                if tested is not None:
                    _tested_row(tested)
                for info in list_user_prompt_sets(storage.prompt_sets_dir(), kind):
                    _user_row(info, parts.refresh)

    with ui.column().classes("w-full gap-0"):
        parts()


# ---------------------------------------------------------------- one set


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return f"This file cannot be read: {exc}"


def _tested_page(info: PromptSetInfo) -> None:
    page_title("Tested setup", TESTED_LINE)
    for file in info.files:
        with ui.column().classes("w-full gap-2 mt-8 pt-4 border-t border-[#D8DEE4]"):
            ui.label(file.name).classes("sr-section-title")
            with ui.row().classes("items-baseline gap-3"):
                ui.label("sha256").classes("sr-small")
                ui.label(file.sha256).classes("sr-code break-all")
            ui.label(f"Placeholders: {_placeholder_text(file.placeholders)}").classes(
                "sr-code"
            )
            body = _read_text(info.folder / file.name)
            text(
                body,
                "sr-code p-3 w-full bg-[#F4F6F8] border border-[#D8DEE4] rounded",
            )


def _editor_tab(info: PromptSetInfo, filename: str, required: tuple[str, ...]) -> None:
    original = _read_text(info.folder / filename)
    with ui.column().classes("w-full gap-3 pt-3"):
        editor = ui.codemirror(
            original, language=None, theme="basicLight", line_wrapping=True
        ).classes("w-full h-[420px] sr-code border border-[#D8DEE4]")
        editor.mark(f"editor-{filename}")
        ui.label(
            f"Placeholders that this file must hold: {_placeholder_text(required)}"
        ).classes("sr-code")
        problems = ui.column().classes("gap-1")
        problems.mark(f"problems-{filename}")

        def save() -> None:
            try:
                messages = write_prompt_file(
                    info.folder, info.kind, filename, editor.value or ""
                )
            except InputError as exc:
                messages = [f"Error: {exc}"]
            problems.clear()
            with problems:
                for message in messages:
                    hard = message.startswith("Error: ")
                    ui.label(message).classes(
                        "text-negative" if hard else "text-warning"
                    )
            if not any(m.startswith("Error: ") for m in messages):
                ui.notify("File saved")

        ui.button("Save file", on_click=save).props(
            "unelevated no-caps color=primary"
        ).mark(f"save-{filename}")


def _user_page(info: PromptSetInfo, tested: PromptSetInfo) -> None:
    required = {f.name: f.placeholders for f in tested.files}
    with ui.row().classes("items-center gap-3"):
        ui.label(info.name).classes("sr-page-title")
        tag("not tested", "caution")
    ui.label(NOT_TESTED_LINE).classes("sr-purpose")
    names = [f.name for f in info.files]
    with ui.column().classes("w-full gap-0 mt-6"):
        with ui.tabs().props("align=left no-caps").classes("w-full") as tabs:
            tab_items = {n: ui.tab(n) for n in names}
        with ui.tab_panels(tabs, value=tab_items[names[0]] if names else None).classes(
            "w-full"
        ):
            for filename in names:
                with ui.tab_panel(tab_items[filename]).classes("p-0"):
                    _editor_tab(info, filename, required.get(filename, ()))
    with ui.row().classes("w-full mt-8 pt-4 border-t border-[#D8DEE4]"):
        ui.button(
            "Delete this prompt set",
            on_click=lambda: _ask_delete(
                info.kind, info.name, lambda: ui.navigate.to("/prompts")
            ),
        ).props("outline no-caps color=negative").mark("delete-set")


def _missing() -> None:
    empty_state("This prompt set does not exist.", "Open the prompt sets", "/prompts")


def set_page(kind: str, name: str) -> None:
    tested = _tested_info(kind) if kind in KINDS else None
    if tested is None:
        _missing()
        return
    if name == TESTED:
        _tested_page(tested)
        return
    info = _user_info(kind, name)
    if info is None or not info.files:
        _missing()
        return
    _user_page(info, tested)
