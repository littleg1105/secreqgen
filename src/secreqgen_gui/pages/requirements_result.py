"""The requirements of a run."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page(run_id: str) -> None:
    page_title("Requirements of the run")
    ui.label("This page is not built yet.")
