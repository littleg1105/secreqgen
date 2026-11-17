"""The requirements of a run."""

from __future__ import annotations

import json

from nicegui import run, ui
from secreqgen.application.documentation import shown_name

from secreqgen_gui import storage
from secreqgen_gui.widgets import (
    context_tag,
    empty_state,
    formats_tag,
    page_title,
    tag,
    text,
)

LINE = "border-top: 1px solid var(--sr-line)"


def _plural(count: int, word: str, many: str = "") -> str:
    if count == 1:
        return f"{count} {word}"
    return f"{count} {many or word + 's'}"


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _story_of(result: dict) -> dict:
    return _as_dict(result.get("user_story"))


def _failure_text(failure: object) -> tuple[str, str]:
    """Return the story id and the error text of one failure."""
    item = _as_dict(failure)
    error = item.get("error", item)
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("type") or "")
    else:
        message = str(error)
    return str(item.get("story_id", "")), message


def count_requirements(data: dict) -> int:
    """Count all requirements of all stories."""
    return sum(
        len(_as_list(_as_dict(r).get("requirements")))
        for r in _as_list(data.get("results"))
    )


def build_markdown(data: dict, product: str = "") -> str:
    """Build a Markdown text from the data: a heading for each story."""
    lines = ["# Requirements", ""]
    if product:
        lines += [f"Product: {product}", ""]
    for result in _as_list(data.get("results")):
        result = _as_dict(result)
        story = _story_of(result)
        lines += [f"## {story.get('id', '')} {story.get('title', '')}".rstrip(), ""]
        if story.get("story"):
            lines += [str(story["story"]), ""]
        for req in _as_list(result.get("requirements")):
            req = _as_dict(req)
            lines += [f"### {req.get('id', '')} {req.get('title', '')}".rstrip(), ""]
            lines.append(f"- Priority: {req.get('priority', '')}")
            lines.append(f"- ASVS category: {req.get('asvs_category', '')}")
            lines += ["", str(req.get("description", "")), ""]
            if req.get("rationale"):
                lines += [f"Why: {req['rationale']}", ""]
            for title, key in (
                ("Acceptance criteria", "acceptance_criteria"),
                ("Sources", "source_references"),
            ):
                items = _as_list(req.get(key))
                if items:
                    lines += [f"{title}:", ""] + [f"- {i}" for i in items] + [""]
            if req.get("implementation_notes"):
                lines += ["Implementation notes:", "", str(req["implementation_notes"])]
                lines.append("")
    failures = _as_list(data.get("failures"))
    if failures:
        lines += ["## Stories with no result", ""]
        for failure in failures:
            story_id, message = _failure_text(failure)
            lines.append(f"- {story_id}: {message}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _requirement(req: dict) -> None:
    with ui.column().classes("w-full gap-2 py-4").style(LINE):
        text(str(req.get("title", "")), "font-semibold")
        with ui.row().classes("gap-2 items-center"):
            tag(f"Priority: {req.get('priority', '')}")
            if req.get("asvs_category"):
                tag(str(req["asvs_category"]))
        if req.get("description"):
            text(str(req["description"]))
        if req.get("rationale"):
            with ui.row().classes("gap-1 no-wrap items-baseline"):
                ui.label("Why:").classes("sr-small")
                text(str(req["rationale"]), "sr-small")
        for title, key in (
            ("Acceptance criteria", "acceptance_criteria"),
            ("Sources", "source_references"),
        ):
            items = _as_list(req.get(key))
            if items:
                ui.label(title).classes("font-semibold")
                with ui.column().classes("gap-1 pl-4"):
                    for item in items:
                        with ui.row().classes("gap-2 no-wrap items-baseline"):
                            ui.label("-")
                            text(str(item))
        if req.get("implementation_notes"):
            ui.label("Implementation notes").classes("font-semibold")
            text(str(req["implementation_notes"]))


def _shown_header(header: str) -> str:
    """Show the real file name in the header "file > section"."""
    name, divider, rest = header.partition(" > ")
    return shown_name(name) + divider + rest


def _passages(result: dict) -> None:
    passages = [_as_dict(p) for p in _as_list(result.get("passages"))]
    if not passages:
        return
    label = f"Passages that the model read ({len(passages)})"
    with ui.expansion(label).classes("w-full").style(LINE):
        for passage in passages:
            with ui.column().classes("w-full gap-1 py-2"):
                ui.label(_shown_header(str(passage.get("header", "")))).classes(
                    "sr-small break-words"
                )
                text(str(passage.get("text", "")), "sr-source")


def _story(result: dict, opened: bool) -> None:
    story = _story_of(result)
    reqs = [_as_dict(r) for r in _as_list(result.get("requirements"))]
    head = f"{story.get('id', '')}  {story.get('title', '')}".strip()
    caption = _plural(len(reqs), "requirement")
    with ui.expansion(head, caption=caption, value=opened).classes("w-full").props(
        'header-class="font-semibold"'
    ).style(LINE):
        with ui.column().classes("w-full gap-0 pb-4"):
            if story.get("story"):
                text(str(story["story"]), "sr-source pb-2")
            for req in reqs:
                _requirement(req)
            _passages(result)


def _failures(data: dict) -> None:
    failures = _as_list(data.get("failures"))
    if not failures:
        return
    ui.label("Stories with no result").classes("sr-section-title mt-6")
    for failure in failures:
        story_id, message = _failure_text(failure)
        with ui.column().classes("w-full gap-1 py-3").style(LINE):
            text(story_id, "font-semibold")
            text(message, "text-negative")


def _not_run(data: dict) -> int:
    return len(_as_list(data.get("not_run")))


async def page(run_id: str) -> None:
    try:
        manifest = await run.io_bound(storage.read_manifest, run_id)
        data = await run.io_bound(storage.read_requirements, run_id)
    except ValueError:
        manifest, data = None, None
        page_title("Requirements")
        empty_state("This run does not exist.", "Open History", "/history")
        return
    if manifest is None and data is None:
        page_title("Requirements")
        empty_state("This run does not exist.", "Open History", "/history")
        return
    if data is None:
        page_title("Requirements")
        empty_state(
            "This run has no requirements.", "Open the run", f"/runs/{run_id}"
        )
        return
    manifest = manifest or {}
    results = [_as_dict(r) for r in _as_list(data.get("results"))]
    total = count_requirements(data)
    product = str(manifest.get("product") or "")
    page_title(
        "Requirements",
        f"{_plural(total, 'requirement')} for "
        f"{_plural(len(results), 'story', 'stories')}.",
    )
    model = data.get("model") or manifest.get("model") or ""
    prompt = _as_dict(data.get("prompt_set")) or _as_dict(manifest.get("prompt_set"))
    tested = data.get("tested_prompts", prompt.get("tested", True))
    with ui.row().classes("w-full items-center gap-4 mt-3"):
        if product:
            text(f"Product: {product}", "sr-small")
        if model:
            text(f"Model: {model}", "sr-small")
        if not tested:
            tag("untested prompts", "caution")
        formats_tag(manifest)
        context_tag(manifest)
    with ui.row().classes("gap-2 mt-3 mb-4"):
        ui.button(
            "Download JSON",
            on_click=lambda: ui.download.content(
                (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode(),
                "requirements.json",
                "application/json",
            ),
        ).props("outline no-caps unelevated color=primary")
        ui.button(
            "Download Markdown",
            on_click=lambda: ui.download.content(
                build_markdown(data, product).encode(),
                "requirements.md",
                "text/markdown",
            ),
        ).props("outline no-caps unelevated color=primary")
    if data.get("stopped") or _not_run(data):
        left = _plural(_not_run(data), "story", "stories")
        verb = "was" if _not_run(data) == 1 else "were"
        ui.label(f"The run stopped. {left} {verb} not run.").classes("text-warning")
    last = "border-bottom: 1px solid var(--sr-line)"
    with ui.column().classes("w-full gap-0").style(last):
        for number, result in enumerate(results):
            _story(result, opened=number == 0)
    _failures(data)
