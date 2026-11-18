"""The words of the step boards, the diagram, the forms and the dialogs (pass 3).

The texts follow the rule of the author: a label is enough, a caption is a
fragment, and the reference text lives on the page Help.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from secreqgen.application.steps import StepEvent

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402
from secreqgen_gui import pipeline_boards as boards  # noqa: E402
from secreqgen_gui import pipeline_view as view  # noqa: E402

pytestmark = pytest.mark.anyio

THREAT_TEXTS = {
    "story": (
        "Story and documentation",
        "One user story and the documentation folder.",
    ),
    "search": (
        "Search",
        "Word search (BM25), no AI. Finds the passages that match the story.",
    ),
    "write": (
        "Write threats",
        "Writer model. Reads the story and one group of passages. For each "
        "threat or security rule in a passage: a threat, its quote, a requirement.",
    ),
    "check": (
        "Quote check",
        "Plain code, no AI. A threat passes only if its quote is in the passage.",
    ),
    "labels": (
        "Actor and STRIDE",
        "Writer model, asked once for the story. Picks both from fixed lists.",
    ),
    "second_model": (
        "Checker",
        "Checker model. Flags a threat if the quote is about another feature, "
        "or if the threat says more than the passage.",
    ),
    "weak_signs": (
        "Weak support",
        "Plain code, no AI. Marks threats with weak signs, such as a quote of "
        "5 words or fewer. Removes nothing.",
    ),
    "read": (
        "Your review",
        "Check each threat against its quote. Keep or delete it. Draft, not a "
        "complete threat model.",
    ),
}
REQUIREMENT_TEXTS = {
    "story": THREAT_TEXTS["story"],
    "search": THREAT_TEXTS["search"],
    "write": (
        "Write requirements",
        "Model, asked once. Reads the story and the passages.",
    ),
    "parse": (
        "Parse",
        "Plain code, no AI. Stores each requirement with its ASVS category and "
        "priority.",
    ),
    "read": ("Your review", "Open the result."),
}


def test_the_titles_and_texts_of_the_threat_board():
    for key, (title, text) in THREAT_TEXTS.items():
        assert boards.TEXTS["threats"][key][:2] == (title, text), key
    assert set(boards.TEXTS["threats"]) == set(THREAT_TEXTS)


def test_the_titles_and_texts_of_the_requirements_board():
    for key, (title, text) in REQUIREMENT_TEXTS.items():
        assert boards.TEXTS["requirements"][key][:2] == (title, text), key
    assert set(boards.TEXTS["requirements"]) == set(REQUIREMENT_TEXTS)


def test_the_details_of_the_steps():
    threats = {key: value[2] for key, value in boards.TEXTS["threats"].items()}
    assert threats["search"] == (
        "Up to 3,000 tokens, page by page: the best sections first, then the "
        "rest of the page. Groups of 5 passages. Then one group of up to 5 other "
        'sections with a risk word such as "attack" or "bypass".'
    )
    assert threats["write"] == (
        "The writer also says if each passage fits the story. Up to 3 tries if "
        "the answer cannot be read."
    )
    assert threats["check"] == (
        "Ignores capital letters and Markdown marks. A threat fails when: the "
        "quote is not in the passage; the passage is not in the group; the "
        "writer did not say that the passage fits the story; a field is empty; "
        "the quote has more than 60 words; another threat has the same quote."
    )
    assert threats["second_model"] == (
        "Reads each threat next to its passage: OK (accepted) or FAULT "
        "(flagged). A flagged threat stays in the list, in its own group."
    )
    for key in ("story", "labels", "weak_signs", "read"):
        assert threats[key] == "", key
    requirements = {
        key: value[2] for key, value in boards.TEXTS["requirements"].items()
    }
    # The groups of 5 and the risk words are true for threat lists only.
    assert requirements["search"] == (
        "Up to 3,000 tokens, page by page: the best sections first, then the "
        "rest of the page."
    )
    assert requirements["write"] == "Up to 3 tries if the answer cannot be read."
    for key in ("story", "parse", "read"):
        assert requirements[key] == "", key


def test_the_texts_hold_no_em_dash_and_no_old_words():
    old = ("SecReqGen searches", "You read", "You pick", "The writer writes")
    for kind in boards.TEXTS.values():
        for parts in kind.values():
            joined = " ".join(parts)
            assert chr(0x2014) not in joined
            assert not any(word in joined for word in old)


def _event(step, state, index=0, total=0, **numbers):
    return StepEvent("S-1", step, state, index, total, numbers)


def test_the_server_note_is_one_text_for_both_flows():
    for kind in ("threats", "requirements"):
        progress = boards.new_progress(kind, "S-1")
        notes = boards.lane_notes("w", "c", "http://localhost:11434/")
        svg = view.render_svg(boards.live_board(progress, notes=notes), moving=False)
        assert "Ollama at localhost:11434" in svg
        assert "The models run in Ollama" not in svg
        assert "The model runs in Ollama" not in svg


def test_the_review_facts_use_the_short_words():
    trace = {
        "story_id": "S-1",
        "check": {"kept": 3, "removed": 0},
        "search": {"passages": 3, "groups": 1},
        "groups": [{"group": 1, "answered": True, "kept": 3, "removed": 0}],
    }
    none = boards.result_board("threats", trace, verdicts={})
    assert none.facts == "3 threats to review."
    assert none.steps["read"].lines == ("3 to review",)
    some = boards.result_board(
        "threats", trace, verdicts={"T1": "keep", "T2": "delete"}
    )
    assert some.facts == "2 of 3 decided: 1 kept, 1 deleted."


def test_the_name_of_a_prompt_set_that_is_not_allowed():
    from secreqgen_gui import jobs

    class Spec:
        prompt_set = "Bad Name"

    manager = jobs.JobManager(check_server=lambda url: None)
    with pytest.raises(jobs.RunFailedError) as caught:
        manager._prompts_dir("threats", Spec())
    assert str(caught.value) == (
        "Name not allowed. Use lowercase letters, digits, hyphens, underscores."
    )


# the forms


def _stories(tmp_path):
    items = [{"id": "US-001", "title": "T", "story": "As a user, I want x."}]
    path = tmp_path / "stories.json"
    path.write_text(json.dumps({"project": "Demo", "user_stories": items}))
    return path


def _docs(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "page.md").write_text("# Page\nText")
    return folder


@pytest.mark.parametrize("route", ["/threats/new", "/requirements/new"])
async def test_no_help_line_under_the_stories_and_the_folder(user, tmp_path, route):
    stories, docs = _stories(tmp_path), _docs(tmp_path)
    url = f"{route}?stories={quote(str(stories))}&docs={quote(str(docs))}"
    await user.open(url)
    await user.should_see("Stories file")
    props = {e.props.get("label"): e.props for e in user.find(kind=ui.input).elements}
    for name in ("Stories file", "Documentation folder"):
        assert "hint" not in props[name], name
    if route == "/threats/new":
        for name in ("Writer", "Checker"):
            assert "hint" not in props[name], name
    await user.should_not_see("JSON or YAML, with a list named user_stories")
    await user.should_not_see("The folder with the documentation of the product")


async def test_the_criteria_field_has_the_new_label(user):
    await user.open("/threats/new")
    user.find("Type a story").click()
    await user.should_see("Acceptance criteria (one per line)")
    await user.should_not_see("one in each line")


async def test_the_hover_of_the_command_block(user, tmp_path):
    stories, docs = _stories(tmp_path), _docs(tmp_path)
    url = f"/threats/new?stories={quote(str(stories))}&docs={quote(str(docs))}"
    await user.open(url)
    await user.should_see("Command")
    tips = [e.text for e in user.find(kind=ui.tooltip).elements]
    assert "The same run, for a terminal" in tips
