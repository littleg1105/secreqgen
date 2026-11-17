"""The threat list of one story with the evidence panel."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page(run_id: str, story_id: str, row: str | None = None) -> None:
    page_title(f"Threat list of {story_id}")
    ui.label("This page is not built yet.")
