"""The shell of the GUI: the left rail, the content area and the routes.

This module does not call ui.run. That is done in __main__.py, so that the
tests can use the root function.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from nicegui import app, run, ui

from secreqgen_gui import storage, theme
from secreqgen_gui.jobs import (
    RequirementsRunSpec,
    manager,
    spec_from_dict,
)
from secreqgen_gui.pages import help as help_page
from secreqgen_gui.pages import (
    history,
    home,
    how_it_works,
    prompts,
    requirements_result,
    requirements_setup,
    threat_result,
    threat_setup,
)
from secreqgen_gui.pages import (
    run as run_page,
)
from secreqgen_gui.pages import (
    settings as settings_page,
)
from secreqgen_gui.settings import load_settings

log = logging.getLogger(__name__)

START_RUN_ENV = "SECREQGEN_GUI_START_RUN"
SERVER_CHECK_SECONDS = 30.0

# The entries of the rail: label, target, the path prefixes that mark it active.
NAV = (
    ("Home", "/", ()),
    ("Threat list", "/threats/new", ("/threats",)),
    ("Requirements", "/requirements/new", ("/requirements",)),
    ("History", "/history", ("/history", "/runs")),
    ("Prompts", "/prompts", ("/prompts",)),
    ("How it works", "/how-it-works", ("/how-it-works",)),
    ("Help", "/help", ("/help",)),
    ("Settings", "/settings", ("/settings",)),
)

# The route table: one single page app, no full reload between pages.
ROUTES = {
    "/": home.page,
    "/threats/new": threat_setup.page,
    "/requirements/new": requirements_setup.page,
    "/runs/{run_id}": run_page.page,
    "/runs/{run_id}/stories/{story_id}": threat_result.page,
    "/runs/{run_id}/requirements": requirements_result.page,
    "/history": history.page,
    "/prompts": prompts.page,
    "/prompts/{kind}/{name}": prompts.set_page,
    "/how-it-works": how_it_works.page,
    "/help": help_page.page,
    "/settings": settings_page.page,
}


def healthz() -> dict:
    """Answer a plain check that the server runs (the address /healthz).

    A script or a container asks this address, not a page. A page request
    with no browser behind it makes timers that never get a connection.
    """
    return {"ok": True}


def _is_active(label: str, prefixes: tuple[str, ...], path: str) -> bool:
    path = path.split("?")[0].split("#")[0].rstrip("/") or "/"
    if label == "Home":
        return path == "/"
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _rail() -> None:
    """Build the left rail: the name, eight entries, the Ollama line."""
    with ui.left_drawer(value=True, fixed=True, bordered=False).props(
        f"width={theme.RAIL_WIDTH} behavior=desktop"
    ).classes("p-0"):
        with ui.column().classes("sr-rail w-full gap-0"):
            ui.label("SecReqGen").classes("sr-rail-name")
            items = {}
            for label, target, prefixes in NAV:
                item = ui.label(label).classes("sr-nav-item w-full")
                item.on("click", lambda _e, t=target: ui.navigate.to(t))
                item.props("tabindex=0 role=link")
                item.on("keydown.enter", lambda _e, t=target: ui.navigate.to(t))
                items[label] = (item, prefixes)

            def mark(path: str) -> None:
                for label, (item, prefixes) in items.items():
                    if _is_active(label, prefixes, path):
                        item.classes(add="sr-nav-active")
                    else:
                        item.classes(remove="sr-nav-active")

            router = ui.context.client.sub_pages_router
            router.on_path_changed(mark)
            mark(router.current_path)
            _server_line()


def _server_line() -> None:
    """Build the Ollama status line at the bottom of the rail."""
    with ui.row().classes("sr-rail-server w-full items-center no-wrap"):
        dot = ui.element("span").classes("sr-dot")
        label = ui.label("Checking Ollama")

    async def update() -> None:
        url = load_settings().base_url
        status = await run.io_bound(manager.server_status, url)
        dot.classes(remove="sr-dot-ok sr-dot-bad")
        if status.reachable:
            dot.classes(add="sr-dot-ok")
            label.set_text("Ollama OK")
        else:
            dot.classes(add="sr-dot-bad")
            label.set_text("Ollama is not running")

    ui.timer(SERVER_CHECK_SECONDS, update)


def root() -> None:
    """Build the shell once for each browser tab."""
    theme.apply_page()
    ui.query(".nicegui-content").classes("p-0 gap-0")
    _rail()
    ui.sub_pages(ROUTES).classes("sr-main w-full")


def start_run_from_file(path: Path) -> str | None:
    """Start the run that a start file describes. Return the run id.

    The file is JSON: the key "kind" and the fields of the run spec. It is
    a way to start a run without the browser, for screenshots.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        spec = spec_from_dict(data)
        if isinstance(spec, RequirementsRunSpec):
            return manager.start_requirements(spec)
        return manager.start_threats(spec)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        log.warning("The start file %s was not used: %s", path.name, exc)
        return None


def _on_startup() -> None:
    storage.mark_interrupted_runs()
    storage.clear_about()
    storage.ensure_examples()
    value = os.environ.get(START_RUN_ENV, "").strip()
    if value:
        start_run_from_file(Path(value))


def setup() -> None:
    """Set the colours and the start hooks. Call it before ui.run."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from secreqgen_gui.settings import allowed_hosts

    theme.apply_app()
    # The server answers only a request that names this computer. This also
    # covers the socket path of NiceGUI, which is mounted on the same app.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.on_startup(_on_startup)
