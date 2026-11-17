"""The form for a requirements run."""

from __future__ import annotations

from nicegui import run, ui

from secreqgen_gui import jobs
from secreqgen_gui.pages._setup_parts import (
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
from secreqgen_gui.settings import expand_path, load_settings
from secreqgen_gui.widgets import page_title, path_field


def page(
    stories: str | None = None,
    docs: str | None = None,
    product: str | None = None,
) -> None:
    """Build the form. The query values stories, docs, product fill the fields."""
    defaults = load_settings()
    page_title(
        "Start requirements",
        "Choose the stories and the documentation of the product.",
    )
    with ui.column().classes("w-full max-w-[680px] gap-6"):
        stories_part = StoriesPart(lambda: refresh(), initial_path(stories))
        docs_part = DocsPart(
            lambda: refresh(),
            stories_part,
            initial_path(docs),
            initial_product(product),
        )

        body = section("Setup")
        with body:
            model = (
                ui.input("Model", value=defaults.requirements_model)
                .props("outlined dense")
                .classes("w-full")
            )
            context = path_field("System context file (optional)")
            choices = prompt_set_choices("requirements")
            prompt = ui.select(
                choices, value=next(iter(choices)), label="Prompt set"
            ).props("outlined dense")
            prompt.classes("w-full")
            untested = message()

        def ready() -> bool:
            return bool(
                stories_part.path
                and stories_part.selected
                and docs_part.ok
                and (model.value or "").strip()
            )

        def refresh() -> None:
            docs_part.fill_product()
            chosen = prompt_set_value(prompt.value)
            set_message(untested, "Not the tested setup" if chosen else "", "caution")
            foot.update()

        async def start() -> None:
            if not stories_part.path or not docs_part.path:
                return
            context_text = (context.value or "").strip()
            context_path = str(expand_path(context_text)) if context_text else None
            spec = jobs.RequirementsRunSpec(
                stories_file=stories_part.path,
                docs=docs_part.path,
                product=(docs_part.product.value or "").strip() or None,
                story_ids=None if stories_part.all_selected else stories_part.selected,
                model=model.value.strip(),
                system_context=context_path,
                prompt_set=prompt_set_value(prompt.value),
            )
            try:
                run_id = await run.io_bound(jobs.manager.start_requirements, spec)
            except RuntimeError as exc:
                ui.notify(str(exc), type="negative")
                foot.update()
                return
            ui.navigate.to(f"/runs/{run_id}")

        foot = Foot(
            "Start requirements",
            ready,
            lambda: about_summary(len(stories_part.selected)),
            start,
        )
        prompt.on_value_change(lambda _e: refresh())
        model.on_value_change(lambda _e: refresh())
        refresh()
