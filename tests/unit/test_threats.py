# ruff: noqa: E501
"""Tests of the threat list (secreqgen.threats). A stand-in model gives prepared answers; no model is called."""

from __future__ import annotations

import hashlib
import json
import re
import string
from pathlib import Path

import pytest
from secreqgen.generation.schemas import UserStory
from secreqgen.threats import (
    ThreatListGenerator,
    checks,
    choose_models,
    pipeline,
    render_markdown,
)

PROMPT_HASHES = {
    "system_prompt.txt": "79b5b822179c49c56d9612eb427145e57cc760a18229a6bd295f5ddd2e614700",
    "user_prompt.txt": "f4b6516a3a3cea972176b185d96297d286d2fc43feb5a1d3a2e7bdcad7580956",
    "labels_prompt.txt": "8ee6692c35e669e9289a28a5da873ccd605c01a331f4b2b6e26302f49b6171a9",
    "check_prompt.txt": "9f97aabdae2e33fd0bb6d5f815d6d8827968dcf8487e900225672d6657ed6245",
}
STORY = UserStory(
    id="US-1",
    title="Session timeout behind a proxy",
    story="As an operator, I want sessions to end after idle time, so that nobody uses an open session.",
    acceptance_criteria=["The session ends after a time with no activity.", "The session cookie is sent over TLS only."],
)
WARNING = "An attacker who steals the session cookie can use the session until the idle timeout ends it."
RULE = "The session cookie must have the Secure flag."


@pytest.fixture()
def docs(tmp_path: Path) -> Path:
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "sessions.md").write_text(
        "# Sessions\n\n## Idle timeout\n\nSet `session-idle-timeout` to end a session after a time with no activity.\n\n"
        f"{WARNING}\n\n## Cookies\n\n{RULE}\n",
        encoding="utf-8",
    )
    (folder / "backup.md").write_text("# Backup\n\n## Schedule\n\nA backup runs each night.\n", encoding="utf-8")
    return folder


class Reply:
    def __init__(self, content: str) -> None:
        self.content = content


class StandIn:
    """Answers a threat prompt with rows made by a function, and an actor prompt with fixed lines."""

    model_name = "stand-in"

    def __init__(self, make_rows, raw: str | None = None) -> None:
        self.make_rows = make_rows
        self.raw = raw
        self.threat_prompts: list[str] = []
        self.actor_prompts: list[str] = []
        self.actors = "1: external_individual, E\n2. Privileged insider, I\n3: somebody, S"

    def generate(self, prompt: str, system_prompt: str | None = None, format: str | None = None) -> Reply:
        if prompt.startswith("Task: choose a threat actor and a STRIDE category"):
            self.actor_prompts.append(prompt)
            return Reply(self.actors)
        self.threat_prompts.append(prompt)
        if self.raw is not None:
            return Reply(self.raw)
        sources = re.findall(r"(?m)^### \[(P\d+)\] ", prompt)
        where = {name: prompt.split(f"### [{name}] ", 1)[1].split("### [", 1)[0] for name in sources}
        notes = [{"source": name, "fits_story": "yes", "statements": []} for name in sources]
        return Reply(json.dumps({"passages": notes, "rows": self.make_rows(where)}))


def source_of(where: dict[str, str], sentence: str) -> str:
    return next(name for name, text in where.items() if sentence in text)


def good_rows(where: dict[str, str]) -> list[dict]:
    rows = []
    if any(WARNING in text for text in where.values()):
        rows.append({"source": source_of(where, WARNING), "quote": WARNING, "type": "attack",
                     "threat": "An attacker who steals the session cookie can use the session.", "harm": "access with no permission",
                     "story_part": "The session ends after a time with no activity.", "requirement": "Set `session-idle-timeout`."})
    if any(RULE in text for text in where.values()):
        rows.append({"source": source_of(where, RULE), "quote": RULE, "type": "rule",
                     "threat": "The session cookie is sent with no Secure flag.", "harm": "disclosure of data",
                     "story_part": "a part that the story does not have", "requirement": "Keep the Secure flag on the session cookie."})
    return rows


