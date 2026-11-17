"""The start page: model server state, two entries, the newest runs."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page() -> None:
    page_title("Home")
    ui.label("This page is not built yet.")
