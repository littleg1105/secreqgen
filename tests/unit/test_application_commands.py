"""Tests of the builders of the command line command (application/commands.py).

Four kinds of test:
1. Each rule as a small case.
2. The command parses: the real parser of the command line reads argv and
   gives the values that went into the builder.
3. The text is the argv: a real shell reads the text and gives the argv.
4. The same run: the built command and the functions that the GUI calls give
   the same prompts to the (stand-in) models.
"""

from __future__ import annotations

import json
import os
import random
import shlex
import subprocess
from pathlib import Path

import pytest
import secreqgen.cli
import typer.main
import typer.testing
from secreqgen.application import commands as cmds
from secreqgen.application.clients import build_ollama_client
from secreqgen.application.commands import (
    Command,
    about_command,
    context_command,
    requirements_command,
    threat_command,
)
from secreqgen.config import LLMConfig, get_config
from secreqgen.threats import TESTED_WRITER, choose_models
from secreqgen.threats.pipeline import TESTED_CHECKER, _same_model

from . import test_cli_golden as g
from .test_cli_golden import _clean_environment  # noqa: F401  (autouse fixture)
from .test_import_weight import forbidden_loaded

HOME = Path("/home/tester")
STORIES = Path("/data/stories.json")
DOCS = Path("/data/docs")
DEFAULT_URL = "http://localhost:11434"
# The default address needs no variable in front of the command.
DEFAULT_ENV: tuple[tuple[str, str], ...] = ()


def expected_env(base_url: str) -> tuple[tuple[str, str], ...]:
    """Return the variables of a command: the address, if not the default."""
    if base_url == DEFAULT_URL:
        return DEFAULT_ENV
    return (("SECREQGEN_LLM_BASE_URL", base_url),)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def threat(**changes) -> Command:
    values = dict(
        stories_file=STORIES,
        docs=DOCS,
        product=None,
        default_product=None,
        total_stories=8,
        story_ids=None,
        writer_model=TESTED_WRITER,
        check=True,
        checker_model=TESTED_CHECKER,
        labels=True,
        about_file=None,
        prompts_dir=None,
        other_formats=False,
        base_url=DEFAULT_URL,
        home=HOME,
    )
    values.update(changes)
    return threat_command(**values)


def requirements(**changes) -> Command:
    values = dict(
        stories_file=STORIES,
        docs=DOCS,
        product=None,
        default_product=None,
        total_stories=8,
        story_ids=None,
        model="llama3:8b",
        system_context=None,
        prompts_dir=None,
        other_formats=False,
        base_url=DEFAULT_URL,
        home=HOME,
    )
    values.update(changes)
    return requirements_command(**values)


def context(**changes) -> Command:
    values = dict(
        folder=Path("/data/code"),
        product=None,
        writer_model=TESTED_WRITER,
        base_url=DEFAULT_URL,
        home=HOME,
    )
    values.update(changes)
    return context_command(**values)


def about(**changes) -> Command:
    values = dict(
        folder=Path("/data/code"),
        product=None,
        writer_model=TESTED_WRITER,
        base_url=DEFAULT_URL,
        home=HOME,
    )
    values.update(changes)
    return about_command(**values)


ROOT = typer.main.get_command(secreqgen.cli.app)


def parse(command: Command) -> dict:
    """Give argv to the real parser of the command line, with no run."""
    assert command.argv[:2] == ("secreqgen", "generate")
    name = command.argv[2]
    sub = ROOT.commands["generate"].commands[name]
    ctx = sub.make_context(name, list(command.argv[3:]))
    return dict(ctx.params)


# ---------------------------------------------------------------------------
# 1. The rules
# ---------------------------------------------------------------------------


def test_threat_default_command_names_both_models():
    command = threat()
    assert command.env == DEFAULT_ENV
    assert command.argv == (
        "secreqgen",
        "generate",
        "threats",
        "/data/stories.json",
        "--docs",
        "/data/docs",
        "--model",
        TESTED_WRITER,
        "--checker-model",
        TESTED_CHECKER,
    )
    assert command.text == (
        "secreqgen generate threats /data/stories.json --docs /data/docs "
        f"--model {TESTED_WRITER} --checker-model {TESTED_CHECKER}"
    )
    assert not hasattr(command, "differences")


def test_threat_writer_is_always_in_the_command():
    # The tested writer too: a variable of the tool must not change the run.
    assert threat(writer_model=TESTED_WRITER).argv[6:8] == ("--model", TESTED_WRITER)
    command = threat(writer_model="mistral-small3.2:24b")
    assert command.argv[6:8] == ("--model", "mistral-small3.2:24b")
    # A blank writer is the writer that the tool takes: the tested one.
    for blank in ("", "   "):
        assert threat(writer_model=blank).argv[6:8] == ("--model", TESTED_WRITER)
    # The name has no spaces around it.
    assert threat(writer_model="  m1 ").argv[6:8] == ("--model", "m1")


def test_threat_checker_is_always_in_the_command_when_the_check_is_on():
    command = threat(writer_model="m1", checker_model="llama3:8b")
    assert command.argv[-2:] == ("--checker-model", "llama3:8b")
    # No name: the checker that the tool takes for that writer (choose_models).
    for blank in (None, "", "  "):
        command = threat(writer_model="m1", checker_model=blank)
        assert command.argv[-2:] == ("--checker-model", choose_models("m1")[1])
        assert command.argv[-1] == TESTED_CHECKER
    # A writer that is the tested checker: the default checker is the tested writer.
    command = threat(writer_model=TESTED_CHECKER, checker_model=None)
    assert command.argv[-2:] == ("--checker-model", TESTED_WRITER)
    assert threat(writer_model="", checker_model=None).argv[-1] == TESTED_CHECKER
    # A name that is given stays, also when it is the default one.
    command = threat(writer_model=TESTED_CHECKER, checker_model="other")
    assert command.argv[-2:] == ("--checker-model", "other")
    assert threat(checker_model=TESTED_CHECKER).argv[-1] == TESTED_CHECKER


def test_threat_check_off_has_no_checker_option():
    command = threat(check=False, checker_model="llama3:8b")
    assert "--no-check" in command.argv
    assert "--checker-model" not in command.argv
    assert command.argv[6:8] == ("--model", TESTED_WRITER)
    assert "--no-check" not in threat(check=True).argv


def test_threat_builder_does_not_raise_for_the_same_model_twice():
    """The tool refuses this pair when it runs. The block of a form still shows it."""
    command = threat(writer_model="m1", checker_model="m1")
    assert command.argv[-4:] == ("--model", "m1", "--checker-model", "m1")


def test_threat_labels_about_and_other_formats():
    assert "--no-labels" in threat(labels=False).argv
    assert "--no-labels" not in threat(labels=True).argv
    command = threat(about_file=HOME / ".secreqgen/runs/about.txt")
    assert command.argv[-2:] == (
        "--about-file",
        "/home/tester/.secreqgen/runs/about.txt",
    )
    assert command.text.endswith("--about-file ~/.secreqgen/runs/about.txt")
    assert threat(other_formats=True).argv[-1] == "--other-formats"
    assert "--other-formats" not in threat().argv


