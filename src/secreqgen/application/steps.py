"""Step events: the tool reports the steps inside one story.

A view (for example a GUI) can show which step of a story is in work. The
run itself does not change. A watcher puts thin proxies around the parts of
one generator object. A proxy reports a step and passes the call on. It
returns the answer of the real part unchanged and it raises the same errors.

The callback never decides anything. An error inside the callback is caught
and ignored. The code that makes a number never raises: when a count cannot
be read, the number is left out.

The two trace functions are pure. They read the steps of a finished story
from its stored result.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

THREAT_STEPS = (
    "search",
    "write",
    "check",
    "labels",
    "second_model",
    "weak_signs",
)
REQUIREMENTS_STEPS = ("search", "write", "parse")


@dataclass(frozen=True)
class StepEvent:
    """One report of one step of one story.

    step is one of THREAT_STEPS or REQUIREMENTS_STEPS. state is "start",
    "done" or "failed". index is the part in work, from 1 (a group, a row or
    an attempt); 0 means that the step has no parts. total is the number of
    parts; 0 means not known. numbers holds counts that are true at this
    moment.
    """

    story_id: str
    step: str
    state: str
    index: int = 0
    total: int = 0
    numbers: dict = field(default_factory=dict)


StepCallback = Callable[[StepEvent], None]


class _Reporter:
    """Send events to the callback. An error of the callback is ignored."""

    def __init__(self, on_step: StepCallback) -> None:
        self.on_step = on_step
        self.story_id = ""
        self.open: list[str] = []  # steps that started and are not done

    def reset(self, story_id: str) -> None:
        self.story_id = story_id
        self.open = []

    def send(
        self,
        step: str,
        state: str,
        index: int = 0,
        total: int = 0,
        **numbers: int,
    ) -> None:
        if state == "start":
            if step in self.open:
                self.open.remove(step)
            self.open.append(step)
        elif state == "done" and step in self.open:
            self.open.remove(step)
        try:
            self.on_step(
                StepEvent(self.story_id, step, state, index, total, dict(numbers))
            )
        except Exception:  # the run never depends on the view  # noqa: BLE001
            pass

    def failed(self) -> None:
        step = self.open[-1] if self.open else "search"
        self.send(step, "failed")


class _Proxy:
    """Pass every other attribute to the real object."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _story_id(user_story: Any) -> str:
    try:
        return str(user_story.id)
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------- threats


