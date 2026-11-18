"""The page Help: the main instructions, in short lines."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import ui

from secreqgen_gui.settings import data_home, show_path
from secreqgen_gui.widgets import page_title, text

_STORY = (
    "As an admin, I want to create a bot account, "
    "so that a script can post messages."
)
EXAMPLE = (
    "{\n"
    '  "project": "Mattermost",\n'
    '  "user_stories": [\n'
    "    {\n"
    '      "id": "MM-US-007",\n'
    '      "title": "Bot and integration setup",\n'
    f'      "story": "{_STORY}",\n'
    '      "acceptance_criteria": ["The bot has a token", '
    '"An admin can disable the bot"]\n'
    "    }\n"
    "  ]\n"
    "}"
)

_CSS = """
.sr-help ul { list-style: disc; padding-left: 22px; margin: 0; }
.sr-help ol { list-style: decimal; padding-left: 22px; margin: 0; }
.sr-help li { padding: 2px 0; }
.sr-help-line { white-space: pre-wrap; overflow-wrap: anywhere; }
"""


def _parse(line: str) -> list[tuple[bool, str]]:
    """Split a line at the backticks. The parts between them are code."""
    pieces = line.split("`")
    return [(index % 2 == 1, piece) for index, piece in enumerate(pieces) if piece]


def _item(parts: list[tuple[bool, str]]) -> None:
    """Show one list item. Code parts use the code font."""
    with ui.element("li"):
        for is_code, piece in parts:
            classes = "inline sr-help-line"
            ui.label(piece).classes(f"{classes} sr-code" if is_code else classes)


def _list(lines: list[str], ordered: bool = False) -> None:
    with ui.element("ol" if ordered else "ul").classes("w-full"):
        for line in lines:
            _item(_parse(line))


@contextmanager
def _section(title: str, note: str = "", first: bool = False) -> Iterator[None]:
    """Show a section: a line, the title, and small text beside it (if any)."""
    classes = "w-full gap-2 pt-0 mt-4" if first else "w-full gap-2 pt-5 mt-6"
    box = ui.column().classes(classes)
    if not first:
        box.style("border-top: 1px solid #D8DEE4")
    with box:
        with ui.row().classes("items-baseline gap-2"):
            ui.label(title).classes("sr-section-title")
            if note:
                ui.label(note).classes("sr-small")
        yield


def page() -> None:
    from secreqgen.threats import TESTED_WRITER
    from secreqgen.threats.pipeline import TESTED_CHECKER

    ui.add_css(_CSS)
    with ui.column().classes("sr-help w-full max-w-[720px] gap-0"):
        page_title("Help")

        with _section("Start", first=True):
            _list(
                [
                    "Start Ollama: open the app, or run `ollama serve`. "
                    'Home shows "Ollama OK".',
                    'Open "Threat list" (draft threats, each with a quote) or '
                    '"Requirements" (security requirements with ASVS category).',
                    "Pick the stories and the documentation folder of the product.",
                    "Click the Start button. Old runs are in History.",
                ],
                ordered=True,
            )

        with _section("Stories"):
            _list(
                [
                    "A file in JSON or YAML (`.json`, `.yaml`, `.yml`) with a list "
                    "named `user_stories`.",
                    "Each story needs `story`. Optional: `id`, `title`, "
                    "`acceptance_criteria` (a list). Optional at the top: "
                    "`project`, the product name.",
                    'No file: "Use the example stories" or "Type a story".',
                ]
            )
            text(
                EXAMPLE,
                "sr-code p-3 w-full bg-[#F4F6F8] border border-[#D8DEE4] rounded",
            )

        with _section("Documentation folder"):
            _list(
                [
                    "The folder with the Markdown files (`.md`) of the product. "
                    "Subfolders count.",
                    '"Read other file types too": also `.rst`, `.adoc`, `.txt`, '
                    "`.mdx`, `.html`, `.pdf`, `.docx`, from a converted copy. "
                    "Your folder does not change.",
                ]
            )

        with _section("System context", "(Requirements, optional)"):
            _list(
                [
                    'A YAML file about the product. "Write one with the model" '
                    "makes a draft from a codebase folder. Check each line "
                    "against its source."
                ]
            )

        with _section("About the product", "(Threat list, optional)"):
            _list(
                [
                    "One paragraph, 120 words at most: what the product does and "
                    "who uses it. Background for the writer, not a source.",
                    '"Write one with the model": a model writes facts from a '
                    "codebase folder. SecReqGen joins them into the paragraph, "
                    "with no model.",
                ]
            )

        with _section("Models"):
            _list(
                [
                    f"Tested: {TESTED_WRITER} writes, {TESTED_CHECKER} checks, "
                    "with the tested prompts.",
                    f"Get a model: `ollama pull {TESTED_WRITER}`. "
                    "Change the models in Settings.",
                ]
            )

        with _section("Read the result"):
            _list(
                [
                    "A threat list is a draft, not a complete threat model. It "
                    "covers only what your documentation says.",
                    "Check each threat against its quote. Keep it or delete it. "
                    "Export saves the threats that you kept.",
                    "Flagged threats and threats with weak support were wrong more "
                    "often than the others in the thesis tests.",
                    "Requirements: check each one against its sources.",
                    "A tag on a run (untested prompts, untested file types, "
                    "generated context, generated paragraph) means: not in the "
                    "thesis tests.",
                ]
            )

        with _section("Privacy"):
            with ui.element("ul").classes("w-full"):
                _item(
                    _parse(
                        "The stories, the documentation and the code go to the "
                        "Ollama address of Settings. The default is this computer."
                    )
                )
                _item(_parse("The GUI has no login. Keep it on `127.0.0.1`."))
                _item(
                    _parse("Data folder: ")
                    + [(True, show_path(data_home()))]
                    + _parse(
                        ". For another folder, set `SECREQGEN_GUI_HOME` before "
                        "the start."
                    )
                )
