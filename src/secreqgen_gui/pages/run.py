"""The progress of a run and, after it, its stories.

A run in work comes from the job manager. A run that ended long ago comes
from its manifest on disk. The page polls the manager and redraws only when
the version number changed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from nicegui import run, ui
from secreqgen.application.steps import requirements_trace, threat_trace

from secreqgen_gui import jobs, pipeline_boards, storage
from secreqgen_gui.step_widget import StepDiagram
from secreqgen_gui.widgets import empty_state, page_title, tag

POLL_SECONDS = 0.5
ACTIVE = ("checking", "running")
TAG_KIND = {"done": "keep", "failed": "delete"}
FAILED_HINT = "Check the model server on the start page, then start the run again."
ID_WIDTH = "width:112px;flex:none"
READABLE = "max-width:960px"
CHECKING_LINE = "The run checks the model server."


@dataclass
class View:
    """What the page shows about one run."""

    id: str
    kind: str
    state: str
    message: str = ""
    stories: list = field(default_factory=list)
    started: float | None = None
    finished: float | None = None
    stop_requested: bool = False
    version: int = 0
    live: bool = False
    product: str = ""
    writer: str = ""
    checker: str = ""
    model: str = ""
    prompt_name: str = ""
    tested: bool = True
    has_results: bool = False
    server: str = ""
    labels: bool = True
    progress: dict | None = None


def _clock(seconds: float | None) -> str:
    """Return seconds as m:ss."""
    if seconds is None:
        return ""
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _parse_time(value: object) -> float | None:
    try:
        stamp = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC).timestamp()


def _meta(view: View, manifest: dict | None) -> None:
    """Copy the product, the models and the prompt set from a manifest."""
    if not manifest:
        return
    view.product = str(manifest.get("product") or "")
    view.writer = str(manifest.get("writer") or "")
    view.checker = str(manifest.get("checker") or "")
    view.model = str(manifest.get("model") or "")
    view.server = str(manifest.get("server") or "")
    view.labels = bool(manifest.get("labels", True))
    prompt = manifest.get("prompt_set")
    if isinstance(prompt, dict):
        view.tested = bool(prompt.get("tested", True))
        view.prompt_name = str(prompt.get("name") or "")


def _titles(run_id: str, manifest: dict) -> dict[str, str]:
    """Read the story titles from the copy of the story file, if it is JSON."""
    name = Path(str(manifest.get("stories_file") or "")).name
    if not name.endswith(".json"):
        return {}
    try:
        data = json.loads((storage.run_dir(run_id) / name).read_text("utf-8"))
        items = data.get("user_stories", [])
        return {str(i["id"]): str(i.get("title") or "") for i in items}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _summary(kind: str, entry: dict, checked: bool) -> str:
    if kind == "requirements":
        count = int(entry.get("requirements") or 0)
        return f"{count} requirement" + ("" if count == 1 else "s")
    rows = int(entry.get("rows") or 0)
    parts = [f"{rows} row" + ("" if rows == 1 else "s")]
    if checked:
        parts.append(f"{int(entry.get('accepted') or 0)} accepted")
        if entry.get("dropped"):
            parts.append(f"{int(entry['dropped'])} with a fault")
    return ", ".join(parts)


def _stories_from_manifest(run_id: str, manifest: dict) -> list:
    kind = str(manifest.get("kind") or "threats")
    titles = _titles(run_id, manifest)
    checked = bool(manifest.get("check", True)) and bool(manifest.get("checker"))
    done = {str(e.get("story_id")): e for e in manifest.get("per_story") or []}
    errors = {
        str(f.get("story_id")): str(f.get("message") or "")
        for f in manifest.get("failures") or []
        if isinstance(f, dict)
    }
    not_run = [str(i) for i in manifest.get("not_run") or []]
    order = [str(i) for i in manifest.get("story_ids") or []]
    for story_id in [*done, *not_run]:
        if story_id not in order:
            order.append(story_id)
    stories = []
    for story_id in order:
        entry = done.get(story_id)
        item = jobs.StoryState(story_id, titles.get(story_id, ""), "not run")
        if entry is not None:
            failed = entry.get("status") == "failed"
            item.state = "failed" if failed else "done"
            item.seconds = entry.get("seconds")
            item.error = errors.get(story_id, "") if failed else ""
            item.summary = "" if failed else _summary(kind, entry, checked)
        stories.append(item)
    return stories


def _from_manifest(run_id: str, manifest: dict) -> View:
    status = str(manifest.get("status") or "")
    state = {
        "finished": "done",
        "stopped": "stopped",
        "aborted": "failed",
        "failed": "failed",
    }.get(status, "failed")
    error = manifest.get("error")
    message = ""
    if isinstance(error, dict):
        message = str(error.get("message") or "")
    if status in ("interrupted", "running"):
        message = "The program ended while the run was in work."
    view = View(
        id=run_id,
        kind=str(manifest.get("kind") or "threats"),
        state=state,
        message=message,
        stories=_stories_from_manifest(run_id, manifest),
        started=_parse_time(manifest.get("created_at")),
        finished=_parse_time(manifest.get("finished_at")),
    )
    _meta(view, manifest)
    view.has_results = any(s.state == "done" for s in view.stories)
    return view


def _from_state(state: jobs.RunState, manifest: dict | None) -> View:
    view = View(
        id=state.id,
        kind=state.kind,
        state=state.state,
        message=state.message,
        stories=list(state.stories),
        started=state.started,
        finished=state.finished,
        stop_requested=state.stop_requested,
        version=state.version,
        live=state.state in ACTIVE,
        progress=getattr(state, "progress", None),
    )
    _meta(view, manifest)
    view.has_results = any(s.state == "done" for s in view.stories)
    return view


def _load(run_id: str) -> View | None:
    """Return the view of a run, or None when the run does not exist."""
    try:
        manifest = storage.read_manifest(run_id)
    except ValueError:
        return None
    state = jobs.manager.get(run_id)
    if state is not None:
        return _from_state(state, manifest)
    if manifest is None:
        return None
    return _from_manifest(run_id, manifest)


def _position(view: View) -> int:
    """Return the number of the story that runs now (1 based)."""
    for number, story in enumerate(view.stories, start=1):
        if story.state == "running":
            return number
    finished = sum(1 for s in view.stories if s.state in ("done", "failed"))
    return min(finished + 1, max(len(view.stories), 1))


def _state_words(view: View) -> str:
    total = len(view.stories)
    finished = sum(1 for s in view.stories if s.state in ("done", "failed"))
    if view.state == "checking":
        return "Checking the model server"
    if view.state == "running":
        return f"Running: story {_position(view)} of {total}"
    if view.state == "done":
        return "Done"
    if view.state == "stopped":
        return f"Stopped after {finished} of {total} stories"
    return f"Failed: {view.message}" if view.message else "Failed"


def _needs_hint(view: View) -> bool:
    low = view.message.lower()
    return not any(w in low for w in ("check ", "start ollama", "ollama pull"))


def _next_story_id(view: View) -> str:
    for wanted in ("running", "waiting"):
        for story in view.stories:
            if story.state == wanted:
                return story.id
    return ""


def _plain(value: str, classes: str = ""):
    """Show untrusted text as text and return the element for styling."""
    return ui.label(str(value)).classes(
        f"whitespace-pre-wrap break-words {classes}".strip()
    )


def _head(view: View) -> None:
    title = "Threat list run" if view.kind == "threats" else "Requirements run"
    page_title(title)
    if view.product:
        ui.label(f"Product: {view.product}").classes("sr-purpose")
    if view.kind == "threats" and view.writer:
        second = f"{view.checker} checks" if view.checker else "no second model"
        ui.label(f"{view.writer} writes, {second}").classes("sr-purpose")
    elif view.kind == "requirements" and view.model:
        ui.label(f"Model: {view.model}").classes("sr-purpose")
    if not view.tested and view.prompt_name:
        ui.label(f"Not the tested setup: prompt set {view.prompt_name}").style(
            "color:var(--sr-caution)"
        )


def _replay_address(view: View, story_id: str) -> str:
    return (
        f"/how-it-works?kind={view.kind}&run={quote(view.id, safe='')}"
        f"&story={quote(story_id, safe='')}"
    )


def _story_title(view: View, story_id: str) -> str:
    for story in view.stories:
        if story.id == story_id:
            return story.title
    return ""


def _notes(view: View) -> dict[str, str]:
    if view.kind == "threats":
        return pipeline_boards.lane_notes(
            view.writer or None, view.checker or None, view.server or None
        )
    return pipeline_boards.lane_notes(view.model or None, None, view.server or None)


def _stored_story(view: View) -> dict | None:
    """Read the last story of the run that has a stored result.

    It reads files, so a page calls it off the event loop.
    """
    if view.kind == "threats":
        for story in reversed(view.stories):
            if story.state != "done":
                continue
            model = storage.read_threats(view.id, story.id)
            if model is None:
                continue
            return {
                "id": story.id,
                "title": story.title,
                "trace": threat_trace(model),
                "verdicts": storage.read_verdicts(view.id, story.id),
            }
        return None
    data = storage.read_requirements(view.id)
    results = data.get("results") if isinstance(data, dict) else None
    for entry in reversed(results if isinstance(results, list) else []):
        if not isinstance(entry, dict):
            continue
        trace = requirements_trace(entry)
        if trace["story_id"]:
            story = entry.get("user_story") or {}
            title = str(story.get("title") or _story_title(view, trace["story_id"]))
            return {
                "id": trace["story_id"],
                "title": title,
                "trace": trace,
                "verdicts": {},
            }
    return None


def _steps_view(view: View, stored: dict | None) -> tuple | None:
    """Return (board, story id, line, link, moving) or None for no section."""
    notes = _notes(view)
    if view.live:
        progress = view.progress
        if not progress or not progress.get("story_id"):
            board = pipeline_boards.live_board(
                pipeline_boards.new_progress(view.kind), notes=notes
            )
            return board, "", CHECKING_LINE, None, True
        story_id = str(progress["story_id"])
        ids = [s.id for s in view.stories]
        number = ids.index(story_id) + 1 if story_id in ids else _position(view)
        line = (
            f"Story {number} of {len(view.stories)}: {story_id} "
            f"{_story_title(view, story_id)}"
        ).strip()
        board = pipeline_boards.live_board(
            progress, labels=view.labels, checker=bool(view.checker), notes=notes
        )
        return board, story_id, line, None, True
    if stored is not None:
        board = pipeline_boards.result_board(
            view.kind, stored["trace"], verdicts=stored["verdicts"], notes=notes
        )
        line = f"Story {stored['id']} {stored['title']}".strip()
        return board, stored["id"], line, _replay_address(view, stored["id"]), False
    progress = view.progress
    if progress and progress.get("story_id"):
        story_id = str(progress["story_id"])
        board = pipeline_boards.live_board(
            progress, labels=view.labels, checker=bool(view.checker), notes=notes
        )
        line = f"Story {story_id} {_story_title(view, story_id)}".strip()
        return board, story_id, line, None, False
    return None


def _story_row(view: View, story) -> None:
    with ui.column().classes("w-full gap-1 py-3").style(
        "border-bottom:1px solid var(--sr-line)"
    ):
        with ui.row().classes("w-full items-baseline no-wrap gap-4"):
            _plain(story.id, "font-semibold").style(ID_WIDTH)
            _plain(story.title, "grow min-w-0")
            with ui.element("div").style("width:76px;flex:none"):
                tag(story.state, TAG_KIND.get(story.state, "plain"))
            ui.label(_clock(story.seconds)).classes("sr-small text-right").style(
                "width:44px;flex:none"
            )
            _plain(story.summary, "sr-small").style("width:210px;flex:none")
            with ui.element("div").style("width:40px;flex:none"):
                if view.kind == "threats" and story.state == "done":
                    target = f"/runs/{view.id}/stories/{quote(story.id, safe='')}"
                    ui.link("Read", target).style("color:var(--sr-blue)")
            if story.state == "done":
                ui.link("Steps", _replay_address(view, story.id)).classes(
                    "sr-small"
                ).style("width:40px;flex:none;color:var(--sr-blue)")
        if story.state == "failed" and story.error:
            _plain(story.error, "text-sm").style(
                "color:var(--sr-delete);margin-left:128px"
            )


def page(run_id: str) -> None:
    """Show one run. The page builds itself after a read off the event loop."""
    holder: dict = {
        "view": None,
        "stop_clicked": False,
        "elapsed": None,
        "stored": None,
        "line": None,
    }

    @ui.refreshable
    def head_view() -> None:
        view = holder["view"]
        _head(view)
        with ui.row().classes("items-baseline gap-3 pt-4"):
            color = {"done": "keep", "failed": "delete"}.get(view.state, "ink")
            if view.state == "failed":
                _plain(_state_words(view), "sr-section-title").style(
                    f"color:var(--sr-{color})"
                )
            else:
                ui.label(_state_words(view)).classes("sr-section-title")
            holder["elapsed"] = ui.label(_elapsed(view)).classes("sr-small")
        if view.state == "failed" and _needs_hint(view):
            ui.label(FAILED_HINT).classes("sr-small")
        total = len(view.stories)
        finished = sum(1 for s in view.stories if s.state in ("done", "failed"))
        ui.linear_progress(
            value=finished / total if total else 0, show_value=False, size="4px"
        ).props("track-color=grey-3").classes("mt-3")
        with ui.row().classes("items-center gap-4 pt-4"):
            _actions(view)

    def _actions(view: View) -> None:
        if view.live:
            if holder["stop_clicked"] or view.stop_requested:
                ui.label(
                    f"The run stops when story {_next_story_id(view)} ends."
                    if _next_story_id(view)
                    else "The run stops after the check."
                )
            else:
                ui.button("Stop after this story", on_click=stop).props(
                    "outline no-caps unelevated color=primary"
                )
            return
        if view.kind == "requirements" and view.has_results:
            ui.button(
                "Read the requirements",
                on_click=lambda: ui.navigate.to(f"/runs/{view.id}/requirements"),
            ).props("unelevated no-caps color=primary")
        target = "/threats/new" if view.kind == "threats" else "/requirements/new"
        ui.link("Start a new run", target).style("color:var(--sr-blue)")

    @ui.refreshable
    def stories_view() -> None:
        view = holder["view"]
        with ui.column().classes("w-full gap-0 mt-6").style(
            f"border-top:1px solid var(--sr-line);{READABLE}"
        ):
            for story in view.stories:
                _story_row(view, story)

    def stop() -> None:
        holder["stop_clicked"] = True
        jobs.manager.request_stop()
        head_view.refresh()

    def _elapsed(view: View) -> str:
        if view.started is None:
            return ""
        end = view.finished if view.finished is not None else time.time()
        return _clock(end - view.started)

    def show_steps(view: View) -> None:
        """Draw the steps section. The diagram draws again only on a change."""
        shown = _steps_view(view, holder["stored"])
        holder["section"].set_visibility(shown is not None)
        if shown is None:
            return
        board, story_id, line, link, moving = shown
        holder["diagram"].show(board, story_id=story_id, moving=moving)
        if holder["line"] != (line, link):
            holder["line"] = (line, link)
            holder["line_row"].clear()
            with holder["line_row"]:
                _plain(line, "sr-small")
                if link:
                    ui.link("Replay the steps", link).classes("sr-small").style(
                        "color:var(--sr-blue)"
                    )

    async def tick() -> None:
        view = holder["view"]
        if view is None:
            return
        if not view.live:
            timer.deactivate()
            return
        state = jobs.manager.get(run_id)
        if state is None:
            return
        if state.version != view.version:
            manifest = await run.io_bound(storage.read_manifest, run_id)
            new = _from_state(state, manifest)
            holder["view"] = new
            head_view.refresh()
            stories_view.refresh()
            if new.live:
                show_steps(new)
            else:
                timer.deactivate()
                holder["stored"] = await run.io_bound(_stored_story, new)
                show_steps(new)  # the "run ended" form, one time
            return
        label = holder["elapsed"]
        if label is not None and not label.is_deleted:
            label.set_text(_elapsed(view))

    timer = ui.timer(POLL_SECONDS, tick, active=False)
    body = ui.column().classes("w-full gap-0")

    async def build() -> None:
        view = await run.io_bound(_load, run_id)
        body.clear()
        with body:
            if view is None:
                empty_state(
                    "This run does not exist.", "Open the history", "/history"
                )
                return
            holder["view"] = view
            holder["stored"] = (
                None if view.live else await run.io_bound(_stored_story, view)
            )
            with ui.column().classes("w-full gap-0").style(READABLE):
                head_view()
            with ui.column().classes("w-full gap-2 mt-6") as section:
                ui.label("Steps").classes("sr-section-title")
                holder["line_row"] = ui.row().classes("items-baseline gap-4")
                holder["diagram"] = StepDiagram()
            holder["section"] = section
            show_steps(view)
            stories_view()
        if view.live:
            timer.activate()

    ui.timer(0.01, build, once=True)