def test_threat_product_is_left_out_when_the_default_gives_the_same():
    assert "--product" not in threat(product=None, default_product="demo").argv
    assert "--product" not in threat(product="demo", default_product="demo").argv
    command = threat(product="keycloak", default_product="demo")
    assert command.argv[6:8] == ("--product", "keycloak")
    command = threat(product="demo", default_product=None)
    assert command.argv[6:8] == ("--product", "demo")


def story_options(command: Command) -> list[str]:
    """Return the values of the --story-id options of a command, in order."""
    argv = command.argv
    return [argv[n + 1] for n, word in enumerate(argv) if word == "--story-id"]


@pytest.mark.parametrize("build", [threat, requirements])
def test_story_choice(build):
    ids = [f"S-{n}" for n in range(1, 9)]
    # No choice, an empty choice, all stories in any order: no option.
    for choice in (None, [], ids, list(reversed(ids)), ids + ids):
        assert story_options(build(story_ids=choice)) == []
        assert "--story-id" not in build(story_ids=choice).argv
    # One story, some stories: one option for each, in the order given.
    assert story_options(build(story_ids=["S-3"])) == ["S-3"]
    assert story_options(build(story_ids=["S-1", "S-2"])) == ["S-1", "S-2"]
    assert story_options(build(story_ids=["S-5", "S-2", "S-7"])) == [
        "S-5",
        "S-2",
        "S-7",
    ]
    assert story_options(build(story_ids=ids[:7])) == ids[:7]
    # An id that stands two times counts one time (first place).
    assert story_options(build(story_ids=["S-4", "S-1", "S-4"])) == ["S-4", "S-1"]
    # One story in a file of one story is the whole file.
    assert story_options(build(story_ids=["S-1"], total_stories=1)) == []
    # Ids with odd characters stay whole.
    assert story_options(build(story_ids=["a b", "-x", "it's"])) == [
        "a b",
        "-x",
        "it's",
    ]


def test_story_options_come_before_the_models_and_after_the_product():
    command = threat(product="p", default_product=None, story_ids=["S-2", "S-1"])
    assert command.argv[6:] == (
        "--product",
        "p",
        "--story-id",
        "S-2",
        "--story-id",
        "S-1",
        "--model",
        TESTED_WRITER,
        "--checker-model",
        TESTED_CHECKER,
    )
    command = requirements(product="p", default_product=None, story_ids=["S-2"])
    assert command.argv[6:] == (
        "--product",
        "p",
        "--story-id",
        "S-2",
        "--model",
        "llama3:8b",
    )


def test_threat_prompts_dir():
    folder = HOME / ".secreqgen/prompt_sets/threats/mine"
    command = threat(prompts_dir=folder)
    assert command.argv[-2:] == ("--prompts-dir", str(folder))
    assert command.text.endswith("--prompts-dir ~/.secreqgen/prompt_sets/threats/mine")
    assert "--prompts-dir" not in threat().argv
    # The folder comes before --other-formats, as the other paths are written.
    command = threat(prompts_dir=Path("/data/my prompts"), other_formats=True)
    assert command.argv[-3:] == ("--prompts-dir", "/data/my prompts", "--other-formats")
    assert command.text.endswith("--prompts-dir '/data/my prompts' --other-formats")
    # A folder that starts with a dash cannot be read as an option.
    assert threat(prompts_dir=Path("-p")).argv[-1] == "./-p"


def test_requirements_default_command():
    command = requirements()
    assert command.env == DEFAULT_ENV
    assert command.argv == (
        "secreqgen",
        "generate",
        "requirements",
        "/data/stories.json",
        "--docs",
        "/data/docs",
        "--model",
        "llama3:8b",
    )
    assert not hasattr(command, "differences")


def test_requirements_model_is_always_an_option_never_a_variable():
    for model in (
        "gemma4:26b",
        "llama3:8b",
        LLMConfig.model_fields["model_name"].default,
    ):
        command = requirements(model=model)
        assert command.env == DEFAULT_ENV
        assert command.argv[-2:] == ("--model", model)
        assert command.text.endswith(f"--model {model}")
        assert "SECREQGEN_LLM_MODEL_NAME" not in command.text
    assert requirements(model="  m1 ").argv[-2:] == ("--model", "m1")
    # An empty model is shown as it is: the tool refuses it, as the run does.
    assert requirements(model="").argv[-2:] == ("--model", "")
    assert requirements(model="").text.endswith("--model ''")


def test_requirements_other_options():
    command = requirements(
        system_context=HOME / "notes/context.yaml",
        product="keycloak",
        default_product="demo",
        other_formats=True,
    )
    assert command.argv[4:] == (
        "--docs",
        "/data/docs",
        "--product",
        "keycloak",
        "--model",
        "llama3:8b",
        "--context",
        "/home/tester/notes/context.yaml",
        "--other-formats",
    )
    assert "--context ~/notes/context.yaml" in command.text
    assert "--product" not in requirements(product="demo", default_product="demo").argv


def test_requirements_prompts_dir():
    folder = HOME / ".secreqgen/prompt_sets/requirements/mine"
    command = requirements(prompts_dir=folder, other_formats=True)
    assert command.argv[-3:] == ("--prompts-dir", str(folder), "--other-formats")
    assert "--prompts-dir ~/.secreqgen/prompt_sets/requirements/mine" in command.text
    assert "--prompts-dir" not in requirements().argv
    command = requirements(
        system_context=HOME / "c.yaml", prompts_dir=Path("/p/q"), story_ids=["a"]
    )
    assert command.argv[6:] == (
        "--story-id",
        "a",
        "--model",
        "llama3:8b",
        "--context",
        "/home/tester/c.yaml",
        "--prompts-dir",
        "/p/q",
    )


def test_context_command():
    command = context()
    assert command.argv == (
        "secreqgen",
        "generate",
        "context",
        "/data/code",
        "--model",
        TESTED_WRITER,
    )
    command = context(writer_model="m1", product="Shop")
    assert command.argv[3:] == ("/data/code", "--model", "m1", "--product", "Shop")
    assert "--product" not in context(product="  ").argv
    # No name: the model that the tool takes, written out.
    assert context(writer_model="").argv[-2:] == ("--model", TESTED_WRITER)


def test_about_command():
    command = about()
    assert command.argv == (
        "secreqgen",
        "generate",
        "about",
        "/data/code",
        "--model",
        TESTED_WRITER,
    )
    assert command.text == (
        f"secreqgen generate about /data/code --model {TESTED_WRITER}"
    )
    command = about(writer_model="m1", product="Shop")
    assert command.argv[3:] == ("/data/code", "--model", "m1", "--product", "Shop")
    assert "--product" not in about(product="  ").argv
    assert about(writer_model="").argv[-2:] == ("--model", TESTED_WRITER)
    assert about(writer_model="  ").argv[-2:] == ("--model", TESTED_WRITER)
    assert about(folder=HOME / "code").text == (
        f"secreqgen generate about ~/code --model {TESTED_WRITER}"
    )


def test_about_and_context_commands_differ_only_in_the_name():
    for changes in (
        {},
        {"writer_model": "m1", "product": "Shop"},
        {"base_url": "http://gpu:11434", "folder": Path("-x")},
    ):
        one, two = about(**changes), context(**changes)
        assert one.env == two.env
        assert one.argv[:2] == two.argv[:2] == ("secreqgen", "generate")
        assert (one.argv[2], two.argv[2]) == ("about", "context")
        assert one.argv[3:] == two.argv[3:]


