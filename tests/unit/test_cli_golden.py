"""Golden tests of the command line.

What this file pins: for inputs that need no stand-in model, the exact console
text and the exit code of the commands that the cleanup moves; the order of the
commands in --help; and the entry points. The expected text of each case is in
tests/unit/golden/cli/<case>.txt. The files were recorded from the code before
the cleanup. A later step must not change them.

Stand-ins: some cases patch the model client, the embedder, the vector store, the
retriever and the evaluation pipeline at the places that the commands use. The
fakes are new code in this file. They write a call log (each constructor call
with its keyword arguments) and a record of each prompt that the model gets.
Both go into the golden file, in the sections "--- calls" and "--- prompts".

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
from types import SimpleNamespace

import click
import numpy as np
import pytest
import secreqgen.cli
import typer.main
import typer.testing
from secreqgen.exceptions import GenerationError, LLMConnectionError
from secreqgen.generation.llm_client import LLMResponse

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
    CALLS.clear()
    PROMPTS.clear()


# The id of "generate single" is random: US- and six hex digits.
RANDOM_ID_RE = re.compile(r"US-[0-9A-F]{6}")
# A threat summary line ends with the run time of the story.
RUN_TIME_RE = re.compile(r", \d+(?:\.\d+)? s$", re.MULTILINE)
# The JSON field "seconds" holds a run time.
SECONDS_RE = re.compile(r'("seconds": )[0-9.]+')
# The page threats.md states the run time of the story.
PAGE_TIME_RE = re.compile(r"Time: \d+ s\.")


def normalise(text: str) -> str:
    text = ANSI_RE.sub("", text)
    text = SPINNER_RE.sub("*", text)
    text = RANDOM_ID_RE.sub("US-<ID>", text)
    text = RUN_TIME_RE.sub(", <N> s", text)
    text = SECONDS_RE.sub(r"\1<N>", text)
    text = PAGE_TIME_RE.sub("Time: <N> s.", text)
    return TIME_RE.sub("<TIME>", text)


# The call log and the prompt record of the stand-ins. The fixture clears them.
CALLS: list[str] = []
PROMPTS: list[str] = []


def show(value: object) -> str:
    """A path shows as given, a plain value as its repr, another object as its class."""
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, bool | int | float | str):
        return repr(value)
    return type(value).__name__


def log_call(name: str, **kwargs: object) -> None:
    args = ", ".join(f"{key}={show(kwargs[key])}" for key in sorted(kwargs))
    CALLS.append(f"{name}({args})")


def describe(args: list[str]) -> list[str]:
    result = typer.testing.CliRunner().invoke(secreqgen.cli.app, args)
    lines = [f"exit={result.exit_code}"]
    if result.exception is not None and not isinstance(
        result.exception, SystemExit
    ):
        exc = result.exception
        lines.append(f"exception={type(exc).__name__}: {exc}")
    lines.append(normalise(result.output))
    return lines


def finish(name: str, lines: list[str], files: tuple[str, ...]) -> None:
    for rel in files:
        path = Path(rel)
        if path.is_file():
            lines.append(f"--- file {rel}")
            lines.append(normalise(path.read_text(encoding="utf-8")))
    if CALLS:
        lines.append("--- calls")
        lines.append(normalise("\n".join(CALLS)))
    if PROMPTS:
        lines.append("--- prompts")
        lines.append(normalise("\n".join(PROMPTS)))
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


def run_case(name: str, args: list[str], files: tuple[str, ...] = ()) -> None:
    finish(name, describe(args), files)


def run_sequence(
    name: str, steps: list[list[str]], files: tuple[str, ...] = ()
) -> None:
    lines: list[str] = []
    for args in steps:
        lines.append(f"=== {' '.join(args)}")
        lines.extend(describe(args))
    finish(name, lines, files)


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
        "generate": [
            "check-ollama",
            "requirements",
            "single",
            "threats",
            "context",
        ],
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


# stand-ins ------------------------------------------------------------------
# New code. Small parts are copied from tests/unit/test_threats.py and
# tests/unit/test_word_search.py: those files stay as they are.

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

REQUIREMENTS_ANSWER = json.dumps(
    {
        "passage_notes": [
            {"passage": 1, "about": "idle timeout", "fits_story": "yes"}
        ],
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
EMBEDDING = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
LABEL_TASK = "Task: choose a threat actor and a STRIDE category"


class StandInClient:
    """A fake model client. It records each prompt and gives fixed replies.

    The script holds one item for each call: a text, or an exception to raise.
    The last item serves all later calls.
    """

    def __init__(
        self,
        model_name: str = "stand-in",
        healthy: bool = True,
        script: tuple = (REQUIREMENTS_ANSWER,),
    ) -> None:
        self._name = model_name
        self.healthy = healthy
        self.script = script
        self.calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    def check_health(self) -> bool:
        return self.healthy

    def list_models(self) -> list[str]:
        return ["gemma4:26b", "llama3:8b"]

    def respond(self, prompt: str) -> object:
        return self.script[min(self.calls, len(self.script) - 1)]

    def generate(self, prompt, system_prompt=None, **other):
        other_text = ", ".join(f"{k}={show(other[k])}" for k in sorted(other))
        PROMPTS.append(
            f"[{len(PROMPTS) + 1}] {self._name} generate({other_text})\n"
            f"system_prompt:\n{system_prompt if system_prompt else '(none)'}\n"
            f"prompt:\n{prompt}\n"
        )
        item = self.respond(prompt)
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return LLMResponse(
            content=item,
            model=self._name,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            raw_response={},
        )


def source_of(where: dict[str, str], sentence: str) -> str:
    return next(name for name, text in where.items() if sentence in text)


class ThreatWriter(StandInClient):
    """The writer of the threat list: an actor line, or an attack row and a rule row."""

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
        if any(WARNING in text for text in where.values()):
            rows.append(
                {
                    "source": source_of(where, WARNING),
                    "quote": WARNING,
                    "type": "attack",
                    "threat": "An attacker who steals the session cookie can use it.",
                    "harm": "access with no permission",
                    "story_part": "The session ends after a time with no activity.",
                    "requirement": "Set `session-idle-timeout`.",
                }
            )
        if any(RULE in text for text in where.values()):
            rows.append(
                {
                    "source": source_of(where, RULE),
                    "quote": RULE,
                    "type": "rule",
                    "threat": "The session cookie is sent with no Secure flag.",
                    "harm": "disclosure of data",
                    "story_part": "a part that the story does not have",
                    "requirement": "Keep the Secure flag on the session cookie.",
                }
            )
        return json.dumps({"passages": notes, "rows": rows})


class ThreatChecker(StandInClient):
    """The checker: it accepts the row with the word attacker in its quote."""

    def respond(self, prompt: str) -> str:
        quote = prompt.split('Quote: "', 1)[1].split('"\n', 1)[0]
        if "attacker" in quote.lower():
            return "The row has no fault.\nOK"
        return "The sentence is about backups.\nFAULT"


def writer_with_broken_name(error: Exception) -> type[ThreatWriter]:
    """A writer whose model_name raises an error.

    The threat generator reads model_name after the model calls, outside the
    try block of the calls. This is the way to put an error into the story
    loop of the command: an error of generate() itself counts as a failed call.
    """

    class BrokenWriter(ThreatWriter):
        @property
        def model_name(self) -> str:
            raise error

    return BrokenWriter


class StandInEmbedder:
    def __init__(self, **kwargs: object) -> None:
        log_call("DocumentEmbedder", **kwargs)

    def embed_chunks(self, chunks):
        return [SimpleNamespace(embedding=np.array(EMBEDDING)) for _ in chunks]


class StandInStore:
    def __init__(self, **kwargs: object) -> None:
        log_call("SecReqGenVectorStore", **kwargs)


class StandInRetriever:
    def __init__(self, **kwargs: object) -> None:
        log_call("SimpleRetriever", **kwargs)

    def get_system_context(self):
        log_call("SimpleRetriever.get_system_context")
        return "name: StoredContext\n"

    def retrieve_for_user_story(self, user_story):
        log_call("SimpleRetriever.retrieve_for_user_story", story=user_story.id)
        return SimpleNamespace(retrieval_time_ms=1.5)

    def format_result_for_prompt(self, result):
        log_call("SimpleRetriever.format_result_for_prompt", result=result)
        return "### [1] fixed.md\nA fixed passage of the stand-in retriever."


class StandInPipeline:
    def __init__(self, **kwargs: object) -> None:
        log_call("EvaluationPipeline", **kwargs)

    def run(self, **kwargs: object):
        log_call("EvaluationPipeline.run", **kwargs)
        raise GenerationError("stand-in stop")


def stand_in_multi_run(**kwargs: object):
    log_call("run_multi_evaluation", **kwargs)
    raise GenerationError("stand-in stop")


def use_client(monkeypatch, client: StandInClient) -> StandInClient:
    """generate requirements and generate single: no model_name parameter."""

    def factory(cfg, tested_setup):
        log_call("_ollama_client", tested_setup=tested_setup)
        return client

    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    return client


def use_threat_clients(monkeypatch, writer=ThreatWriter, checker=ThreatChecker):
    """generate threats: the first client is the writer, the next one the checker."""
    made: list[StandInClient] = []

    def factory(cfg, tested_setup, model_name=None):
        log_call("_ollama_client", model_name=model_name, tested_setup=tested_setup)
        kind = checker if made else writer
        client = kind(model_name or cfg.llm.model_name, script=(None,))
        made.append(client)
        return client

    monkeypatch.setattr("secreqgen.cli._ollama_client", factory)
    return made


def use_ollama_class(monkeypatch, healthy: bool = True) -> None:
    """The commands that build OllamaClient themselves."""

    def make(**kwargs: object) -> StandInClient:
        log_call("OllamaClient", **kwargs)
        return StandInClient(str(kwargs["model_name"]), healthy=healthy)

    monkeypatch.setattr("secreqgen.generation.OllamaClient", make)


def use_embedder(monkeypatch) -> None:
    monkeypatch.setattr("secreqgen.ingestion.DocumentEmbedder", StandInEmbedder)


def use_vector_stand_ins(monkeypatch) -> None:
    use_embedder(monkeypatch)
    monkeypatch.setattr("secreqgen.retrieval.SecReqGenVectorStore", StandInStore)
    monkeypatch.setattr("secreqgen.retrieval.SimpleRetriever", StandInRetriever)


def docs_folder() -> str:
    Path("docs").mkdir()
    write("docs/sessions.md", PAGE)
    return "docs"


def two_stories_file() -> str:
    data = {
        "project": "demo",
        "user_stories": [
            {"id": "US-001", "title": "Log in", "story": "As a user I log in."},
            {"id": "US-002", "title": "Log out", "story": "As a user I log out."},
        ],
    }
    return write("stories.json", json.dumps(data))


def threat_stories_file(ids: tuple[str, ...] = ("US-1",)) -> str:
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
    return write("stories.json", json.dumps(data))


# generate check-ollama -------------------------------------------------------


def test_check_ollama_healthy(monkeypatch):
    use_ollama_class(monkeypatch)
    run_case("check_ollama_healthy", ["generate", "check-ollama"])


def test_check_ollama_not_healthy(monkeypatch):
    use_ollama_class(monkeypatch, healthy=False)
    run_case("check_ollama_not_healthy", ["generate", "check-ollama"])


# generate requirements, with stand-ins ---------------------------------------


def test_requirements_docs_markdown(monkeypatch):
    use_client(monkeypatch, StandInClient())
    stories_file()
    docs = docs_folder()
    run_case(
        "requirements_docs_markdown",
        ["generate", "requirements", "stories.json", "--docs", docs],
    )


def test_requirements_docs_json_file(monkeypatch):
    use_client(monkeypatch, StandInClient())
    stories_file()
    docs = docs_folder()
    write("ctx.yaml", "name: Demo\n")
    run_case(
        "requirements_docs_json_file",
        [
            "generate",
            "requirements",
            "stories.json",
            "--docs",
            docs,
            "--format",
            "json",
            "--output",
            "out.json",
            "--context",
            "ctx.yaml",
            "--product",
            "demo",
        ],
        files=("out.json",),
    )


def test_requirements_vector_path(monkeypatch):
    use_client(monkeypatch, StandInClient())
    use_vector_stand_ins(monkeypatch)
    stories_file()
    run_case("requirements_vector_path", ["generate", "requirements", "stories.json"])


def test_requirements_vector_baseline(monkeypatch):
    use_client(monkeypatch, StandInClient())
    use_vector_stand_ins(monkeypatch)
    stories_file()
    run_case(
        "requirements_vector_baseline",
        ["generate", "requirements", "stories.json", "--baseline"],
    )


def test_requirements_not_healthy(monkeypatch):
    use_client(monkeypatch, StandInClient(healthy=False))
    stories_file()
    docs = docs_folder()
    run_case(
        "requirements_not_healthy",
        ["generate", "requirements", "stories.json", "--docs", docs],
    )


def test_requirements_second_story_fails(monkeypatch):
    script = (REQUIREMENTS_ANSWER, GenerationError("stand-in failure"))
    use_client(monkeypatch, StandInClient(script=script))
    two_stories_file()
    docs = docs_folder()
    run_case(
        "requirements_second_story_fails",
        ["generate", "requirements", "stories.json", "--docs", docs],
    )


def test_requirements_all_stories_fail(monkeypatch):
    script = (GenerationError("stand-in failure"),)
    use_client(monkeypatch, StandInClient(script=script))
    stories_file()
    docs = docs_folder()
    run_case(
        "requirements_all_stories_fail",
        ["generate", "requirements", "stories.json", "--docs", docs],
    )


def test_requirements_invalid_story_entry(monkeypatch):
    use_client(monkeypatch, StandInClient())
    data = {"user_stories": [STORIES["user_stories"][0], 5]}
    write("stories.json", json.dumps(data))
    docs = docs_folder()
    run_case(
        "requirements_invalid_story_entry",
        ["generate", "requirements", "stories.json", "--docs", docs],
    )


# generate single, with stand-ins ---------------------------------------------


def test_single_docs(monkeypatch):
    use_client(monkeypatch, StandInClient())
    docs = docs_folder()
    run_case("single_docs", [*SINGLE, "--docs", docs])


def test_single_vector_path(monkeypatch):
    use_client(monkeypatch, StandInClient())
    use_vector_stand_ins(monkeypatch)
    run_case("single_vector_path", SINGLE)


def test_single_not_healthy(monkeypatch):
    use_client(monkeypatch, StandInClient(healthy=False))
    use_vector_stand_ins(monkeypatch)
    run_case("single_not_healthy", SINGLE)


def test_single_generation_error(monkeypatch):
    script = (GenerationError("stand-in failure"),)
    use_client(monkeypatch, StandInClient(script=script))
    use_vector_stand_ins(monkeypatch)
    run_case("single_generation_error", SINGLE)


# generate threats, with stand-ins --------------------------------------------

THREAT_FILES = (
    "out/US-1/threats.json",
    "out/US-1/threats.md",
)


def threat_args(*extra: str) -> list[str]:
    return ["generate", "threats", "stories.json", "--docs", "docs", *extra]


def test_threats_default_pair(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_default_pair",
        threat_args("--output", "out"),
        files=THREAT_FILES,
    )


def test_threats_no_check(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_no_check",
        threat_args("--no-check", "--output", "out"),
        files=THREAT_FILES,
    )


def test_threats_model_is_checker(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_model_is_checker",
        threat_args(
            "--model", "one-model", "--checker-model", "one-model", "--output", "out"
        ),
        files=THREAT_FILES,
    )


def test_threats_no_check_with_checker(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_no_check_with_checker",
        threat_args(
            "--no-check", "--checker-model", "other-model", "--output", "out"
        ),
        files=THREAT_FILES,
    )


def test_threats_same_folder_name(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file(("A/1", "A_1"))
    docs_folder()
    run_case(
        "threats_same_folder_name",
        threat_args("--no-check", "--output", "out"),
        files=(
            "out/A_1/threats.json",
            "out/A_1/threats.md",
            "out/A_1_2/threats.json",
            "out/A_1_2/threats.md",
        ),
    )


def test_threats_connection_error(monkeypatch):
    writer = writer_with_broken_name(LLMConnectionError("stand-in lost"))
    use_threat_clients(monkeypatch, writer=writer)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_connection_error",
        threat_args("--no-check", "--output", "out"),
        files=THREAT_FILES,
    )


def test_threats_list_error(monkeypatch):
    from secreqgen.threats import ThreatListError

    writer = writer_with_broken_name(ThreatListError("stand-in list error"))
    use_threat_clients(monkeypatch, writer=writer)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_list_error",
        threat_args("--no-check", "--output", "out"),
        files=THREAT_FILES,
    )


def test_threats_about_file_missing(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    run_case(
        "threats_about_file_missing",
        threat_args("--no-check", "--about-file", "none.txt", "--output", "out"),
    )


def test_threats_about_file_is_folder(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    Path("adir").mkdir()
    run_case(
        "threats_about_file_is_folder",
        threat_args("--no-check", "--about-file", "adir", "--output", "out"),
    )


def test_threats_about_file_empty(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    write("about.txt", "  \n \n")
    run_case(
        "threats_about_file_empty",
        threat_args("--no-check", "--about-file", "about.txt", "--output", "out"),
    )


def test_threats_about_file_not_utf8(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    Path("about.txt").write_bytes(b"\xff\xfe\xfa about")
    run_case(
        "threats_about_file_not_utf8",
        threat_args("--no-check", "--about-file", "about.txt", "--output", "out"),
    )


def test_threats_about_file_good(monkeypatch):
    use_threat_clients(monkeypatch)
    threat_stories_file()
    docs_folder()
    write("about.txt", "Demo is a web shop with sessions and a cart.\n")
    run_case(
        "threats_about_file_good",
        threat_args("--no-check", "--about-file", "about.txt", "--output", "out"),
        files=THREAT_FILES,
    )


# ingest and evaluate, with stand-ins -----------------------------------------


def test_ingest_in_order(monkeypatch):
    use_embedder(monkeypatch)
    Path("docs").mkdir()
    write("docs/policy.md", "# Policy\n\n## Passwords\n\nUse long passwords.\n")
    write("ctx.yaml", "name: Demo\ncomponents:\n  - web\n  - api\n")
    run_sequence(
        "ingest_in_order",
        [
            ["ingest", "documents", "docs"],
            ["ingest", "system-context", "ctx.yaml"],
            ["ingest", "stats"],
            ["ingest", "clear", "--force"],
        ],
    )


def test_evaluate_run_stops(monkeypatch):
    use_ollama_class(monkeypatch)
    use_vector_stand_ins(monkeypatch)
    monkeypatch.setattr("secreqgen.evaluation.EvaluationPipeline", StandInPipeline)
    stories_file()
    write("gt.json", "{}")
    run_case("evaluate_run_stops", ["evaluate", "run", "stories.json", "gt.json"])


def test_evaluate_compare_runs_stops(monkeypatch):
    use_ollama_class(monkeypatch)
    use_vector_stand_ins(monkeypatch)
    monkeypatch.setattr("secreqgen.evaluation.EvaluationPipeline", StandInPipeline)
    monkeypatch.setattr(
        "secreqgen.evaluation.run_multi_evaluation", stand_in_multi_run
    )
    stories_file()
    write("gt.json", "{}")
    run_case(
        "evaluate_compare_runs_stops",
        ["evaluate", "compare-runs", "stories.json", "gt.json", "--runs", "3"],
    )


def test_normalise_hides_the_page_run_time() -> None:
    assert (
        normalise("Model: m. Time: 3 s. Rows that the script removed: 0.")
        == "Model: m. Time: <N> s. Rows that the script removed: 0."
    )
