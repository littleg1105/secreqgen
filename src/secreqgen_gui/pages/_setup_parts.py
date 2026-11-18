"""Parts that the two setup forms share: stories, documentation, foot.

Each part keeps its own state and calls on_change when that state changes.
Text from a story file or from a folder is untrusted. It is shown as text.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from nicegui import events, run, ui
from secreqgen.application.documentation import count_documentation
from secreqgen.application.errors import InputError
from secreqgen.application.inputs import (
    UPLOAD_EXTENSIONS,
    check_story_upload,
    read_story_file,
)
from secreqgen.application.prompt_sets import list_user_prompt_sets

from secreqgen_gui import context_job, form_memory, jobs, storage
from secreqgen_gui.form_memory import COMMON
from secreqgen_gui.settings import expand_path, saves, show_path
from secreqgen_gui.widgets import CommandBlock, path_field

TESTED_SET = "__tested__"
FOLDER_MISSING = "Folder not found. Check the path or click Browse."
FILE_MISSING = "File not found. Check the path or click Browse."
READING = "Reading the folder."
NO_FILES = "This folder has no documentation files that the search can read."
WRONG_TYPE = "Use a .json, .yaml or .yml file."
STORIES_HINT = "JSON or YAML, with a list named user_stories"
EXAMPLES_HINT = "Eight generic stories that fit most products."
DOCS_HINT = "The folder with the documentation of the product"
OTHER_SWITCH = "Read other file types too"
BUSY = "Another run is in progress. Wait for it to end, or stop it."
CONTEXT_BUSY = "A model is writing a system context. Wait for it to end."
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
    return f"{count:,} {one if count == 1 else many}"


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


def reason_text(exc: Exception) -> str:
    """Return the text of an error as a sentence that ends in a full stop."""
    text = str(exc).strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def folders_text(folders: tuple[str, ...]) -> str:
    """Return the names of dependency folders: two are named, the rest counted."""
    if len(folders) <= 2:
        return " and ".join(folders)
    more = len(folders) - 2
    rest = f"{more} more {'folder' if more == 1 else 'folders'}"
    return f"{folders[0]}, {folders[1]} and {rest}"


def docs_line(
    markdown: int,
    others: dict[str, int],
    convert: bool,
    in_dependencies: int = 0,
    dependency_folders: tuple[str, ...] = (),
) -> str:
    """Return the line under the documentation folder field.

    Markdown is the count of .md files. In_dependencies is the part of them
    that is in folders such as node_modules. Others counts the files of other
    types by suffix. Convert is the state of the switch.
    """
    total = sum(others.values())
    found = f"{plural(markdown, 'Markdown file', 'Markdown files')} found."
    if markdown and in_dependencies and dependency_folders:
        be = "is" if in_dependencies == 1 else "are"
        place = folders_text(dependency_folders)
        found += f" {in_dependencies:,} {be} in {place}."
    if not total:
        return found if markdown else NO_FILES
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
        rest = f"the {total:,} other {'file' if one else 'files'}"
        return (
            "This folder has no Markdown files. "
            f'Turn on "{OTHER_SWITCH}" to read {rest}.'
        )
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
        self.project_is_text = True  # False: the raw key project is not text
        self.checks: dict[str, ui.checkbox] = {}
        self._token = 0
        self.loaded: Path | None = None
        self.muted = False
        self.pick_count = 0  # how often the person changed the file
        self.named_pick: int | None = (
            None  # the pick of the file that named the project
        )
        previous = form_memory.recall(COMMON, "stories_file", "", str)
        if initial != previous:
            form_memory.remember(COMMON, "stories_file", initial)
            form_memory.remember(COMMON, "story_ids", None)
        # The chosen stories wait for the first redraw of the list.
        self._restore = form_memory.recall(COMMON, "story_ids", None, list)
        saved = form_memory.recall(COMMON, "stories_tab", "path", str)
        if saved not in TABS:
            saved = "path"
        body = section("Stories")
        with body:
            with (
                ui.tabs(value=saved)
                .props("dense align=left no-caps")
                .classes("w-full") as tabs
            ):
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
        self.pick_count += 1
        value = (self.field.value or "").strip()
        if value != form_memory.recall(COMMON, "stories_file", "", str):
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
        # No story file is left, so no project name is left.
        self._token += 1
        self.named_pick = None
        self._clear("")

    # ways to give a file

    async def _use_examples(self) -> None:
        """Put the example stories of the GUI into the field."""
        path = await run.io_bound(storage.ensure_examples)
        self.field.set_value(show_path(path))

    def _upload_panel(self) -> None:
        ui.upload(
            on_upload=self._on_upload,
            on_rejected=lambda: set_message(self.problem, WRONG_TYPE, "bad"),
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
            set_message(self.problem, f"{reason_text(exc)} Pick another file.", "bad")
            return
        self.field.set_value(show_path(path))

    def _type_panel(self) -> None:
        saved = form_memory.recall(COMMON, "typed_story", {}, dict)

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
        pick = self.pick_count
        value = (self.field.value or "").strip()
        if not value:
            self._clear("")
            return
        path = expand_path(value)
        if self.loaded is not None and path != self.loaded:
            # A new file is being read: the old file must not stay the choice.
            self.loaded = None
            self.on_change()
        try:
            if not await run.io_bound(path.is_file):
                raise FileNotFoundError(FILE_MISSING)
            result = await run.io_bound(read_story_file, path)
            text_key = await run.io_bound(jobs.project_key_is_text, path)
        except Exception as exc:  # the text of the error is the message
            if token == self._token:
                missing = isinstance(exc, FileNotFoundError)
                text = (
                    FILE_MISSING
                    if missing
                    else f"{reason_text(exc)} Pick another file."
                )
                self._clear(text)
                if not pick and self.tabs.value != "path":
                    # A remembered file failed: show the path that it came from.
                    self.muted = True
                    self.tabs.set_value("path")
                    self.muted = False
            return
        if token != self._token:
            return
        self.stories = list(result.stories)
        self.project = result.project
        self.project_is_text = text_key
        self.named_pick = pick if result.project else None
        self.loaded = path
        set_message(self.problem, "")
        found = len(self.stories)
        set_message(self.info, f"{plural(found, 'story', 'stories')} found.")
        skipped = len(result.skipped)
        set_message(
            self.skipped,
            (
                f"{plural(skipped, 'story', 'stories')} could not be read "
                f"and {'is' if skipped == 1 else 'are'} skipped."
                if skipped
                else ""
            ),
            "caution",
        )
        self._draw_list()
        self.on_change()

    def _clear(self, problem: str) -> None:
        self.stories = []
        self.project = None
        self.project_is_text = True
        self.loaded = None
        self.checks = {}
        self.named_pick = None
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

    @property
    def requirements_default(self) -> str | None:
        """Return the product name that the command for requirements takes.

        It is None when the raw key project is not text: the command then
        gets the name with --product.
        """
        return self.project if self.project_is_text else None


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
        self.in_dependencies = 0
        self.dependency_folders: tuple[str, ...] = ()
        self.others: dict[str, int] = {}
        self.isdir = False
        self.scanning = bool(initial.strip())  # the first scan starts at once
        self._token = 0
        self._auto_product = ""
        self._typed_pick = -1  # the pick of the story file at the last typing
        self.muted = False
        form_memory.remember(COMMON, "docs", initial)
        form_memory.remember(COMMON, "product", product)
        body = section("Documentation")
        with body:
            self.field = path_field(
                "Documentation folder", initial, folder=True, hint=DOCS_HINT
            )
            on = form_memory.recall(COMMON, "other_formats", True, bool)
            self.switch = ui.switch(OTHER_SWITCH, value=on).mark("other_formats")
            self.info = message()
            self.info.set_visibility(False)
            if self.scanning:
                set_message(self.info, READING)
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
        """Remember the switch and show the line again.

        The counts of the last scan serve both states of the switch. A folder
        that was missing is read again: it can be back.
        """
        if not self.muted:
            form_memory.remember(COMMON, "other_formats", bool(self.switch.value))
        value = (self.field.value or "").strip()
        if value and not self.isdir and not self.scanning and not self.muted:
            ui.timer(0.01, self.check, once=True)
        self._show()
        self.on_change()

    def _show(self) -> None:
        """Show the line under the field from the last scan."""
        if not (self.field.value or "").strip():
            set_message(self.info, "")
        elif self.scanning:
            set_message(self.info, READING)
        elif not self.isdir:
            set_message(self.info, FOLDER_MISSING, "bad")
        else:
            line = docs_line(
                self.files,
                self.others,
                bool(self.switch.value),
                self.in_dependencies,
                self.dependency_folders,
            )
            set_message(self.info, line, "plain" if self.ok else "bad")

    def _field_changed(self, _e=None) -> None:
        if not self.muted:
            form_memory.remember(COMMON, "docs", (self.field.value or "").strip())

    def _product_changed(self, _e=None) -> None:
        """Remember only a name that the person typed."""
        value = (self.product.value or "").strip()
        if not self.muted and value != self._auto_product:
            form_memory.remember(COMMON, "product", value)
            self._typed_pick = self.stories.pick_count
        self.on_change()

    def reset(self) -> None:
        """Show the defaults in place. The caller empties the memory."""
        self.muted = True
        self._auto_product = ""
        self._typed_pick = -1
        self.field.set_value("")
        self.product.set_value("")
        self.switch.set_value(True)
        self.muted = False
        self._token += 1  # a scan in progress must not show its result
        self._forget_scan()
        self.scanning = False
        set_message(self.info, "")

    def _forget_scan(self) -> None:
        self.files = 0
        self.in_dependencies = 0
        self.dependency_folders = ()
        self.others = {}
        self.isdir = False

    async def check(self) -> None:
        """Read the folder in the background. The old result is never shown."""
        self._token += 1
        token = self._token
        value = (self.field.value or "").strip()
        self._forget_scan()
        if not value:
            self.scanning = False
            set_message(self.info, "")
            self.on_change()
            return
        self.scanning = True
        set_message(self.info, READING)
        self.on_change()
        folder = expand_path(value)
        isdir = await run.io_bound(folder.is_dir)
        count = await run.io_bound(count_documentation, folder) if isdir else None
        if token != self._token:
            return
        if count is not None:
            self.files = count.markdown
            self.in_dependencies = count.in_dependencies
            self.dependency_folders = count.dependency_folders
            self.others = dict(count.others)
        self.isdir = isdir
        self.scanning = False
        self._show()
        self.on_change()

    async def still_there(self) -> bool:
        """Check once more that the folder is there. Show the text if not."""
        folder = self.path
        if folder is None:
            return False
        if await run.io_bound(folder.is_dir):
            return True
        self._token += 1  # a scan in progress must not show its old result
        self._forget_scan()
        self.scanning = False
        self._show()
        self.on_change()
        return False

    def fill_product(self) -> None:
        """Fill the product name from the story file.

        An empty box, or a name that came from a file, takes the project name.
        So does any name, when the person picked another stories file after
        the typing and that file names a project.
        """
        name = self.stories.project or ""
        if not name:
            return
        pick = self.stories.named_pick
        another = pick is not None and pick > 0 and self._typed_pick != pick
        if another or self.product.value in ("", self._auto_product):
            self._auto_product = name
            if self.product.value != name:
                self.product.set_value(name)
            if another:
                form_memory.remember(COMMON, "product", None)

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
        if self.scanning:
            return False
        if self.files > 0:
            return True
        return bool(self.switch.value) and sum(self.others.values()) > 0


class Foot:
    """The foot of a form: a summary, the start button and the busy line.

    Under the button the block shows the command of the run. The function
    command gives (command, lines) for a form that is complete, or None.
    """

    def __init__(
        self,
        label: str,
        ready: Callable[[], bool],
        summary: Callable[[], str],
        start: Callable[[], Awaitable[None]],
        command: Callable[[], tuple | None] | None = None,
    ) -> None:
        self.ready = ready
        self.summary = summary
        self.command = command
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
                    self.busy_label = (
                        ui.label(BUSY)
                        .classes("sr-small")
                        .style("color: var(--sr-caution)")
                    )
                    self.link_slot = ui.element("span")
            self.block = CommandBlock()
        self._link_id: str | None = None
        self._saves = saves()
        ui.timer(0.5, self.update)
        self.update()
        self.show_command()

    def show_command(self) -> None:
        """Show the command of the form, or hide the block when it is not complete."""
        if self.command is None:
            return
        shown = self.command() if self.ready() else None
        if shown is None:
            self.block.show(None)
        else:
            self.block.show(*shown)

    def update(self) -> None:
        """Show the summary and switch the button on or off."""
        running = jobs.manager.busy()
        current = running or context_job.writer.busy()
        self.summary_label.set_text(self.summary())
        self.busy_label.set_text(BUSY if running else CONTEXT_BUSY)
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
        if saves() != self._saves:
            # The settings changed on another page (the address of Ollama).
            # A click on Start reads them again, so the command must too.
            self._saves = saves()
            self.show_command()


def about_summary(count: int) -> str:
    """Return the line of the foot: the stories and the time."""
    stories = plural(count, "story", "stories")
    return f"{stories}, about {plural(count, 'minute', 'minutes')}"
