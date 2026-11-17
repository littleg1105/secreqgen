"""The step diagram: layout, boards and the SVG text (pure functions)."""

from __future__ import annotations

import pytest
from secreqgen.application.steps import (
    REQUIREMENTS_STEPS,
    THREAT_STEPS,
    StepEvent,
)
from secreqgen_gui import pipeline_boards as boards
from secreqgen_gui import pipeline_view as view
from secreqgen_gui.pipeline_view import Board, StepView

STORY = "MM-US-007"


def event(step: str, state: str, index: int = 0, total: int = 0, **numbers):
    return StepEvent(STORY, step, state, index, total, numbers)


def threat_events() -> list[StepEvent]:
    """The events of one story, in the order of the contract."""
    return [
        event("search", "start"),
        event("search", "done", passages=14, groups=3, extra_passages=4),
        event("write", "start", 1, 3, attempt=1, passages=5),
        event("write", "done", 1, 3, attempt=1, answered=0, rows_written=0),
        event("write", "start", 1, 3, attempt=2, passages=5),
        event("write", "done", 1, 3, attempt=2, answered=1, rows_written=4),
        event("check", "done", 1, 3),
        event("write", "start", 2, 3, attempt=1, passages=5),
        event("write", "done", 2, 3, attempt=1, answered=1, rows_written=5),
        event("check", "done", 2, 3),
        event("write", "start", 3, 3, attempt=1, passages=4),
        event("write", "done", 3, 3, attempt=1, answered=1, rows_written=2),
        event("check", "done", 3, 3),
        event("check", "done", 3, 3, kept=9, removed=2),
        event("labels", "start", rows=9),
        event("labels", "done", rows=9, labelled=9),
        event("second_model", "start", 1, 9),
        event("second_model", "done", 1, 9, accepted=1, faults=0, no_verdict=0),
        event("second_model", "start", 2, 9),
        event("second_model", "done", 2, 9, accepted=1, faults=1, no_verdict=0),
        event("second_model", "done", 9, 9, accepted=7, faults=2, no_verdict=0),
        event("weak_signs", "done", with_sign=1, clean=8),
    ]


def board_after(count: int, **options) -> Board:
    progress = boards.new_progress("threats", STORY)
    for event in threat_events()[:count]:
        boards.apply_event(progress, event)
    return boards.live_board(progress, **options)


TRACE = {
    "story_id": STORY,
    "seconds": 59,
    "writer": "gemma4:26b",
    "search": {"passages": 12, "groups": 3, "extra_passages": 2},
    "groups": [
        {
            "group": 1,
            "passages": 5,
            "attempts": 1,
            "answered": True,
            "seconds": 11.3,
            "kept": 1,
            "removed": 1,
            "extra": False,
        },
        {
            "group": 2,
            "passages": 5,
            "attempts": 2,
            "answered": True,
            "seconds": 4.0,
            "kept": 2,
            "removed": 0,
            "extra": False,
        },
        {
            "group": 3,
            "passages": 2,
            "attempts": 3,
            "answered": False,
            "seconds": 9.0,
            "kept": 0,
            "removed": 0,
            "extra": True,
        },
    ],
    "check": {
        "kept": 3,
        "removed": 1,
        "reasons": [["the quote is not in the passage", 1]],
    },
    "labels": {"on": True, "rows": 3, "labelled": 3},
    "second_model": {
        "on": True,
        "model": "qwen3.8:27b",
        "accepted": 2,
        "faults": 1,
        "no_verdict": 0,
    },
    "weak_signs": {"with_sign": 1, "clean": 2},
}


# ------------------------------------------------------------------ layout


def test_the_flows_hold_the_steps_that_the_tool_reports():
    threat_keys = tuple(step.key for step in view.THREAT_FLOW.steps)
    assert threat_keys == ("story", *THREAT_STEPS, "read")
    requirement_keys = tuple(step.key for step in view.REQUIREMENTS_FLOW.steps)
    assert requirement_keys == ("story", *REQUIREMENTS_STEPS, "read")


def test_each_step_has_a_text_that_ends_with_a_period():
    for kind, flow in view.FLOWS.items():
        for step in flow.steps:
            title, text = boards.TEXTS[kind][step.key]
            assert title and not title.endswith(".")
            assert text.endswith(".")
            assert chr(0x2014) not in title + text
            assert len(step.title[0]) <= 14 and len(step.title[1]) <= 14


