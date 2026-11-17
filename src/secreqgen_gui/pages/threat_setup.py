"""The form for a threat list run."""

from __future__ import annotations

from nicegui import run, ui
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import check_about

from secreqgen_gui import jobs
from secreqgen_gui.pages._setup_parts import (
    DocsPart,
    Foot,
    StoriesPart,
    about_summary,
    message,
    prompt_set_choices,
    prompt_set_value,
    section,
    set_message,
)
from secreqgen_gui.settings import load_settings
from secreqgen_gui.widgets import page_title

ABOUT_LIMIT = 120
SAME_MODEL = "The second model must be a different model."


def page(stories: str | None = None, docs: str | None = None) -> None:
    """Build the form. The query values stories and docs fill the two paths."""
    defaults = load_settings()
    page_title(
        "Start a threat list",
        "Choose the stories and the documentation of the product. "
        "The defaults are the tested setup.",
    )
    with ui.column().classes("w-full max-w-[680px] gap-6"):
        stories_part = StoriesPart(lambda: refresh(), stories or "")
        docs_part = DocsPart(lambda: refresh(), stories_part, docs or "")

        body = section("Setup")
        with body:
            writer = (
                ui.input("Writer model", value=defaults.writer_model)
                .props("outlined dense")
                .classes("w-full")
            )
            check = ui.switch("A second model checks each row", value=True)
            checker = (
                ui.input("Second model", value=defaults.checker_model)
                .props("outlined dense")
                .classes("w-full")
            )
            same = message()
            labels = ui.switch(
                "Threat actor and STRIDE category for each row", value=True
            )
            about = (
                ui.textarea("Paragraph about the product (optional)")
                .props("outlined autogrow")
                .classes("w-full")
            )
            counter = ui.label().classes("sr-small")
            choices = prompt_set_choices("threats")
            prompt = ui.select(
                choices, value=next(iter(choices)), label="Prompt set"
            ).props("outlined dense")
            prompt.classes("w-full")
            untested = message()

        def words() -> int:
            return len((about.value or "").split())

        def same_model() -> bool:
            return bool(
                check.value
                and (writer.value or "").strip() == (checker.value or "").strip()
            )

        def ready() -> bool:
            if not (stories_part.path and stories_part.selected and docs_part.ok):
                return False
            if not (writer.value or "").strip():
                return False
            if check.value and (
                not (checker.value or "").strip() or same_model()
            ):
                return False
            return words() <= ABOUT_LIMIT

        def refresh() -> None:
            docs_part.fill_product()
            checker.set_enabled(bool(check.value))
            set_message(same, SAME_MODEL if same_model() else "", "caution")
            over = words() > ABOUT_LIMIT
            counter.set_text(f"{words()} of {ABOUT_LIMIT} words")
            counter.classes(remove="text-negative")
            if over:
                counter.classes(add="text-negative")
            chosen = prompt_set_value(prompt.value)
            set_message(untested, "Not the tested setup" if chosen else "", "caution")
            foot.update()

        async def start() -> None:
            try:
                about_text = check_about(about.value)
            except InputError as exc:
                ui.notify(str(exc), type="negative")
                return
            if not stories_part.path or not docs_part.path:
                return
            spec = jobs.ThreatRunSpec(
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
            )
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
        )
        for element in (check, labels, prompt):
            element.on_value_change(lambda _e: refresh())
        for element in (writer, checker, about):
            element.on_value_change(lambda _e: refresh())
        refresh()
