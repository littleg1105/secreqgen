"""The command of a form gives the same run as the GUI. No model runs.

The GUI shows a command under each form. The author asked that the command is
exact: when it is pasted into a terminal, the models get the same prompts.
This file proves it. For each case there are two runs:

- the GUI path: the real JobManager takes the spec that a click on Start gives,
  and calls run_threat_lists or run_requirements;
- the command path: the command that the GUI shows for the same spec goes
  through the real command line (typer.testing.CliRunner).

Both paths use the stand-in model clients of tests/unit/test_cli_golden.py.
The stand-ins record each prompt that a model gets. The two records must be
equal. Never weaken a case to make it pass: a difference is a fault of the
command or of the GUI.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui")

import secreqgen.cli  # noqa: E402
import typer.testing  # noqa: E402
from secreqgen.application.prompt_sets import (  # noqa: E402
    FILE_NAMES,
    UNUSED_FILES,
    copy_prompt_set,
)
from secreqgen_gui import jobs, storage  # noqa: E402
from secreqgen_gui.settings import save_settings  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
g = importlib.import_module("tests.unit.test_cli_golden")

WRITER = "gemma4:26b"
CHECKER = "qwen3.8:27b"


@pytest.fixture(autouse=True)
def _same_run_environment(tmp_path, monkeypatch):
    """A clean folder, no variable of the tool, and clean records."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    for name in list(os.environ):
        if name.startswith(("SECREQGEN_LLM_", "SECREQGEN_VECTORSTORE_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY", "vectordb")
    monkeypatch.setattr(secreqgen.cli.console, "_width", 200)
    g.CALLS.clear()
    g.PROMPTS.clear()
    return work


# stand-in clients


def threat_factory():
    """The first client is the writer, the next one is the checker."""
    made: list = []

    def factory(cfg, tested_setup, model_name=None):
        kind = g.ThreatChecker if made else g.ThreatWriter
        client = kind(model_name or cfg.llm.model_name, script=(None,))
        made.append(client)
        return client

    return factory


def requirements_factory():
    def factory(cfg, tested_setup, model_name=None):
        return g.StandInClient(model_name or cfg.llm.model_name)

    return factory


def _server(*names):
    """Return a stand-in for the check of the model server."""

    def check(url):
        models = tuple(SimpleNamespace(name=name) for name in names)
        return SimpleNamespace(reachable=True, models=models, error=None)

    return check


# the two paths


def write_inputs(ids=("US-1", "US-2", "US-3")):
    stories = Path(g.threat_stories_file(tuple(ids))).resolve()
    docs = Path(g.docs_folder()).resolve()
    return stories, docs


def make_prompt_set(kind: str, name: str = "mine") -> tuple[Path, dict[str, str]]:
    """Make a prompt set of the person with a changed text in each used file.

    Return its folder and the changed text of each file.
    """
    folder = copy_prompt_set(kind, name, storage.prompt_sets_dir())
    marks = {}
    for filename in FILE_NAMES[kind]:
        path = folder / filename
        mark = f"EXTRA-LINE-{kind}-{filename.replace('.txt', '')}"
        path.write_text(
            path.read_text(encoding="utf-8").rstrip("\n") + f"\n\n{mark}.\n",
            encoding="utf-8",
        )
        if (kind, filename) not in UNUSED_FILES:
            marks[filename] = mark
    return folder, marks


def gui_prompts(kind: str, spec, factory, models) -> list[str]:
    """Run the spec through the real JobManager. Return the prompts."""
    from secreqgen.application.requirements import run_requirements
    from secreqgen.application.threats import run_threat_lists

    real = run_threat_lists if kind == "threats" else run_requirements

    def run(options, **kw):
        return real(options, make_client=factory, **kw)

    manager = jobs.JobManager(check_server=_server(*models))
    g.PROMPTS.clear()
    if kind == "threats":
        manager._run_threats = run
        run_id = manager.start_threats(spec)
    else:
        manager._run_requirements = run
        run_id = manager.start_requirements(spec)
    manager.join(60)
    manifest = storage.read_manifest(run_id)
    assert manifest["status"] == "finished", manifest.get("error")
    return list(g.PROMPTS)


def command_prompts(command, monkeypatch, factory, extra_env=None) -> list[str]:
    """Run the command through the real command line. Return the prompts."""
    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    g.PROMPTS.clear()
    # A variable in front of the command wins over the terminal, as in a shell.
    env = dict(extra_env or {})
    env.update(dict(command.env))
    result = typer.testing.CliRunner().invoke(
        secreqgen.cli.app, list(command.argv[1:]), env=env
    )
    assert result.exit_code == 0, f"{command.text}\n{result.output}"
    return list(g.PROMPTS)


def threat_spec(stories, docs, **changes):
    values = dict(
        stories_file=stories,
        docs=docs,
        product=None,
        story_ids=None,
        writer_model=WRITER,
        check=True,
        checker_model=CHECKER,
        labels=True,
        about=None,
        prompt_set=None,
        other_formats=False,
    )
    values.update(changes)
    return jobs.ThreatRunSpec(**values)


def requirements_spec(stories, docs, **changes):
    values = dict(
        stories_file=stories,
        docs=docs,
        product=None,
        story_ids=None,
        model=WRITER,
        system_context=None,
        prompt_set=None,
        other_formats=False,
    )
    values.update(changes)
    return jobs.RequirementsRunSpec(**values)


def threat_command_of(spec, *, about_file=None):
    return jobs.command_for_threats(
        spec,
        total_stories=3,
        default_product="demo",
        about_file=about_file,
        base_url=jobs.load_settings().base_url,
    )


def requirements_command_of(spec):
    return jobs.command_for_requirements(
        spec,
        total_stories=3,
        default_product="demo",
        base_url=jobs.load_settings().base_url,
    )


def assert_same_threats(spec, monkeypatch, *, about_file=None, extra_env=None):
    command = threat_command_of(spec, about_file=about_file)
    assert command is not None
    models = [spec.writer_model] + ([spec.checker_model] if spec.check else [])
    by_gui = gui_prompts("threats", spec, threat_factory(), models)
    by_command = command_prompts(
        command, monkeypatch, threat_factory(), extra_env=extra_env
    )
    assert by_gui, command.text
    assert by_command == by_gui, command.text
    return command, by_command


def assert_same_requirements(spec, monkeypatch, *, extra_env=None):
    command = requirements_command_of(spec)
    assert command is not None
    by_gui = gui_prompts("requirements", spec, requirements_factory(), [spec.model])
    by_command = command_prompts(
        command, monkeypatch, requirements_factory(), extra_env=extra_env
    )
    assert by_gui, command.text
    assert by_command == by_gui, command.text
    return command, by_command


# the cases: the stories


def test_the_stories_of_all_three_give_the_same_prompts(monkeypatch):
    stories, docs = write_inputs()
    command, prompts = assert_same_threats(threat_spec(stories, docs), monkeypatch)
    assert "--story-id" not in command.text
    command, requirements = assert_same_requirements(
        requirements_spec(stories, docs), monkeypatch
    )
    assert "--story-id" not in command.text


@pytest.mark.parametrize(
    "ids",
    [("US-2", "US-3"), ("US-1", "US-3"), ("US-3",), ("US-1",), ("US-2",)],
)
def test_some_stories_give_the_same_prompts_for_threats(ids, monkeypatch):
    stories, docs = write_inputs()
    spec = threat_spec(stories, docs, story_ids=ids)
    command, prompts = assert_same_threats(spec, monkeypatch)
    assert command.text.count("--story-id") == len(ids)
    # Fewer stories give fewer prompts than all three stories give.
    every = gui_prompts(
        "threats", threat_spec(stories, docs), threat_factory(), [WRITER, CHECKER]
    )
    assert len(prompts) < len(every)


@pytest.mark.parametrize(
    "ids",
    [("US-2", "US-3"), ("US-1", "US-3"), ("US-3",), ("US-1",), ("US-2",)],
)
def test_some_stories_give_the_same_prompts_for_requirements(ids, monkeypatch):
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, story_ids=ids)
    command, prompts = assert_same_requirements(spec, monkeypatch)
    assert command.text.count("--story-id") == len(ids)
    assert len(prompts) == len(ids)


