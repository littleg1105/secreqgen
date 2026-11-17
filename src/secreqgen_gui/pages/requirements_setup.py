"""The form for a requirements run."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page() -> None:
    page_title("Requirements")
    ui.label("This page is not built yet.")