def test_the_address_of_ollama_is_in_front_only_when_it_is_not_the_default():
    for command in (
        threat(base_url="http://gpu:11434"),
        requirements(base_url="http://gpu:11434"),
        context(base_url="http://gpu:11434"),
        about(base_url="http://gpu:11434"),
    ):
        assert command.env == (("SECREQGEN_LLM_BASE_URL", "http://gpu:11434"),)
        assert command.text.startswith("SECREQGEN_LLM_BASE_URL=http://gpu:11434 ")
    # The default address: no variable. The tool has the same default.
    for command in (threat(), requirements(), context(), about()):
        assert command.env == ()
        assert command.text.startswith("secreqgen generate ")


def test_the_only_variable_is_the_address_of_ollama():
    """No builder puts a model into a variable: the models are options."""
    for command in (
        threat(base_url="http://gpu:1", writer_model="m1"),
        requirements(model="gemma4:26b", base_url="http://gpu:1"),
        context(base_url="http://gpu:1", writer_model="m1"),
        about(base_url="http://gpu:1", writer_model="m1"),
    ):
        assert [name for name, _ in command.env] == ["SECREQGEN_LLM_BASE_URL"]
    assert not hasattr(cmds, "MODEL_VARIABLE")
    assert not hasattr(cmds, "STORIES_SUBSET")
    assert not hasattr(cmds, "PROMPT_SET")


def test_the_variable_wins_over_ollama_host(monkeypatch):
    """The proof for the address: the client takes the settings, not OLLAMA_HOST."""
    monkeypatch.setenv("OLLAMA_HOST", "http://from-ollama-host:1")
    monkeypatch.setenv("SECREQGEN_LLM_BASE_URL", "http://from-settings:2")
    client = build_ollama_client(get_config(), tested_setup=True)
    assert client.base_url == "http://from-settings:2"
    # Only OLLAMA_HOST: the settings have their default and it wins.
    monkeypatch.delenv("SECREQGEN_LLM_BASE_URL")
    client = build_ollama_client(get_config(), tested_setup=True)
    assert client.base_url == DEFAULT_URL
    assert cmds.BASE_URL_VARIABLE == "SECREQGEN_LLM_BASE_URL"


def test_the_default_address_and_model_are_the_ones_of_the_settings():
    assert LLMConfig.model_fields["base_url"].default == DEFAULT_URL
    assert cmds._settings_default("model_name") == "llama3:8b"


def test_tilde_rule():
    home = Path("/home/tester")
    shown = cmds._shell_path
    assert shown(home / "a/b.txt", home) == "~/a/b.txt"
    assert shown(home / "A_z-9.x/y", home) == "~/A_z-9.x/y"
    # A rest with another character: the whole path, quoted when it needs it.
    assert shown(home / "my docs/x", home) == "'/home/tester/my docs/x'"
    assert shown(home / "ünï", home) == "'/home/tester/ünï'"
    assert shown(home / "it's", home) == shlex.quote("/home/tester/it's")
    assert shown(home / "a$b", home) == "'/home/tester/a$b'"
    # Not below home, or the home itself.
    assert shown(Path("/data/x"), home) == "/data/x"
    assert shown(Path("/home/tester2/x"), home) == "/home/tester2/x"
    assert shown(home, home) == "/home/tester"
    assert shown(Path("relative/x"), home) == "relative/x"
    # A home that is the root gives no ~.
    assert shown(Path("/x"), Path("/")) == "/x"


def test_values_are_quoted_for_a_shell():
    command = threat(
        product="My Product; rm -rf /",
        writer_model="it's",
        about_file=Path("/data/a b/$HOME.txt"),
    )
    words = shlex.split(command.text)
    assert words[: len(command.env)] == [f"{n}={v}" for n, v in command.env]
    assert words[len(command.env) :] == list(command.argv)
    assert "'My Product; rm -rf /'" in command.text
    assert command.text.count("'") >= 8


def test_a_path_that_starts_with_a_dash_cannot_be_read_as_an_option():
    command = threat(stories_file=Path("-s.json"), docs=Path("-docs"))
    assert command.argv[3] == "./-s.json"
    assert parse(command)["input_file"] == "./-s.json"
    assert parse(command)["docs"] == "./-docs"
    assert command.text == (
        "secreqgen generate threats ./-s.json --docs ./-docs "
        f"--model {TESTED_WRITER} --checker-model {TESTED_CHECKER}"
    )


def test_the_module_loads_no_heavy_package():
    found = forbidden_loaded(
        ["secreqgen.application.commands"],
        [
            "chromadb",
            "torch",
            "sentence_transformers",
            "secreqgen.retrieval",
            "secreqgen.ingestion",
            "typer",
            "rich",
            "secreqgen.cli",
        ],
    )
    assert found == []


def test_the_module_reads_no_file(tmp_path, monkeypatch):
    # The paths do not exist. The builders must still work.
    command = threat(stories_file=tmp_path / "missing.json", docs=tmp_path / "no")
    assert command.argv[3] == str(tmp_path / "missing.json")
    assert not (tmp_path / "missing.json").exists()


# ---------------------------------------------------------------------------
# A generator of cases
# ---------------------------------------------------------------------------

SPECIAL_PATHS = [
    "/data/stories.json",
    "/data/with space/s.json",
    "/data/it's/s.json",
    '/data/say "hi"/s.json',
    "/data/$HOME/x",
    "/data/a;b/s.json",
    "/data/ünï/ö.json",
    "-leading/x.json",
    "-x",
    "/data/back`tick`/f",
    "/data/a&b|c>d/f",
    "/data/(par)/x",
    "/data/glob*?[x]/y",
    "/data/~tilde/x",
    "/data/#hash/x",
    "/data/!bang/x",
    "/data/new\nline/x",
    "/data/tab\tx",
    "/data/Βιο/τεστ.json",
    "relative/path.json",
    "/home/tester/docs",
    "/home/tester/my docs/x",
    "/home/tester/a/b-c_d.e/f",
    "/home/tester/.secreqgen/runs/r1/stories.json",
    "/home/tester/ünï",
    "/home/tester/$x",
    "/home/tester/-dash",
    "/home/tester",
    "/home/tester2/x",
    "/home/tester/../etc",
]
SPECIAL_TEXTS = [
    "keycloak",
    "My Product",
    "x;y",
    "-dash",
    "--no-check",
    "--help",
    "it's",
    '"quoted"',
    "ünï",
    "$HOME",
    "`id`",
    "",
    " lead",
    "a=b",
    "~/x",
    "demo",
    "m:1",
    "gemma4:26b",
    "qwen3.8:27b",
    "llama3:8b",
    "a b c",
]
URLS = [
    DEFAULT_URL,
    "http://127.0.0.1:11434",
    "http://gpu box:11434",
    "https://example.org:8443/x?a=b&c=d",
    "http://[::1]:1",
    "http://h;ost:1",
]
STORY_IDS = ["US-1", "US-2", "US-3", "A/1", "-x", "it's", "KC-S2-004", "a b"]


def pick_path(rng: random.Random) -> Path:
    return Path(rng.choice(SPECIAL_PATHS))


