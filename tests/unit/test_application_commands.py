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
        prompt_set=None,
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
        prompt_set=None,
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


def test_threat_default_command_has_no_optional_option():
    command = threat()
    assert command.env == ()
    assert command.argv == (
        "secreqgen",
        "generate",
        "threats",
        "/data/stories.json",
        "--docs",
        "/data/docs",
    )
    assert command.text == (
        "secreqgen generate threats /data/stories.json --docs /data/docs"
    )
    assert command.differences == ()


def test_threat_writer_is_left_out_only_when_it_is_the_tested_one():
    assert "--model" not in threat(writer_model=TESTED_WRITER).argv
    command = threat(writer_model="mistral-small3.2:24b")
    assert command.argv[-2:] == ("--model", "mistral-small3.2:24b")


def test_threat_checker_follows_the_default_of_the_writer():
    # Writer other than the tested one: the default checker is still qwen.
    command = threat(writer_model="m1", checker_model=TESTED_CHECKER)
    assert "--checker-model" not in command.argv
    # A writer that is the tested checker: the default checker is the tested writer.
    command = threat(writer_model=TESTED_CHECKER, checker_model=TESTED_WRITER)
    assert "--checker-model" not in command.argv
    command = threat(writer_model=TESTED_CHECKER, checker_model=TESTED_CHECKER + "x")
    assert "--checker-model" in command.argv
    # The tested checker is not the default when it is named for that writer.
    command = threat(writer_model=TESTED_CHECKER, checker_model="other")
    assert command.argv[-2:] == ("--checker-model", "other")
    command = threat(checker_model="llama3:8b")
    assert command.argv[-2:] == ("--checker-model", "llama3:8b")


def test_threat_check_off_has_no_checker_option():
    command = threat(check=False, checker_model="llama3:8b")
    assert "--no-check" in command.argv
    assert "--checker-model" not in command.argv
    assert "--no-check" not in threat(check=True).argv


def test_threat_checker_none_means_the_default():
    assert "--checker-model" not in threat(checker_model=None).argv


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
    assert command.argv[-2:] == ("--product", "keycloak")
    command = threat(product="demo", default_product=None)
    assert command.argv[-2:] == ("--product", "demo")


def test_threat_story_choice():
    ids = [f"S-{n}" for n in range(1, 9)]
    for choice in (None, [], ids, list(reversed(ids))):
        command = threat(story_ids=choice)
        assert "--story-id" not in command.argv
        assert command.differences == ()
    one = threat(story_ids=["S-3"])
    assert one.argv[-2:] == ("--story-id", "S-3")
    assert one.differences == ()
    for choice in (["S-1", "S-2"], ids[:7]):
        command = threat(story_ids=choice)
        assert "--story-id" not in command.argv
        assert command.differences == ("stories_subset",)
    # One story in a file of one story is the whole file.
    assert "--story-id" not in threat(story_ids=["S-1"], total_stories=1).argv


def test_threat_prompt_set_is_a_difference():
    assert threat(prompt_set="mine").differences == ("prompt_set",)
    both = threat(prompt_set="mine", story_ids=["a", "b"])
    assert both.differences == ("stories_subset", "prompt_set")
    # The command is the one of the tested prompts: it has no prompt option.
    assert both.argv == threat(story_ids=["a", "b"]).argv


def test_requirements_default_command():
    command = requirements()
    assert command.env == ()
    assert command.argv == (
        "secreqgen",
        "generate",
        "requirements",
        "/data/stories.json",
        "--docs",
        "/data/docs",
    )
    assert command.differences == ()


def test_requirements_model_is_a_variable_in_front():
    command = requirements(model="gemma4:26b")
    assert command.env == (("SECREQGEN_LLM_MODEL_NAME", "gemma4:26b"),)
    assert command.text.startswith("SECREQGEN_LLM_MODEL_NAME=gemma4:26b secreqgen ")
    assert "--model" not in command.argv
    assert requirements(model=LLMConfig.model_fields["model_name"].default).env == ()


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
        "--context",
        "/home/tester/notes/context.yaml",
        "--other-formats",
    )
    assert "--context ~/notes/context.yaml" in command.text
    assert "--product" not in requirements(product="demo", default_product="demo").argv


def test_requirements_cannot_pick_stories():
    ids = ["a", "b", "c"]
    assert requirements(story_ids=ids, total_stories=3).differences == ()
    assert requirements(story_ids=None).differences == ()
    for choice in (["a"], ["a", "b"]):
        command = requirements(story_ids=choice, total_stories=3)
        assert command.differences == ("stories_subset",)
        assert "--story-id" not in command.argv
    command = requirements(story_ids=["a"], total_stories=3, prompt_set="mine")
    assert command.differences == ("stories_subset", "prompt_set")