def test_the_last_story_only(monkeypatch):
    stories, docs = write_inputs()
    command, prompts = assert_same_requirements(
        requirements_spec(stories, docs, story_ids=("US-3",)), monkeypatch
    )
    assert len(prompts) == 1 and "US-3" in command.text
    assert_same_threats(threat_spec(stories, docs, story_ids=("US-3",)), monkeypatch)


def test_ids_in_another_order_than_the_file(monkeypatch):
    stories, docs = write_inputs()
    ids = ("US-3", "US-1", "US-2")[:2]  # US-3 and US-1: the file has US-1 first
    assert_same_requirements(
        requirements_spec(stories, docs, story_ids=ids), monkeypatch
    )
    assert_same_threats(threat_spec(stories, docs, story_ids=ids), monkeypatch)


def test_an_id_that_stands_two_times_counts_one_time(monkeypatch):
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, story_ids=("US-2", "US-2"))
    command, prompts = assert_same_requirements(spec, monkeypatch)
    assert command.text.count("--story-id") == 1 and len(prompts) == 1


# the prompt set of the person


def test_a_prompt_set_of_the_person_gives_the_same_prompts_for_threats(monkeypatch):
    stories, docs = write_inputs()
    folder, marks = make_prompt_set("threats")
    spec = threat_spec(stories, docs, prompt_set="mine")
    command, prompts = assert_same_threats(spec, monkeypatch)
    assert f"--prompts-dir {folder}" in command.text
    # The changed text of each file is in the prompts of both paths.
    text = "\n".join(prompts)
    for filename, mark in marks.items():
        assert mark in text, filename


