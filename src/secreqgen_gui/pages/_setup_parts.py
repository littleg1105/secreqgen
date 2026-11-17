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
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import check_story_upload, read_story_file
from secreqgen.application.prompt_sets import list_user_prompt_sets

from secreqgen_gui import jobs, storage
from secreqgen_gui.settings import expand_path, show_path
from secreqgen_gui.widgets import path_field

TESTED_SET = "__tested__"
FOLDER_MISSING = (
    "Folder not found. Choose the folder that holds the Markdown files of the product."
)
FOLDER_EMPTY = "The folder holds no Markdown file."
PRODUCT_NOTE = "Product name"
PRODUCT_LIMIT = 80


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
    """Count the Markdown files in a folder and in the folders below it."""
    total = 0
    for _root, _dirs, names in os.walk(folder):
        total += sum(1 for n in names if n.lower().endswith(".md"))
    return total


def prompt_set_choices(kind: str) -> dict[str, str]:
    """Return the choices of the prompt set select: tested first."""
    choices = {TESTED_SET: "Tested setup"}
    try:
        sets = list_user_prompt_sets(storage.prompt_sets_dir(), kind)
    except (OSError, InputError, ValueError):
        sets = []
    for info in sets:
        choices[info.name] = info.name
    return choices


def prompt_set_value(value: str | None) -> str | None:
    """Return the name of a user set, or None for the tested setup."""
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
        body = section("Stories")
        with body:
            with ui.tabs().props("dense align=left no-caps").classes("w-full") as tabs:
                tab_file = ui.tab("File on this computer")
                tab_up = ui.tab("Upload")
                tab_one = ui.tab("Type one story")
            with ui.tab_panels(tabs, value=tab_file).classes("w-full"):
                with ui.tab_panel(tab_file).classes("p-0 pt-3"):
                    self.field = path_field("Stories file (JSON or YAML)", initial)
                with ui.tab_panel(tab_up).classes("p-0 pt-3"):
                    self._upload_panel()
                with ui.tab_panel(tab_one).classes("p-0 pt-3"):
                    self._type_panel()
            self.problem = message("text-negative")
            self.info = message()
            self.skipped = message()
            self.list_box = ui.column().classes("w-full gap-1")
        self.field.props("debounce=300")
        self.field.on_value_change(lambda _e: self.read())
        for label in (self.problem, self.info, self.skipped):
            label.set_visibility(False)
        if initial:
            ui.timer(0.05, self.read, once=True)

    # ways to give a file

    def _upload_panel(self) -> None:
        ui.upload(
            on_upload=self._on_upload,
            on_rejected=lambda: set_message(
                self.problem, "The file must be a .json, .yaml or .yml file.", "bad"
            ),
            max_file_size=storage.MAX_UPLOAD_BYTES,
            max_files=1,
            auto_upload=True,
            label="Choose a stories file",
        ).props('accept=".json,.yaml,.yml" flat bordered').classes("w-full")

    async def _on_upload(self, e: events.UploadEventArguments) -> None:
        try:
            data = await e.file.read()
            check_story_upload(e.file.name, len(data))
            path = await run.io_bound(storage.save_upload, e.file.name, data)
        except (InputError, ValueError) as exc:
            set_message(self.problem, f"{exc} Choose another file.", "bad")
            return
        self.field.set_value(show_path(path))

    def _type_panel(self) -> None:
        story_id = ui.input("Story id", value="US-001").props("outlined dense")
        title = ui.input("Title").props("outlined dense").classes("w-full")
        story = (
            ui.textarea("Story")
            .props("outlined autogrow")
            .classes("w-full")
            .mark("story_text")
        )
        criteria = (
            ui.textarea("Acceptance criteria, one in each line")
            .props("outlined autogrow")
            .classes("w-full")
        )
        warn = message("text-negative")
        warn.set_visibility(False)

        async def use() -> None:
            if not story_id.value.strip() or not story.value.strip():
                set_message(
                    warn, "Give a story id and a story text, then choose again.", "bad"
                )
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
        set_message(
            self.info, f"{plural(len(self.stories), 'story', 'stories')} in the file."
        )
        skipped = len(result.skipped)
        set_message(
            self.skipped,
            f"{plural(skipped, 'entry', 'entries')} of the file could not be read "
            "and will not run."
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
                ui.button("Select all", on_click=lambda: self._set_all(True)).props(
                    "flat no-caps dense"
                )
                ui.button("Select none", on_click=lambda: self._set_all(False)).props(
                    "flat no-caps dense"
                )
            with ui.column().classes("w-full gap-0 max-h-[240px] overflow-auto"):
                for story in self.stories:
                    box = ui.checkbox(
                        f"{story.id}   {story.title}",
                        value=True,
                        on_change=lambda _e: self.on_change(),
                    ).props("dense")
                    self.checks[story.id] = box

    def _set_all(self, value: bool) -> None:
        for box in self.checks.values():
            box.set_value(value)
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
        self._token = 0
        self._auto_product = ""
        body = section("Documentation")
        with body:
            self.field = path_field("Documentation folder", initial, folder=True)
            self.info = message()
            self.info.set_visibility(False)
            self.product = (
                ui.input(PRODUCT_NOTE, value=product)
                .props("outlined dense")
                .classes("w-full")
            )
        self.field.props("debounce=300")
        self.field.on_value_change(lambda _e: self.check())
        self.product.on_value_change(lambda _e: self.on_change())
        if initial:
            ui.timer(0.05, self.check, once=True)

    async def check(self) -> None:
        self._token += 1
        token = self._token
        value = (self.field.value or "").strip()
        if not value:
            self.files = 0
            set_message(self.info, "")
            self.on_change()
            return
        folder = expand_path(value)
        isdir = await run.io_bound(folder.is_dir)
        count = await run.io_bound(count_markdown, folder) if isdir else 0
        if token != self._token:
            return
        self.files = count
        if not isdir:
            set_message(self.info, FOLDER_MISSING, "bad")
        elif count == 0:
            set_message(self.info, FOLDER_EMPTY, "bad")
        else:
            set_message(
                self.info,
                f"{plural(count, 'Markdown file', 'Markdown files')} in the folder.",
            )
        self.on_change()

    def fill_product(self) -> None:
        """Fill the product name from the story file when the person did not."""
        name = self.stories.project or ""
        if name and self.product.value in ("", self._auto_product):
            self.product.set_value(name)
            self._auto_product = name

    @property
    def path(self) -> Path | None:
        value = (self.field.value or "").strip()
        return expand_path(value) if value else None

    @property
    def ok(self) -> bool:
        return self.files > 0


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
                    ui.label("A run is in work.").classes("sr-small").style(
                        "color: var(--sr-caution)"
                    )
                    self.link_slot = ui.element("span")
        self._link_id: str | None = None
        ui.timer(0.5, self.update)
        self.update()

    def update(self) -> None:
        """Show the summary and switch the button on or off."""
        current = jobs.manager.busy()
        self.summary_label.set_text(self.summary())
        self.busy_row.set_visibility(current)
        state = jobs.manager.current() if current else None
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
    return f"{stories}, about {plural(count, 'minute', 'minutes')}."
