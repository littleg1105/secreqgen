"""Tests of the paragraph "About the product". No model, no network."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from secreqgen.context import (
    ABOUT_BUDGET,
    AboutDraft,
    ContextError,
    about_from_context,
    about_from_file_data,
    about_paragraph,
    scan_codebase,
)
from secreqgen.context.checks import check_answer
from secreqgen.threats.pipeline import ABOUT_MAX_WORDS

DATA = json.loads(
    (Path(__file__).parent / "data" / "about_rule_2026-11-17.json").read_text(
        encoding="utf-8"
    )
)
CASES = DATA["cases"]


def groups_of(file_data: dict) -> list[tuple[str, list[str]]]:
    return list(file_data["features"].items())


# 1. The rule -----------------------------------------------------------------


def test_the_budget_is_90_and_the_limit_is_120():
    assert ABOUT_BUDGET == 90 == DATA["budget"]
    assert ABOUT_MAX_WORDS == 120


@pytest.mark.parametrize("case", CASES, ids=[c["product"] for c in CASES])
def test_the_rule_gives_back_the_stored_paragraph_word_for_word(case):
    project = case["file_data"]["project"]
    text, used = about_paragraph(
        project["name"], project["description"], groups_of(case["file_data"])
    )
    assert text == case["paragraph"]
    assert used == text.count(";") + 1
    assert about_from_file_data(case["file_data"]).paragraph == case["paragraph"]


def test_the_form_of_the_paragraph():
    text, used = about_paragraph(
        "Shop",
        "A web shop.",
        [("order_flow", ["Orders.", "Refunds"]), ("ci_cd", ["Deploys"])],
    )
    assert text == (
        "Shop: A web shop. Main parts: order flow, ci cd. "
        "Features: Orders; Refunds; Deploys."
    )
    assert used == 3


def test_no_groups_means_no_main_parts_sentence():
    text, used = about_paragraph("Shop", "A web shop", [])
    assert text == "Shop: A web shop."
    assert used == 0
    text, _ = about_paragraph("Shop", "A web shop", [("", ["Orders"])])
    assert "Main parts" not in text
    assert text == "Shop: A web shop. Features: Orders."


def random_words(rng: random.Random, low: int, high: int) -> str:
    return " ".join(f"w{rng.randint(0, 99)}" for _ in range(rng.randint(low, high)))


def random_input(rng: random.Random):
    name = random_words(rng, 1, 4)
    description = random_words(rng, 0, rng.choice([5, 30, 150])) + rng.choice(["", "."])
    groups = [
        (
            f"g_{rng.randint(0, 9)}",
            [random_words(rng, 1, 14) for _ in range(rng.randint(0, 8))],
        )
        for _ in range(rng.randint(0, 9))
    ]
    return name, description, groups


def test_budget_properties_over_random_inputs():
    rng = random.Random(20261118)
    some_full = some_not_full = 0
    for _ in range(600):
        name, description, groups = random_input(rng)
        text, used = about_paragraph(name, description, groups)
        words = text.split()
        assert len(words) <= ABOUT_MAX_WORDS
        flat = [" ".join(i.rstrip(".").split()) for _g, items in groups for i in items]
        flat = [i for i in flat if i]
        if used:
            assert "Features:" in words
            body = text.split(" Features: ", 1)[1]
            assert body.endswith(".")
            listed = body[:-1].split("; ")
            # The features in the paragraph are a prefix of the list.
            assert listed == flat[:used]
        else:
            assert "Features:" not in words
        # Adding the next feature would cost more than the budget.
        head = text.split(" Features: ", 1)[0].split()
        cost = len(head) + sum(len(i.split()) + 1 for i in flat[:used])
        cost += 1 if used else 0
        assert cost <= ABOUT_BUDGET or not used
        if used < len(flat):
            some_full += 1
            extra = len(flat[used].split()) + 1 + (0 if used else 1)
            assert cost + extra > ABOUT_BUDGET
        else:
            some_not_full += 1
    assert some_full and some_not_full


def test_a_long_head_loses_words_at_the_end_of_the_description():
    description = " ".join(f"d{n}" for n in range(200)) + "."
    groups = [("alpha_beta", ["Orders"])]
    text, used = about_paragraph("Shop", description, groups)
    words = text.split()
    assert len(words) == ABOUT_MAX_WORDS
    assert used == 0 and "Features:" not in words
    assert text.startswith("Shop: d0 d1 d2 ")
    assert text.endswith(" Main parts: alpha beta.")
    assert "d199" not in words
    # The cut keeps the front of the description, word by word.
    kept = [w.rstrip(".") for w in words if w.startswith("d")]
    assert kept == [f"d{n}" for n in range(len(kept))]


def test_a_long_head_without_a_description_still_fits():
    groups = [(f"group{n}", ["x"]) for n in range(300)]
    text, used = about_paragraph("Shop", "", groups)
    assert len(text.split()) <= ABOUT_MAX_WORDS
    assert used == 0
    text, _ = about_paragraph("word " * 400, "A shop.", [])
    assert len(text.split()) == ABOUT_MAX_WORDS


def test_a_description_with_no_text():
    assert about_paragraph("Shop", "", [])[0] == "Shop."
    assert about_paragraph("Shop", " . ", [])[0] == "Shop."


def test_empty_features_are_skipped():
    text, used = about_paragraph("Shop", "A shop", [("a", ["", " . ", "Orders"])])
    assert text == "Shop: A shop. Main parts: a. Features: Orders."
    assert used == 1


# 2. From a context -----------------------------------------------------------


def context() -> dict:
    items = [
        {"text": f"Feature number {n} with a few words in it", "path": f"f{n}.md"}
        for n in range(30)
    ]
    return {
        "name": "Shop",
        "description": "A web shop for small stores.",
        "description_path": "README.md",
        "features": [
            {"group": "Orders", "items": items[:12]},
            {"group": "Pay_ments", "items": items[12:21]},
            {"group": "Admin", "items": items[21:]},
        ],
    }


def test_the_lines_are_the_description_and_the_features_of_the_paragraph():
    ctx = context()
    draft = about_from_context(ctx)
    assert isinstance(draft, AboutDraft)
    all_items = [(g["group"], i) for g in ctx["features"] for i in g["items"]]
    used = len(draft.lines) - 1
    assert 0 < used < len(all_items)
    assert draft.lines[0] == {
        "where": "description",
        "text": "A web shop for small stores.",
        "path": "README.md",
    }
    for line, (group, item) in zip(draft.lines[1:], all_items, strict=False):
        assert line == {
            "where": f"features: {group}",
            "text": item["text"],
            "path": item["path"],
        }
    # The paragraph holds exactly these features, and left_out holds the rest.
    body = draft.paragraph.split(" Features: ", 1)[1][:-1].split("; ")
    assert body == [line["text"] for line in draft.lines[1:]]
    assert [line["text"] for line in draft.left_out] == [
        i["text"] for _g, i in all_items[used:]
    ]
    assert [(line["where"], line["path"]) for line in draft.left_out] == [
        (f"features: {g}", i["path"]) for g, i in all_items[used:]
    ]
    assert len(draft.lines) - 1 + len(draft.left_out) == len(all_items)


def test_a_context_with_a_short_feature_list_leaves_nothing_out():
    ctx = context()
    ctx["features"] = [{"group": "Orders", "items": ctx["features"][0]["items"][:2]}]
    draft = about_from_context(ctx)
    assert draft.left_out == ()
    assert len(draft.lines) == 3


def test_a_context_without_a_description_source_has_an_empty_path():
    ctx = context()
    del ctx["description_path"]
    assert about_from_context(ctx).lines[0]["path"] == ""
    ctx["description_path"] = ""
    assert about_from_context(ctx).lines[0]["path"] == ""


def test_a_context_with_no_description_has_no_description_line():
    ctx = context()
    ctx["description"] = ""
    draft = about_from_context(ctx)
    assert draft.lines[0]["where"].startswith("features:")
    assert draft.paragraph.startswith("Shop. Main parts")


def test_a_context_that_is_no_mapping():
    with pytest.raises(ContextError):
        about_from_context(None)  # type: ignore[arg-type]


def test_check_answer_gives_the_path_of_the_description(tmp_path):
    (tmp_path / "README.md").write_text("# Shop\nA web shop.\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "shop"}')
    facts = scan_codebase(tmp_path)
    refs = {p.path: p.ref for p in facts.parts}
    answer = {"name": "Shop", "description": "A web shop.", "security_profile": "Low"}
    for source, path in [
        (refs["README.md"], "README.md"),
        (refs["package.json"], "package.json"),
        ("F99", ""),
        ("", ""),
        (7, ""),
    ]:
        context, _ = check_answer({**answer, "description_source": source}, facts, None)
        assert context["description_path"] == path, source
    context, _ = check_answer(answer, facts, None)
    assert context["description_path"] == ""
    empty = {**answer, "description": "", "description_source": refs["README.md"]}
    context, _ = check_answer(empty, facts, None)
    assert context["description_path"] == ""


# 3. From the data of a file ---------------------------------------------------


def test_the_four_files_of_the_thesis_give_the_four_paragraphs():
    for case in CASES:
        draft = about_from_file_data(case["file_data"])
        assert draft.paragraph == case["paragraph"]
        assert all(line["path"] == "" for line in draft.lines + draft.left_out)
        assert draft.lines[0]["where"] == "description"
        features = sum(len(v) for v in case["file_data"]["features"].values())
        assert len(draft.lines) - 1 + len(draft.left_out) == features


def test_the_name_and_description_may_stand_in_system():
    data = {
        "system": {"name": "Keycloak", "description": "An IAM tool."},
        "features": {"login": ["Single sign-on"]},
    }
    draft = about_from_file_data(data)
    assert draft.paragraph == (
        "Keycloak: An IAM tool. Main parts: login. Features: Single sign-on."
    )


def test_a_file_with_no_feature_list_raises():
    entry = DATA["no_feature_list"]
    with pytest.raises(ContextError, match="The file has no feature list."):
        about_from_file_data({"system": entry["system"]})
    with pytest.raises(ContextError, match="The file has no feature list."):
        about_from_file_data({"system": entry["system"], "architecture": {"a": [1]}})
    with pytest.raises(ContextError, match="The file has no feature list."):
        about_from_file_data({"system": entry["system"], "features": {"a": []}})


@pytest.mark.parametrize(
    "data",
    [
        [],
        ["a"],
        7,
        None,
        "text",
        {},
        {"project": {"description": "x"}, "features": {"a": ["b"]}},
        {"project": ["Shop"], "features": {"a": ["b"]}},
        {"project": {"name": "  "}, "features": {"a": ["b"]}},
        {"project": {"name": "Shop"}, "features": ["a", "b"]},
        {"project": {"name": "Shop"}, "features": 5},
        {"project": {"name": "Shop"}, "features": {"a": "text"}},
        {"project": {"name": "Shop"}, "features": {"a": [None, "", []]}},
    ],
)
def test_odd_data_raises_a_context_error(data):
    with pytest.raises(ContextError):
        about_from_file_data(data)


def test_the_messages_are_plain_sentences():
    with pytest.raises(ContextError) as e:
        about_from_file_data({"features": {"a": ["b"]}})
    assert str(e.value) == "The file has no product name."


def test_odd_items_are_skipped_not_guessed():
    data = {
        "project": {"name": "Shop", "description": 5},
        "features": {"a": ["Orders", None, {"x": 1}, 7, True], 3: ["Refunds"]},
    }
    draft = about_from_file_data(data)
    assert draft.paragraph == (
        "Shop: 5. Main parts: a, 3. Features: Orders; 7; Refunds."
    )


# 3. The description is a used line only when the paragraph keeps a word of it


def test_a_description_that_the_rule_cut_out_is_no_used_line():
    long_group = " ".join(f"g{n}" for n in range(125))
    ctx = {
        "name": "Shop",
        "description": "A web shop for small stores.",
        "description_path": "README.md",
        "features": [{"group": long_group, "items": [{"text": "One", "path": "a.md"}]}],
    }
    draft = about_from_context(ctx)
    assert "web shop" not in draft.paragraph
    assert "description" not in [line["where"] for line in draft.lines]
    data = {
        "project": {"name": "Shop", "description": "A web shop for small stores."},
        "features": {long_group: ["One"]},
    }
    from_file = about_from_file_data(data)
    assert "web shop" not in from_file.paragraph
    assert "description" not in [line["where"] for line in from_file.lines]


def test_a_description_that_the_rule_cut_in_part_is_still_a_used_line():
    description = " ".join(f"d{n}" for n in range(200)) + "."
    ctx = {
        "name": "Shop",
        "description": description,
        "description_path": "README.md",
        "features": [{"group": "alpha", "items": [{"text": "One", "path": "a.md"}]}],
    }
    draft = about_from_context(ctx)
    assert "Shop: d0 d1" in draft.paragraph
    assert draft.lines[0]["where"] == "description"
    data = {
        "system": {"name": "Shop", "description": description},
        "features": {"alpha": ["One"]},
    }
    assert about_from_file_data(data).lines[0]["where"] == "description"