class _ThreatState:
    """What the threat watcher knows about the story in work."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.groups: list = []
        self.last_prompt: str | None = None
        self.group = 0
        self.attempt = 0
        self.written = 0
        self.final_check = False
        self.checks_total = 0
        self.accepted = 0
        self.faults = 0
        self.no_verdict = 0


class _WriterProxy(_Proxy):
    def __init__(self, real: Any, reporter: _Reporter, state: _ThreatState) -> None:
        super().__init__(real)
        self._reporter = reporter
        self._state = state

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("format") != "json":
            return self._real.generate(*args, **kwargs)  # the label call
        reporter, state = self._reporter, self._state
        prompt = kwargs.get("prompt", args[0] if args else None)
        if state.group == 0 or prompt != state.last_prompt:
            state.group += 1
            state.attempt = 1
        else:
            state.attempt += 1
        state.last_prompt = prompt
        numbers: dict[str, int] = {"attempt": state.attempt}
        try:
            numbers["passages"] = len(state.groups[state.group - 1])
        except Exception:  # noqa: BLE001
            pass
        total = len(state.groups)
        reporter.send("write", "start", state.group, total, **numbers)
        try:
            response = self._real.generate(*args, **kwargs)
        except Exception:
            reporter.send(
                "write",
                "done",
                state.group,
                total,
                attempt=state.attempt,
                answered=0,
                rows_written=0,
            )
            raise
        answered, rows_written = 0, 0
        try:
            from secreqgen.threats.checks import extract_json

            candidate = extract_json(response.content)
            if isinstance(candidate.get("rows"), list):
                answered = 1
                rows_written = sum(1 for r in candidate["rows"] if isinstance(r, dict))
        except Exception:  # noqa: BLE001
            answered, rows_written = 0, 0
        state.written += rows_written
        reporter.send(
            "write",
            "done",
            state.group,
            total,
            attempt=state.attempt,
            answered=answered,
            rows_written=rows_written,
        )
        if answered:
            reporter.send("check", "done", state.group, total)
        return response


class _CheckerProxy(_Proxy):
    def __init__(self, real: Any, reporter: _Reporter, state: _ThreatState) -> None:
        super().__init__(real)
        self._reporter = reporter
        self._state = state
        self._calls = 0

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        state, reporter = self._state, self._reporter
        self._calls += 1
        index = self._calls
        reporter.send("second_model", "start", index, state.checks_total)
        verdict = ""
        try:
            response = self._real.generate(*args, **kwargs)
        except Exception:
            state.no_verdict += 1
            self._done(index)
            raise
        try:
            from secreqgen.threats.checks import keep_or_drop

            verdict = keep_or_drop(response.content)
        except Exception:  # noqa: BLE001
            verdict = ""
        if verdict == "accepts":
            state.accepted += 1
        elif verdict == "refuses":
            state.faults += 1
        else:
            state.no_verdict += 1
        self._done(index)
        return response

    def reset_calls(self) -> None:
        self._calls = 0

    def _done(self, index: int) -> None:
        state = self._state
        self._reporter.send(
            "second_model",
            "done",
            index,
            state.checks_total,
            accepted=state.accepted,
            faults=state.faults,
            no_verdict=state.no_verdict,
        )


class _ThreatWatcher:
    """The object that watch_threat_generator returns."""

    def __init__(self, generator: Any, on_step: StepCallback) -> None:
        self._generator = generator
        self._reporter = _Reporter(on_step)
        self._state = _ThreatState()
        self._checker: _CheckerProxy | None = None
        generator.llm = _WriterProxy(generator.llm, self._reporter, self._state)
        if getattr(generator, "checker", None) is not None:
            self._checker = _CheckerProxy(
                generator.checker, self._reporter, self._state
            )
            generator.checker = self._checker
        for name in ("passage_groups", "_name_labels", "_check_rows"):
            setattr(generator, name, getattr(self, "_wrap_" + name)(
                getattr(generator, name)
            ))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._generator, name)

    # -- the final "check done" ------------------------------------------------
    def _final_check(self, kept: int, removed: int) -> None:
        state = self._state
        if state.final_check:
            return
        state.final_check = True
        total = len(state.groups)
        self._reporter.send(
            "check", "done", total, total, kept=kept, removed=max(removed, 0)
        )

    def _wrap_passage_groups(self, original: Callable) -> Callable:
        def passage_groups(*args: Any, **kwargs: Any) -> Any:
            reporter, state = self._reporter, self._state
            reporter.send("search", "start")
            result = original(*args, **kwargs)
            numbers: dict[str, int] = {}
            try:
                groups, extra = result
                state.groups = list(groups)
                numbers = {
                    "passages": sum(len(g) for g in groups),
                    "groups": len(groups),
                    "extra_passages": int(extra),
                }
            except Exception:  # noqa: BLE001
                numbers = {}
            reporter.send("search", "done", **numbers)
            return result

        return passage_groups

    def _wrap__name_labels(self, original: Callable) -> Callable:
        def name_labels(user_story: Any, rows: list, *args: Any, **kwargs: Any) -> Any:
            reporter, state = self._reporter, self._state
            n_rows = _length(rows)
            self._final_check(n_rows, state.written - n_rows)
            reporter.send("labels", "start", rows=n_rows)
            result = original(user_story, rows, *args, **kwargs)
            labelled = _count(rows, lambda r: r.get("actor") or r.get("stride"))
            reporter.send("labels", "done", rows=n_rows, labelled=labelled)
            return result

        return name_labels

    def _wrap__check_rows(self, original: Callable) -> Callable:
        def check_rows(user_story: Any, rows: list, *args: Any, **kwargs: Any) -> Any:
            reporter, state = self._reporter, self._state
            n_rows = _length(rows)
            self._final_check(n_rows, state.written - n_rows)
            state.checks_total = n_rows
            result = original(user_story, rows, *args, **kwargs)
            reporter.send(
                "second_model",
                "done",
                n_rows,
                n_rows,
                accepted=_count(rows, lambda r: r.get("second_model") == "accepts"),
                faults=_count(rows, lambda r: r.get("second_model") == "refuses"),
                no_verdict=_count(
                    rows,
                    lambda r: r.get("second_model") not in ("accepts", "refuses"),
                ),
            )
            return result

        return check_rows

    # -- the story ---------------------------------------------------------------
    def generate(self, user_story: Any) -> Any:
        reporter, state = self._reporter, self._state
        reporter.reset(_story_id(user_story))
        state.reset()
        if self._checker is not None:
            self._checker.reset_calls()
        try:
            result = self._generator.generate(user_story)
        except Exception:
            reporter.failed()
            raise
        try:
            rows = result.get("rows") or []
            dropped = result.get("dropped") or []
            self._final_check(len(rows), len(dropped))
            with_sign = _count(rows, lambda r: r.get("weak_signs"))
            reporter.send(
                "weak_signs",
                "done",
                with_sign=with_sign,
                clean=len(rows) - with_sign,
            )
        except Exception:  # noqa: BLE001
            pass
        return result


def _length(items: Any) -> int:
    try:
        return len(items)
    except Exception:  # noqa: BLE001
        return 0


def _count(items: Any, test: Callable[[dict], Any]) -> int:
    try:
        return sum(1 for item in items if test(item))
    except Exception:  # noqa: BLE001
        return 0


def watch_threat_generator(generator: Any, on_step: StepCallback) -> Any:
    """Return a watcher of a ThreatListGenerator.

    Its generate(user_story) gives the result of the real generate, unchanged.
    It reports the steps of THREAT_STEPS to on_step, in the order of the code.
    """
    return _ThreatWatcher(generator, on_step)


# ---------------------------------------------------------------- requirements


class _RetrieverProxy(_Proxy):
    def __init__(self, real: Any, reporter: _Reporter) -> None:
        super().__init__(real)
        self._reporter = reporter

    def retrieve_for_user_story(self, *args: Any, **kwargs: Any) -> Any:
        self._reporter.send("search", "start")
        result = self._real.retrieve_for_user_story(*args, **kwargs)
        numbers: dict[str, int] = {}
        try:
            numbers = {"passages": int(result.total_retrieved)}
        except Exception:  # noqa: BLE001
            try:
                numbers = {"passages": len(result.chunks)}
            except Exception:  # noqa: BLE001
                numbers = {}
        self._reporter.send("search", "done", **numbers)
        return result


class _ModelProxy(_Proxy):
    def __init__(self, real: Any, reporter: _Reporter, total: int) -> None:
        super().__init__(real)
        self._reporter = reporter
        self._total = total
        self.attempt = 0

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        self.attempt += 1
        self._reporter.send("write", "start", self.attempt, self._total)
        response = self._real.generate(*args, **kwargs)
        self._reporter.send(
            "write", "done", self.attempt, self._total, attempt=self.attempt
        )
        return response


class _RequirementsWatcher:
    """The object that watch_requirements_generator returns."""

    def __init__(self, generator: Any, on_step: StepCallback) -> None:
        self._generator = generator
        self._reporter = _Reporter(on_step)
        if getattr(generator, "retriever", None) is not None:
            generator.retriever = _RetrieverProxy(generator.retriever, self._reporter)
        total = getattr(generator, "max_retries", 0)
        self._model = _ModelProxy(
            generator.llm_client,
            self._reporter,
            total if isinstance(total, int) else 0,
        )
        generator.llm_client = self._model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._generator, name)

    def generate(self, **kwargs: Any) -> Any:
        self._reporter.reset(_story_id(kwargs.get("user_story")))
        self._model.attempt = 0
        try:
            result = self._generator.generate(**kwargs)
        except Exception:
            self._reporter.failed()
            raise
        numbers: dict[str, int] = {}
        try:
            numbers = {"requirements": len(result.requirements)}
        except Exception:  # noqa: BLE001
            numbers = {}
        self._reporter.send("parse", "done", **numbers)
        return result


def watch_requirements_generator(generator: Any, on_step: StepCallback) -> Any:
    """Return a watcher of a SecurityRequirementsGenerator.

    Its generate(**kwargs) gives the result of the real generate, unchanged.
    It reports the steps of REQUIREMENTS_STEPS to on_step.
    """
    return _RequirementsWatcher(generator, on_step)


# ---------------------------------------------------------------------- traces

_REASON_TEXTS = (
    ("unknown source name", "the source name is unknown"),
    ("the note of", "the passage does not fit the story"),
    ("the quote has", "the quote is too long"),
)


def _int(value: Any) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001
        return 0


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _reason(why: Any) -> str:
    text = why if isinstance(why, str) else ""
    for start, fixed in _REASON_TEXTS:
        if text.startswith(start):
            return fixed
    return text


def threat_trace(model: dict) -> dict:
    """Describe the steps of a finished threat list, from its stored dict.

    The input is the dict of threats.json. The result is JSON-safe. A damaged
    input gives zeros, empty lists or None. It never raises.
    """
    try:
        return _threat_trace(model if isinstance(model, dict) else {})
    except Exception:  # noqa: BLE001
        return _threat_trace({})


def _threat_trace(model: dict) -> dict:
    story = model.get("story") if isinstance(model.get("story"), dict) else {}
    passages = _dicts(model.get("passages"))
    rows = _dicts(model.get("rows"))
    dropped = _dicts(model.get("dropped"))
    calls = _dicts(model.get("calls"))
    extra = _int(model.get("extra_passages"))
    group_of = {p.get("ref"): _int(p.get("group")) for p in passages}
    kept_by: Counter = Counter(group_of.get(r.get("source"), 0) for r in rows)
    removed_by: Counter = Counter(_int(d.get("group")) for d in dropped)
    n_groups = len(calls)
    groups = []
    for position, call in enumerate(calls, start=1):
        number = _int(call.get("group")) or position
        groups.append(
            {
                "group": number,
                "passages": _int(call.get("passages")),
                "attempts": _int(call.get("attempts")),
                "answered": bool(call.get("ok")),
                "seconds": _number(call.get("seconds")),
                "kept": kept_by.get(number, 0),
                "removed": removed_by.get(number, 0),
                "extra": extra > 0 and position == n_groups,
            }
        )
    reasons = Counter(_reason(d.get("why")) for d in dropped)
    labelled = _count(rows, lambda r: r.get("actor") or r.get("stride"))
    checker = model.get("checker")
    with_sign = _count(rows, lambda r: r.get("weak_signs"))
    writer = model.get("model")
    return {
        "story_id": str(story.get("id") or ""),
        "seconds": _number(model.get("seconds")),
        "writer": writer if isinstance(writer, str) else None,
        "search": {
            "passages": len(passages),
            "groups": n_groups,
            "extra_passages": extra,
        },
        "groups": groups,
        "check": {
            "kept": len(rows),
            "removed": len(dropped),
            "reasons": [[text, n] for text, n in reasons.most_common()],
        },
        "labels": {
            "on": bool(model.get("label_step")),
            "rows": len(rows),
            "labelled": labelled,
        },
        "second_model": {
            "on": bool(checker),
            "model": checker if isinstance(checker, str) else None,
            "accepted": _count(rows, lambda r: r.get("second_model") == "accepts"),
            "faults": _count(rows, lambda r: r.get("second_model") == "refuses"),
            "no_verdict": _count(
                rows, lambda r: r.get("second_model") not in ("accepts", "refuses")
            ),
        },
        "weak_signs": {"with_sign": with_sign, "clean": len(rows) - with_sign},
    }


def _number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return value


def requirements_trace(result: dict) -> dict:
    """Describe the steps of one stored requirements result.

    A value that the stored result does not hold is None. It never raises.
    """
    try:
        data = result if isinstance(result, dict) else {}
        story = data.get("user_story")
        story = story if isinstance(story, dict) else {}
        passages = data.get("passages")
        requirements = data.get("requirements")
        return {
            "story_id": story.get("id") if isinstance(story.get("id"), str) else None,
            "passages": len(passages) if isinstance(passages, list) else None,
            "attempts": _optional_int(data.get("attempts")),
            "requirements": (
                len(requirements) if isinstance(requirements, list) else None
            ),
            "seconds": _optional_number(data.get("seconds")),
        }
    except Exception:  # noqa: BLE001
        return {
            "story_id": None,
            "passages": None,
            "attempts": None,
            "requirements": None,
            "seconds": None,
        }


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value
