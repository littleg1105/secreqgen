"""The page "How it works": the step diagram with a replay of one story."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from nicegui import run as nice_run
from nicegui import ui
from secreqgen.application.steps import requirements_trace, threat_trace

from secreqgen_gui import pipeline_boards as boards
from secreqgen_gui import storage
from secreqgen_gui.pages.history import format_date
from secreqgen_gui.pages.home import section
from secreqgen_gui.settings import load_settings
from secreqgen_gui.step_widget import StepDiagram
from secreqgen_gui.widgets import page_title

KINDS = (("threats", "Threat list"), ("requirements", "Requirements"))
PLAY_SECONDS = 2.8
PURPOSE = "The parts of the tool and the steps of one story."
NO_RUN = "No finished run yet. The diagram shows the steps with no numbers."
NO_RESULT = "This story has no stored result."
CSS = ".sr-link { color: #1D3A6B; }"
KEYS_HINT = (
    "Arrow right: next step. Arrow left: previous step. Space: play or pause. "
    "Esc: leave the full screen."
)


def _pick_threat_story(run_id: str, manifest: dict) -> str | None:
    """Return the id of the first story of a run that has a stored result."""
    for entry in manifest.get("per_story") or []:
        if not isinstance(entry, dict):
            continue
        story_id = entry.get("story_id")
        if isinstance(story_id, str) and storage.read_threats(run_id, story_id):
            return story_id
    return None


def _requirements_entry(run_id: str, story_id: str | None) -> dict | None:
    """Return the stored requirements of one story, or of the first story."""
    data = storage.read_requirements(run_id)
    results = [r for r in (data or {}).get("results") or [] if isinstance(r, dict)]
    for result in results:
        story = result.get("user_story")
        found = story.get("id") if isinstance(story, dict) else None
        if story_id is None or found == story_id:
            return result
    return None


def _entry_story_id(entry: dict) -> str:
    story = entry.get("user_story")
    found = story.get("id") if isinstance(story, dict) else None
    return found if isinstance(found, str) else ""


def _manifest_seconds(manifest: dict, story_id: str) -> Any:
    """Return the seconds that the run manifest holds for one story."""
    for item in manifest.get("per_story") or []:
        if isinstance(item, dict) and item.get("story_id") == story_id:
            return item.get("seconds")
    return None


def _stored(kind: str, run_id: str, story_id: str | None) -> dict | None:
    """Return the stored data of a story, or None when there is none."""
    manifest = storage.read_manifest(run_id)
    if manifest is None or manifest.get("kind") != kind:
        return None
    if kind == "threats":
        story_id = story_id or _pick_threat_story(run_id, manifest)
        model = storage.read_threats(run_id, story_id) if story_id else None
        if not model:
            return None
        return {
            "run_id": run_id,
            "story_id": story_id,
            "manifest": manifest,
            "trace": threat_trace(model),
            "verdicts": storage.read_verdicts(run_id, story_id),
        }
    entry = _requirements_entry(run_id, story_id)
    if entry is None:
        return None
    found_id = _entry_story_id(entry)
    trace = requirements_trace(entry)
    if trace.get("seconds") is None:
        trace["seconds"] = _manifest_seconds(manifest, found_id)
    return {
        "run_id": run_id,
        "story_id": found_id,
        "manifest": manifest,
        "trace": trace,
        "verdicts": {},
    }


def _load(kind: str, run_id: str | None, story_id: str | None) -> dict:
    """Read the story that the page replays. Runs in a worker thread."""
    settings = load_settings()
    if run_id and story_id:
        try:
            found = _stored(kind, run_id, story_id)
        except ValueError:
            found = None
        if found is None:
            return {"status": "missing", "settings": settings}
        found["status"] = "story"
        found["settings"] = settings
        return found
    for item in storage.list_runs():
        if item.get("kind") != kind or not item.get("id"):
            continue
        try:
            found = _stored(kind, item["id"], None)
        except ValueError:
            continue
        if found is not None:
            found["status"] = "story"
            found["settings"] = settings
            return found
    return {"status": "none", "settings": settings}


def _notes(kind: str, data: dict) -> tuple[dict[str, str], str]:
    """Return the lane notes and the address of the model server."""
    settings = data["settings"]
    manifest = data.get("manifest") if data["status"] == "story" else None
    if manifest is None:
        address = settings.base_url
        if kind == "threats":
            writer, checker = settings.writer_model, settings.checker_model
        else:
            writer, checker = settings.requirements_model, None
    else:
        address = str(manifest.get("server") or settings.base_url)
        if kind == "threats":
            writer = manifest.get("writer")
            checker = manifest.get("checker") if manifest.get("check") else None
        else:
            writer, checker = manifest.get("model"), None
    notes = boards.lane_notes(
        str(writer) if writer else None,
        str(checker) if checker else None,
        address,
    )
    return notes, address


def _seconds_text(trace: dict) -> str:
    seconds = trace.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return ""
    if seconds <= 0:
        return ""
    count = max(1, round(seconds))
    return f"{count} second" if count == 1 else f"{count} seconds"


def _frame_number(value: Any, total: int) -> int:
    """Return the index of the frame that a query value names."""
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return 0
    return number - 1 if 1 <= number <= total else 0


def _kind_links(kind: str) -> None:
    with ui.row().classes("w-full items-center gap-6 mt-4"):
        for key, label in KINDS:
            if key == kind:
                ui.label(label).classes("font-semibold")
            else:
                ui.link(label, f"/how-it-works?kind={key}").classes("sr-link")


def _source_line(kind: str, data: dict) -> None:
    with ui.row().classes("w-full items-baseline gap-x-3 gap-y-0 mt-2"):
        if data["status"] == "story":
            manifest = data["manifest"]
            text = (
                f"Replay of story {data['story_id']} from the run of "
                f"{format_date({**manifest, 'id': data['run_id']})}."
            )
            seconds = _seconds_text(data["trace"])
            if seconds:
                text += f" The real run of this story took {seconds}."
            ui.label(text).classes("sr-small")
            if kind == "threats":
                target = (
                    f"/runs/{data['run_id']}"
                    f"/stories/{quote(data['story_id'], safe='')}"
                )
            else:
                target = f"/runs/{data['run_id']}/requirements"
            ui.link("Open the result", target).classes("sr-link text-sm")
            return
        if data["status"] == "missing":
            ui.label(NO_RESULT).classes("sr-small")
        else:
            ui.label(NO_RUN).classes("sr-small")
        if kind == "threats":
            ui.link("Start a threat list", "/threats/new").classes("sr-link text-sm")
        else:
            ui.link("Start requirements", "/requirements/new").classes(
                "sr-link text-sm"
            )


def _parts(kind: str, address: str) -> None:
    with section("The parts"):
        if kind == "threats":
            lines = [
                (
                    "You",
                    "you give the story and the documentation. "
                    "You decide about each row.",
                ),
                (
                    "Tool code",
                    "fixed rules, no AI. It searches the documentation, "
                    "checks each quote and builds the page.",
                ),
                (
                    "Models",
                    "language models. They answer through the model "
                    f"server at {address}.",
                ),
            ]
        else:
            lines = [
                (
                    "You",
                    "you give the story and the documentation. "
                    "You read the requirements.",
                ),
                (
                    "Tool code",
                    "fixed rules, no AI. It searches the documentation and "
                    "puts the answer of the model into the fixed form.",
                ),
                (
                    "Model",
                    "a language model. It answers through the model "
                    f"server at {address}.",
                ),
            ]
        for name, rest in lines:
            with ui.row().classes("w-full gap-0 no-wrap items-baseline"):
                ui.label(f"{name}:").classes("font-semibold mr-1 shrink-0")
                ui.label(rest)


async def page(
    kind: str = "threats",
    run: str | None = None,
    story: str | None = None,
    frame: str | None = None,
    play: str | None = None,
) -> None:
    ui.add_css(CSS)
    if kind not in dict(KINDS):
        kind = "threats"
    data = await nice_run.io_bound(_load, kind, run, story)
    notes, address = _notes(kind, data)
    has_story = data["status"] == "story"
    if has_story:
        trace = data["trace"]
        frames = boards.replay_frames(kind, trace)
        story_id = data["story_id"]
        verdicts = data["verdicts"]
    else:
        frames = boards.plain_frames(kind)
        story_id = ""

    def board_of(position: int):
        if not has_story:
            return boards.plain_board(kind, frames[position], notes=notes)
        return boards.replay_board(
            kind,
            trace,
            frames[position],
            verdicts=verdicts if position == len(frames) - 1 else None,
            notes=notes,
        )

    total = len(frames)
    state = {"index": _frame_number(frame, total), "playing": False}

    with ui.column().classes("w-full gap-0"):
        page_title("How it works", PURPOSE)
        _kind_links(kind)
        _source_line(kind, data)
        # The buttons stand above the diagram. So they stay in their place
        # when the text below the diagram gets longer or shorter.
        with ui.row().classes("w-full items-center gap-3 mt-4"):
            previous = ui.button("Previous step").props("outline no-caps")
            play_button = ui.button("Play").props("unelevated no-caps")
            following = ui.button("Next step").props("outline no-caps")
            counter = ui.label().classes("sr-small ml-2")
        with ui.column().classes("w-full gap-0 mt-2"):
            diagram = StepDiagram(hint=KEYS_HINT)
        _parts(kind, address)

    def draw(position: int, moving: bool) -> None:
        came_from = frames[position - 1][0] if position > 0 else None
        state["index"] = position
        diagram.show(
            board_of(position),
            story_id=story_id,
            came_from=came_from,
            moving=moving,
        )
        counter.set_text(f"Step {position + 1} of {total}")

    timer = ui.timer(PLAY_SECONDS, lambda: tick(), active=False)

    def set_playing(value: bool) -> None:
        state["playing"] = value
        timer.active = value
        play_button.set_text("Pause" if value else "Play")

    def tick() -> None:
        if state["index"] >= total - 1:
            set_playing(False)
            return
        draw(state["index"] + 1, True)
        if state["index"] >= total - 1:
            set_playing(False)

    def go_next() -> None:
        set_playing(False)
        if state["index"] < total - 1:
            draw(state["index"] + 1, True)

    def go_previous() -> None:
        set_playing(False)
        if state["index"] > 0:
            draw(state["index"] - 1, False)

    def toggle_play() -> None:
        if state["playing"]:
            set_playing(False)
            return
        if state["index"] >= total - 1:
            draw(0, False)
        set_playing(True)

    previous.on_click(go_previous)
    following.on_click(go_next)
    play_button.on_click(toggle_play)

    def on_key(event) -> None:
        if not event.action.keydown or event.action.repeat:
            return
        mod = event.modifiers
        if mod.ctrl or mod.meta or mod.alt:
            return
        if event.key.arrow_right:
            go_next()
        elif event.key.arrow_left:
            go_previous()
        elif event.key.space:
            toggle_play()

    ui.keyboard(on_key=on_key)
    draw(state["index"], False)
    if str(play) == "1":
        if state["index"] >= total - 1:
            draw(0, False)
        set_playing(True)