# ---------------------------------------------------------------------------------------------- script checks
def test_the_prompt_files_are_the_tested_files() -> None:
    for name, expected in PROMPT_HASHES.items():
        data = (pipeline.PROMPT_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, f"{name} was changed: test it again before use"


def test_extract_json_reads_plain_fenced_and_wrapped_answers() -> None:
    assert checks.extract_json('{"rows": []}') == {"rows": []}
    assert checks.extract_json('```json\n{"rows": [1]}\n```') == {"rows": [1]}
    assert checks.extract_json('Here it is: {"rows": [2]} Done.') == {"rows": [2]}
    with pytest.raises(ValueError):
        checks.extract_json("no object here")


def test_check_quote_ignores_markdown_marks_and_letter_case() -> None:
    passage = "> Do **not** use the `request` object.\n\nIt was provided by the user."
    assert checks.check_quote("Do not use the request object.", passage)
    assert checks.check_quote("do NOT use the request object", passage)
    assert checks.check_quote("Do not use ... provided by the user", passage)
    assert not checks.check_quote("Do not trust the request object.", passage)
    assert not checks.check_quote("", passage)


def test_weak_signs_of_a_row() -> None:
    passage = "The token revocation endpoint is used to revoke tokens. Each request to it must be signed."
    story = "As a resource server, I want to revoke tokens."
    none = checks.weak_signs("rule", "Each request to it must be signed.", "A client sends a request that is not signed.", passage, story)
    assert none == []
    no_rule_word = checks.weak_signs("rule", "The token revocation endpoint is used to revoke tokens.", "A client does not revoke tokens.", passage, story)
    assert len(no_rule_word) == 1 and "no rule word" in no_rule_word[0]
    attack = checks.weak_signs("attack", "The token revocation endpoint is used to revoke tokens.", "An attacker can revoke tokens.", passage, story)
    assert attack == [], "an attack row needs no rule word"
    added = checks.weak_signs("rule", "Each request to it must be signed.", "An attacker injects malicious payloads and escalates privileges.", passage, story)
    assert any("not in the passage" in sign for sign in added)
    fragment = checks.weak_signs("attack", "Logged in only", "An attacker can read posts.", "Logged in only. An attacker can read posts.", story)
    assert any("fragment" in sign for sign in fragment)
    no_stride = checks.weak_signs("rule", "Each request to it must be signed.", "A client sends a request that is not signed.", passage, story, "none")
    assert any("STRIDE" in sign for sign in no_stride)


def test_new_words_ignores_the_words_of_the_passage_and_free_words() -> None:
    passage = "Rogue clients can set this header and trick Keycloak."
    assert checks.new_words("An attacker can set the header to trick Keycloak.", passage, "") == []
    assert checks.new_words("An attacker escalates privileges with the header.", passage, "") == ["escalat", "privileg"]
    other_forms = "A client stores the token, validates the secret and changes the settings."
    assert checks.new_words(other_forms, "Store the token. Validate the secret. Change the setting.", "") == []


def test_has_risk_word() -> None:
    assert checks.has_risk_word("Rogue clients can set this header.")
    assert checks.has_risk_word("This **exposes** the password.")
    assert not checks.has_risk_word("A backup runs each night.")


# ---------------------------------------------------------------------------------------------- pipeline
def test_a_valid_row_is_kept_with_its_fields(docs: Path) -> None:
    model = ThreatListGenerator(StandIn(good_rows), docs).generate(STORY)
    rows = {r["quote"]: r for r in model["rows"]}
    attack, rule = rows[WARNING], rows[RULE]
    assert attack["type"] == "attack" and attack["harm"] == "access with no permission"
    assert attack["story_part"] == "The session ends after a time with no activity."
    assert attack["weak_signs"] == [] and rule["weak_signs"] == []
    assert rule["type"] == "rule"
    assert rule["harm"] == "", "the harm of a rule row is not kept"
    assert rule["story_part"] == "", "a story part that is not a copy of the story is not kept"
    assert model["dropped"] == []
    assert model["model"] == "stand-in" and model["prompt_set"] == pipeline.PROMPT_SET


SETTING = "Set `session-idle-timeout` to end a session after a time with no activity."
BACKUP = "A backup runs each night."


def test_rows_that_break_a_rule_are_dropped_with_the_reason(docs: Path) -> None:
    def rows(where: dict[str, str]) -> list[dict]:
        out = good_rows(where)
        if not out:
            return out
        src = out[0]["source"]
        base = {"type": "rule", "threat": "t", "harm": "not stated", "story_part": "", "requirement": "r"}
        return out + [
            {**base, "source": src, "quote": "This sentence is not in the passage."},
            {**base, "source": "P99", "quote": WARNING},
            {**base, "source": out[0]["source"], "quote": out[0]["quote"]},
            {**base, "source": source_of(where, SETTING), "quote": SETTING, "threat": ""},
            {**base, "source": source_of(where, BACKUP), "quote": BACKUP, "requirement": ""},
        ]

    model = ThreatListGenerator(StandIn(rows), docs).generate(STORY)
    reasons = [d["why"] for d in model["dropped"]]
    assert "the quote is not in the passage" in reasons
    assert any(r.startswith("unknown source name") for r in reasons)
    assert "a second row for the same quote" in reasons
    assert "no threat sentence" in reasons and "no requirement" in reasons
    assert {r["quote"] for r in model["rows"]} == {WARNING, RULE}


def test_source_names_and_notes_are_read_in_any_usual_form(docs: Path) -> None:
    class Loose(StandIn):
        def generate(self, prompt, system_prompt=None, format=None):
            reply = super().generate(prompt, system_prompt, format)
            if not prompt.startswith("## Documentation passages"):
                return reply
            data = json.loads(reply.content)
            for note in data["passages"]:
                note["source"], note["fits_story"] = f"[{note['source']}]", "Yes"
            for row in data["rows"]:
                row["source"] = row["source"].lower()
            return Reply(json.dumps(data))

    model = ThreatListGenerator(Loose(good_rows), docs).generate(STORY)
    assert {r["quote"] for r in model["rows"]} == {WARNING, RULE} and model["dropped"] == []
    assert all(re.fullmatch(r"P\d+", r["source"]) for r in model["rows"])


def test_an_object_with_no_row_list_is_no_answer(docs: Path) -> None:
    llm = StandIn(good_rows, raw="{}")
    model = ThreatListGenerator(llm, docs).generate(STORY)
    assert all(c["ok"] is False and c["attempts"] == pipeline.MAX_JSON_ATTEMPTS for c in model["calls"])
    page = render_markdown(model)
    assert "model calls gave no valid answer" in page
    assert "state no threat" not in page, "a failed run must not read as a clean result"


def test_a_model_call_that_fails_does_not_stop_the_story(docs: Path) -> None:
    class Broken(StandIn):
        def generate(self, prompt, system_prompt=None, format=None):
            raise RuntimeError("the server is gone")

    model = ThreatListGenerator(Broken(good_rows), docs).generate(STORY)
    assert model["rows"] == [] and all(c["ok"] is False for c in model["calls"])


def test_the_label_call_and_the_check_call_can_fail(docs: Path) -> None:
    class NoLabels(StandIn):
        def generate(self, prompt, system_prompt=None, format=None):
            if prompt.startswith("Task: choose a threat actor"):
                raise RuntimeError("no answer")
            return super().generate(prompt, system_prompt, format)

    class BrokenChecker:
        model_name = "broken"

        def generate(self, prompt, system_prompt=None, format=None):
            raise RuntimeError("no answer")

    model = ThreatListGenerator(NoLabels(good_rows), docs, checker=BrokenChecker()).generate(STORY)
    assert len(model["rows"]) == 2
    assert all(r["actor"] == "" and r["stride"] == "" and r["second_model"] == "" for r in model["rows"])
    page = render_markdown(model)
    assert "Rows with no verdict of the second model (2)" in page
    assert "accepts (0)" in page and "sees a fault (0)" not in page


def test_the_same_quote_in_two_groups_gives_one_row(tmp_path: Path) -> None:
    folder = tmp_path / "many"
    folder.mkdir()
    same = "The session token must be kept secret."
    text = "# Sessions\n\n" + "".join(f"## Part {i}\n\nThe session timeout rule number {i} ends an idle session. {same}\n\n" for i in range(1, 13))
    (folder / "sessions.md").write_text(text, encoding="utf-8")

    def rows(where: dict[str, str]) -> list[dict]:
        name = next(iter(where))
        return [{"source": name, "quote": same, "type": "rule", "threat": "The session token is not kept secret.", "harm": "not stated",
                 "story_part": "", "requirement": "Keep the session token secret."}]

    model = ThreatListGenerator(StandIn(rows), folder, name_labels=False).generate(STORY)
    assert len(model["calls"]) >= 2
    assert len(model["rows"]) == 1
    assert [d["why"] for d in model["dropped"]] == ["a second row for the same quote"] * (len(model["calls"]) - 1)


def test_a_row_from_a_passage_that_does_not_fit_is_dropped(docs: Path) -> None:
    class NoFit(StandIn):
        def generate(self, prompt, system_prompt=None, format=None):
            reply = super().generate(prompt, system_prompt, format)
            if not prompt.startswith("## Documentation passages"):
                return reply
            data = json.loads(reply.content)
            for note in data["passages"]:
                note["fits_story"] = "no"
            return Reply(json.dumps(data))

    model = ThreatListGenerator(NoFit(good_rows), docs).generate(STORY)
    assert model["rows"] == []
    assert all("fits_story" in d["why"] for d in model["dropped"]) and model["dropped"]


def test_an_answer_with_no_json_is_asked_again_and_then_left_out(docs: Path) -> None:
    llm = StandIn(good_rows, raw="I cannot answer in JSON.")
    model = ThreatListGenerator(llm, docs).generate(STORY)
    assert model["rows"] == []
    assert all(c["ok"] is False and c["attempts"] == pipeline.MAX_JSON_ATTEMPTS for c in model["calls"])
    assert len(llm.threat_prompts) == pipeline.MAX_JSON_ATTEMPTS * len(model["calls"])


def test_a_group_has_at_most_group_size_passages(tmp_path: Path) -> None:
    folder = tmp_path / "many"
    folder.mkdir()
    text = "# Sessions\n\n" + "".join(f"## Part {i}\n\nThe session timeout rule number {i} ends an idle session.\n\n" for i in range(1, 13))
    (folder / "sessions.md").write_text(text, encoding="utf-8")
    llm = StandIn(lambda where: [])
    generator = ThreatListGenerator(llm, folder)
    groups, _ = generator.passage_groups(STORY)
    assert len(groups) >= 2 and all(1 <= len(g) <= pipeline.GROUP_SIZE for g in groups)
    names = [p["ref"] for g in groups for p in g]
    assert len(names) == len(set(names)), "each passage has its own source name"
    generator.generate(STORY)
    assert len(llm.threat_prompts) == len(groups), "one call for each group"


def test_each_row_gets_a_threat_actor_and_a_stride_category(docs: Path) -> None:
    llm = StandIn(good_rows)
    model = ThreatListGenerator(llm, docs).generate(STORY)
    assert [r["actor"] for r in model["rows"]] == ["external_individual", "privileged_insider"]
    assert [r["stride"] for r in model["rows"]] == ["E", "I"]
    assert len(llm.actor_prompts) == 1, "one label call for a story"
    assert "1. " in llm.actor_prompts[0] and "2. " in llm.actor_prompts[0]
    page = render_markdown(model)
    assert "- Threat actor: external individual" in page and "not from the documentation" in page
    assert "- STRIDE: E, elevation of privilege" in page


def test_a_label_that_is_not_in_a_list_is_left_empty(docs: Path) -> None:
    llm = StandIn(good_rows)
    llm.actors = "1: hacker, X\n2: the user, none"
    model = ThreatListGenerator(llm, docs).generate(STORY)
    assert [r["actor"] for r in model["rows"]] == ["", ""]
    assert [r["stride"] for r in model["rows"]] == ["", "none"]
    page = render_markdown(model)
    assert "Threat actor" not in page and "- STRIDE:" not in page
    assert "no STRIDE category" in page, "a row with the value none gets a weak sign"


def test_no_label_call_when_the_step_is_off_or_there_is_no_row(docs: Path) -> None:
    llm = StandIn(good_rows)
    ThreatListGenerator(llm, docs, name_labels=False).generate(STORY)
    assert llm.actor_prompts == []
    empty = StandIn(lambda where: [])
    ThreatListGenerator(empty, docs).generate(STORY)
    assert empty.actor_prompts == []


def test_parse_label_lines_reads_words_and_other_spellings() -> None:
    got = pipeline.parse_label_lines("1: external individual, Spoofing\n2: trusted-insider, Elevation of privilege\n3: accidental_actor; none")
    assert got == {1: ("external_individual", "S"), 2: ("trusted_insider", "E"), 3: ("accidental_actor", "none")}


def test_parse_label_lines() -> None:
    got = pipeline.parse_label_lines("1: external_individual, S\n2) trusted insider - none\n3: nobody, T\n4: OTHER, e\n4: nation_state, D")
    assert got == {1: ("external_individual", "S"), 2: ("trusted_insider", "none"), 3: ("", "T"), 4: ("other", "E")}


class Checker:
    """A stand-in for the second model: it accepts a row when the quote holds the word attacker."""

    model_name = "checker-stand-in"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, system_prompt: str | None = None, format: str | None = None) -> Reply:
        self.prompts.append(prompt)
        quote = prompt.split('Quote: "', 1)[1].split('"\n', 1)[0]
        return Reply("The row has no fault.\nOK" if "attacker" in quote.lower() else "The sentence is about backups.\nFAULT")


