"""Tests of the threat list use case in the application layer."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import typer.testing
from secreqgen.application.errors import InputError, ModelServerError, NothingToDoError
from secreqgen.application.events import ProgressEvent
from secreqgen.application.threats import (
    ThreatListSummary,
    ThreatOptions,
    resolve_threat_models,
    run_threat_lists,
    select_stories,
    summarise_threat_list,
    threat_folder_name,
    threat_product_name,
    write_threat_files,
)
from secreqgen.config import get_config
from secreqgen.exceptions import GenerationError
from secreqgen.generation.llm_client import LLMResponse
from secreqgen.threats import ThreatListError

SECONDS_RE = re.compile(r'("seconds": )[0-9.]+')

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
LABEL_TASK = "Task: choose a threat actor and a STRIDE category"


class StandIn:
    """A fake model client. It answers each prompt with a fixed reply."""

    def __init__(self, model_name: str = "stand-in", healthy: bool = True) -> None:
        self.model_name = model_name
        self.healthy = healthy

    def check_health(self) -> bool:
        return self.healthy

    def respond(self, prompt: str) -> str:
        raise NotImplementedError

    def generate(self, prompt, system_prompt=None, **other):
        item = self.respond(prompt)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item,
            model=self.model_name,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            raw_response={},
        )


class Writer(StandIn):
    """The writer: an actor line, or an attack row and a rule row."""

    def respond(self, prompt: str) -> str:
        if prompt.startswith(LABEL_TASK):
            return "1: external_individual, E\n2. Privileged insider, I"
        sources = re.findall(r"(?m)^### \[(P\d+)\] ", prompt)
        where = {
            name: prompt.split(f"### [{name}] ", 1)[1].split("### [", 1)[0]
            for name in sources
        }
        notes = [
            {"source": name, "fits_story": "yes", "statements": []}
            for name in sources
        ]
        rows = []
        for sentence, kind, threat, part in (
            (
                WARNING,
                "attack",
                "An attacker who steals the session cookie can use it.",
                "The session ends after a time with no activity.",
            ),
            (
                RULE,
                "rule",
                "The session cookie is sent with no Secure flag.",
                "a part that the story does not have",
            ),
        ):
            name = next((n for n, t in where.items() if sentence in t), None)
            if name is not None:
                rows.append(
                    {
                        "source": name,
                        "quote": sentence,
                        "type": kind,
                        "threat": threat,
                        "harm": "access with no permission",
                        "story_part": part,
                        "requirement": "Set `session-idle-timeout`.",
                    }
                )
        return json.dumps({"passages": notes, "rows": rows})


class Checker(StandIn):
    """The checker: it accepts the row with the word attacker in its quote."""

    def respond(self, prompt: str) -> str:
        quote = prompt.split('Quote: "', 1)[1].split('"\n', 1)[0]
        if "attacker" in quote.lower():
            return "The row has no fault.\nOK"
        return "The sentence is about backups.\nFAULT"


class BrokenWriter(Writer):
    """The model name raises an error after the model calls."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    @property
    def model_name(self) -> str:
        raise self._error

    @model_name.setter
    def model_name(self, value: str) -> None:
        pass


def make_factory(made: list | None = None, writer=Writer, checker=Checker):
    """A client factory like the one of the command line."""
    made = [] if made is None else made

    def factory(cfg, tested_setup, model_name=None):
        kind = checker if made else writer
        client = kind(model_name or cfg.llm.model_name)
        made.append(client)
        return client

    return factory


