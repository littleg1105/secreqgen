"""The prompt sets: the list, and one set to view or edit."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.widgets import page_title


def page() -> None:
    page_title("Prompts")
    ui.label("This page is not built yet.")


def set_page(kind: str, name: str) -> None:
    page_title(f"Prompt set {name}")
    ui.label("This page is not built yet.")