def test_a_second_model_checks_each_row_and_orders_the_page(docs: Path) -> None:
    checker = Checker()
    model = ThreatListGenerator(StandIn(good_rows), docs, checker=checker).generate(STORY)
    by_quote = {r["quote"]: r for r in model["rows"]}
    assert by_quote[WARNING]["second_model"] == "accepts" and by_quote[RULE]["second_model"] == "refuses"
    assert len(model["rows"]) == 2, "the check removes no row"
    assert len(checker.prompts) == 2 and model["checker"] == "checker-stand-in"
    assert any("Set `session-idle-timeout`" in p for p in checker.prompts), "the check call holds the passage, not only the quote"
    page = render_markdown(model)
    assert page.index("accepts (1)") < page.index("sees a fault (1)")
    assert page.index("An attacker who steals the session cookie") < page.index("The session cookie is sent with no Secure flag.")


def test_no_check_call_with_no_second_model(docs: Path) -> None:
    model = ThreatListGenerator(StandIn(good_rows), docs).generate(STORY)
    assert model["checker"] is None and all(r["second_model"] == "" for r in model["rows"])
    assert "second model" not in render_markdown(model)


def test_keep_or_drop_takes_the_last_verdict_word() -> None:
    assert checks.keep_or_drop("The sentence is about another flow.\nFAULT") == "refuses"
    assert checks.keep_or_drop("At first it looks like a FAULT, but the passage states it.\nOK") == "accepts"
    assert checks.keep_or_drop("The row is ok, I think.") == "", "a small ok in a sentence is no verdict"
    assert checks.keep_or_drop("") == ""


