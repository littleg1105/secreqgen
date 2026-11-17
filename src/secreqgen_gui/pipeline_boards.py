"""The boards of the step diagram: what each box shows at one moment.

Three sources give a board:

- live_board: the reports of the tool about the story in work (a progress
  dict that apply_event fills from the step events).
- replay_board: one frame of a finished story, from its stored result.
- result_board: a finished story with every step done.

Every number on a board comes from a step event or from a stored result.
The texts say in plain words what a step does. They are true to the code of
the tool (threats/pipeline.py and generation/generator.py).
"""

from __future__ import annotations

from collections.abc import Mapping

from secreqgen_gui.pipeline_view import (
    FLOWS,
    REQUIREMENTS_FLOW,
    THREAT_FLOW,
    Board,
    StepView,
    plural,
    short,
)

# ---------------------------------------------------------------- texts
_STORY = (
    "Your input",
    "You give a user story and the folder with the documentation of the product.",
)

TEXTS: dict[str, dict[str, tuple[str, str]]] = {
    "threats": {
        "story": _STORY,
        "search": (
            "The tool code searches the documentation",
            "A word search (BM25) takes the sections that fit the story best, at "
            "most 3,000 tokens of text. The passages go into groups of 5. One more "
            "group holds at most 5 sections that have a risk word.",
        ),
        "write": (
            "The writer model writes rows",
            "The writer model reads the story and one group of passages. It says "
            "for each passage if the passage fits the story. For each threat or "
            "security rule that a passage states, it writes one row: the threat, "
            "the exact words of the documentation, and a requirement.",
        ),
        "check": (
            "The tool code checks each row",
            "These are fixed rules, with no AI. The quote must be in the passage "
            "word for word. The note of the writer must say that the passage fits "
            "the story. A row with a missing part, a quote that is too long, or a "
            "second row for the same quote is removed.",
        ),
        "labels": (
            "The writer model names a threat actor and a STRIDE category",
            "This is one call for the story. The writer model chooses a threat "
            "actor and a STRIDE category for each row, from fixed lists.",
        ),
        "second_model": (
            "The second model checks each row",
            "The second model reads each row beside its passage. It looks for two "
            "clear faults: the quote is about a different feature than the story, "
            "or the threat says something that the passage does not say. It "
            "answers OK or FAULT. A row with a fault stays in the list, in a group "
            "of its own.",
        ),
        "weak_signs": (
            "The tool code marks weak signs",
            "A script looks for signs of a weak row, for example a quote of 5 "
            "words or fewer. The page shows the signs beside the row. They remove "
            "no row.",
        ),
        "read": (
            "You read the list",
            "You read each row beside its evidence, and you keep it or delete it. "
            "The list is a draft for a person to check. It is not a complete "
            "threat model.",
        ),
    },
    "requirements": {
        "story": _STORY,
        "search": (
            "The tool code searches the documentation",
            "A word search (BM25) takes the sections that fit the story best, at "
            "most 3,000 tokens of text.",
        ),
        "write": (
            "The model writes the requirements",
            "The model reads the story and the passages. It writes security "
            "requirements in a fixed form. When the tool code cannot read the "
            "answer, it asks again. It asks at most 3 times in all.",
        ),
        "parse": (
            "The tool code reads the answer",
            "The tool code puts each requirement into the fixed form: the text, "
            "the ASVS category and the priority.",
        ),
        "read": (
            "You read the requirements",
            "You read the requirements of each story on the result page.",
        ),
    },
}


def lane_notes(
    writer: str | None = None, checker: str | None = None, server: str | None = None
) -> dict[str, str]:
    """Return the small texts of the lanes: the model names and the server."""
    notes = {"checker": checker or "off in this run"}
    if writer:
        notes["writer"] = writer
    if server:
        notes["server"] = server.split("://", 1)[-1].rstrip("/")
    return notes


def _number(numbers: Mapping, name: str) -> int:
    value = numbers.get(name, 0)
    return int(value) if isinstance(value, int | float) else 0


def _stays(count: int) -> str:
    return f"{plural(count, 'row')} {'stays' if count == 1 else 'stay'}"


def _removed(count: int) -> str:
    return f"{plural(count, 'row')} {'is' if count == 1 else 'are'} removed"


# ---------------------------------------------------------------- live


