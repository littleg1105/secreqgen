"""The boards of the step diagram: what each box shows at one moment.

Three sources give a board:

- live_board: the reports of the tool about the story in work (a progress
  dict that apply_event fills from the step events).
- replay_board: one frame of a finished story, from its stored result.
- result_board: a finished story with every step done.

Every number on a board comes from a step event or from a stored result.
Each step has a title, one short sentence and, where they help, details. The
texts are true to the code of the tool (threats/pipeline.py and
generation/generator.py). The words follow the list of names of the GUI:
threat, quote, passage, writer, checker, accepted, flagged, passed, failed.
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
# For each step: the title, one short sentence, and the details.

_STORY = (
    "Story and documentation",
    "One user story and the documentation folder.",
    "",
)
_SEARCH = (
    "Search",
    "Word search (BM25), no AI. Finds the passages that match the story.",
)
_PAGE_FILL = (
    "Up to 3,000 tokens, page by page: the best sections first, then the rest "
    "of the page."
)

TEXTS: dict[str, dict[str, tuple[str, str, str]]] = {
    "threats": {
        "story": _STORY,
        "search": (
            *_SEARCH,
            _PAGE_FILL + " Groups of 5 passages. Then one group of up to 5 other "
            'sections with a risk word such as "attack" or "bypass".',
        ),
        "write": (
            "Write threats",
            "Writer model. Reads the story and one group of passages. For each "
            "threat or security rule in a passage: a threat, its quote, a "
            "requirement.",
            "The writer also says if each passage fits the story. Up to 3 tries "
            "if the answer cannot be read.",
        ),
        "check": (
            "Quote check",
            "Plain code, no AI. A threat passes only if its quote is in the "
            "passage.",
            "Ignores capital letters and Markdown marks. A threat fails when: "
            "the quote is not in the passage; the passage is not in the group; "
            "the writer did not say that the passage fits the story; a field is "
            "empty; the quote has more than 60 words; another threat has the "
            "same quote.",
        ),
        "labels": (
            "Actor and STRIDE",
            "Writer model, asked once for the story. Picks both from fixed lists.",
            "",
        ),
        "second_model": (
            "Checker",
            "Checker model. Flags a threat if the quote is about another "
            "feature, or if the threat says more than the passage.",
            "Reads each threat next to its passage: OK (accepted) or FAULT "
            "(flagged). A flagged threat stays in the list, in its own group.",
        ),
        "weak_signs": (
            "Weak support",
            "Plain code, no AI. Marks threats with weak signs, such as a quote of "
            "5 words or fewer. Removes nothing.",
            "",
        ),
        "read": (
            "Your review",
            "Check each threat against its quote. Keep or delete it. Draft, not a "
            "complete threat model.",
            "",
        ),
    },
    "requirements": {
        "story": _STORY,
        "search": (*_SEARCH, _PAGE_FILL),
        "write": (
            "Write requirements",
            "Model, asked once. Reads the story and the passages.",
            "Up to 3 tries if the answer cannot be read.",
        ),
        "parse": (
            "Parse",
            "Plain code, no AI. Stores each requirement with its ASVS category "
            "and priority.",
            "",
        ),
        "read": (
            "Your review",
            "Open the result.",
            "",
        ),
    },
}

# The reasons for a threat that failed the quote check, in the words of the GUI.
REASONS = {
    "a second row for the same quote": "same quote as another threat",
    "the quote is not in the passage": "quote not in the passage",
    "the passage does not fit the story": "passage does not fit the story",
    "the writer gave no note about the passage": "no fit note from the writer",
    "the source name is unknown": "passage not in the group",
    "the quote is too long": "quote too long",
}


def lane_notes(
    writer: str | None = None, checker: str | None = None, server: str | None = None
) -> dict[str, str]:
    """Return the small texts of the lanes: the model names and the server."""
    notes = {"checker": checker or "off"}
    if writer:
        notes["writer"] = writer
    if server:
        notes["server"] = server.split("://", 1)[-1].rstrip("/")
    return notes


def _number(numbers: Mapping, name: str) -> int:
    value = numbers.get(name, 0)
    return int(value) if isinstance(value, int | float) else 0


# ---------------------------------------------------------------- box lines


def _search_lines(numbers: Mapping) -> tuple[str, ...]:
    if "passages" not in numbers:
        return ()
    lines = [plural(_number(numbers, "passages"), "passage")]
    if "groups" in numbers:
        lines.append(plural(_number(numbers, "groups"), "group"))
    return tuple(lines)


def _written_lines(written: int, failed_groups: int = 0) -> tuple[str, ...]:
    lines = [f"{written} written"]
    if failed_groups > 0:
        lines.append(f"{plural(failed_groups, 'group')} failed")
    return tuple(lines)


def _passed_lines(passed: int, failed: int) -> tuple[str, str]:
    return (f"{passed} passed", f"{failed} failed")


def _label_lines(labelled: int, total: int) -> tuple[str, ...]:
    if labelled == total:
        return (f"{labelled} labelled",)
    return (f"{labelled} of {total} labelled",)


def _checker_lines(accepted: int, flagged: int, open_rows: int) -> tuple[str, str]:
    """Return the two lines of the checker box when the step is done.

    A threat with no answer must show: with no such line, a step where every
    call failed looks like a step with nothing flagged.
    """
    if not open_rows:
        return (f"{accepted} accepted", f"{flagged} flagged")
    if not flagged:
        return (f"{accepted} accepted", f"{open_rows} no answer")
    return (f"{accepted} accepted", f"{flagged + open_rows} not accepted")


def _weak_lines(weak: int) -> tuple[str, ...]:
    return (f"{weak} marked",) if weak else ("none marked",)


def _weak_text(weak: int) -> str:
    if not weak:
        return "No threat has weak support."
    if weak == 1:
        return "1 threat has weak support."
    return f"{weak} threats have weak support."


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
        return _search_lines(numbers) if state == "done" else ()
    if key == "write" and kind == "threats":
        if state == "active":
            lines = [f"group {index} of {total}" if total else f"group {index}"]
            if n("attempt") > 1:
                lines.append(f"attempt {n('attempt')}")
            return tuple(lines)
        return _written_lines(n("rows"), total - n("answered") if total else 0)
    if key == "write":
        attempt = max(index, n("attempt"))
        if state == "active":
            return (f"attempt {attempt}",) if attempt > 1 else ()
        return (plural(attempt, "attempt"),) if attempt > 1 else ()
    if key == "check":
        if state == "partial":
            return (f"{index} of {total} checked",) if total else ()
        return _passed_lines(n("kept"), n("removed"))
    if key == "labels":
        if state == "active":
            return (plural(n("rows"), "threat"),)
        return _label_lines(n("labelled"), n("rows"))
    if key == "second_model":
        if state == "active":
            lines = [f"threat {index} of {total}" if total else f"threat {index}"]
            if n("accepted") or n("faults") or n("no_verdict"):
                lines.append(f"{n('accepted')} accepted")
            return tuple(lines)
        return _checker_lines(n("accepted"), n("faults"), n("no_verdict"))
    if key == "weak_signs":
        return _weak_lines(n("with_sign"))
    if key == "parse":
        return (plural(n("requirements"), "requirement"),)
    return ()


def _live_facts(kind: str, key: str, entry: Mapping) -> str:
    """Return the numbers of the step in work, as one short sentence."""
    numbers, index, total = entry["numbers"], entry["index"], entry["total"]
    if key == "write" and kind == "threats" and total:
        attempt = _number(numbers, "attempt")
        more = f" Attempt {attempt}." if attempt > 1 else ""
        return f"Group {index} of {total}.{more}"
    if key == "write" and index > 1:
        return f"Attempt {index}."
    if key == "second_model" and total:
        return f"Threat {index} of {total}."
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
            views[key] = StepView("off", ("no threats",))
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
    facts = details = ""
    if failed:
        title = f"This story stopped at {flow.step(failed).name}"
        text = "The story list below shows the error."
    elif active:
        title, text, details = texts[active]
        facts = _live_facts(kind, active, progress["steps"][active])
    elif finished:
        title, text, details = texts["read"]
    elif story_id:
        title, text = "The story is starting", ""
    else:
        title, text = "No story is running", ""
    return Board(
        kind=kind,
        steps=views,
        active=active,
        part=part if active == "write" else 0,
        loop_used=part > 1,
        title=title,
        text=text,
        facts=facts,
        details=details,
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
    parts = [f"{REASONS.get(text, text)} ({count})" for text, count in reasons or []]
    return " Failed because: " + ", ".join(parts) + "." if parts else ""


def _seconds(value) -> str:
    try:
        return f"{float(value):g} seconds"
    except (TypeError, ValueError):
        return ""


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
    passed_all, removed_all = _number(check, "kept"), _number(check, "removed")

    def before(key: str) -> bool:
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
    facts = ""
    # search
    if step == "search" or before("search"):
        lines = _search_lines(
            {
                "passages": _number(search, "passages"),
                "groups": _number(search, "groups"),
            }
        )
        views["search"] = StepView("active" if step == "search" else "done", lines)
        if step == "search":
            facts = f"{lines[0]} in {lines[1]}."
            extra = _number(search, "extra_passages")
            if extra:
                facts += (
                    f" The last group has {plural(extra, 'passage')} "
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
            views["check"] = StepView("partial", _passed_lines(*so_far(group_no - 1)))
        if this:
            facts = (
                f"Group {group_no} of {total}: "
                f"{plural(_number(this, 'passages'), 'passage')}"
            )
            attempts = _number(this, "attempts")
            if attempts > 1:
                facts += f", {attempts} attempts"
            took = _seconds(this.get("seconds"))
            facts += f", {took}." if took else "."
            if this.get("extra"):
                facts += " Passages with a risk word."
            if not this.get("answered"):
                facts += " SecReqGen could not read the answer."
    elif step == "check":
        passed, failed = so_far(group_no)
        last = group_no >= total
        views["write"] = (
            StepView("done", _written_lines(passed_all + removed_all))
            if last
            else StepView("partial", (f"{group_no} of {total} groups",))
        )
        views["check"] = StepView("active", _passed_lines(passed, failed))
        facts = (
            f"Group {group_no} of {total}: {_number(this, 'kept')} passed, "
            f"{_number(this, 'removed')} failed."
        )
        if last:
            facts += (
                f" In total {passed_all} passed and {removed_all} failed."
                + _reason_text(check.get("reasons"))
            )
        elif group_no > 1:
            facts += f" So far {passed} passed, {failed} failed."
    elif after_loop:
        views["write"] = StepView("done", _written_lines(passed_all + removed_all))
        views["check"] = StepView("done", _passed_lines(passed_all, removed_all))
    # the labels and the checker
    for key, part in (("labels", labels), ("second_model", second)):
        if not part.get("on"):
            views[key] = StepView("off", ("off",))
        elif after_loop and not passed_all:
            views[key] = StepView("off", ("no threats",))
    if labels.get("on") and passed_all and (step == "labels" or before("labels")):
        labelled, rows = _number(labels, "labelled"), _number(labels, "rows")
        views["labels"] = StepView(
            "active" if step == "labels" else "done", _label_lines(labelled, rows)
        )
        if step == "labels":
            facts = f"{labelled} of {plural(rows, 'threat')} labelled."
    if (
        second.get("on")
        and passed_all
        and (step == "second_model" or before("second_model"))
    ):
        accepted, flagged = _number(second, "accepted"), _number(second, "faults")
        open_rows = _number(second, "no_verdict")
        views["second_model"] = StepView(
            "active" if step == "second_model" else "done",
            _checker_lines(accepted, flagged, open_rows),
        )
        if step == "second_model":
            facts = f"{accepted} accepted, {flagged} flagged"
            facts += f", {open_rows} with no answer." if open_rows else "."
    if step == "weak_signs" or before("weak_signs"):
        with_sign = _number(weak, "with_sign")
        views["weak_signs"] = StepView(
            "active" if step == "weak_signs" else "done", _weak_lines(with_sign)
        )
        if step == "weak_signs":
            facts = _weak_text(with_sign)
    if step == "read":
        lines: tuple[str, ...] = ()
        if verdicts is not None and passed_all:
            decided = sum(1 for v in verdicts.values() if v in ("keep", "delete"))
            kept = sum(1 for v in verdicts.values() if v == "keep")
            if decided:
                lines = (f"{decided} of {passed_all} decided",)
                facts = (
                    f"{decided} of {passed_all} decided: "
                    f"{kept} kept, {decided - kept} deleted."
                )
            else:
                lines = (f"{passed_all} to review",)
                facts = f"{plural(passed_all, 'threat')} to review."
        views["read"] = StepView("active", lines)
    loop_used = (in_loop and group_no > 1) or (after_loop and total > 1)
    return views, loop_used, facts


def _requirements_frame(trace: Mapping, frame: tuple[str, int]) -> tuple:
    step = frame[0]
    order = [s.key for s in REQUIREMENTS_FLOW.steps]
    here = order.index(step)
    views: dict[str, StepView] = {}
    facts = ""
    lines = {
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
            views[key] = StepView("done", lines[key])
        elif position == here:
            views[key] = StepView("active", lines[key])
            if lines[key] and key in ("search", "parse"):
                facts = f"{lines[key][0].capitalize()}."
    return views, False, facts


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
    verdicts are the decisions of the reader: threat id to "keep" or "delete".
    """
    if kind == "threats":
        views, loop_used, facts = _threat_frame(trace, frame, verdicts)
    else:
        views, loop_used, facts = _requirements_frame(trace, frame)
    title, text, details = TEXTS[kind][frame[0]]
    return Board(
        kind=kind,
        steps=views,
        active=frame[0],
        part=frame[1],
        loop_used=loop_used,
        title=title,
        text=text,
        facts=facts,
        details=details,
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
        details=board.details,
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
    title, text, details = TEXTS[kind][frame[0]]
    return Board(
        kind=kind,
        steps=views,
        active=frame[0],
        title=title,
        text=text,
        details=details,
        lane_notes=dict(notes or {}),
    )