def test_the_paragraph_about_the_product_goes_to_the_writer_only_and_is_limited(docs: Path) -> None:
    about = "ExampleApp is a web shop. It has sessions, a cart and a backup job."
    llm, checker = StandIn(good_rows), Checker()
    model = ThreatListGenerator(llm, docs, checker=checker, about=about).generate(STORY)
    assert model["about"] == about and len(model["rows"]) == 2
    # The paragraph is the only difference: each prompt is its text block plus the prompt of a run with no paragraph.
    plain, plain_checker = StandIn(good_rows), Checker()
    ThreatListGenerator(plain, docs, checker=plain_checker).generate(STORY)
    assert plain.threat_prompts and len(plain_checker.prompts) == 2
    assert all(p.startswith("## Documentation passages") for p in plain.threat_prompts)
    assert all(p.startswith("User story:") for p in plain_checker.prompts)
    writer_block = string.Template(pipeline.ABOUT_WRITER).substitute(about=about)
    assert about in writer_block
    assert llm.threat_prompts == [writer_block + p for p in plain.threat_prompts]
    assert checker.prompts == plain_checker.prompts, "the checker does not get the paragraph"
    assert all(about not in p for p in checker.prompts)
    # The limit: 120 words are accepted, 121 are refused.
    at_limit = ThreatListGenerator(StandIn(good_rows), docs, about="word " * pipeline.ABOUT_MAX_WORDS)
    assert len(at_limit.about.split()) == pipeline.ABOUT_MAX_WORDS == 120
    with pytest.raises(pipeline.ThreatListError):
        ThreatListGenerator(StandIn(good_rows), docs, about="word " * (pipeline.ABOUT_MAX_WORDS + 1))
    assert f"({len(about.split())} words)" in render_markdown(model) and "paragraph about the product" in render_markdown(model)
    assert "paragraph about the product" not in render_markdown(ThreatListGenerator(StandIn(good_rows), docs).generate(STORY))
    spaced = ThreatListGenerator(StandIn(good_rows), docs, about="  ExampleApp is\n\n a web   shop.  ")
    assert spaced.about == "ExampleApp is a web shop.", "the text becomes one paragraph"
    assert ThreatListGenerator(StandIn(good_rows), docs, about="  \n ").about is None


