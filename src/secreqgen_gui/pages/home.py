"""The start page: Ollama state, two entries, the recent runs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import run, ui
from secreqgen.application.server import check_model_server, model_available

from secreqgen_gui import storage
from secreqgen_gui.jobs import manager
from secreqgen_gui.settings import load_settings, make_config
from secreqgen_gui.widgets import empty_state, page_title

PURPOSE = "Threat lists and security requirements from your user stories."
NEWEST = 5
KIND_WORDS = {"threats": "Threat list", "requirements": "Requirements"}
STATE_WORDS = {
    "finished": "Done",
    "running": "Running",
    "checking": "Starting",
    "stopped": "Stopped",
    "aborted": "Failed",
    "failed": "Failed",
    "interrupted": "Interrupted",
    "damaged": "Unreadable",
    "unknown": "Unknown",
}
STATE_HINTS = {
    "interrupted": "The GUI closed during this run.",
    "damaged": "The files of this run cannot be read. Delete it.",
}


@contextmanager
def section(title: str, first: bool = False) -> Iterator[None]:
    """Show a part of a page: a line, a section title (if any) and the content."""
    classes = "w-full gap-3 pt-5 mt-6"
    if first:
        classes = "w-full gap-3 pt-0 mt-4"
    box = ui.column().classes(classes)
    if not first:
        box.style("border-top: 1px solid #D8DEE4")
    with box:
        if title:
            ui.label(title).classes("sr-section-title")
        yield


def model_roles(writer: str, checker: str, requirements: str) -> list[tuple[str, str]]:
    """Return the pairs (role, model name) of the three model settings."""
    return [
        ("writer", writer),
        ("checker", checker),
        ("requirements", requirements),
    ]


def _missing_lines(status, roles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return (name, roles text) for each model that Ollama does not have."""
    by_name: dict[str, list[str]] = {}
    for role, name in roles:
        name = (name or "").strip()
        if name:
            by_name.setdefault(name, []).append(role)
    return [
        (name, ", ".join(found))
        for name, found in by_name.items()
        if not model_available(status, name)
    ]


def show_server_result(status, roles: list[tuple[str, str]], address: str) -> None:
    """Show the answer of the Ollama check: one status row, then the gaps."""
    with ui.column().classes("gap-1"):
        if status.reachable:
            names = ", ".join(m.name for m in status.models) or "none"
            with ui.row().classes("items-baseline gap-3"):
                ui.label("Ollama OK").classes("sr-ok")
                ui.label(f"Models: {names}").classes("sr-small")
            for name, role_text in _missing_lines(status, roles):
                ui.label(
                    f"Not in Ollama: {name} ({role_text}). Run: ollama pull {name}"
                ).classes("sr-caution")
        else:
            ui.label(
                f"Ollama is not running at {address}. "
                "Start Ollama, then check again."
            ).classes("sr-bad")


def _run_line(run_data: dict) -> str:
    return KIND_WORDS.get(run_data.get("kind", ""), "Run")


def when_text(run_data: dict) -> str:
    """Return the date and time of a run, in the local time zone."""
    return storage.run_date_text(run_data) or str(run_data.get("id", ""))


def stories_text(run_data: dict) -> str:
    """Return the number of stories of a run in words."""
    total = run_data.get("stories_total")
    if not isinstance(total, int):
        total = len(run_data.get("per_story") or [])
    return f"{total} story" if total == 1 else f"{total} stories"


def state_text(run_data: dict) -> str:
    status = str(run_data.get("status", "unknown"))
    return STATE_WORDS.get(status, status.capitalize())


def state_hint(run_data: dict) -> str:
    """Return the hover text of a run state, or an empty text."""
    return STATE_HINTS.get(str(run_data.get("status", "")), "")


def _banner_text(state) -> str:
    kind = "threat list" if state.kind == "threats" else "requirements"
    return f"A {kind} run is in progress."


