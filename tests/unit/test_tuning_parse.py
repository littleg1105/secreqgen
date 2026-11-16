"""Tests of scripts/tuning_lib/parse.py. No network, no model."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from secreqgen.generation.generator import SecurityRequirementsGenerator  # noqa: E402
from secreqgen.generation.llm_client import LLMResponse  # noqa: E402
from secreqgen.generation.schemas import UserStory  # noqa: E402
from tuning_lib import parse as R  # noqa: E402
from tuning_lib.paths import MODELS, PROJECTS, STUDY2B, model_slug  # noqa: E402

FENCED_CMD = "run:\n```bash\nkc.sh start\n```\nthen stop"


def _req(i=1, **over):
    d = {
        "id": f"SR-{i:03d}", "user_story_id": "S1", "title": f"T{i}",
        "description": "d", "rationale": "r", "acceptance_criteria": ["a", "b"],
        "asvs_category": "V2: Authentication", "priority": "high",
        "implementation_notes": None, "source_references": ["x"],
    }
    d.update(over)
    return d


def _answer(reqs, **extra):
    return json.dumps({"requirements": reqs, **extra})


def _oracle() -> SecurityRequirementsGenerator:
    return SecurityRequirementsGenerator(llm_client=None, prompt_builder=object())  # type: ignore[arg-type]


def _old_parse(text: str, story_id: str):
    resp = LLMResponse(content=text, model="m", prompt_tokens=0, completion_tokens=0,
                       total_tokens=0, latency_ms=0.0, raw_response={})
    return _oracle()._parse_response(resp, UserStory(id=story_id, title="", story=""))


# ---------- extract_json ----------


def test_whole():
    r = R.extract_json(_answer([_req()]))
    assert (r.method, r.parsed_by_fallback) == ("whole", False)


def test_text_around_object():
    r = R.extract_json("Here you go:\n" + _answer([_req()]) + "\nHope it helps")
    assert (r.method, r.parsed_by_fallback) == ("object", True)


def test_fence_with_and_without_tag():
    body = _answer([_req()])
    for fence in ("```json\n%s\n```", "```\n%s\n```"):
        r = R.extract_json("text " + fence % body)
        assert r.method in ("object", "fence")  # object slice also reads it
    # braces in prose break the object step, so the fence step must work
    r = R.extract_json("use {x} like this\n```json\n%s\n```\nand {y}" % body)
    assert r.method == "fence" and r.parsed_by_fallback


def test_second_fence_wins_when_first_invalid():
    text = "{a}\n```\nnot json\n```\n```json\n{\"requirements\": []}\n```\n{b}"
    r = R.extract_json(text)
    assert r.method == "fence" and r.data == {"requirements": []}


@pytest.mark.parametrize("text", ["", "plain words", "[1, 2]", "```json\n[1]\n```"])
def test_unreadable(text):
    with pytest.raises(R.UnreadableAnswer) as ei:
        R.extract_json(text)
    assert len(ei.value.errors) == 3


def test_fence_inside_json_string_is_read_whole():
    data = {"requirements": [_req(description=FENCED_CMD)]}
    text = json.dumps(data)
    r = R.extract_json(text)
    assert r.method == "whole" and r.data == data
    # the old order takes the inner snippet first and fails
    with pytest.raises(Exception):
        R.old_order_extract(text)
    assert R.compare_with_old(text, r.data) in ("failed", "different")


def test_compare_with_old_same_for_ordinary():
    text = _answer([_req()])
    assert R.compare_with_old(text, R.extract_json(text).data) == "same"
    t2 = "```json\n%s\n```" % text
    assert R.compare_with_old(t2, R.extract_json(t2).data) == "same"


TEN = [
    _answer([_req()]),
    "pre " + _answer([_req()]) + " post",
    "```json\n" + _answer([_req()]) + "\n```",
    "```\n" + _answer([_req()]) + "\n```",
    json.dumps({"requirements": [_req(description=FENCED_CMD)]}),
    "no json",
    "",
    "[1,2]",
    "```json\nbroken\n```",
    "{ bad",
]


@pytest.mark.parametrize("text", TEN)
def test_old_order_equals_old_method(text):
    try:
        expected = _oracle()._extract_json(text)
    except Exception as exc:
        with pytest.raises(type(exc)):
            R.old_order_extract(text)
    else:
        assert R.old_order_extract(text) == expected


# ---------- parse_answer ----------


def test_parse_matches_old_on_fixture():
    text = _answer([_req(1), _req(2, priority="CRITICAL", asvs_category="Session")],
                   threat_mappings=[{"category": "c", "attack_vector": "v",
                                     "complexity_capability": "low", "relevance_ranking": 9}])
    new = R.parse_answer(text, "S1")
    old_reqs, old_threats = _old_parse(text, "S1")
    assert R.requirement_dicts(new.requirements) == R.requirement_dicts(old_reqs)
    assert [t.model_dump() for t in new.threat_mappings] == [t.model_dump() for t in old_threats]
    assert new.old_parser_result == "same" and new.method == "whole"


def _stored_requirement_sets(limit: int = 50):
    out = []
    for project in PROJECTS:
        for model in MODELS:
            path = STUDY2B / project / model_slug(model) / "records.jsonl"
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("error") is None and rec.get("requirements"):
                        out.append((rec["story_id"], rec["requirements"]))
    return out[:limit]


def test_parse_equals_old_and_stored_on_50_real_records():
    """The records hold no raw reply, so the stored requirements are written back as an answer."""
    sets = _stored_requirement_sets(50)
    assert len(sets) == 50
    for story_id, reqs in sets:
        text = json.dumps({"requirements": reqs})
        new = R.parse_answer(text, story_id)
        old_reqs, _ = _old_parse(text, story_id)
        assert new.n_requirements_failed_validation == 0
        assert new.asvs_defaulted == 0
        assert new.old_parser_result == "same"
        assert R.requirement_dicts(new.requirements) == R.requirement_dicts(old_reqs)
        assert R.requirement_dicts(new.requirements) == reqs


def test_failed_validation_is_counted():
    reqs = [_req(i) for i in range(1, 6)]
    reqs[2]["acceptance_criteria"] = [{"step": 1}]
    p = R.parse_answer(_answer(reqs), "S1")
    assert len(p.requirements) == 4
    assert p.n_requirements_raw == 5
    assert p.n_requirements_failed_validation == 1
    assert p.validation_failures[0].index == 2
    assert len(p.validation_failures[0].error) <= 300
    # the old code drops it in silence: same four requirements
    old_reqs, _ = _old_parse(_answer(reqs), "S1")
    assert R.requirement_dicts(p.requirements) == R.requirement_dicts(old_reqs)


def test_string_item_is_a_failure_not_a_crash():
    p = R.parse_answer(_answer([_req(1), "oops"]), "S1")
    assert len(p.requirements) == 1 and p.n_requirements_failed_validation == 1


def test_asvs_defaulted_count():
    assert R.parse_answer(_answer([_req(asvs_category="Banana")]), "S1").asvs_defaulted == 1
    assert R.parse_answer(_answer([_req(asvs_category="V2")]), "S1").asvs_defaulted == 0
    assert R.parse_answer(_answer([_req(asvs_category="V1: Architecture")]), "S1").asvs_defaulted == 0
    assert R.parse_answer(_answer([_req(asvs_category="V1")]), "S1").asvs_defaulted == 0


@pytest.mark.parametrize("text", ['{"a": 1}', '{"requirements": null}', '{"requirements": {"a": 1}}'])
def test_no_requirements_list(text):
    with pytest.raises(R.NoRequirementsList):
        R.parse_answer(text, "S1")


def test_empty_list_is_fine():
    p = R.parse_answer('{"requirements": []}', "S1")
    assert p.n_requirements_raw == 0 and p.requirements == ()


def test_threat_mappings():
    good = {"category": "c", "attack_vector": "v", "complexity_capability": "low", "relevance_ranking": 2}
    p = R.parse_answer(_answer([_req()], threat_mappings=[good, {"relevance_ranking": "x"}, "s"]), "S1")
    assert (len(p.threat_mappings), p.n_threat_mappings_raw, p.n_threat_mappings_failed) == (1, 3, 2)
    p = R.parse_answer(_answer([_req()]), "S1")
    assert (len(p.threat_mappings), p.n_threat_mappings_raw, p.n_threat_mappings_failed) == (0, 0, 0)
    p = R.parse_answer(_answer([_req()], threat_mappings="x"), "S1")
    assert p.n_threat_mappings_failed == 1


def test_threads_do_not_mix_counts():
    texts = [_answer([_req(asvs_category="Banana")] * (i % 3 + 1)) for i in range(24)]
    out: dict[int, int] = {}

    def work(i: int) -> None:
        out[i] = R.parse_answer(texts[i], "S1").asvs_defaulted

    threads = [threading.Thread(target=work, args=(i,)) for i in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert out == {i: i % 3 + 1 for i in range(24)}


def test_import_is_light():
    import subprocess

    code = (
        "import sys; sys.path[:0]=[%r,%r]; import tuning_lib.parse, tuning_lib.passages;"
        "bad=[m for m in ('secreqgen.retrieval','chromadb','sentence_transformers') if m in sys.modules];"
        "assert not bad, bad"
    ) % (str(_ROOT / "scripts"), str(_ROOT / "src"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
