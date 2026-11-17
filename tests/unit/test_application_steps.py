"""Tests of the step events: the watchers and the traces. No model server."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from secreqgen.application.requirements import RequirementsOptions, run_requirements
from secreqgen.application.steps import (
    StepEvent,
    requirements_trace,
    threat_trace,
    watch_requirements_generator,
    watch_threat_generator,
)
from secreqgen.application.threats import ThreatOptions, run_threat_lists
from secreqgen.generation.llm_client import LLMResponse
from secreqgen.retrieval.word_search import SearchError
from secreqgen.threats import ThreatListGenerator

WARNING = (
    "An attacker who steals the session cookie can use the session until the "
    "idle timeout ends it."
)
RULE = "The session cookie must have the Secure flag."
PAGE = (
    "# Sessions\n\n## Idle timeout\n\n"
    "Set `session-idle-timeout` to end a session after a time with no activity.\n\n"
    f"{WARNING}\n\n## Cookies\n\n{RULE}\n"
)
PAGE_TWO = (
    "# Tokens\n\n## Lifetime\n\n"
    "A token must expire. Set `token-lifetime` for the session token.\n"
)
LABEL_TASK = "Task: choose a threat actor and a STRIDE category"
STORY = SimpleNamespace(
    id="US-1",
    title="Session timeout",
    story="As an operator, I want sessions to end after idle time.",
    acceptance_criteria=["The session ends after a time with no activity."],
)
REQ_ANSWER = json.dumps(
    {
        "passage_notes": [{"passage": 1, "about": "idle timeout", "fits_story": "yes"}],
        "requirements": [
            {
                "id": "SR-001",
                "title": "Idle timeout",
                "description": "Set session-idle-timeout.",
                "asvs_category": "V3",
                "priority": "high",
                "rationale": "Passage 1 states the setting.",
                "acceptance_criteria": ["The session ends."],
            }
        ],
    }
)


class StandIn:
    """A fake model client."""

    def __init__(self, model_name: str = "stand-in") -> None:
        self.model_name = model_name
        self.calls = 0

    def check_health(self) -> bool:
        return True

    def respond(self, prompt: str) -> str:
        raise NotImplementedError

    def generate(self, prompt, system_prompt=None, **other):
        self.calls += 1
        item = self.respond(prompt)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item,
            model=self.model_name,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
            raw_response={},
        )


class Writer(StandIn):
    """One good row, one row with a quote that is not in the passage."""

    bad_first = 0  # the first answers that are not JSON

    def respond(self, prompt: str) -> str:
        if prompt.startswith(LABEL_TASK):
            return "1: external_individual, E\n2. Privileged insider, I"
        if self.bad_first > 0:
            self.bad_first -= 1
            return "this is no JSON"
        sources = re.findall(r"(?m)^### \[(P\d+)\] ", prompt)
        notes = [{"source": n, "fits_story": "yes"} for n in sources]
        rows = []
        for name in sources:
            body = prompt.split(f"### [{name}] ", 1)[1].split("### [", 1)[0]
            if WARNING in body and not rows:
                rows.append(self.row(name, WARNING, "attack"))
                rows.append(self.row(name, "The quote is not here at all.", "rule"))
        return json.dumps({"passages": notes, "rows": rows})

    @staticmethod
    def row(source: str, quote: str, kind: str) -> dict:
        return {
            "source": source,
            "quote": quote,
            "type": kind,
            "threat": "An attacker who steals the cookie can use it.",
            "harm": "access with no permission",
            "story_part": "sessions to end",
            "requirement": "Set `session-idle-timeout`.",
        }


class Checker(StandIn):
    def respond(self, prompt: str) -> str:
        return "No fault.\nOK"


@pytest.fixture
def docs(tmp_path) -> Path:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "sessions.md").write_text(PAGE, encoding="utf-8")
    (folder / "tokens.md").write_text(PAGE_TWO, encoding="utf-8")
    return folder


def run_story(docs, *, watch: bool, writer=None, checker="default", labels=True):
    """Run one story. Return (result, events)."""
    writer = writer or Writer()
    checker = Checker("checker") if checker == "default" else checker
    generator = ThreatListGenerator(
        writer, docs, product="demo", name_labels=labels, checker=checker
    )
    events: list[StepEvent] = []
    if watch:
        generator = watch_threat_generator(generator, events.append)
    return generator.generate(STORY), events


def without_time(model: dict) -> dict:
    data = json.loads(json.dumps(model))
    data.pop("seconds", None)
    for call in data.get("calls", []):
        call.pop("seconds", None)
    return data


def names(events, step=None):
    return [(e.step, e.state) for e in events if step is None or e.step == step]


# 1 ---------------------------------------------------------------------------


def test_threat_events_in_order_with_true_numbers(docs):
    result, events = run_story(docs, watch=True)
    order = [(e.step, e.state) for e in events]
    assert order[0] == ("search", "start")
    assert order[1] == ("search", "done")
    assert order[2] == ("write", "start")
    assert order[-1] == ("weak_signs", "done")
    first = {step: order.index(item) for item in order for step in [item]}
    assert first[("write", "start")] < first[("check", "done")]
    assert first[("check", "done")] < first[("labels", "start")]
    assert first[("labels", "done")] < first[("second_model", "start")]
    assert first[("second_model", "done")] < first[("weak_signs", "done")]

    search = events[1].numbers
    assert search["passages"] == len(result["passages"])
    assert search["extra_passages"] == result["extra_passages"]
    assert search["groups"] == len({p["group"] for p in result["passages"]})

    final = [e for e in events if e.step == "check" and "kept" in e.numbers]
    assert len(final) == 1
    assert final[0].numbers == {
        "kept": len(result["rows"]),
        "removed": len(result["dropped"]),
    }
    assert final[0].numbers["kept"] == 1
    assert final[0].numbers["removed"] == 1

    labels = [e for e in events if e.step == "labels"]
    assert labels[0].numbers == {"rows": 1}
    assert labels[1].numbers["rows"] == 1

    second = [e for e in events if e.step == "second_model"]
    assert second[0].state == "start" and second[0].index == 1
    assert second[-1].numbers == {"accepted": 1, "faults": 0, "no_verdict": 0}
    assert second[-1].index == second[-1].total == 1

    last = events[-1]
    assert last.numbers["with_sign"] + last.numbers["clean"] == len(result["rows"])
    assert all(e.story_id == "US-1" for e in events)


def test_write_events_hold_attempt_and_rows(docs):
    _, events = run_story(docs, watch=True)
    starts = [e for e in events if (e.step, e.state) == ("write", "start")]
    dones = [e for e in events if (e.step, e.state) == ("write", "done")]
    assert starts[0].numbers["attempt"] == 1
    assert starts[0].numbers["passages"] >= 1
    assert starts[0].index == 1 and starts[0].total >= 1
    assert dones[0].numbers["answered"] == 1
    assert dones[0].numbers["rows_written"] in (0, 2)


# 2 ---------------------------------------------------------------------------


def test_result_is_the_same_with_and_without_a_watcher(docs):
    plain, _ = run_story(docs, watch=False)
    watched, events = run_story(docs, watch=True)
    assert events
    assert without_time(watched) == without_time(plain)


# 3 ---------------------------------------------------------------------------


def test_second_attempt_of_the_same_group(docs):
    writer = Writer()
    writer.bad_first = 1
    result, events = run_story(docs, watch=True, writer=writer)
    dones = [e for e in events if (e.step, e.state) == ("write", "done")]
    seen = [(e.index, e.numbers["attempt"], e.numbers["answered"]) for e in dones]
    assert seen[:2] == [(1, 1, 0), (1, 2, 1)]
    assert dones[0].numbers["rows_written"] == 0
    assert result["calls"][0]["attempts"] == 2
    # the check of the group is sent after the answered attempt only
    checks = [e for e in events if e.step == "check" and "kept" not in e.numbers]
    assert checks[0].index == 1


# 4 ---------------------------------------------------------------------------


def test_a_callback_that_raises_changes_nothing(docs):
    plain, _ = run_story(docs, watch=False)
    generator = ThreatListGenerator(
        Writer(), docs, product="demo", name_labels=True, checker=Checker("checker")
    )

    def broken(event):
        raise RuntimeError("the view is broken")

    watched = watch_threat_generator(generator, broken).generate(STORY)
    assert without_time(watched) == without_time(plain)


# 5 ---------------------------------------------------------------------------


def test_failed_event_and_same_error(docs):
    generator = ThreatListGenerator(Writer(), docs, product="demo", checker=None)

    def broken(*args, **kwargs):
        raise SearchError("no documentation")

    generator.passage_groups = broken
    events: list[StepEvent] = []
    watched = watch_threat_generator(generator, events.append)
    with pytest.raises(SearchError, match="no documentation"):
        watched.generate(STORY)
    assert [(e.step, e.state) for e in events] == [
        ("search", "start"),
        ("search", "failed"),
    ]
    assert [e.state for e in events].count("failed") == 1


def test_failed_event_names_the_step_in_work(docs):
    writer = Writer()
    generator = ThreatListGenerator(writer, docs, product="demo", checker=None)

    def broken(*args, **kwargs):
        raise ValueError("boom")

    generator._name_labels = broken
    events: list[StepEvent] = []
    with pytest.raises(ValueError, match="boom"):
        watch_threat_generator(generator, events.append).generate(STORY)
    assert events[-1].state == "failed"
    assert events[-1].story_id == "US-1"


# 6 ---------------------------------------------------------------------------


def test_no_labels_and_no_checker(docs):
    result, events = run_story(docs, watch=True, checker=None, labels=False)
    steps = {e.step for e in events}
    assert "labels" not in steps
    assert "second_model" not in steps
    final = [e for e in events if e.step == "check" and "kept" in e.numbers]
    assert len(final) == 1
    assert final[0].numbers["kept"] == len(result["rows"])
    assert final[0].numbers["removed"] == len(result["dropped"])
    assert events[-1].step == "weak_signs"


# 7 ---------------------------------------------------------------------------


def hand_made() -> dict:
    return {
        "story": {"id": "S-9"},
        "seconds": 50,
        "model": "writer:1",
        "checker": "check:2",
        "label_step": True,
        "extra_passages": 1,
        "calls": [
            {"group": 1, "passages": 5, "attempts": 1, "ok": True, "seconds": 41.2},
            {"group": 2, "passages": 2, "attempts": 3, "ok": False, "seconds": 9.0},
        ],
        "passages": [
            {"ref": "P1", "group": 1},
            {"ref": "P2", "group": 1},
            {"ref": "P6", "group": 2},
        ],
        "rows": [
            {"source": "P1", "actor": "a", "stride": "",
             "second_model": "accepts", "weak_signs": []},
            {"source": "P2", "actor": "", "stride": "E",
             "second_model": "refuses", "weak_signs": ["x"]},
            {"source": "P6", "actor": "", "stride": "",
             "second_model": "", "weak_signs": []},
        ],
        "dropped": [
            {"group": 1, "why": "the quote is not in the passage"},
            {"group": 1, "why": "the quote is not in the passage"},
            {"group": 2, "why": "unknown source name 'P9'"},
            {"group": 2, "why": "the note of P6 says fits_story 'no'"},
            {"group": 2, "why": "the quote has 70 words"},
        ],
    }


def test_threat_trace():
    trace = threat_trace(hand_made())
    json.dumps(trace)
    assert trace["story_id"] == "S-9"
    assert trace["writer"] == "writer:1"
    assert trace["search"] == {"passages": 3, "groups": 2, "extra_passages": 1}
    assert trace["groups"] == [
        {"group": 1, "passages": 5, "attempts": 1, "answered": True, "seconds": 41.2,
         "kept": 2, "removed": 2, "extra": False},
        {"group": 2, "passages": 2, "attempts": 3, "answered": False, "seconds": 9.0,
         "kept": 1, "removed": 3, "extra": True},
    ]
    assert trace["check"]["kept"] == 3 and trace["check"]["removed"] == 5
    assert trace["check"]["reasons"][0] == ["the quote is not in the passage", 2]
    texts = {t for t, _ in trace["check"]["reasons"]}
    assert {
        "the source name is unknown",
        "the passage does not fit the story",
        "the quote is too long",
    } <= texts
    assert trace["labels"] == {"on": True, "rows": 3, "labelled": 2}
    assert trace["second_model"] == {
        "on": True, "model": "check:2", "accepted": 1, "faults": 1, "no_verdict": 1,
    }
    assert trace["weak_signs"] == {"with_sign": 1, "clean": 2}


def test_threat_trace_of_damaged_input():
    for bad in ({}, None, {"calls": "x", "rows": 5, "dropped": [1], "passages": None}):
        trace = threat_trace(bad)
        json.dumps(trace)
        assert trace["groups"] == []
        assert trace["search"] == {"passages": 0, "groups": 0, "extra_passages": 0}
        assert trace["writer"] is None
        assert trace["second_model"]["on"] is False
        assert trace["check"] == {"kept": 0, "removed": 0, "reasons": []}


def test_trace_of_a_real_result(docs):
    result, _ = run_story(docs, watch=False)
    trace = threat_trace(result)
    assert trace["check"]["kept"] == len(result["rows"])
    assert trace["search"]["passages"] == len(result["passages"])
    assert sum(g["kept"] for g in trace["groups"]) == len(result["rows"])


def test_requirements_trace():
    stored = {
        "user_story": {"id": "R-1"},
        "passages": [{"n": 1}, {"n": 2}],
        "requirements": [{}, {}, {}],
    }
    assert requirements_trace(stored) == {
        "story_id": "R-1",
        "passages": 2,
        "attempts": None,
        "requirements": 3,
        "seconds": None,
    }
    empty = requirements_trace({})
    assert set(empty.values()) == {None}
    assert set(requirements_trace(None).values()) == {None}


# 8 ---------------------------------------------------------------------------


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("SECREQGEN_"):
            monkeypatch.delenv(name, raising=False)
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "sessions.md").write_text(PAGE, encoding="utf-8")
    data = {
        "project": "demo",
        "user_stories": [
            {
                "id": "US-1",
                "title": STORY.title,
                "story": STORY.story,
                "acceptance_criteria": STORY.acceptance_criteria,
            }
        ],
    }
    (tmp_path / "stories.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


class ReqClient(StandIn):
    def respond(self, prompt: str) -> str:
        return REQ_ANSWER


def req_options(workdir: Path) -> RequirementsOptions:
    return RequirementsOptions(
        input_file=workdir / "stories.json",
        docs=workdir / "docs",
        output_format="json",
    )


def req_factory(client):
    def factory(cfg, tested_setup, model_name=None):
        return client

    return factory


def test_requirements_events_and_same_result(workdir):
    events: list[StepEvent] = []
    plain = run_requirements(
        req_options(workdir), make_client=req_factory(ReqClient())
    )
    watched = run_requirements(
        req_options(workdir),
        make_client=req_factory(ReqClient()),
        on_step=events.append,
    )
    assert [(e.step, e.state) for e in events] == [
        ("search", "start"),
        ("search", "done"),
        ("write", "start"),
        ("write", "done"),
        ("parse", "done"),
    ]
    assert events[1].numbers["passages"] >= 1
    assert events[2].index == 1 and events[2].total == 3
    assert events[-1].numbers == {"requirements": 1}
    assert all(e.story_id == "US-1" for e in events)
    assert [r.requirements for r in watched.results] == [
        r.requirements for r in plain.results
    ]
    assert [r.retrieval_context for r in watched.results] == [
        r.retrieval_context for r in plain.results
    ]


def test_requirements_failed_event(workdir):
    class Raising(ReqClient):
        def respond(self, prompt: str):
            return RuntimeError("model gone")

    events: list[StepEvent] = []
    run = run_requirements(
        RequirementsOptions(
            input_file=workdir / "stories.json",
            docs=workdir / "docs",
            output_format="json",
            keep_finished=True,
        ),
        make_client=req_factory(Raising()),
        on_step=events.append,
    )
    assert events[-1].state == "failed" and events[-1].step == "write"
    assert run.aborted is not None


def test_watch_requirements_generator_passes_attributes():
    inner = SimpleNamespace(
        llm_client=SimpleNamespace(model_name="m"),
        retriever=None,
        max_retries=3,
        other="kept",
        generate=lambda **kw: SimpleNamespace(requirements=[1, 2]),
    )
    events: list[StepEvent] = []
    watched = watch_requirements_generator(inner, events.append)
    assert watched.other == "kept"
    assert inner.llm_client.model_name == "m"
    result = watched.generate(user_story=STORY)
    assert len(result.requirements) == 2
    assert events[-1].numbers == {"requirements": 2}


# 9 ---------------------------------------------------------------------------


def test_threat_run_with_on_step(workdir):
    made: list = []

    def factory(cfg, tested_setup, model_name=None):
        client = Checker("checker") if made else Writer(model_name or "w")
        made.append(client)
        return client

    events: list[StepEvent] = []
    options = ThreatOptions(
        input_file=workdir / "stories.json",
        docs=workdir / "docs",
        check=True,
        writer_model="a:1",
        checker_model="b:2",
    )
    run = run_threat_lists(options, make_client=factory, on_step=events.append)
    assert len(run.lists) == 1
    assert events[0].step == "search" and events[-1].step == "weak_signs"
    assert run.lists[0].model["rows"]