# ---------------------------------------------------------------------------------------------- page
def test_the_page_shows_rows_with_no_weak_sign_first_and_attacks_before_rules(docs: Path) -> None:
    model = ThreatListGenerator(StandIn(good_rows), docs).generate(STORY)
    by_quote = {r["quote"]: r for r in model["rows"]}
    by_quote[WARNING]["weak_signs"] = ["the quote is a fragment of 5 words or fewer"]
    page = render_markdown(model)
    assert page.index("## Rows with no weak sign (1)") < page.index("## Rows with a weak sign (1)")
    assert page.index("The session cookie is sent with no Secure flag.") < page.index("An attacker who steals the session cookie")
    assert "- Weak sign: the quote is a fragment" in page
    assert "Your verdict:" in page and model["draft_notice"] in page
    assert "- Harm: access with no permission" in page


def test_the_page_says_so_when_there_is_no_row(docs: Path) -> None:
    model = ThreatListGenerator(StandIn(lambda where: []), docs).generate(STORY)
    page = render_markdown(model)
    assert "state no threat and no security rule for this story" in page
    assert "Rows with a weak sign" not in page


# ---------------------------------------------------------------------------------------------- command line
def test_the_command_line_has_the_threats_command() -> None:
    from secreqgen.cli import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["generate", "threats", "--help"])
    assert result.exit_code == 0, result.output
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--docs" in text and "--no-labels" in text and "--story-id" in text and "--checker-model" in text
    assert "--about-file" in text
    assert "--model" in text and "--no-check" in text
    flat = " ".join(text.replace("│", " ").split())
    assert f"else {pipeline.TESTED_WRITER}" in flat, "the help of --model names the default writer"
    assert f"Default: {pipeline.TESTED_CHECKER}" in flat, "the help of --checker-model names the default checker"


