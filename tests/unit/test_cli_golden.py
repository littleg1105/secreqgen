"""Golden tests of the command line.

What this file pins: for inputs that need no stand-in model, the exact console
text and the exit code of the commands that the cleanup moves; the order of the
commands in --help; and the entry points. The expected text of each case is in
tests/unit/golden/cli/<case>.txt. The files were recorded from the code before
the cleanup. A later step must not change them.

How to record again: set GOLDEN_RECORD=1 and run this file. Do it only for an
intended change of the console text. Then read the diff of the golden files.

Versions at recording time: click 8.3.1, typer 0.16.1, rich 13.9.4. A newer
Rich or Click can change the layout of a panel. Then record again and read the
diff.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import click
import pytest
import secreqgen.cli
import typer.main
import typer.testing

# The name has no SECREQGEN_ prefix, because the fixture removes those variables.
RECORD = os.environ.get("GOLDEN_RECORD") == "1"

REPO = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "cli"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
SPINNER_RE = re.compile("[⠀-⣿]")
TIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)


@pytest.fixture(autouse=True)
def _clean_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("SECREQGEN_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY", "vectordb")
    # The private field: monkeypatch restores it to None after the test.
    monkeypatch.setattr(secreqgen.cli.console, "_width", 200)


def normalise(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = SPINNER_RE.sub("*", text)
    return TIME_RE.sub("<TIME>", text)


def run_case(name: str, args: list[str], files: tuple[str, ...] = ()) -> None:
    result = typer.testing.CliRunner().invoke(secreqgen.cli.app, args)
    lines = [f"exit={result.exit_code}"]
    if result.exception is not None and not isinstance(
        result.exception, SystemExit
    ):
        exc = result.exception
        lines.append(f"exception={type(exc).__name__}: {exc}")
    lines.append(normalise(result.output))
    for rel in files:
        path = Path(rel)
        if path.is_file():
            lines.append(f"--- file {rel}")
            lines.append(normalise(path.read_text(encoding="utf-8")))
    text = "\n".join(lines)
    golden = GOLDEN_DIR / f"{name}.txt"
    if RECORD:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(text, encoding="utf-8")
        return
    expected = golden.read_text(encoding="utf-8")
    if text != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                text.splitlines(),
                "golden",
                "now",
                lineterm="",
            )
        )
        pytest.fail(f"Output of case {name} changed:\n{diff}")


def write(rel: str, text: str) -> str:
    Path(rel).write_text(text, encoding="utf-8")
    return rel


STORIES = {
    "project": "demo",
    "user_stories": [
        {"id": "US-001", "title": "Log in", "story": "As a user I log in."}
    ],
}


def stories_file(rel: str = "stories.json") -> str:
    return write(rel, json.dumps(STORIES))


# ingest ---------------------------------------------------------------------


def test_ingest_documents_missing_folder():
    run_case("ingest_documents_missing_folder", ["ingest", "documents", "nodocs"])


def test_ingest_documents_file_not_folder():
    write("afile.md", "# x\n")
    run_case("ingest_documents_file_not_folder", ["ingest", "documents", "afile.md"])


def test_ingest_documents_invalid_type():
    Path("docs").mkdir()
    run_case(
        "ingest_documents_invalid_type",
        ["ingest", "documents", "docs", "--type", "invalid"],
    )


def test_ingest_system_context_missing_file():
    run_case(
        "ingest_system_context_missing_file", ["ingest", "system-context", "no.yaml"]
    )


def test_ingest_system_context_folder():
    Path("adir").mkdir()
    run_case("ingest_system_context_folder", ["ingest", "system-context", "adir"])


def test_ingest_system_context_wrong_suffix():
    write("ctx.txt", "a: b\n")
    run_case(
        "ingest_system_context_wrong_suffix", ["ingest", "system-context", "ctx.txt"]
    )


def test_ingest_stats_no_store():
    run_case("ingest_stats_no_store", ["ingest", "stats"])


def test_ingest_clear_no_store():
    run_case("ingest_clear_no_store", ["ingest", "clear", "--force"])


# generate requirements ------------------------------------------------------


def test_requirements_missing_input():
    run_case("requirements_missing_input", ["generate", "requirements", "no.json"])


def test_requirements_missing_docs():
    stories_file()
    run_case(
        "requirements_missing_docs",
        ["generate", "requirements", "stories.json", "--docs", "nodocs"],
    )


def test_requirements_baseline_with_docs():
    stories_file()
    Path("docs").mkdir()
    run_case(
        "requirements_baseline_with_docs",
        ["generate", "requirements", "stories.json", "--docs", "docs", "--baseline"],
    )


def test_requirements_invalid_format():
    stories_file()
    run_case(
        "requirements_invalid_format",
        ["generate", "requirements", "stories.json", "--format", "invalid"],
    )


def test_requirements_bad_json():
    write("bad.json", "{not json")
    run_case("requirements_bad_json", ["generate", "requirements", "bad.json"])


def test_requirements_bad_yaml():
    write("bad.yaml", "a: [1, 2\nb: : :\n")
    run_case("requirements_bad_yaml", ["generate", "requirements", "bad.yaml"])


def test_requirements_json_list():
    # Today a list gives an uncaught AttributeError at data.get. Pin it.
    write("list.json", "[1, 2]")
    run_case("requirements_json_list", ["generate", "requirements", "list.json"])


def test_requirements_empty_stories():
    write("empty.json", json.dumps({"user_stories": []}))
    run_case("requirements_empty_stories", ["generate", "requirements", "empty.json"])


def test_requirements_missing_context():
    stories_file()
    run_case(
        "requirements_missing_context",
        ["generate", "requirements", "stories.json", "--context", "no.yaml"],
    )


# generate single ------------------------------------------------------------

SINGLE = ["generate", "single", "--title", "T", "--story", "S"]


def test_single_format_csv():
    run_case("single_format_csv", [*SINGLE, "--format", "csv"])


def test_single_missing_docs():
    run_case("single_missing_docs", [*SINGLE, "--docs", "nodocs"])


def test_single_baseline_with_docs():
    Path("docs").mkdir()
    run_case("single_baseline_with_docs", [*SINGLE, "--docs", "docs", "--baseline"])


def test_single_missing_context():
    run_case("single_missing_context", [*SINGLE, "--context", "no.yaml"])


# generate threats -----------------------------------------------------------


def test_threats_missing_input():
    Path("docs").mkdir()
    run_case(
        "threats_missing_input", ["generate", "threats", "no.json", "--docs", "docs"]
    )


def test_threats_missing_docs():
    stories_file()
    run_case(
        "threats_missing_docs",
        ["generate", "threats", "stories.json", "--docs", "nodocs"],
    )


def test_threats_bad_json():
    write("bad.json", "{not json")
    Path("docs").mkdir()
    run_case(
        "threats_bad_json", ["generate", "threats", "bad.json", "--docs", "docs"]
    )


def test_threats_json_list():
    write("list.json", "[1, 2]")
    Path("docs").mkdir()
    run_case(
        "threats_json_list", ["generate", "threats", "list.json", "--docs", "docs"]
    )


def test_threats_unknown_story_id():
    stories_file()
    Path("docs").mkdir()
    run_case(
        "threats_unknown_story_id",
        [
            "generate",
            "threats",
            "stories.json",
            "--docs",
            "docs",
            "--story-id",
            "NOPE",
        ],
    )


def test_threats_empty_stories():
    write("empty.json", json.dumps({"user_stories": []}))
    Path("docs").mkdir()
    run_case(
        "threats_empty_stories",
        ["generate", "threats", "empty.json", "--docs", "docs"],
    )


# evaluate -------------------------------------------------------------------


@pytest.mark.parametrize("command", ["run", "compare-runs"])
def test_evaluate_missing_stories(command):
    write("gt.json", "{}")
    stem = command.replace("-", "_")
    run_case(
        f"evaluate_{stem}_missing_stories", ["evaluate", command, "no.json", "gt.json"]
    )


@pytest.mark.parametrize("command", ["run", "compare-runs"])
def test_evaluate_missing_ground_truth(command):
    stories_file()
    stem = command.replace("-", "_")
    run_case(
        f"evaluate_{stem}_missing_ground_truth",
        ["evaluate", command, "stories.json", "no.json"],
    )


@pytest.mark.parametrize("command", ["run", "compare-runs"])
def test_evaluate_missing_context(command):
    stories_file()
    write("gt.json", "{}")
    stem = command.replace("-", "_")
    run_case(
        f"evaluate_{stem}_missing_context",
        ["evaluate", command, "stories.json", "gt.json", "--context", "no.yaml"],
    )


# structure ------------------------------------------------------------------


def _command_names(group: click.Group, ctx: click.Context) -> list[str]:
    return list(group.list_commands(ctx))


def test_command_order():
    root = typer.main.get_command(secreqgen.cli.app)
    ctx = click.Context(root)
    assert _command_names(root, ctx) == [
        "version",
        "config",
        "ingest",
        "generate",
        "evaluate",
    ]
    expected = {
        "ingest": ["documents", "system-context", "stats", "clear"],
        "generate": ["check-ollama", "requirements", "single", "threats"],
        "evaluate": ["run", "compare-runs", "compare", "report"],
    }
    for name, order in expected.items():
        sub = root.get_command(ctx, name)
        assert _command_names(sub, click.Context(sub)) == order


@pytest.mark.parametrize("module", ["secreqgen", "secreqgen.cli"])
def test_entry_points(module, tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-m", module, "version"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert "SecReqGen version: 0.1.0" in done.stdout