def test_a_prompt_set_of_the_person_gives_the_same_prompts_for_requirements(
    monkeypatch,
):
    stories, docs = write_inputs()
    folder, marks = make_prompt_set("requirements")
    spec = requirements_spec(stories, docs, prompt_set="mine")
    command, prompts = assert_same_requirements(spec, monkeypatch)
    assert f"--prompts-dir {folder}" in command.text
    text = "\n".join(prompts)
    for filename, mark in marks.items():
        assert mark in text, filename


def test_the_tested_prompts_have_none_of_the_changed_text(monkeypatch):
    stories, docs = write_inputs()
    _folder, marks = make_prompt_set("requirements")
    _command, prompts = assert_same_requirements(
        requirements_spec(stories, docs), monkeypatch
    )
    assert not any(mark in "\n".join(prompts) for mark in marks.values())


# the model of a requirements run


def test_a_requirements_model_that_is_not_the_default(monkeypatch):
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, model="mistral-small3.2:24b")
    command, prompts = assert_same_requirements(spec, monkeypatch)
    assert "--model mistral-small3.2:24b" in command.text
    assert all("mistral-small3.2:24b generate" in text for text in prompts)


def test_the_model_of_the_settings_is_named_too(monkeypatch):
    # The default model of the tool settings is llama3:8b. The command still
    # names it, so that a variable in a terminal cannot change the run.
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, model="llama3:8b")
    command, _prompts = assert_same_requirements(spec, monkeypatch)
    assert "--model llama3:8b" in command.text


# a terminal with variables of the tool set


def test_a_variable_for_the_model_does_not_change_the_requirements_run(monkeypatch):
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, story_ids=("US-2",))
    command = requirements_command_of(spec)
    by_gui = gui_prompts("requirements", spec, requirements_factory(), [WRITER])
    by_command = command_prompts(
        command,
        monkeypatch,
        requirements_factory(),
        extra_env={"SECREQGEN_LLM_MODEL_NAME": "other:1"},
    )
    assert by_command == by_gui
    assert all(f"{WRITER} generate" in text for text in by_command)
    assert not any("other:1" in text for text in by_command)


def test_a_variable_for_the_model_does_not_change_the_threat_run(monkeypatch):
    stories, docs = write_inputs()
    spec = threat_spec(stories, docs)
    assert_same_threats(
        spec, monkeypatch, extra_env={"SECREQGEN_LLM_MODEL_NAME": "other:1"}
    )


# the address of Ollama

OTHER_SERVER = "http://example.invalid:1"
GUI_SERVER = "http://gui-server.invalid:2"
DEFAULT_SERVER = "http://localhost:11434"


def recording(factory, addresses):
    """Wrap a stand-in factory. It notes the address of each client that it builds."""

    def wrapped(cfg, tested_setup, model_name=None):
        addresses.append(cfg.llm.base_url)
        return factory(cfg, tested_setup, model_name)

    return wrapped


