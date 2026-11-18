"""Ollama, default models, data folder and about."""

from __future__ import annotations

from nicegui import run, ui
from secreqgen.application.server import check_model_server

from secreqgen_gui import __version__
from secreqgen_gui.pages.home import (
    STATUS_CSS,
    model_roles,
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

MODEL_FIELDS = (
    ("writer", "Writer", "writer_model", "Enter the writer model."),
    (
        "checker",
        "Checker",
        "checker_model",
        "Enter the checker model.",
    ),
    (
        "requirements",
        "Requirements model",
        "requirements_model",
        "Enter the requirements model.",
    ),
)
ADDRESS_ERROR = "Enter the address of Ollama."


def _clean(value: object) -> str:
    return str(value or "").strip()


def _tested_models() -> dict[str, str]:
    """Return the tested model for the writer and for the checker."""
    from secreqgen.threats import TESTED_WRITER
    from secreqgen.threats.pipeline import TESTED_CHECKER

    return {"writer": TESTED_WRITER, "checker": TESTED_CHECKER}


def page() -> None:
    ui.add_css(STATUS_CSS)
    ui.add_css(_CSS)
    settings = load_settings()
    tested = _tested_models()
    selects: dict[str, ui.select] = {}
    notes: dict[str, ui.label] = {}

    with ui.column().classes("w-full max-w-[640px] gap-0"):
        page_title("Settings")

        with section("Ollama", first=True):
            address = (
                ui.input("Address", value=settings.base_url)
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
                    ui.label("Checking Ollama").classes("sr-small")
                status = await run.io_bound(
                    check_model_server, make_config(_current(url))
                )
                result.clear()
                with result:
                    show_server_result(status, _roles(), url)
                _offer(status)

            ui.button("Check Ollama", on_click=check).props(
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
                if key in tested:
                    ui.label(f"Tested: {tested[key]}").classes("sr-small")
                notes[key] = ui.label().classes("sr-field-error")

        def _current(url: str) -> GuiSettings:
            return GuiSettings(
                base_url=url,
                writer_model=_clean(selects["writer"].value),
                checker_model=_clean(selects["checker"].value),
                requirements_model=_clean(selects["requirements"].value),
            )

        def _roles() -> list[tuple[str, str]]:
            return model_roles(
                _clean(selects["writer"].value),
                _clean(selects["checker"].value),
                _clean(selects["requirements"].value),
            )

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
            ui.notify("Saved.", type="positive")

        ui.button("Save", on_click=save).props(
            "unelevated no-caps color=primary"
        ).classes("mt-3")

        with section("Data folder"):
            ui.label(show_path(data_home())).classes("sr-code break-all")

        with section("About"):
            ui.label(f"SecReqGen {__version__}").classes("sr-small")


_CSS = """
.sr-field-error { color: #B3261E; font-size: 13px; min-height: 0; }
.sr-field-error:empty { display: none; }
"""