def test_the_command_line_refuses_a_missing_documentation_folder(tmp_path: Path) -> None:
    from secreqgen.cli import app
    from typer.testing import CliRunner

    stories = tmp_path / "stories.json"
    stories.write_text(json.dumps({"project": "x", "user_stories": [{"id": "US-1", "title": "t", "story": "s", "acceptance_criteria": []}]}), encoding="utf-8")
    result = CliRunner().invoke(app, ["generate", "threats", str(stories), "--docs", str(tmp_path / "nothing")])
    assert result.exit_code == 1, result.output
    assert "Documentation folder not found" in result.output and "Traceback" not in result.output


class _Healthy:
    model_name = "main-model"

    def check_health(self) -> bool:
        return True

    def generate(self, prompt, system_prompt=None, format=None):
        return Reply('{"passages": [], "rows": []}')


def test_the_command_line_refuses_a_checker_that_is_the_main_model(docs: Path, tmp_path: Path, monkeypatch) -> None:
    import secreqgen.cli as cli
    from typer.testing import CliRunner

    monkeypatch.setattr(cli, "_ollama_client", lambda cfg, tested_setup, model_name=None: _Healthy())
    stories = tmp_path / "stories.json"
    stories.write_text(json.dumps({"project": 2024, "user_stories": [{"id": "US-1", "title": "Session timeout", "story": "s", "acceptance_criteria": []}]}), encoding="utf-8")
    base = ["generate", "threats", str(stories), "--docs", str(docs), "--output", str(tmp_path / "out")]
    result = CliRunner().invoke(cli.app, base + ["--model", "one-model", "--checker-model", "one-model"])
    assert result.exit_code == 1, result.output
    assert "must be a different model" in result.output and "Traceback" not in result.output
    # with no --model the writer is the default writer, so the default writer cannot be the checker
    monkeypatch.delenv("SECREQGEN_LLM_MODEL_NAME", raising=False)
    result = CliRunner().invoke(cli.app, base + ["--checker-model", pipeline.TESTED_WRITER])
    assert result.exit_code == 1 and "must be a different model" in result.output, result.output
    result = CliRunner().invoke(cli.app, base + ["--no-check", "--checker-model", "other-model"])
    assert result.exit_code == 1 and "not both" in result.output and "Traceback" not in result.output, result.output
    assert not (tmp_path / "out").exists(), "a refused command writes nothing"


# ---------------------------------------------------------------------------------------------- the two models
def test_the_default_pair_is_the_tested_one() -> None:
    assert (pipeline.TESTED_WRITER, pipeline.TESTED_CHECKER) == ("gemma4:26b", "qwen3.8:27b")
    assert choose_models() == ("gemma4:26b", "qwen3.8:27b")
    assert choose_models(None, None, None, True) == ("gemma4:26b", "qwen3.8:27b")
    assert choose_models("", "  ", " ") == ("gemma4:26b", "qwen3.8:27b"), "an empty name is no name"


def test_the_writer_is_the_named_model_then_the_model_of_the_settings() -> None:
    assert choose_models(settings_model="mistral-small3.2:24b") == ("mistral-small3.2:24b", "qwen3.8:27b")
    assert choose_models("gemma4:26b", "mistral-small3.2:24b")[0] == "gemma4:26b", "the option wins over the settings"
    assert choose_models(" llama3:8b ") == ("llama3:8b", "qwen3.8:27b")


def test_a_model_does_not_check_its_own_rows() -> None:
    assert choose_models("qwen3.8:27b") == ("qwen3.8:27b", "gemma4:26b"), "the tested pair, the other way round"
    assert choose_models(settings_model="qwen3.8:27b") == ("qwen3.8:27b", "gemma4:26b")
    assert choose_models("gemma4:26b", checker="llama3:8b") == ("gemma4:26b", "llama3:8b")
    for writer, checker in (("gemma4:26b", "gemma4:26b"), (None, "gemma4:26b"), ("qwen3.8:27b", " qwen3.8:27b ")):
        with pytest.raises(pipeline.ThreatListError, match="different model"):
            choose_models(writer, None, checker)
    # two names of one model: another letter case, or the name with no tag
    for writer, checker in (("gemma4", "gemma4:26b"), ("gemma4:26b", "gemma4"), ("Gemma4:26B", "gemma4:26b")):
        with pytest.raises(pipeline.ThreatListError, match="different model"):
            choose_models(writer, None, checker)
    assert choose_models("qwen3.8") == ("qwen3.8", "gemma4:26b"), "the default checker is not the writer with a tag"
    assert choose_models("QWEN3.8:27B") == ("QWEN3.8:27B", "gemma4:26b")
    assert choose_models("gemma4:12b", checker="gemma4:26b") == ("gemma4:12b", "gemma4:26b"), "two sizes are two models"