def address_case(kind, monkeypatch, extra_env):
    """Run one spec by the GUI and by its command.

    Return the command and the addresses of the clients of each path.
    """
    stories, docs = write_inputs()
    by_gui_at, by_command_at = [], []
    if kind == "threats":
        spec = threat_spec(stories, docs)
        command = threat_command_of(spec)
        make, models = threat_factory, [spec.writer_model, spec.checker_model]
    else:
        spec = requirements_spec(stories, docs)
        command = requirements_command_of(spec)
        make, models = requirements_factory, [WRITER]
    by_gui = gui_prompts(kind, spec, recording(make(), by_gui_at), models)
    by_command = command_prompts(
        command, monkeypatch, recording(make(), by_command_at), extra_env=extra_env
    )
    assert by_command == by_gui
    assert by_gui_at and by_command_at
    return command, by_gui_at, by_command_at


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_the_default_address_needs_no_variable(kind, monkeypatch):
    """The GUI address is the default one, and the terminal sets none."""
    command, by_gui_at, by_command_at = address_case(kind, monkeypatch, None)
    assert command.env == ()
    assert by_command_at == by_gui_at
    assert set(by_gui_at) == {DEFAULT_SERVER}


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_an_address_of_the_settings_wins_over_the_terminal(kind, monkeypatch):
    """The GUI has its own address. The terminal has another one set."""
    save_settings(dataclasses.replace(jobs.load_settings(), base_url=GUI_SERVER))
    command, by_gui_at, by_command_at = address_case(
        kind, monkeypatch, {"SECREQGEN_LLM_BASE_URL": OTHER_SERVER}
    )
    assert command.env == (("SECREQGEN_LLM_BASE_URL", GUI_SERVER),)
    assert by_command_at == by_gui_at
    assert set(by_gui_at) == {GUI_SERVER}


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_a_terminal_with_its_own_address_keeps_it_for_a_default_command(
    kind, monkeypatch
):
    """The known limit. The README of the GUI states it.

    The GUI address is the default one, so the command has no variable. A
    terminal that sets SECREQGEN_LLM_BASE_URL runs the command on its own
    server. The prompts are the same.
    """
    command, by_gui_at, by_command_at = address_case(
        kind, monkeypatch, {"SECREQGEN_LLM_BASE_URL": OTHER_SERVER}
    )
    assert command.env == ()
    assert set(by_gui_at) == {DEFAULT_SERVER}
    assert set(by_command_at) == {OTHER_SERVER}


# the older cases and all together


def test_the_older_options_still_give_the_same_prompts(monkeypatch):
    stories, docs = write_inputs()
    about = storage.swap_about("Demo is a web shop. It has sessions and a cart.")
    assert_same_threats(
        threat_spec(
            stories,
            docs,
            about="Demo is a web shop. It has sessions and a cart.",
            labels=False,
            product="keycloak",
        ),
        monkeypatch,
        about_file=about,
    )
    assert_same_threats(
        threat_spec(stories, docs, check=False, checker_model=None), monkeypatch
    )
    assert_same_threats(
        threat_spec(stories, docs, writer_model=CHECKER, checker_model=WRITER),
        monkeypatch,
    )


def test_the_older_requirements_options_still_give_the_same_prompts(monkeypatch):
    stories, docs = write_inputs()
    context = Path("ctx.yaml").resolve()
    context.write_text("name: Demo\nstack: Ruby\n", encoding="utf-8")
    assert_same_requirements(
        requirements_spec(
            stories, docs, system_context=str(context), product="keycloak"
        ),
        monkeypatch,
    )
    (docs / "guide.rst").write_text(
        "Sessions\n========\n\nThe session cookie must have the Secure flag.\n",
        encoding="utf-8",
    )
    assert_same_requirements(
        requirements_spec(stories, docs, other_formats=True), monkeypatch
    )
    assert_same_threats(threat_spec(stories, docs, other_formats=True), monkeypatch)


def test_all_options_together_for_threats(monkeypatch):
    stories, docs = write_inputs()
    folder, marks = make_prompt_set("threats")
    about = storage.swap_about("Demo is a web shop.")
    spec = threat_spec(
        stories,
        docs,
        story_ids=("US-3", "US-1"),
        writer_model="one-writer",
        checker_model="other-checker",
        labels=False,
        about="Demo is a web shop.",
        prompt_set="mine",
        product="keycloak",
    )
    command, prompts = assert_same_threats(
        spec,
        monkeypatch,
        about_file=about,
        extra_env={"SECREQGEN_LLM_MODEL_NAME": "other:1"},
    )
    assert command.text.count("--story-id") == 2
    assert f"--prompts-dir {folder}" in command.text
    assert "--model one-writer" in command.text
    assert "--checker-model other-checker" in command.text
    assert any(mark in "\n".join(prompts) for mark in marks.values())


def test_all_options_together_for_requirements(monkeypatch):
    stories, docs = write_inputs()
    folder, marks = make_prompt_set("requirements")
    context = Path("ctx.yaml").resolve()
    context.write_text("name: Demo\nstack: Ruby\n", encoding="utf-8")
    spec = requirements_spec(
        stories,
        docs,
        story_ids=("US-3", "US-1"),
        model="mistral-small3.2:24b",
        system_context=str(context),
        prompt_set="mine",
        product="keycloak",
    )
    command, prompts = assert_same_requirements(
        spec, monkeypatch, extra_env={"SECREQGEN_LLM_MODEL_NAME": "other:1"}
    )
    assert command.text.count("--story-id") == 2
    assert f"--prompts-dir {folder}" in command.text
    assert "mistral-small3.2:24b generate" in prompts[0]
    assert all(mark in "\n".join(prompts) for mark in marks.values())


