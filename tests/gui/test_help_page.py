"""The page Help: the sections, the example and the real facts."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

pytestmark = pytest.mark.anyio

SECTIONS = [
    "Start",
    "Stories",
    "Documentation folder",
    "System context",
    "About the product",
    "Models",
    "Read the result",
    "Privacy",
]


async def test_help_page_holds_each_section_title(user):
    await user.open("/help")
    await user.should_see("Help")
    for title in SECTIONS:
        await user.should_see(title)
    await user.should_see("(Requirements, optional)")
    await user.should_see("(Threat list, optional)")


async def test_help_page_shows_the_json_example_as_text(user):
    await user.open("/help")
    await user.should_see('"user_stories": [')
    await user.should_see('"id": "MM-US-007"')
    await user.should_see('"acceptance_criteria": ["The bot has a token"')


async def test_help_page_shows_the_real_data_folder(user, gui_home):
    from secreqgen_gui.settings import data_home, show_path

    await user.open("/help")
    await user.should_see(show_path(data_home()))
    assert gui_home.is_dir()


async def test_help_page_names_the_two_tested_models(user):
    from secreqgen.threats import TESTED_WRITER
    from secreqgen.threats.pipeline import TESTED_CHECKER

    await user.open("/help")
    await user.should_see(f"Tested: {TESTED_WRITER} writes, {TESTED_CHECKER} checks")
    await user.should_see(f"ollama pull {TESTED_WRITER}")


async def test_help_page_has_the_main_instructions(user):
    await user.open("/help")
    await user.should_see("Home shows")
    await user.should_see("A threat list is a draft, not a complete threat model.")
    await user.should_see("The GUI has no login.")
    await user.should_see("SECREQGEN_GUI_HOME")