def new_progress(kind: str, story_id: str = "") -> dict:
    """Return the empty progress of one story. The dict is JSON safe."""
    flow = FLOWS[kind]
    return {
        "kind": kind,
        "story_id": story_id,
        "steps": {
            step.key: {"state": "waiting", "index": 0, "total": 0, "numbers": {}}
            for step in flow.steps[1:-1]
        },
        "finished": False,
    }


def apply_event(progress: dict, event) -> None:
    """Put one step event of the tool into the progress. It never raises.

    The event has story_id, step, state, index, total and numbers (see
    secreqgen.application.steps.StepEvent).
    """
    try:
        _apply_event(progress, event)
    except Exception:  # the picture must never stop a run  # noqa: BLE001
        return


def _apply_event(progress: dict, event) -> None:
    story_id = str(getattr(event, "story_id", "") or "")
    if story_id and story_id != progress.get("story_id"):
        progress.update(new_progress(progress["kind"], story_id))
    steps = progress["steps"]
    entry = steps.get(event.step)
    if entry is None:
        return
    numbers = {
        str(name): value
        for name, value in dict(event.numbers or {}).items()
        if isinstance(value, int | float)
    }
    if event.state == "failed":
        entry["state"] = "failed"
        return
    names = list(steps)
    # The checks of one group are a part of the loop: the writer goes on.
    in_loop = event.step == "check" and "kept" not in numbers
    if not in_loop:
        for name in names[: names.index(event.step)]:
            earlier = steps[name]
            if earlier["state"] in ("active", "partial"):
                earlier["state"] = "done"
            elif earlier["state"] == "waiting":
                earlier["state"] = "off"  # the story passed this step
    if event.index:
        entry["index"] = int(event.index)
    if event.total:
        entry["total"] = int(event.total)
    kept = entry["numbers"]
    if event.step == "write" and progress["kind"] == "threats":
        kept["attempt"] = numbers.get("attempt", kept.get("attempt", 1))
        if event.state == "start":
            kept["passages"] = numbers.get("passages", 0)
        elif numbers.get("answered"):
            kept["rows"] = kept.get("rows", 0) + numbers.get("rows_written", 0)
            kept["answered"] = kept.get("answered", 0) + 1
        entry["state"] = "active"  # the next step ends the loop
    elif event.step == "write":
        kept.update(numbers)
        entry["state"] = "active"  # the step "parse" ends it
    elif event.step == "check":
        kept.update(numbers)
        entry["state"] = "done" if "kept" in numbers else "partial"
    else:
        kept.update(numbers)
        last_part = not event.total or event.index >= event.total
        done = event.state == "done" and last_part
        entry["state"] = "done" if done else "active"
    if event.step == names[-1] and entry["state"] == "done":
        progress["finished"] = True


def _live_lines(kind: str, key: str, entry: Mapping) -> tuple[str, ...]:
    """Return the short lines of one box from the progress of its step."""
    state, numbers = entry["state"], entry["numbers"]
    index, total = entry["index"], entry["total"]

    def n(name: str) -> int:
        return _number(numbers, name)

    if state in ("waiting", "off"):
        return ()
    if state == "failed":
        return ("stopped here",)
    if key == "search":
        if state != "done" or "passages" not in numbers:
            return ()
        lines = [plural(n("passages"), "passage")]
        if "groups" in numbers:
            lines.append(plural(n("groups"), "group"))
        return tuple(lines)
    if key == "write" and kind == "threats":
        if state == "active":
            lines = [f"group {index} of {total}" if total else f"group {index}"]
            if n("attempt") > 1:
                lines.append(f"attempt {n('attempt')}")
            return tuple(lines)
        lines = [plural(n("rows"), "row")]
        failed = total - n("answered")
        if total and failed > 0:
            lines.append(f"{plural(failed, 'group')} failed")
        return tuple(lines)
    if key == "write":
        attempt = max(index, n("attempt"))
        if state == "active":
            return (f"attempt {attempt}",) if attempt > 1 else ()
        return (plural(attempt, "attempt"),) if attempt > 1 else ()
    if key == "check":
        if state == "partial":
            return (f"{index} of {total} groups",) if total else ()
        return (f"{n('kept')} kept", f"{n('removed')} removed")
    if key == "labels":
        if state == "active":
            return (plural(n("rows"), "row"),)
        return (f"{n('labelled')} of {n('rows')} rows",)
    if key == "second_model":
        if state == "active":
            lines = [f"row {index} of {total}" if total else f"row {index}"]
            if n("accepted") or n("faults") or n("no_verdict"):
                lines.append(f"{n('accepted')} accepted")
            return tuple(lines)
        return (f"{n('accepted')} accepted", plural(n("faults"), "fault"))
    if key == "weak_signs":
        return (f"{n('with_sign')} with a sign", f"{n('clean')} with none")
    if key == "parse":
        return (plural(n("requirements"), "requirement"),)
    return ()


