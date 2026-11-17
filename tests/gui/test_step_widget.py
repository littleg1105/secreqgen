"""The step diagram on a page."""

from __future__ import annotations

import pytest

pytest.importorskip("nicegui")

from nicegui.testing import User  # noqa: E402
from secreqgen.application.steps import StepEvent  # noqa: E402
from secreqgen_gui import pipeline_boards as boards  # noqa: E402
from secreqgen_gui.step_widget import StepDiagram  # noqa: E402

pytestmark = pytest.mark.anyio

STORY = "S-1"


async def test_the_diagram_draws_only_when_the_board_changed(user: User) -> None:
    await user.open("/")
    with user.client.content:
        diagram = StepDiagram()
        progress = boards.new_progress("threats", STORY)
        boards.apply_event(progress, StepEvent(STORY, "search", "start"))
        assert diagram.show(boards.live_board(progress), story_id=STORY)
        first = diagram.picture.content
        assert first.startswith("<svg") and "<animateMotion" in first
        # The same board again: no new picture, so the slip does not move again.
        assert not diagram.show(boards.live_board(progress), story_id=STORY)
        assert diagram.picture.content is first
    await user.should_see("The tool code searches the documentation")

    numbers = {"passages": 4, "groups": 1, "extra_passages": 0}
    boards.apply_event(progress, StepEvent(STORY, "search", "done", 0, 0, numbers))
    boards.apply_event(
        progress, StepEvent(STORY, "write", "start", 1, 1, {"attempt": 1})
    )
    with user.client.content:
        assert diagram.show(boards.live_board(progress), story_id=STORY)
        assert "4 passages" in diagram.picture.content
    await user.should_see("The writer model writes rows")
    await user.should_see("Group 1 of 1.")
    # The button and the hint of the full screen are parts of the diagram.
    await user.should_see("Full screen")
    await user.should_see("Esc: leave the full screen.")


async def test_without_moving_the_picture_has_no_slip(user: User) -> None:
    await user.open("/")
    board = boards.plain_board("requirements", ("write", 0))
    with user.client.content:
        diagram = StepDiagram()
        assert diagram.show(board, moving=False)
        assert "<animateMotion" not in diagram.picture.content
    await user.should_see("The model writes the requirements")
