"""Parts that the two setup forms share: stories, documentation, foot.

Each part keeps its own state and calls on_change when that state changes.
Text from a story file or from a folder is untrusted. It is shown as text.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from nicegui import events, run, ui
from secreqgen.application.documentation import convertible_files
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import (
    UPLOAD_EXTENSIONS,
    check_story_upload,
    read_story_file,
)
from secreqgen.application.prompt_sets import list_user_prompt_sets

from secreqgen_gui import context_job, form_memory, jobs, storage
from secreqgen_gui.form_memory import COMMON
from secreqgen_gui.settings import expand_path, show_path
from secreqgen_gui.widgets import path_field

TESTED_SET = "__tested__"
FOLDER_MISSING = "Folder not found. Check the path or click Browse."
NO_FILES = "This folder has no documentation files that the search can read."
WRONG_TYPE = "Use a .json, .yaml or .yml file."
STORIES_HINT = "JSON or YAML, with a list named user_stories"
EXAMPLES_HINT = "Eight generic stories that fit most products."
DOCS_HINT = "The folder with the documentation of the product"
OTHER_SWITCH = "Read other file types too"
BUSY = "Another run is in progress. Wait for it to end, or stop it."
UNTESTED_PROMPTS = "A run with these prompts gets the tag: untested prompts."
PRODUCT_NOTE = "Product name (optional)"
PRODUCT_LIMIT = 80
TABS = {"path": "File path", "upload": "Upload", "type": "Type a story"}
TYPED_DEFAULT = {"id": "US-001", "title": "", "story": "", "criteria": ""}
CLEAR_FORM = "Clear the form"


def clear_button(on_click: Callable[[], None]) -> None:
    """Show the link at the foot of a form that empties the memory."""
    ui.button(CLEAR_FORM, on_click=on_click).props("flat dense no-caps").classes(
        "sr-link-button"
    )


def initial_path(text: str | None) -> str:
    """Return a path from a query value in the short form for the field."""
    value = (text or "").strip()
    return show_path(expand_path(value)) if value else ""


def initial_product(text: str | None) -> str:
    """Return the product name from a query value, at most 80 characters."""
    return (text or "").strip()[:PRODUCT_LIMIT]


def plural(count: int, one: str, many: str) -> str:
    """Return the count with the right form of the noun."""
    return f"{count} {one if count == 1 else many}"


def section(title: str) -> ui.column:
    """Start a part of a form: a line, a title and a column for the body."""
    with ui.column().classes("w-full gap-3 pt-5 border-t border-[#D8DEE4]") as body:
        ui.label(title).classes("sr-section-title")
    return body


def message(classes: str = "") -> ui.label:
    """Return an empty label for a message under a field."""
    return ui.label().classes(f"sr-small {classes}".strip())


def set_message(label: ui.label, text: str, kind: str = "plain") -> None:
    """Show a message in the label. The kind is plain, bad or caution."""
    label.set_text(text)
    label.classes(remove="text-negative")
    label.style(replace="")
    if kind == "bad":
        label.classes(add="text-negative")
    elif kind == "caution":
        label.style("color: var(--sr-caution)")
    label.set_visibility(bool(text))


def count_markdown(folder: Path) -> int:
    """Count the Markdown files that the search reads.

    The search reads each file whose name ends in ".md" (small letters), in
    the folder and in every folder below it.
    """
    return sum(
        1 for _root, _dirs, names in os.walk(folder) for n in names if n.endswith(".md")
    )


def docs_line(markdown: int, others: dict[str, int], convert: bool) -> str:
    """Return the line under the documentation folder field.

    Markdown is the count of .md files. Others counts the files of other
    types by suffix. Convert is the state of the switch.
    """
    total = sum(others.values())
    if not total:
        if markdown:
            return f"{plural(markdown, 'Markdown file', 'Markdown files')} found."
        return NO_FILES
    kinds = ", ".join(sorted(others, key=lambda kind: (-others[kind], kind))[:3])
    one = total == 1
    if not markdown:
        if convert:
            verb = "is" if one else "are"
            return (
                f"No Markdown files. {plural(total, 'file', 'files')} of "
                f"{'another type' if one else 'other types'} {verb} converted "
                f"({kinds})."
            )
        rest = f"the {total} other {'file' if one else 'files'}"
        return (
            "This folder has no Markdown files. "
            f'Turn on "{OTHER_SWITCH}" to read {rest}.'
        )
    found = f"{plural(markdown, 'Markdown file', 'Markdown files')} found."
    verb = ("is" if one else "are") + (" converted" if convert else " not read")
    kind = "another type" if one else "other types"
    return f"{found} {plural(total, 'file', 'files')} of {kind} {verb} ({kinds})."


def prompt_set_choices(kind: str) -> dict[str, str]:
    """Return the choices of the prompt set select: tested first."""
    choices = {TESTED_SET: "Tested prompts"}
    try:
        sets = list_user_prompt_sets(storage.prompt_sets_dir(), kind)
    except (OSError, InputError, ValueError):
        sets = []
    for info in sets:
        choices[info.name] = info.name
    return choices


def prompt_set_value(value: str | None) -> str | None:
    """Return the name of a user set, or None for the tested prompts."""
    return None if value in (None, "", TESTED_SET) else value


class StoriesPart:
    """Part 1: the story file, three ways to give it, and the story choice."""

    def __init__(self, on_change: Callable[[], None], initial: str = "") -> None:
        self.on_change = on_change
        self.stories: list = []
        self.project: str | None = None
        self.checks: dict[str, ui.checkbox] = {}
        self._token = 0
        self.loaded: Path | None = None
        self.muted = False
        previous = form_memory.recall(COMMON, "stories_file", "")
        if initial != previous:
            form_memory.remember(COMMON, "stories_file", initial)
            form_memory.remember(COMMON, "story_ids", None)
        # The chosen stories wait for the first redraw of the list.
        self._restore = form_memory.recall(COMMON, "story_ids")
        if not isinstance(self._restore, list):
            self._restore = None
        saved = form_memory.recall(COMMON, "stories_tab", "path")
        if saved not in TABS:
            saved = "path"
        body = section("Stories")
        with body:
            with ui.tabs(value=saved).props("dense align=left no-caps").classes(
                "w-full"
            ) as tabs:
                self.tabs = tabs
                tab_file = ui.tab("path", TABS["path"])
                tab_up = ui.tab("upload", TABS["upload"])
                tab_one = ui.tab("type", TABS["type"])
            with ui.tab_panels(tabs, value=saved).classes("w-full"):
                with ui.tab_panel(tab_file).classes("p-0 pt-3"):
                    self.field = path_field(
                        "Stories file",
                        initial,
                        hint=STORIES_HINT,
                        start=storage.examples_dir(),
                    )
                    with ui.row().classes("items-baseline gap-3 pt-5"):
                        ui.button(
                            "Use the example stories", on_click=self._use_examples
                        ).props("flat dense no-caps").classes("sr-link-button")
                        ui.label(EXAMPLES_HINT).classes("sr-small")
                with ui.tab_panel(tab_up).classes("p-0 pt-3"):
                    self._upload_panel()
                with ui.tab_panel(tab_one).classes("p-0 pt-3"):
                    self._type_panel()
            self.problem = message("text-negative")
            self.info = message()
            self.skipped = message()
            self.list_box = ui.column().classes("w-full gap-1")
        self.field.props("debounce=300")
        self.field.on_value_change(self._field_changed)
        self.field.on_value_change(lambda _e: self.read())
        tabs.on_value_change(self._tab_changed)
        for label in (self.problem, self.info, self.skipped):
            label.set_visibility(False)
        if initial:
            ui.timer(0.05, self.read, once=True)

    # the memory

    def _field_changed(self, _e=None) -> None:
        """Remember the file. A different file forgets the chosen stories."""
        if self.muted:
            return
        value = (self.field.value or "").strip()
        if value != form_memory.recall(COMMON, "stories_file", ""):
            self._restore = None
            form_memory.remember(COMMON, "story_ids", None)
        form_memory.remember(COMMON, "stories_file", value)

    def _tab_changed(self, _e=None) -> None:
        if not self.muted:
            form_memory.remember(COMMON, "stories_tab", self.tabs.value)

    def _save_choice(self) -> None:
        """Remember the chosen stories (nothing when all are chosen)."""
        if self.muted or not self.checks:
            return
        form_memory.remember(
            COMMON, "story_ids", None if self.all_selected else list(self.selected)
        )

    def reset(self) -> None:
        """Show the defaults in place. The caller empties the memory."""
        self.muted = True
        self._restore = None
        self.tabs.set_value("path")
        for name, box in self._typed.items():
            box.set_value(TYPED_DEFAULT[name])
        self.field.set_value("")
        self.muted = False

    # ways to give a file

    async def _use_examples(self) -> None:
        """Put the example stories of the GUI into the field."""
        path = await run.io_bound(storage.ensure_examples)
        self.field.set_value(show_path(path))

    def _upload_panel(self) -> None:
        ui.upload(
            on_upload=self._on_upload,
            on_rejected=lambda: set_message(
                self.problem, WRONG_TYPE, "bad"
            ),
            max_file_size=storage.MAX_UPLOAD_BYTES,
            max_files=1,
            auto_upload=True,
            label="Pick a stories file",
        ).props('accept=".json,.yaml,.yml" flat bordered').classes("w-full")

    async def _on_upload(self, e: events.UploadEventArguments) -> None:
        try:
            data = await e.file.read()
            check_story_upload(e.file.name, len(data))
            path = await run.io_bound(storage.save_upload, e.file.name, data)
        except InputError:
            # The GUI shows its own text in place of the text of the tool.
            wrong = Path(e.file.name).suffix.lower() not in UPLOAD_EXTENSIONS
            reason = WRONG_TYPE if wrong else "The file is larger than 1 MB."
            set_message(self.problem, f"{reason} Pick another file.", "bad")
            return
        except ValueError as exc:
            set_message(self.problem, f"{exc} Pick another file.", "bad")
            return
        self.field.set_value(show_path(path))

    def _type_panel(self) -> None:
        saved = form_memory.recall(COMMON, "typed_story", {})
        if not isinstance(saved, dict):
            saved = {}

        def old(name: str) -> str:
            value = saved.get(name)
            return value if isinstance(value, str) else TYPED_DEFAULT[name]

        story_id = ui.input("Story ID", value=old("id")).props("outlined dense")
        title = (
            ui.input("Title", value=old("title"))
            .props("outlined dense")
            .classes("w-full")
        )
        story = (
            ui.textarea("Story", value=old("story"))
            .props("outlined autogrow")
            .classes("w-full")
            .mark("story_text")
        )
        criteria = (
            ui.textarea("Acceptance criteria (one in each line)", value=old("criteria"))
            .props("outlined autogrow")
            .classes("w-full")
        )
        self._typed = {
            "id": story_id,
            "title": title,
            "story": story,
            "criteria": criteria,
        }

        def keep(_e=None) -> None:
            if self.muted:
                return
            data = {name: box.value or "" for name, box in self._typed.items()}
            form_memory.remember(
                COMMON, "typed_story", None if data == TYPED_DEFAULT else data
            )

        for box in self._typed.values():
            box.on_value_change(keep)
        warn = message("text-negative")
        warn.set_visibility(False)

        async def use() -> None:
            if not story_id.value.strip() or not story.value.strip():
                set_message(warn, "Enter a story ID and the story text.", "bad")
                return
            lines = [x.strip() for x in (criteria.value or "").splitlines()]
            item = {
                "id": story_id.value.strip(),
                "title": (title.value or "").strip() or story_id.value.strip(),
                "story": story.value.strip(),
                "acceptance_criteria": [x for x in lines if x],
            }
            data = json.dumps({"user_stories": [item]}, indent=2).encode("utf-8")
            name = "story-" + "".join(
                c if c.isalnum() or c in "-_" else "_" for c in item["id"]
            )
            try:
                path = await run.io_bound(storage.save_upload, f"{name}.json", data)
            except ValueError as exc:
                set_message(warn, f"{exc} Change the text and try again.", "bad")
                return
            set_message(warn, "")
            self.field.set_value(show_path(path))

        ui.button("Use this story", on_click=use).props("outline no-caps unelevated")

    # reading the file

    async def read(self) -> None:
        """Read the story file in the background and show the stories."""
        self._token += 1
        token = self._token
        value = (self.field.value or "").strip()
        if not value:
            self._clear("")
            return
        path = expand_path(value)
        try:
            result = await run.io_bound(read_story_file, path)
        except Exception as exc:  # the text of the error is the message
            if token == self._token:
                self._clear(str(exc))
            return
        if token != self._token:
            return
        self.stories = list(result.stories)
        self.project = result.project
        self.loaded = path
        set_message(self.problem, "")
        found = len(self.stories)
        set_message(self.info, f"{plural(found, 'story', 'stories')} found.")
        skipped = len(result.skipped)
        set_message(
            self.skipped,
            f"{plural(skipped, 'story', 'stories')} could not be read "
            f"and {'is' if skipped == 1 else 'are'} skipped."
            if skipped
            else "",
            "caution",
        )
        self._draw_list()
        self.on_change()

    def _clear(self, problem: str) -> None:
        self.stories = []
        self.project = None
        self.loaded = None
        self.checks = {}
        self.list_box.clear()
        set_message(self.problem, problem, "bad")
        set_message(self.info, "")
        set_message(self.skipped, "")
        self.on_change()

    def _draw_list(self) -> None:
        self.list_box.clear()
        self.checks = {}
        with self.list_box:
            with ui.row().classes("gap-2"):
                ui.button("Pick all", on_click=lambda: self._set_all(True)).props(
                    "flat no-caps dense"
                )
                ui.button("Pick none", on_click=lambda: self._set_all(False)).props(
                    "flat no-caps dense"
                )
            with ui.column().classes("w-full gap-0 max-h-[240px] overflow-auto"):
                chosen = self._chosen_ids()
                for story in self.stories:
                    box = ui.checkbox(
                        f"{story.id}   {story.title}",
                        value=chosen is None or story.id in chosen,
                        on_change=lambda _e: self._box_changed(),
                    ).props("dense")
                    self.checks[story.id] = box
        self._restore = None

    def _box_changed(self) -> None:
        self._save_choice()
        self.on_change()

    def _chosen_ids(self) -> set[str] | None:
        """Return the remembered ids that are in this file, or None for all."""
        if self._restore is None:
            return None
        if not self._restore:
            return set()  # the person picked none
        known = {story.id for story in self.stories} & set(self._restore)
        return known or None

    def _set_all(self, value: bool) -> None:
        for box in self.checks.values():
            box.set_value(value)
        self._save_choice()
        self.on_change()

    # what the form reads

    @property
    def selected(self) -> tuple[str, ...]:
        return tuple(sid for sid, box in self.checks.items() if box.value)

    @property
    def all_selected(self) -> bool:
        return bool(self.checks) and all(b.value for b in self.checks.values())

    @property
    def path(self) -> Path | None:
        return self.loaded


class DocsPart:
    """Part 2: the documentation folder and the product name."""

    def __init__(
        self,
        on_change: Callable[[], None],
        stories: StoriesPart,
        initial: str = "",
        product: str = "",
    ) -> None:
        self.on_change = on_change
        self.stories = stories
        self.files = 0
        self.others: dict[str, int] = {}
        self.isdir = False
        self._token = 0
        self._auto_product = ""
        self.muted = False
        form_memory.remember(COMMON, "docs", initial)
        form_memory.remember(COMMON, "product", product)
        body = section("Documentation")
        with body:
            self.field = path_field(
                "Documentation folder", initial, folder=True, hint=DOCS_HINT
            )
            on = bool(form_memory.recall(COMMON, "other_formats", True))
            self.switch = ui.switch(OTHER_SWITCH, value=on).mark("other_formats")
            self.info = message()
            self.info.set_visibility(False)
            self.product = (
                ui.input(PRODUCT_NOTE, value=product)
                .props("outlined dense")
                .classes("w-full")
            )
        self.field.props("debounce=300")
        self.field.on_value_change(self._field_changed)
        self.field.on_value_change(lambda _e: self.check())
        self.product.on_value_change(self._product_changed)
        self.switch.on_value_change(self._switch_changed)
        if initial:
            ui.timer(0.05, self.check, once=True)

    def _switch_changed(self, _e=None) -> None:
        """Remember the switch and show the line again. No new scan."""
        if not self.muted:
            form_memory.remember(COMMON, "other_formats", bool(self.switch.value))
        self._show()
        self.on_change()

    def _show(self) -> None:
        """Show the line under the field from the last scan."""
        if not (self.field.value or "").strip():
            set_message(self.info, "")
        elif not self.isdir:
            set_message(self.info, FOLDER_MISSING, "bad")
        else:
            line = docs_line(self.files, self.others, bool(self.switch.value))
            set_message(self.info, line, "plain" if self.ok else "bad")

    def _field_changed(self, _e=None) -> None:
        if not self.muted:
            form_memory.remember(COMMON, "docs", (self.field.value or "").strip())

    def _product_changed(self, _e=None) -> None:
        """Remember only a name that the person typed."""
        value = (self.product.value or "").strip()
        if not self.muted and value != self._auto_product:
            form_memory.remember(COMMON, "product", value)
        self.on_change()

    def reset(self) -> None:
        """Show the defaults in place. The caller empties the memory."""
        self.muted = True
        self._auto_product = ""
        self.field.set_value("")
        self.product.set_value("")
        self.switch.set_value(True)
        self.muted = False

    async def check(self) -> None:
        self._token += 1
        token = self._token
        value = (self.field.value or "").strip()
        if not value:
            self.files = 0
            self.others = {}
            self.isdir = False
            set_message(self.info, "")
            self.on_change()
            return
        folder = expand_path(value)
        isdir = await run.io_bound(folder.is_dir)
        count = await run.io_bound(count_markdown, folder) if isdir else 0
        others = await run.io_bound(convertible_files, folder) if isdir else {}
        if token != self._token:
            return
        self.files, self.others, self.isdir = count, others, isdir
        self._show()
        self.on_change()

    def fill_product(self) -> None:
        """Fill the product name from the story file when the person did not."""
        name = self.stories.project or ""
        if name and self.product.value in ("", self._auto_product):
            self._auto_product = name
            self.product.set_value(name)

    @property
    def path(self) -> Path | None:
        value = (self.field.value or "").strip()
        return expand_path(value) if value else None

    @property
    def other_formats(self) -> bool:
        return bool(self.switch.value)

    @property
    def ok(self) -> bool:
        """True when the folder has files that the run can read."""
        if self.files > 0:
            return True
        return bool(self.switch.value) and sum(self.others.values()) > 0


class Foot:
    """The foot of a form: a summary, the start button and the busy line."""

    def __init__(
        self,
        label: str,
        ready: Callable[[], bool],
        summary: Callable[[], str],
        start: Callable[[], Awaitable[None]],
    ) -> None:
        self.ready = ready
        self.summary = summary
        with ui.column().classes("w-full gap-2 pt-5 border-t border-[#D8DEE4]"):
            self.summary_label = ui.label().classes("sr-small")
            with ui.row().classes("items-center gap-4"):
                self.button = (
                    ui.button(label, on_click=start)
                    .props("unelevated no-caps color=primary")
                    .mark("start")
                )
                self.busy_row = ui.row().classes("items-center gap-2")
                with self.busy_row:
                    ui.label(BUSY).classes("sr-small").style(
                        "color: var(--sr-caution)"
                    )
                    self.link_slot = ui.element("span")
        self._link_id: str | None = None
        ui.timer(0.5, self.update)
        self.update()

    def update(self) -> None:
        """Show the summary and switch the button on or off."""
        running = jobs.manager.busy()
        current = running or context_job.writer.busy()
        self.summary_label.set_text(self.summary())
        self.busy_row.set_visibility(current)
        self.link_slot.set_visibility(running)
        state = jobs.manager.current() if running else None
        run_id = state.id if state else ""
        if run_id != self._link_id:
            self._link_id = run_id
            self.link_slot.clear()
            with self.link_slot:
                ui.link("Open the run", f"/runs/{run_id}" if run_id else "/history")
        self.button.set_enabled(self.ready() and not current)


def about_summary(count: int) -> str:
    """Return the line of the foot: the stories and the time."""
    stories = plural(count, "story", "stories")
    return f"{stories}, about {plural(count, 'minute', 'minutes')}"
