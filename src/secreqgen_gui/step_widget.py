"""The step diagram on a page: the picture and the sentences below it."""

from __future__ import annotations

from nicegui import ui

from secreqgen_gui.pipeline_view import Board, render_svg

SAME_STORY = object()  # the slip starts at the step of the last picture


class StepDiagram:
    """Show a board. Call show() again when the board can have changed.

    The picture is drawn again only when the board changed. So the slip
    moves one time for each change, and a page can call show() on a timer.
    """

    def __init__(self) -> None:
        self._key: tuple | None = None
        self._story = ""
        self._active: str | None = None
        with ui.column().classes("w-full gap-0 sr-steps"):
            # The SVG text is built by render_svg from fixed strings and
            # escaped values. No text from outside is in it unescaped.
            self.picture = ui.html("", sanitize=False).classes("w-full")
            self.title = ui.label().classes("sr-step-title")
            self.facts = ui.label().classes("sr-step-facts")
            self.text = ui.label().classes("sr-step-text")

    def show(
        self,
        board: Board,
        *,
        story_id: str = "",
        came_from: object = SAME_STORY,
        moving: bool = True,
    ) -> bool:
        """Draw the board when it changed. Return True when it was drawn.

        came_from is the step that the slip starts at. The default is the
        step in work of the last picture of the same story. None means: the
        slip moves along the one connector into the step in work.
        """
        key = (story_id, board.key(), board.title, board.facts)
        if key == self._key:
            return False
        if came_from is SAME_STORY:
            came_from = self._active if story_id == self._story else None
        self.picture.set_content(
            render_svg(board, came_from=came_from, moving=moving)
        )
        self.title.set_text(board.title)
        self.facts.set_text(board.facts)
        self.facts.set_visibility(bool(board.facts))
        self.text.set_text(board.text)
        self.text.set_visibility(bool(board.text))
        self._key, self._story, self._active = key, story_id, board.active
        return True
