"""The threat list of one story with the evidence panel.

The list is on the left. The evidence of the chosen row is on the right and
stays in view. The text of a threat comes from a model or from the
documentation. It is shown with ui.label or the shared widgets, never as HTML.
"""

from __future__ import annotations

from urllib.parse import quote as url_quote

from nicegui import run, ui
from secreqgen.application.documentation import shown_name
from secreqgen.application.steps import threat_trace
from secreqgen.application.threat_view import (
    export_kept,
    group_threat_rows,
    locate_quote,
    split_passage,
    threat_labels,
)

from secreqgen_gui import storage
from secreqgen_gui.widgets import (
    empty_state,
    formats_tag,
    marked_passage,
    page_title,
    tag,
    text,
)

STYLE = """
.sr-main:has(.sr-result) { max-width: none; margin: 0; padding: 0; }
.sr-result { align-items: flex-start; flex-wrap: nowrap; width: 100%; }
.sr-list-col {
  flex: 1 1 0;
  min-width: 0;
  padding: 28px 32px 64px 32px;
}
.sr-evidence {
  flex: 0 0 440px;
  width: 440px;
  position: sticky;
  top: 0;
  align-self: flex-start;
  height: 100vh;
  overflow-y: auto;
  background: var(--sr-mist);
  border-left: 3px solid var(--sr-blue);
  padding: 24px 24px 32px 24px;
}
.sr-notice {
  color: var(--sr-slate);
  border-left: 3px solid var(--sr-line);
  padding: 2px 0 2px 12px;
  max-width: 76ch;
}
.sr-warning { color: var(--sr-caution); border-left-color: var(--sr-caution); }
.sr-count { font-size: var(--sr-size-small); color: var(--sr-slate); }
.sr-group-title {
  font-size: var(--sr-size-section);
  font-weight: 600;
  margin-top: 28px;
}
.sr-group-note { color: var(--sr-slate); max-width: 76ch; margin-bottom: 4px; }
.sr-group-box { border-top: 1px solid var(--sr-line); margin-top: 8px; }
.sr-row {
  display: flex;
  flex-wrap: nowrap;
  gap: 12px;
  padding: 14px 12px 14px 12px;
  border-bottom: 1px solid var(--sr-line);
  border-left: 3px solid transparent;
  cursor: pointer;
  width: 100%;
}
.sr-row:hover { background: #FAFBFC; }
.sr-row-chosen, .sr-row-chosen:hover {
  background: var(--sr-mist);
  border-left-color: var(--sr-blue);
}
.sr-row-no {
  flex: 0 0 24px;
  font-size: var(--sr-size-small);
  color: var(--sr-slate);
  padding-top: 2px;
}
.sr-row-body { flex: 1 1 0; min-width: 0; }
.sr-threat { font-size: var(--sr-size-text); font-weight: 500; line-height: 1.45; }
.sr-quote-box {
  font-family: var(--sr-font-source);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-width: 72ch;
}
.sr-quote-box > * {
  display: inline;
  background: var(--sr-marker);
  border-radius: 2px;
  padding: 0 2px;
  -webkit-box-decoration-break: clone;
  box-decoration-break: clone;
}
.sr-row-actions { flex: 0 0 auto; }
.sr-ev-label { font-size: var(--sr-size-small); color: var(--sr-slate); }
.sr-ev-passage {
  max-height: 40vh;
  overflow-y: auto;
  position: relative;
  background: var(--sr-paper);
  border: 1px solid var(--sr-line);
  border-radius: var(--sr-radius);
  padding: 12px 14px;
  width: 100%;
}
.sr-ev-passage .sr-source { max-width: none; }
.sr-sign {
  color: var(--sr-caution);
  border-left: 2px solid var(--sr-caution);
  padding-left: 10px;
  font-size: var(--sr-size-small);
}
"""

VERDICT_COLOR = {"keep": "positive", "delete": "negative"}
CHECKER_TEXT = {"accepts": "accepted", "refuses": "flagged"}
NOTICE = (
    "This is a draft, not a complete threat model. It covers only what your "
    "documentation says. Check each threat against its quote."
)
KIND_SHORT = {"attack": "Attack", "rule": "Rule breach"}
KIND_HINT = {
    "attack": "The documentation describes an attack.",
    "rule": "Someone breaks a security rule of the documentation.",
}
CHOICE_HINT = "Chosen by the writer, not taken from the documentation."
GROUP_TITLE = {
    "accepts": "Accepted by the checker",
    "refuses": "Flagged by the checker",
    "no_verdict": "No answer from the checker",
    "no_weak_sign": "Threats",
    "weak_sign": "Weak support",
}
GROUP_NOTE = {
    "refuses": (
        "For example, the quote is about another feature, or the threat says "
        "more than the passage. In the tests, flagged threats were wrong more "
        "often than accepted ones."
    ),
    "weak_sign": (
        "SecReqGen found a weak point in the support, for example a very short "
        "quote. In the tests, these threats were wrong more often than the others."
    ),
}


