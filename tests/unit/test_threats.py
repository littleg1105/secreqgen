# ruff: noqa: E501
"""Tests of the threat list (secreqgen.threats). A stand-in model gives prepared answers; no model is called."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from secreqgen.generation.schemas import UserStory
from secreqgen.threats import ThreatListGenerator, checks, pipeline, render_markdown

PROMPT_HASHES = {
    "system_prompt.txt": "79b5b822179c49c56d9612eb427145e57cc760a18229a6bd295f5ddd2e614700",
    "user_prompt.txt": "f4b6516a3a3cea972176b185d96297d286d2fc43feb5a1d3a2e7bdcad7580956",
    "labels_prompt.txt": "8ee6692c35e669e9289a28a5da873ccd605c01a331f4b2b6e26302f49b6171a9",
    "check_prompt.txt": "1c30797e226a04a76fd269a1b112a45b62778d3d5f6bf7db0099185805019377",
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
    assert "accepts (0)" in page and "does not accept (0)" not in page


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
        return Reply("1. yes\n2. yes\n3. yes\nKEEP" if "attacker" in quote.lower() else "1. no\n2. yes\n3. yes\nDROP")


def test_a_second_model_checks_each_row_and_orders_the_page(docs: Path) -> None:
    checker = Checker()
    model = ThreatListGenerator(StandIn(good_rows), docs, checker=checker).generate(STORY)
    by_quote = {r["quote"]: r for r in model["rows"]}
    assert by_quote[WARNING]["second_model"] == "accepts" and by_quote[RULE]["second_model"] == "refuses"
    assert len(model["rows"]) == 2, "the check removes no row"
    assert len(checker.prompts) == 2 and model["checker"] == "checker-stand-in"
    assert any("Set `session-idle-timeout`" in p for p in checker.prompts), "the check call holds the passage, not only the quote"
    page = render_markdown(model)
    assert page.index("accepts (1)") < page.index("does not accept (1)")
    assert page.index("An attacker who steals the session cookie") < page.index("The session cookie is sent with no Secure flag.")


def test_no_check_call_with_no_second_model(docs: Path) -> None:
    model = ThreatListGenerator(StandIn(good_rows), docs).generate(STORY)
    assert model["checker"] is None and all(r["second_model"] == "" for r in model["rows"])
    assert "second model" not in render_markdown(model)


def test_keep_or_drop_takes_the_last_word() -> None:
    assert checks.keep_or_drop("1. yes\n2. no\n3. yes\nDROP") == "refuses"
    assert checks.keep_or_drop("I would KEEP it, but question 2 is no.\nDROP") == "refuses"
    assert checks.keep_or_drop("1. yes\n2. yes\n3. yes\nKEEP") == "accepts"
    assert checks.keep_or_drop("no verdict") == ""
    assert checks.keep_or_drop("1. yes\n2. yes\n3. yes\nKeep") == "accepts"


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
    main_model = cli.get_config().llm.model_name
    result = CliRunner().invoke(cli.app, ["generate", "threats", str(stories), "--docs", str(docs), "--checker-model", main_model, "--output", str(tmp_path / "out")])
    assert result.exit_code == 1, result.output
    assert "must be a different model" in result.output and "Traceback" not in result.output


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
