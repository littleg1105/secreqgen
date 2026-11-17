"""The form for a threat list run."""

from __future__ import annotations

from nicegui import run, ui
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import check_about

from secreqgen_gui import jobs
from secreqgen_gui.pages._setup_parts import (
    UNTESTED_PROMPTS,
    DocsPart,
    Foot,
    StoriesPart,
    about_summary,
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
SAME_MODEL = "The checker must be a different model from the writer."
ABOUT_HINT = "What the product does and who uses it. The writer reads this."


def page(
    stories: str | None = None,
    docs: str | None = None,
    product: str | None = None,
) -> None:
    """Build the form. The query values stories, docs, product fill the fields."""
    defaults = load_settings()
    page_title("New threat list")
    with ui.column().classes("w-full max-w-[680px] gap-6"):
        stories_part = StoriesPart(lambda: refresh(), initial_path(stories))
        docs_part = DocsPart(
            lambda: refresh(),
            stories_part,
            initial_path(docs),
            initial_product(product),
        )

        body = section("Options")
        with body:
            writer = (
                ui.input("Writer", value=defaults.writer_model)
                .props('outlined dense hint="The model that writes the threats."')
                .classes("w-full")
            )
            check = ui.switch("Use a checker", value=True)
            checker = (
                ui.input("Checker", value=defaults.checker_model)
                .props('outlined dense hint="The model that checks each threat."')
                .classes("w-full")
            )
            same = message()
            labels = ui.switch(
                "Add a threat actor and a STRIDE category", value=True
            )
            about = (
                ui.textarea("About the product (optional)")
                .props(f'outlined autogrow hint="{ABOUT_HINT}"')
                .classes("w-full")
            )
            counter = ui.label().classes("sr-small")
            choices = prompt_set_choices("threats")
            prompt = ui.select(
                choices, value=next(iter(choices)), label="Prompts"
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
            size = f"{words()} of {ABOUT_LIMIT} words"
            counter.set_text(size + (". Shorten the paragraph." if over else ""))
            counter.classes(remove="text-negative")
            if over:
                counter.classes(add="text-negative")
            chosen = prompt_set_value(prompt.value)
            set_message(untested, UNTESTED_PROMPTS if chosen else "", "caution")
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