def _load(run_id: str, story_id: str) -> dict:
    """Read the files of the story. Return the data or the reason it is empty."""
    try:
        manifest = storage.read_manifest(run_id)
    except ValueError:
        return {"error": "run"}
    if manifest is None:
        return {"error": "run"}
    try:
        model = storage.read_threats(run_id, story_id)
    except ValueError:
        model = None
    required = ("rows", "calls", "passages", "dropped", "story", "seconds")
    if model is None or any(key not in model for key in required):
        return {"error": "story"}
    story_ids = [
        e.get("story_id")
        for e in manifest.get("per_story") or []
        if isinstance(e, dict) and isinstance(e.get("story_id"), str)
    ]
    try:
        grouped = group_threat_rows(model)
    except (KeyError, TypeError, ValueError):
        return {"error": "story"}
    return {
        "model": model,
        "manifest": manifest,
        "story_ids": story_ids,
        "grouped": grouped,
        "verdicts": storage.read_verdicts(run_id, story_id),
    }


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _steps_line(model: dict) -> str:
    """Return the line with the numbers of the steps of the story."""
    trace = threat_trace(model)
    removed = trace["check"]["removed"]
    written = trace["check"]["kept"] + removed
    line = (
        f"From {_plural(trace['search']['passages'], 'passage')} the writer "
        f"wrote {_plural(written, 'threat')}."
    )
    if removed == 1:
        return line + " 1 failed the quote check and is not in the list."
    if removed:
        return (
            f"{line} {removed} failed the quote check and are not in the list."
        )
    return line + " All passed the quote check." if written else line


def _count_lines(rows: list[dict], has_checker: bool) -> str:
    """Return the first line of counts: threats and the answers of the checker."""
    line = _plural(len(rows), "threat")
    if has_checker:
        accepted = sum(1 for r in rows if r.get("second_model") == "accepts")
        flagged = sum(1 for r in rows if r.get("second_model") == "refuses")
        silent = len(rows) - accepted - flagged
        line += f": {accepted} accepted, {flagged} flagged"
        if silent:
            line += f", {silent} with no answer"
        return line
    weak = sum(1 for r in rows if r.get("weak_signs"))
    return f"{line}: {weak} with weak support" if weak else line


def _decision_line(total: int, kept: int, deleted: int) -> str:
    """Return the second line of counts: what the reader decided."""
    if not kept and not deleted:
        return "Keep or delete each threat."
    left = total - kept - deleted
    line = f"You kept {kept} and deleted {deleted}."
    return f"{line} {left} left." if left else line


def _short(label: str) -> str:
    """Return the first words of a label, without a letter or an explanation."""
    head = label.split(" (")[0].strip()
    if len(head) > 3 and head[1:3] == ", ":
        head = head[3:]
    return head[:1].upper() + head[1:]


def _not_tested(model: dict) -> bool:
    if model.get("tested_prompts", True) is False:
        return True
    return str(model.get("prompt_set") or "").startswith("user:")


