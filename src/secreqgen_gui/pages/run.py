"""The progress of a run and, after it, its stories."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page(run_id: str) -> None:
    page_title(f"Run {run_id}")
    ui.label("This page is not built yet.")
