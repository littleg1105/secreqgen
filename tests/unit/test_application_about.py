"""Tests of the "About the product" use case. No model and no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from secreqgen.application.about import (
    AboutOptions,
    AboutRun,
    about_draft,
    run_about,
)
from secreqgen.application.errors import InputError, ModelServerError
from secreqgen.application.inputs import check_about
from secreqgen.application.steps import StepEvent
from secreqgen.context import AboutDraft, ContextError
from secreqgen.threats import TESTED_WRITER

from .test_application_system_context import (
    Stand,
    cfg,
    factory,
    good_answer,
)

DATA = json.loads(
    (Path(__file__).parent / "data" / "about_rule_2026-11-17.json").read_text(
        encoding="utf-8"
    )
)
FILE_CASE = DATA["cases"][0]


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "shop"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(
        "# Shop\nA web shop for small stores. Uses PostgreSQL.\n", encoding="utf-8"
    )
    (root / "package.json").write_text(
        '{"name": "shop", "dependencies": {"express": "4"}}'
    )
    (root / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:15\n"
    )
    (root / "docs" / "index.md").write_text("Orders and payments overview.\n")
    return root


def long_answer() -> dict:
    answer = good_answer()
    answer["description_source"] = "F2"
    answer["features"] = [
        {
            "group": f"Group {g}",
            "items": [
                {"text": f"Feature {g}.{n} with a few more words", "source": "F5"}
                for n in range(6)
            ],
        }
        for g in range(4)
    ]
    return answer


def client(answer=None):
    return factory(Stand([json.dumps(answer or long_answer())]))


def context_file(tmp_path: Path, data=None) -> Path:
    path = tmp_path / "context.yaml"
    path.write_text(
        yaml.safe_dump(
            FILE_CASE["file_data"] if data is None else data, sort_keys=False
        ),
        encoding="utf-8",
    )
    return path


# a folder --------------------------------------------------------------------


def test_a_folder_runs_the_writer_and_makes_the_paragraph(codebase):
    seen = []
    run = run_about(
        AboutOptions(codebase, product="Shop"),
        cfg=cfg(),
        make_client=factory(Stand([json.dumps(long_answer())]), seen),
    )
    assert isinstance(run, AboutRun) and isinstance(run.draft, AboutDraft)
    assert run.writer == TESTED_WRITER and run.output is None
    assert seen == [(True, TESTED_WRITER)]
    assert run.result is not None and run.result["context"]["name"] == "Shop"
    assert run.draft == about_draft(run.result)
    assert run.draft.paragraph.startswith("Shop: A web shop for small stores.")
    assert run.draft.lines[0] == {
        "where": "description",
        "text": "A web shop for small stores.",
        "path": "README.md",
    }
    assert all(line["path"] == "docs/index.md" for line in run.draft.lines[1:])
    assert run.draft.left_out
    assert len(run.draft.paragraph.split()) <= 120
    assert "Features:" in run.draft.paragraph


def test_a_folder_with_a_model_option(codebase):
    seen = []
    run = run_about(
        AboutOptions(codebase, writer_model="m:1"),
        cfg=cfg(),
        make_client=factory(Stand([json.dumps(long_answer())]), seen),
    )
    assert run.writer == "m:1" and seen == [(True, "m:1")]


def test_a_description_with_no_source_has_an_empty_path(codebase):
    answer = long_answer()
    del answer["description_source"]
    run = run_about(AboutOptions(codebase), cfg=cfg(), make_client=client(answer))
    assert run.draft.lines[0]["where"] == "description"
    assert run.draft.lines[0]["path"] == ""


def test_the_steps_of_the_writer_go_to_the_callback(codebase):
    events: list[StepEvent] = []
    run_about(
        AboutOptions(codebase),
        cfg=cfg(),
        make_client=client(),
        on_step=events.append,
    )
    assert [(e.step, e.state) for e in events] == [
        ("scan", "start"),
        ("scan", "done"),
        ("write", "start"),
        ("write", "done"),
        ("check", "done"),
    ]


def test_the_output_file_holds_the_paragraph_and_one_newline(codebase, tmp_path):
    out = tmp_path / "made" / "about.txt"
    run = run_about(AboutOptions(codebase, output=out), cfg=cfg(), make_client=client())
    assert run.output == out
    assert out.read_bytes() == (run.draft.paragraph + "\n").encode("utf-8")


def test_no_output_writes_no_file(codebase, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    run_about(AboutOptions(codebase), cfg=cfg(), make_client=client())
    assert set(tmp_path.rglob("*")) == before


def test_the_output_is_checked_before_any_work(codebase, tmp_path):
    out = tmp_path / "about.txt"
    out.write_text("MINE\n", encoding="utf-8")
    stand = Stand([json.dumps(long_answer())])
    with pytest.raises(InputError, match="The output file exists"):
        run_about(
            AboutOptions(codebase, output=out), cfg=cfg(), make_client=factory(stand)
        )
    assert out.read_text(encoding="utf-8") == "MINE\n"
    assert len(stand.answers) == 1  # the model was not asked
    with pytest.raises(InputError, match="is a folder"):
        run_about(
            AboutOptions(codebase, output=tmp_path),
            cfg=cfg(),
            make_client=factory(stand),
        )
    assert len(stand.answers) == 1


def test_a_paragraph_that_the_command_writes_is_accepted_by_about_file(
    codebase, tmp_path
):
    out = tmp_path / "about.txt"
    run_about(AboutOptions(codebase, output=out), cfg=cfg(), make_client=client())
    text = out.read_text(encoding="utf-8")
    folded = check_about(text)
    assert folded == " ".join(text.split())
    assert folded == run_about_paragraph(codebase)


def run_about_paragraph(codebase) -> str:
    return run_about(
        AboutOptions(codebase), cfg=cfg(), make_client=client()
    ).draft.paragraph


def test_the_file_of_a_file_source_is_accepted_by_about_file(tmp_path):
    out = tmp_path / "about.txt"
    run_about(AboutOptions(context_file(tmp_path), output=out))
    assert check_about(out.read_text(encoding="utf-8")) == FILE_CASE["paragraph"]


# a file ----------------------------------------------------------------------


def test_a_file_runs_no_model(tmp_path):
    def no_client(*args, **kwargs):
        raise AssertionError("a model client was made")

    run = run_about(
        AboutOptions(context_file(tmp_path)), cfg=cfg(), make_client=no_client
    )
    assert run.writer is None and run.result is None and run.output is None
    assert run.draft.paragraph == FILE_CASE["paragraph"]
    assert all(line["path"] == "" for line in run.draft.lines)


def test_a_file_with_an_output(tmp_path):
    out = tmp_path / "x" / "about.txt"
    run = run_about(AboutOptions(context_file(tmp_path), output=out))
    assert run.output == out
    assert out.read_text(encoding="utf-8") == FILE_CASE["paragraph"] + "\n"


def test_the_product_replaces_the_name_of_a_file(tmp_path):
    run = run_about(AboutOptions(context_file(tmp_path), product=" My Vault "))
    assert run.draft.paragraph.startswith("My Vault: Open-source password manager")
    run = run_about(AboutOptions(context_file(tmp_path), product="  "))
    assert run.draft.paragraph.startswith("Bitwarden Server: ")
    entry = DATA["no_feature_list"]
    path = tmp_path / "keycloak.yaml"
    path.write_text(yaml.safe_dump({"system": entry["system"]}), encoding="utf-8")
    with pytest.raises(ContextError, match="no feature list"):
        run_about(AboutOptions(path, product="Other"))


def test_the_product_gives_the_name_to_a_file_with_no_name(tmp_path):
    data = {"features": {"a": ["Orders"]}}
    run = run_about(AboutOptions(context_file(tmp_path, data), product="Shop"))
    assert run.draft.paragraph == "Shop. Main parts: a. Features: Orders."


def test_a_file_with_the_system_key(tmp_path):
    data = {
        "system": {"name": "Keycloak", "description": "An IAM tool."},
        "features": {"login": ["Single sign-on"]},
    }
    run = run_about(AboutOptions(context_file(tmp_path, data), product="KC"))
    assert run.draft.paragraph.startswith("KC: An IAM tool.")


# errors ----------------------------------------------------------------------


def test_a_model_option_has_no_use_with_a_file(tmp_path):
    for model in ("m:1", ""):
        with pytest.raises(InputError) as e:
            run_about(AboutOptions(context_file(tmp_path), writer_model=model))
        assert str(e.value) == (
            "The option --model has no use with a system context file."
        )


def test_a_path_that_is_not_there(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(InputError) as e:
        run_about(AboutOptions(missing))
    assert str(e.value) == f"Not found: {missing}"


@pytest.mark.parametrize(
    "text", ["- a\n- b\n", "just text", "a: [unclosed", "7", ""], ids=str
)
def test_a_file_that_is_no_yaml_mapping(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InputError) as e:
        run_about(AboutOptions(path))
    assert str(e.value) == f"{path} is not a system context file (YAML)."


def test_a_file_that_is_no_text(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_bytes(b"\xff\xfe\x00\x01 \x80")
    with pytest.raises(InputError, match="is not a system context file"):
        run_about(AboutOptions(path))


def test_a_file_with_no_name_or_no_features(tmp_path):
    with pytest.raises(ContextError, match="The file has no product name."):
        run_about(AboutOptions(context_file(tmp_path, {"features": {"a": ["b"]}})))
    with pytest.raises(ContextError, match="The file has no feature list."):
        run_about(AboutOptions(context_file(tmp_path, {"project": {"name": "S"}})))


def test_no_file_is_written_when_the_work_fails(tmp_path):
    out = tmp_path / "about.txt"
    with pytest.raises(ContextError):
        run_about(AboutOptions(context_file(tmp_path, {"project": {}}), output=out))
    assert not out.exists()


def test_unhealthy_client(codebase):
    with pytest.raises(ModelServerError, match="does not have the model"):
        run_about(
            AboutOptions(codebase, writer_model="m:1"),
            cfg=cfg(),
            make_client=factory(Stand([], healthy=False)),
        )


def test_folder_errors(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InputError, match="no README"):
        run_about(AboutOptions(empty), cfg=cfg(), make_client=factory(Stand([])))


def test_model_answer_errors_pass_through(codebase):
    with pytest.raises(ContextError, match="no answer that can be read"):
        run_about(
            AboutOptions(codebase),
            cfg=cfg(),
            make_client=factory(Stand(["a", "b", "c"])),
        )


# about_draft -----------------------------------------------------------------


def test_about_draft_needs_a_context():
    for result in ({}, {"context": "x"}, {"context": None}, None):
        with pytest.raises(ContextError):
            about_draft(result)  # type: ignore[arg-type]
    draft = about_draft({"context": {"name": "S", "description": "D.", "features": []}})
    assert draft.paragraph == "S: D."
    assert draft.lines[0]["where"] == "description"


def test_the_module_is_light():
    from .test_import_weight import forbidden_loaded

    heavy = ["chromadb", "torch", "sentence_transformers", "secreqgen.retrieval"]
    found = forbidden_loaded(
        ["secreqgen.application.about", "secreqgen.context.about"],
        heavy + ["typer", "rich", "secreqgen.threats", "yaml"],
    )
    assert found == []


def test_a_file_that_appears_while_the_model_works_is_never_replaced(
    codebase, tmp_path
):
    out = tmp_path / "about.txt"

    class Late(Stand):
        def generate(self, prompt, system_prompt=None, format=None):
            out.write_text("MY OWN FILE\n", encoding="utf-8")
            return super().generate(prompt, system_prompt, format)

    stand = Late([json.dumps(long_answer())])
    with pytest.raises(InputError, match="The output file exists"):
        run_about(
            AboutOptions(codebase, output=out), cfg=cfg(), make_client=factory(stand)
        )
    assert out.read_text(encoding="utf-8") == "MY OWN FILE\n"
