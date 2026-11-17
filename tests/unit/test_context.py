"""Tests of the system context package. No model, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml
from secreqgen.context import (
    DRAFT_NOTICE,
    ContextError,
    SystemContextWriter,
    render_yaml,
    scan_codebase,
    source_lines,
)
from secreqgen.context.checks import check_answer
from secreqgen.context.scan import TOTAL_WORDS

SECRET = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture()
def codebase(tmp_path):
    root = tmp_path / "shop"
    (root / "docs").mkdir(parents=True)
    (root / "src" / "auth").mkdir(parents=True)
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "README.md").write_text(
        "# Shop\nA web shop for small stores. Uses PostgreSQL.\n"
        f"api_key: {SECRET}\npassword = hunter2hunter2\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"name": "shop", "dependencies": {"express": "4"}}'
    )
    (root / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:15\n"
    )
    (root / "docs" / "index.md").write_text("Orders and payments overview.\n")
    (root / "src" / "auth" / "login.py").write_text("def login(): pass\n")
    (root / ".env").write_text("DB_PASSWORD=supersecretvalue\n")
    (root / "node_modules" / "x" / "README.md").write_text("MODULE JUNK TEXT\n")
    (root / "config.yml").write_text(f"api_key: {SECRET}\n")
    return root


def _good_answer():
    return {
        "name": "Shop",
        "description": "A web shop for small stores.",
        "tech_stack": [
            {"kind": "database", "value": "PostgreSQL 15", "source": "F2"},
            {"kind": "framework", "value": "Express", "source": "F3"},
        ],
        "security_profile": "Medium - holds orders",
        "features": [
            {
                "group": "Orders",
                "items": [{"text": "Orders and payments", "source": "F5"}],
            }
        ],
        "data_sensitivity": {
            "critical": [{"text": "Passwords", "source": "F2"}],
            "high": [],
            "medium": [],
        },
    }


class Stand:
    model_name = "stand-in"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def generate(self, prompt, system_prompt=None, format=None):
        self.prompts.append(prompt)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


def _refs(facts):
    return {p.kind: p.ref for p in facts.parts}


class TestScan:
    def test_parts_order_and_kinds(self, codebase):
        facts = scan_codebase(codebase)
        kinds = [p.kind for p in facts.parts]
        assert kinds == [
            "tree",
            "readme",
            "manifest",
            "container",
            "docs",
            "security_paths",
        ]
        assert [p.ref for p in facts.parts] == [f"F{i}" for i in range(1, 7)]
        assert facts.name == "shop"

    def test_skipped_and_secret_files(self, codebase):
        facts = scan_codebase(codebase)
        everything = "\n".join(p.text + p.path for p in facts.parts)
        assert "MODULE JUNK" not in everything
        assert "node_modules" not in everything
        assert ".env" not in everything
        assert "supersecretvalue" not in everything
        assert facts.secret_files_skipped == 1

    def test_secret_values_removed(self, codebase):
        facts = scan_codebase(codebase)
        everything = "\n".join(p.text for p in facts.parts)
        assert SECRET not in everything
        assert "hunter2hunter2" not in everything
        assert "<removed>" in everything

    def test_budget(self, codebase):
        (codebase / "README.md").write_text(
            "\n".join(f"line {i} " + "word " * 9 for i in range(400)),
            encoding="utf-8",
        )
        facts = scan_codebase(codebase)
        readme = next(p for p in facts.parts if p.kind == "readme")
        assert len(readme.text.split()) <= 900
        assert sum(len(p.text.split()) for p in facts.parts) <= TOTAL_WORDS

    def test_nothing_useful(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"\x00\x01")
        with pytest.raises(ContextError):
            scan_codebase(tmp_path)
        with pytest.raises(ContextError):
            scan_codebase(tmp_path / "missing")


class TestChecks:
    def test_unknown_source_dropped(self, codebase):
        facts = scan_codebase(codebase)
        answer = _good_answer()
        answer["features"][0]["items"].append({"text": "Ghost", "source": "F99"})
        context, dropped = check_answer(answer, facts, None)
        texts = [i["text"] for g in context["features"] for i in g["items"]]
        assert "Ghost" not in texts
        assert {"what": "feature: Ghost", "why": "unknown source name"} in dropped

    def test_technology_not_in_files(self, codebase):
        facts = scan_codebase(codebase)
        answer = _good_answer()
        answer["tech_stack"].append(
            {"kind": "cache", "value": "Redis", "source": "F2"}
        )
        context, dropped = check_answer(answer, facts, None)
        assert "Redis" not in [t["value"] for t in context["tech_stack"]]
        assert any(d["why"] == "not in the files" for d in dropped)

    def test_source_is_corrected(self, codebase):
        facts = scan_codebase(codebase)
        refs = _refs(facts)
        answer = _good_answer()
        answer["tech_stack"] = [
            {"kind": "framework", "value": "express", "source": refs["readme"]}
        ]
        context, _ = check_answer(answer, facts, None)
        assert context["tech_stack"][0]["source"] == refs["manifest"]
        assert context["tech_stack"][0]["path"] == "package.json"

    def test_limits_and_profile(self, codebase):
        facts = scan_codebase(codebase)
        answer = _good_answer()
        answer["features"] = [
            {
                "group": f"G{n}",
                "items": [{"text": f"item {n} {k}", "source": "F2"} for k in range(8)],
            }
            for n in range(8)
        ]
        answer["features"][0]["items"].append(
            {"text": "w " * 13, "source": "F2"}
        )
        answer["features"][1]["items"].append(
            {"text": "ITEM 0 0", "source": "F2"}
        )
        answer["security_profile"] = "Great - fine"
        context, dropped = check_answer(answer, facts, None)
        assert len(context["features"]) == 6
        assert all(len(g["items"]) <= 6 for g in context["features"])
        assert context["security_profile"] == ""
        whys = {d["why"] for d in dropped}
        assert {"too long", "two times", "over the limit"} <= whys

    def test_strange_answer(self, codebase):
        facts = scan_codebase(codebase)
        for strange in (
            {},
            {"name": 5, "tech_stack": "x", "features": [1, "a", {"items": 3}]},
            {"data_sensitivity": [], "security_profile": 7, "description": []},
        ):
            context, _ = check_answer(strange, facts, "Mine")
            assert context["name"] in ("Mine", "shop")
        context, _ = check_answer([], facts, None)
        assert context["name"] == "shop"


class TestRender:
    def test_yaml_form(self, codebase):
        facts = scan_codebase(codebase)
        context, _ = check_answer(_good_answer(), facts, None)
        loaded = yaml.safe_load(render_yaml(context))
        assert set(loaded) <= {"project", "features", "data_sensitivity"}
        assert loaded["project"]["name"] == "Shop"
        assert {"database": "PostgreSQL 15"} in loaded["project"]["tech_stack"]
        assert loaded["features"]["orders"] == ["Orders and payments"]
        assert loaded["data_sensitivity"] == {"critical": ["Passwords"]}

    def test_source_lines(self, codebase):
        facts = scan_codebase(codebase)
        context, _ = check_answer(_good_answer(), facts, None)
        lines = source_lines(context)
        assert len(lines) == 4
        assert lines[0]["where"] == "tech_stack"
        assert {"where", "text", "path"} == set(lines[0])
        assert any(x["where"] == "features: Orders" for x in lines)
        assert any(x["where"] == "data_sensitivity: critical" for x in lines)


class TestPipeline:
    def test_result_and_stages(self, codebase):
        client = Stand([json.dumps(_good_answer())])
        stages = []
        result = SystemContextWriter(client).generate(
            codebase, "Shop", on_stage=lambda *a: stages.append(a)
        )
        assert set(result) == {
            "draft_notice",
            "folder_name",
            "model",
            "seconds",
            "calls",
            "facts",
            "context",
            "dropped",
            "yaml",
        }
        assert result["draft_notice"] == DRAFT_NOTICE
        assert result["model"] == "stand-in"
        assert result["facts"]["secret_files_skipped"] == 1
        assert [s[:2] for s in stages] == [
            ("scan", "start"),
            ("scan", "done"),
            ("write", "start"),
            ("write", "done"),
            ("check", "done"),
        ]
        assert stages[3][2] == {"attempt": 1, "answered": 1}
        assert "The person calls the product: Shop" in client.prompts[0]

    def test_retry_then_ok(self, codebase):
        client = Stand(["not json", RuntimeError("x"), json.dumps(_good_answer())])
        result = SystemContextWriter(client).generate(codebase)
        assert result["calls"][0]["attempts"] == 3
        assert result["calls"][0]["ok"] is True

    def test_three_bad_answers(self, codebase):
        client = Stand(["a", "b", "c"])
        with pytest.raises(ContextError, match="no answer that can be read"):
            SystemContextWriter(client).generate(codebase)

    def test_nothing_supported(self, codebase):
        bad = {"tech_stack": [{"kind": "x", "value": "Redis", "source": "F2"}]}
        client = Stand([json.dumps(bad)])
        with pytest.raises(ContextError, match="nothing that the files support"):
            SystemContextWriter(client).generate(codebase)

    def test_callback_error_ignored(self, codebase):
        def boom(*_a):
            raise RuntimeError("view")

        client = Stand([json.dumps(_good_answer())])
        result = SystemContextWriter(client).generate(codebase, on_stage=boom)
        assert result["context"]["name"] == "Shop"

    def test_prompt_holds_no_secret(self, codebase):
        client = Stand([json.dumps(_good_answer())])
        SystemContextWriter(client).generate(codebase)
        prompt = client.prompts[0]
        assert SECRET not in prompt
        assert "hunter2hunter2" not in prompt
        assert "supersecretvalue" not in prompt
        assert "DB_PASSWORD" not in prompt