def _live_text(kind: str, key: str, entry: Mapping) -> str:
    """Return the sentence with the numbers of the step in work."""
    numbers, index, total = entry["numbers"], entry["index"], entry["total"]
    if key == "write" and kind == "threats" and total:
        attempt = _number(numbers, "attempt")
        more = f", attempt {attempt}" if attempt > 1 else ""
        return f"Group {index} of {total}{more}."
    if key == "write" and index > 1:
        return f"Attempt {index}."
    if key == "second_model" and total:
        return f"Row {index} of {total}."
    return ""


def live_board(
    progress: Mapping,
    *,
    labels: bool = True,
    checker: bool = True,
    notes: Mapping[str, str] | None = None,
) -> Board:
    """Return the board of the story in work, from the reports of the tool."""
    kind = progress["kind"]
    flow = FLOWS[kind]
    texts = TEXTS[kind]
    story_id = str(progress.get("story_id") or "")
    views: dict[str, StepView] = {
        "story": StepView("done", (short(story_id, 14),))
        if story_id
        else StepView("waiting")
    }
    active = failed = None
    for key, entry in progress["steps"].items():
        state = entry["state"]
        switched_off = (key == "labels" and not labels) or (
            key == "second_model" and not checker
        )
        if switched_off:
            views[key] = StepView("off", ("off",))
            continue
        if state == "off":
            views[key] = StepView("off", ("no rows",))
            continue
        views[key] = StepView(state, _live_lines(kind, key, entry))
        if state == "active":
            active = key
        elif state == "failed":
            failed = key
    finished = bool(progress.get("finished"))
    views["read"] = StepView("partial", ("your turn",)) if finished else StepView()
    write = progress["steps"].get("write", {})
    part = int(write.get("index", 0)) if kind == "threats" else 0
    facts = ""
    if failed:
        title = "The story stopped at this step: " + flow.step(failed).name
        text = "The step did not end. The list of stories below shows the error."
    elif active:
        title, text = texts[active]
        facts = _live_text(kind, active, progress["steps"][active])
    elif finished:
        title, text = texts["read"]
    elif story_id:
        title, text = "The story starts", ""
    else:
        title, text = "No story is in work", ""
    return Board(
        kind=kind,
        steps=views,
        active=active,
        part=part if active == "write" else 0,
        loop_used=part > 1,
        title=title,
        text=text,
        facts=facts,
        lane_notes=dict(notes or {}),
    )


# ---------------------------------------------------------------- a finished story


def replay_frames(kind: str, trace: Mapping) -> list[tuple[str, int]]:
    """Return the frames of a replay: (step, group number or 0), in true order."""
    if kind != "threats":
        return [(step.key, 0) for step in REQUIREMENTS_FLOW.steps]
    frames: list[tuple[str, int]] = [("story", 0), ("search", 0)]
    for group in trace.get("groups") or []:
        number = int(group.get("group", 0))
        frames.append(("write", number))
        if group.get("answered"):
            frames.append(("check", number))
    if not (trace.get("groups") or []):
        frames.append(("write", 0))
    rows = _number(trace.get("check") or {}, "kept")
    if (trace.get("labels") or {}).get("on") and rows:
        frames.append(("labels", 0))
    if (trace.get("second_model") or {}).get("on") and rows:
        frames.append(("second_model", 0))
    frames += [("weak_signs", 0), ("read", 0)]
    return frames


def _reason_text(reasons) -> str:
    parts = [f"{text} ({count})" for text, count in reasons or []]
    return " Reasons: " + ", ".join(parts) + "." if parts else ""


