"""Tests of the new options of the two generate commands.

The options: --story-id (repeated) and --prompts-dir on `generate threats`;
--story-id, --model and --prompts-dir on `generate requirements`. The rule of
the change: the command that the GUI shows must give the same run. So the
commands call the same helper functions as the application layer, and give the
same error sentences in the same order.

The proof that the prompts are equal is in test_application_commands.py. This
file tests the options themselves: the parser, the help texts, the order, the
errors, the shared helpers and the files of the run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import secreqgen.cli
import typer.main
import typer.testing
from secreqgen.application import prompt_sets, requirements, threats

from . import test_application_commands as ac
from . import test_cli_golden as g
from . import test_cli_golden_plan2 as p2
from .test_cli_golden import _clean_environment  # noqa: F401  (autouse fixture)

ROOT = typer.main.get_command(secreqgen.cli.app)
THREATS_COMMAND = ROOT.commands["generate"].commands["threats"]
REQUIREMENTS_COMMAND = ROOT.commands["generate"].commands["requirements"]

PROMPTS_HELP = (
    "Folder with your own prompt files, in place of the tested prompts. "
    "Outside the tested setup"
)


def option(command, name: str):
    return next(p for p in command.params if p.name == name)


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


# ---------------------------------------------------------------------------
# The parser and the help texts
# ---------------------------------------------------------------------------


def test_help_texts_are_the_given_words():
    assert flat(option(THREATS_COMMAND, "story_id").help) == (
        "Write the threat list of these stories only. Give the option one time "
        "for each story"
    )
    assert flat(option(THREATS_COMMAND, "prompts_dir").help) == PROMPTS_HELP
    assert flat(option(REQUIREMENTS_COMMAND, "story_id").help) == (
        "Write the requirements of these stories only. Give the option one time "
        "for each story"
    )
    assert flat(option(REQUIREMENTS_COMMAND, "model").help) == (
        "The model that writes the requirements. Default: the setting "
        "SECREQGEN_LLM_MODEL_NAME"
    )
    assert flat(option(REQUIREMENTS_COMMAND, "prompts_dir").help) == PROMPTS_HELP


def test_the_option_names_and_the_repeat():
    for command in (THREATS_COMMAND, REQUIREMENTS_COMMAND):
        story = option(command, "story_id")
        assert story.opts == ["--story-id"]
        assert story.multiple is True
        assert option(command, "prompts_dir").opts == ["--prompts-dir"]
        assert option(command, "prompts_dir").multiple is False
    model = option(REQUIREMENTS_COMMAND, "model")
    assert model.opts == ["--model", "-m"]
    assert model.multiple is False


def parse_args(command, *args: str) -> dict:
    ctx = command.make_context(command.name, list(args))
    return dict(ctx.params)


def test_the_parser_gives_a_list_of_ids_and_none_without_the_option():
    base = ["stories.json", "--docs", "docs"]
    for command in (THREATS_COMMAND, REQUIREMENTS_COMMAND):
        assert not parse_args(command, *base)["story_id"]
        one = parse_args(command, *base, "--story-id", "A")
        assert list(one["story_id"]) == ["A"]
        many = parse_args(
            command, *base, "--story-id", "B", "--story-id", "A", "--story-id", "B"
        )
        assert list(many["story_id"]) == ["B", "A", "B"]
        assert parse_args(command, *base)["prompts_dir"] is None
    assert parse_args(REQUIREMENTS_COMMAND, *base)["model"] is None
    assert parse_args(REQUIREMENTS_COMMAND, *base, "-m", "x:1")["model"] == "x:1"
    assert parse_args(REQUIREMENTS_COMMAND, *base, "--model", "y:2")["model"] == "y:2"
    parsed = parse_args(THREATS_COMMAND, *base, "--prompts-dir", "p")
    assert Path(parsed["prompts_dir"]) == Path("p")


# ---------------------------------------------------------------------------
# The order and the errors
# ---------------------------------------------------------------------------


def test_the_stories_run_in_the_order_of_the_file(monkeypatch):
    g.use_threat_clients(monkeypatch)
    p2.three_stories()
    lines = g.describe(
        [
            *p2.THREATS,
            "--story-id",
            "US-3",
            "--story-id",
            "US-1",
            "--no-check",
            "--output",
            "out",
        ]
    )
    text = "\n".join(lines)
    assert lines[0] == "exit=0"
    assert text.index("✓ US-1:") < text.index("✓ US-3:")
    assert "US-2" not in text


def test_a_story_id_error_comes_before_any_model_call_on_both_commands(monkeypatch):
    for kind in ("threats", "requirements"):
        g.CALLS.clear()
        if kind == "threats":
            g.use_threat_clients(monkeypatch)
            args = p2.THREATS
        else:
            p2.use_requirements_client(monkeypatch)
            args = p2.REQUIREMENTS
        Path("stories.json").unlink(missing_ok=True)
        p2.g.threat_stories_file(p2.IDS3)
        if not Path("docs").exists():
            g.docs_folder()
        lines = g.describe([*args, "--story-id", "US-1", "--story-id", "[red]X"])
        assert lines[0] == "exit=1", lines
        # The id shows as text. It is not read as markup.
        assert "Error: Story not found in the input file: [red]X" in "\n".join(lines)
        assert not g.CALLS and not g.PROMPTS


def test_threats_story_id_in_an_empty_file_is_still_an_error():
    g.write("empty.json", json.dumps({"user_stories": []}))
    Path("docs").mkdir()
    lines = g.describe(
        ["generate", "threats", "empty.json", "--docs", "docs", "--story-id", "A"]
    )
    assert lines[0] == "exit=1"
    assert "Error: Story not found in the input file: A" in "\n".join(lines)


def test_requirements_model_with_no_text_is_an_error(monkeypatch):
    p2.use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    for value in ("", " ", "\t"):
        g.CALLS.clear()
        lines = g.describe([*p2.REQUIREMENTS, "--model", value])
        assert lines[0] == "exit=1", lines
        assert "Error: The model name is empty." in "\n".join(lines)
        assert not g.CALLS


def test_requirements_without_the_new_options_makes_the_client_as_before(monkeypatch):
    """No --model: the client call has no model_name. The settings choose."""
    p2.use_requirements_client(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    lines = g.describe(p2.REQUIREMENTS)
    assert lines[0] == "exit=0", lines
    assert g.CALLS[0] == "_ollama_client(model_name=None, tested_setup=True)"


def test_the_prompt_folder_is_checked_before_the_models_are_resolved(monkeypatch):
    """A bad folder and a bad model pair: the folder error comes first."""
    g.use_threat_clients(monkeypatch)
    g.threat_stories_file()
    g.docs_folder()
    lines = g.describe(
        [
            *p2.THREATS,
            "--prompts-dir",
            "nothing",
            "--model",
            "same:1",
            "--checker-model",
            "same:1",
        ]
    )
    assert lines[0] == "exit=1"
    assert "The prompt folder does not exist: nothing" in "\n".join(lines)


# ---------------------------------------------------------------------------
# The commands call the helpers of the application layer
# ---------------------------------------------------------------------------


def spy(monkeypatch, module, name: str) -> list[tuple]:
    """Wrap a function of a module. Return the list that records its calls."""
    calls: list[tuple] = []
    real = getattr(module, name)

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)
    return calls


def test_threats_uses_the_shared_helpers(monkeypatch):
    g.use_threat_clients(monkeypatch)
    p2.three_stories()
    folder = p2.prompt_folder("threats")
    selected = spy(monkeypatch, threats, "select_story_ids")
    checked = spy(monkeypatch, prompt_sets, "check_prompt_folder")
    built = spy(monkeypatch, threats, "build_threat_generator")
    lines = g.describe(
        [
            *p2.THREATS,
            "--story-id",
            "US-2",
            "--story-id",
            "US-3",
            "--prompts-dir",
            folder,
            "--no-check",
            "--output",
            "out",
        ]
    )
    assert lines[0] == "exit=0", lines
    ((args, _),) = selected
    assert [s.id for s in args[0]] == ["US-1", "US-2", "US-3"]
    assert list(args[1]) == ["US-2", "US-3"]
    assert [(a[0], str(a[1])) for a, _ in checked] == [("threats", folder)]
    ((args, _),) = built
    assert args[0].prompts_dir == Path(folder)
    assert args[0].labels is True


def test_requirements_uses_the_shared_helpers(monkeypatch):
    p2.use_requirements_client(monkeypatch)
    p2.three_stories()
    folder = p2.prompt_folder("requirements")
    selected = spy(monkeypatch, threats, "select_story_ids")
    checked = spy(monkeypatch, prompt_sets, "check_prompt_folder")
    chosen = spy(monkeypatch, requirements, "choose_requirements_prompts")
    lines = g.describe(
        [*p2.REQUIREMENTS, "--story-id", "US-1", "--prompts-dir", folder]
    )
    assert lines[0] == "exit=0", lines
    ((args, _),) = selected
    assert list(args[1]) == ["US-1"]
    assert [(a[0], str(a[1])) for a, _ in checked] == [("requirements", folder)]
    ((args, _),) = chosen
    assert args[0] == Path("docs") and args[1] == Path(folder)


def test_run_requirements_uses_the_same_prompt_helper(monkeypatch, tmp_path):
    """The GUI path and the command line call one function for the prompts."""
    stories, docs = ac.write_inputs()
    folder = ac.marked_prompt_set("requirements", tmp_path / "sets")
    chosen = spy(monkeypatch, requirements, "choose_requirements_prompts")
    values = ac.requirements_values(stories, docs, prompts_dir=folder)
    ac.run_gui_requirements(values, tmp_path, ac.requirements_factory())
    ((args, _),) = chosen
    assert args == (docs, folder)


@pytest.mark.parametrize("with_folder", [False, True])
def test_requirements_prompt_helper_gives_what_run_requirements_gave(
    with_folder, tmp_path
):
    """The helper that was moved out of run_requirements keeps its three values."""
    docs = tmp_path / "docs"
    docs.mkdir()
    if not with_folder:
        builder, name, tested = requirements.choose_requirements_prompts(docs, None)
        assert name == "s1_word_3000" and tested is True
        _, name, tested = requirements.choose_requirements_prompts(None, None)
        assert name == "" and tested is True
        return
    folder = ac.marked_prompt_set("requirements", tmp_path / "sets")
    builder, name, tested = requirements.choose_requirements_prompts(docs, folder)
    assert name == "user:mine" and tested is False
    plain = prompt_sets.copy_prompt_set("requirements", "plain", tmp_path / "sets")
    _, name, tested = requirements.choose_requirements_prompts(None, plain)
    assert name == "user:plain" and tested is True


# ---------------------------------------------------------------------------
# The files of a run
# ---------------------------------------------------------------------------


def scrub(value):
    """Remove the run times, the only fields that differ between two runs."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k != "seconds"}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def test_the_files_of_a_run_with_a_prompt_folder_name_the_set_as_the_gui_run_does(
    tmp_path, monkeypatch
):
    stories, docs = ac.write_inputs()
    folder = ac.marked_prompt_set("threats", tmp_path / "sets")
    values = ac.threat_values(
        stories, docs, prompts_dir=folder, story_ids=["US-2", "US-3"]
    )
    command = ac.threat_command(**values)
    out = tmp_path / "cli_out"
    ac.run_command(
        command,
        monkeypatch,
        ac.threat_factory(),
        extra=("--output", str(out)),
    )
    ac.run_gui_threats(values, tmp_path, ac.threat_factory())
    gui = tmp_path / "gui_out"
    assert sorted(p.name for p in out.iterdir()) == ["US-2", "US-3"]
    assert sorted(p.name for p in gui.iterdir()) == ["US-2", "US-3"]
    for story in ("US-2", "US-3"):
        by_command = json.loads((out / story / "threats.json").read_text("utf-8"))
        by_gui = json.loads((gui / story / "threats.json").read_text("utf-8"))
        # The keys that hold the prompt label.
        assert by_command["prompt_set"] == by_gui["prompt_set"] == "user:mine"
        assert by_command["tested_prompts"] is by_gui["tested_prompts"] is False
        # And the whole file: the two paths write the same list.
        assert scrub(by_command) == scrub(by_gui)
        page_by_command = (out / story / "threats.md").read_text("utf-8")
        page_by_gui = (gui / story / "threats.md").read_text("utf-8")
        assert g.normalise(page_by_command) == g.normalise(page_by_gui)


def test_the_tested_prompts_keep_the_tested_label(tmp_path, monkeypatch):
    stories, docs = ac.write_inputs(("US-1",))
    values = ac.threat_values(stories, docs)
    out = tmp_path / "cli_out"
    ac.run_command(
        ac.threat_command(**values),
        monkeypatch,
        ac.threat_factory(),
        extra=("--output", str(out)),
    )
    saved = json.loads((out / "US-1" / "threats.json").read_text("utf-8"))
    assert saved["prompt_set"] == "threats_v5b_labels"
    assert "tested_prompts" not in saved
