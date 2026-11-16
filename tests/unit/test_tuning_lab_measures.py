"""Tests of scripts/tuning_lib/lab_measures.py. Read only on the registered data. No network."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from tuning_lib import lab_measures as L  # noqa: E402
from tuning_lib import prompts as P  # noqa: E402
from tuning_lib.passages import doc_passages, split_context  # noqa: E402
from tuning_lib.paths import MODELS  # noqa: E402

_spec = importlib.util.spec_from_file_location("study2_names_orig", _ROOT / "scripts" / "study2_names.py")
ORIG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ORIG)

HAND = [
    "Use `keycloak.conf` and `--import-realm` here.",
    "Set FOO_BAR and fooBar and a.b.c and /a/b/c and file.yaml",
    "Replace <realm> with name=value and `a, b`",
    "plain text with no name at all",
    "run --proxy-headers and proxy-headers-x-y and end-to-end",
]
VOCAB = {"proxy-headers", "keycloak.conf"}


@pytest.fixture(scope="module")
def builder() -> P.PromptBuilder:
    return P.PromptBuilder()


def _ref(text: str, vocab=frozenset()) -> L.Reference:
    return L.Reference("S", "m", text, "0" * 64, frozenset(vocab))


def _real_requirements(n: int = 25) -> list[dict]:
    import json

    from tuning_lib.paths import STUDY2B

    out: list[dict] = []
    for rec_path in sorted(STUDY2B.glob("*/*/records.jsonl")):
        for line in rec_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            for req in rec.get("requirements") or []:
                if isinstance(req, dict):
                    out.append(req)
            if len(out) >= n:
                return out
    return out


def test_copy_fidelity_hand_made():
    for text in HAND:
        assert L.extract_names(text, VOCAB) == ORIG.extract_names(text, set(VOCAB))
    for name in ["--import-realm", "a.b.c", "FOO", "--log"]:
        assert L.search_forms(name) == ORIG.search_forms(name)
        for form in L.search_forms(name):
            assert L.whole_rx(form).pattern == ORIG.whole_rx(form).pattern


def test_copy_fidelity_real_requirements():
    reqs = _real_requirements()
    assert len(reqs) >= 20
    for req in reqs:
        assert L.requirement_text(req) == ORIG.requirement_text(req)
        t = ORIG.requirement_text(req)
        assert L.extract_names(t, VOCAB) == ORIG.extract_names(t, set(VOCAB))


def test_build_reference(builder):
    sid = next(iter(builder.stories()))
    story = builder.story(sid)
    ref = L.build_reference(sid, MODELS[0], builder=builder)
    assert "### code search hit" not in ref.text
    assert story.title in ref.text
    first = builder.system_context(story.project).splitlines()[0]
    assert first in ref.text
    from secreqgen.generation.prompts import get_json_schema

    assert get_json_schema() in ref.text
    docs = doc_passages(split_context(builder.stored_block(sid, MODELS[0])))
    assert all(p.text in ref.text for p in docs)
    assert L.build_reference(sid, MODELS[0], builder=builder).sha256 == ref.sha256


def test_unknown_story_raises(builder):
    with pytest.raises(P.PromptError):
        L.build_reference("NOPE-000", MODELS[0], builder=builder)


def test_reference_does_not_depend_on_version(builder, tmp_path):
    root = tmp_path / "pv"
    shutil.copytree(P.VERSIONS_ROOT / "v0_old", root / "v0_old")
    from tuning_lib.guard import WriteGuard

    guard = WriteGuard(allowed_roots=[root], forbidden_roots=[], forbidden_names=[])
    folder = P.new_version("t_drop", "v0_old", "test version", root=root, guard=guard)
    meta = json.loads((folder / "version.json").read_text(encoding="utf-8"))
    meta["passage_changes"] = ["drop_code_hits"]
    (folder / "version.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    b2 = P.PromptBuilder(versions_root=root)
    sid = next(iter(builder.stories()))
    assert b2.build(sid, MODELS[0], "t_drop").n_code_hits == 0
    a = L.build_reference(sid, MODELS[0], builder=b2)
    c = L.build_reference(sid, MODELS[0], builder=builder)
    assert (a.text, a.sha256) == (c.text, c.sha256)


def test_name_in_reference():
    ref = _ref("keycloak.config.resolver and --import-realm and foo_bar")
    assert L.name_in_reference("foo_bar", ref)
    assert not L.name_in_reference("keycloak.conf", ref)
    assert not L.name_in_reference("--import", ref)
    assert L.name_in_reference("--import-realm", ref)
    assert not L.name_in_reference("only_in_code", ref)


def test_code_hit_name_is_outside(builder):
    sid = next(iter(builder.stories()))
    ref = L.build_reference(sid, MODELS[0], builder=builder)
    assert not L.name_in_reference("zz_not_anywhere_qq", ref)


def test_check_requirements_counts():
    ref = _ref("alpha_key and beta_key", {"alpha_key"})
    reqs = [
        {"title": "nothing here", "description": "plain words"},
        {"title": "t", "description": "uses alpha_key"},
        {"title": "t", "description": "uses alpha_key and gamma_key"},
        {"title": "t", "description": "uses delta_key"},
    ]
    c = L.check_requirements(reqs, ref)
    assert (c.n_requirements, c.n_with_names, c.n_outside, c.n_all_inside) == (4, 3, 2, 1)
    assert c.n_outside + c.n_all_inside == c.n_with_names
    assert c.outside_share + c.all_inside_share + c.no_name_share == pytest.approx(1.0)
    assert c.per_requirement[2].outside == ("gamma_key",)


def test_empty_requirements():
    c = L.check_requirements([], _ref("x"))
    assert (c.n_requirements, c.outside_share, c.all_inside_share, c.no_name_share) == (0, 0.0, 0.0, 0.0)


def test_outside_is_split_by_code_hits():
    ref = L.Reference("S", "m", "alpha_key", "0" * 64, frozenset(), code_text="in_hits_key was found here")
    reqs = [
        {"title": "t", "description": "uses in_hits_key"},
        {"title": "t", "description": "uses in_hits_key and nowhere_key"},
        {"title": "t", "description": "uses alpha_key"},
        {"title": "t", "description": "plain"},
    ]
    c = L.check_requirements(reqs, ref)
    assert c.n_outside == 2 and c.n_outside_code == 1
    assert c.outside_code_share == pytest.approx(0.25) and c.outside_none_share == pytest.approx(0.25)
    assert c.outside_code_share + c.outside_none_share == pytest.approx(c.outside_share)


def test_build_reference_holds_code_hits_apart(builder):
    for sid in builder.stories():
        ref = L.build_reference(sid, MODELS[0], builder=builder)
        if ref.code_text:
            assert ref.code_text not in ref.text
            return
    raise AssertionError("no story with code hits")