def test_the_connectors_follow_the_order_and_one_goes_back():
    lines = view.connectors(view.THREAT_FLOW)
    forward = [(line.source, line.target) for line in lines if not line.back]
    keys = [step.key for step in view.THREAT_FLOW.steps]
    assert forward == list(zip(keys, keys[1:], strict=False))
    back = [line for line in lines if line.back]
    assert [(line.source, line.target) for line in back] == [("check", "write")]
    assert all(line.path.startswith("M") for line in lines)
    assert not [line for line in view.connectors(view.REQUIREMENTS_FLOW) if line.back]


def chain(came_from, active, part=0):
    board = Board("threats", {}, active=active, part=part)
    found = view.slip_chain(view.THREAT_FLOW, came_from, board)
    return [("back" if c.back else c.source, c.target) for c in found]


def test_the_slip_moves_along_the_true_path():
    assert chain(None, "search") == [("story", "search")]
    assert chain("search", "write", 1) == [("search", "write")]
    # The next group: the rows go to the check, then the next group goes back.
    assert chain("write", "write", 2) == [("write", "check"), ("back", "write")]
    assert chain("write", "write", 1) == []
    assert chain("check", "write", 2) == [("back", "write")]
    assert chain("write", "labels") == [("write", "check"), ("check", "labels")]
    assert chain("labels", "second_model") == [("labels", "second_model")]
    assert chain("second_model", "second_model") == []
    assert chain("labels", None) == []
    assert chain("second_model", "search") == []


# ------------------------------------------------------------------ live


def test_the_live_board_follows_the_reports_of_the_tool():
    board = board_after(1)
    assert board.active == "search"
    assert board.steps["story"] == StepView("done", (STORY,))
    assert board.steps["write"].state == "waiting"

    board = board_after(5)  # second attempt of group 1
    assert board.active == "write" and board.part == 1 and not board.loop_used
    assert board.steps["search"] == StepView("done", ("14 passages", "3 groups"))
    assert board.steps["write"] == StepView("active", ("group 1 of 3", "attempt 2"))
    assert board.facts == "Group 1 of 3, attempt 2."

    board = board_after(8)  # group 2 starts
    assert board.part == 2 and board.loop_used
    assert board.steps["write"] == StepView("active", ("group 2 of 3",))
    assert board.steps["check"] == StepView("partial", ("1 of 3 groups",))

    board = board_after(14)  # the tool knows the rows that stay
    assert board.steps["write"] == StepView("done", ("11 rows",))
    assert board.steps["check"] == StepView("done", ("9 kept", "2 removed"))
    assert board.active is None

    board = board_after(19)  # the second row of the second model starts
    assert board.active == "second_model"
    assert board.steps["labels"] == StepView("done", ("9 of 9 rows",))
    assert board.steps["second_model"] == StepView(
        "active", ("row 2 of 9", "1 accepted")
    )
    assert board.facts == "Row 2 of 9."

    board = board_after(len(threat_events()))
    assert board.active is None
    assert board.steps["second_model"] == StepView("done", ("7 accepted", "2 faults"))
    assert board.steps["weak_signs"] == StepView(
        "done", ("1 with a sign", "8 with none")
    )
    assert board.steps["read"] == StepView("partial", ("your turn",))
    assert board.title == "You read the list"


def test_options_that_are_off_show_as_off():
    board = board_after(1, labels=False, checker=False)
    assert board.steps["labels"] == StepView("off", ("off",))
    assert board.steps["second_model"] == StepView("off", ("off",))


def test_a_step_that_the_story_passed_shows_no_rows():
    progress = boards.new_progress("threats", STORY)
    events = threat_events()
    for event in [*events[:4], StepEvent(STORY, "check", "done", 3, 3, {"kept": 0})]:
        boards.apply_event(progress, event)
    boards.apply_event(
        progress, StepEvent(STORY, "weak_signs", "done", 0, 0, {"clean": 0})
    )
    board = boards.live_board(progress)
    assert board.steps["labels"] == StepView("off", ("no rows",))
    assert board.steps["second_model"] == StepView("off", ("no rows",))
    assert board.steps["write"].lines == ("0 rows", "3 groups failed")