def test_the_check_can_be_switched_off() -> None:
    assert choose_models(check=False) == ("gemma4:26b", None)
    assert choose_models("qwen3.8:27b", check=False) == ("qwen3.8:27b", None)
    with pytest.raises(pipeline.ThreatListError, match="not both"):
        choose_models(checker="qwen3.8:27b", check=False)


class _Named:
    """A stand-in client that knows its model name. Some names are not in the stand-in server."""

    made: list[str] = []
    missing: set[str] = set()
    tested_setup: list[bool] = []

    def __init__(self, model_name: str, tested_setup: bool = True) -> None:
        self.model_name = model_name
        _Named.made.append(model_name)
        _Named.tested_setup.append(tested_setup)

    def check_health(self) -> bool:
        return self.model_name not in _Named.missing

    def generate(self, prompt, system_prompt=None, format=None):
        return Reply('{"passages": [], "rows": []}')


@pytest.fixture
def named_clients(monkeypatch):
    import secreqgen.cli as cli

    _Named.made, _Named.missing, _Named.tested_setup = [], set(), []
    for name in ("SECREQGEN_LLM_MODEL_NAME", "SECREQGEN_LLM__MODEL_NAME"):  # the two spellings that the settings read
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        cli,
        "_ollama_client",
        lambda cfg, tested_setup, model_name=None: _Named(model_name or cfg.llm.model_name, tested_setup),
    )
    return _Named


def _one_story_file(tmp_path: Path) -> Path:
    stories = tmp_path / "stories.json"
    stories.write_text(json.dumps({"project": "x", "user_stories": [{"id": "US-1", "title": "Session timeout", "story": "s", "acceptance_criteria": []}]}), encoding="utf-8")
    return stories


def test_the_command_line_runs_the_tested_pair_by_default(docs: Path, tmp_path: Path, named_clients, monkeypatch) -> None:
    import secreqgen.cli as cli
    from typer.testing import CliRunner

    base = ["generate", "threats", str(_one_story_file(tmp_path)), "--docs", str(docs), "--output", str(tmp_path / "out")]
    result = CliRunner().invoke(cli.app, base)
    assert result.exit_code == 0, result.output
    assert named_clients.made == ["gemma4:26b", "qwen3.8:27b"], "writer first, then the checker"
    assert named_clients.tested_setup == [True, True], "the two clients get the call values of the tested runs"
    assert "Writer: gemma4:26b. Checker: qwen3.8:27b." in result.output
    saved = json.loads((tmp_path / "out" / "US-1" / "threats.json").read_text(encoding="utf-8"))
    assert saved["model"] == "gemma4:26b" and saved["checker"] == "qwen3.8:27b"

    named_clients.made = []
    result = CliRunner().invoke(cli.app, base + ["--no-check"])
    assert result.exit_code == 0, result.output
    assert named_clients.made == ["gemma4:26b"] and "Checker: none (--no-check)." in result.output
    saved = json.loads((tmp_path / "out" / "US-1" / "threats.json").read_text(encoding="utf-8"))
    assert saved["checker"] is None

    named_clients.made = []
    result = CliRunner().invoke(cli.app, base + ["--model", "qwen3.8:27b"])
    assert result.exit_code == 0 and named_clients.made == ["qwen3.8:27b", "gemma4:26b"], result.output

    # a model name that the user set in the settings is the writer; the option --model wins over it
    monkeypatch.setenv("SECREQGEN_LLM_MODEL_NAME", "mistral-small3.2:24b")
    named_clients.made = []
    result = CliRunner().invoke(cli.app, base)
    assert result.exit_code == 0 and named_clients.made == ["mistral-small3.2:24b", "qwen3.8:27b"], result.output
    named_clients.made = []
    result = CliRunner().invoke(cli.app, base + ["-m", "gemma4:26b", "--checker-model", "llama3:8b"])
    assert result.exit_code == 0 and named_clients.made == ["gemma4:26b", "llama3:8b"], result.output


