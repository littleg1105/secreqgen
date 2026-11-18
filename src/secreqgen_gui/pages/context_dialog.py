"""The dialog that writes a system context with the model.

The text of the draft comes from a model. It is shown with ui.label and in
the value of a text field. It never goes through markdown or HTML.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from nicegui import run, ui
from secreqgen.application.commands import context_command
from secreqgen.application.system_context import context_lines

from secreqgen_gui import context_job, form_memory, jobs, storage
from secreqgen_gui.form_memory import REQUIREMENTS
from secreqgen_gui.settings import (
    data_home,
    expand_path,
    load_settings,
    saves,
    show_path,
)
from secreqgen_gui.widgets import CommandBlock, path_field

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
STOPPED_EARLY = (
    "The scan stopped at {n} files. Pick a smaller folder to scan all files."
)
CAUTION = "color: var(--sr-caution)"


def _files(count: int) -> str:
    return f"{count:,} file" if count == 1 else f"{count:,} files"


def numbers_text(stage: str, numbers: dict) -> str:
    """Return the numbers of a stage as a short text. Empty: none yet."""
    try:
        if stage == "scan" and "files" in numbers:
            return _files(int(numbers["files"]))
        if stage == "write" and int(numbers.get("attempt") or 0) >= 2:
            return f"try {int(numbers['attempt'])} of {context_job.TRIES}"
        if stage == "check" and "kept" in numbers:
            kept = int(numbers["kept"])
            return f"{kept:,} of {kept + int(numbers['dropped']):,} passed"
    except (KeyError, TypeError, ValueError):
        return ""
    return ""


def notes_lines(result: dict) -> list[str]:
    """Return the lines above the draft. Empty: nothing to say."""
    facts = result.get("facts") or {}
    lines: list[str] = []
    secret = int(facts.get("secret_files_skipped") or 0)
    if secret == 1:
        lines.append("1 file that can hold secrets is not read.")
    elif secret:
        lines.append(f"{secret:,} files that can hold secrets are not read.")
    if facts.get("stopped_early"):
        seen = int(facts.get("files_seen") or 0)
        lines.append(STOPPED_EARLY.format(n=f"{seen:,}"))
    return lines


def grouped_lines(result: dict) -> list[tuple[str, list[dict]]]:
    """Return the lines of a draft as (place, lines), in order of first use."""
    try:
        lines = context_lines(result)
    except (KeyError, TypeError, AttributeError):
        return []
    groups: dict[str, list[dict]] = {}
    for line in lines:
        groups.setdefault(str(line.get("where", "")), []).append(line)
    return list(groups.items())


def save_error_text(exc: OSError) -> str:
    """Return the line for a context file that cannot be saved."""
    reason = exc.strerror or type(exc).__name__
    folder = show_path(data_home() / "contexts")
    return f"The system context cannot be saved: {reason}. Check the folder {folder}."


def open_context_dialog(
    folder: str, product: str, on_saved: Callable[[str], None]
) -> None:
    """Open the dialog. on_saved gets the short path of the saved file."""
    shown = {"token": None}
    # A new dialog starts empty. It shows a job only when that job is in
    # progress now or starts after the dialog opened.
    first = context_job.writer.state()
    old = {"serial": None if first["state"] == "running" else first["serial"]}
    pending = {"start": False}
    with (
        ui.dialog() as dialog,
        ui.card().classes("w-[760px] max-w-full gap-3 overflow-y-auto"),
    ):
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
        block = CommandBlock(mark="context-command")
        error_line = ui.label().classes("text-negative").mark("context-error")
        stage_rows: dict[str, tuple[ui.label, ui.label]] = {}
        steps = ui.column().classes("w-full gap-1")
        with steps:
            for name in context_job.STAGES:
                with ui.row().classes("items-baseline gap-3 no-wrap"):
                    ui.label(context_job.STAGE_NAMES[name]).classes("w-[170px]")
                    state_label = ui.label().classes("sr-small w-[90px]")
                    numbers_label = ui.label().classes("sr-small")
                stage_rows[name] = (state_label, numbers_label)
        steps.set_visibility(False)
        result_box = ui.column().classes("w-full gap-3")

        def remember_folder() -> None:
            form_memory.remember(
                REQUIREMENTS, "context_folder", (folder_field.value or "").strip()
            )

        def show_command() -> None:
            """Show the command for the folder and the product in the fields."""
            text = (folder_field.value or "").strip()
            if not text:
                block.show(None)
                return
            settings = load_settings()
            block.show(
                context_command(
                    folder=jobs.absolute(expand_path(text)),
                    product=(product_field.value or "").strip() or None,
                    writer_model=settings.writer_model,
                    base_url=settings.base_url,
                )
            )

        folder_field.on_value_change(lambda _e: remember_folder())
        folder_field.on_value_change(lambda _e: show_command())
        product_field.on_value_change(lambda _e: show_command())
        show_command()
        seen = {"saves": saves()}

        def follow_settings() -> None:
            """Build the command again when the settings changed elsewhere."""
            if saves() != seen["saves"]:
                seen["saves"] = saves()
                show_command()

        ui.timer(0.5, follow_settings)

        async def write() -> None:
            if pending["start"]:
                return
            text = (folder_field.value or "").strip()
            if not text:
                error_line.set_text(NO_FOLDER)
                return
            error_line.set_text("")
            write_button.set_enabled(False)
            pending["start"] = True
            name = (product_field.value or "").strip() or None
            model = load_settings().writer_model
            try:
                await run.io_bound(
                    context_job.writer.start, expand_path(text), name, model
                )
            except RuntimeError as exc:
                error_line.set_text(str(exc))
            finally:
                pending["start"] = False
            refresh()

        write_button.on_click(write)

        def show_result(result: dict) -> None:
            result_box.clear()
            with result_box:
                for line in notes_lines(result):
                    ui.label(line).mark("context-note")
                ui.label(NOTICE).classes("sr-notice")
                ui.label(LINES_TITLE).classes("sr-section-title")
                with ui.column().classes("w-full gap-1"):
                    for place, lines in grouped_lines(result):
                        ui.label(place).classes("sr-code mt-2")
                        for line in lines:
                            with ui.row().classes(
                                "w-full items-baseline gap-3 no-wrap"
                            ):
                                ui.label(str(line.get("text", ""))).classes(
                                    "grow break-words"
                                )
                                source = (
                                    "sr-small"
                                    if place == "security_profile"
                                    else "sr-code"
                                )
                                ui.label(str(line.get("path", ""))).classes(
                                    f"{source} w-[270px] shrink-0 break-words"
                                )
                dropped = result.get("dropped") or []
                if dropped:
                    with ui.expansion(f"Not in the draft ({len(dropped)})"):
                        for item in dropped:
                            with ui.row().classes(
                                "w-full items-baseline gap-3 no-wrap"
                            ):
                                ui.label(str(item.get("what", ""))).classes(
                                    "sr-small shrink-0"
                                )
                                ui.label(str(item.get("why", ""))).classes(
                                    "sr-small break-words"
                                )
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
                yaml_error.set_text(save_error_text(exc))
                return
            on_saved(show_path(path))
            dialog.close()
            ui.notify(SAVED, type="positive")

        def refresh() -> None:
            state = context_job.writer.state()
            another = jobs.manager.busy()
            working = state["state"] == "running"
            mine = state["serial"] != old["serial"]
            busy_line.set_visibility(another)
            write_button.set_enabled(
                not another and not working and not pending["start"]
            )
            steps.set_visibility(mine and state["state"] != "idle")
            for name, (state_label, numbers_label) in stage_rows.items():
                item = state["stages"][name]
                state_label.set_text(item["state"] if mine else "")
                numbers_label.set_text(
                    numbers_text(name, item["numbers"]) if mine else ""
                )
            if mine and state["state"] == "failed":
                error_line.set_text(state["message"])
            elif mine and state["state"] in ("running", "done"):
                error_line.set_text("")
            token = f"{state['state']}:{state['serial']}" if mine else "old"
            if token != shown["token"]:
                shown["token"] = token
                result_box.clear()
                if mine and state["state"] == "done" and state["result"]:
                    show_result(state["result"])

        timer = ui.timer(0.5, refresh)
        dialog.on("hide", timer.cancel)
        refresh()
    dialog.open()