def test_context_command():
    command = context()
    assert command.argv == ("secreqgen", "generate", "context", "/data/code")
    assert command.differences == ()
    command = context(writer_model="m1", product="Shop")
    assert command.argv[3:] == ("/data/code", "--model", "m1", "--product", "Shop")
    assert "--product" not in context(product="  ").argv
    assert "--model" not in context(writer_model="").argv


def test_the_address_of_ollama_is_a_variable_in_front():
    for command in (
        threat(base_url="http://gpu:11434"),
        requirements(base_url="http://gpu:11434"),
        context(base_url="http://gpu:11434"),
    ):
        assert command.env == (("SECREQGEN_LLM_BASE_URL", "http://gpu:11434"),)
        assert command.text.startswith("SECREQGEN_LLM_BASE_URL=http://gpu:11434 ")
    for command in (threat(), requirements(), context()):
        assert command.env == ()


def test_both_variables_come_in_a_fixed_order():
    command = requirements(model="gemma4:26b", base_url="http://gpu:1")
    assert [name for name, _ in command.env] == [
        "SECREQGEN_LLM_BASE_URL",
        "SECREQGEN_LLM_MODEL_NAME",
    ]


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
    assert shlex.split(command.text) == list(command.argv)
    assert "'My Product; rm -rf /'" in command.text
    assert command.text.count("'") >= 8


def test_a_path_that_starts_with_a_dash_cannot_be_read_as_an_option():
    command = threat(stories_file=Path("-s.json"), docs=Path("-docs"))
    assert command.argv[3] == "./-s.json"
    assert parse(command)["input_file"] == "./-s.json"
    assert parse(command)["docs"] == "./-docs"
    assert command.text == "secreqgen generate threats ./-s.json --docs ./-docs"


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
    total = rng.randint(1, 8)
    kind = rng.choice(["none", "all", "one", "some", "empty"])
    ids = [f"S-{n}" for n in range(total)]
    if kind == "none":
        return total, None
    if kind == "empty":
        return total, []
    if kind == "all":
        return total, rng.sample(ids, len(ids))
    if kind == "one":
        return total, [rng.choice(ids)]
    return total, rng.sample(ids, rng.randint(1, len(ids)))


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
        prompt_set=maybe(rng, "mine", 0.3),
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
        prompt_set=maybe(rng, "mine", 0.3),
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
        # The stories.
        ids = list(dict.fromkeys(case["story_ids"] or ()))
        total = case["total_stories"]
        if ids and len(ids) < total and len(ids) == 1:
            assert parsed["story_id"] == ids[0], where
            assert command.differences == (
                ("prompt_set",) if case["prompt_set"] else ()
            ), where
        else:
            assert parsed["story_id"] is None, where
            subset = bool(ids) and len(ids) < total
            expected = (["stories_subset"] if subset else []) + (
                ["prompt_set"] if case["prompt_set"] else []
            )
            assert list(command.differences) == expected, where
        # The models that the run uses.
        writer, checker = choose_models(
            parsed["writer_model"], None, parsed["checker_model"], parsed["check"]
        )
        assert parsed["check"] is case["check"], where
        want_writer = case["writer_model"].strip() or TESTED_WRITER
        assert writer == want_writer, where
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
        ids = list(dict.fromkeys(case["story_ids"] or ()))
        subset = bool(ids) and len(ids) < case["total_stories"]
        expected = (["stories_subset"] if subset else []) + (
            ["prompt_set"] if case["prompt_set"] else []
        )
        assert list(command.differences) == expected, where
        llm = effective_llm(command.env, monkeypatch)
        assert llm.base_url == case["base_url"], where
        assert llm.model_name == case["model"], where


def test_context_commands_parse(monkeypatch):
    for number, case in enumerate(CONTEXT_CASES):
        command = context_command(**case)
        parsed = parse(command)
        where = f"case {number}: {case}\n{command.text}"
        assert same_path(parsed["folder"], case["folder"]), where
        from secreqgen.application.system_context import resolve_context_model

        llm = effective_llm(command.env, monkeypatch)
        assert llm.base_url == case["base_url"], where
        name = (
            resolve_context_model(
                type("Cfg", (), {"llm": llm})(), parsed["writer_model"]
            )
            if case["writer_model"].strip()
            else resolve_context_model(type("Cfg", (), {"llm": llm})(), None)
        )
        assert name == (case["writer_model"].strip() or TESTED_WRITER), where
        product = case["product"]
        want = product if product is not None and product.strip() else None
        assert parsed["product"] == want, where
        assert command.differences == (), where
        assert command.env == (
            (("SECREQGEN_LLM_BASE_URL", case["base_url"]),)
            if case["base_url"] != DEFAULT_URL
            else ()
        ), where


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
    lambda: context(folder=Path("=code"), product="=p", writer_model="=m"),
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
    assert "SECREQGEN_LLM_MODEL_NAME='=m'" in command.text
    assert "--product '=p'" in command.text
    assert command.argv[-2:] == ("--product", "=p")


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


