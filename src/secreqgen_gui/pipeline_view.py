"""The step diagram: the parts of the tool as lanes, the steps of a story as boxes.

The diagram is one SVG picture. It is made by pure functions:

- A flow is the fixed layout: lanes, boxes, connectors and texts.
- A progress dict holds what the tool reported about the story in work.
- A board is the state of each box at one moment, with its short number lines.
- render_svg draws a board.

Rules of the diagram:

- A box changes only when the tool reported a step, or when a finished story
  is shown again from its stored result. The diagram never shows a number
  that it did not get.
- Every value from outside (story id, model name) goes through html.escape.
  No model output and no documentation text goes into the picture.
- One slip of paper moves one time into the step that starts. The step in
  work has a slow pulse. With "reduce motion" nothing moves.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass, field

from secreqgen_gui.theme import COLORS, FONT_TOOL

# Geometry, in units of the picture (1 unit is 1 px at full width).
LABEL_W = 136  # the column of the lane names
PITCH = 128  # from one box column to the next
BOX_W = 112
BOX_H = 76
LANE_H = 96
PAD = 6  # free space above the first lane and below the last lane
BOX_DX = 8
BOX_DY = 10
CORNER = 8  # radius of a connector corner
ENTRY_SHIFT = 16  # two connectors that enter one box keep this distance
SLIP_SECONDS = 0.9  # one slip on one connector
SLIP_SECONDS_MOST = 2.8  # all slips of one change together

STATES = ("waiting", "active", "partial", "done", "off", "failed")


@dataclass(frozen=True)
class Step:
    """One box of a flow."""

    key: str
    lane: int
    col: int
    title: tuple[str, str]
    name: str  # the step in words, for the line under the diagram


@dataclass(frozen=True)
class Flow:
    """The fixed layout of a diagram."""

    kind: str
    lanes: tuple[tuple[str, str], ...]  # (name, key of the small text below it)
    steps: tuple[Step, ...]
    loop: tuple[str, str] | None = None  # first and last step of the loop
    loop_note: tuple[str, str] = ("", "")

    def step(self, key: str) -> Step:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(key)

    def index(self, key: str) -> int:
        return [step.key for step in self.steps].index(key)

    @property
    def width(self) -> int:
        columns = max(step.col for step in self.steps) + 1
        return LABEL_W + columns * PITCH + BOX_DX

    @property
    def height(self) -> int:
        return 2 * PAD + len(self.lanes) * LANE_H


THREAT_FLOW = Flow(
    kind="threats",
    lanes=(
        ("You", "reader"),
        ("SecReqGen", "code"),
        ("Writer", "writer"),
        ("Checker", "checker"),
    ),
    steps=(
        Step("story", 0, 0, ("Story and", "documentation"), "your input"),
        Step("search", 1, 1, ("Search the", "documentation"), "the search"),
        Step("write", 2, 2, ("Write threats", "with quotes"), "the writer"),
        Step("check", 1, 3, ("Check the", "quotes"), "the quote check"),
        Step("labels", 2, 4, ("Add actor", "and STRIDE"), "the labels"),
        Step("second_model", 3, 5, ("Accept", "or flag"), "the checker"),
        Step("weak_signs", 1, 6, ("Mark weak", "support"), "the weak support check"),
        Step("read", 0, 7, ("Read, keep", "or delete"), "your review"),
    ),
    loop=("write", "check"),
    loop_note=("Repeats for each", "group of passages"),
)

REQUIREMENTS_FLOW = Flow(
    kind="requirements",
    lanes=(("You", "reader"), ("SecReqGen", "code"), ("Model", "writer")),
    steps=(
        Step("story", 0, 0, ("Story and", "documentation"), "your input"),
        Step("search", 1, 1, ("Search the", "documentation"), "the search"),
        Step("write", 2, 2, ("Write the", "requirements"), "the model"),
        Step("parse", 1, 3, ("Parse the", "answer"), "the parser"),
        Step("read", 0, 4, ("Read the", "requirements"), "your review"),
    ),
)

FLOWS = {"threats": THREAT_FLOW, "requirements": REQUIREMENTS_FLOW}

LANE_NOTES = {"code": "plain code, no AI"}


@dataclass(frozen=True)
class StepView:
    """The state of one box and its short lines (at most two)."""

    state: str = "waiting"
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Board:
    """The state of a diagram at one moment."""

    kind: str
    steps: Mapping[str, StepView]
    active: str | None = None  # the step in work
    part: int = 0  # the group in work, for the loop
    loop_used: bool = False  # the connector that goes back was used
    title: str = ""  # the step in words
    text: str = ""  # plain sentences about the step
    facts: str = ""  # one sentence with the numbers of this story
    details: str = ""  # more about the step, for a person who asks
    lane_notes: Mapping[str, str] = field(default_factory=dict)

    def key(self) -> tuple:
        """Return a value that changes when the picture must be drawn again."""
        steps = tuple(
            (name, view.state, view.lines) for name, view in self.steps.items()
        )
        return (self.kind, steps, self.active, self.part, self.loop_used)


def plural(count: int, word: str, many: str | None = None) -> str:
    if count == 1:
        return f"1 {word}"
    return f"{count} {many or word + 's'}"


def short(text: str, most: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[: most - 1] + "…"


# ---------------------------------------------------------------- geometry


def _box(step: Step) -> tuple[int, int]:
    """Return the left and the top of the box of a step."""
    return (
        LABEL_W + step.col * PITCH + BOX_DX,
        PAD + step.lane * LANE_H + BOX_DY,
    )


def _elbow(x0: float, y0: float, x1: float, y1: float) -> str:
    """Return a path: level from (x0, y0), then up or down to (x1, y1)."""
    side = 1 if x1 > x0 else -1
    turn = 1 if y1 > y0 else -1
    return (
        f"M{x0:g} {y0:g} H{x1 - side * CORNER:g} "
        f"Q{x1:g} {y0:g} {x1:g} {y0 + turn * CORNER:g} V{y1:g}"
    )


@dataclass(frozen=True)
class Connector:
    """A line from one box into the next box."""

    source: str
    target: str
    path: str
    tip: tuple[float, float]  # the point of the arrow head
    down: bool  # the arrow head points down
    back: bool = False  # the connector of the loop that goes back


def connectors(flow: Flow) -> list[Connector]:
    """Return the connectors of a flow, in the order of the steps."""
    result: list[Connector] = []
    first = flow.loop[0] if flow.loop else None
    for source, target in zip(flow.steps, flow.steps[1:], strict=False):
        sx, sy = _box(source)
        tx, ty = _box(target)
        x1 = tx + BOX_W / 2
        if target.key == first:
            x1 -= ENTRY_SHIFT
        down = target.lane > source.lane
        y1 = ty - 1 if down else ty + BOX_H + 1
        path = _elbow(sx + BOX_W, sy + BOX_H / 2, x1, y1 - 5 if down else y1 + 5)
        result.append(Connector(source.key, target.key, path, (x1, y1), down))
    if flow.loop:
        source, target = flow.step(flow.loop[1]), flow.step(flow.loop[0])
        sx, sy = _box(source)
        tx, ty = _box(target)
        x1 = tx + BOX_W / 2 + ENTRY_SHIFT
        y1 = ty - 1
        path = _elbow(sx, sy + BOX_H / 2, x1, y1 - 5)
        result.append(
            Connector(source.key, target.key, path, (x1, y1), True, back=True)
        )
    return result


def slip_chain(flow: Flow, came_from: str | None, board: Board) -> list[Connector]:
    """Return the connectors that a slip moves along, for one change.

    came_from is the step that was in work in the last picture. None means
    that there was no picture of this story: the slip moves along the one
    connector into the step in work.
    """
    target = board.active
    if target is None or target == flow.steps[0].key:
        return []
    lines = connectors(flow)
    forward = {c.target: c for c in lines if not c.back}
    back = next((c for c in lines if c.back), None)
    if came_from is None:
        return [forward[target]]
    if flow.loop and back is not None and target == flow.loop[0]:
        if came_from == flow.loop[0]:
            return [forward[flow.loop[1]], back] if board.part > 1 else []
        if came_from == flow.loop[1]:
            return [back]
    start, end = flow.index(came_from), flow.index(target)
    if start >= end:
        return []
    return [forward[step.key] for step in flow.steps[start + 1 : end + 1]]


# ---------------------------------------------------------------- drawing

_INK, _SLATE, _LINE = COLORS["ink"], COLORS["slate"], COLORS["line"]
_BLUE, _MIST, _PAPER = COLORS["ink_blue"], COLORS["mist"], COLORS["paper"]

STYLE = f"""
.srp {{ font-family: {FONT_TOOL}; display: block; width: 100%; height: auto; }}
.srp text {{ fill: {_INK}; }}
.srp .srp-lane-name {{ font-size: 13px; font-weight: 600; }}
.srp .srp-lane-note {{ font-size: 12px; fill: {_SLATE}; }}
.srp .srp-title {{ font-size: 13px; font-weight: 600; }}
.srp .srp-num {{ font-size: 12px; }}
.srp .srp-note {{ font-size: 12px; fill: {_SLATE}; }}
.srp .srp-box {{ fill: {_PAPER}; stroke: {_LINE}; stroke-width: 1; }}
.srp .srp-waiting .srp-title {{ fill: {_SLATE}; font-weight: 500; }}
.srp .srp-done .srp-box {{ stroke: {_INK}; }}
.srp .srp-active .srp-box {{ stroke: {_BLUE}; stroke-width: 2; }}
.srp .srp-active .srp-num {{ fill: {_BLUE}; font-weight: 600; }}
.srp .srp-partial .srp-box {{ stroke: {_BLUE}; stroke-width: 1.25; }}
.srp .srp-partial .srp-num {{ fill: {_BLUE}; }}
.srp .srp-off .srp-box {{ fill: none; stroke-dasharray: 4 3; }}
.srp .srp-off .srp-title, .srp .srp-off .srp-num {{ fill: {_SLATE}; font-weight: 400; }}
.srp .srp-failed .srp-box {{ stroke: {COLORS["delete"]}; stroke-width: 2; }}
.srp .srp-failed .srp-num {{ fill: {COLORS["delete"]}; font-weight: 600; }}
.srp .srp-line {{ fill: none; stroke: {_LINE}; stroke-width: 1.5; }}
.srp .srp-head {{ fill: {_LINE}; }}
.srp .srp-line-used {{ stroke: {_BLUE}; }}
.srp .srp-head-used {{ fill: {_BLUE}; }}
.srp .srp-pulse {{
  fill: none; stroke: {_BLUE}; stroke-width: 2; opacity: 0;
  animation: srp-pulse 2.4s ease-in-out infinite;
}}
@keyframes srp-pulse {{
  0% {{ opacity: 0; }} 45% {{ opacity: 0.38; }} 100% {{ opacity: 0; }}
}}
@media (prefers-reduced-motion: reduce) {{
  .srp .srp-pulse {{ animation: none; opacity: 0.3; }}
  .srp .srp-slip {{ display: none; }}
}}
"""


def _text(x: float, y: float, text: str, css: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x:g}" y="{y:g}" class="{css}" text-anchor="{anchor}">'
        f"{html.escape(str(text))}</text>"
    )


def _lanes(flow: Flow, notes: Mapping[str, str]) -> list[str]:
    parts = []
    for number, (name, note_key) in enumerate(flow.lanes):
        top = PAD + number * LANE_H
        if note_key in ("writer", "checker"):
            parts.append(
                f'<rect x="0" y="{top}" width="{flow.width}" height="{LANE_H}" '
                f'fill="{_MIST}"/>'
            )
        if number:
            parts.append(
                f'<line x1="0" y1="{top}" x2="{flow.width}" y2="{top}" '
                f'stroke="{_LINE}" stroke-width="1"/>'
            )
        middle = top + LANE_H / 2
        parts.append(_text(12, middle - 3, name, "srp-lane-name"))
        note = notes.get(note_key) or LANE_NOTES.get(note_key, "")
        if note:
            parts.append(_text(12, middle + 14, short(note, 19), "srp-lane-note"))
    return parts


def _loop_note(flow: Flow) -> list[str]:
    """Return the dashed frame of the loop and its two lines of text."""
    if not flow.loop:
        return []
    first, last = flow.step(flow.loop[0]), flow.step(flow.loop[1])
    fx, fy = _box(first)
    lx, ly = _box(last)
    left, right = min(fx, lx) - 6, max(fx, lx) + BOX_W + 6
    top, bottom = min(fy, ly) - 6, max(fy, ly) + BOX_H + 6
    parts = [
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" '
        f'rx="6" fill="none" stroke="{_SLATE}" stroke-width="1" '
        f'stroke-dasharray="2 4" opacity="0.7"/>'
    ]
    for number, line in enumerate(flow.loop_note):
        parts.append(_text(left + 8, top + 17 + number * 15, line, "srp-note"))
    return parts


def _server_note(flow: Flow, notes: Mapping[str, str]) -> list[str]:
    """Return one line at the right end of the last lane: where Ollama runs."""
    server = notes.get("server")
    if not server:
        return []
    x = flow.width - 12
    bottom = PAD + len(flow.lanes) * LANE_H
    return [
        _text(x, bottom - 10, "Ollama at " + short(server, 34), "srp-note", "end"),
    ]


def _connector(line: Connector, used: bool) -> str:
    x, y = line.tip
    if line.down:
        head = f"{x - 4:g},{y - 7:g} {x + 4:g},{y - 7:g} {x:g},{y:g}"
    else:
        head = f"{x - 4:g},{y + 7:g} {x + 4:g},{y + 7:g} {x:g},{y:g}"
    more = " srp-line-used" if used else ""
    more_head = " srp-head-used" if used else ""
    return (
        f'<path d="{line.path}" class="srp-line{more}"/>'
        f'<polygon points="{head}" class="srp-head{more_head}"/>'
    )


def _mark(x: float, y: float, state: str) -> str:
    """Return the small mark in the corner of a box: a tick or a cross."""
    if state == "done":
        return (
            f'<path d="M{x - 17:g} {y + 13:g} l3.2 3.4 l6 -7" fill="none" '
            f'stroke="{COLORS["keep"]}" stroke-width="1.8" stroke-linecap="round" '
            'stroke-linejoin="round"/>'
        )
    if state == "failed":
        return (
            f'<path d="M{x - 16:g} {y + 9:g} l7 7 m0 -7 l-7 7" fill="none" '
            f'stroke="{COLORS["delete"]}" stroke-width="1.8" stroke-linecap="round"/>'
        )
    return ""


def _step_box(step: Step, view: StepView) -> str:
    x, y = _box(step)
    state = view.state if view.state in STATES else "waiting"
    parts = [f'<g class="srp-{state}">']
    if state == "active":
        parts.append(
            f'<rect x="{x - 4}" y="{y - 4}" width="{BOX_W + 8}" height="{BOX_H + 8}" '
            'rx="7" class="srp-pulse"/>'
        )
    parts.append(
        f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="4" '
        'class="srp-box"/>'
    )
    parts.append(_text(x + 9, y + 20, step.title[0], "srp-title"))
    parts.append(_text(x + 9, y + 36, step.title[1], "srp-title"))
    for number, line in enumerate(view.lines[:2]):
        parts.append(_text(x + 9, y + 54 + number * 15, short(line, 15), "srp-num"))
    parts.append(_mark(x + BOX_W, y, state))
    parts.append("</g>")
    return "".join(parts)


def _slips(flow: Flow, chain: list[Connector]) -> list[str]:
    if not chain:
        return []
    seconds = min(SLIP_SECONDS, SLIP_SECONDS_MOST / len(chain))
    keys = [step.key for step in flow.steps]
    rows_exist_from = keys.index("write") if "write" in keys else len(keys)
    parts = []
    for number, line in enumerate(chain):
        begin = number * seconds
        with_quote = not line.back and keys.index(line.source) >= rows_exist_from
        stripe = (
            f'<rect x="-4" y="-2" width="8" height="4" fill="{COLORS["marker"]}"/>'
            if with_quote
            else ""
        )
        parts.append(
            '<g class="srp-slip" opacity="0">'
            f'<rect x="-6" y="-8" width="12" height="16" rx="1.5" fill="{_PAPER}" '
            f'stroke="{_INK}" stroke-width="1"/>{stripe}'
            f'<animateMotion dur="{seconds:g}s" begin="{begin:g}s" fill="freeze" '
            f'calcMode="spline" keyTimes="0;1" keySplines="0.4 0 0.2 1" '
            f'path="{line.path}"/>'
            f'<animate attributeName="opacity" dur="{seconds:g}s" begin="{begin:g}s" '
            'values="0;1;1;0" keyTimes="0;0.12;0.86;1" fill="freeze"/>'
            "</g>"
        )
    return parts


def _used(line: Connector, flow: Flow, board: Board) -> bool:
    """True when the story passed along this connector."""
    if line.back:
        return board.loop_used
    view = board.steps.get(line.target, StepView())
    if view.state in ("active", "partial", "done", "failed"):
        return True
    if view.state == "off":
        # The flow passed a step that is off when a later step was reached.
        later = flow.steps[flow.index(line.target) + 1 :]
        return any(
            board.steps.get(step.key, StepView()).state
            in ("active", "partial", "done", "failed")
            for step in later
        )
    return False


def describe(board: Board) -> str:
    """Return one sentence that says what the picture shows."""
    flow = FLOWS[board.kind]
    what = "a threat list" if board.kind == "threats" else "requirements"
    if board.active is None:
        return f"The steps of {what} for one story."
    name = flow.step(board.active).name
    return f"The steps of {what} for one story. Now: {name}."


def render_svg(
    board: Board, *, came_from: str | None = None, moving: bool = True
) -> str:
    """Return the SVG text of a board.

    With moving, a slip moves along the connectors from the step came_from
    to the step in work (see slip_chain). Without moving, no slip is drawn.
    """
    flow = FLOWS[board.kind]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="srp" role="img" '
        f'viewBox="0 0 {flow.width} {flow.height}" '
        f'style="max-width:{flow.width}px" '
        f'aria-label="{html.escape(describe(board), quote=True)}">',
        f"<style>{STYLE}</style>",
    ]
    parts += _lanes(flow, board.lane_notes)
    parts += _loop_note(flow)
    parts += _server_note(flow, board.lane_notes)
    parts += [_connector(line, _used(line, flow, board)) for line in connectors(flow)]
    parts += [
        _step_box(step, board.steps.get(step.key, StepView())) for step in flow.steps
    ]
    if moving:
        parts += _slips(flow, slip_chain(flow, came_from, board))
    parts.append("</svg>")
    return "".join(parts)
