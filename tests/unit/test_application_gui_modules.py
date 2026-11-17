"""Tests of the GUI helper modules of the application layer."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from secreqgen.application import prompt_sets, serialise, server, threat_view
from secreqgen.application.errors import InputError, ModelServerError
from secreqgen.exceptions import GenerationError

# ---------------------------------------------------------------- fixtures


def _row(rid, kind, second, signs=(), source="P1"):
    return {
        "id": rid,
        "type": kind,
        "threat": f"Threat {rid}.",
        "harm": "false identity" if kind == "attack" else "",
        "story_part": "Webhooks",
        "source": source,
        "quote": f"Quote of {rid} in the   passage.",
        "requirement": f"Requirement {rid}.",
        "from_extra_group": False,
        "actor": "trusted_insider",
        "stride": "S",
        "weak_signs": list(signs),
        "second_model": second,
    }


def _model(checker="qwen3.8:27b", calls_ok=(True, True), passages=True, rows=None):
    if rows is None:
        rows = [
            _row("T1", "rule", "accepts"),
            _row("T2", "attack", "accepts"),
            _row("T3", "rule", "refuses", ["the quote is a fragment"]),
            _row("T4", "attack", "refuses"),
            _row("T5", "attack", ""),
            _row("T6", "rule", "accepts", ["a weak sign"]),
        ]
    return {
        "draft_notice": "DRAFT for a person to check.",
        "prompt_set": "threats_v5b_labels",
        "product": "mattermost",
        "model": "gemma4:26b",
        "story": {
            "id": "MM-1",
            "title": "Title",
            "story": "As a user",
            "acceptance_criteria": [],
        },
        "seconds": 12,
        "calls": [{"group": i + 1, "ok": ok} for i, ok in enumerate(calls_ok)],
        "checker": checker,
        "about": "One two three",
        "passages": (
            [
                {
                    "ref": "P1",
                    "header": "page.md > Top > Sub",
                    "text": "### [1] page.md > Top > Sub (Relevance: 0.50)"
                    "\nSection: Sub\n\nBody.",
                    "group": 1,
                }
            ]
            if passages
            else []
        ),
        "rows": rows,
        "dropped": [{"group": 1}],
    }


def _page_lines(model):
    from secreqgen.threats.render import render_markdown

    return re.findall(r"^\*\*(\d+)\. (.*)\*\*$", render_markdown(model), re.M)


def _group_lines(model):
    grouped = threat_view.group_threat_rows(model)
    return [
        (str(item["number"]), item["row"]["threat"])
        for g in grouped["groups"]
        for item in g["rows"]
    ]


# ---------------------------------------------------------------- threat_view


@pytest.mark.parametrize("checker", ["qwen3.8:27b", None])
def test_groups_match_the_review_page(checker):
    model = _model(checker=checker)
    assert _group_lines(model) == _page_lines(model)
    assert len(_group_lines(model)) == 6


def test_group_titles_and_notes_with_checker():
    model = _model()
    from secreqgen.threats.render import render_markdown

    page = render_markdown(model)
    grouped = threat_view.group_threat_rows(model)
    assert [g["key"] for g in grouped["groups"]] == [
        "accepts",
        "refuses",
        "no_verdict",
    ]
    for g in grouped["groups"]:
        assert f"## {g['title']}" in page
        if g["note"]:
            assert g["note"] in page
    assert grouped["footer"].splitlines()[0] in page
    assert "Rows that the script removed: 1." in grouped["footer"]


def test_group_without_checker_and_empty_groups():
    model = _model(checker=None)
    keys = [g["key"] for g in threat_view.group_threat_rows(model)["groups"]]
    assert keys == ["no_weak_sign", "weak_sign"]
    only = _model(rows=[_row("T1", "attack", "accepts")])
    keys = [g["key"] for g in threat_view.group_threat_rows(only)["groups"]]
    assert keys == ["accepts"]


def test_notices_match_the_review_page():
    from secreqgen.threats.render import render_markdown

    cases = [
        _model(calls_ok=(True, False)),
        _model(passages=False, rows=[]),
        _model(rows=[]),
        _model(rows=[_row("T1", "attack", "refuses")]),
    ]
    for model in cases:
        page = render_markdown(model)
        notices = threat_view.group_threat_rows(model)["notices"]
        assert notices
        for notice in notices:
            assert notice in page


def test_labels_and_limits():
    labels = threat_view.threat_labels()
    assert labels["actors"]["other"] == "other"
    assert labels["stride"]["S"].startswith("S, spoofing")
    assert "disclosure of data" in labels["harms"]
    assert len(labels["weak_sign_texts"]) == 4
    limits = threat_view.threat_limits()
    assert limits["about_max_words"] == 120
    assert limits["prompt_set"] == "threats_v5b_labels"


def test_locate_quote_other_spacing_case_and_link():
    passage = (
        "Intro. By  enabling THIS feature, users may see the "
        "[docs](https://example.org/a/b) page. End."
    )
    spans = threat_view.locate_quote(
        passage, "by enabling this\nfeature, users may see the docs page"
    )
    assert len(spans) == 1
    start, end = spans[0]
    assert passage[start:end].startswith("By  enabling")
    assert passage[start:end].endswith("page")


def test_locate_quote_link_target_words_do_not_match():
    passage = "See [docs](https://example.org/secret) now."
    assert threat_view.locate_quote(passage, "docs secret") == []


def test_locate_quote_curly_quotes_and_ellipsis():
    passage = "The user’s token expires. Many words sit here. The admin resets it."
    spans = threat_view.locate_quote(
        passage, "the user's token expires ... the admin resets it"
    )
    assert len(spans) == 2
    assert passage[spans[0][0] : spans[0][1]] == "The user’s token expires"
    assert passage[spans[1][0] : spans[1][1]] == "The admin resets it"
    assert threat_view.locate_quote(passage, "no such words here") == []


def test_split_passage_and_status():
    model = _model()
    part = threat_view.split_passage(model["passages"][0])
    assert part["page"] == "page.md"
    assert part["section"] == "Top > Sub"
    assert part["body"].startswith("Section: Sub")
    assert "Relevance" not in part["body"]
    assert threat_view.story_status(model) == "ok"
    assert threat_view.story_status(_model(calls_ok=(True, False))) == "partial"
    assert threat_view.story_status(_model(calls_ok=(False, False))) == "failed"
    assert threat_view.story_status(_model(passages=False)) == "empty"


def test_export_kept():
    model = _model()
    verdicts = {"T2": "keep", "T1": "delete", "T4": "keep"}
    data = json.loads(threat_view.export_kept(model, verdicts, fmt="json"))
    assert [r["id"] for r in data["kept"]] == ["T2", "T4"]
    assert data["kept"][0]["page"] == "page.md"
    assert data["kept"][0]["section"] == "Top > Sub"
    assert data["prompt_set"] == "threats_v5b_labels"
    text = threat_view.export_kept(model, verdicts, fmt="markdown")
    assert re.findall(r"^\*\*\d+\. ", text, re.M) == ["**1. ", "**4. "]
    assert "Threat T2." in text and "Threat T1." not in text
    with pytest.raises(InputError):
        threat_view.export_kept(model, verdicts, fmt="xml")


# ---------------------------------------------------------------- server


def _fake_get(response=None, error=None):
    def fake(url, timeout=None):
        assert url.endswith("/api/tags")
        assert isinstance(timeout, httpx.Timeout)
        if error:
            raise error
        return response

    return fake


def test_server_reachable_with_models(monkeypatch):
    body = {
        "models": [
            {
                "name": "gemma4:26b",
                "size": 100,
                "details": {
                    "family": "gemma",
                    "parameter_size": "26B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {"model": "qwen3.8:27b"},
            "broken",
        ]
    }
    resp = httpx.Response(200, json=body)
    monkeypatch.setattr(httpx, "get", _fake_get(resp))
    status = server.check_model_server(base_url="http://x:1/")
    assert status.reachable and status.error is None
    assert [m.name for m in status.models] == ["gemma4:26b", "qwen3.8:27b"]
    assert status.models[0].parameter_size == "26B"
    assert status.models[1].size_bytes is None
    assert server.model_available(status, "gemma4")
    assert not server.model_available(status, "")
    assert server.model_check_text(status, ["gemma4:26b"]) == []
    text = server.model_check_text(status, ["nope:1b"])
    assert text == ["The model nope:1b is missing. Run: ollama pull nope:1b"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectError("x"), "Nothing answers at http://x:1"),
        (httpx.ReadTimeout("x"), "timeout after 3 s"),
        (httpx.DecodingError("x"), "No valid answer"),
        (OSError("x"), "No valid answer"),
        (RuntimeError("x"), "Check failed"),
    ],
)
def test_server_errors_never_raise(monkeypatch, error, expected):
    monkeypatch.setattr(httpx, "get", _fake_get(error=error))
    status = server.check_model_server(base_url="http://x:1")
    assert not status.reachable
    assert expected in status.error
    assert "does not answer" in server.model_check_text(status, ["m"])[0]


def test_server_bad_answers(monkeypatch):
    for resp, expected in [
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, content=b"not json"), "not JSON"),
        (httpx.Response(200, json=[1]), "no model list"),
    ]:
        monkeypatch.setattr(httpx, "get", _fake_get(resp))
        status = server.check_model_server(base_url="http://x:1")
        assert not status.reachable
        assert expected in status.error


def test_server_base_url_from_config(monkeypatch):
    seen = []

    def fake(url, timeout=None):
        seen.append(url)
        return httpx.Response(200, json={"models": []})

    monkeypatch.setattr(httpx, "get", fake)

    @dataclass
    class Llm:
        base_url: str = "http://cfg:9"

    @dataclass
    class Cfg:
        llm: Llm

    server.check_model_server(Cfg(Llm()))
    assert seen == ["http://cfg:9/api/tags"]


# ---------------------------------------------------------------- serialise


@dataclass
class _Story:
    id: str = "S1"
    title: str = "T"
    story: str = "As a user"
    acceptance_criteria: tuple = ("a",)


def test_error_to_dict():
    d = serialise.error_to_dict(ModelServerError("down", model="m", details="d"))
    assert d == {
        "type": "ModelServerError",
        "message": "down",
        "details": "d",
        "model": "m",
    }
    d = serialise.error_to_dict(ValueError("plain"))
    assert d["message"] == "plain" and d["model"] is None


def test_threat_run_round_trip_old_result_class():
    from secreqgen.application.threats import (
        ThreatListOutcome,
        ThreatListSummary,
        ThreatRun,
    )

    summary = ThreatListSummary(1, 1, 0, 1, 1, 0, 2, 5)
    outcome = ThreatListOutcome("S1", {"rows": []}, summary, Path("out/S1"))
    run = ThreatRun(
        writer="w",
        checker=None,
        lists=[outcome],
        failures=[("S2", GenerationError("bad", "why"))],
        skipped=[(3, ValueError("x"))],
    )
    data = json.loads(json.dumps(serialise.threat_run_to_dict(run)))
    assert data["lists"][0]["summary"]["rows"] == 1
    assert data["lists"][0]["folder"] == str(Path("out/S1"))
    assert data["failures"][0]["error"]["details"] == "why"
    assert data["skipped"] == [{"position": 3, "message": "x"}]
    assert data["stopped"] is False and data["aborted"] is None
    assert data["not_run"] == []
    slim = serialise.threat_run_to_dict(run, include_models=False)
    assert "model" not in slim["lists"][0]
    assert serialise.story_to_dict(_Story())["acceptance_criteria"] == ["a"]


def test_requirements_run_round_trip():
    from secreqgen.generation.schemas import GenerationResult, UserStory

    context = (
        "### [1] a.md > A (Relevance: 0.50)\nSection: A\n\ntext one"
        "\n\n---\n\n"
        "### [2] b.md > B (Relevance: 0.50)\nSection: B\n\ntext two"
    )
    result = GenerationResult(
        user_story=UserStory(id="S1", title="T", story="As a user"),
        requirements=[],
        model_used="m",
        rag_enabled=True,
        retrieval_context=context,
    )
    bad = result.model_copy(update={"retrieval_context": "odd text"})

    class Run:
        results = [result, bad]
        failures = []
        skipped = []

    data = json.loads(json.dumps(serialise.requirements_run_to_dict(Run())))
    assert [p["n"] for p in data["results"][0]["passages"]] == [1, 2]
    assert data["results"][0]["passages"][0]["header"] == "a.md > A"
    assert data["results"][1]["passages"] == []
    assert data["tested_prompts"] is True and data["model"] == ""


# ---------------------------------------------------------------- prompt_sets


def test_tested_sets_and_listing(tmp_path):
    tested = prompt_sets.tested_prompt_sets()
    assert [t.kind for t in tested] == ["threats", "requirements"]
    assert all(t.tested for t in tested)
    req = tested[1]
    zero = [f for f in req.files if f.name == "zero_shot_user_prompt.txt"][0]
    assert zero.used is False
    user = [f for f in tested[0].files if f.name == "user_prompt.txt"][0]
    assert "$passages" in [f"${n}" for n in user.placeholders]
    assert len(user.sha256) == 64
    assert prompt_sets.list_user_prompt_sets(tmp_path, "threats") == []


def test_copy_list_delete(tmp_path):
    folder = prompt_sets.copy_prompt_set("threats", "mine", tmp_path)
    assert folder == tmp_path / "threats" / "mine"
    assert prompt_sets.is_tested("threats", folder)
    listed = prompt_sets.list_user_prompt_sets(tmp_path, "threats")
    assert [s.name for s in listed] == ["mine"] and listed[0].tested
    with pytest.raises(InputError):
        prompt_sets.copy_prompt_set("threats", "mine", tmp_path)
    prompt_sets.check_prompt_folder("threats", folder)
    prompt_sets.delete_user_prompt_set("threats", "mine", tmp_path)
    assert not folder.exists()
    with pytest.raises(InputError):
        prompt_sets.delete_user_prompt_set("threats", "mine", tmp_path)


@pytest.mark.parametrize(
    "name", ["../x", "a/b", "A", "", "-a", "a" * 41, "a b", "a.b", ".."]
)
def test_bad_names_are_refused(tmp_path, name):
    with pytest.raises(InputError):
        prompt_sets.copy_prompt_set("threats", name, tmp_path)
    with pytest.raises(InputError):
        prompt_sets.delete_user_prompt_set("threats", name, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bad_kind_and_file_name(tmp_path):
    with pytest.raises(InputError):
        prompt_sets.copy_prompt_set("other", "ok", tmp_path)
    folder = prompt_sets.copy_prompt_set("threats", "mine", tmp_path)
    for filename in ("../user_prompt.txt", "notes.txt", "rag_user_prompt.txt"):
        with pytest.raises(InputError):
            prompt_sets.write_prompt_file(folder, "threats", filename, "x")


def test_write_prompt_file_checks(tmp_path):
    folder = prompt_sets.copy_prompt_set("threats", "mine", tmp_path)
    path = folder / "check_prompt.txt"
    original = path.read_bytes()
    text = original.decode("utf-8")
    # a missing placeholder
    problems = prompt_sets.write_prompt_file(
        folder, "threats", "check_prompt.txt", text.replace("$quote", "")
    )
    assert any("$quote is missing" in p and p.startswith("Error: ") for p in problems)
    assert path.read_bytes() == original
    # an unknown placeholder
    problems = prompt_sets.write_prompt_file(
        folder, "threats", "check_prompt.txt", text + "\n$extra"
    )
    assert any("$extra is not known" in p for p in problems)
    assert path.read_bytes() == original
    # a stray dollar sign
    problems = prompt_sets.write_prompt_file(
        folder, "threats", "check_prompt.txt", text + "\ncost $"
    )
    assert any("$$" in p for p in problems)
    assert path.read_bytes() == original
    # a good change with a soft warning
    new = text.replace("FAULT", "BAD")
    problems = prompt_sets.write_prompt_file(
        folder, "threats", "check_prompt.txt", new
    )
    assert problems and all(p.startswith("Warning: ") for p in problems)
    assert path.read_text(encoding="utf-8") == new
    assert not prompt_sets.is_tested("threats", folder)
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []
    # a set with a hard problem on disk is refused
    path.write_text(text + "\n$extra", encoding="utf-8")
    with pytest.raises(InputError, match="check_prompt.txt"):
        prompt_sets.check_prompt_folder("threats", folder)


def test_write_refuses_big_and_empty_text(tmp_path):
    folder = prompt_sets.copy_prompt_set("requirements", "mine", tmp_path)
    for text in ("", "x" * 70000):
        problems = prompt_sets.write_prompt_file(
            folder, "requirements", "system_prompt.txt", text
        )
        assert problems[0].startswith("Error: ")
    problems = prompt_sets.write_prompt_file(
        folder, "requirements", "system_prompt.txt", "Use $name here."
    )
    assert problems == [
        "Warning: $name stays in the prompt as written: it is not replaced."
    ]


def test_check_prompt_folder_missing_file(tmp_path):
    folder = prompt_sets.copy_prompt_set("threats", "mine", tmp_path)
    (folder / "labels_prompt.txt").unlink()
    with pytest.raises(InputError, match="labels_prompt.txt: the file is missing"):
        prompt_sets.check_prompt_folder("threats", folder)


def test_symlink_set_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    (root / "threats").mkdir(parents=True)
    (root / "threats" / "link").symlink_to(outside)
    with pytest.raises(InputError):
        prompt_sets.delete_user_prompt_set("threats", "link", root)
    assert prompt_sets.list_user_prompt_sets(root, "threats") == []


def test_load_threat_prompts_and_generator_class(tmp_path):
    folder = prompt_sets.copy_prompt_set("threats", "mine", tmp_path)
    system, user, labels, check = prompt_sets.load_threat_prompts(folder)
    assert isinstance(system, str)
    assert "passages" in user.get_identifiers()
    (folder / "user_prompt.txt").write_bytes(b"\xff\xfe")
    with pytest.raises(InputError):
        prompt_sets.load_threat_prompts(folder)
    (folder / "user_prompt.txt").unlink()
    with pytest.raises(InputError):
        prompt_sets.load_threat_prompts(folder)


def test_generator_subclass_keeps_attribute_names(tmp_path):
    from secreqgen.threats import ThreatListGenerator

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nThe admin must set a password.\n")
    folder = prompt_sets.copy_prompt_set("threats", "mine", tmp_path / "root")
    base = ThreatListGenerator(object(), docs, product="x")
    cls = prompt_sets.build_threat_generator_class()
    assert cls is prompt_sets.build_threat_generator_class()
    sub = cls(object(), docs, prompts_dir=folder, prompt_label="user:mine", product="x")
    assert set(vars(sub)) - {"prompt_label"} == set(vars(base))
    assert sub._system == base._system
    assert sub._user.template == base._user.template
    assert sub.prompt_label == "user:mine"