def _run_banner() -> None:
    @ui.refreshable
    def banner() -> None:
        state = manager.current()
        if state is None or state.state not in ("checking", "running"):
            return
        with ui.row().classes("sr-banner w-full items-center gap-4"):
            ui.label(_banner_text(state))
            ui.link("Open the run", f"/runs/{state.id}").classes("sr-link")

    banner()
    seen = {"key": None}

    def poll() -> None:
        state = manager.current()
        key = None if state is None else (state.id, state.version, state.state)
        if key != seen["key"]:
            seen["key"] = key
            banner.refresh()

    ui.timer(1.0, poll)


def _server_part() -> None:
    settings = load_settings()
    roles = model_roles(
        settings.writer_model, settings.checker_model, settings.requirements_model
    )
    address = settings.base_url
    result = ui.column().classes("gap-1")

    async def check() -> None:
        result.clear()
        with result:
            ui.label("Checking Ollama").classes("sr-small")
        status = await run.io_bound(check_model_server, make_config(settings))
        result.clear()
        with result:
            show_server_result(status, roles, address)

    ui.button("Check again", on_click=check).props("outline no-caps unelevated")
    ui.timer(0.1, check, once=True)


def _entry(title: str, fragment: str, button: str, target: str) -> None:
    with ui.column().classes("gap-2 grow basis-0 min-w-[260px] items-start"):
        ui.label(title).classes("sr-entry-title")
        ui.label(fragment).classes("sr-muted max-w-[44ch]")
        ui.button(button, on_click=lambda: ui.navigate.to(target)).props(
            "unelevated no-caps color=primary"
        ).classes("mt-1")


def _newest_runs() -> None:
    runs = storage.list_runs()[:NEWEST]
    if not runs:
        empty_state("No runs yet.")
        return
    with ui.column().classes("w-full gap-0"):
        for item in runs:
            link = ui.link(target=f"/runs/{item['id']}").classes(
                "sr-run-line w-full"
            )
            with link:
                ui.label(when_text(item)).classes("sr-run-when")
                ui.label(_run_line(item)).classes("sr-run-kind")
                ui.label(str(item.get("product") or "")).classes("sr-run-product")
                ui.label(stories_text(item)).classes("sr-run-stories")
                label = ui.label(state_text(item)).classes("sr-run-state")
                if state_hint(item):
                    label.tooltip(state_hint(item))
    ui.link("All runs", "/history").classes("mt-1 sr-link")


def page() -> None:
    ui.add_css(STATUS_CSS)
    ui.add_css(_CSS)
    with ui.column().classes("w-full max-w-[900px] gap-0"):
        page_title("SecReqGen", PURPOSE)
        _run_banner()
        with section("", first=True):
            _server_part()
        with section(""):
            with ui.row().classes("w-full gap-10 no-wrap items-start"):
                _entry(
                    "Threat list",
                    "Draft threats, each with a quote",
                    "New threat list",
                    "/threats/new",
                )
                _entry(
                    "Requirements",
                    "Security requirements with ASVS category",
                    "New requirements",
                    "/requirements/new",
                )
        with section("Recent runs"):
            _newest_runs()


STATUS_CSS = """
.sr-ok { color: #1B7F4B; }
.sr-bad { color: #B3261E; }
.sr-caution { color: #8A5A00; }
.sr-muted { color: #5B6570; }
"""
_CSS = """
.sr-link { color: #1D3A6B; }
.sr-entry-title { font-size: 15px; font-weight: 600; }
.sr-banner {
  margin-top: 16px; padding: 8px 12px; background: #F4F6F8;
  border-left: 3px solid #1D3A6B; border-radius: 4px;
}
.sr-run-line {
  display: grid; grid-template-columns: 190px 120px 1fr 90px 110px;
  gap: 12px; align-items: baseline; padding: 9px 4px;
  border-bottom: 1px solid #D8DEE4; color: #1B1F23; text-decoration: none;
}
.sr-run-line:hover { background: #F4F6F8; }
.sr-run-when, .sr-run-stories { color: #5B6570; font-size: 13px; }
.sr-run-kind { font-weight: 600; }
"""