def maybe(rng: random.Random, value, chance: float = 0.5):
    return value if rng.random() < chance else None


def pick_story_ids(rng: random.Random):
    """Return the number of stories of the file and a choice of 0 to 5 ids.

    The choice can be None, empty, all stories in another order, or some ids
    with repeats. The ids of the choice are ids of the file.
    """
    total = rng.randint(1, 8)
    ids = [f"S-{n}" for n in range(total)]
    kind = rng.choice(["none", "empty", "all", "some", "some", "repeat"])
    if kind == "none":
        return total, None
    if kind == "empty":
        return total, []
    if kind == "all":
        return total, rng.sample(ids, len(ids))
    if kind == "repeat":
        return total, rng.choices(ids, k=rng.randint(2, 5))
    return total, rng.sample(ids, rng.randint(1, min(5, total)))


def threat_case(rng: random.Random) -> dict:
    total, story_ids = pick_story_ids(rng)
    while True:
        writer = rng.choice(SPECIAL_TEXTS + [TESTED_WRITER, TESTED_CHECKER] * 3)
        checker = maybe(
            rng, rng.choice(SPECIAL_TEXTS + [TESTED_WRITER, TESTED_CHECKER] * 3), 0.7
        )
        check = rng.random() < 0.7
        # The command line refuses a checker that is the writer. The GUI does too.
        if check and checker and _same_model(checker, writer.strip() or TESTED_WRITER):
            continue
        break
    return dict(
        stories_file=pick_path(rng),
        docs=pick_path(rng),
        product=maybe(rng, rng.choice(SPECIAL_TEXTS)),
        default_product=maybe(rng, rng.choice(SPECIAL_TEXTS), 0.6),
        total_stories=total,
        story_ids=story_ids,
        writer_model=writer,
        check=check,
        checker_model=checker,
        labels=rng.random() < 0.6,
        about_file=maybe(rng, pick_path(rng), 0.4),
        prompts_dir=maybe(rng, pick_path(rng), 0.3),
        other_formats=rng.random() < 0.3,
        base_url=rng.choice(URLS),
        home=HOME,
    )


def requirements_case(rng: random.Random) -> dict:
    total, story_ids = pick_story_ids(rng)
    return dict(
        stories_file=pick_path(rng),
        docs=pick_path(rng),
        product=maybe(rng, rng.choice(SPECIAL_TEXTS)),
        default_product=maybe(rng, rng.choice(SPECIAL_TEXTS), 0.6),
        total_stories=total,
        story_ids=story_ids,
        model=rng.choice([m for m in SPECIAL_TEXTS if m.strip()] + ["llama3:8b"] * 4),
        system_context=maybe(rng, pick_path(rng), 0.4),
        prompts_dir=maybe(rng, pick_path(rng), 0.3),
        other_formats=rng.random() < 0.3,
        base_url=rng.choice(URLS),
        home=HOME,
    )


def context_case(rng: random.Random) -> dict:
    return dict(
        folder=pick_path(rng),
        product=maybe(rng, rng.choice(SPECIAL_TEXTS)),
        writer_model=rng.choice(SPECIAL_TEXTS + [TESTED_WRITER] * 4),
        base_url=rng.choice(URLS),
        home=HOME,
    )


CASES = 300


def cases(make, seed: int):
    rng = random.Random(seed)
    return [make(rng) for _ in range(CASES)]


THREAT_CASES = cases(threat_case, 20261118)
REQUIREMENTS_CASES = cases(requirements_case, 20261119)
CONTEXT_CASES = cases(context_case, 20261120)
ABOUT_CASES = cases(context_case, 20261121)


def effective_llm(env: tuple[tuple[str, str], ...], monkeypatch) -> LLMConfig:
    """Return the LLM settings that the tool has when the variables are set."""
    for name in list(os.environ):
        if name.startswith("SECREQGEN_LLM_"):
            monkeypatch.delenv(name)
    for name, value in env:
        monkeypatch.setenv(name, value)
    return get_config().llm


def same_path(parsed: str | Path | None, given: Path | None) -> bool:
    if given is None:
        return parsed is None
    return parsed is not None and Path(parsed) == Path(given)


# ---------------------------------------------------------------------------
# 2. The command parses
# ---------------------------------------------------------------------------


def expected_story_ids(case: dict) -> list[str]:
    """The ids that the command must carry: none for no choice or all stories."""
    ids = list(dict.fromkeys(case["story_ids"] or ()))
    return ids if ids and len(ids) < case["total_stories"] else []


def test_threat_commands_parse(monkeypatch):
    for number, case in enumerate(THREAT_CASES):
        command = threat_command(**case)
        parsed = parse(command)
        where = f"case {number}: {case}\n{command.text}"
        assert same_path(parsed["input_file"], case["stories_file"]), where
        assert same_path(parsed["docs"], case["docs"]), where
        # The product that the run uses.
        used = (
            case["product"] if case["product"] is not None else case["default_product"]
        )
        got = (
            parsed["product"]
            if parsed["product"] is not None
            else case["default_product"]
        )
        assert got == used, where
        # The stories: the list that went in, with no repeat, in the order given.
        assert list(parsed["story_id"] or []) == expected_story_ids(case), where
        assert same_path(parsed["prompts_dir"], case["prompts_dir"]), where
        # The models that the run uses.
        writer, checker = choose_models(
            parsed["writer_model"], None, parsed["checker_model"], parsed["check"]
        )
        assert parsed["check"] is case["check"], where
        want_writer = case["writer_model"].strip() or TESTED_WRITER
        assert writer == want_writer, where
        # Both models are in the command, so that no variable can change them.
        assert parsed["writer_model"] == want_writer, where
        assert (parsed["checker_model"] is not None) is case["check"], where
        if case["check"]:
            if case["checker_model"] and case["checker_model"].strip():
                assert checker == case["checker_model"].strip(), where
            else:
                assert (
                    checker == choose_models(case["writer_model"], None, None)[1]
                ), where
        else:
            assert checker is None, where
        # The other options.
        assert parsed["labels"] is case["labels"], where
        assert same_path(parsed["about_file"], case["about_file"]), where
        assert parsed["other_formats"] is case["other_formats"], where
        # The variables: the tool must have the address of the GUI.
        llm = effective_llm(command.env, monkeypatch)
        assert llm.base_url == case["base_url"], where
        assert set(dict(command.env)) <= {"SECREQGEN_LLM_BASE_URL"}, where


def test_requirements_commands_parse(monkeypatch):
    for number, case in enumerate(REQUIREMENTS_CASES):
        command = requirements_command(**case)
        parsed = parse(command)
        where = f"case {number}: {case}\n{command.text}"
        assert same_path(parsed["input_file"], case["stories_file"]), where
        assert same_path(parsed["docs"], case["docs"]), where
        used = (
            case["product"] if case["product"] is not None else case["default_product"]
        )
        got = (
            parsed["product"]
            if parsed["product"] is not None
            else case["default_product"]
        )
        assert got == used, where
        assert same_path(parsed["context"], case["system_context"]), where
        assert parsed["other_formats"] is case["other_formats"], where
        assert parsed["baseline"] is False, where
        assert list(parsed["story_id"] or []) == expected_story_ids(case), where
        assert same_path(parsed["prompts_dir"], case["prompts_dir"]), where
        # The model is an option. No variable carries it.
        assert parsed["model"] == case["model"].strip(), where
        assert set(dict(command.env)) <= {"SECREQGEN_LLM_BASE_URL"}, where
        llm = effective_llm(command.env, monkeypatch)
        assert llm.base_url == case["base_url"], where