def test_a_failed_step_shows_where_the_story_stopped():
    progress = boards.new_progress("threats", STORY)
    for event in threat_events()[:3]:
        boards.apply_event(progress, event)
    boards.apply_event(progress, StepEvent(STORY, "write", "failed"))
    board = boards.live_board(progress)
    assert board.steps["write"] == StepView("failed", ("stopped here",))
    assert board.active is None
    assert board.title.startswith("The story stopped at this step")


def test_a_new_story_starts_with_an_empty_progress():
    progress = boards.new_progress("threats", STORY)
    for event in threat_events():
        boards.apply_event(progress, event)
    boards.apply_event(progress, StepEvent("MM-US-008", "search", "start"))
    board = boards.live_board(progress)
    assert progress["story_id"] == "MM-US-008" and not progress["finished"]
    assert board.steps["story"].lines == ("MM-US-008",)
    assert board.steps["write"].state == "waiting"


def test_a_bad_event_changes_nothing_and_raises_nothing():
    progress = boards.new_progress("threats", STORY)
    before = repr(progress)
    boards.apply_event(progress, object())
    boards.apply_event(progress, StepEvent(STORY, "no such step", "start"))
    assert repr(progress) == before
    # A value that is not a number is left out.
    boards.apply_event(progress, StepEvent(STORY, "search", "done", 0, 0, {"x": "y"}))
    assert progress["steps"]["search"] == {
        "state": "done",
        "index": 0,
        "total": 0,
        "numbers": {},
    }


def test_the_requirements_board():
    progress = boards.new_progress("requirements", STORY)
    events = [
        StepEvent(STORY, "search", "start"),
        StepEvent(STORY, "search", "done", 0, 0, {"passages": 9}),
        StepEvent(STORY, "write", "start", 1, 3),
        StepEvent(STORY, "write", "done", 1, 3, {"attempt": 1}),
        StepEvent(STORY, "write", "start", 2, 3),
    ]
    for event in events:
        boards.apply_event(progress, event)
    board = boards.live_board(progress, notes=boards.lane_notes("gemma4:26b"))
    assert board.steps["search"] == StepView("done", ("9 passages",))
    assert board.steps["write"] == StepView("active", ("attempt 2",))
    assert board.facts == "Attempt 2."
    boards.apply_event(progress, StepEvent(STORY, "write", "done", 2, 3))
    boards.apply_event(
        progress, StepEvent(STORY, "parse", "done", 0, 0, {"requirements": 5})
    )
    board = boards.live_board(progress)
    assert board.steps["write"] == StepView("done", ("2 attempts",))
    assert board.steps["parse"] == StepView("done", ("5 requirements",))
    assert board.steps["read"].state == "partial"


# ------------------------------------------------------------------ replay


def test_the_replay_shows_each_group_in_true_order():
    frames = boards.replay_frames("threats", TRACE)
    assert frames == [
        ("story", 0),
        ("search", 0),
        ("write", 1),
        ("check", 1),
        ("write", 2),
        ("check", 2),
        ("write", 3),  # no answer: no check of this group
        ("labels", 0),
        ("second_model", 0),
        ("weak_signs", 0),
        ("read", 0),
    ]
    board = boards.replay_board("threats", TRACE, ("write", 2))
    assert board.active == "write" and board.part == 2 and board.loop_used
    assert board.steps["write"] == StepView("active", ("group 2 of 3", "5 passages"))
    assert board.steps["check"] == StepView("partial", ("1 kept", "1 removed"))
    assert board.facts == "Group 2 of 3: 5 passages, 2 attempts, 4 seconds."

    board = boards.replay_board("threats", TRACE, ("check", 2))
    assert board.steps["check"] == StepView("active", ("3 kept", "1 removed"))
    assert board.steps["write"] == StepView("partial", ("2 of 3 groups",))
    assert board.facts == (
        "Group 2 of 3: 2 rows stay, 0 rows are removed. "
        "So far: 3 rows stay, 1 row is removed."
    )

    board = boards.replay_board("threats", TRACE, ("write", 3))
    assert "could not read an answer" in board.facts
    assert "risk word" in board.facts

    board = boards.replay_board("threats", TRACE, ("second_model", 0))
    assert board.steps["write"] == StepView("done", ("4 rows",))
    assert board.steps["second_model"] == StepView("active", ("2 accepted", "1 fault"))
    assert board.facts == "2 accepted, 1 with a fault."