def run_command(command: Command, monkeypatch, factory) -> list[str]:
    """Run the built command through the command line. Return the prompts."""
    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    g.PROMPTS.clear()
    result = typer.testing.CliRunner().invoke(
        secreqgen.cli.app, list(command.argv[1:]), env=dict(command.env)
    )
    assert result.exit_code == 0, result.output
    return list(g.PROMPTS)


def gui_cfg(base_url: str):
    """The settings of the GUI run (make_config of the GUI)."""
    cfg = get_config()
    cfg.llm.base_url = base_url
    return cfg


def run_gui_threats(values: dict, tmp_path: Path, factory) -> list[str]:
    """Run the functions that the GUI calls, with the options of jobs.py."""
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
        prompts_dir=None,
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
        prompts_dir=None,
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
        prompt_set=None,
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
        prompt_set=None,
        other_formats=False,
        base_url=DEFAULT_URL,
        home=Path.home(),
    )
    values.update(changes)
    return values


def threat_option_sets(about: Path) -> list[dict]:
    return [
        {},  # the tested defaults
        {"writer_model": "one-writer"},  # the default checker follows
        {"check": False, "labels": False},
        {"checker_model": "other-checker", "about_file": about},
        {"story_ids": ["US-2"]},  # one story: --story-id
        {"product": "keycloak", "base_url": "http://gpu:11434"},
        {
            "story_ids": ["US-3", "US-1", "US-2"],
            "writer_model": TESTED_CHECKER,
            "checker_model": TESTED_WRITER,  # the default checker of that writer
        },
        {"writer_model": TESTED_CHECKER, "checker_model": "other-checker"},
    ]


@pytest.mark.parametrize("index", range(8))
def test_the_same_run_threats(index, tmp_path, monkeypatch):
    stories, docs = write_inputs()
    about = Path("about.txt").resolve()
    about.write_text(
        "Demo is a web shop.\n\nIt has   sessions\nand a cart.\n", encoding="utf-8"
    )
    changes = threat_option_sets(about)[index]
    values = threat_values(stories, docs, **changes)
    command = threat_command(**values)
    by_command = run_command(command, monkeypatch, threat_factory())
    by_gui = run_gui_threats(values, tmp_path, threat_factory())
    assert by_command
    assert by_command == by_gui, command.text
    assert command.differences == ()


def requirements_option_sets(context_file: Path) -> list[dict]:
    return [
        {},  # the model of the GUI, a variable in front
        {"model": "llama3:8b"},  # the default model of the settings
        {"system_context": context_file, "product": "keycloak"},
        {"story_ids": ["US-1", "US-2", "US-3"], "base_url": "http://gpu:11434"},
    ]


@pytest.mark.parametrize("index", range(4))
def test_the_same_run_requirements(index, tmp_path, monkeypatch):
    stories, docs = write_inputs()
    context_file = Path("ctx.yaml").resolve()
    context_file.write_text("name: Demo\nstack: Ruby\n", encoding="utf-8")
    changes = requirements_option_sets(context_file)[index]
    values = requirements_values(stories, docs, **changes)
    command = requirements_command(**values)
    by_command = run_command(command, monkeypatch, requirements_factory())
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    assert by_command
    assert by_command == by_gui, command.text
    assert command.differences == ()


def test_the_same_run_with_a_story_subset_is_a_difference(tmp_path, monkeypatch):
    """The command line cannot pick two stories: the code is in differences."""
    stories, docs = write_inputs()
    values = requirements_values(stories, docs, story_ids=["US-1", "US-3"])
    command = requirements_command(**values)
    assert command.differences == ("stories_subset",)
    by_command = run_command(command, monkeypatch, requirements_factory())
    by_gui = run_gui_requirements(values, tmp_path, requirements_factory())
    # The command runs all 3 stories. The GUI runs 2.
    assert len(by_command) == 3
    assert len(by_gui) == 2
    assert by_command != by_gui


def test_the_same_run_with_a_prompt_set_is_a_difference(tmp_path, monkeypatch):
    """The command line has no prompt set option. It runs the tested prompts."""
    stories, docs = write_inputs()
    values = threat_values(stories, docs, prompt_set="mine")
    command = threat_command(**values)
    assert command.differences == ("prompt_set",)
    # What the command runs is what the same run with no prompt set runs.
    plain = threat_command(**threat_values(stories, docs))
    assert command.argv == plain.argv


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
    assert all("gemma4:26b generate" in text for text in prompts)
    # The variable of the command is what puts the model into the run.
    assert command.env == (("SECREQGEN_LLM_MODEL_NAME", "gemma4:26b"),)
    assert json.dumps(prompts)


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
