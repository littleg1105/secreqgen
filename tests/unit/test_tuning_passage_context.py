"""Tests of the passage context, story_name_hits and the product rule of render_markup."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from tuning_lib import prompts as P
from tuning_lib.guard import private_markers
from tuning_lib.passages import SEPARATOR, code_passages, doc_passages, split_context

STORY = "Reattach login\nAs an admin I want ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH to be safe.\n- It works"
CTX = P.PassageContext(project="mastodon", story_text=STORY)
NAME = "ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH"


def doc(number: int = 1, body: str = "Some documentation.") -> str:
    return f"### [{number}] page.md > Part (Relevance: 0.55)\n{body}"


def hit(number: int, pattern: str, location: str, line: str) -> str:
    name = location.rsplit("/", 1)[-1]
    return (
        f"### [{number}] {name} > {name} (Relevance: 0.55)\n"
        f"### code search hit (pattern: '{pattern}')\nFile: {location}\n```\n{line}\n```\n"
    )


def block(*parts: str) -> str:
    return SEPARATOR.join(parts)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repos"
    (root / "mastodon").mkdir(parents=True)
    monkeypatch.setattr(P, "REPOS_ROOT", root)
    return root / "mastodon"


def write(repo: Path, rel: str, lines: list[str]) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def source(n: int = 20) -> list[str]:
    return [f"line {i}" for i in range(1, n + 1)]


def one_hit_block(pattern: str, location: str, line: str) -> str:
    return block(doc(1), hit(2, pattern, location, line))


def classify(blk: str, ctx: P.PassageContext = CTX) -> list[str | None]:
    return [reason for reason, _ in P.classify_code_hits(blk, ctx)]


# --- Part A: the context ---------------------------------------------------------------


def test_context_reaches_a_change(monkeypatch):
    seen = []
    monkeypatch.setitem(P.PASSAGE_CHANGES, "spy", lambda b, c=None: seen.append(c) or b)
    P.apply_passage_changes(doc(), ["spy"], CTX)
    assert seen == [CTX]


def test_context_is_none_by_default(monkeypatch):
    seen = []
    monkeypatch.setitem(P.PASSAGE_CHANGES, "spy", lambda b, c=None: seen.append(c) or b)
    P.apply_passage_changes(doc(), ["spy"])
    assert seen == [None]


def test_context_is_frozen():
    with pytest.raises(Exception):
        CTX.project = "x"  # type: ignore[misc]


def test_story_name_hits_needs_the_context():
    with pytest.raises(P.PromptError):
        P.apply_passage_changes(doc(), ["story_name_hits"])
    with pytest.raises(P.PromptError):
        P.PASSAGE_CHANGES["story_name_hits"](doc(), None)


def test_story_prompt_text_has_title_story_and_criteria():
    story = P.Story("X-1", "mastodon", "Title", "Sentence.", ("a", "b"), ("id",))
    assert story.prompt_text() == "Title\nSentence.\n- a\n- b"


# --- Part B: which hits are kept -------------------------------------------------------


def test_a_kept_hit(repo):
    lines = source()
    lines[9] = f"x = ENV['{NAME}']"
    write(repo, "app/models/o.rb", lines)
    blk = one_hit_block(NAME, "app/models/o.rb:10", f"x = ENV['{NAME}']")
    assert classify(blk) == [None]
    out = P.apply_passage_changes(blk, ["story_name_hits"], CTX)
    parts = split_context(out)
    assert [p.kind for p in parts] == ["doc", "code"]
    assert parts[1].code_pattern == NAME and parts[1].code_location == "app/models/o.rb:10"
    text = parts[1].text.split("\n")
    assert text[3] == "Source code, not documentation. Lines 7 to 13 of the file. The search found line 10."
    assert text[4] == "```"
    assert text[5:12] == [f"{n}: {lines[n - 1]}" for n in range(7, 14)]
    assert text[12] == "```"


@pytest.mark.parametrize("pattern", ["a|b", "(?i)ALLOW_X", "ALW", "plainword", "log-*", "two words", ".*Foo", "ab-"])
def test_a_pattern_that_is_not_literal_is_dropped(repo, pattern):
    blk = one_hit_block(pattern, "app/a.rb:1", "x")
    assert classify(blk) == ["not_literal"]


def test_code_like_forms():
    for good in ["foo_bar", "ab.cd", "ab/cd", "ab:cd", "ab-cd", "fooBar", "ABCD", "A_1B_CD", "ALLOW_X"]:
        assert P._is_code_like_name(good), good
    for bad in ["abc", "1234", "____", "foobar", "_a_", "FOO!", "", "1.2.3", "e.g.", "a.b.c", "A_1B", "a.bc"]:
        assert not P._is_code_like_name(bad), bad


def test_a_name_that_the_story_does_not_hold_is_dropped(repo):
    write(repo, "a.rb", ["OTHER_NAME_HERE"])
    blk = one_hit_block("OTHER_NAME_HERE", "a.rb:1", "OTHER_NAME_HERE")
    assert classify(blk) == ["not_in_story"]
    lower = P.PassageContext("mastodon", STORY.lower())
    write(repo, "b.rb", [NAME])
    assert classify(one_hit_block(NAME, "b.rb:1", NAME), lower) == ["not_in_story"]


@pytest.mark.parametrize(
    "story_text",
    [f"the {NAME}_V2 flag", f"the PRE_{NAME} flag", f"the x{NAME} flag", f"the {NAME}2 flag"],
)
def test_a_name_inside_a_longer_name_is_dropped(repo, story_text):
    write(repo, "a.rb", [NAME])
    ctx = P.PassageContext("mastodon", story_text)
    assert classify(one_hit_block(NAME, "a.rb:1", NAME), ctx) == ["not_in_story"]


def test_a_name_next_to_punctuation_counts(repo):
    write(repo, "a.rb", [NAME])
    ctx = P.PassageContext("mastodon", f"set `{NAME}`, then ({NAME}).")
    assert classify(one_hit_block(NAME, "a.rb:1", NAME), ctx) == [None]


@pytest.mark.parametrize(
    "path",
    [
        "test/a.rb", "src/Tests/a.rb", "spec/a.rb", "app/SPECS/a.rb", "x/__tests__/a.js",
        "app/foo_test.rb", "app/foo_tests.rb", "app/foo_spec.rb", "app/foo.test.js",
        "app/foo.spec.ts", "app/foo-test.js", "app/foo-spec.js", "app/FooTest.java",
        "app/FooTests.cs", "app/FooSpec.scala", "app/FooSpecs.scala", "app/FooIT.java",
        "app/test.rb", "app/spec.rb",
    ],
)
def test_a_test_file_is_dropped(repo, path):
    write(repo, path, [NAME])
    assert classify(one_hit_block(NAME, f"{path}:1", NAME)) == ["test_file"]


@pytest.mark.parametrize("path", ["app/latest.rb", "app/Contest.java", "app/protest_x.rb", "app/fooit.rb", "app/footest.rb"])
def test_a_file_that_only_looks_like_a_test_is_kept(repo, path):
    write(repo, path, [NAME])
    assert classify(one_hit_block(NAME, f"{path}:1", NAME)) == [None]


def test_a_missing_file_is_dropped(repo):
    assert classify(one_hit_block(NAME, "nope/a.rb:1", NAME)) == ["not_found"]


def test_a_missing_repository_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "REPOS_ROOT", tmp_path / "none")
    assert classify(one_hit_block(NAME, "a.rb:1", NAME)) == ["not_found"]


def test_a_line_that_differs_is_dropped(repo):
    write(repo, "a.rb", ["something else"])
    assert classify(one_hit_block(NAME, "a.rb:1", NAME)) == ["not_found"]


def test_a_line_number_outside_the_file_is_dropped(repo):
    write(repo, "a.rb", [NAME])
    assert classify(one_hit_block(NAME, "a.rb:2", NAME)) == ["not_found"]
    assert classify(one_hit_block(NAME, "a.rb:0", NAME)) == ["not_found"]


def test_the_stored_line_is_compared_after_strip_of_the_file_line(repo):
    write(repo, "a.rb", [f"      {NAME} = 1"])
    assert classify(one_hit_block(NAME, "a.rb:1", f"{NAME} = 1")) == [None]


def test_a_path_that_leaves_the_folder_is_dropped(repo, tmp_path):
    (tmp_path / "repos" / "outside.rb").write_text(NAME + "\n", encoding="utf-8")
    assert classify(one_hit_block(NAME, "../outside.rb:1", NAME)) == ["not_found"]
    with tempfile.TemporaryDirectory(prefix="abs") as folder:  # a name that is not a test folder
        (Path(folder) / "abs.rb").write_text(NAME + "\n", encoding="utf-8")
        assert classify(one_hit_block(NAME, f"{folder}/abs.rb:1", NAME)) == ["not_found"]


def test_a_link_that_leaves_the_folder_is_dropped(repo, tmp_path):
    outside = tmp_path / "secret.rb"
    outside.write_text(NAME + "\n", encoding="utf-8")
    (repo / "link.rb").symlink_to(outside)
    assert classify(one_hit_block(NAME, "link.rb:1", NAME)) == ["not_found"]


def test_the_window_at_the_start_of_a_file(repo):
    ctx = P.PassageContext("mastodon", NAME)
    write(repo, "a.rb", [NAME] + source(9))
    out = P.apply_passage_changes(one_hit_block(NAME, "a.rb:1", NAME), ["story_name_hits"], ctx)
    text = split_context(out)[1].text.split("\n")
    assert text[3] == "Source code, not documentation. Lines 1 to 4 of the file. The search found line 1."
    assert text[5].startswith("1: ") and text[8].startswith("4: ") and text[9] == "```"


def test_the_window_at_the_end_of_a_file(repo):
    ctx = P.PassageContext("mastodon", NAME)
    lines = source(9) + [NAME]
    write(repo, "a.rb", lines)
    out = P.apply_passage_changes(one_hit_block(NAME, "a.rb:10", NAME), ["story_name_hits"], ctx)
    text = split_context(out)[1].text.split("\n")
    assert text[3] == "Source code, not documentation. Lines 7 to 10 of the file. The search found line 10."
    assert text[4] == "```" and text[5] == "7: line 7" and text[8] == f"10: {NAME}" and text[9] == "```"
    assert "10: " + NAME in out and "11: " not in out


def test_an_opening_line_is_found(repo):
    ctx = P.PassageContext("mastodon", NAME)
    lines = (
        ["class Foo", "  def reattach(auth)", "    a = 1", "    # c1", "    # c2", "    # c3", "    # c4"]
        + [f"    x = ENV['{NAME}']"]
    )
    write(repo, "a.rb", lines)
    out = P.apply_passage_changes(one_hit_block(NAME, "a.rb:8", f"x = ENV['{NAME}']"), ["story_name_hits"], ctx)
    text = split_context(out)[1].text.split("\n")
    assert text[4] == "Line 2 starts the enclosing block: def reattach(auth)"
    assert text[5] == "```"


def test_an_opening_line_that_is_not_found(repo):
    ctx = P.PassageContext("mastodon", NAME)
    lines = ["# header"] + [f"  v{i} = {i}" for i in range(7)] + [f"  x = ENV['{NAME}']"]
    write(repo, "a.rb", lines)
    out = P.apply_passage_changes(one_hit_block(NAME, "a.rb:9", f"x = ENV['{NAME}']"), ["story_name_hits"], ctx)
    text = split_context(out)[1].text.split("\n")
    assert "enclosing block" not in out
    assert text[4] == "```"


def test_an_opening_line_needs_less_indentation_and_a_keyword(repo):
    ctx = P.PassageContext("mastodon", NAME)
    lines = (
        ["def outer", "  def same_level"] + ["  a"] * 5 + [f"  x = '{NAME}'"]
    )
    write(repo, "a.rb", lines)
    out = P.apply_passage_changes(one_hit_block(NAME, "a.rb:8", f"x = '{NAME}'"), ["story_name_hits"], ctx)
    assert "Line 1 starts the enclosing block: def outer" in out


def test_a_long_line_is_cut(repo):
    ctx = P.PassageContext("mastodon", NAME)
    write(repo, "a.rb", [NAME + "x" * 400])
    blk = one_hit_block(NAME, "a.rb:1", NAME + "x" * 400)
    out = P.apply_passage_changes(blk, ["story_name_hits"], ctx)
    shown = [ln for ln in out.split("\n") if ln.startswith("1: ")][0]
    assert shown == "1: " + (NAME + "x" * 400)[:240] + " ..."


def test_the_numbering_starts_at_one_in_kept_order(repo):
    write(repo, "a.rb", [NAME])
    write(repo, "b.rb", [NAME])
    blk = block(
        doc(1, "first"),
        hit(2, "a|b", "z.rb:1", "x"),
        hit(3, NAME, "a.rb:1", NAME),
        doc(4, "second"),
        hit(5, NAME, "b.rb:1", NAME),
    )
    parts = split_context(P.apply_passage_changes(blk, ["story_name_hits"], CTX))
    assert [p.index for p in parts] == [1, 2, 3, 4]
    assert [p.kind for p in parts] == ["doc", "code", "doc", "code"]
    assert parts[0].text.endswith("first") and parts[2].text.endswith("second")


def test_a_privacy_marker_in_a_neighbour_line_drops_the_hit(repo):
    marker = [m for m in private_markers() if m != "/Users/"][0]
    lines = ["a", f"# {marker}", "b", NAME, "c"]
    write(repo, "a.rb", lines)
    blk = one_hit_block(NAME, "a.rb:4", NAME)
    assert classify(blk) == ["privacy"]
    assert P.apply_passage_changes(blk, ["story_name_hits"], CTX) == P.apply_passage_changes(blk, ["drop_code_hits"])


def test_the_result_is_read_by_split_context(repo):
    write(repo, "a.rb", ["```", NAME, "### [9] x (Relevance: 0.5)"])
    out = P.apply_passage_changes(one_hit_block(NAME, "a.rb:2", NAME), ["story_name_hits"], CTX)
    parts = split_context(out)
    assert len(doc_passages(parts)) == 1 and len(code_passages(parts)) == 1


def test_no_hit_kept_equals_drop_code_hits(repo):
    blk = block(doc(1), hit(2, "a|b", "x.rb:1", "x"), doc(3, "two"), hit(4, "plainword", "y.rb:1", "y"))
    assert P.PASSAGE_CHANGES["story_name_hits"](blk, CTX) == P.PASSAGE_CHANGES["drop_code_hits"](blk)
    only_docs = block(doc(1), doc(2, "two"))
    assert P.PASSAGE_CHANGES["story_name_hits"](only_docs, CTX) == only_docs


def test_story_name_hits_is_deterministic_and_writes_nothing(repo):
    write(repo, "a.rb", source(5) + [NAME])
    blk = one_hit_block(NAME, "a.rb:6", NAME)
    before = sorted(p.name for p in repo.iterdir())
    a = P.apply_passage_changes(blk, ["story_name_hits"], CTX)
    assert a == P.apply_passage_changes(blk, ["story_name_hits"], CTX)
    assert sorted(p.name for p in repo.iterdir()) == before


def test_the_repository_folder_of_bitwarden(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "REPOS_ROOT", tmp_path)
    assert P._repo_dir("bitwarden") == tmp_path / "bitwarden-server"
    assert P._repo_dir("keycloak") == tmp_path / "keycloak"


# --- Part C: render_markup -------------------------------------------------------------

KC = P.PassageContext("keycloak", "")
MARKUP = 'Start with <@kc.start parameters="--x"/> for {project_name}.'


def test_render_markup_leaves_another_product_unchanged():
    blk = doc(1, MARKUP)
    assert P.apply_passage_changes(blk, ["render_markup"], P.PassageContext("mastodon", "")) == blk
    assert P.PASSAGE_CHANGES["render_markup"](blk, P.PassageContext("bitwarden", "")) == blk


def test_render_markup_renders_keycloak_with_and_without_context():
    out = P.apply_passage_changes(doc(1, MARKUP), ["render_markup"], KC)
    assert out.endswith("Start with bin/kc.sh start --x for Keycloak.")
    assert out == P.apply_passage_changes(doc(1, MARKUP), ["render_markup"])


def test_print_relevant_options_with_names():
    body = 'Text\n\n<@opts.printRelevantOptions includedOptions="hostname hostname-admin"/>\n\nMore'
    out = P.apply_passage_changes(doc(1, body), ["render_markup"], KC)
    assert "Relevant options: hostname, hostname-admin\n" in out
    assert "<@" not in out


def test_print_relevant_options_with_a_pair_of_tags_and_escapes():
    body = (
        'A\n\n\\<@opts.printRelevantOptions includedOptions="log log-*" '
        'excludedOptions="log-file-async* db-\\<datasource\\>"\\>\n\n=== Console\n\n'
        "\\</@opts.printRelevantOptions\\>\n\nB"
    )
    out = P.apply_passage_changes(doc(1, body), ["render_markup"], KC)
    assert "Relevant options: log, log-*\n" in out and "Left out" not in out and "log-file-async" not in out
    assert "</@" not in out and "<@" not in out and "=== Console" in out


@pytest.mark.parametrize(
    "call",
    ["<@opts.printRelevantOptions/>", '<@opts.printRelevantOptions includedOptions=""/>', "<@opts.printRelevantOptions >"],
)
def test_print_relevant_options_without_a_name(call):
    out = P.apply_passage_changes(doc(1, f"x\n\n{call}\n\ny"), ["render_markup"], KC)
    assert "(The published page shows a table of the relevant options here.)" in out
    assert "<@" not in out


def test_print_relevant_options_is_not_rendered_for_another_product():
    blk = doc(1, '<@opts.printRelevantOptions includedOptions="a b"/>')
    assert P.apply_passage_changes(blk, ["render_markup"], P.PassageContext("mastodon", "")) == blk


# --- Part D: repairs after the review (F1 to F8, F10, F11) -----------------------------

ALT_STORY = "Title\nSet S3_HOSTNAME and keep it. Do not touch other things."
ALT = P.PassageContext("mastodon", ALT_STORY)


def kept_text(blk: str, ctx: P.PassageContext = CTX) -> str:
    return P.apply_passage_changes(blk, ["story_name_hits"], ctx)


# F1: an alternation of literal names
def test_an_alternation_is_kept_when_one_part_is_in_the_story_and_in_the_line(repo):
    write(repo, "a.rb", ["x = ENV['S3_ALIAS_HOST'] || ENV['S3_HOSTNAME']"])
    blk = one_hit_block("S3_HOSTNAME|S3_ALIAS_HOST", "a.rb:1", "x = ENV['S3_ALIAS_HOST'] || ENV['S3_HOSTNAME']")
    assert classify(blk, ALT) == [None]
    assert "1: x = ENV['S3_ALIAS_HOST']" in kept_text(blk, ALT)


def test_an_alternation_needs_the_line_to_hold_a_part_that_the_story_holds(repo):
    write(repo, "a.rb", ["x = ENV['S3_ALIAS_HOST']"])
    blk = one_hit_block("S3_HOSTNAME|S3_ALIAS_HOST", "a.rb:1", "x = ENV['S3_ALIAS_HOST']")
    assert classify(blk, ALT) == ["name_not_in_line"]


def test_an_alternation_needs_one_part_in_the_story(repo):
    write(repo, "a.rb", ["S3_ALIAS_HOST S3_OTHER_NAME"])
    blk = one_hit_block("S3_OTHER_NAME|S3_ALIAS_HOST", "a.rb:1", "S3_ALIAS_HOST S3_OTHER_NAME")
    assert classify(blk, ALT) == ["not_in_story"]


@pytest.mark.parametrize(
    "pattern",
    ["S3_HOSTNAME|allowlist", "S3_HOSTNAME|(S3_X_NAME)", "S3_HOSTNAME|S3_.*", "S3_HOSTNAME|S3\\w_NAME", "S3_HOSTNAME|[AB]_NAME",
     "S3_HOSTNAME|", "|S3_HOSTNAME", "S3_HOSTNAME||S3_ALIAS_HOST", "S3_HOSTNAME|abc"],
)
def test_an_alternation_with_a_part_that_is_not_literal_is_dropped(repo, pattern):
    write(repo, "a.rb", ["S3_HOSTNAME"])
    assert classify(one_hit_block(pattern, "a.rb:1", "S3_HOSTNAME"), ALT) == ["not_literal"]


def test_pattern_names_split_at_the_bar():
    assert P._pattern_names("AB_CD|ef_gh") == ["AB_CD", "ef_gh"]
    assert P._pattern_names("AB_CD") == ["AB_CD"]
    assert P._pattern_names("AB_CD|x") is None


# F2: the stored line holds the name
def test_a_line_without_the_name_is_dropped_with_its_own_reason(repo):
    write(repo, "a.rb", ["something else"])
    assert classify(one_hit_block(NAME, "a.rb:1", "something else")) == ["name_not_in_line"]
    assert "name_not_in_line" in P.HIT_REASONS


def test_the_name_in_the_line_is_case_sensitive(repo):
    write(repo, "a.rb", [NAME.lower()])
    assert classify(one_hit_block(NAME, "a.rb:1", NAME.lower())) == ["name_not_in_line"]


# F3: the story holds the name as a whole token
@pytest.mark.parametrize(
    "story_text, held",
    [
        ("see A_B.c.d here", False),
        ("see A_B.c here", True),
        ("see pre-MY_NAME here", False),
        ("see MY_NAME. Next", True),
        ("see `MY_NAME`", True),
        ("set MY_NAME=true", True),
        ("see MY_NAME_X here", False),
        ("see (MY_NAME), then", True),
        ("path/MY_NAME", False),
        ("see :MY_NAME: here", True),
        ("see -MY_NAME- here", True),
        ("my_name here", False),
    ],
)
def test_story_holds_the_name_by_token(story_text, held):
    name = "A_B.c" if "A_B" in story_text else "MY_NAME"
    assert P._story_holds_name(story_text, name) is held


def test_a_name_inside_a_dotted_longer_name_is_dropped(repo):
    write(repo, "a.rb", ["x = AB_C.de"])
    ctx = P.PassageContext("mastodon", "use AB_C.de.fg here")
    assert classify(one_hit_block("AB_C.de", "a.rb:1", "x = AB_C.de"), ctx) == ["not_in_story"]
    assert classify(one_hit_block("AB_C.de", "a.rb:1", "x = AB_C.de"), P.PassageContext("mastodon", "use AB_C.de.")) == [None]


# F4: 4 letters or more
@pytest.mark.parametrize("name", ["1.2.3", "e.g.", "a.b.c", "A_1_2", "12_34_5"])
def test_a_name_with_fewer_than_four_letters_is_not_code_like(name):
    assert not P._is_code_like_name(name)


def test_a_name_with_four_letters_is_code_like():
    assert P._is_code_like_name("ab.cd") and P._is_code_like_name("a_b_c_d")


# F5: the opening line, level by level
def test_the_opening_line_of_a_block_that_ended_is_not_shown(repo):
    ctx = P.PassageContext("mastodon", "MY_NAME_X")
    lines = ["  def a", "    1", "  end", "  x", "  y", "  z", "  foo.each do", "    q", "    MY_NAME_X", "    r", "  end"]
    write(repo, "a.rb", lines)
    out = kept_text(one_hit_block("MY_NAME_X", "a.rb:9", "MY_NAME_X"), ctx)
    assert "enclosing block" not in out and "9: " in out


def test_the_walk_goes_up_through_lines_that_are_not_keywords(repo):
    ctx = P.PassageContext("mastodon", "MY_NAME_X")
    lines = ["def outer", "  items.each do |i|", "    if i", "      q1", "      q2", "      q3", "      q4", "      MY_NAME_X"]
    write(repo, "a.rb", lines)
    out = kept_text(one_hit_block("MY_NAME_X", "a.rb:8", "MY_NAME_X"), ctx)
    assert "Line 1 starts the enclosing block: def outer" in out


def test_the_walk_skips_comments_and_blank_lines(repo):
    ctx = P.PassageContext("mastodon", "MY_NAME_X")
    lines = ["class K", "# note", "// note", " * note", "/* note */", "", "  # c", "  w1", "  w2", "  w3", "  w4", "  MY_NAME_X"]
    write(repo, "a.rb", lines)
    out = kept_text(one_hit_block("MY_NAME_X", "a.rb:12", "MY_NAME_X"), ctx)
    assert "Line 1 starts the enclosing block: class K" in out


def test_an_opening_line_inside_the_window_is_not_shown(repo):
    ctx = P.PassageContext("mastodon", "MY_NAME_X")
    write(repo, "a.rb", ["class K", "  def m", "    MY_NAME_X"])
    out = kept_text(one_hit_block("MY_NAME_X", "a.rb:3", "MY_NAME_X"), ctx)
    assert "enclosing block" not in out


def test_the_walk_stops_after_400_lines(repo):
    ctx = P.PassageContext("mastodon", "MY_NAME_X")
    far = ["def far"] + ["  w"] * 397 + ["  MY_NAME_X"]  # line 399, def far is 398 lines up
    write(repo, "a.rb", far)
    assert "Line 1 starts the enclosing block: def far" in kept_text(one_hit_block("MY_NAME_X", "a.rb:399", "MY_NAME_X"), ctx)
    too_far = ["def far"] + ["  w"] * 400 + ["  MY_NAME_X"]
    write(repo, "b.rb", too_far)
    assert "enclosing block" not in kept_text(one_hit_block("MY_NAME_X", "b.rb:402", "MY_NAME_X"), ctx)


def test_the_opening_line_is_cut_after_240_characters(repo):
    ctx = P.PassageContext("mastodon", "MY_NAME_X")
    head = "def " + "n" * 400
    write(repo, "a.rb", [head] + ["  w"] * 6 + ["  MY_NAME_X"])
    out = kept_text(one_hit_block("MY_NAME_X", "a.rb:8", "MY_NAME_X"), ctx)
    assert f"Line 1 starts the enclosing block: {head[:240]} ..." in out


# F6: a cut line ends with " ..."
def test_a_line_of_exactly_240_characters_is_not_cut():
    assert P._shown("x" * 240) == "x" * 240
    assert P._shown("x" * 241) == "x" * 240 + " ..."


# F7: test files
@pytest.mark.parametrize(
    "path",
    [
        "testsuite/a.rb", "x/testing/a.rb", "x/e2e/a.js", "cypress/a.js", "x/fixtures/a.rb", "x/mocks/a.rb",
        "x/__mocks__/a.js", "x/Testsuite/a.rb", "test-utils/a.js", "test_helpers/a.rb", "tests-x/a.js",
        "tests_x/a.js", "x/api-test/a.js", "x/api_test/a.js", "x/api-tests/a.js", "x/api_tests/a.js",
        "app/LoginIT.java", "app/foo.test.js", "app/test_foo.py", "app/FooTests.cs", "app/Test.java", "app/Spec.java",
    ],
)
def test_more_forms_of_a_test_file(path):
    assert P._is_test_path(path)


@pytest.mark.parametrize(
    "path",
    ["app/SUBMIT.java", "app/Submit.java", "app/AIT.java", "app/IT.java", "app/Test_x.py", "app/latest.rb",
     "app/testing_x.rb", "x/contest/a.rb", "x/attest/a.rb", "x/mock/a.rb", "x/fixture/a.rb", "app/footest.rb",
     "app/FOOTEST.java", "app/Footest.java", "app/FooTESTS.java"],
)
def test_a_path_that_is_not_a_test_file(path):
    assert not P._is_test_path(path)


def test_a_it_suffix_needs_a_lower_case_letter_before_it():
    assert P._is_test_file_name("LoginIT") and not P._is_test_file_name("LOGINIT") and not P._is_test_file_name("Login1IT")


# F8: printRelevantOptions
def opts(body: str) -> str:
    return P.apply_passage_changes(doc(1, body), ["render_markup"], KC).split("\n", 1)[1]


def test_options_list_the_included_names_without_the_excluded_ones():
    out = opts('<@opts.printRelevantOptions includedOptions="a b c" excludedOptions="b"/>')
    assert out == "Relevant options: a, c"


def test_options_do_not_show_excluded_or_denied_names():
    out = opts('<@opts.printRelevantOptions includedOptions="a" excludedOptions="zz" deniedCategories="secret"/>')
    assert out == "Relevant options: a"


def test_options_with_no_name_left():
    line = "(The published page shows a table of the relevant options here.)"
    assert opts('<@opts.printRelevantOptions includedOptions="a" excludedOptions="a"/>') == line
    assert opts('<@opts.printRelevantOptions excludedOptions="a"/>') == line


def test_options_accept_single_quotes():
    assert opts("<@opts.printRelevantOptions includedOptions='a b' excludedOptions='a'/>") == "Relevant options: b"
    assert opts("<@opts.printRelevantOptions includedOptions='x y'/>") == "Relevant options: x, y"
    assert opts("<@opts.printRelevantOptions includedOptions=\"a 'b'\"/>") == "Relevant options: a, 'b'"


def test_options_keep_the_order_of_the_included_names():
    assert opts('<@opts.printRelevantOptions includedOptions="z a m"/>') == "Relevant options: z, a, m"


# F10: {project_name} in the header
def test_project_name_is_rendered_in_the_header_line():
    blk = "### [1] file.md > Title at {project_name} startup (Relevance: 0.45)\nBody {project_name}."
    out = P.apply_passage_changes(blk, ["render_markup"], KC)
    assert out == "### [1] file.md > Title at Keycloak startup (Relevance: 0.45)\nBody Keycloak."
    part = split_context(out)[0]
    assert part.index == 1 and part.text.startswith("### [1] file.md > Title at Keycloak startup (Relevance: 0.45)")


def test_project_name_in_the_header_stays_for_another_product():
    blk = "### [1] file.md > Title at {project_name} startup (Relevance: 0.45)\nBody"
    assert P.apply_passage_changes(blk, ["render_markup"], P.PassageContext("mastodon", "")) == blk


# F11: a carriage return in a shown line, and the closing line of the options macro
def test_a_carriage_return_is_removed_from_shown_lines(repo):
    (repo / "a.rb").write_bytes(f"class K\r\n  def m\r\n    w1\r\n    w2\r\n    w3\r\n    w4\r\n    x = {NAME}\r\n    w5\r\n".encode())
    out = kept_text(one_hit_block(NAME, "a.rb:7", f"x = {NAME}"))
    assert "\r" not in out
    assert f"7:     x = {NAME}\n" in out and "Line 2 starts the enclosing block: def m\n" in out


@pytest.mark.parametrize("closing", ["</@opts.printRelevantOptions>", "\\</@opts.printRelevantOptions\\>", "  </@opts.printRelevantOptions>  "])
def test_the_closing_line_of_the_options_macro_is_dropped(closing):
    body = f'A\n\n<@opts.printRelevantOptions includedOptions="a">\n\n=== Console\n\n{closing}\n\nB'
    assert opts(body) == "A\n\nRelevant options: a\n\n=== Console\n\nB"


def test_the_closing_line_of_the_options_macro_stays_in_a_code_fence():
    body = 'A\n\n```\n</@opts.printRelevantOptions>\n```\n\nB'
    assert "</@opts.printRelevantOptions>" in opts(body)


def test_shown_and_neighbour_lines_remove_the_carriage_return():
    """The file reader turns CRLF into LF, so the removal is tested on the lines directly."""
    assert P._shown("abc\r") == "abc"
    lines = ["class K\r", "  def m\r", "    w1\r", "    w2\r", "    w3\r", "    w4\r", "    x = 1\r", "    w5\r"]
    out = "\n".join(P._neighbour_lines(lines, 7))
    assert "\r" not in out and "7:     x = 1\n" in out and "starts the enclosing block: def m" in out
