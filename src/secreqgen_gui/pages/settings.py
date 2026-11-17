"""Model server, default models, data folder and about."""

from __future__ import annotations

from nicegui import run, ui
from secreqgen.application.server import check_model_server

from secreqgen_gui import __version__
from secreqgen_gui.pages.home import (
    STATUS_CSS,
    model_names,
    section,
    show_server_result,
)
from secreqgen_gui.settings import (
    GuiSettings,
    data_home,
    load_settings,
    make_config,
    save_settings,
    show_path,
)
from secreqgen_gui.widgets import page_title

DATA_SENTENCE = (
    "Uploads, runs and your prompt sets are in this folder. Set the "
    "environment variable SECREQGEN_GUI_HOME before the start to use "
    "another folder."
)
ABOUT = (
    "The threat list is a draft for a person to check. It holds only what "
    "the documentation states. It is not a complete threat model.",
    "The tested setup: gemma4:26b writes the rows and qwen3.8:27b checks them.",
    "The older commands of the vector store (ingest, evaluate) are on the "
    "command line only.",
)
MODEL_FIELDS = (
    ("writer", "Writer model", "writer_model", "Give the name of the writer model."),
    (
        "checker",
        "Second model",
        "checker_model",
        "Give the name of the second model.",
    ),
    (
        "requirements",
        "Model for requirements",
        "requirements_model",
        "Give the name of the model for requirements.",
    ),
)
ADDRESS_ERROR = "Give the address of the model server."


def _clean(value: object) -> str:
    return str(value or "").strip()


def page() -> None:
    ui.add_css(STATUS_CSS)
    ui.add_css(_CSS)
    settings = load_settings()
    selects: dict[str, ui.select] = {}
    notes: dict[str, ui.label] = {}

    with ui.column().classes("w-full max-w-[640px] gap-0"):
        page_title("Settings")

        with section("Model server", first=True):
            address = (
                ui.input("Address of the model server", value=settings.base_url)
                .props("outlined dense")
                .classes("w-full")
                .mark("address")
            )
            notes["address"] = ui.label().classes("sr-field-error")
            result = ui.column().classes("gap-1")

            async def check() -> None:
                url = _clean(address.value)
                if not url:
                    notes["address"].set_text(ADDRESS_ERROR)
                    return
                notes["address"].set_text("")
                result.clear()
                with result:
                    ui.label("Checking the model server").classes("sr-small")
                status = await run.io_bound(
                    check_model_server, make_config(_current(url))
                )
                result.clear()
                with result:
                    show_server_result(status, _names(), url)
                _offer(status)

            ui.button("Check the model server", on_click=check).props(
                "outline no-caps unelevated"
            )

        with section("Models"):
            for key, label, field, _error in MODEL_FIELDS:
                value = getattr(settings, field)
                selects[key] = (
                    ui.select(
                        [value],
                        value=value,
                        label=label,
                        with_input=True,
                        new_value_mode="add-unique",
                    )
                    .props("outlined dense")
                    .classes("w-full")
                    .mark(key)
                )
                notes[key] = ui.label().classes("sr-field-error")

        def _current(url: str) -> GuiSettings:
            return GuiSettings(
                base_url=url,
                writer_model=_clean(selects["writer"].value),
                checker_model=_clean(selects["checker"].value),
                requirements_model=_clean(selects["requirements"].value),
            )

        def _names() -> list[str]:
            return model_names(*[_clean(selects[k].value) for k in selects])

        def _offer(status) -> None:
            if not status.reachable:
                return
            found = [m.name for m in status.models]
            for select in selects.values():
                value = _clean(select.value)
                options = list(found)
                if value and value not in options:
                    options.append(value)
                select.set_options(options, value=select.value or None)

        def save() -> None:
            ok = True
            url = _clean(address.value)
            notes["address"].set_text("" if url else ADDRESS_ERROR)
            ok = bool(url)
            for key, _label, _field, error in MODEL_FIELDS:
                empty = not _clean(selects[key].value)
                notes[key].set_text(error if empty else "")
                ok = ok and not empty
            if not ok:
                return
            save_settings(_current(url))
            ui.notify("Settings saved", type="positive")

        ui.button("Save settings", on_click=save).props(
            "unelevated no-caps color=primary"
        ).classes("mt-3")

        with section("Data folder"):
            ui.label(show_path(data_home())).classes("sr-code break-all")
            ui.label(DATA_SENTENCE).classes("sr-muted")

        with section("About"):
            ui.label(f"SecReqGen {__version__}").classes("sr-small")
            for sentence in ABOUT:
                ui.label(sentence).classes("sr-muted")


_CSS = """
.sr-field-error { color: #B3261E; font-size: 13px; min-height: 0; }
.sr-field-error:empty { display: none; }
"""
