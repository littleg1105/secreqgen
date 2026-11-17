"""Model server, default models, data folder and about."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page() -> None:
    page_title("Settings")
    ui.label("This page is not built yet.")
