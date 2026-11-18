"""The dialog that writes the paragraph "About the product" with the model.

It starts the same job as the dialog "Write a system context". When the job
is done, plain code makes the paragraph from the checked lines (the tool does
this, no model writes it). The text of the draft comes from a model. It is
shown with ui.label and in the value of a text field. It never goes through
markdown or HTML.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import run, ui
from secreqgen.application.about import about_draft
from secreqgen.application.commands import about_command
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import check_about

from secreqgen_gui import context_job, form_memory, jobs, storage
from secreqgen_gui.form_memory import REQUIREMENTS
from secreqgen_gui.pages.context_dialog import (
    BUSY,
    CAUTION,
    FOLDER_HINT,
    NO_FOLDER,
    NOTICE,
    PRODUCT_LABEL,
    notes_lines,
    numbers_text,
)
from secreqgen_gui.settings import expand_path, load_settings, saves
from secreqgen_gui.widgets import CommandBlock, path_field

ABOUT_LIMIT = 120
TITLE = "Write a paragraph about the product"
INTRO = (
    "The model reads the key files of a codebase and writes a system context: "
    "facts about the product."
)
INTRO_2 = "SecReqGen makes the paragraph from these facts, with no model."
TEXT_LABEL = "About the product"
LINES_TITLE = "Lines and their sources"
NO_SOURCE = "The model named no source file."
MAKE_STEP = "Make the paragraph"
USE = "Use this paragraph"
REPLACES = "This replaces the text in the form."
SHORTEN = ". Shorten the paragraph."


OWNER = "about"  # the name of this dialog for the shared job


def count_words(text: str | None) -> int:
    """Count the words of a paragraph the way the form does."""
    return len((text or "").split())


def counter_text(words: int) -> str:
    """Return the line under a paragraph: the words and the limit."""
    over = words > ABOUT_LIMIT
    return f"{words} of {ABOUT_LIMIT} words" + (SHORTEN if over else "")


def left_out_title(count: int) -> str:
    return f"Left out for length ({count})"


def failed_title(count: int) -> str:
    return f"Failed the check ({count})"


def group_lines(lines) -> list[tuple[str, list[dict]]]:
    """Return the lines as (place, lines), in the order of first use."""
    groups: dict[str, list[dict]] = {}
    for line in lines:
        groups.setdefault(str(line.get("where", "")), []).append(line)
    return list(groups.items())


def draw_lines(lines, mark: str) -> None:
    """Show lines with their sources. Each text is a plain label."""
    with ui.column().classes("w-full gap-1"):
        for place, items in group_lines(lines):
            ui.label(place).classes("sr-code mt-2")
            for line in items:
                with ui.row().classes("w-full items-baseline gap-3 no-wrap"):
                    ui.label(str(line.get("text", ""))).classes(
                        "grow break-words"
                    ).mark(f"{mark}-text")
                    path = str(line.get("path") or "")
                    if place == "description" and not path:
                        ui.label(NO_SOURCE).classes(
                            "sr-small w-[270px] shrink-0 break-words"
                        ).mark(f"{mark}-source")
                    else:
                        ui.label(path).classes(
                            "sr-code w-[270px] shrink-0 break-words"
                        ).mark(f"{mark}-source")


def draw_failed(dropped) -> None:
    """Show the lines that the check removed, as the other dialog does."""
    with ui.expansion(failed_title(len(dropped))).mark("about-failed"):
        for item in dropped:
            with ui.row().classes("w-full items-baseline gap-3 no-wrap"):
                ui.label(str(item.get("what", ""))).classes("sr-small shrink-0")
                ui.label(str(item.get("why", ""))).classes("sr-small break-words")


def open_about_dialog(
    folder: str,
    product: str,
    current_text: Callable[[], str],
    on_use: Callable[[str], None],
) -> None:
    """Open the dialog.

    Current_text gives the text that the field of the form holds now. On_use
    gets the paragraph (white space folded) when the person clicks the button.
    """
    shown = {"token": None, "words": None, "bad": False}
    # A new dialog starts empty. It shows a job only when that job is in
    # progress now (and an about dialog started it) or starts after the
    # dialog opened. A job of the other dialog is never shown here.
    first = context_job.writer.state()
    show_first = first["state"] == "running" and first.get("owner") == OWNER
    old = {"serial": None if show_first else first["serial"]}
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
        ).mark("about-folder")
        product_field = (
            ui.input(PRODUCT_LABEL, value=product)
            .props("outlined dense")
            .classes("w-full")
            .mark("about-product")
        )
        with ui.row().classes("items-center gap-4"):
            write_button = (
                ui.button("Write")
                .props("unelevated no-caps color=primary")
                .mark("about-write")
            )
            busy_line = ui.label(BUSY).classes("sr-small").style(CAUTION)
        block = CommandBlock(mark="about-command")
        error_line = ui.label().classes("text-negative").mark("about-error")
        stage_rows: dict[str, tuple[ui.label, ui.label]] = {}
        steps = ui.column().classes("w-full gap-1")
        with steps:
            for name in (*context_job.STAGES, "make"):
                title = MAKE_STEP if name == "make" else context_job.STAGE_NAMES[name]
                with ui.row().classes("items-baseline gap-3 no-wrap"):
                    ui.label(title).classes("w-[170px]")
                    state_label = (
                        ui.label()
                        .classes("sr-small w-[90px]")
                        .mark(f"about-state-{name}")
                    )
                    numbers_label = (
                        ui.label().classes("sr-small").mark(f"about-numbers-{name}")
                    )
                stage_rows[name] = (state_label, numbers_label)
        steps.set_visibility(False)
        result_box = ui.column().classes("w-full gap-3")

        def remember_folder() -> None:
            # The same value as the dialog "Write a system context" keeps.
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
                about_command(
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
                    context_job.writer.start, expand_path(text), name, model, OWNER
                )
            except RuntimeError as exc:
                error_line.set_text(str(exc))
            finally:
                pending["start"] = False
            refresh()

        write_button.on_click(write)

        def show_result(result: dict) -> None:
            """Show the draft. The tool makes the paragraph from the result."""
            try:
                draft = about_draft(result)
            except Exception as exc:  # noqa: BLE001 - ContextError or odd data
                shown["bad"] = True
                error_line.set_text(
                    context_job.error_text(exc, load_settings().base_url)
                )
                return
            shown["words"] = count_words(draft.paragraph)
            with result_box:
                for line in notes_lines(result):
                    ui.label(line).mark("about-note")
                ui.label(NOTICE).classes("sr-notice")
                area = (
                    ui.textarea(TEXT_LABEL, value=draft.paragraph)
                    .props("outlined autogrow")
                    .classes("w-full")
                    .mark("about-text")
                )
                counter = ui.label().classes("sr-small").mark("about-counter")
                ui.label(LINES_TITLE).classes("sr-section-title")
                draw_lines(draft.lines, "about-line")
                if draft.left_out:
                    with ui.expansion(left_out_title(len(draft.left_out))).mark(
                        "about-left-out"
                    ):
                        draw_lines(draft.left_out, "about-left")
                dropped = result.get("dropped") or []
                if dropped:
                    draw_failed(dropped)
                with ui.row().classes("w-full items-center justify-end gap-3"):
                    if (current_text() or "").strip():
                        ui.label(REPLACES).classes("sr-small").mark("about-replaces")
                    ui.button("Close", on_click=dialog.close).props("flat no-caps")
                    use_button = (
                        ui.button(USE)
                        .props("unelevated no-caps color=primary")
                        .mark("about-use")
                    )

                def follow_text() -> None:
                    words = count_words(area.value)
                    counter.set_text(counter_text(words))
                    counter.classes(remove="text-negative")
                    if words > ABOUT_LIMIT:
                        counter.classes(add="text-negative")
                    use_button.set_enabled(0 < words <= ABOUT_LIMIT)

                area.on_value_change(lambda _e: follow_text())
                follow_text()

                async def use() -> None:
                    text = area.value or ""
                    try:
                        folded = check_about(text)
                    except InputError:
                        return  # too long: the button is off
                    if not folded:
                        return
                    name = (
                        (product_field.value or "").strip()
                        or str(result.get("folder_name") or "")
                        or expand_path((folder_field.value or "").strip()).name
                    )
                    try:
                        await run.io_bound(storage.save_about_draft, text, result, name)
                    except (OSError, ValueError):
                        pass  # no record: the paragraph still goes into the form
                    on_use(folded)
                    dialog.close()

                use_button.on_click(use)

        def refresh() -> None:
            state = context_job.writer.state()
            another = jobs.manager.busy()
            working = state["state"] == "running"
            mine = state["serial"] != old["serial"] and state.get("owner") == OWNER
            busy_line.set_visibility(another)
            write_button.set_enabled(
                not another and not working and not pending["start"]
            )
            steps.set_visibility(mine and state["state"] != "idle")
            for name in context_job.STAGES:
                state_label, numbers_label = stage_rows[name]
                item = state["stages"][name]
                state_label.set_text(item["state"] if mine else "")
                numbers_label.set_text(
                    numbers_text(name, item["numbers"]) if mine else ""
                )
            if mine and state["state"] == "failed":
                error_line.set_text(state["message"])
            token = f"{state['state']}:{state['serial']}" if mine else "old"
            if token != shown["token"]:
                shown.update(token=token, words=None, bad=False)
                result_box.clear()
                if mine and state["state"] in ("running", "done"):
                    error_line.set_text("")
                if mine and state["state"] == "done" and state["result"]:
                    show_result(state["result"])
            make_state, make_numbers = stage_rows["make"]
            if not mine:
                make_state.set_text("")
                make_numbers.set_text("")
            elif shown["bad"]:
                make_state.set_text("failed")
                make_numbers.set_text("")
            elif shown["words"] is not None:
                make_state.set_text("done")
                make_numbers.set_text(f"{shown['words']} words")
            else:
                make_state.set_text("waiting")
                make_numbers.set_text("")

        timer = ui.timer(0.5, refresh)
        dialog.on("hide", timer.cancel)
        refresh()
    dialog.open()
