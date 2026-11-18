"""The form for a requirements run."""

from __future__ import annotations

from nicegui import run, ui

from secreqgen_gui import form_memory, jobs
from secreqgen_gui.form_memory import COMMON, REQUIREMENTS
from secreqgen_gui.pages._setup_parts import (
    FILE_MISSING,
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
from secreqgen_gui.pages.context_dialog import BUTTON as CONTEXT_BUTTON
from secreqgen_gui.pages.context_dialog import open_context_dialog
from secreqgen_gui.settings import expand_path, load_settings
from secreqgen_gui.widgets import page_title, path_field

CONTEXT_MISSING = FILE_MISSING


def page(
    stories: str | None = None,
    docs: str | None = None,
    product: str | None = None,
) -> None:
    """Build the form. The query values stories, docs, product fill the fields."""
    defaults = load_settings()
    saved = form_memory.recall
    page_title("New requirements")
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
            model = (
                ui.input(
                    "Model",
                    value=saved(
                        REQUIREMENTS, "model", defaults.requirements_model, str
                    ),
                )
                .props("outlined dense")
                .classes("w-full")
            )
            context_start = saved(REQUIREMENTS, "context_file", "", str)
            context = path_field("System context file (optional)", context_start)
            context_note = message()
            context_note.set_visibility(False)
            ui.button(
                CONTEXT_BUTTON,
                on_click=lambda: open_context_dialog(
                    saved(REQUIREMENTS, "context_folder", "", str),
                    (docs_part.product.value or "").strip(),
                    context.set_value,
                ),
            ).props("outline no-caps unelevated color=primary").mark(
                "context-write-open"
            ).classes(
                "self-start"
            )
            choices = prompt_set_choices("requirements")
            old_set = saved(REQUIREMENTS, "prompts", None, str)
            if old_set is not None and old_set not in choices:
                form_memory.remember(REQUIREMENTS, "prompts", None)  # the set is gone
            prompt = ui.select(
                choices,
                value=old_set if old_set in choices else next(iter(choices)),
                label="Prompts",
            ).props("outlined dense")
            prompt.classes("w-full")
            untested = message()

        # The check of the context file runs off the event loop. While it
        # runs, the file counts as not ready. An empty field is always fine.
        context_state = {"ok": not context_start.strip(), "token": 0}

        async def check_context() -> None:
            context_state["token"] += 1
            token = context_state["token"]
            text = (context.value or "").strip()
            if not text:
                context_state["ok"] = True
                set_message(context_note, "")
                refresh()
                return
            context_state["ok"] = False
            set_message(context_note, "")
            there = await run.io_bound(expand_path(text).is_file)
            if token != context_state["token"]:
                return
            context_state["ok"] = there
            set_message(context_note, "" if there else CONTEXT_MISSING, "bad")
            refresh()

        def ready() -> bool:
            return bool(
                stories_part.path
                and stories_part.selected
                and docs_part.ok
                and context_state["ok"]
                and (model.value or "").strip()
            )

        def refresh() -> None:
            docs_part.fill_product()
            chosen = prompt_set_value(prompt.value)
            set_message(untested, UNTESTED_PROMPTS if chosen else "", "caution")
            foot.update()
            foot.show_command()

        def make_spec() -> jobs.RequirementsRunSpec | None:
            """Return what a click on Start gives to the run."""
            if not stories_part.path or not docs_part.path:
                return None
            context_text = (context.value or "").strip()
            context_path = str(expand_path(context_text)) if context_text else None
            return jobs.RequirementsRunSpec(
                stories_file=stories_part.path,
                docs=docs_part.path,
                product=(docs_part.product.value or "").strip() or None,
                story_ids=None if stories_part.all_selected else stories_part.selected,
                model=model.value.strip(),
                system_context=context_path,
                prompt_set=prompt_set_value(prompt.value),
                other_formats=docs_part.other_formats,
            )

        def command():
            """Return the command of the form."""
            spec = make_spec()
            if spec is None:
                return None
            return jobs.command_for_requirements(
                spec,
                total_stories=len(stories_part.checks),
                default_product=stories_part.requirements_default,
                base_url=load_settings().base_url,
            )

        async def start() -> None:
            if not stories_part.path or not docs_part.path:
                return
            await check_context()
            if not context_state["ok"]:
                return
            if not await docs_part.still_there():
                return
            spec = make_spec()
            if spec is None:
                return
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
            command,
        )
        prompt.on_value_change(lambda _e: refresh())
        model.on_value_change(lambda _e: refresh())
        context.on_value_change(lambda _e: check_context())
        if context_start.strip():
            ui.timer(0.05, check_context, once=True)
        kept = {"model": model, "context_file": context, "prompts": prompt}
        muted = {"on": False}

        def keep(name: str):
            def handler(_e) -> None:
                if not muted["on"]:
                    value = kept[name].value
                    if name == "context_file":
                        value = (value or "").strip()
                    form_memory.remember(REQUIREMENTS, name, value)

            return handler

        for name, element in kept.items():
            element.on_value_change(keep(name))

        def reset() -> None:
            muted["on"] = True
            model.set_value(defaults.requirements_model)
            context.set_value("")
            prompt.set_value(next(iter(choices)))
            muted["on"] = False
            stories_part.reset()
            docs_part.reset()
            form_memory.clear(COMMON, REQUIREMENTS)
            refresh()

        clear_button(reset)
        refresh()
