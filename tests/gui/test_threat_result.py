"""The threat list of one story shows its threats and the evidence beside them."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("nicegui")

from nicegui import ui  # noqa: E402

pytestmark = pytest.mark.anyio

STORY = "MM-US-007"
SCRIPT = "<script>alert(1)</script>"
IMG = '<img src=x onerror="document.title=1">'
NOTICE = (
    "This is a draft, not a complete threat model. It covers only what your "
    "documentation says. Check each threat against its quote."
)


NOT_FOUND = "Cannot highlight the quote in this passage. The quote is below."


def _row(row_id, kind, threat, quote, **extra):
    row = {
        "id": row_id,
        "type": kind,
        "threat": threat,
        "harm": "",
        "story_part": "Webhooks can be configured",
        "source": "P1",
        "quote": quote,
        "requirement": f"Requirement of {row_id}.",
        "from_extra_group": False,
        "actor": "",
        "stride": "",
        "weak_signs": [],
        "second_model": "accepts",
    }
    row.update(extra)
    return row


def _model(**extra):
    model = {
        "draft_notice": NOTICE,
        "prompt_set": "threats_v5b_labels",
        "product": "mattermost",
        "model": "gemma4:26b",
        "story": {
            "id": STORY,
            "title": "Bot and integration setup",
            "story": "As an admin I set up webhooks.",
            "acceptance_criteria": [],
        },
        "seconds": 12,
        "calls": [
            {"group": 1, "passages": 1, "attempts": 1, "ok": True, "seconds": 3.0}
        ],
        "label_step": True,
        "about": None,
        "checker": "qwen3.8:27b",
        "extra_passages": 0,
        "passages": [
            {
                "ref": "P1",
                "header": "configure_integrations.md > Integrations > Webhooks",
                "text": (
                    "### [1] configure_integrations.md > Integrations "
                    "(Relevance: 0.5)\n"
                    "Review the settings. By enabling this feature, users may be able "
                    "to perform phishing attacks. A BOT tag appears next to posts."
                ),
                "group": 1,
            }
        ],
        "rows": [
            _row(
                "T1",
                "attack",
                "Users may phish other users.",
                "By enabling this feature, users may be able to perform phishing "
                "attacks.",
                actor="trusted_insider",
                stride="S",
                harm="false identity",
            ),
            _row(
                "T2",
                "rule",
                "A threat with a quote that is gone.",
                "This sentence is not in the passage at all.",
                weak_signs=["the quote is a fragment of 5 words or fewer"],
                second_model="refuses",
            ),
            _row(
                "T3",
                "attack",
                SCRIPT,
                "A BOT tag appears next to posts.",
                requirement=IMG,
            ),
        ],
        "dropped": [],
    }
    model.update(extra)
    return model


def _store(model=None, run_id="20261117-214500-threats", extra_stories=()):
    from secreqgen_gui import storage

    model = model or _model()
    folder = storage.run_dir(run_id) / "stories" / STORY
    folder.mkdir(parents=True)
    (folder / "threats.json").write_text(json.dumps(model), encoding="utf-8")
    (folder / "threats.md").write_text("# page\n", encoding="utf-8")
    per_story = [{"story_id": STORY, "folder": STORY, "status": "ok"}]
    for story_id in extra_stories:
        per_story.append({"story_id": story_id, "folder": story_id, "status": "ok"})
    storage.write_manifest(
        run_id,
        {
            "run_id": run_id,
            "kind": "threats",
            "status": "finished",
            "per_story": per_story,
        },
    )
    return run_id


def _url(run_id, suffix=""):
    return f"/runs/{run_id}/stories/{STORY}{suffix}"


def _verdict_file(run_id):
    from secreqgen_gui import storage

    return storage.run_dir(run_id) / "stories" / STORY / "verdicts.json"


def _marked(user):
    return [
        e.text
        for e in user.find(kind=ui.label).elements
        if "sr-mark" in e.classes
    ]


async def test_groups_and_rows_show_in_order(user):
    run_id = _store()
    await user.open(_url(run_id))
    await user.should_see(f"Threat list: {STORY}")
    await user.should_see("Bot and integration setup")
    await user.should_see(NOTICE)
    await user.should_see("Accepted by the checker (2)")
    await user.should_see("Flagged by the checker (1)")
    await user.should_see("3 threats: 2 accepted, 1 flagged")
    await user.should_see("Keep or delete each threat.")
    await user.should_see("weak support")
    await user.should_see("From 1 passage the writer wrote 3 threats.")
    await user.should_see("All passed the quote check.")
    found = sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)
    texts = [e.text for e in found]
    first = texts.index("Users may phish other users.")
    assert texts[first - 1] == "1"
    second = texts.index(SCRIPT)
    assert texts[second - 1] == "2"
    third = texts.index("A threat with a quote that is gone.")
    assert texts[third - 1] == "3"
    assert first < second < third
    await user.should_see("Pick a threat to see its evidence.")


async def test_click_on_a_row_shows_the_marked_quote(user):
    run_id = _store()
    await user.open(_url(run_id))
    await user.should_see("3 threats: 2 accepted, 1 flagged")
    user.find(marker="row-T1").click()
    await user.should_see("Evidence")
    await user.should_see("configure_integrations.md")
    await user.should_see("Integrations > Webhooks")
    await user.should_see('"Webhooks can be configured"')
    await user.should_see("Requirement for this threat")
    await user.should_see("Requirement of T1.")
    await user.should_see("In your story")
    assert any("phishing" in piece for piece in _marked(user))
    await user.should_not_see(NOT_FOUND)


async def test_missing_quote_shows_the_line_about_it(user):
    run_id = _store()
    await user.open(_url(run_id, "?row=T2"))
    await user.should_see(NOT_FOUND)
    await user.should_see("Why the support is weak")
    await user.should_see("the quote is a fragment of 5 words or fewer")
    await user.should_see("Checker")
    await user.should_see("flagged")
    assert _marked(user) == []


async def test_row_parameter_opens_the_row(user):
    run_id = _store()
    await user.open(_url(run_id, "?row=T3"))
    await user.should_see("Evidence")
    await user.should_see(IMG)
    assert any("BOT tag" in piece for piece in _marked(user))


async def test_keep_stores_the_verdict_and_a_second_click_clears_it(user):
    run_id = _store()
    await user.open(_url(run_id))
    await user.should_see("3 threats: 2 accepted, 1 flagged")
    user.find(marker="row-keep-T1").click()
    await user.should_see("You kept 1 and deleted 0. 2 left.")
    data = json.loads(_verdict_file(run_id).read_text(encoding="utf-8"))
    assert data["rows"]["T1"]["verdict"] == "keep"
    user.find(marker="row-keep-T1").click()
    await user.should_see("Keep or delete each threat.")
    data = json.loads(_verdict_file(run_id).read_text(encoding="utf-8"))
    assert "T1" not in data["rows"]
    user.find(marker="row-delete-T2").click()
    await user.should_see("You kept 0 and deleted 1. 2 left.")
    data = json.loads(_verdict_file(run_id).read_text(encoding="utf-8"))
    assert data["rows"]["T2"]["verdict"] == "delete"


async def test_panel_button_uses_the_same_state(user):
    run_id = _store()
    await user.open(_url(run_id, "?row=T1"))
    await user.should_see("Evidence")
    user.find(marker="panel-keep-T1").click()
    await user.should_see("You kept 1 and deleted 0. 2 left.")
    data = json.loads(_verdict_file(run_id).read_text(encoding="utf-8"))
    assert data["rows"]["T1"]["verdict"] == "keep"


async def test_next_row_moves_the_panel(user):
    run_id = _store()
    await user.open(_url(run_id, "?row=T1"))
    await user.should_see("Evidence")
    user.find("Next threat").click()
    await user.should_see(IMG)


async def test_export_with_no_kept_row_gives_the_notification(user):
    run_id = _store()
    await user.open(_url(run_id))
    await user.should_see("3 threats: 2 accepted, 1 flagged")
    user.find("Export kept threats").click()
    user.find(marker="export-markdown").click()
    await user.should_see("Keep at least one threat before you export.")


async def test_export_after_keep_downloads_the_row(user):
    run_id = _store()
    await user.open(_url(run_id))
    await user.should_see("3 threats: 2 accepted, 1 flagged")
    user.find(marker="row-keep-T1").click()
    await user.should_see("You kept 1 and deleted 0. 2 left.")
    user.find("Export kept threats").click()
    user.find(marker="export-json").click()
    response = await user.download.next()
    assert "Users may phish other users." in response.text


async def test_untrusted_text_shows_as_text(user):
    run_id = _store()
    await user.open(_url(run_id, "?row=T3"))
    await user.should_see(SCRIPT)
    await user.should_see(IMG)
    for kind in (ui.html, ui.markdown, ui.code):
        await user.should_not_see(kind=kind)
    labels = user.find(kind=ui.label).elements
    assert all(e.tag not in ("script", "img") for e in labels)


async def test_story_navigation_follows_the_manifest(user):
    run_id = _store(extra_stories=("MM-US-008",))
    await user.open(_url(run_id))
    await user.should_see("Previous story")
    await user.should_see("Next story")


async def test_not_the_tested_setup_label(user):
    run_id = _store(_model(prompt_set="user:mine", tested_prompts=False))
    await user.open(_url(run_id))
    await user.should_see("untested prompts")


async def test_failed_calls_show_the_warning(user):
    model = _model(
        calls=[{"group": 1, "passages": 1, "attempts": 3, "ok": False, "seconds": 1.0}]
    )
    run_id = _store(model)
    await user.open(_url(run_id))
    await user.should_see(
        "1 of 1 calls to the writer failed. This list is not complete."
    )


async def test_unknown_story_shows_the_empty_state(user):
    run_id = _store()
    await user.open(f"/runs/{run_id}/stories/NOPE")
    await user.should_see("This story is not part of the run.")
    await user.should_see("Open the run")


async def test_unknown_run_shows_the_empty_state(user):
    await user.open(f"/runs/20200101-000000-threats/stories/{STORY}")
    await user.should_see("This run does not exist.")
    await user.should_see("Open History")


async def test_no_threats_shows_the_plain_line(user):
    run_id = _store(_model(rows=[]))
    await user.open(_url(run_id))
    await user.should_see(
        "No threats found for this story. Replay the steps to see why."
    )


async def test_no_checker_counts_weak_support(user):
    run_id = _store(_model(checker=None))
    await user.open(_url(run_id))
    await user.should_see("3 threats: 1 with weak support")
    await user.should_see("Weak support (1)")
    await user.should_see("Threats (2)")


async def test_old_words_are_gone(user):
    run_id = _store()
    await user.open(_url(run_id, "?row=T2"))
    for old in ("Second model", "with a fault", "tool code", "Weak signs"):
        await user.should_not_see(old)
