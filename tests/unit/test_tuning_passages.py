"""Tests of scripts/tuning_lib/passages.py. Read only. No network, no model."""

from __future__ import annotations

import builtins
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from tuning_lib import passages as P  # noqa: E402
from tuning_lib.paths import FALLBACK_CONTEXT, MODELS, PROJECTS  # noqa: E402


def _doc(i: int, body: str = "text") -> str:
    return f"### [{i}] a.md > A (Relevance: 0.50)\nSection: A\n{body}"


def _code(i: int) -> str:
    return (
        f"### [{i}] f.py:3 > f.py:3 (Relevance: 0.55)\n"
        "### code search hit (pattern: 'x|y')\n"
        "File: src/f.py:3\n```\nline\n```\n"
    )


def _join(*parts: str) -> str:
    return P.SEPARATOR.join(parts)


# ---------- split_context on fixtures ----------


def test_code_hit_found_by_second_line():
    ps = P.split_context(_join(_doc(1), _code(2)))
    assert [p.kind for p in ps] == ["doc", "code"]
    assert ps[1].code_pattern == "x|y"
    assert ps[1].code_location == "src/f.py:3"
    assert ps[1].relevance == 0.55
    assert ps[0].code_pattern is None


def test_rule_and_subheading_do_not_split():
    body = "intro\n\n---\n\n#### sub heading\nmore"
    ps = P.split_context(_join(_doc(1, body), _doc(2)))
    assert len(ps) == 2
    assert "#### sub heading" in ps[0].text


def test_mention_of_code_search_hit_in_body_is_doc():
    ps = P.split_context(_doc(1, "we say: ### code search hit (pattern: 'a')"))
    assert ps[0].kind == "doc"


@pytest.mark.parametrize(
    "text",
    [
        "",
        FALLBACK_CONTEXT,
        "stray\n\n" + _doc(1),
        "no header here",
        _join(_doc(1), _doc(3)),
        _doc(1) + "\n---\n" + _doc(2),
        _join(_doc(1), _code(2).replace("File: src/f.py:3\n", "")),
    ],
)
def test_split_errors(text):
    with pytest.raises(P.PassagesError):
        P.split_context(text)


def test_helpers_filter():
    ps = P.split_context(_join(_doc(1), _code(2), _doc(3)))
    assert [p.index for p in P.doc_passages(ps)] == [1, 3]
    assert [p.index for p in P.code_passages(ps)] == [2]


# ---------- real stored data (read only) ----------

_real = None


def _real_contexts():
    global _real
    if _real is None:
        _real = []
        for project in PROJECTS:
            for model in MODELS:
                path = P.study2b_cell_dir(project, model) / "retrieval_frozen.json"
                with path.open("r", encoding="utf-8") as fh:
                    d = json.load(fh)
                for sid in sorted(d):
                    _real.append((project, model, sid, P.read_study2b_context(project, model, sid)))
    return _real


def test_all_30_contexts_split_and_round_trip():
    ctxs = _real_contexts()
    assert len(ctxs) == 30
    n_doc = n_code = 0
    for project, model, sid, c in ctxs:
        ps = P.split_context(c.text)
        assert len(ps) == c.n_chunks_stored, (project, model, sid)
        assert [p.index for p in ps] == list(range(1, len(ps) + 1))
        assert P.doc_passages(ps)
        assert P.SEPARATOR.join(p.text for p in ps) == c.text
        n_doc += len(P.doc_passages(ps))
        n_code += len(P.code_passages(ps))
    # the design counts 252 and 216 for 60 contexts (Study 2 and Study 2b copies together)
    assert (n_doc, n_code) == (126, 108)


def test_read_prompts_60_verified_and_sorted():
    prompts = P.read_study2b_prompts()
    assert len(prompts) == 60
    keys = [(p.project, p.model, p.arm, p.story_id) for p in prompts]
    assert keys == sorted(keys)
    n_b2 = n_b2_doc = n_b2_code = 0
    for sp in prompts:
        if sp.arm == "B2":
            ctx = P.read_study2b_context(sp.project, sp.model, sp.story_id)
            P.verify_stored_prompt(sp, ctx)
            # the function that separates the document block returns the exact prompt again
            before, after = sp.user_sent.split(ctx.text)
            assert before + ctx.text + after == sp.user_sent
            assert before + after == sp.user_non_doc
            ps = P.split_context(ctx.text)
            n_b2 += 1
            n_b2_doc += len(P.doc_passages(ps))
            n_b2_code += len(P.code_passages(ps))
        else:
            P.verify_stored_prompt(sp, None)
    assert n_b2 == 30
    assert n_b2_doc + n_b2_code > 0
    assert (n_b2_doc, n_b2_code) == (126, 108)


def test_all_330_records_of_study2b_keep_the_prompt():
    """For every B-type record of Study 2b, context removal and re-insertion is exact."""
    n = 0
    for project in PROJECTS:
        for model in MODELS:
            path = P.study2b_cell_dir(project, model) / "records.jsonl"
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    n += 1
                    if rec.get("doc_context_ref") == "frozen":
                        ctx = P.read_study2b_context(project, model, rec["story_id"])
                        assert rec["prompt_user_sent"].count(ctx.text) == 1
                        assert (
                            rec["prompt_user_sent"].replace(ctx.text, "", 1)
                            == rec["prompt_user_non_doc"]
                        )
                    elif rec["arm"] == "A2":
                        assert rec["prompt_user_sent"] == rec["prompt_user_non_doc"]
    assert n == 330