def make_stories(workdir: Path, ids: tuple[str, ...] = ("US-1",)) -> None:
    story = "As an operator, I want sessions to end after idle time."
    data = {
        "project": "demo",
        "user_stories": [
            {
                "id": story_id,
                "title": "Session timeout",
                "story": story,
                "acceptance_criteria": [
                    "The session ends after a time with no activity."
                ],
            }
            for story_id in ids
        ],
    }
    (workdir / "stories.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("SECREQGEN_"):
            monkeypatch.delenv(name, raising=False)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sessions.md").write_text(PAGE, encoding="utf-8")
    make_stories(tmp_path)
    return tmp_path


def options(workdir: Path, **kwargs) -> ThreatOptions:
    return ThreatOptions(
        input_file=workdir / "stories.json", docs=workdir / "docs", **kwargs
    )


def read_tree(folder: Path) -> dict[str, str]:
    """The text of each file below the folder. The run times are removed."""
    return {
        str(path.relative_to(folder)): SECONDS_RE.sub(
            r"\1<N>", path.read_text(encoding="utf-8")
        )
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


# stages ---------------------------------------------------------------------


def test_threat_folder_name_adds_a_number():
    used: set[str] = set()
    assert threat_folder_name("A/1", used) == "A_1"
    assert threat_folder_name("A_1", used) == "A_1_2"
    assert used == {"A_1", "A_1_2"}


def test_threat_folder_name_dots():
    assert threat_folder_name("..", set()) == "story"
    assert threat_folder_name("", set()) == "story"


def test_summarise_threat_list():
    model = {
        "rows": [
            {"type": "attack", "second_model": "accepts"},
            {"type": "rule", "weak_signs": ["a"]},
            {"type": "rule"},
        ],
        "dropped": [{}],
        "calls": [{}, {}],
        "seconds": 1.5,
    }
    assert summarise_threat_list(model) == ThreatListSummary(
        rows=3,
        attacks=1,
        rules=2,
        clean=2,
        accepted=1,
        dropped=1,
        calls=2,
        seconds=1.5,
    )


def test_threat_product_name():
    assert threat_product_name("given", {"project": "other"}) == "given"
    assert threat_product_name(None, {"project": "demo"}) == "demo"
    assert threat_product_name(None, {"project": 7}) == "7"
    assert threat_product_name(None, {"project": 1.5}) == "1.5"
    assert threat_product_name(None, {"project": ["a"]}) is None
    assert threat_product_name(None, {}) is None


def test_select_stories():
    class Story:
        def __init__(self, id):
            self.id = id

    stories = [Story("A"), Story("B"), Story("A")]
    assert [s.id for s in select_stories(stories, "A")] == ["A", "A"]
    assert select_stories(stories, "C") == []


def test_resolve_threat_models_default(workdir):
    assert resolve_threat_models(get_config(), None, None, True) == (
        "gemma4:26b",
        "qwen3.8:27b",
    )
    assert resolve_threat_models(get_config(), None, None, False) == (
        "gemma4:26b",
        None,
    )


def test_resolve_threat_models_with_the_setting(workdir, monkeypatch):
    monkeypatch.setenv("SECREQGEN_LLM_MODEL_NAME", "mine:1b")
    cfg = get_config()
    assert resolve_threat_models(cfg, None, None, True)[0] == "mine:1b"
    assert resolve_threat_models(cfg, "named:2b", None, True)[0] == "named:2b"


def test_resolve_threat_models_error_passes_through(workdir):
    with pytest.raises(ThreatListError):
        resolve_threat_models(get_config(), "a:1", "a:1", True)


def test_write_threat_files(workdir):
    run = run_threat_lists(options(workdir), make_client=make_factory())
    model = run.lists[0].model
    folder = workdir / "out" / "S"
    write_threat_files(folder, model)
    assert json.loads((folder / "threats.json").read_text(encoding="utf-8")) == model
    assert (folder / "threats.md").read_text(encoding="utf-8").strip()


# run_threat_lists -----------------------------------------------------------


def test_run_matches_command_output(workdir, monkeypatch):
    import secreqgen.cli

    monkeypatch.setattr(
        "secreqgen.cli._ollama_client", make_factory()
    )
    monkeypatch.setattr(secreqgen.cli.console, "_width", 200)
    result = typer.testing.CliRunner().invoke(
        secreqgen.cli.app,
        ["generate", "threats", "stories.json", "--docs", "docs", "--output", "cli"],
    )
    assert result.exit_code == 0, result.output

    made: list = []
    run = run_threat_lists(
        options(workdir, output=workdir / "api"),
        make_client=make_factory(made),
    )
    assert [type(c) for c in made] == [Writer, Checker]
    assert run.writer == "gemma4:26b"
    assert run.checker == "qwen3.8:27b"
    assert run.failures == []
    assert run.skipped == []
    assert [o.story_id for o in run.lists] == ["US-1"]
    assert run.lists[0].folder == workdir / "api" / "US-1"
    tree = read_tree(workdir / "api")
    assert sorted(tree) == ["US-1/threats.json", "US-1/threats.md"]
    assert tree == read_tree(workdir / "cli")
    assert run.lists[0].summary.rows == len(run.lists[0].model["rows"])


def test_run_twice_gives_the_same_files(workdir):
    run_threat_lists(
        options(workdir, output=workdir / "one"), make_client=make_factory()
    )
    run_threat_lists(
        options(workdir, output=workdir / "two"), make_client=make_factory()
    )
    assert read_tree(workdir / "one") == read_tree(workdir / "two")


def test_run_writes_no_file_without_output(workdir):
    run = run_threat_lists(options(workdir), make_client=make_factory())
    assert run.lists[0].folder is None
    assert not (workdir / "threat_lists").exists()


def test_run_no_check(workdir):
    made: list = []
    run = run_threat_lists(
        options(workdir, check=False), make_client=make_factory(made)
    )
    assert run.checker is None
    assert len(made) == 1


def test_run_same_folder_name(workdir):
    make_stories(workdir, ("A/1", "A_1"))
    run = run_threat_lists(
        options(workdir, check=False, output=workdir / "out"),
        make_client=make_factory(),
    )
    assert [o.folder.name for o in run.lists] == ["A_1", "A_1_2"]


def test_run_story_id_filter(workdir):
    make_stories(workdir, ("US-1", "US-2"))
    run = run_threat_lists(
        options(workdir, check=False, story_id="US-2"), make_client=make_factory()
    )
    assert [o.story_id for o in run.lists] == ["US-2"]


def test_run_reports_progress(workdir):
    make_stories(workdir, ("US-1", "US-2"))
    events: list[ProgressEvent] = []
    run_threat_lists(
        options(workdir, check=False),
        make_client=make_factory(),
        on_progress=events.append,
    )
    assert events == [
        ProgressEvent(1, 2, "US-1", None),
        ProgressEvent(2, 2, "US-2", None),
    ]


def test_run_story_failure_goes_to_failures(workdir):
    error = ThreatListError("stand-in list error")
    events: list[ProgressEvent] = []
    run = run_threat_lists(
        options(workdir, check=False),
        make_client=make_factory(writer=lambda name: BrokenWriter(error)),
        on_progress=events.append,
    )
    assert run.lists == []
    assert [story_id for story_id, _ in run.failures] == ["US-1"]
    assert run.failures[0][1] is error
    assert events[0].error is error


def test_run_generation_error_goes_to_failures(workdir):
    error = GenerationError("stand-in failure")
    run = run_threat_lists(
        options(workdir, check=False),
        make_client=make_factory(writer=lambda name: BrokenWriter(error)),
    )
    assert run.lists == []
    assert run.failures[0][1] is error


def test_run_skipped_entries(workdir):
    data = json.loads((workdir / "stories.json").read_text(encoding="utf-8"))
    data["user_stories"].append(5)
    (workdir / "stories.json").write_text(json.dumps(data), encoding="utf-8")
    run = run_threat_lists(options(workdir, check=False), make_client=make_factory())
    assert [number for number, _ in run.skipped] == [2]
    assert len(run.lists) == 1


# errors ---------------------------------------------------------------------


def test_run_input_errors(workdir):
    with pytest.raises(InputError, match="Input file not found"):
        run_threat_lists(ThreatOptions(input_file=workdir / "no.json", docs=workdir))
    with pytest.raises(InputError, match="Documentation folder not found"):
        run_threat_lists(
            ThreatOptions(input_file=workdir / "stories.json", docs=workdir / "no")
        )
    with pytest.raises(InputError, match="Story not found in the input file: X"):
        run_threat_lists(options(workdir, story_id="X"), make_client=make_factory())


def test_run_parse_errors(workdir):
    (workdir / "bad.json").write_text("{not json", encoding="utf-8")
    (workdir / "list.json").write_text("[1]", encoding="utf-8")
    for name in ("bad.json", "list.json"):
        with pytest.raises(InputError, match="Error parsing input file"):
            run_threat_lists(
                ThreatOptions(input_file=workdir / name, docs=workdir / "docs")
            )


def test_run_nothing_to_do(workdir):
    (workdir / "empty.json").write_text('{"user_stories": []}', encoding="utf-8")
    with pytest.raises(NothingToDoError, match="No user stories found"):
        run_threat_lists(
            ThreatOptions(input_file=workdir / "empty.json", docs=workdir / "docs")
        )


def test_run_model_server_error_for_the_writer(workdir):
    def factory(cfg, tested_setup, model_name=None):
        return Writer(model_name, healthy=False)

    with pytest.raises(ModelServerError) as caught:
        run_threat_lists(options(workdir), make_client=factory)
    assert caught.value.model == "gemma4:26b"


def test_run_model_server_error_for_the_checker(workdir):
    made: list = []

    def factory(cfg, tested_setup, model_name=None):
        client = Writer(model_name, healthy=not made)
        made.append(client)
        return client

    with pytest.raises(ModelServerError) as caught:
        run_threat_lists(options(workdir), make_client=factory)
    assert caught.value.model == "qwen3.8:27b"
    assert "checker model" in str(caught.value)


def test_run_model_choice_error_passes_through(workdir):
    with pytest.raises(ThreatListError):
        run_threat_lists(
            options(workdir, writer_model="a:1", checker_model="a:1"),
            make_client=make_factory(),
        )
