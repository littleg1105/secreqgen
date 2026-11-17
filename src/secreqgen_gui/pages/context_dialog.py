"""The dialog that writes a system context with the model.

The text of the draft comes from a model. It is shown with ui.label and in
the value of a text field. It never goes through markdown or HTML.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from nicegui import run, ui

from secreqgen_gui import context_job, form_memory, jobs, storage
from secreqgen_gui.form_memory import REQUIREMENTS
from secreqgen_gui.settings import expand_path, load_settings, show_path
from secreqgen_gui.widgets import path_field

BUTTON = "Write one with the model"
TITLE = "Write a system context"
INTRO = "The model reads the key files of a codebase and writes a draft."
INTRO_2 = "You check it before you use it."
FOLDER_HINT = "The top folder of the product's code"
PRODUCT_LABEL = "Product name (optional)"
BUSY = "Another run is in progress. Wait for it to end, or stop it."
NOTICE = "This is a draft. Check each line against its source before you use it."
NO_FOLDER = "Enter the codebase folder."
BAD_YAML = "This is not valid YAML. Correct it, then save again."
SAVED = "Saved."
LINES_TITLE = "Lines and their sources"
CAUTION = "color: var(--sr-caution)"


def source_rows(context: dict) -> list[tuple[str, str, str]]:
    """Return (place, text, file path) for each line of a context dict."""
    rows: list[tuple[str, str, str]] = []
    for item in context.get("tech_stack") or []:
        text = f"{item.get('kind', '')}: {item.get('value', '')}"
        rows.append(("tech_stack", text, str(item.get("path", ""))))
    for group in context.get("features") or []:
        place = f"features: {group.get('group', '')}"
        for item in group.get("items") or []:
            rows.append((place, str(item.get("text", "")), str(item.get("path", ""))))
    for level, items in (context.get("data_sensitivity") or {}).items():
        place = f"data_sensitivity: {level}"
        for item in items or []:
            rows.append((place, str(item.get("text", "")), str(item.get("path", ""))))
    return rows


def numbers_text(stage: str, numbers: dict) -> str:
    """Return the numbers of a stage as a short text. Empty: none yet."""
    try:
        if stage == "scan" and "files" in numbers:
            return f"{numbers['files']} files, {numbers['parts']} parts"
        if stage == "write" and numbers.get("attempt"):
            return f"attempt {numbers['attempt']}"
        if stage == "check" and "kept" in numbers:
            return f"{numbers['kept']} kept, {numbers['dropped']} removed"
    except KeyError:
        return ""
    return ""


def files_line(result: dict) -> str:
    """Return the line about the scan of the codebase."""
    facts = result.get("facts") or {}
    line = f"{facts.get('files_seen', 0)} files scanned."
    secret = int(facts.get("secret_files_skipped") or 0)
    if secret:
        line += f" {secret} secret files were not read."
    return line


def open_context_dialog(
    folder: str, product: str, on_saved: Callable[[str], None]
) -> None:
    """Open the dialog. on_saved gets the short path of the saved file."""
    shown = {"token": None}
    with ui.dialog() as dialog, ui.card().classes("w-[760px] max-w-full gap-3"):
        ui.label(TITLE).classes("sr-section-title")
        with ui.column().classes("gap-0"):
            ui.label(INTRO).classes("sr-small")
            ui.label(INTRO_2).classes("sr-small")
        folder_field = path_field(
            "Codebase folder", folder, folder=True, hint=FOLDER_HINT
        ).mark("context-folder")
        product_field = (
            ui.input(PRODUCT_LABEL, value=product)
            .props("outlined dense")
            .classes("w-full")
            .mark("context-product")
        )
        with ui.row().classes("items-center gap-4"):
            write_button = (
                ui.button("Write")
                .props("unelevated no-caps color=primary")
                .mark("context-write")
            )
            busy_line = ui.label(BUSY).classes("sr-small").style(CAUTION)
        error_line = ui.label().classes("text-negative").mark("context-error")
        stage_rows: dict[str, tuple[ui.label, ui.label]] = {}
        steps = ui.column().classes("w-full gap-1")
        with steps:
            for name in context_job.STAGES:
                with ui.row().classes("items-baseline gap-3 no-wrap"):
                    ui.label(context_job.STAGE_NAMES[name]).classes("w-[170px]")
                    state_label = ui.label().classes("sr-small w-[70px]")
                    numbers_label = ui.label().classes("sr-small")
                stage_rows[name] = (state_label, numbers_label)
        steps.set_visibility(False)
        result_box = ui.column().classes("w-full gap-3")

        def remember_folder() -> None:
            form_memory.remember(
                REQUIREMENTS, "context_folder", (folder_field.value or "").strip()
            )

        folder_field.on_value_change(lambda _e: remember_folder())

        async def write() -> None:
            text = (folder_field.value or "").strip()
            if not text:
                error_line.set_text(NO_FOLDER)
                return
            error_line.set_text("")
            name = (product_field.value or "").strip() or None
            model = load_settings().writer_model
            try:
                await run.io_bound(
                    context_job.writer.start, expand_path(text), name, model
                )
            except RuntimeError as exc:
                error_line.set_text(str(exc))
                return
            refresh()

        write_button.on_click(write)

        def show_result(result: dict) -> None:
            result_box.clear()
            with result_box:
                ui.label(files_line(result)).mark("context-files")
                ui.label(NOTICE).classes("sr-notice")
                ui.label(LINES_TITLE).classes("sr-section-title")
                with ui.column().classes("w-full gap-1 max-h-[220px] overflow-auto"):
                    for place, text, path in source_rows(result.get("context") or {}):
                        with ui.row().classes("w-full items-baseline gap-3 no-wrap"):
                            ui.label(place).classes("sr-code w-[170px] shrink-0")
                            ui.label(text).classes("grow break-words")
                            ui.label(path).classes("sr-code w-[150px] break-all")
                dropped = result.get("dropped") or []
                if dropped:
                    with ui.expansion(f"Removed by the check ({len(dropped)})"):
                        for item in dropped:
                            what = str(item.get("what", ""))
                            why = str(item.get("why", ""))
                            ui.label(f"{what}: {why}").classes("sr-small")
                area = (
                    ui.textarea(value=str(result.get("yaml") or ""))
                    .props("outlined rows=16 input-class=sr-code")
                    .classes("w-full")
                    .mark("context-yaml")
                )
                yaml_error = ui.label().classes("text-negative").mark("yaml-error")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Close", on_click=dialog.close).props("flat no-caps")

                    async def save() -> None:
                        await save_context(area.value or "", result, yaml_error)

                    ui.button("Save and use", on_click=save).props(
                        "unelevated no-caps color=primary"
                    ).mark("context-save")

        async def save_context(text: str, result: dict, yaml_error: ui.label) -> None:
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError:
                data = None
            if not isinstance(data, dict):
                yaml_error.set_text(BAD_YAML)
                return
            yaml_error.set_text("")
            name = (
                (product_field.value or "").strip()
                or str(result.get("folder_name") or "")
                or Path((folder_field.value or "").strip()).name
            )
            try:
                path = await run.io_bound(storage.save_context, text, result, name)
            except OSError as exc:
                yaml_error.set_text(f"The file cannot be saved: {exc}")
                return
            on_saved(show_path(path))
            dialog.close()
            ui.notify(SAVED, type="positive")

        def refresh() -> None:
            state = context_job.writer.state()
            another = jobs.manager.busy()
            working = state["state"] == "running"
            busy_line.set_visibility(another)
            write_button.set_enabled(not another and not working)
            steps.set_visibility(state["state"] != "idle")
            for name, (state_label, numbers_label) in stage_rows.items():
                item = state["stages"][name]
                state_label.set_text(item["state"])
                numbers_label.set_text(numbers_text(name, item["numbers"]))
            if state["state"] == "failed":
                error_line.set_text(state["message"])
            token = f"{state['state']}:{state['serial']}"
            if token != shown["token"]:
                shown["token"] = token
                result_box.clear()
                if state["state"] == "done" and state["result"]:
                    show_result(state["result"])

        timer = ui.timer(0.5, refresh)
        dialog.on("hide", timer.cancel)
        refresh()
    dialog.open()

