"""Tests of the run function options for a GUI (strict input, stop, keep)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from secreqgen.application.errors import InputError, ModelServerError, NothingToDoError
from secreqgen.application.events import ProgressEvent
from secreqgen.application.inputs import (
    check_about,
    check_story_upload,
    read_story_file,
)
from secreqgen.application.requirements import (
    RequirementsOptions,
    generate_stories_stoppable,
    run_requirements,
)
from secreqgen.application.threats import ThreatOptions, run_threat_lists
from secreqgen.exceptions import LLMConnectionError
from secreqgen.generation.llm_client import LLMResponse

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
    """A fake model client. It gives a fixed answer, or raises an error."""

    def __init__(self, model_name: str = "stand-in", answer=ANSWER) -> None:
        self.model_name = model_name
        self.answer = answer
        self.healthy = True
        self.health_calls = 0
        self.after_calls: list = []

    def check_health(self) -> bool:
        self.health_calls += 1
        return self.healthy

    def generate(self, prompt, system_prompt=None, **other):
        item = self.answer
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


def write_stories(folder: Path, ids=("US-1",)) -> None:
    data = {
        "project": "demo",
        "user_stories": [
            {
                "id": story_id,
                "title": "Session timeout",
                "story": "As an operator, I want sessions to end after idle time.",
                "acceptance_criteria": ["The session ends."],
            }
            for story_id in ids
        ],
    }
    (folder / "stories.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("SECREQGEN_"):
            monkeypatch.delenv(name, raising=False)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sessions.md").write_text(PAGE, encoding="utf-8")
    write_stories(tmp_path)
    return tmp_path


def factory_with(clients: list, made: list | None = None):
    """A client factory like the one of the command line. It takes model_name."""

    def factory(cfg, tested_setup, model_name=None):
        client = clients[len(made) if made is not None else 0]
        if made is not None:
            made.append(model_name)
        return client

    return factory


def threat_options(workdir: Path, **kwargs) -> ThreatOptions:
    kwargs.setdefault("check", False)
    return ThreatOptions(
        input_file=workdir / "stories.json", docs=workdir / "docs", **kwargs
    )


def requirements_options(workdir: Path, **kwargs) -> RequirementsOptions:
    return RequirementsOptions(
        input_file=workdir / "stories.json",
        docs=workdir / "docs",
        output_format="json",
        **kwargs,
    )


def one_client_factory(client):
    def factory(cfg, tested_setup, model_name=None):
        return client

    return factory


def requirements_factory(client):
    """The factory of the existing tests: it has no model_name argument."""

    def factory(cfg, tested_setup):
        return client

    return factory


# input helpers ---------------------------------------------------------------


def test_read_story_file(workdir):
    data = json.loads((workdir / "stories.json").read_text(encoding="utf-8"))
    data["user_stories"].append(5)
    (workdir / "stories.json").write_text(json.dumps(data), encoding="utf-8")
    story_file = read_story_file(workdir / "stories.json")
    assert story_file.project == "demo"
    assert [s.id for s in story_file.stories] == ["US-1"]
    assert story_file.skipped[0][0] == 2
    assert isinstance(story_file.skipped[0][1], str)


def test_read_story_file_errors(workdir):
    with pytest.raises(InputError, match="Input file not found"):
        read_story_file(workdir / "none.json")
    (workdir / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError, match="Error parsing input file"):
        read_story_file(workdir / "bad.json")
    (workdir / "list.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(InputError, match="Error parsing input file"):
        read_story_file(workdir / "list.json")
    (workdir / "empty.json").write_text('{"user_stories": []}', encoding="utf-8")
    with pytest.raises(NothingToDoError):
        read_story_file(workdir / "empty.json")


def test_read_story_file_yaml_and_project_number(workdir):
    (workdir / "s.yaml").write_text(
        "project: 7\nuser_stories:\n  - id: A\n    story: text\n", encoding="utf-8"
    )
    story_file = read_story_file(workdir / "s.yaml")
    assert story_file.project == "7"
    assert story_file.stories[0].title == "Untitled"


def test_check_about():
    assert check_about(None) is None
    assert check_about("   \n ") is None
    assert check_about("a  b\n c") == "a b c"
    assert check_about(" ".join(["w"] * 120)) is not None
    with pytest.raises(InputError, match="121 words"):
        check_about(" ".join(["w"] * 121))


def test_check_story_upload():
    check_story_upload("a.json", 10)
    check_story_upload("A.YML", 10)
    check_story_upload("a.yaml", 1_000_000)
    with pytest.raises(InputError, match=r"\.json, \.yaml or \.yml"):
        check_story_upload("a.txt", 10)
    with pytest.raises(InputError, match=r"\.json, \.yaml or \.yml"):
        check_story_upload("noextension", 10)
    with pytest.raises(InputError, match="too large"):
        check_story_upload("a.json", 1_000_001)
    with pytest.raises(InputError, match="too large"):
        check_story_upload("a.json", 11, max_bytes=10)


def test_progress_event_outcome_is_optional():
    assert ProgressEvent(1, 2, "A").outcome is None
    assert ProgressEvent(1, 2, "A", None) == ProgressEvent(1, 2, "A", None, "x")


# threat lists: strict input --------------------------------------------------


def test_threat_model_error_is_raw_without_strict(workdir):
    from secreqgen.threats import ThreatListError

    options = threat_options(
        workdir, check=True, writer_model="a:1", checker_model="a:1"
    )
    with pytest.raises(ThreatListError):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


def test_threat_strict_model_error_is_input_error(workdir):
    options = threat_options(
        workdir,
        check=True,
        writer_model="a:1",
        checker_model="a:1",
        strict_input=True,
    )
    with pytest.raises(InputError, match="checker must be a different model"):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


def test_threat_strict_about_is_checked_before_the_server(workdir):
    client = StandIn()
    client.healthy = False
    options = threat_options(
        workdir, about=" ".join(["w"] * 121), strict_input=True
    )
    with pytest.raises(InputError, match="121 words"):
        run_threat_lists(options, make_client=factory_with([client]))
    assert client.health_calls == 0


def test_threat_strict_bad_docs_is_input_error(workdir):
    (workdir / "docs" / "sessions.md").unlink()
    options = threat_options(workdir, strict_input=True)
    with pytest.raises(InputError):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


def test_threat_strict_output_folder(workdir):
    blocker = workdir / "blocker"
    blocker.write_text("a file", encoding="utf-8")
    options = threat_options(workdir, output=blocker / "out", strict_input=True)
    with pytest.raises(InputError, match="Cannot write to the output folder"):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


def test_threat_strict_makes_the_output_folder(workdir):
    options = threat_options(workdir, output=workdir / "a" / "b", strict_input=True)
    run = run_threat_lists(options, make_client=factory_with([StandIn(answer="{}")]))
    assert (workdir / "a" / "b").is_dir()
    assert not (workdir / "a" / "b" / ".write_test").exists()
    assert len(run.lists) == 1


def test_threat_strict_server_error_stays_typed(workdir):
    client = StandIn()
    client.healthy = False
    options = threat_options(workdir, strict_input=True)
    with pytest.raises(ModelServerError):
        run_threat_lists(options, make_client=factory_with([client]))


# threat lists: story selection -----------------------------------------------


def test_threat_story_ids_keep_file_order(workdir):
    write_stories(workdir, ("A", "B", "C"))
    options = threat_options(workdir, story_ids=("C", "A"))
    run = run_threat_lists(options, make_client=factory_with([StandIn("{}")]))
    assert [o.story_id for o in run.lists] == ["A", "C"]


def test_threat_unknown_story_id(workdir):
    options = threat_options(workdir, story_ids=("US-1", "NOPE"))
    with pytest.raises(InputError, match="Story not found in the input file: NOPE"):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


def test_threat_story_id_and_story_ids_together(workdir):
    options = threat_options(workdir, story_id="US-1", story_ids=("US-1",))
    with pytest.raises(InputError, match="not both"):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


# threat lists: stop, start events, keep --------------------------------------


def test_threat_should_stop_before_the_next_story(workdir):
    write_stories(workdir, ("A", "B", "C"))
    events: list[ProgressEvent] = []
    stop = {"now": False}

    def on_progress(event):
        events.append(event)
        stop["now"] = True

    run = run_threat_lists(
        threat_options(workdir),
        make_client=factory_with([StandIn("{}")]),
        on_progress=on_progress,
        should_stop=lambda: stop["now"],
    )
    assert run.stopped is True
    assert [o.story_id for o in run.lists] == ["A"]
    assert run.not_run == ["B", "C"]
    assert run.aborted is None
    assert [e.story_id for e in events] == ["A"]
    assert events[0].outcome is run.lists[0]


def test_threat_stop_before_the_first_story(workdir):
    run = run_threat_lists(
        threat_options(workdir),
        make_client=factory_with([StandIn("{}")]),
        should_stop=lambda: True,
    )
    assert run.stopped is True
    assert run.lists == []
    assert run.not_run == ["US-1"]


def test_threat_on_story_start(workdir):
    write_stories(workdir, ("A", "B"))
    started: list = []
    run_threat_lists(
        threat_options(workdir),
        make_client=factory_with([StandIn("{}")]),
        on_story_start=lambda *args: started.append(args),
    )
    assert started == [("A", 1, 2), ("B", 2, 2)]


def raise_on_second_story(monkeypatch, error: Exception) -> None:
    from secreqgen.threats import ThreatListGenerator

    original = ThreatListGenerator.generate

    def generate(self, user_story):
        if user_story.id == "B":
            raise error
        return original(self, user_story)

    monkeypatch.setattr(ThreatListGenerator, "generate", generate)


def test_threat_keep_finished_keeps_the_lists(workdir, monkeypatch):
    write_stories(workdir, ("A", "B", "C"))
    error = OSError("disk full")
    raise_on_second_story(monkeypatch, error)
    events: list[ProgressEvent] = []
    run = run_threat_lists(
        threat_options(workdir, keep_finished=True),
        make_client=factory_with([StandIn("{}")]),
        on_progress=events.append,
    )
    assert [o.story_id for o in run.lists] == ["A"]
    assert run.aborted is error
    assert run.not_run == ["C"]
    assert run.stopped is False
    assert [story_id for story_id, _ in run.failures] == ["B"]
    assert events[-1].error is error


def test_threat_error_is_raised_without_keep_finished(workdir, monkeypatch):
    write_stories(workdir, ("A", "B"))
    raise_on_second_story(monkeypatch, OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        run_threat_lists(
            threat_options(workdir),
            make_client=factory_with([StandIn("{}")]),
        )


def test_threat_failed_calls_ask_the_health_check(workdir):
    write_stories(workdir, ("A", "B", "C"))
    client = StandIn(answer=RuntimeError("the server is gone"))
    original_check = client.check_health

    def check_health():
        # The first call is the check before the run. Later calls fail.
        original_check()
        return client.health_calls == 1

    client.check_health = check_health
    run = run_threat_lists(
        threat_options(workdir, keep_finished=True),
        make_client=factory_with([client]),
    )
    assert [o.story_id for o in run.lists] == ["A"]
    assert all(not call["ok"] for call in run.lists[0].model["calls"])
    assert isinstance(run.aborted, ModelServerError)
    assert run.aborted.model == "gemma4:26b"
    assert "story A" in str(run.aborted)
    assert run.not_run == ["B", "C"]


def test_threat_failed_calls_with_a_healthy_server_go_on(workdir):
    write_stories(workdir, ("A", "B"))
    client = StandIn(answer=RuntimeError("one bad answer"))
    run = run_threat_lists(
        threat_options(workdir, keep_finished=True),
        make_client=factory_with([client]),
    )
    assert [o.story_id for o in run.lists] == ["A", "B"]
    assert run.aborted is None
    assert run.not_run == []


def test_threat_health_check_is_not_used_without_keep_finished(workdir):
    write_stories(workdir, ("A", "B"))
    client = StandIn(answer=RuntimeError("one bad answer"))
    run_threat_lists(threat_options(workdir), make_client=factory_with([client]))
    assert client.health_calls == 1


def test_threat_prompts_dir_needs_the_prompt_sets_module(workdir):
    pytest.importorskip("secreqgen.application.prompt_sets")
    missing = workdir / "no_such_set"
    options = threat_options(workdir, prompts_dir=missing)
    with pytest.raises(InputError):
        run_threat_lists(options, make_client=factory_with([StandIn()]))


# requirements ----------------------------------------------------------------


def test_requirements_model_goes_to_the_factory_only_when_set(workdir):
    made: list = []
    clients = [StandIn("mine:1b")]
    run = run_requirements(
        requirements_options(workdir, model=" mine:1b "),
        make_client=factory_with(clients, made),
    )
    assert made == ["mine:1b"]
    assert run.model == "mine:1b"
    # With no model, the factory of the existing tests (no model_name) works.
    run = run_requirements(
        requirements_options(workdir), make_client=requirements_factory(StandIn())
    )
    assert run.model == "stand-in"


def test_requirements_blank_model(workdir):
    with pytest.raises(InputError, match="model name is empty"):
        run_requirements(
            requirements_options(workdir, model="  "),
            make_client=requirements_factory(StandIn()),
        )


def test_requirements_story_ids(workdir):
    write_stories(workdir, ("A", "B", "C"))
    run = run_requirements(
        requirements_options(workdir, story_ids=("C", "A")),
        make_client=one_client_factory(StandIn()),
    )
    assert [r.user_story.id for r in run.results] == ["A", "C"]
    with pytest.raises(InputError, match="Story not found in the input file: Z"):
        run_requirements(
            requirements_options(workdir, story_ids=("Z",)),
            make_client=one_client_factory(StandIn()),
        )


def test_requirements_strict_empty_docs_is_input_error(workdir):
    (workdir / "docs" / "sessions.md").unlink()
    client = StandIn()
    client.healthy = False
    with pytest.raises(InputError):
        run_requirements(
            requirements_options(workdir, strict_input=True),
            make_client=requirements_factory(client),
        )
    assert client.health_calls == 0


def test_requirements_stop_and_start_events(workdir):
    write_stories(workdir, ("A", "B", "C"))
    started: list = []
    events: list[ProgressEvent] = []
    stop = {"now": False}

    def on_progress(event):
        events.append(event)
        stop["now"] = event.done == 2

    run = run_requirements(
        requirements_options(workdir),
        make_client=requirements_factory(StandIn()),
        on_progress=on_progress,
        on_story_start=lambda *args: started.append(args),
        should_stop=lambda: stop["now"],
    )
    assert run.stopped is True
    assert [r.user_story.id for r in run.results] == ["A", "B"]
    assert run.not_run == ["C"]
    assert started == [("A", 1, 3), ("B", 2, 3)]
    assert events[0].outcome is run.results[0]


def test_requirements_connection_error_is_raised_by_default(workdir):
    client = StandIn(answer=LLMConnectionError("server gone"))
    with pytest.raises(LLMConnectionError):
        run_requirements(
            requirements_options(workdir),
            make_client=requirements_factory(client),
        )


def test_requirements_keep_finished_after_a_connection_error(workdir):
    write_stories(workdir, ("A", "B", "C"))
    good = StandIn()
    calls = {"n": 0}
    original = good.generate

    def generate(prompt, system_prompt=None, **other):
        calls["n"] += 1
        if calls["n"] == 2:
            raise LLMConnectionError("server gone")
        return original(prompt, system_prompt, **other)

    good.generate = generate
    run = run_requirements(
        requirements_options(workdir, keep_finished=True),
        make_client=requirements_factory(good),
    )
    assert [r.user_story.id for r in run.results] == ["A"]
    assert isinstance(run.aborted, LLMConnectionError)
    assert run.not_run == ["C"]
    assert [story_id for story_id, _ in run.failures] == ["B"]
    assert run.text is not None


def test_requirements_prompts_dir_needs_the_prompt_sets_module(workdir):
    pytest.importorskip("secreqgen.application.prompt_sets")
    options = requirements_options(workdir, prompts_dir=workdir / "no_such_set")
    with pytest.raises(InputError):
        run_requirements(options, make_client=requirements_factory(StandIn()))


def test_requirements_prompt_set_name_of_the_tested_set(workdir):
    run = run_requirements(
        requirements_options(workdir),
        make_client=requirements_factory(StandIn()),
    )
    assert run.prompt_set == "s1_word_3000"
    assert run.tested_prompts is True


def test_generate_stories_stoppable_matches_generate_for_stories(workdir):
    from secreqgen.application.inputs import build_user_stories
    from secreqgen.application.requirements import (
        build_requirements_generator,
        build_requirements_retriever,
        generate_for_stories,
        requirements_prompt_builder,
    )
    from secreqgen.generation import GenerationMode

    stories, _ = build_user_stories(
        [{"id": "A", "title": "T", "story": "As a user I log in."}]
    )
    docs = workdir / "docs"

    def build():
        return build_requirements_generator(
            StandIn(),
            build_requirements_retriever(
                None, GenerationMode.RAG, docs=docs, product="demo"
            ),
            requirements_prompt_builder(docs),
        )

    results, failures = generate_for_stories(
        build(), stories, GenerationMode.RAG, None
    )
    loop = generate_stories_stoppable(build(), stories, GenerationMode.RAG, None)
    assert failures == loop.failures == []
    assert [r.requirements for r in results] == [r.requirements for r in loop.results]
    assert loop.stopped is False
    assert loop.aborted is None
    assert loop.not_run == []
