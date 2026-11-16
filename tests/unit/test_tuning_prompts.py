"""Tests of scripts/tuning_lib/prompts.py. Read-only on the registered data. No network, no model."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from tuning_lib import prompts as P  # noqa: E402
from tuning_lib.guard import GuardError, WriteGuard  # noqa: E402
from tuning_lib.passages import (  # noqa: E402
    SEPARATOR,
    doc_passages,
    read_study2b_prompts,
    split_context,
)
from tuning_lib.paths import DATA, MODELS, PROJECTS, RTC, TUNING_ROOT  # noqa: E402

V0_HASHES = {
    "rag_user_prompt.txt": "bab4639429934b7fe8844aa94075f1d4edabb82e51a842415e2dae6f50e9ffc0",
    "system_prompt.txt": "814750d9ceeb8221622a8d079c3509212e696d78fd0fdae7697802f7428bdfca",
    "zero_shot_user_prompt.txt": "72f9ea78e4087092c399aad662be64ac1ee51c1458c0035f08450a72edcdb3f4",
}


@pytest.fixture(scope="module")
def builder() -> P.PromptBuilder:
    return P.PromptBuilder()


@pytest.fixture(scope="module")
def stored_b2() -> dict[tuple[str, str], object]:
    out = {}
    for project in PROJECTS:
        for sp in read_study2b_prompts(projects=(project,), arms=("B2",), run=1):
            out[(sp.story_id, sp.model)] = sp
    return out


def _vroot(tmp_path: Path) -> Path:
    """A temporary versions root that holds a copy of v0_old."""
    root = tmp_path / "pv"
    shutil.copytree(P.VERSIONS_ROOT / "v0_old", root / "v0_old")
    return root


def _guard(root: Path) -> WriteGuard:
    return WriteGuard(allowed_roots=[root], forbidden_roots=[], forbidden_names=[])


def _make(root: Path, name: str = "v1", *, parent: str = "v0_old", changes=()) -> Path:
    """Make a valid version by new_version in a temporary root, then set passage_changes."""
    folder = P.new_version(name, parent, "A test version.", root=root, guard=_guard(root))
    meta = json.loads((folder / "version.json").read_text())
    meta["passage_changes"] = list(changes)
    (folder / "version.json").write_text(json.dumps(meta, indent=1) + "\n")
    return folder


def _append(folder: Path, file_name: str, text: str) -> None:
    path = folder / file_name
    path.write_text(path.read_text() + text)


# ---------------------------------------------------------------------------
# 1 and 2: the old prompt, byte for byte
# ---------------------------------------------------------------------------


def test_v0_old_equals_the_stored_prompts_of_all_30_cases(builder, stored_b2):
    assert len(stored_b2) == 30
    for (story_id, model), sp in stored_b2.items():
        built = builder.build(story_id, model, "v0_old")
        assert built.system == sp.system, (story_id, model)
        assert built.user == sp.user_sent, (story_id, model)
        assert built.user_non_doc == sp.user_non_doc, (story_id, model)
        assert built.block_sha256 == sp.doc_context_sha256, (story_id, model)
        assert builder.verify_v0_old(story_id, model) is None


def test_build_prompt_wrapper_gives_system_and_user(stored_b2):
    story_id, model = sorted(stored_b2)[0]
    built = P.build_prompt(story_id, model, "v0_old")
    sp = stored_b2[(story_id, model)]
    assert (built.system, built.user) == (sp.system, sp.user_sent)
    assert built.version == "v0_old" and built.project in PROJECTS


def test_v0_old_files_have_the_registered_hashes():
    info = P.load_version("v0_old")
    assert dict(info.template_sha256) == V0_HASHES
    assert list(info.template_sha256) == list(P.TEMPLATE_FILES)
    for name in P.TEMPLATE_FILES:
        mine = (P.VERSIONS_ROOT / "v0_old" / name).read_bytes()
        theirs = (DATA / "winning_prompts_s2b" / "rep" / name).read_bytes()
        assert mine == theirs
        assert hashlib.sha256(mine).hexdigest() == V0_HASHES[name]
    assert P.list_versions()[0] == "v0_old" or "v0_old" in P.list_versions()


# ---------------------------------------------------------------------------
# 3: a mismatch is loud
# ---------------------------------------------------------------------------


def test_changed_template_makes_verify_fail(tmp_path, stored_b2):
    root = _vroot(tmp_path)
    path = root / "v0_old" / "rag_user_prompt.txt"
    text = path.read_text()
    changed = text.replace("the ", "tHe ", 1)
    assert changed != text
    path.write_text(changed)
    story_id, model = sorted(stored_b2)[0]
    b = P.PromptBuilder(versions_root=root)
    with pytest.raises(P.PromptMismatch, match="user"):
        b.verify_v0_old(story_id, model)


def test_changed_system_context_makes_verify_fail(tmp_path, stored_b2):
    rtc = tmp_path / "rtc"
    for project in PROJECTS:
        (rtc / project).mkdir(parents=True)
        shutil.copy(RTC / project / "system_context.yaml", rtc / project / "system_context.yaml")
    story_id, model = sorted(stored_b2)[0]
    b = P.PromptBuilder(rtc_root=rtc)
    project = b.story(story_id).project
    target = rtc / project / "system_context.yaml"
    target.write_text(target.read_text() + "\n# one more line\n")
    with pytest.raises(P.PromptMismatch):
        b.verify_v0_old(story_id, model)


def test_missing_system_context_raises(tmp_path, stored_b2):
    story_id, model = sorted(stored_b2)[0]
    b = P.PromptBuilder(rtc_root=tmp_path)
    with pytest.raises(P.PromptError, match="system context"):
        b.build(story_id, model, "v0_old")


def test_unknown_story_and_model(builder):
    with pytest.raises(P.PromptError, match="unknown story"):
        builder.story("NOPE-001")
    story_id = sorted(builder.stories())[0]
    with pytest.raises(P.PromptError, match="unknown model"):
        builder.stored_block(story_id, "nomodel:1b")


# ---------------------------------------------------------------------------
# 4: load_version refusals
# ---------------------------------------------------------------------------


def _drop_files(f: Path):
    (f / "system_prompt.txt").unlink()


def _extra_file(f: Path):
    (f / "notes.txt").write_text("hidden text")


def _carriage_return(f: Path):
    p = f / "rag_user_prompt.txt"
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n", 1))


def _meta(f: Path, **changes):
    meta = json.loads((f / "version.json").read_text())
    for k, v in changes.items():
        if v is KeyError:
            del meta[k]
        else:
            meta[k] = v
    (f / "version.json").write_text(json.dumps(meta, indent=1) + "\n")


def _no_context(f: Path):
    p = f / "rag_user_prompt.txt"
    p.write_text(re.sub(r"\$\{?retrieved_context\}?", "", p.read_text()))


def _no_system_context(f: Path):
    p = f / "rag_user_prompt.txt"
    p.write_text(re.sub(r"\$\{?system_context\}?", "", p.read_text()))


CASES = [
    ("missing file", _drop_files, "system_prompt.txt"),
    ("extra file", _extra_file, "notes.txt"),
    ("carriage return", _carriage_return, "rag_user_prompt.txt.*carriage return"),
    ("unknown placeholder", lambda f: _append(f, "rag_user_prompt.txt", "\n$foo\n"), "foo"),
    ("dollar digit", lambda f: _append(f, "rag_user_prompt.txt", "\ncost $5\n"), "rag_user_prompt.txt.*'\\$'"),
    ("double dollar", lambda f: _append(f, "zero_shot_user_prompt.txt", "\n$$\n"), "zero_shot_user_prompt.txt.*'\\$'"),
    ("context twice", lambda f: _append(f, "rag_user_prompt.txt", "\n$retrieved_context\n"), "retrieved_context.*exactly one"),
    ("context zero", _no_context, "retrieved_context.*exactly one"),
    ("context in zero shot", lambda f: _append(f, "zero_shot_user_prompt.txt", "\n$retrieved_context\n"), "zero_shot_user_prompt.txt.*retrieved_context"),
    ("dropped system_context", _no_system_context, "system_context.*missing"),
    ("bad name key", lambda f: _meta(f, name="V1"), "name"),
    ("name differs from folder", lambda f: _meta(f, name="other"), "name"),
    ("parent missing", lambda f: _meta(f, parent="nope"), "parent 'nope' does not exist"),
    ("parent null for non baseline", lambda f: _meta(f, parent=None), "parent"),
    ("empty description", lambda f: _meta(f, description="   "), "description"),
    ("two line description", lambda f: _meta(f, description="a\nb"), "description.*one line"),
    ("long description", lambda f: _meta(f, description="x" * 301), "description"),
    ("extra key", lambda f: _meta(f, extra=1), "extra"),
    ("missing key", lambda f: _meta(f, description=KeyError), "missing"),
    ("unknown passage change", lambda f: _meta(f, passage_changes=["nope"]), "unknown passage change"),
    ("changes not strings", lambda f: _meta(f, passage_changes=[1]), "passage_changes"),
]


@pytest.mark.parametrize("label,mutate,pattern", CASES, ids=[c[0] for c in CASES])
def test_load_version_refuses(tmp_path, label, mutate, pattern):
    root = _vroot(tmp_path)
    folder = _make(root, "v1")
    mutate(folder)
    with pytest.raises(P.PromptError, match=pattern):
        P.load_version("v1", root)


def test_v0_old_with_a_parent_is_refused(tmp_path):
    root = _vroot(tmp_path)
    _meta(root / "v0_old", parent="v1")
    with pytest.raises(P.PromptError, match="parent.*null"):
        P.load_version("v0_old", root)


def test_parent_loop_is_refused(tmp_path):
    root = _vroot(tmp_path)
    a = _make(root, "va")
    b = _make(root, "vb", parent="va")
    _meta(a, parent="vb")
    with pytest.raises(P.PromptError, match="loop"):
        P.load_version("vb", root)
    assert b.is_dir()


def test_bad_and_missing_names(tmp_path):
    root = _vroot(tmp_path)
    with pytest.raises(P.PromptError, match="bad version name"):
        P.load_version("Bad-Name", root)
    with pytest.raises(P.PromptError, match="folder not found"):
        P.load_version("nothere", root)


def test_missing_template_never_reaches_the_silent_default(tmp_path, stored_b2):
    root = _vroot(tmp_path)
    folder = _make(root, "v1")
    (folder / "system_prompt.txt").unlink()
    story_id, model = sorted(stored_b2)[0]
    with pytest.raises(P.PromptError, match="system_prompt.txt"):
        P.PromptBuilder(versions_root=root).build(story_id, model, "v1")


def test_dollar_in_system_prompt_is_allowed(tmp_path):
    root = _vroot(tmp_path)
    folder = _make(root, "v1")
    _append(folder, "system_prompt.txt", "\nCost is $5.\n")
    assert P.load_version("v1", root).name == "v1"


def test_braced_placeholder_form_is_accepted(tmp_path):
    root = _vroot(tmp_path)
    folder = _make(root, "v1")
    _append(folder, "zero_shot_user_prompt.txt", "\nStory ${story_id}.\n")
    assert P.load_version("v1", root).name == "v1"


def test_list_versions(tmp_path):
    root = _vroot(tmp_path)
    _make(root, "v1")
    (root / "not_a_version").mkdir()
    assert P.list_versions(root) == ["v0_old", "v1"]
    assert P.list_versions(tmp_path / "none") == []


# ---------------------------------------------------------------------------
# 5: passage changes
# ---------------------------------------------------------------------------


def _all_blocks(builder):
    for story_id in sorted(builder.stories()):
        for model in MODELS:
            yield story_id, model, builder.stored_block(story_id, model)


def test_identity_returns_the_same_string(builder):
    for _, _, block in _all_blocks(builder):
        assert P.PASSAGE_CHANGES["identity"](block) is block
        assert P.apply_passage_changes(block, ["identity"]) == block


def test_drop_code_hits_on_all_30_blocks(builder):
    saw_code = saw_no_code = False
    counts = []
    for story_id, model, block in _all_blocks(builder):
        original = split_context(block)
        docs = doc_passages(original)
        out = P.PASSAGE_CHANGES["drop_code_hits"](block)
        new = split_context(out)
        assert all(p.kind == "doc" for p in new), (story_id, model)
        assert len(new) == len(docs)
        assert [p.index for p in new] == list(range(1, len(new) + 1))
        for old_p, new_p in zip(docs, new):
            old_lines = old_p.text.split("\n", 1)
            new_lines = new_p.text.split("\n", 1)
            assert old_lines[1:] == new_lines[1:]
            assert re.sub(r"^### \[\d+\]", "", old_lines[0]) == re.sub(r"^### \[\d+\]", "", new_lines[0])
        assert "### code search hit" not in out
        if len(docs) == len(original):
            saw_no_code = True
            assert out == block
        else:
            saw_code = True
        counts.append(len(new))
    assert saw_code
    # Report values for the author.
    print("doc passages kept: min", min(counts), "max", max(counts), "no-code-hit block seen:", saw_no_code)
    assert min(counts) >= 1


def test_drop_code_hits_is_stable_on_a_block_without_hits(builder):
    story_id, model, block = next(iter(_all_blocks(builder)))
    once = P.PASSAGE_CHANGES["drop_code_hits"](block)
    assert P.PASSAGE_CHANGES["drop_code_hits"](once) == once
    assert once.count(SEPARATOR) == len(split_context(once)) - 1


def test_block_with_only_code_hits_raises():
    block = (
        "### [1] f.py:3 > f.py:3 (Relevance: 0.55)\n"
        "### code search hit (pattern: 'x|y')\n"
        "File: src/f.py:3\n```\nline\n```"
    )
    with pytest.raises(P.NoDocumentationAfterChange):
        P.PASSAGE_CHANGES["drop_code_hits"](block)
    with pytest.raises(P.NoDocumentationAfterChange):
        P.apply_passage_changes(block, ["drop_code_hits"])


def test_unknown_change_and_order(builder):
    _, _, block = next(iter(_all_blocks(builder)))
    with pytest.raises(P.PromptError, match="identity"):
        P.apply_passage_changes(block, ["nope"])
    assert P.apply_passage_changes(block, ["drop_code_hits", "identity"]) == P.apply_passage_changes(
        block, ["drop_code_hits"]
    )


def test_a_change_that_empties_the_block_raises(monkeypatch, builder):
    monkeypatch.setitem(P.PASSAGE_CHANGES, "drop_all", lambda b, c=None: "")
    _, _, block = next(iter(_all_blocks(builder)))
    with pytest.raises(P.NoDocumentationAfterChange):
        P.apply_passage_changes(block, ["drop_all"])


def test_a_change_that_breaks_the_block_raises(monkeypatch, builder):
    monkeypatch.setitem(P.PASSAGE_CHANGES, "break_it", lambda b, c=None: "free text")
    _, _, block = next(iter(_all_blocks(builder)))
    with pytest.raises(P.PromptError, match="broken"):
        P.apply_passage_changes(block, ["break_it"])


# ---------------------------------------------------------------------------
# 6 and 7: build with a passage change
# ---------------------------------------------------------------------------


def test_build_with_drop_code_hits(tmp_path, builder):
    root = _vroot(tmp_path)
    _make(root, "v1", changes=["drop_code_hits"])
    b = P.PromptBuilder(versions_root=root)
    checked = 0
    for story_id, model, _ in _all_blocks(builder):
        old = builder.build(story_id, model, "v0_old")
        new = b.build(story_id, model, "v1")
        assert new.n_code_hits == 0
        assert new.block == P.apply_passage_changes(old.block_original, ["drop_code_hits"])
        assert new.block_original == old.block_original
        assert new.user.count(new.block) == 1
        assert new.n_doc_passages == old.n_doc_passages
        assert new.system == old.system
        if old.n_code_hits:
            checked += 1
            assert new.block_sha256 != old.block_sha256
            assert new.user != old.user
            assert old.user.replace(old.block, "\0", 1) == new.user.replace(new.block, "\0", 1)
        else:
            assert new.user == old.user
    assert checked > 0


def test_user_non_doc_equals_the_prompt_with_an_empty_block(tmp_path, builder):
    root = _vroot(tmp_path)
    _make(root, "v1", changes=["drop_code_hits"])
    b = P.PromptBuilder(versions_root=root)
    story_id, model, _ = next(iter(_all_blocks(builder)))
    new = b.build(story_id, model, "v1")
    assert new.user_non_doc == new.user.replace(new.block, "", 1)


def test_removal_check_catches_a_bad_template(monkeypatch, builder):
    real = P.SecurityRequirementsPromptBuilder.build_rag_prompt

    def fake(self, user_story, retrieved_context, system_context=None):
        text = real(self, user_story, retrieved_context, system_context)
        return text + ("X" if retrieved_context == "" else "")

    monkeypatch.setattr(P.SecurityRequirementsPromptBuilder, "build_rag_prompt", fake)
    story_id, model, _ = next(iter(_all_blocks(builder)))
    with pytest.raises(P.PromptError, match="removing the block"):
        P.PromptBuilder().build(story_id, model, "v0_old")


# ---------------------------------------------------------------------------
# 8: new_version
# ---------------------------------------------------------------------------


def test_new_version_copies_the_parent(tmp_path):
    root = _vroot(tmp_path)
    folder = P.new_version("v1_test", "v0_old", "Say what changed.", root=root, guard=_guard(root))
    assert sorted(p.name for p in folder.iterdir()) == sorted([*P.TEMPLATE_FILES, "version.json"])
    for name in P.TEMPLATE_FILES:
        assert (folder / name).read_bytes() == (root / "v0_old" / name).read_bytes()
    meta = json.loads((folder / "version.json").read_text())
    assert list(meta) == ["name", "parent", "description", "passage_changes"]
    assert meta == {
        "name": "v1_test",
        "parent": "v0_old",
        "description": "Say what changed.",
        "passage_changes": [],
    }
    info = P.load_version("v1_test", root)
    assert info.parent == "v0_old" and info.template_sha256 == P.load_version("v0_old", root).template_sha256


def test_new_version_copies_passage_changes_of_the_parent(tmp_path):
    root = _vroot(tmp_path)
    _make(root, "v1", changes=["drop_code_hits"])
    folder = P.new_version("v2", "v1", "Child.", root=root, guard=_guard(root))
    assert json.loads((folder / "version.json").read_text())["passage_changes"] == ["drop_code_hits"]


def test_new_version_refusals(tmp_path):
    root = _vroot(tmp_path)
    g = _guard(root)
    P.new_version("v1", "v0_old", "One.", root=root, guard=g)
    with pytest.raises(P.PromptError, match="exists"):
        P.new_version("v1", "v0_old", "One.", root=root, guard=g)
    with pytest.raises(P.PromptError, match="bad version name"):
        P.new_version("Bad", "v0_old", "One.", root=root, guard=g)
    with pytest.raises(P.PromptError, match="not found"):
        P.new_version("v2", "nope", "One.", root=root, guard=g)
    for bad in ("", "  ", "a\nb", "x" * 301):
        with pytest.raises(P.PromptError, match="description"):
            P.new_version("v3", "v0_old", bad, root=root, guard=g)
    with pytest.raises(P.PromptError, match="reserved"):
        P.new_version("v0_old", "v0_old", "One.", root=root, guard=g)
    assert not (root / "v2").exists() and not (root / "v3").exists()


def test_new_version_writes_only_inside_the_guard(tmp_path):
    root = _vroot(tmp_path)
    other = tmp_path / "other"
    shutil.copytree(root / "v0_old", other / "v0_old")
    with pytest.raises(GuardError):
        P.new_version("v1", "v0_old", "One.", root=other, guard=_guard(root))
    assert not (other / "v1").exists()


def test_default_guard_refuses_a_temporary_root(tmp_path):
    root = _vroot(tmp_path)
    with pytest.raises(GuardError):
        P.new_version("v1", "v0_old", "One.", root=root)
    assert not (root / "v1").exists()


# ---------------------------------------------------------------------------
# 9: the generality check
# ---------------------------------------------------------------------------


def test_v0_old_is_general_with_inherited_hits(builder):
    report = P.check_generality("v0_old", builder=builder)
    assert report.ok and report.new_hits == ()
    terms = {(h.kind, h.term) for h in report.inherited_hits}
    assert ("fact_identifier", "strict") in terms
    assert all(h.inherited and h.count == h.baseline_count for h in report.hits)
    assert P.assert_general("v0_old", builder=builder).ok


def _pick_identifier(builder) -> str:
    base = " ".join((P.VERSIONS_ROOT / "v0_old" / n).read_text() for n in P.TEMPLATE_FILES)
    for story in builder.stories().values():
        for ident in story.identifiers:
            if "_" in ident and ident.isupper() and ident not in base:
                return ident
    raise AssertionError("no identifier found")


def _pick_phrase(builder) -> str:
    """Five plain words from a story: no product word, no identifier, not in v0_old."""
    base = " ".join(
        (P.VERSIONS_ROOT / "v0_old" / n).read_text().casefold() for n in P.TEMPLATE_FILES
    )
    idents = {i.casefold() for s in builder.stories().values() for i in s.identifiers}
    for story in sorted(builder.stories().values(), key=lambda s: s.story_id):
        words = re.findall(r"\w+", story.story)
        for i in range(len(words) - 4):
            phrase = " ".join(words[i : i + 5])
            low = phrase.casefold()
            if any(w in low for w in P.PRODUCT_WORDS) or low in base:
                continue
            if any(w.casefold() in idents for w in words[i : i + 5]):
                continue
            return phrase
    raise AssertionError("no phrase found")


def _hits_of(tmp_path, builder, sentence: str, name="v1"):
    root = _vroot(tmp_path)
    folder = _make(root, name)
    _append(folder, "rag_user_prompt.txt", "\n" + sentence + "\n")
    b = P.PromptBuilder(versions_root=root)
    return b, P.check_generality(name, builder=b)


@pytest.mark.parametrize(
    "sentence_of,kind",
    [
        (lambda b: "Keep to what Keycloak documents.", "product"),
        (lambda b: "Prefer BITWARDEN style names.", "product"),
        (lambda b: "Think about mastodon too.", "product"),
        (lambda b: f"Use names like {_pick_identifier(b)} when you can.", "fact_identifier"),
        (lambda b: "Story KC-S2-005 is an example.", "story_id"),
        (lambda b: f"Rule: {_pick_phrase(b)}.", "story_phrase"),
    ],
    ids=["keycloak", "bitwarden upper", "mastodon", "identifier", "story id", "phrase"],
)
def test_one_new_hit_of_the_right_kind(tmp_path, builder, sentence_of, kind):
    b, report = _hits_of(tmp_path, builder, sentence_of(builder))
    assert [h.kind for h in report.new_hits] == [kind], report.new_hits
    assert not report.ok
    with pytest.raises(P.GeneralityError, match="v1"):
        P.assert_general("v1", builder=b)


def test_three_kinds_at_once(tmp_path, builder):
    sentence = f"Use Keycloak names such as {_pick_identifier(builder)} in story KC-S2-005."
    b, report = _hits_of(tmp_path, builder, sentence)
    assert sorted(h.kind for h in report.new_hits) == ["fact_identifier", "product", "story_id"]
    with pytest.raises(P.GeneralityError) as err:
        P.assert_general("v1", builder=b)
    assert "product" in str(err.value) and "story_id" in str(err.value)


def test_a_clean_general_sentence_has_no_hit(tmp_path, builder):
    b, report = _hits_of(
        tmp_path,
        builder,
        "Name each setting exactly as the passages write it, and say when a detail is missing.",
    )
    assert report.ok and report.new_hits == ()
    assert P.assert_general("v1", builder=b).ok


def test_one_more_occurrence_of_an_inherited_word_is_new(tmp_path, builder):
    b, report = _hits_of(tmp_path, builder, "Check the strict case.")
    new = report.new_hits
    assert [(h.kind, h.term) for h in new] == [("fact_identifier", "strict")]
    assert new[0].count == new[0].baseline_count + 1
    # A copy that keeps the single old occurrence has none.
    root = _vroot(tmp_path / "second")
    _make(root, "v1")
    assert P.check_generality("v1", builder=P.PromptBuilder(versions_root=root)).ok


@pytest.mark.parametrize(
    "sentence,kind,term",
    [
        ("Story kc-s2-005 is an example.", "story_id", "KC-S2-005"),
        ("Use cas_uid_key here.", "fact_identifier", "CAS_UID_KEY"),
        ("Apply command query separation.", "fact_identifier", "Command Query Separation"),
        ("Pick a PRF for it.", "fact_identifier", "prf"),
        ("Use a BCFIPS-approved module.", "fact_identifier", "BCFIPS"),
        ("Run kcadm.sh to do it.", "fact_identifier", "kcadm"),
    ],
    ids=["id lower", "ident lower", "phrase ident", "PRF upper", "BCFIPS glued", "kcadm.sh"],
)
def test_generality_finds_other_case_and_glued_forms(tmp_path, builder, sentence, kind, term):
    b, report = _hits_of(tmp_path, builder, sentence)
    assert (kind, term) in {(h.kind, h.term) for h in report.new_hits}, report.new_hits
    with pytest.raises(P.GeneralityError):
        P.assert_general("v1", builder=b)


def test_assert_general_lists_at_most_ten_hits(tmp_path, builder):
    sentence = " ".join(f"keycloak mastodon bitwarden KC-S2-{n:03d}" for n in range(1, 9))
    b, report = _hits_of(tmp_path, builder, sentence)
    assert len(report.new_hits) > 10
    with pytest.raises(P.GeneralityError, match="more"):
        P.assert_general("v1", builder=b)


# ---------------------------------------------------------------------------
# 10 and 11: import safety and no write call
# ---------------------------------------------------------------------------


def _listing() -> list[str]:
    out = []
    for base in (TUNING_ROOT, P.VERSIONS_ROOT):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            out.extend(os.path.join(dirpath, f) for f in sorted(filenames))
            out.extend(os.path.join(dirpath, d) for d in dirnames)
    return out


def test_import_is_safe():
    before = _listing()
    code = (
        "import sys, json; import tuning_lib.prompts; "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('chromadb', 'sentence_transformers', 'torch') "
        "or m.startswith('secreqgen.retrieval')]; print(json.dumps(bad))"
    )
    env = dict(os.environ, PYTHONPATH=f"{_ROOT / 'src'}{os.pathsep}{_ROOT / 'scripts'}", PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=_ROOT)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []
    assert _listing() == before


def test_source_has_no_unguarded_write_call():
    source = (_ROOT / "scripts" / "tuning_lib" / "prompts.py").read_text()
    assert not re.search(r"(?<!guarded_)write_text\(", source)
    assert not re.search(r"(?<!guarded_)mkdir\(", source)
    assert not re.search(r"\bopen\(", source)
    for word in ("shutil", "os.replace", "write_bytes(", "unlink("):
        assert word not in source


# ---------------------------------------------------------------------------
# render_markup
# ---------------------------------------------------------------------------


def _doc(body: str, number: int = 1) -> str:
    return f"### [{number}] page.md > Part (Relevance: 0.55)\n{body}"


def _render(body: str) -> str:
    out = P.PASSAGE_CHANGES["render_markup"](_doc(body))
    header, _, rest = out.partition("\n")
    assert header == "### [1] page.md > Part (Relevance: 0.55)"
    return rest


@pytest.mark.parametrize(
    "body, expected",
    [
        ('\\<@kc.start parameters="--hostname x"/\\>', "bin/kc.sh start --hostname x"),
        ('<@kc.start parameters="--hostname x"/>', "bin/kc.sh start --hostname x"),
        ('\\<@kc.start parameters="--a b\n--c d"/\\>', "bin/kc.sh start --a b --c d"),
        ('\\<@kc.bootstrapadmin parameters="user"/\\>', "bin/kc.sh bootstrap-admin user"),
        ('\\<@kc.startdev parameters="--x"/\\>', "bin/kc.sh start-dev --x"),
        ("\\<@kc.start/\\>", "bin/kc.sh start"),
        ('\\<@kc.start parameters=""/\\>', "bin/kc.sh start"),
        (
            '\\<@kc.start parameters=" --f=/p\n--w=\\<value\\>"/\\>',
            "bin/kc.sh start --f=/p --w=<value>",
        ),
        (
            '\\<@kc.start parameters="--hostname <https://a.b>\n--p x"/\\>',
            "bin/kc.sh start --hostname https://a.b --p x",
        ),
        (
            '\\<@kc.start parameters="--hostname\n[https://a.b:1/auth"/](https://a.b:1/auth"/)\\>',
            "bin/kc.sh start --hostname https://a.b:1/auth",
        ),
        ('\\<@kc.start rootParameters="--v" parameters="--x"/\\>', "bin/kc.sh --v start --x"),
    ],
)
def test_render_markup_kc_macro(body, expected):
    assert _render(f"Run:\n\n{body}\n\nDone.") == f"Run:\n\n{expected}\n\nDone."


def test_render_markup_unknown_macro_stays():
    body = '<@kc.nothing parameters="x"/> and <@opts.other includedOptions="a">'
    assert _render(body) == body
    # Only the Markdown escapes of an unknown macro go (rule e).
    assert _render('\\<@kc.nothing parameters="x"/\\>') == '<@kc.nothing parameters="x"/>'
    assert _render('<@links.server id="zzz"/>') == '<@links.server id="zzz"/>'
    assert _render('<@kc.start parameters="a" other="b"/>') == '<@kc.start parameters="a" other="b"/>'


def test_render_markup_links_server_gives_the_guide_title():
    assert _render('see \\<@links.server\nid="vault"/\\> and <@links.server id="configuration" />.') == (
        "see Using a vault and Configuring Keycloak."
    )


def test_render_markup_raw_string():
    assert _render('Use `${r"${vault.realm-name_alias}"}`.') == "Use `${vault.realm-name_alias}`."
    assert _render('x ${r"A"} y ${other}') == "x A y ${other}"


def test_render_markup_removes_tag_lines():
    text = (
        '<#import "/templates/x.adoc" as x>\n'
        "Text one.\n\n"
        "\\<#list ctx.guides as guide\\>\n"
        "Text two.\n"
        "\\</#list\\>\n"
        "</#list>\n\n"
        '\\<@tmpl.guide\ntitle="T"\nsummary="S"\\>\n\n'
        "Text three.\n\n"
        "\\</@tmpl.guide\\>\n"
    )
    assert _render(text) == "Text one.\n\nText two.\n\nText three.\n"
    # A tag inside a sentence is not a tag line.
    assert _render("A </#list> in a sentence.") == "A </#list> in a sentence."


def test_render_markup_project_name():
    assert _render("In {project_name} and {other_name}.") == "In Keycloak and {other_name}."
    assert _render("```\n{project_name}\n```") == "```\n{project_name}\n```"


def test_render_markup_unescape_outside_code_only():
    text = (
        "Use X-Forwarded-\\* and \\[`ms`\\] and \\<value\\> and snake\\_case and \\\\keep.\n\n"
        "```\nc:\\> kcreg \\* \\<x\\> \"a\\\"\n```\n\n"
        "Inline `a\\*b` stays, ``c\\<d`` too."
    )
    expected = (
        "Use X-Forwarded-* and [`ms`] and <value> and snake_case and \\\\keep.\n\n"
        "```\nc:\\> kcreg \\* \\<x\\> \"a\\\"\n```\n\n"
        "Inline `a\\*b` stays, ``c\\<d`` too."
    )
    assert _render(text) == expected
    # Not a second pass of unescape on a double backslash.
    assert _render(expected) == expected


def test_render_markup_keeps_headers_code_hits_and_separators():
    code = (
        "### [2] f.py:3 > f.py:3 (Relevance: 0.55)\n"
        "### code search hit (pattern: 'x|y')\n"
        "File: src/f.py:3\n```\n<@kc.start parameters=\"\\*\"/> {project_name}\n```"
    )
    block = SEPARATOR.join(
        [
            "### [1] a.md > {project_name} \\<x\\> (Relevance: 0.55)\n\\<@kc.start parameters=\"y\"/\\>",
            code,
            "### [3] b.md > B (Relevance: 0.50)\nPlain text.",
        ]
    )
    out = P.PASSAGE_CHANGES["render_markup"](block)
    parts = out.split(SEPARATOR)
    assert parts[0] == "### [1] a.md > Keycloak \\<x\\> (Relevance: 0.55)\nbin/kc.sh start y"
    assert parts[1] == code
    assert parts[2] == "### [3] b.md > B (Relevance: 0.50)\nPlain text."


def test_render_markup_block_without_markup_is_equal():
    block = SEPARATOR.join([_doc("Plain.\n- item `code`", 1), _doc("More plain.", 2)])
    assert P.PASSAGE_CHANGES["render_markup"](block) == block


def test_render_markup_refuses_a_broken_block():
    with pytest.raises(P.PromptError, match="render_markup"):
        P.PASSAGE_CHANGES["render_markup"]("not a block")


def test_render_markup_is_deterministic_and_idempotent(builder):
    for _, _, block in _all_blocks(builder):
        once = P.PASSAGE_CHANGES["render_markup"](block)
        assert P.PASSAGE_CHANGES["render_markup"](block) == once
        assert P.PASSAGE_CHANGES["render_markup"](once) == once


def test_render_markup_and_drop_code_hits_commute_on_all_30_blocks(builder):
    for story_id, model, block in _all_blocks(builder):
        a = P.apply_passage_changes(block, ["drop_code_hits", "render_markup"])
        b = P.apply_passage_changes(block, ["render_markup", "drop_code_hits"])
        assert a == b, (story_id, model)


def test_render_markup_on_all_30_blocks(builder):
    n_keycloak = n_changed_keycloak = 0
    for story_id, model, block in _all_blocks(builder):
        project = builder.story(story_id).project
        out = P.PASSAGE_CHANGES["render_markup"](block)
        old, new = split_context(block), split_context(out)
        assert len(old) == len(new)
        for a, b in zip(old, new):
            assert a.kind == b.kind
            # The header may differ only by the rendered {project_name} attribute (rule F10).
            assert a.text.split("\n", 1)[0].replace("{project_name}", "Keycloak") == b.text.split("\n", 1)[0]
            if a.kind == "code":
                assert a.text == b.text
            else:
                assert "<@kc." not in b.text and "\\<@kc." not in b.text
                assert '${r"' not in b.text
        if project == "keycloak":
            n_keycloak += 1
            n_changed_keycloak += out != block
        else:
            # Only a backslash escape may differ (rule e): the text equals itself without escapes.
            if out != block:
                assert out == re.sub(r"\\([<>*_\[\]#])", r"\1", block), (story_id, model)
    assert n_keycloak == 10
    assert n_changed_keycloak >= 8


def test_render_markup_keeps_the_answer_key_identifiers(builder):
    """Reads only the development stories (splits.json, key "dev"), never a hold-out story."""
    splits = json.loads((_ROOT / "scripts" / "tuning_lib" / "splits.json").read_text(encoding="utf-8"))
    checked = 0
    for story_id in splits["dev"]:
        story = builder.story(story_id)
        if story.project != "keycloak":
            continue
        for model in MODELS:
            block = builder.stored_block(story_id, model)
            out = P.PASSAGE_CHANGES["render_markup"](block)
            for ident in story.identifiers:
                if ident in block:
                    checked += 1
                    assert ident in out, (story_id, model, ident)
    assert checked > 0


def test_version_t_minimal_clean_builds_and_is_general(builder):
    info = builder.version("t_minimal_clean")
    assert info.passage_changes == ("drop_code_hits", "render_markup")
    built = builder.build("KC-S2-002", MODELS[0], "t_minimal_clean")
    assert "<@kc." not in built.user and "### code search hit" not in built.user
    assert "bin/kc.sh bootstrap-admin service" in built.user
    assert P.check_generality("t_minimal_clean").ok