def _threat_frame(trace: Mapping, frame: tuple[str, int], verdicts) -> tuple:
    """Return the views, the loop state and the numbers text of one frame."""
    step, group_no = frame
    order = [s.key for s in THREAT_FLOW.steps]
    here = order.index(step)
    search = trace.get("search") or {}
    groups = list(trace.get("groups") or [])
    check = trace.get("check") or {}
    labels = trace.get("labels") or {}
    second = trace.get("second_model") or {}
    weak = trace.get("weak_signs") or {}
    total = len(groups)
    kept_all, removed_all = _number(check, "kept"), _number(check, "removed")

    def passed(key: str) -> bool:
        return order.index(key) < here

    def so_far(upto: int) -> tuple[int, int]:
        part = [g for g in groups if int(g.get("group", 0)) <= upto]
        return (
            sum(_number(g, "kept") for g in part),
            sum(_number(g, "removed") for g in part),
        )

    this = next((g for g in groups if int(g.get("group", 0)) == group_no), {})
    views: dict[str, StepView] = {
        "story": StepView(
            "active" if step == "story" else "done",
            (short(str(trace.get("story_id") or ""), 14),),
        )
    }
    text = ""
    # search
    if step == "search" or passed("search"):
        lines = (
            plural(_number(search, "passages"), "passage"),
            plural(_number(search, "groups"), "group"),
        )
        views["search"] = StepView("active" if step == "search" else "done", lines)
        if step == "search":
            text = f" For this story: {lines[0]} in {lines[1]}."
            extra = _number(search, "extra_passages")
            if extra:
                text += (
                    f" The last group holds {plural(extra, 'section')} "
                    "with a risk word."
                )
    # the loop
    in_loop = step in ("write", "check")
    after_loop = here > order.index("check")
    if step == "write":
        lines = [f"group {group_no} of {total}"] if total else []
        if this:
            lines.append(plural(_number(this, "passages"), "passage"))
        views["write"] = StepView("active", tuple(lines))
        if group_no > 1:
            kept, removed = so_far(group_no - 1)
            views["check"] = StepView("partial", (f"{kept} kept", f"{removed} removed"))
        if this:
            text = (
                f" Group {group_no} of {total}: "
                f"{plural(_number(this, 'passages'), 'passage')}, "
                f"{plural(_number(this, 'attempts'), 'attempt')}, "
                f"{this.get('seconds', 0):g} seconds."
            )
            if this.get("extra"):
                text += " This is the group of sections with a risk word."
            if not this.get("answered"):
                text += " The tool code could not read an answer for this group."
    elif step == "check":
        kept, removed = so_far(group_no)
        last = group_no >= total
        views["write"] = (
            StepView("done", (plural(kept_all + removed_all, "row"),))
            if last
            else StepView("partial", (f"{group_no} of {total} groups",))
        )
        views["check"] = StepView("active", (f"{kept} kept", f"{removed} removed"))
        text = (
            f" Group {group_no} of {total}: {_stays(_number(this, 'kept'))}, "
            f"{_removed(_number(this, 'removed'))}."
        )
        if last:
            text += (
                f" In all, {_stays(kept_all)} and {_removed(removed_all)}."
                + _reason_text(check.get("reasons"))
            )
        elif group_no > 1:
            text += f" So far: {_stays(kept)}, {_removed(removed)}."
    elif after_loop:
        views["write"] = StepView("done", (plural(kept_all + removed_all, "row"),))
        views["check"] = StepView(
            "done", (f"{kept_all} kept", f"{removed_all} removed")
        )
    # labels and the second model
    for key, facts in (("labels", labels), ("second_model", second)):
        if not facts.get("on"):
            views[key] = StepView("off", ("off",))
        elif after_loop and not kept_all:
            views[key] = StepView("off", ("no rows",))
    if labels.get("on") and kept_all and (step == "labels" or passed("labels")):
        line = f"{_number(labels, 'labelled')} of {_number(labels, 'rows')} rows"
        views["labels"] = StepView("active" if step == "labels" else "done", (line,))
        if step == "labels":
            text = f" {line.capitalize()} have a label."
    if second.get("on") and kept_all and (
        step == "second_model" or passed("second_model")
    ):
        accepted, faults = _number(second, "accepted"), _number(second, "faults")
        open_rows = _number(second, "no_verdict")
        views["second_model"] = StepView(
            "active" if step == "second_model" else "done",
            (f"{accepted} accepted", plural(faults, "fault")),
        )
        if step == "second_model":
            text = f" {accepted} accepted, {faults} with a fault"
            text += f", {open_rows} with no verdict." if open_rows else "."
    if step == "weak_signs" or passed("weak_signs"):
        with_sign, clean = _number(weak, "with_sign"), _number(weak, "clean")
        views["weak_signs"] = StepView(
            "active" if step == "weak_signs" else "done",
            (f"{with_sign} with a sign", f"{clean} with none"),
        )
        if step == "weak_signs":
            text = (
                f" {plural(with_sign, 'row')} with a weak sign, "
                f"{plural(clean, 'row')} with none."
            )
    if step == "read":
        lines: tuple[str, ...] = ()
        if verdicts is not None and kept_all:
            decided = sum(1 for v in verdicts.values() if v in ("keep", "delete"))
            lines = (f"{decided} of {kept_all} decided",)
            kept_rows = sum(1 for v in verdicts.values() if v == "keep")
            text = f" You decided {decided} of {plural(kept_all, 'row')}"
            text += (
                f": {kept_rows} kept, {decided - kept_rows} deleted."
                if decided
                else "."
            )
        views["read"] = StepView("active", lines)
    loop_used = (in_loop and group_no > 1) or (after_loop and total > 1)
    return views, loop_used, text


