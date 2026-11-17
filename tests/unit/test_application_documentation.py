"""Tests of the documentation use case. No model and no network."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from secreqgen.application import documentation
from secreqgen.application.documentation import (
    convertible_files,
    docs_note,
    prepare_documentation,
    shown_name,
)
from secreqgen.application.errors import InputError
from secreqgen.application.requirements import RequirementsOptions, run_requirements
from secreqgen.application.threats import ThreatOptions, run_threat_lists
from secreqgen.generation.llm_client import LLMResponse

WARNING = (
    "An attacker who steals the session cookie can use the session until the "
    "idle timeout ends it."
)
MD_PAGE = (
    "# Sessions\n\n## Idle timeout\n\n"
    "Set `session-idle-timeout` to end a session after a time with no activity.\n\n"
    f"{WARNING}\n"
)
RST_PAGE = (
    "Tokens\n======\n\nLifetime\n--------\n\n"
    "Set ``token-lifetime`` for the session token.\n\n"
    "A stolen token must not work after it expires. The token must expire.\n"
)
REQ_ANSWER = json.dumps(
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
    def __init__(self, model_name: str = "stand-in") -> None:
        self.model_name = model_name

    def check_health(self) -> bool:
        return True

    def respond(self, prompt: str) -> str:
        raise NotImplementedError

    def generate(self, prompt, system_prompt=None, **other):
        return LLMResponse(
            content=self.respond(prompt),
            model=self.model_name,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
            raw_response={},
        )


class ThreatWriter(StandIn):
    def respond(self, prompt: str) -> str:
        if prompt.startswith("Task: choose a threat actor"):
            return "1: external_individual, E"
        sources = re.findall(r"(?m)^### \[(P\d+)\] ", prompt)
        notes = [{"source": n, "fits_story": "yes"} for n in sources]
        return json.dumps({"passages": notes, "rows": []})


class ReqClient(StandIn):
    def respond(self, prompt: str) -> str:
        return REQ_ANSWER


def factory(client):
    def make(cfg, tested_setup, model_name=None):
        return client

    return make


def write_stories(folder: Path) -> Path:
    data = {
        "project": "demo",
        "user_stories": [
            {
                "id": "US-1",
                "title": "Session timeout",
                "story": "As an operator, I want sessions to end after idle time.",
                "acceptance_criteria": ["The session ends after idle time."],
            }
        ],
    }
    path = folder / "stories.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import os

    for name in list(os.environ):
        if name.startswith("SECREQGEN_"):
            monkeypatch.delenv(name, raising=False)
    return tmp_path


def make_docs(root: Path, *, md: bool, rst: bool) -> Path:
    folder = root / "docs"
    folder.mkdir()
    if md:
        (folder / "sessions.md").write_text(MD_PAGE, encoding="utf-8")
    if rst:
        (folder / "tokens.rst").write_text(RST_PAGE, encoding="utf-8")
    return folder


def without_time(model: dict) -> dict:
    data = json.loads(json.dumps(model))
    data.pop("seconds", None)
    for call in data.get("calls", []):
        call.pop("seconds", None)
    return data


# 0 ---------------------------------------------------------------------------


def test_shown_name():
    assert shown_name("guide/install.rst.md") == "guide/install.rst"
    assert shown_name("guide/install.rst.converted.md") == "guide/install.rst"
    assert shown_name("guide/install.md") == "guide/install.md"
    assert shown_name("a.md.notes.md") == "a.md.notes.md"
    assert shown_name("a.converted.md") == "a.converted.md"
    assert shown_name("Guide.RST.md") == "Guide.RST"


def test_convertible_files(tmp_path):
    docs = make_docs(tmp_path, md=True, rst=True)
    (docs / "a.txt").write_text("text", encoding="utf-8")
    assert convertible_files(docs) == {".rst": 1, ".txt": 1}
    assert convertible_files(tmp_path / "missing") == {}


# 1 ---------------------------------------------------------------------------


def test_off_returns_the_folder_and_loads_nothing(tmp_path):
    docs = make_docs(tmp_path, md=True, rst=True)
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from secreqgen.application.documentation import prepare_documentation\n"
        f"p = prepare_documentation(Path({str(docs)!r}))\n"
        f"assert p.folder == Path({str(docs)!r}) and p.report is None\n"
        "assert 'secreqgen.documents' not in sys.modules\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr


def test_off_builds_no_mirror(tmp_path, monkeypatch):
    import secreqgen.documents.mirror as mirror

    def fail(*args, **kwargs):
        raise AssertionError("build_mirror was called")

    monkeypatch.setattr(mirror, "build_mirror", fail)
    docs = make_docs(tmp_path, md=True, rst=True)
    prepared = prepare_documentation(docs, other_formats=False)
    assert prepared.folder == docs and prepared.report is None


# 2 ---------------------------------------------------------------------------


def test_md_only_is_never_copied(tmp_path, monkeypatch):
    import secreqgen.documents.mirror as mirror

    def fail(*args, **kwargs):
        raise AssertionError("build_mirror was called")

    monkeypatch.setattr(mirror, "build_mirror", fail)
    docs = make_docs(tmp_path, md=True, rst=False)
    prepared = prepare_documentation(
        docs, other_formats=True, cache_dir=tmp_path / "cache"
    )
    assert prepared.folder == docs and prepared.report is None
    assert not (tmp_path / "cache").exists()


# 3 ---------------------------------------------------------------------------


def test_mixed_folder_gives_a_temporary_mirror(tmp_path):
    docs = make_docs(tmp_path, md=True, rst=True)
    prepared = prepare_documentation(docs, other_formats=True)
    assert prepared.folder != docs
    assert (prepared.folder / "sessions.md").is_file()
    assert (prepared.folder / "tokens.rst.md").is_file()
    assert prepared.report.converted == {".rst": 1}
    assert prepared.report.markdown == 1
    assert not (docs / "tokens.rst.md").exists()
    prepared.close()
    assert not prepared.folder.exists()
    prepared.close()  # safe a second time


def test_cache_is_used_again_and_rebuilt_after_a_change(tmp_path):
    docs = make_docs(tmp_path, md=True, rst=True)
    cache = tmp_path / "cache"
    first = prepare_documentation(docs, other_formats=True, cache_dir=cache)
    marker = first.folder / "marker.txt"
    marker.write_text("kept", encoding="utf-8")
    first.close()
    assert first.folder.is_dir()  # a cache stays
    second = prepare_documentation(docs, other_formats=True, cache_dir=cache)
    assert second.folder == first.folder
    assert marker.exists()  # the folder was not built again
    assert second.report.converted == {".rst": 1}
    assert second.report.folder == second.folder
    assert not any(p.suffix == ".json" for p in second.folder.rglob("*"))
    (docs / "tokens.rst").write_text(RST_PAGE + "\nMore text.\n", encoding="utf-8")
    third = prepare_documentation(docs, other_formats=True, cache_dir=cache)
    assert third.folder != first.folder
    assert not (third.folder / "marker.txt").exists()
    assert "More text." in (third.folder / "tokens.rst.md").read_text("utf-8")


def test_a_bad_mirror_becomes_input_error(tmp_path, monkeypatch):
    import secreqgen.documents.mirror as mirror

    def bad(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mirror, "build_mirror", bad)
    docs = make_docs(tmp_path, md=True, rst=True)
    with pytest.raises(InputError, match="disk full"):
        prepare_documentation(docs, other_formats=True)


# 4 ---------------------------------------------------------------------------


def report(converted, skipped=()):
    return SimpleNamespace(converted=converted, skipped=list(skipped))


def test_docs_note():
    assert docs_note(None) is None
    assert docs_note(report({".rst": 10, ".txt": 2}, [("a", "x")])) == (
        "12 files of other types were converted to Markdown (.rst 10, .txt 2). "
        "1 file was skipped. This is outside the tested setup."
    )
    assert docs_note(report({".pdf": 1})) == (
        "1 file of another type was converted to Markdown (.pdf 1). "
        "This is outside the tested setup."
    )
    assert "2 files were skipped." in docs_note(
        report({".pdf": 1}, [("a", "x"), ("b", "y")])
    )
    assert docs_note(report({})).startswith("No file of other types")


# 5 ---------------------------------------------------------------------------


def threat_options(root: Path, docs: Path, **extra) -> ThreatOptions:
    return ThreatOptions(
        input_file=write_stories(root),
        docs=docs,
        check=False,
        labels=False,
        **extra,
    )


def test_threats_with_a_converted_file(clean_env):
    docs = make_docs(clean_env, md=True, rst=True)
    run = run_threat_lists(
        threat_options(clean_env, docs, other_formats=True),
        make_client=factory(ThreatWriter()),
    )
    passages = run.lists[0].model["passages"]
    assert any(".rst.md" in p["header"] for p in passages)
    assert run.docs_report.converted == {".rst": 1}
    assert not run.docs_report.folder.exists()  # the temporary mirror is gone
    run = run_threat_lists(
        threat_options(clean_env, docs),
        make_client=factory(ThreatWriter()),
    )
    assert not any(".rst.md" in p["header"] for p in run.lists[0].model["passages"])
    assert run.docs_report is None


# 6 ---------------------------------------------------------------------------


def test_threats_md_only_is_equal_with_the_option_on(clean_env):
    docs = make_docs(clean_env, md=True, rst=False)
    off = run_threat_lists(
        threat_options(clean_env, docs), make_client=factory(ThreatWriter())
    )
    on = run_threat_lists(
        threat_options(clean_env, docs, other_formats=True),
        make_client=factory(ThreatWriter()),
    )
    assert on.docs_report is None
    assert without_time(on.lists[0].model) == without_time(off.lists[0].model)


# 7 ---------------------------------------------------------------------------


def req_options(root: Path, docs: Path, **extra) -> RequirementsOptions:
    return RequirementsOptions(
        input_file=write_stories(root), docs=docs, output_format="json", **extra
    )


def test_requirements_md_only_is_equal_with_the_option_on(clean_env):
    docs = make_docs(clean_env, md=True, rst=False)
    off = run_requirements(
        req_options(clean_env, docs), make_client=factory(ReqClient())
    )
    on = run_requirements(
        req_options(clean_env, docs, other_formats=True),
        make_client=factory(ReqClient()),
    )
    assert on.docs_report is None and off.docs_report is None
    assert [r.requirements for r in on.results] == [
        r.requirements for r in off.results
    ]
    assert [r.retrieval_context for r in on.results] == [
        r.retrieval_context for r in off.results
    ]


def test_requirements_with_a_converted_file(clean_env):
    docs = make_docs(clean_env, md=True, rst=True)
    run = run_requirements(
        req_options(clean_env, docs, other_formats=True),
        make_client=factory(ReqClient()),
    )
    assert run.docs_report.converted == {".rst": 1}
    assert run.results
    assert not run.docs_report.folder.exists()


# 8 ---------------------------------------------------------------------------


def test_rst_only_folder(clean_env):
    docs = make_docs(clean_env, md=False, rst=True)
    run = run_threat_lists(
        threat_options(clean_env, docs, other_formats=True),
        make_client=factory(ThreatWriter()),
    )
    assert run.docs_report.converted == {".rst": 1}
    assert run.lists[0].model["passages"]
    with pytest.raises(Exception) as error:
        run_threat_lists(
            threat_options(clean_env, docs, strict_input=True),
            make_client=factory(ThreatWriter()),
        )
    assert isinstance(error.value, InputError)


def test_no_new_call_when_off(clean_env, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("prepare_documentation was called")

    monkeypatch.setattr(documentation, "prepare_documentation", fail)
    docs = make_docs(clean_env, md=True, rst=True)
    options = threat_options(clean_env, docs)
    run_threat_lists(options, make_client=factory(ThreatWriter()))
    assert options.docs == docs and options.other_formats is False
