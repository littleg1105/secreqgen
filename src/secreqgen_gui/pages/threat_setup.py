"""The form for a threat list run."""

from __future__ import annotations

import asyncio

from nicegui import run, ui
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import check_about

from secreqgen_gui import form_memory, jobs, storage
from secreqgen_gui.form_memory import COMMON, THREATS
from secreqgen_gui.pages._setup_parts import (
    UNTESTED_PROMPTS,
    DocsPart,
    Foot,
    StoriesPart,
    about_summary,
    clear_button,
    initial_path,
    initial_product,
    message,
    prompt_set_choices,
    prompt_set_value,
    section,
    set_message,
)
from secreqgen_gui.settings import load_settings
from secreqgen_gui.widgets import page_title

ABOUT_LIMIT = 120
ABOUT_PAUSE = 0.3  # seconds with no change before the paragraph goes to its file
SAME_MODEL = "The checker must be a different model from the writer."
ABOUT_HINT = "What the product does and who uses it. The writer reads this."


def page(
    stories: str | None = None,
    docs: str | None = None,
    product: str | None = None,
) -> None:
    """Build the form. The query values stories, docs, product fill the fields."""
    defaults = load_settings()
    saved = form_memory.recall
    page_title("New threat list")
    with ui.column().classes("w-full max-w-[680px] gap-6"):
        stories_part = StoriesPart(
            lambda: refresh(),
            initial_path(stories) or saved(COMMON, "stories_file", "", str),
        )
        docs_part = DocsPart(
            lambda: refresh(),
            stories_part,
            initial_path(docs) or saved(COMMON, "docs", "", str),
            initial_product(product) or saved(COMMON, "product", "", str),
        )

        body = section("Options")
        with body:
            writer = (
                ui.input(
                    "Writer",
                    value=saved(THREATS, "writer", defaults.writer_model, str),
                )
                .props('outlined dense hint="The model that writes the threats."')
                .classes("w-full")
            )
            check = ui.switch(
                "Use a checker", value=saved(THREATS, "check", True, bool)
            )
            checker = (
                ui.input(
                    "Checker",
                    value=saved(THREATS, "checker", defaults.checker_model, str),
                )
                .props('outlined dense hint="The model that checks each threat."')
                .classes("w-full")
            )
            same = message()
            labels = ui.switch(
                "Add a threat actor and a STRIDE category",
                value=saved(THREATS, "labels", True, bool),
            )
            about = (
                ui.textarea(
                    "About the product (optional)",
                    value=saved(THREATS, "about", "", str),
                )
                .props(f'outlined autogrow hint="{ABOUT_HINT}"')
                .classes("w-full")
            )
            counter = ui.label().classes("sr-small")
            choices = prompt_set_choices("threats")
            old_set = saved(THREATS, "prompts", None, str)
            if old_set is not None and old_set not in choices:
                form_memory.remember(THREATS, "prompts", None)  # the set is gone
            prompt = ui.select(
                choices,
                value=old_set if old_set in choices else next(iter(choices)),
                label="Prompts",
            ).props("outlined dense")
            prompt.classes("w-full")
            untested = message()

        def words() -> int:
            return len((about.value or "").split())

        def same_model() -> bool:
            """True when the tool counts the checker as the writer.

            The tool ignores the letter case and a missing tag, so "gemma4"
            and "gemma4:26b" are one model.
            """
            from secreqgen.threats import ThreatListError, choose_models

            if not check.value:
                return False
            named_writer = (writer.value or "").strip()
            named_checker = (checker.value or "").strip()
            if not named_writer or not named_checker:
                return False
            try:
                choose_models(named_writer, None, named_checker, True)
            except ThreatListError:
                return True
            return False

        def ready() -> bool:
            if not (stories_part.path and stories_part.selected and docs_part.ok):
                return False
            if not (writer.value or "").strip():
                return False
            if check.value and (not (checker.value or "").strip() or same_model()):
                return False
            return words() <= ABOUT_LIMIT

        def refresh() -> None:
            docs_part.fill_product()
            checker.set_enabled(bool(check.value))
            set_message(same, SAME_MODEL if same_model() else "", "caution")
            over = words() > ABOUT_LIMIT
            size = f"{words()} of {ABOUT_LIMIT} words"
            counter.set_text(size + (". Shorten the paragraph." if over else ""))
            counter.classes(remove="text-negative")
            if over:
                counter.classes(add="text-negative")
            chosen = prompt_set_value(prompt.value)
            set_message(untested, UNTESTED_PROMPTS if chosen else "", "caution")
            foot.update()
            foot.show_command()

        def make_spec(about_text: str | None) -> jobs.ThreatRunSpec | None:
            """Return what a click on Start gives to the run."""
            if not stories_part.path or not docs_part.path:
                return None
            return jobs.ThreatRunSpec(
                stories_file=stories_part.path,
                docs=docs_part.path,
                product=(docs_part.product.value or "").strip() or None,
                story_ids=None if stories_part.all_selected else stories_part.selected,
                writer_model=writer.value.strip(),
                check=bool(check.value),
                checker_model=checker.value.strip() if check.value else None,
                labels=bool(labels.value),
                about=about_text,
                prompt_set=prompt_set_value(prompt.value),
                other_formats=docs_part.other_formats,
            )

        def command() -> tuple | None:
            """Return the command of the form and its lines."""
            try:
                about_text = check_about(about.value)
            except InputError:
                return None
            if about_text and on_disk["text"] != about_text:
                # The file of this paragraph is not written yet (or cannot be
                # written). A command that names it would not be true.
                return None
            spec = make_spec(about_text)
            if spec is None:
                return None
            total = len(stories_part.checks)
            found = jobs.command_for_threats(
                spec,
                total_stories=total,
                default_product=stories_part.project,
                about_file=on_disk["path"] if about_text else None,
                base_url=load_settings().base_url,
            )
            lines = jobs.command_lines(
                found, picked=len(stories_part.selected), total=total
            )
            return found, lines

        # The command needs the paragraph as a file. Each text has its own
        # file (the name comes from the text). on_disk holds the text whose
        # file this page wrote last.
        paragraph = {"token": 0}
        on_disk: dict = {"text": None, "path": None}

        async def write_about() -> None:
            """Put the paragraph into its file, a moment after the last change."""
            paragraph["token"] += 1
            token = paragraph["token"]
            await asyncio.sleep(ABOUT_PAUSE)
            if token != paragraph["token"]:
                return  # a newer change waits already
            try:
                text = check_about(about.value)
            except InputError:
                text = None  # too long: the form is not complete
            if text == on_disk["text"]:
                return
            old = on_disk["path"]
            on_disk.update(text=None, path=None)
            try:
                path = await run.io_bound(storage.swap_about, text, old)
            except (OSError, ValueError):
                return  # no file: the block shows no command for this text
            if token == paragraph["token"]:
                on_disk.update(text=text, path=path)
                foot.show_command()
            elif path is not None:
                # A newer change came while the file was written. Let go of
                # this file. The newer change writes its own.
                await run.io_bound(storage.swap_about, None, path)

        async def start() -> None:
            try:
                about_text = check_about(about.value)
            except InputError as exc:
                ui.notify(str(exc), type="negative")
                return
            if not stories_part.path or not docs_part.path:
                return
            if not await docs_part.still_there():
                return
            spec = make_spec(about_text)
            if spec is None:
                return
            try:
                run_id = await run.io_bound(jobs.manager.start_threats, spec)
            except RuntimeError as exc:
                ui.notify(str(exc), type="negative")
                foot.update()
                return
            ui.navigate.to(f"/runs/{run_id}")

        foot = Foot(
            "Start threat list",
            ready,
            lambda: about_summary(len(stories_part.selected)),
            start,
            command,
        )
        about.on_value_change(lambda _e: write_about())
        if (about.value or "").strip():
            ui.timer(0.05, write_about, once=True)
        for element in (check, labels, prompt):
            element.on_value_change(lambda _e: refresh())
        for element in (writer, checker, about):
            element.on_value_change(lambda _e: refresh())
        kept = {
            "writer": writer,
            "check": check,
            "checker": checker,
            "labels": labels,
            "about": about,
            "prompts": prompt,
        }
        muted = {"on": False}

        def keep(name: str):
            def handler(_e) -> None:
                if not muted["on"]:
                    form_memory.remember(THREATS, name, kept[name].value)

            return handler

        for name, element in kept.items():
            element.on_value_change(keep(name))

        def reset() -> None:
            muted["on"] = True
            writer.set_value(defaults.writer_model)
            check.set_value(True)
            checker.set_value(defaults.checker_model)
            labels.set_value(True)
            about.set_value("")
            prompt.set_value(next(iter(choices)))
            muted["on"] = False
            stories_part.reset()
            docs_part.reset()
            form_memory.clear(COMMON, THREATS)
            refresh()

        clear_button(reset)
        refresh()