def test_the_last_check_frame_gives_the_reasons():
    trace = dict(TRACE, groups=TRACE["groups"][:2])
    board = boards.replay_board("threats", trace, ("check", 2))
    assert board.facts.endswith(
        "In all, 3 rows stay and 1 row is removed. "
        "Reasons: the quote is not in the passage (1)."
    )


def test_the_result_board_has_every_step_done():
    board = boards.result_board("threats", TRACE, verdicts={"T1": "keep", "T2": "x"})
    assert board.active is None
    states = {key: step.state for key, step in board.steps.items()}
    assert states == {
        "story": "done",
        "search": "done",
        "write": "done",
        "check": "done",
        "labels": "done",
        "second_model": "done",
        "weak_signs": "done",
        "read": "partial",
    }
    assert board.steps["read"].lines == ("1 of 3 decided",)
    done = boards.result_board(
        "threats", TRACE, verdicts={"T1": "keep", "T2": "delete", "T3": "keep"}
    )
    assert done.steps["read"] == StepView("done", ("3 of 3 decided",))


def test_a_trace_with_options_off_and_an_empty_trace():
    trace = dict(TRACE, labels={"on": False}, second_model={"on": False})
    frames = boards.replay_frames("threats", trace)
    assert ("labels", 0) not in frames and ("second_model", 0) not in frames
    board = boards.result_board("threats", trace)
    assert board.steps["labels"] == StepView("off", ("off",))
    assert board.steps["second_model"] == StepView("off", ("off",))
    empty = boards.result_board("threats", {})
    assert empty.steps["check"] == StepView("done", ("0 kept", "0 removed"))
    assert view.render_svg(empty, moving=False).startswith("<svg")


def test_the_requirements_replay():
    trace = {"story_id": STORY, "passages": 9, "attempts": None, "requirements": 5}
    frames = boards.replay_frames("requirements", trace)
    assert [frame[0] for frame in frames] == [
        "story",
        "search",
        "write",
        "parse",
        "read",
    ]
    board = boards.replay_board("requirements", trace, ("parse", 0))
    assert board.steps["search"] == StepView("done", ("9 passages",))
    assert board.steps["parse"] == StepView("active", ("5 requirements",))
    assert board.facts == "For this story: 5 requirements."


# ------------------------------------------------------------------ the picture


def test_outside_text_is_escaped_in_the_picture():
    progress = boards.new_progress("threats", "<script>x</script>")
    notes = boards.lane_notes('g"<b>', "<i>q</i>", "http://localhost:11434/")
    svg = view.render_svg(boards.live_board(progress, notes=notes))
    assert "<script" not in svg and "<b>" not in svg and "<i>" not in svg
    assert "&lt;script&gt;" in svg
    assert "at localhost:11434" in svg


def test_the_step_in_work_pulses_and_the_slip_moves_one_time():
    board = board_after(8)  # group 2 starts
    svg = view.render_svg(board, came_from="write")
    assert svg.count("srp-pulse") >= 2  # the style rule and the ring
    assert svg.count("<animateMotion") == 2
    assert "repeatCount" not in svg
    still = view.render_svg(board, came_from="write", moving=False)
    assert "<animateMotion" not in still
    assert "prefers-reduced-motion" in view.STYLE


def test_each_state_has_its_own_look():
    steps = {
        "story": StepView("done", ("S-1",)),
        "search": StepView("failed", ("stopped here",)),
        "labels": StepView("off", ("off",)),
    }
    svg = view.render_svg(Board("threats", steps), moving=False)
    for name in ("srp-done", "srp-failed", "srp-off", "srp-waiting"):
        assert f'class="{name}"' in svg
    assert 'class="srp-active"' not in svg


def test_the_key_changes_only_when_the_picture_changes():
    first, same = board_after(8), board_after(9)  # write done changes no line
    assert first.key() == same.key()
    assert first.key() != board_after(10).key()


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_an_empty_board_draws(kind):
    board = boards.live_board(boards.new_progress(kind))
    svg = view.render_svg(board)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert board.title == "No story is in work"