def _requirements_frame(trace: Mapping, frame: tuple[str, int]) -> tuple:
    step = frame[0]
    order = [s.key for s in REQUIREMENTS_FLOW.steps]
    here = order.index(step)
    views: dict[str, StepView] = {}
    text = ""
    facts = {
        "story": (short(str(trace.get("story_id") or ""), 14),),
        "search": (
            (plural(int(trace["passages"]), "passage"),)
            if isinstance(trace.get("passages"), int)
            else ()
        ),
        "write": (
            (plural(int(trace["attempts"]), "attempt"),)
            if isinstance(trace.get("attempts"), int) and trace["attempts"] > 1
            else ()
        ),
        "parse": (
            (plural(int(trace["requirements"]), "requirement"),)
            if isinstance(trace.get("requirements"), int)
            else ()
        ),
        "read": (),
    }
    for position, key in enumerate(order):
        if position < here:
            views[key] = StepView("done", facts[key])
        elif position == here:
            views[key] = StepView("active", facts[key])
            if facts[key] and key in ("search", "parse"):
                text = f" For this story: {facts[key][0]}."
    return views, False, text


def replay_board(
    kind: str,
    trace: Mapping,
    frame: tuple[str, int],
    *,
    verdicts: Mapping[str, str] | None = None,
    notes: Mapping[str, str] | None = None,
) -> Board:
    """Return the board of one frame of a finished story.

    The numbers come from the stored result of the story (the trace). The
    verdicts are the decisions of the reader: row id to "keep" or "delete".
    """
    if kind == "threats":
        views, loop_used, more = _threat_frame(trace, frame, verdicts)
    else:
        views, loop_used, more = _requirements_frame(trace, frame)
    title, text = TEXTS[kind][frame[0]]
    return Board(
        kind=kind,
        steps=views,
        active=frame[0],
        part=frame[1],
        loop_used=loop_used,
        title=title,
        text=text,
        facts=more.strip(),
        lane_notes=dict(notes or {}),
    )


def result_board(
    kind: str,
    trace: Mapping,
    *,
    verdicts: Mapping[str, str] | None = None,
    notes: Mapping[str, str] | None = None,
) -> Board:
    """Return the board of a finished story: every step done, with its numbers."""
    last = replay_frames(kind, trace)[-1]
    board = replay_board(kind, trace, last, verdicts=verdicts, notes=notes)
    views = dict(board.steps)
    read = views.get("read", StepView())
    rows = _number(trace.get("check") or {}, "kept") if kind == "threats" else 0
    decided = sum(1 for v in (verdicts or {}).values() if v in ("keep", "delete"))
    all_decided = bool(rows) and decided >= rows
    views["read"] = StepView("done" if all_decided else "partial", read.lines)
    return Board(
        kind=kind,
        steps=views,
        active=None,
        loop_used=board.loop_used,
        title=board.title,
        text=board.text,
        facts=board.facts,
        lane_notes=board.lane_notes,
    )


# ---------------------------------------------------------------- no story


def plain_frames(kind: str) -> list[tuple[str, int]]:
    """Return one frame for each step of a flow, for a page with no story."""
    return [(step.key, 0) for step in FLOWS[kind].steps]


def plain_board(
    kind: str, frame: tuple[str, int], *, notes: Mapping[str, str] | None = None
) -> Board:
    """Return a board with no numbers: the steps before the frame are done."""
    flow = FLOWS[kind]
    here = flow.index(frame[0])
    views = {
        step.key: StepView("done" if position < here else "active")
        for position, step in enumerate(flow.steps)
        if position <= here
    }
    title, text = TEXTS[kind][frame[0]]
    return Board(
        kind=kind,
        steps=views,
        active=frame[0],
        title=title,
        text=text,
        lane_notes=dict(notes or {}),
    )