def test_the_command_line_says_how_to_get_a_missing_model(docs: Path, tmp_path: Path, named_clients) -> None:
    import secreqgen.cli as cli
    from typer.testing import CliRunner

    base = ["generate", "threats", str(_one_story_file(tmp_path)), "--docs", str(docs), "--output", str(tmp_path / "out")]
    named_clients.missing = {"qwen3.8:27b"}
    result = CliRunner().invoke(cli.app, base)
    text = " ".join(result.output.split())
    assert result.exit_code == 1 and "Traceback" not in text, result.output
    assert "checker model qwen3.8:27b" in text and "ollama pull qwen3.8:27b" in text and "--no-check" in text
    assert not (tmp_path / "out").exists(), "no list is written with half of the setup"
    result = CliRunner().invoke(cli.app, base + ["--no-check"])
    assert result.exit_code == 0, "with no check the missing checker model does not matter"

    for missing, options in (({"gemma4:26b"}, []), ({"gemma4:26b"}, ["--no-check"]), ({"gemma4:26b", "qwen3.8:27b"}, [])):
        named_clients.missing = missing
        out = tmp_path / f"out_{len(missing)}_{len(options)}"
        result = CliRunner().invoke(cli.app, base[:-1] + [str(out)] + options)
        text = " ".join(result.output.split())
        assert result.exit_code == 1 and "Traceback" not in text, result.output
        assert "the model gemma4:26b" in text and "ollama pull gemma4:26b" in text and "ollama serve" in text
        assert not out.exists(), "no list is written when the writer is missing"


def test_the_real_client_takes_the_named_model_and_the_tested_call_values() -> None:
    import secreqgen.cli as cli

    cfg = cli.get_config()
    client = cli._ollama_client(cfg, tested_setup=True, model_name="qwen3.8:27b")
    assert client.model_name == "qwen3.8:27b"
    assert client.num_ctx == (cfg.llm.num_ctx or cli.WORD_SEARCH_NUM_CTX)
    assert cli._ollama_client(cfg, tested_setup=True).model_name == cfg.llm.model_name


def test_the_command_line_takes_a_paragraph_about_the_product_from_a_file(docs: Path, tmp_path: Path, monkeypatch) -> None:
    import secreqgen.cli as cli
    from typer.testing import CliRunner

    monkeypatch.setattr(cli, "_ollama_client", lambda cfg, tested_setup, model_name=None: _Healthy())
    stories = tmp_path / "stories.json"
    stories.write_text(json.dumps({"project": "x", "user_stories": [{"id": "US-1", "title": "Session timeout", "story": "s", "acceptance_criteria": []}]}), encoding="utf-8")
    base = ["generate", "threats", str(stories), "--docs", str(docs), "--output", str(tmp_path / "out")]
    good = tmp_path / "about.txt"
    good.write_text("\ufeffExampleApp is a web shop.\nIt has sessions and a cart.\n", encoding="utf-8")
    result = CliRunner().invoke(cli.app, base + ["--about-file", str(good)])
    assert result.exit_code == 0, result.output
    saved = json.loads((tmp_path / "out" / "US-1" / "threats.json").read_text(encoding="utf-8"))
    assert saved["about"] == "ExampleApp is a web shop. It has sessions and a cart."
    for name, text, message in (("long.txt", "word " * 121, "the limit is 120"), ("empty.txt", " \n", "The file has no text"), ("mark.txt", "\ufeff", "The file has no text")):
        bad = tmp_path / name
        bad.write_text(text, encoding="utf-8")
        result = CliRunner().invoke(cli.app, base + ["--about-file", str(bad)])
        assert result.exit_code == 1 and message in result.output and "Traceback" not in result.output, result.output
    result = CliRunner().invoke(cli.app, base + ["--about-file", str(tmp_path / "missing.txt")])
    assert result.exit_code == 1 and "File not found" in result.output, result.output
    result = CliRunner().invoke(cli.app, base + ["--about-file", str(tmp_path)])
    assert result.exit_code == 1 and "Not a file" in result.output and "File not found" not in result.output, result.output
    not_text = tmp_path / "bytes.txt"
    not_text.write_bytes(b"\xff\xfe\xfa product")
    result = CliRunner().invoke(cli.app, base + ["--about-file", str(not_text)])
    assert result.exit_code == 1 and "Cannot read" in result.output and "Traceback" not in result.output, result.output


def test_the_command_line_gives_each_story_its_own_folder(docs: Path, tmp_path: Path, monkeypatch) -> None:
    import secreqgen.cli as cli
    from typer.testing import CliRunner

    monkeypatch.setattr(cli, "_ollama_client", lambda cfg, tested_setup, model_name=None: _Healthy())
    stories = tmp_path / "stories.json"
    story = {"title": "Session timeout", "story": "As a user I want the session to end.", "acceptance_criteria": []}
    stories.write_text(json.dumps({"project": 2024, "user_stories": [{"id": "A/1", **story}, {"id": "A_1", **story}]}), encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(cli.app, ["generate", "threats", str(stories), "--docs", str(docs), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == ["A_1", "A_1_2"]
    assert (out / "A_1_2" / "threats.md").is_file()