# the folder of the prompt set: one function for the run and for the command


def test_the_run_and_the_command_get_the_same_prompt_folder(tmp_path):
    folder, _marks = make_prompt_set("threats")
    assert jobs.prompt_set_folder("threats", None) is None
    assert jobs.prompt_set_folder("threats", "mine") == folder
    manager = jobs.JobManager()
    spec = SimpleNamespace(prompt_set="mine")
    assert manager._prompts_dir("threats", spec) == folder
    stories, docs = write_inputs()
    command = threat_command_of(threat_spec(stories, docs, prompt_set="mine"))
    at = command.argv.index("--prompts-dir")
    assert command.argv[at + 1] == str(folder)


def test_a_prompt_set_that_is_not_there_gives_no_command_and_no_run(tmp_path):
    stories, docs = write_inputs()
    spec = threat_spec(stories, docs, prompt_set="gone")
    assert threat_command_of(spec) is None
    assert (
        requirements_command_of(requirements_spec(stories, docs, prompt_set="gone"))
        is None
    )
    with pytest.raises(jobs.RunFailedError):
        jobs.prompt_set_folder("threats", "gone")
    with pytest.raises(jobs.RunFailedError):
        jobs.prompt_set_folder("threats", "Not Allowed")


def test_the_whole_file_of_stories_is_what_the_run_reads(monkeypatch):
    """A run of some stories reads the file as it is, as the command does."""
    stories, docs = write_inputs()
    seen = {}

    def run(options, **kw):
        seen["file"] = options.input_file
        seen["ids"] = options.story_ids
        return SimpleNamespace(
            lists=[], failures=[], skipped=[], stopped=False, aborted=None, not_run=[]
        )

    manager = jobs.JobManager(check_server=_server(WRITER, CHECKER))
    manager._run_threats = run
    run_id = manager.start_threats(
        threat_spec(stories, docs, story_ids=("US-1", "US-3"))
    )
    manager.join(30)
    manifest = storage.read_manifest(run_id)
    copy = storage.run_dir(run_id) / manifest["stories_file"]
    assert copy.read_bytes() == stories.read_bytes()
    assert seen["file"] == copy and seen["ids"] == ("US-1", "US-3")
    data = json.loads(copy.read_text(encoding="utf-8"))
    assert len(data["user_stories"]) == 3


# the comparison can fail: it sees a command that lost an option


def _without(command, option):
    """Return the command with an option and its value taken out."""
    words = list(command.argv)
    while option in words:
        at = words.index(option)
        del words[at : at + 2]
    return SimpleNamespace(argv=tuple(words), env=command.env, text=" ".join(words))


def test_a_command_without_story_options_gives_other_prompts(monkeypatch):
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, story_ids=("US-1", "US-3"))
    command = requirements_command_of(spec)
    by_gui = gui_prompts("requirements", spec, requirements_factory(), [WRITER])
    cut = command_prompts(
        _without(command, "--story-id"), monkeypatch, requirements_factory()
    )
    assert len(by_gui) == 2 and len(cut) == 3


def test_a_command_without_the_prompt_folder_gives_other_prompts(monkeypatch):
    stories, docs = write_inputs()
    _folder, marks = make_prompt_set("requirements")
    spec = requirements_spec(stories, docs, prompt_set="mine")
    command = requirements_command_of(spec)
    by_gui = gui_prompts("requirements", spec, requirements_factory(), [WRITER])
    cut = command_prompts(
        _without(command, "--prompts-dir"), monkeypatch, requirements_factory()
    )
    assert by_gui != cut
    assert any(mark in "\n".join(by_gui) for mark in marks.values())
    assert not any(mark in "\n".join(cut) for mark in marks.values())


def test_a_command_without_the_model_gives_other_prompts(monkeypatch):
    stories, docs = write_inputs()
    spec = requirements_spec(stories, docs, model="mistral-small3.2:24b")
    command = requirements_command_of(spec)
    by_gui = gui_prompts(
        "requirements", spec, requirements_factory(), ["mistral-small3.2:24b"]
    )
    cut = command_prompts(
        _without(command, "--model"), monkeypatch, requirements_factory()
    )
    assert by_gui != cut