def test_context_commands_parse(monkeypatch):
    for number, case in enumerate(CONTEXT_CASES):
        command = context_command(**case)
        parsed = parse(command)
        where = f"case {number}: {case}\n{command.text}"
        assert same_path(parsed["folder"], case["folder"]), where
        from secreqgen.application.system_context import resolve_context_model

        llm = effective_llm(command.env, monkeypatch)
        assert llm.base_url == case["base_url"], where
        name = resolve_context_model(
            type("Cfg", (), {"llm": llm})(), parsed["writer_model"]
        )
        assert name == (case["writer_model"].strip() or TESTED_WRITER), where
        assert parsed["writer_model"] == name, where
        product = case["product"]
        want = product if product is not None and product.strip() else None
        assert parsed["product"] == want, where
        assert command.env == expected_env(case["base_url"]), where


def test_about_commands_parse(monkeypatch):
    for number, case in enumerate(ABOUT_CASES):
        command = about_command(**case)
        parsed = parse(command)
        where = f"case {number}: {case}\n{command.text}"
        assert same_path(parsed["source"], case["folder"]), where
        from secreqgen.application.system_context import resolve_context_model

        llm = effective_llm(command.env, monkeypatch)
        assert llm.base_url == case["base_url"], where
        name = resolve_context_model(
            type("Cfg", (), {"llm": llm})(), parsed["writer_model"]
        )
        assert name == (case["writer_model"].strip() or TESTED_WRITER), where
        assert parsed["writer_model"] == name, where
        product = case["product"]
        want = product if product is not None and product.strip() else None
        assert parsed["product"] == want, where
        assert parsed["output"] is None, where
        assert command.env == expected_env(case["base_url"]), where


# ---------------------------------------------------------------------------
# 3. The text is the argv
# ---------------------------------------------------------------------------