async def page(run_id: str, story_id: str, row: str | None = None) -> None:
    ui.add_css(STYLE)
    data = await run.io_bound(_load, run_id, story_id)
    if data.get("error") == "run":
        empty_state("This run does not exist.", "Open History", "/history")
        return
    if data.get("error"):
        empty_state(
            "This story is not part of the run.",
            "Open the run",
            f"/runs/{run_id}",
        )
        return

    model = data["model"]
    grouped = data["grouped"]
    labels = threat_labels()
    story = model["story"] if isinstance(model["story"], dict) else {}
    passages = {
        p.get("ref"): split_passage(p) for p in model["passages"] if isinstance(p, dict)
    }
    for part in passages.values():
        part["page"] = shown_name(part["page"])
    flat = [item for group in grouped["groups"] for item in group["rows"]]
    order = [item["row"]["id"] for item in flat]
    by_id = {item["row"]["id"]: item for item in flat}
    state = {
        "chosen": row if row in by_id else None,
        "verdicts": dict(data["verdicts"]),
    }
    story_ids = data["story_ids"]
    position = story_ids.index(story_id) if story_id in story_ids else -1
    previous_id = story_ids[position - 1] if position > 0 else None
    following_id = (
        story_ids[position + 1] if 0 <= position < len(story_ids) - 1 else None
    )
    story_path = f"/runs/{run_id}/stories/"

    def counts() -> tuple[str, str]:
        rows = [item["row"] for item in flat]
        about = _count_lines(rows, bool(model.get("checker")))
        verdicts = [state["verdicts"].get(r["id"]) for r in rows]
        kept = verdicts.count("keep")
        read = _decision_line(len(rows), kept, verdicts.count("delete"))
        return about, read

    def set_verdict(row_id: str, verdict: str) -> None:
        new = None if state["verdicts"].get(row_id) == verdict else verdict
        try:
            storage.write_verdict(run_id, story_id, row_id, new)
        except (ValueError, OSError):
            ui.notify(
                "Could not save your choice. Reload the page and try again.",
                type="negative",
            )
            return
        if new is None:
            state["verdicts"].pop(row_id, None)
        else:
            state["verdicts"][row_id] = new
        refresh_all()

    def choose(row_id: str | None, scroll: bool = False) -> None:
        state["chosen"] = row_id
        refresh_all()
        if scroll and row_id in order:
            number = by_id[row_id]["number"]
            ui.run_javascript(
                f"document.getElementById('sr-row-{number}')"
                "?.scrollIntoView({block: 'nearest'})"
            )

    def move(step: int) -> None:
        if not order:
            return
        if state["chosen"] not in order:
            choose(order[0] if step > 0 else order[-1], scroll=True)
            return
        index = order.index(state["chosen"]) + step
        if 0 <= index < len(order):
            choose(order[index], scroll=True)

    def on_key(event) -> None:
        if not event.action.keydown:
            return
        if event.action.repeat and not event.key.is_cursorkey:
            return
        mod = event.modifiers
        if mod.ctrl or mod.meta or mod.alt:
            return
        if event.key.arrow_down:
            move(1)
        elif event.key.arrow_up:
            move(-1)
        elif state["chosen"] in order and event.key.name in ("k", "K"):
            set_verdict(state["chosen"], "keep")
        elif state["chosen"] in order and event.key.name in ("d", "D"):
            set_verdict(state["chosen"], "delete")

    def refresh_all() -> None:
        counts_line.refresh()
        list_view.refresh()
        evidence.refresh()

    # ---- the downloads and the export

    def export(fmt: str) -> None:
        if "keep" not in state["verdicts"].values():
            ui.notify("Keep at least one threat before you export.", type="warning")
            return
        body = export_kept(model, state["verdicts"], fmt=fmt)
        if fmt == "json":
            data = body.encode("utf-8")
            ui.download.content(data, "kept_threats.json", "application/json")
        else:
            data = body.encode("utf-8")
            ui.download.content(data, "kept_threats.md", "text/markdown")

    async def download(name: str) -> None:
        def read() -> bytes | None:
            if name == "threats.md":
                page_text = storage.read_threats_page(run_id, story_id)
                return None if page_text is None else page_text.encode("utf-8")
            folder = storage.story_folder(run_id, story_id)
            if folder is None:
                return None
            try:
                return (folder / name).read_bytes()
            except OSError:
                return None

        body = await run.io_bound(read)
        if body is None:
            ui.notify(f"{name} is not in the run folder.", type="negative")
            return
        media = "application/json" if name.endswith(".json") else "text/markdown"
        ui.download.content(body, name, media)

    # ---- the parts of the page

    @ui.refreshable
    def counts_line() -> None:
        about, read = counts()
        with ui.column().classes("gap-0"):
            ui.label(about)
            ui.label(read).classes("font-medium")

    def verdict_buttons(row_id: str, marker: str) -> None:
        current = state["verdicts"].get(row_id)
        for verdict, label in (("keep", "Keep"), ("delete", "Delete")):
            chosen = current == verdict
            button = ui.button(label).props(
                "dense no-caps size=sm "
                + (
                    f"unelevated color={VERDICT_COLOR[verdict]}"
                    if chosen
                    else f"outline color={VERDICT_COLOR[verdict]}"
                )
            )
            button.classes("px-3")
            button.mark(f"{marker}-{verdict}-{row_id}")
            button.on("click.stop", lambda _e, r=row_id, v=verdict: set_verdict(r, v))

    def row_view(item: dict) -> None:
        item_row = item["row"]
        row_id = item_row["id"]
        chosen = state["chosen"] == row_id
        box = ui.element("div").classes("sr-row" + (" sr-row-chosen" if chosen else ""))
        box.props(f"id=sr-row-{item['number']} tabindex=0 role=button")
        box.mark(f"row-{row_id}")
        box.on("click", lambda _e, r=row_id: choose(r))
        box.on("keydown.enter", lambda _e, r=row_id: choose(r))
        with box:
            ui.label(str(item["number"])).classes("sr-row-no")
            with ui.column().classes("sr-row-body gap-1"):
                text(item_row.get("threat", ""), "sr-threat")
                with ui.row().classes("items-center gap-1"):
                    kind = item_row.get("type", "")
                    with ui.element("span"):
                        tag(KIND_SHORT.get(kind, str(kind).capitalize()))
                        if kind in KIND_HINT:
                            ui.tooltip(KIND_HINT[kind])
                    actor = labels["actors"].get(item_row.get("actor", ""))
                    if actor:
                        with ui.element("span"):
                            tag(_short(actor))
                            ui.tooltip(CHOICE_HINT)
                    stride = labels["stride"].get(item_row.get("stride", ""))
                    if stride:
                        with ui.element("span"):
                            tag(_short(stride))
                            ui.tooltip(CHOICE_HINT)
                    if item_row.get("harm"):
                        tag(str(item_row["harm"]).capitalize())
                    if item_row.get("weak_signs"):
                        with ui.element("span"):
                            tag("weak support", "caution")
                            ui.tooltip("; ".join(map(str, item_row["weak_signs"])))
                folded = " ".join(str(item_row.get("quote", "")).split())
                with ui.element("div").classes("sr-quote-box"):
                    ui.label(folded)
                with ui.row().classes("items-center gap-x-4 gap-y-0"):
                    part = passages.get(item_row.get("source"))
                    if part:
                        text(part["page"], "sr-small break-all")
            with ui.row().classes("sr-row-actions gap-1 no-wrap"):
                verdict_buttons(row_id, "row")

    @ui.refreshable
    def list_view() -> None:
        for group in grouped["groups"]:
            title = GROUP_TITLE.get(group["key"], str(group["title"]))
            ui.label(f"{title} ({len(group['rows'])})").classes("sr-group-title")
            if GROUP_NOTE.get(group["key"]):
                ui.label(GROUP_NOTE[group["key"]]).classes("sr-group-note")
            with ui.column().classes("sr-group-box w-full gap-0"):
                for item in group["rows"]:
                    row_view(item)
        if model.get("about"):
            words = len(str(model["about"]).split())
            ui.label(
                f"The writer also read your paragraph about the product "
                f"({words} words). It is background, not a source."
            ).classes("sr-small mt-6")

    @ui.refreshable
    def evidence() -> None:
        item = by_id.get(state["chosen"])
        if item is None:
            ui.label("Pick a threat to see its evidence.").classes("text-[#5B6570]")
            return
        item_row = item["row"]
        row_id = item_row["id"]
        ui.label("Evidence").classes("sr-section-title")
        part = passages.get(item_row.get("source"))
        quote = str(item_row.get("quote", ""))
        with ui.column().classes("gap-0 w-full"):
            if part:
                text(part["page"], "sr-small break-all")
                if part["section"]:
                    text(part["section"], "font-medium")
        spans = locate_quote(part["body"], quote) if part else []
        if part and spans:
            with ui.element("div").classes("sr-ev-passage").props("id=sr-ev-passage"):
                marked_passage(part["body"], spans)
            # Scroll to the quote. Two lines of text stay in view above it,
            # and the top edge of the box is at the start of a line.
            ui.run_javascript(
                "setTimeout(() => {"
                "const box = document.getElementById('sr-ev-passage');"
                "const mark = box && box.querySelector('.sr-mark');"
                "if (!mark) { return; }"
                "const line = parseFloat(getComputedStyle(mark).lineHeight) || 24;"
                "const pad = parseFloat(getComputedStyle(box).paddingTop) || 0;"
                # A box that scrolls gets a height of whole lines, so the
                # last scroll position is also at the start of a line.
                "if (box.scrollHeight > box.clientHeight) {"
                "const rows = Math.floor((box.clientHeight - pad - 4) / line);"
                "const edge = box.offsetHeight - box.clientHeight;"
                "box.style.maxHeight = (rows * line + pad + 4 + edge) + 'px';"
                "}"
                "const above = Math.round((mark.offsetTop - pad) / line) - 2;"
                "box.scrollTop = above > 0 ? pad + above * line - 4 : 0;"
                "}, 80)"
            )
        else:
            ui.label(
                "Cannot highlight the quote in this passage. The quote is below."
            ).classes("text-warning")
            marked_passage(" ".join(quote.split()), [])
        if item_row.get("story_part"):
            ui.label("In your story").classes("sr-ev-label")
            text(f'"{item_row["story_part"]}"')
        ui.label("Requirement for this threat").classes("sr-ev-label")
        text(item_row.get("requirement", ""))
        ui.label("Checker").classes("sr-ev-label")
        second = item_row.get("second_model", "")
        ui.label(CHECKER_TEXT.get(second, "no answer")).classes(
            "text-warning" if second == "refuses" else ""
        )
        if item_row.get("weak_signs"):
            ui.label("Why the support is weak").classes("sr-ev-label")
            with ui.column().classes("gap-1"):
                for sign in item_row["weak_signs"]:
                    text(str(sign), "sr-sign")
        with ui.row().classes("gap-2 mt-2"):
            verdict_buttons(row_id, "panel")
        with ui.row().classes("gap-2"):
            index = order.index(row_id)
            previous_button = ui.button("Previous threat", on_click=lambda: move(-1))
            previous_button.props("flat no-caps dense color=primary")
            previous_button.set_enabled(index > 0)
            next_button = ui.button("Next threat", on_click=lambda: move(1))
            next_button.props("flat no-caps dense color=primary")
            next_button.set_enabled(index < len(order) - 1)

    # ---- the page

    with ui.row().classes("sr-result gap-0"):
        with ui.column().classes("sr-list-col gap-3"):
            with ui.row().classes("w-full items-start justify-between no-wrap"):
                with ui.column().classes("gap-0 min-w-0"):
                    page_title(
                        f"Threat list: {story_id}", str(story.get("title", ""))
                    )
                with ui.row().classes("gap-1 no-wrap"):
                    for label, target in (
                        ("Previous story", previous_id),
                        ("Next story", following_id),
                    ):
                        button = ui.button(
                            label,
                            on_click=lambda _e=None, t=target: ui.navigate.to(
                                story_path + url_quote(t, safe="")
                            ),
                        ).props("flat no-caps color=primary")
                        button.set_enabled(target is not None)
            if story.get("story"):
                text(str(story["story"]), "max-w-[76ch]")
            text(NOTICE, "sr-notice")
            with ui.row().classes("items-center gap-3"):
                counts_line()
                if _not_tested(model):
                    tag("untested prompts", "caution")
                formats_tag(data["manifest"])
            with ui.row().classes("items-baseline gap-3"):
                ui.label(_steps_line(model)).classes("sr-small").mark("steps-line")
                address = (
                    f"/how-it-works?kind=threats&run={url_quote(run_id, safe='')}"
                    f"&story={url_quote(story_id, safe='')}"
                )
                ui.link("Replay", address).classes("sr-small").style(
                    "color:var(--sr-blue)"
                )
            with ui.row().classes("items-center gap-2 w-full"):
                export_button = ui.button("Export kept threats")
                export_button.props("outline no-caps color=primary")
                with export_button, ui.menu():
                    item = ui.menu_item("Markdown", lambda: export("markdown"))
                    item.mark("export-markdown")
                    item = ui.menu_item("JSON", lambda: export("json"))
                    item.mark("export-json")
                ui.label("All threats:").classes("sr-small ml-3")
                for name in ("threats.json", "threats.md"):
                    link = ui.link(name, "#").classes("sr-small")
                    link.style("color:var(--sr-blue)")
                    link.on("click.prevent", lambda _e=None, n=name: download(n))
            failed = sum(1 for c in model["calls"] if not c.get("ok"))
            if failed:
                text(
                    f"{failed} of {len(model['calls'])} calls to the writer failed. "
                    "This list is not complete.",
                    "sr-notice sr-warning",
                )
            if not flat:
                text(
                    "No threats found for this story. Replay the steps to see why.",
                    "sr-notice",
                )
            list_view()
        with ui.column().classes("sr-evidence gap-3"):
            evidence()
    ui.keyboard(on_key=on_key, ignore=["input", "select", "textarea"])
