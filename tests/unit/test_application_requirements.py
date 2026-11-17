"""Tests of the requirements use case in the application layer."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import typer.testing
from secreqgen.application.errors import (
    ApplicationError,
    InputError,
    ModelServerError,
    NothingToDoError,
)
from secreqgen.application.events import ProgressEvent
from secreqgen.application.inputs import (
    build_user_stories,
    check_docs_folder,
    check_docs_option,
    check_input_file,
    read_story_data,
)
from secreqgen.application.requirements import (
    RequirementsOptions,
    run_requirements,
)
from secreqgen.exceptions import GenerationError, SecReqGenError
from secreqgen.generation.llm_client import LLMResponse

TIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
SECONDS_RE = re.compile(r'("[a-z_]*(?:seconds|time_ms|latency_ms)": )[0-9.]+')

PAGE = (
    "# Sessions\n\n## Idle timeout\n\n"
    "Set `session-idle-timeout` to end a session after a time with no activity.\n"
)
ANSWER = json.dumps(
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
    def __init__(self, healthy: bool = True, script: tuple = (ANSWER,)) -> None:
        self.healthy = healthy
        self.script = script
        self.calls = 0
        self.model_name = "stand-in"

    def check_health(self) -> bool:
        return self.healthy

    def generate(self, prompt, system_prompt=None, **other):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item,
            model="stand-in",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            raw_response={},
        )


def factory_for(client):
    def factory(cfg, tested_setup):
        return client

    return factory


def normalise(text: str) -> str:
    return SECONDS_RE.sub(r"\1<N>", TIME_RE.sub("<TIME>", text))


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sessions.md").write_text(PAGE, encoding="utf-8")
    stories = {
        "project": "demo",
        "user_stories": [
            {"id": "US-001", "title": "Log in", "story": "As a user I log in."}
        ],
    }
    (tmp_path / "stories.json").write_text(json.dumps(stories), encoding="utf-8")
    return tmp_path


# checks ---------------------------------------------------------------------


def test_error_classes():
    assert issubclass(ApplicationError, SecReqGenError)
    assert issubclass(NothingToDoError, InputError)
    error = ModelServerError("down", model="m")
    assert error.model == "m"
    assert str(error) == "down"


def test_check_messages(tmp_path):
    with pytest.raises(InputError, match="Input file not found: nope.json"):
        check_input_file(Path("nope.json"))
    with pytest.raises(InputError, match="Documentation folder not found: nodocs"):
        check_docs_folder(Path("nodocs"))
    check_input_file(tmp_path)
    check_docs_folder(tmp_path)


def test_check_docs_option(tmp_path):
    check_docs_option(None, True)
    check_docs_option(tmp_path, False)
    with pytest.raises(InputError, match="--baseline cannot be used with --docs."):
        check_docs_option(tmp_path, True)
    with pytest.raises(InputError, match="Documentation folder not found"):
        check_docs_option(tmp_path / "nodocs", True)


# readers --------------------------------------------------------------------


def test_read_story_data_json_and_yaml(tmp_path):
    (tmp_path / "a.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "a.YML").write_text("a: 1\n", encoding="utf-8")
    assert read_story_data(tmp_path / "a.json") == {"a": 1}
    assert read_story_data(tmp_path / "a.yaml") == {"a": 1}
    assert read_story_data(tmp_path / "a.YML") == {"a": 1}


def test_read_story_data_encoding(tmp_path):
    path = tmp_path / "a.json"
    path.write_bytes('{"a": "é"}'.encode("latin-1"))
    assert read_story_data(path, encoding="latin-1") == {"a": "é"}


def test_read_story_data_require_object(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert read_story_data(path) == [1, 2]
    with pytest.raises(ValueError, match="the file must hold an object"):
        read_story_data(path, require_object=True)


def test_read_story_data_errors_pass_through(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_story_data(path)
    with pytest.raises(FileNotFoundError):
        read_story_data(tmp_path / "none.json")


def test_build_user_stories():
    stories, skipped = build_user_stories(
        [{"id": "A", "title": "T", "story": "S"}, 5, {"story": "S2"}]
    )
    assert [s.id for s in stories] == ["A", "US-003"]
    assert stories[1].title == "Untitled"
    assert len(skipped) == 1
    assert skipped[0][0] == 2
    assert isinstance(skipped[0][1], Exception)


# run_requirements -----------------------------------------------------------


def options(workdir, **kwargs):
    return RequirementsOptions(
        input_file=workdir / "stories.json",
        docs=workdir / "docs",
        output_format="json",
        **kwargs,
    )


def test_run_matches_command_output(workdir, monkeypatch):
    import secreqgen.cli

    client = StandIn()
    monkeypatch.setattr(
        "secreqgen.cli._ollama_client", lambda cfg, tested_setup: client
    )
    assert secreqgen.cli._ollama_client is not None
    result = typer.testing.CliRunner().invoke(
        secreqgen.cli.app,
        [
            "generate",
            "requirements",
            "stories.json",
            "--docs",
            "docs",
            "--format",
            "json",
            "--output",
            "out.json",
        ],
    )
    assert result.exit_code == 0, result.output
    written = Path("out.json").read_text(encoding="utf-8")

    run = run_requirements(
        options(workdir), make_client=factory_for(StandIn())
    )
    assert run.text is not None
    assert normalise(run.text) == normalise(written)
    assert len(run.results) == 1
    assert run.failures == []
    assert run.skipped == []


def test_run_twice_gives_the_same_text(workdir):
    first = run_requirements(options(workdir), make_client=factory_for(StandIn()))
    second = run_requirements(options(workdir), make_client=factory_for(StandIn()))
    assert normalise(first.text) == normalise(second.text)


def test_run_reports_progress(workdir):
    events: list[ProgressEvent] = []
    run_requirements(
        options(workdir),
        make_client=factory_for(StandIn()),
        on_progress=events.append,
    )
    assert events == [ProgressEvent(1, 1, "US-001", None)]


def test_run_input_errors(workdir):
    with pytest.raises(InputError, match="Input file not found"):
        run_requirements(RequirementsOptions(input_file=workdir / "no.json"))
    with pytest.raises(InputError, match="Documentation folder not found"):
        run_requirements(
            RequirementsOptions(
                input_file=workdir / "stories.json", docs=workdir / "nodocs"
            )
        )
    with pytest.raises(InputError, match="--baseline cannot be used"):
        run_requirements(options(workdir, baseline=True))
    with pytest.raises(InputError, match="Invalid format: bad"):
        run_requirements(
            RequirementsOptions(
                input_file=workdir / "stories.json", output_format="bad"
            )
        )


def test_run_parse_error(workdir):
    (workdir / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError, match="Error parsing input file"):
        run_requirements(RequirementsOptions(input_file=workdir / "bad.json"))
    (workdir / "list.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(InputError, match="Error parsing input file"):
        run_requirements(RequirementsOptions(input_file=workdir / "list.json"))


def test_run_nothing_to_do(workdir):
    (workdir / "empty.json").write_text('{"user_stories": []}', encoding="utf-8")
    with pytest.raises(NothingToDoError, match="No user stories found"):
        run_requirements(RequirementsOptions(input_file=workdir / "empty.json"))


def test_run_model_server_error(workdir):
    with pytest.raises(ModelServerError) as caught:
        run_requirements(
            options(workdir), make_client=factory_for(StandIn(healthy=False))
        )
    assert caught.value.model == "stand-in"
    assert str(caught.value) == "Cannot connect to Ollama."


def test_run_all_stories_fail(workdir):
    client = StandIn(script=(GenerationError("stand-in failure"),))
    events: list[ProgressEvent] = []
    run = run_requirements(
        options(workdir), make_client=factory_for(client), on_progress=events.append
    )
    assert run.results == []
    assert run.text is None
    assert [story_id for story_id, _ in run.failures] == ["US-001"]
    assert events[0].error is not None