def split_text(command: Command, home: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Read the text as a shell does, and expand the ~ by hand."""
    tokens = shlex.split(command.text)
    env: list[tuple[str, str]] = []
    while tokens and "=" in tokens[0] and not tokens[0].startswith("secreqgen"):
        name, _, value = tokens.pop(0).partition("=")
        env.append((name, value))
    return env, tokens


def expand_by_hand(tokens: list[str], argv: tuple[str, ...], home: Path) -> list[str]:
    result = []
    for token, real in zip(tokens, argv, strict=False):
        if token != real and token.startswith("~/"):
            token = str(home) + token[1:]
        result.append(token)
    return result


@pytest.mark.parametrize(
    ("make", "build", "name"),
    [
        (THREAT_CASES, threat_command, "threats"),
        (REQUIREMENTS_CASES, requirements_command, "requirements"),
        (CONTEXT_CASES, context_command, "context"),
        (ABOUT_CASES, about_command, "about"),
    ],
)
def test_the_text_is_the_argv(make, build, name):
    for number, case in enumerate(make):
        command = build(**case)
        env, tokens = split_text(command, HOME)
        where = f"case {number} ({name}): {command.text!r}"
        assert tuple(env) == command.env, where
        assert len(tokens) == len(command.argv), where
        assert tuple(expand_by_hand(tokens, command.argv, HOME)) == command.argv, where
        # A ~ stands only for a path below home that has safe characters.
        for token, real in zip(tokens, command.argv, strict=True):
            if token != real:
                assert token.startswith("~/"), where
                assert real == str(HOME) + token[1:], where
                assert cmds._TILDE_REST.fullmatch(token[2:]), where
        assert "\n" not in command.text or "'" in command.text, where


SHELL = r"""
secreqgen() {
  printf '%s\0' "${SECREQGEN_LLM_BASE_URL-<unset>}"
  printf '%s\0' "${SECREQGEN_LLM_MODEL_NAME-<unset>}"
  printf '%s\0' "$@"
}
"""


@pytest.mark.parametrize(
    ("make", "build"),
    [
        (THREAT_CASES, threat_command),
        (REQUIREMENTS_CASES, requirements_command),
        (CONTEXT_CASES, context_command),
        (ABOUT_CASES, about_command),
    ],
)
def test_a_real_shell_reads_the_text_as_the_argv(make, build, tmp_path):
    """The proof for the quoting and the ~: /bin/sh gives the argv back."""
    for number, case in enumerate(make[:60]):
        command = build(**case)
        env = {"HOME": str(HOME), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        done = subprocess.run(
            ["/bin/sh", "-c", SHELL + command.text],
            env=env,
            capture_output=True,
            timeout=20,
            cwd=tmp_path,
        )
        where = f"case {number}: {command.text!r}\n{done.stderr!r}"
        assert done.returncode == 0, where
        parts = done.stdout.decode("utf-8").split("\0")[:-1]
        base_url = dict(command.env).get("SECREQGEN_LLM_BASE_URL", "<unset>")
        model = dict(command.env).get("SECREQGEN_LLM_MODEL_NAME", "<unset>")
        assert parts[0] == base_url, where
        assert parts[1] == model, where
        assert tuple(["secreqgen", *parts[2:]]) == command.argv, where


ZSH = Path("/bin/zsh")

# zsh reads a word that starts with = as the name of a program (=ls becomes
# /bin/ls, and =x stops the command with "x not found").
EQUALS_COMMANDS = [
    lambda: threat(product="=x", default_product=None),
    lambda: threat(writer_model="=m"),
    lambda: threat(checker_model="=c"),
    lambda: threat(story_ids=["=1"], total_stories=3),
    lambda: threat(stories_file=Path("=s.json"), docs=Path("=docs")),
    lambda: threat(about_file=Path("=about.txt")),
    lambda: requirements(model="=m"),
    lambda: requirements(product="=", default_product=None),
    lambda: requirements(system_context=Path("=c.yaml")),
    lambda: requirements(base_url="=u"),
    lambda: threat(prompts_dir=Path("=p")),
    lambda: requirements(prompts_dir=Path("=p"), story_ids=["=a", "=b"]),
    lambda: context(folder=Path("=code"), product="=p", writer_model="=m"),
    lambda: about(folder=Path("=code"), product="=p", writer_model="=m"),
]


def shell_words(command: Command) -> list[str]:
    """Return what the function of SHELL must print for a command."""
    env = dict(command.env)
    return [
        env.get("SECREQGEN_LLM_BASE_URL", "<unset>"),
        env.get("SECREQGEN_LLM_MODEL_NAME", "<unset>"),
        *command.argv[1:],
    ]


def zsh_reads(command: Command, folder: Path) -> tuple[int, bytes, list[str]]:
    """Give the text of a command to zsh. Return the code, stderr and the words."""
    done = subprocess.run(
        [str(ZSH), "-f", "-c", SHELL + command.text],
        env={"HOME": str(HOME), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=20,
        cwd=folder,
    )
    return done.returncode, done.stderr, done.stdout.decode("utf-8").split("\0")[:-1]


def test_a_value_that_starts_with_an_equals_sign_gets_quotes():
    assert cmds._quote("=x") == "'=x'"
    assert cmds._quote("=") == "'='"
    assert cmds._quote("a=b") == "a=b"
    assert cmds._quote("it's") == shlex.quote("it's")
    command = requirements(model="=m", product="=p", default_product=None)
    assert "--model '=m'" in command.text
    assert "--product '=p'" in command.text
    assert command.argv[-4:] == ("--product", "=p", "--model", "=m")


@pytest.mark.skipif(not ZSH.exists(), reason="zsh is not on this computer")
def test_zsh_reads_a_value_that_starts_with_an_equals_sign(tmp_path):
    for number, make in enumerate(EQUALS_COMMANDS):
        command = make()
        code, stderr, parts = zsh_reads(command, tmp_path)
        where = f"case {number}: {command.text!r}\n{stderr!r}"
        assert code == 0 and not stderr, where
        assert shell_words(command) == parts, where


@pytest.mark.skipif(not ZSH.exists(), reason="zsh is not on this computer")
@pytest.mark.parametrize(
    ("make", "build"),
    [
        (THREAT_CASES, threat_command),
        (REQUIREMENTS_CASES, requirements_command),
        (CONTEXT_CASES, context_command),
        (ABOUT_CASES, about_command),
    ],
)
def test_zsh_reads_the_text_as_the_argv(make, build, tmp_path):
    """zsh is the shell of macOS. It must give the same argv as /bin/sh."""
    for number, case in enumerate(make[:60]):
        command = build(**case)
        code, stderr, parts = zsh_reads(command, tmp_path)
        where = f"case {number}: {command.text!r}\n{stderr!r}"
        assert code == 0 and not stderr, where
        assert shell_words(command) == parts, where


# ---------------------------------------------------------------------------
# 4. The same run
# ---------------------------------------------------------------------------


def threat_factory():
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


def write_inputs(ids=("US-1", "US-2", "US-3")) -> tuple[Path, Path]:
    """Write the stories file and the documentation folder in the current folder."""
    stories = Path(g.threat_stories_file(tuple(ids))).resolve()
    docs = Path(g.docs_folder()).resolve()
    return stories, docs


def marked_prompt_set(kind: str, root: Path, name: str = "mine") -> Path:
    """Copy the tested prompt set and change the text of each file.

    Each file gets one line at its end: MARKER-<name of the file>. A run that
    reads the folder must show the line in the prompts that the model gets.
    """
    from secreqgen.application.prompt_sets import FILE_NAMES, copy_prompt_set

    folder = copy_prompt_set(kind, name, root)
    for filename in FILE_NAMES[kind]:
        path = folder / filename
        marker = "MARKER-" + filename.removesuffix(".txt")
        path.write_text(
            path.read_text(encoding="utf-8").rstrip("\n") + "\n" + marker + "\n",
            encoding="utf-8",
        )
    return folder


def marker_of(filename: str) -> str:
    return "MARKER-" + filename.removesuffix(".txt")


def run_command(
    command: Command,
    monkeypatch,
    factory,
    extra: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> list[str]:
    """Run the built command through the command line. Return the prompts.

    extra: more arguments (for example --output). env: the variables of the
    terminal. A variable in front of the command wins over the terminal, as in
    a shell.
    """
    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    g.PROMPTS.clear()
    result = typer.testing.CliRunner().invoke(
        secreqgen.cli.app,
        [*command.argv[1:], *extra],
        env={**(env or {}), **dict(command.env)},
    )
    assert result.exit_code == 0, result.output
    return list(g.PROMPTS)


def gui_cfg(base_url: str):
    """The settings of the GUI run (make_config of the GUI)."""
    cfg = get_config()
    cfg.llm.base_url = base_url
    return cfg


def run_gui_threats(values: dict, tmp_path: Path, factory) -> list[str]:
    """Run the functions that the GUI calls, with the options of jobs.py.

    The files of the run go to tmp_path / "gui_out".
    """
    from secreqgen.application.threats import ThreatOptions, run_threat_lists

    about = None
    if values["about_file"] is not None:
        about = Path(values["about_file"]).read_text(encoding="utf-8")
    story_ids = values["story_ids"]
    options = ThreatOptions(
        input_file=values["stories_file"],
        docs=values["docs"],
        product=values["product"],
        story_ids=tuple(story_ids) if story_ids else None,
        labels=values["labels"],
        writer_model=values["writer_model"],
        check=values["check"],
        checker_model=values["checker_model"] if values["check"] else None,
        about=about,
        output=tmp_path / "gui_out",
        strict_input=True,
        keep_finished=True,
        prompts_dir=values["prompts_dir"],
        other_formats=values["other_formats"],
        docs_cache=tmp_path / "docs_cache",
    )
    g.PROMPTS.clear()
    run = run_threat_lists(
        options, cfg=gui_cfg(values["base_url"]), make_client=factory
    )
    assert run.aborted is None and not run.failures
    assert run.lists
    return list(g.PROMPTS)


def run_gui_requirements(values: dict, tmp_path: Path, factory) -> list[str]:
    from secreqgen.application.requirements import (
        RequirementsOptions,
        run_requirements,
    )

    context_text = None
    if values["system_context"] is not None:
        context_text = Path(values["system_context"]).read_text(encoding="utf-8")
    story_ids = values["story_ids"]
    options = RequirementsOptions(
        input_file=values["stories_file"],
        docs=values["docs"],
        product=values["product"],
        story_ids=tuple(story_ids) if story_ids else None,
        model=values["model"],
        system_context=context_text,
        output_format="json",
        strict_input=True,
        keep_finished=True,
        prompts_dir=values["prompts_dir"],
        other_formats=values["other_formats"],
        docs_cache=tmp_path / "docs_cache",
    )
    g.PROMPTS.clear()
    run = run_requirements(
        options, cfg=gui_cfg(values["base_url"]), make_client=factory
    )
    assert run.aborted is None and not run.failures
    assert run.results
    return list(g.PROMPTS)


def threat_values(stories: Path, docs: Path, **changes) -> dict:
    values = dict(
        stories_file=stories,
        docs=docs,
        product=None,
        default_product="demo",
        total_stories=3,
        story_ids=None,
        writer_model=TESTED_WRITER,
        check=True,
        checker_model=TESTED_CHECKER,
        labels=True,
        about_file=None,
        prompts_dir=None,
        other_formats=False,
        base_url=DEFAULT_URL,
        home=Path.home(),
    )
    values.update(changes)
    return values


def requirements_values(stories: Path, docs: Path, **changes) -> dict:
    values = dict(
        stories_file=stories,
        docs=docs,
        product=None,
        default_product="demo",
        total_stories=3,
        story_ids=None,
        model="gemma4:26b",
        system_context=None,
        prompts_dir=None,
        other_formats=False,
        base_url=DEFAULT_URL,
        home=Path.home(),
    )
    values.update(changes)
    return values


def threat_option_sets(about: Path, prompts: Path) -> list[dict]:
    return [
        {},  # the tested defaults
        {"writer_model": "one-writer"},  # the default checker follows
        {"check": False, "labels": False},
        {"checker_model": "other-checker", "about_file": about},
        {"story_ids": ["US-2"]},  # one story
        {"product": "keycloak", "base_url": "http://gpu:11434"},
        {
            "story_ids": ["US-3", "US-1", "US-2"],  # all stories, another order
            "writer_model": TESTED_CHECKER,
            "checker_model": TESTED_WRITER,  # the default checker of that writer
        },
        {"writer_model": TESTED_CHECKER, "checker_model": "other-checker"},
        {"story_ids": ["US-1", "US-3"]},  # 2 of 3 stories
        {"story_ids": ["US-3"]},  # the last story only
        {"story_ids": ["US-3", "US-1"]},  # ids in another order than the file
        {"story_ids": ["US-2", "US-2"]},  # an id two times
        {"prompts_dir": prompts},  # a prompt folder of the person
        {"prompts_dir": prompts, "check": False, "labels": False},
        {
            # all of these together
            "story_ids": ["US-3", "US-1"],
            "prompts_dir": prompts,
            "writer_model": "one-writer",
            "checker_model": "other-checker",
            "about_file": about,
            "product": "keycloak",
            "base_url": "http://gpu:11434",
        },
    ]


THREAT_SETS = 15


@pytest.mark.parametrize("index", range(THREAT_SETS))
def test_the_same_run_threats(index, tmp_path, monkeypatch):
    stories, docs = write_inputs()
    about = Path("about.txt").resolve()
    about.write_text(
        "Demo is a web shop.\n\nIt has   sessions\nand a cart.\n", encoding="utf-8"
    )
    prompts = marked_prompt_set("threats", tmp_path / "sets")
    changes = threat_option_sets(about, prompts)[index]
    values = threat_values(stories, docs, **changes)
    command = threat_command(**values)
    by_command = run_command(command, monkeypatch, threat_factory())
    by_gui = run_gui_threats(values, tmp_path, threat_factory())
    assert by_command
    assert by_command == by_gui, command.text
    # The number of model calls shows the picked stories: no run of all stories.
    ids = changes.get("story_ids")
    if ids is not None:
        picked = len(set(ids))
        full = run_command(
            threat_command(**threat_values(stories, docs)),
            monkeypatch,
            threat_factory(),
        )
        assert len(by_command) == len(full) * picked // 3


def requirements_option_sets(context_file: Path, prompts: Path) -> list[dict]:
    return [
        {},  # the model of the GUI
        {"model": "llama3:8b"},  # the default model of the settings
        {"system_context": context_file, "product": "keycloak"},
        {"story_ids": ["US-1", "US-2", "US-3"], "base_url": "http://gpu:11434"},
        {"story_ids": ["US-1", "US-3"]},  # 2 of 3 stories
        {"story_ids": ["US-2"]},  # 1 of 3 stories
        {"story_ids": ["US-3"]},  # the last story only
        {"story_ids": ["US-3", "US-1"]},  # ids in another order than the file
        {"story_ids": ["US-1", "US-1"]},  # an id two times
        {"model": "other-model:7b"},  # a model that is not the default
        {"prompts_dir": prompts},  # a prompt folder of the person
        {
            # all of these together
            "story_ids": ["US-3", "US-2"],
            "prompts_dir": prompts,
            "model": "other-model:7b",
            "system_context": context_file,
            "product": "keycloak",
            "base_url": "http://gpu:11434",
        },
    ]


REQUIREMENTS_SETS = 12


@pytest.mark.parametrize("index", range(REQUIREMENTS_SETS))
def test_the_same_run_requirements(index, tmp_path, monkeypatch):
    stories, docs = write_inputs()
    context_file = Path("ctx.yaml").resolve()
    context_file.write_text("name: Demo\nstack: Ruby\n", encoding="utf-8")
    prompts = marked_prompt_set("requirements", tmp_path / "sets")
    changes = requirements_option_sets(context_file, prompts)[index]
    values = requirements_values(stories, docs, **changes)
    command = requirements_command(**values)
    by_command = run_command(command, monkeypatch, requirements_factory())
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    assert by_command
    assert by_command == by_gui, command.text
    # One model call for each story that the run writes.
    ids = changes.get("story_ids")
    assert len(by_command) == (len(set(ids)) if ids is not None else 3)


def test_two_stories_of_three_run_two_stories_on_both_paths(tmp_path, monkeypatch):
    """The two old tests of a difference are now tests of the same run.

    Before, the command line could not pick two stories: it ran all three and
    the GUI ran two. Now the command carries --story-id for each story.
    """
    stories, docs = write_inputs()
    values = requirements_values(stories, docs, story_ids=["US-1", "US-3"])
    command = requirements_command(**values)
    assert story_options(command) == ["US-1", "US-3"]
    by_command = run_command(command, monkeypatch, requirements_factory())
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    assert len(by_command) == len(by_gui) == 2
    assert by_command == by_gui
    # The two stories are the two that were named, not the first two.
    assert any("US-3" in text or "Log" in text for text in by_command)


def test_a_prompt_folder_of_the_person_is_the_same_run(tmp_path, monkeypatch):
    """The old test said that the command line cannot do this. It can now.

    Every prompt file has a changed text. The text must be in the prompts that
    the models get, on both paths, and the two paths must give equal prompts.
    """
    from secreqgen.application.prompt_sets import FILE_NAMES

    stories, docs = write_inputs()
    prompts = marked_prompt_set("threats", tmp_path / "sets")
    values = threat_values(stories, docs, prompts_dir=prompts)
    command = threat_command(**values)
    assert command.argv[-2:] == ("--prompts-dir", str(prompts))
    by_command = run_command(command, monkeypatch, threat_factory())
    by_gui = run_gui_threats(values, tmp_path, threat_factory())
    assert by_command == by_gui
    joined = "\n".join(by_command)
    for filename in FILE_NAMES["threats"]:
        assert marker_of(filename) in joined, filename
    # Without the folder the changed text is not there.
    plain = run_command(
        threat_command(**threat_values(stories, docs)), monkeypatch, threat_factory()
    )
    assert "MARKER-" not in "\n".join(plain)

    prompts = marked_prompt_set("requirements", tmp_path / "sets")
    values = requirements_values(stories, docs, prompts_dir=prompts)
    command = requirements_command(**values)
    by_command = run_command(command, monkeypatch, requirements_factory())
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    assert by_command == by_gui
    joined = "\n".join(by_command)
    # The zero-shot file is not used by a run with documentation.
    for filename in ("system_prompt.txt", "rag_user_prompt.txt"):
        assert marker_of(filename) in joined, filename
    assert marker_of("zero_shot_user_prompt.txt") not in joined


def test_the_same_run_with_other_formats(tmp_path, monkeypatch):
    stories, docs = write_inputs()
    (docs / "guide.rst").write_text(
        "Sessions\n========\n\nThe session cookie must have the Secure flag.\n",
        encoding="utf-8",
    )
    values = threat_values(stories, docs, other_formats=True)
    command = threat_command(**values)
    assert "--other-formats" in command.argv
    by_command = run_command(command, monkeypatch, threat_factory())
    by_gui = run_gui_threats(values, tmp_path, threat_factory())
    assert by_command
    assert by_command == by_gui


def test_the_same_run_prompts_are_not_empty_and_name_the_models(tmp_path, monkeypatch):
    stories, docs = write_inputs()
    values = requirements_values(stories, docs, model="gemma4:26b")
    command = requirements_command(**values)
    prompts = run_command(command, monkeypatch, requirements_factory())
    assert prompts
    assert all("gemma4:26b generate" in text for text in prompts)
    # The option --model is what puts the model into the run.
    assert command.env == DEFAULT_ENV
    assert command.argv[-2:] == ("--model", "gemma4:26b")
    assert json.dumps(prompts)


def test_threat_prompts_name_the_writer_and_the_checker(tmp_path, monkeypatch):
    stories, docs = write_inputs(("US-1",))
    values = threat_values(
        stories, docs, writer_model="writer-x", checker_model="checker-y"
    )
    prompts = run_command(threat_command(**values), monkeypatch, threat_factory())
    assert any("writer-x generate" in text for text in prompts)
    assert any("checker-y generate" in text for text in prompts)
    assert prompts == run_gui_threats(values, tmp_path, threat_factory())


def test_requirements_product_is_named_when_the_file_key_is_not_text(
    tmp_path, monkeypatch
):
    """The key project of the file is a number: the command names the product.

    The command line gives the raw key to the search. Without --product a
    number would reach the search as a number. With --product 123 it is text,
    as in the GUI run.
    """
    stories, docs = write_inputs()
    data = json.loads(stories.read_text(encoding="utf-8"))
    data["project"] = 123
    stories.write_text(json.dumps(data), encoding="utf-8")
    values = requirements_values(stories, docs, product="123", default_product=None)
    command = requirements_command(**values)
    assert "--product" in command.argv
    assert parse(command)["product"] == "123"
    by_command = run_command(command, monkeypatch, requirements_factory())
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    assert by_command
    assert by_command == by_gui


# ---------------------------------------------------------------------------
# 5. The same run in a terminal that has variables of the tool
# ---------------------------------------------------------------------------

TERMINAL = {
    "SECREQGEN_LLM_MODEL_NAME": "other:1",
    "SECREQGEN_LLM_BASE_URL": DEFAULT_URL,
}


def test_a_variable_for_the_model_in_the_terminal_changes_no_requirements_run(
    tmp_path, monkeypatch
):
    stories, docs = write_inputs()
    for changes in ({}, {"model": "llama3:8b"}, {"story_ids": ["US-3", "US-1"]}):
        values = requirements_values(stories, docs, **changes)
        command = requirements_command(**values)
        by_command = run_command(
            command, monkeypatch, requirements_factory(), env=TERMINAL
        )
        by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
        assert by_command
        assert by_command == by_gui
        assert not any("other:1" in text for text in by_command)


def test_a_variable_for_the_model_in_the_terminal_changes_no_threat_run(
    tmp_path, monkeypatch
):
    stories, docs = write_inputs()
    for changes in (
        {},
        {"writer_model": "one-writer"},
        {"check": False},
        {"story_ids": ["US-2"]},
    ):
        values = threat_values(stories, docs, **changes)
        command = threat_command(**values)
        by_command = run_command(command, monkeypatch, threat_factory(), env=TERMINAL)
        by_gui = run_gui_threats(values, tmp_path, threat_factory())
        assert by_command
        assert by_command == by_gui
        assert not any("other:1" in text for text in by_command)


def test_the_models_in_the_command_are_what_protects_the_run(tmp_path, monkeypatch):
    """The negative control: without --model the variable of the terminal wins.

    This shows that the tests above can fail. If the builder left the model
    out, a terminal with the variable would give another run.
    """
    stories, docs = write_inputs()
    values = requirements_values(stories, docs, model="gemma4:26b")
    command = requirements_command(**values)
    argv = list(command.argv)
    at = argv.index("--model")
    without = Command(env=command.env, argv=tuple(argv[:at] + argv[at + 2 :]), text="")
    by_command = run_command(without, monkeypatch, requirements_factory(), env=TERMINAL)
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    assert by_command != by_gui
    assert all("other:1 generate" in text for text in by_command)


def recording(factory, addresses: list[str]):
    """Wrap a stand-in factory. It notes the address of each client that is built."""

    def wrapped(cfg, tested_setup, model_name=None):
        addresses.append(cfg.llm.base_url)
        return factory(cfg, tested_setup, model_name)

    return wrapped


OTHER_SERVER = "http://example.invalid:1"
GUI_SERVER = "http://gui-server.invalid:2"


def address_runs(kind: str, base_url: str, tmp_path, monkeypatch, terminal):
    """Run one command and the GUI path. Return the command and the addresses.

    The addresses are the ones of the clients that each path builds.
    """
    stories, docs = write_inputs()
    if kind == "threats":
        values = threat_values(stories, docs, base_url=base_url)
        command = threat_command(**values)
        make, run_gui = threat_factory, run_gui_threats
    else:
        values = requirements_values(stories, docs, base_url=base_url)
        command = requirements_command(**values)
        make, run_gui = requirements_factory, run_gui_requirements
    by_command_at: list[str] = []
    by_command = run_command(
        command, monkeypatch, recording(make(), by_command_at), env=terminal
    )
    by_gui_at: list[str] = []
    by_gui = run_gui(values, tmp_path, recording(make(), by_gui_at))
    assert by_command
    assert by_command == by_gui
    assert by_command_at and by_gui_at
    return command, by_command_at, by_gui_at


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_the_default_address_needs_no_variable(kind, tmp_path, monkeypatch):
    """The GUI address is the default one. The command has no variable.

    A terminal that does not set the address uses the default of the tool,
    which is the same address.
    """
    command, by_command_at, by_gui_at = address_runs(
        kind, DEFAULT_URL, tmp_path, monkeypatch, terminal=None
    )
    assert command.env == ()
    assert by_command_at == by_gui_at
    assert set(by_command_at) == {DEFAULT_URL}


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_an_address_of_the_gui_that_is_not_the_default_wins_over_the_terminal(
    kind, tmp_path, monkeypatch
):
    """The GUI has its own address. The terminal has another address set.

    The command names the address of the GUI, so the clients get that address.
    """
    command, by_command_at, by_gui_at = address_runs(
        kind,
        GUI_SERVER,
        tmp_path,
        monkeypatch,
        terminal={"SECREQGEN_LLM_BASE_URL": OTHER_SERVER},
    )
    assert command.env == (("SECREQGEN_LLM_BASE_URL", GUI_SERVER),)
    assert by_command_at == by_gui_at
    assert set(by_command_at) == {GUI_SERVER}


@pytest.mark.parametrize("kind", ["threats", "requirements"])
def test_a_terminal_with_its_own_address_keeps_it_for_a_default_command(
    kind, tmp_path, monkeypatch
):
    """The known limit. The README of the GUI states it.

    The GUI address is the default one, so the command has no variable. A
    terminal that sets SECREQGEN_LLM_BASE_URL runs the command on its own
    server. The prompts are the same.
    """
    command, by_command_at, by_gui_at = address_runs(
        kind,
        DEFAULT_URL,
        tmp_path,
        monkeypatch,
        terminal={"SECREQGEN_LLM_BASE_URL": OTHER_SERVER},
    )
    assert command.env == ()
    assert set(by_gui_at) == {DEFAULT_URL}
    assert set(by_command_at) == {OTHER_SERVER}
