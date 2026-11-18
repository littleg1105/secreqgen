"""Tests of the system context use case. No model and no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml
from secreqgen.application.errors import InputError, ModelServerError
from secreqgen.application.steps import StepEvent
from secreqgen.application.system_context import (
    CONTEXT_STEPS,
    ContextOptions,
    ContextRun,
    resolve_context_model,
    run_system_context,
    write_context_file,
)
from secreqgen.context import ContextError
from secreqgen.threats import TESTED_WRITER


def good_answer() -> dict:
    return {
        "name": "Shop",
        "description": "A web shop for small stores.",
        "tech_stack": [
            {"kind": "database", "value": "PostgreSQL 15", "source": "F2"},
        ],
        "security_profile": "Medium - holds orders",
        "features": [
            {
                "group": "Orders",
                "items": [{"text": "Orders and payments", "source": "F5"}],
            }
        ],
        "data_sensitivity": {
            "critical": [{"text": "Passwords", "source": "F2"}],
            "high": [],
            "medium": [],
        },
    }


class Stand:
    def __init__(self, answers, healthy=True, model_name="stand-in"):
        self.answers = list(answers)
        self.healthy = healthy
        self.model_name = model_name

    def check_health(self):
        return self.healthy

    def generate(self, prompt, system_prompt=None, format=None):
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


def factory(client, seen=None):
    def make(cfg, tested_setup, model_name=None):
        if seen is not None:
            seen.append((tested_setup, model_name))
        return client

    return make


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "shop"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(
        "# Shop\nA web shop for small stores. Uses PostgreSQL.\n", encoding="utf-8"
    )
    (root / "package.json").write_text(
        '{"name": "shop", "dependencies": {"express": "4"}}'
    )
    (root / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:15\n"
    )
    (root / "docs" / "index.md").write_text("Orders and payments overview.\n")
    return root


def cfg(model_set=None):
    fields = set() if model_set is None else {"model_name"}
    llm = SimpleNamespace(
        model_name=model_set or "default-model", model_fields_set=fields
    )
    return SimpleNamespace(llm=llm)


# 1 ---------------------------------------------------------------------------


def test_run_and_output_file(codebase, tmp_path):
    out = tmp_path / "made" / "shop.yaml"
    seen = []
    run = run_system_context(
        ContextOptions(codebase, product="Shop", output=out),
        cfg=cfg(),
        make_client=factory(Stand([json.dumps(good_answer())]), seen),
    )
    assert isinstance(run, ContextRun)
    assert run.writer == TESTED_WRITER and run.output == out
    assert seen == [(True, TESTED_WRITER)]
    assert run.result["context"]["name"] == "Shop"
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded == yaml.safe_load(run.result["yaml"])


def test_no_output_writes_no_file(codebase, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    run = run_system_context(
        ContextOptions(codebase),
        cfg=cfg(),
        make_client=factory(Stand([json.dumps(good_answer())])),
    )
    assert run.output is None
    assert set(tmp_path.rglob("*")) == before


def test_write_context_file(tmp_path):
    path = tmp_path / "a" / "b.yaml"
    write_context_file(path, {"yaml": "name: Café\n"})
    assert path.read_bytes() == "name: Café\n".encode()


# 2 ---------------------------------------------------------------------------


def test_step_events_in_order(codebase):
    events: list[StepEvent] = []
    run_system_context(
        ContextOptions(codebase),
        cfg=cfg(),
        make_client=factory(Stand([json.dumps(good_answer())])),
        on_step=events.append,
    )
    assert [(e.step, e.state) for e in events] == [
        ("scan", "start"),
        ("scan", "done"),
        ("write", "start"),
        ("write", "done"),
        ("check", "done"),
    ]
    assert {e.step for e in events} <= set(CONTEXT_STEPS)
    assert all(e.story_id == "shop" for e in events)
    assert events[2].index == 1
    assert events[1].numbers["parts"] >= 1


def test_a_callback_that_raises_does_not_stop_the_work(codebase):
    def broken(event):
        raise RuntimeError("the view is broken")

    run = run_system_context(
        ContextOptions(codebase),
        cfg=cfg(),
        make_client=factory(Stand([json.dumps(good_answer())])),
        on_step=broken,
    )
    assert run.result["context"]["name"] == "Shop"


# 3 ---------------------------------------------------------------------------


def test_unhealthy_client(codebase):
    with pytest.raises(ModelServerError, match="does not have the model"):
        run_system_context(
            ContextOptions(codebase, writer_model="m:1"),
            cfg=cfg(),
            make_client=factory(Stand([], healthy=False)),
        )


# 4 ---------------------------------------------------------------------------


def test_folder_errors(tmp_path):
    client = factory(Stand([]))
    with pytest.raises(InputError, match="Codebase folder not found"):
        run_system_context(
            ContextOptions(tmp_path / "missing"), cfg=cfg(), make_client=client
        )
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InputError, match="no README"):
        run_system_context(ContextOptions(empty), cfg=cfg(), make_client=client)


def test_model_answer_errors_pass_through(codebase):
    with pytest.raises(ContextError, match="no answer that can be read"):
        run_system_context(
            ContextOptions(codebase),
            cfg=cfg(),
            make_client=factory(Stand(["a", "b", "c"])),
        )


# 5 ---------------------------------------------------------------------------


def test_model_rule():
    assert resolve_context_model(cfg("set-model"), "option:1") == "option:1"
    assert resolve_context_model(cfg("set-model"), None) == "set-model"
    assert resolve_context_model(cfg(), None) == TESTED_WRITER
    with pytest.raises(InputError):
        resolve_context_model(cfg(), "  ")


def test_context_lines():
    from secreqgen.application.system_context import context_lines

    result = {
        "context": {
            "tech_stack": [{"kind": "language", "value": "Go", "path": "go.mod"}],
            "features": [
                {"group": "Messages", "items": [{"text": "Send", "path": "README.md"}]}
            ],
            "data_sensitivity": {"critical": [{"text": "Tokens", "path": "README.md"}]},
            "security_profile": "High - keeps tokens",
        }
    }
    lines = context_lines(result)
    assert [line["where"] for line in lines] == [
        "tech_stack",
        "features: Messages",
        "data_sensitivity: critical",
        "security_profile",
    ]
    assert lines[0] == {"where": "tech_stack", "text": "language: Go", "path": "go.mod"}
    assert lines[-1]["path"] == "Chosen by the model, not taken from a file."
    assert context_lines({}) == []
    assert context_lines({"context": "x"}) == []


def test_the_number_of_tries_is_the_number_of_the_writer():
    from secreqgen.application.system_context import CONTEXT_TRIES
    from secreqgen.context.pipeline import MAX_JSON_ATTEMPTS

    assert CONTEXT_TRIES == MAX_JSON_ATTEMPTS
