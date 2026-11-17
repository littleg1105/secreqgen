"""All runs."""

from __future__ import annotations

from nicegui import run, ui

from secreqgen_gui import jobs, storage
from secreqgen_gui.widgets import confirm, empty_state, page_title

KIND_WORDS = {"threats": "Threat list", "requirements": "Requirements"}
STATE_WORDS = {
    "running": "Running",
    "finished": "Done",
    "stopped": "Stopped",
    "aborted": "Failed",
    "failed": "Failed",
    "interrupted": "Interrupted",
    "damaged": "Unreadable",
}
STATE_HINTS = {
    "interrupted": "The GUI closed during this run.",
    "damaged": "The files of this run cannot be read. Delete it.",
}

COLUMNS = [
    {"name": "date", "label": "Date", "field": "date", "align": "left"},
    {"name": "kind", "label": "Kind", "field": "kind", "align": "left"},
    {"name": "product", "label": "Product", "field": "product", "align": "left"},
    {"name": "stories", "label": "Stories", "field": "stories", "align": "left"},
    {"name": "models", "label": "Models", "field": "models", "align": "left"},
    {"name": "result", "label": "Result", "field": "result", "align": "left"},
    {"name": "state", "label": "State", "field": "state", "align": "left"},
    {"name": "action", "label": "", "field": "id", "align": "right"},
]

ACTION_SLOT = """
<q-td :props="props" auto-width>
  <q-btn flat dense no-caps color="negative" label="Delete"
         :disable="props.row.busy"
         @click.stop="$parent.$emit('delete', props.row)" />
</q-td>
"""

STATE_SLOT = """
<q-td :props="props">
  <span :title="props.row.hint">{{ props.value }}</span>
</q-td>
"""

IN_PROGRESS = "This run is in progress. Stop it first."


def _in_progress(run_id: str) -> bool:
    """Return True when the run is checking or running now."""
    state = jobs.manager.get(run_id)
    if state is not None and state.state in ("checking", "running"):
        return True
    try:
        manifest = storage.read_manifest(run_id)
    except ValueError:
        return False
    return isinstance(manifest, dict) and manifest.get("status") == "running"


def format_date(item: dict) -> str:
    """Return the start of a run in the local time zone."""
    return storage.run_date_text(item)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _models(item: dict) -> str:
    names = [item.get("writer"), item.get("checker")] or []
    if item.get("kind") == "requirements":
        names = [item.get("model")]
    return ", ".join(str(n) for n in names if n)


def _result(item: dict) -> str:
    done = [
        s
        for s in item.get("per_story") or []
        if isinstance(s, dict) and s.get("status", "ok") == "ok"
    ]
    if not done:
        return ""
    if item.get("kind") == "requirements":
        count = sum(int(s.get("requirements") or 0) for s in done)
        return _plural(count, "requirement")
    text = _plural(sum(int(s.get("rows") or 0) for s in done), "threat")
    if item.get("checker"):
        text += f", {sum(int(s.get('accepted') or 0) for s in done)} accepted"
    return text


def make_row(item: dict) -> dict:
    """Make one plain table row from a manifest. A row has no lists."""
    status = str(item.get("status") or "")
    total = item.get("stories_total")
    return {
        "id": item["id"],
        "date": format_date(item),
        "kind": KIND_WORDS.get(str(item.get("kind")), ""),
        "product": str(item.get("product") or ""),
        "stories": str(total) if total else "",
        "models": _models(item),
        "result": _result(item),
        "state": STATE_WORDS.get(status, status.capitalize()),
        "hint": STATE_HINTS.get(status, ""),
        "busy": status == "running",
    }


async def page() -> None:
    page_title("History")
    holder = ui.column().classes("w-full gap-0 mt-4")

    def show(runs: list[dict]) -> None:
        holder.clear()
        with holder:
            if not runs:
                empty_state("No runs yet.", "New threat list", "/threats/new")
                return
            _table([make_row(item) for item in runs], reload)

    def reload() -> None:
        show(storage.list_runs())

    show(await run.io_bound(storage.list_runs))


def _table(rows: list[dict], reload) -> None:
    table = (
        ui.table(columns=COLUMNS, rows=rows, row_key="id", pagination=25)
        .classes("w-full")
        .props("flat dense")
        .style("cursor: pointer")
    )
    table.add_slot("body-cell-action", ACTION_SLOT)
    table.add_slot("body-cell-state", STATE_SLOT)

    def open_run(event) -> None:
        ui.navigate.to(f"/runs/{event.args[1]['id']}")

    def ask(event) -> None:
        row = event.args
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            return
        run_id = row["id"]
        if _in_progress(run_id):
            ui.notify(IN_PROGRESS, type="warning")
            return

        def delete() -> None:
            if _in_progress(run_id):
                ui.notify(IN_PROGRESS, type="warning")
                return
            try:
                storage.delete_run(run_id)
            except (ValueError, OSError):
                ui.notify("The run could not be deleted.", type="negative")
                return
            ui.notify("Run deleted.", type="positive")
            ui.timer(0.05, reload, once=True)

        confirm("Delete this run and its files?", "Delete", delete)

    table.on("rowClick", open_run)
    table.on("delete", ask)