def test_filter_and_unknown_story():
    one = P.read_study2b_prompts(stories=["BW-S2-001"], projects=["bitwarden"])
    assert len(one) == 4 and {p.story_id for p in one} == {"BW-S2-001"}
    with pytest.raises(P.PassagesError):
        P.read_study2b_prompts(stories=["NOPE-1"], projects=["bitwarden"])


def test_study2_prompts_have_no_user_sent():
    ps = P.read_study2_prompts(projects=["bitwarden"], models=["gemma4:26b"], arms=["A"])
    assert len(ps) == 15 and all(p.user_sent is None for p in ps)
    c = P.read_study2_context("bitwarden", "gemma4:26b", "BW-S2-001")
    assert P.split_context(c.text)


# ---------- verify_stored_prompt on fixtures ----------


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _fixture():
    ctx_text = _join(_doc(1), _code(2))
    ctx = P.StoredContext("S1", ctx_text, _sha(ctx_text), 2)
    non_doc = "head\n\n## User Story\nstory"
    user = "head\n\n" + ctx_text + "\n\n## User Story\nstory"
    non_doc = user.replace(ctx_text, "", 1)
    sp = P.StoredPrompt(
        "study2b", "p", "m", "S1", "B2", 1, 1, "sys", user, non_doc, "frozen",
        ctx.sha256, _sha("sys"), _sha(user), 1, 1, 1, "x/records.jsonl", 1,
    )
    return sp, ctx


def test_verify_ok():
    sp, ctx = _fixture()
    P.verify_stored_prompt(sp, ctx)


def test_verify_failures():
    sp, ctx = _fixture()
    bad = [
        (dataclasses.replace(sp, user_sent=sp.user_sent + "x"), ctx),
        (sp, dataclasses.replace(ctx, text=ctx.text + "x")),
        (dataclasses.replace(sp, user_non_doc=sp.user_non_doc + "x"), ctx),
        (sp, None),
    ]
    twice = sp.user_sent + ctx.text
    bad.append((dataclasses.replace(sp, user_sent=twice, user_sha256=_sha(twice)), ctx))
    a2 = dataclasses.replace(sp, doc_context_ref="frozen", arm="A2", user_non_doc=sp.user_sent)
    bad.append((a2, None))
    for s, c in bad:
        with pytest.raises(P.PassagesError):
            P.verify_stored_prompt(s, c)


def test_verify_a2_with_context_ref_fails():
    sp, _ = _fixture()
    a2 = dataclasses.replace(sp, doc_context_ref="other", user_non_doc=sp.user_sent)
    with pytest.raises(P.PassagesError):
        P.verify_stored_prompt(a2, None)


# ---------- error handling with fixture files ----------


def _write_cell(root: Path, records: list[dict], stories=("S1",)):
    cell = root / "proj" / "m_x"
    cell.mkdir(parents=True)
    (cell / "retrieval_frozen.json").write_text(json.dumps({s: {} for s in stories}))
    (cell / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _rec(error=None, arm="A2", story="S1"):
    return {
        "project": "proj", "model": "m:x", "story_id": story, "arm": arm, "run": 1,
        "attempt": 1, "prompt_system": "s", "prompt_user_non_doc": "u",
        "prompt_user_sent": "u", "n_requirements": 1, "error": error,
    }


def test_last_line_error_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "STUDY2B_ROOT", tmp_path)
    _write_cell(tmp_path, [_rec(), _rec(error="boom")])
    with pytest.raises(P.PassagesError, match="error"):
        P.read_study2b_prompts(projects=["proj"], models=["m:x"], arms=["A2"])


def test_last_line_wins_when_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "STUDY2B_ROOT", tmp_path)
    _write_cell(tmp_path, [_rec(error="boom"), _rec()])
    out = P.read_study2b_prompts(projects=["proj"], models=["m:x"], arms=["A2"])
    assert len(out) == 1 and out[0].source_line == 2


def test_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "STUDY2B_ROOT", tmp_path)
    _write_cell(tmp_path, [_rec()], stories=("S1", "S2"))
    with pytest.raises(P.PassagesError, match="missing"):
        P.read_study2b_prompts(projects=["proj"], models=["m:x"], arms=["A2"])


# ---------- estimate_prompt_tokens ----------


def test_estimate_tokens(monkeypatch, tmp_path):
    text = "abcdefghi"  # 9 bytes
    assert P.estimate_prompt_tokens(text) == 3
    assert P.estimate_prompt_tokens("ab", "cdefg") == 2
    assert P.estimate_prompt_tokens(text, method="bytes32") == 3
    big = "a" * 1000
    assert P.estimate_prompt_tokens(big, method="bytes32") > P.estimate_prompt_tokens(big)
    with pytest.raises(ValueError):
        P.estimate_prompt_tokens(text, method="nope")
    monkeypatch.setattr(P, "_tiktoken_cache_dir", lambda: tmp_path)
    with pytest.raises(P.PassagesError):
        P.estimate_prompt_tokens(text, method="tiktoken")
    assert P.MAX_PROMPT_TOKENS_EST == 12000


# ---------- no sealed folder is opened ----------


def test_no_sealed_path_opened(monkeypatch):
    seen: list[str] = []
    real_open = builtins.open
    real_path_open = Path.open

    def spy_open(f, *a, **k):
        seen.append(str(f))
        return real_open(f, *a, **k)

    def spy_path_open(self, *a, **k):
        seen.append(str(self))
        return real_path_open(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "open", spy_path_open)
    P.read_study2b_prompts(projects=["bitwarden"])
    P.read_study2_prompts(projects=["bitwarden"], arms=["A"])
    assert seen
    for s in seen:
        assert "key_do_not_open" not in s and "study2_threat" not in s
        assert "/review/" not in s and "/review_v2/" not in s
