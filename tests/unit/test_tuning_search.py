"""Tests of scripts/tuning_lib/search.py and of the passage changes "search_<option>".

No network, no model: the index test uses small stand-ins for the embedder and the reranker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from tuning_lib import prompts as P  # noqa: E402
from tuning_lib import search as S  # noqa: E402
from tuning_lib.passages import SEPARATOR, split_context  # noqa: E402


def _chunks() -> list[S.Chunk]:
    """Three pages. Page a has 4 sections, page b has 2, page c has 1."""
    spec = [
        ("a.md", "A > one", 100), ("a.md", "A > two", 100), ("a.md", "A > three", 100), ("a.md", "A > four", 100),
        ("b.md", "B > one", 100), ("b.md", "B > two", 100),
        ("c.md", "", 100),
    ]
    return [S.Chunk(file=f, section=s, text=f"Section: {s}\n\ntext {i}", tokens=t) for i, (f, s, t) in enumerate(spec)]


# --- tokens and query ------------------------------------------------------------------


def test_word_tokens_keep_a_name_whole_and_add_its_parts():
    assert S.word_tokens("Set proxy-headers now") == ["set", "proxy-headers", "proxy", "headers", "now"]
    assert S.word_tokens("CAS_UID_KEY") == ["cas_uid_key", "cas", "uid", "key"]
    assert S.word_tokens("clientAuth") == ["clientauth", "client", "auth"]
    assert S.word_tokens("conf/server.keystore") == ["conf/server.keystore", "conf", "server", "keystore"]


def test_word_tokens_drop_stop_words_only_on_request():
    assert S.word_tokens("the server must be set") == ["the", "server", "must", "be", "set"]
    assert S.word_tokens("the server must be set", drop_stop=True) == ["server", "set"]


def test_clean_query_holds_the_three_parts_and_no_keyword_list():
    q = S.clean_query("Title", "As a user, I want X.", ["first", "second"])
    assert q == "Title\nAs a user, I want X.\nfirst; second"


# --- include wrappers ------------------------------------------------------------------


def test_strip_include_wrappers_removes_the_wrapper_and_its_path_and_keeps_other_divs():
    text = "\n".join(
        [
            "Intro line.",
            '<div class="included"',
            'path="/some/local/folder/file.adoc">',
            "",
            "# Heading",
            '<div class="note">',
            "A note.",
            "</div>",
            "Body line.",
            "</div>",
            '<div class="included" path="/other/local/file.adoc">',
            "Second body.",
            "</div>",
            "Last line.",
        ]
    )
    out = S.strip_include_wrappers(text)
    assert "path=" not in out and "/some/local" not in out and "/other/local" not in out
    assert out.split("\n") == [
        "Intro line.",
        "",
        "# Heading",
        '<div class="note">',
        "A note.",
        "</div>",
        "Body line.",
        "Second body.",
        "Last line.",
    ]


def test_strip_include_wrappers_leaves_a_text_with_no_wrapper_unchanged():
    text = "# A\n\n<div>\nx\n</div>\n\ntext"
    assert S.strip_include_wrappers(text) == text


# --- merge and candidates --------------------------------------------------------------


def test_rrf_prefers_an_item_of_both_lists_and_breaks_ties_by_index():
    assert S.rrf([[5, 1, 2], [1, 7]])[0] == 1
    assert S.rrf([[3], [2]]) == [2, 3]  # equal score: the lower index first


def test_candidates_of_each_kind_of_option():
    both, words = S.OPTIONS["both_3000"], S.OPTIONS["word_3000"]
    dense, word = list(range(100)), list(range(99, -1, -1))
    merged = S.candidates(both, dense, word)
    assert len(merged) == S.POOL and set(merged) <= set(dense[:30]) | set(word[:30])
    assert S.candidates(words, None, word) == word[: S.POOL]
    with pytest.raises(S.SearchError):
        S.candidates(both, None, word)
    with pytest.raises(S.SearchError):
        S.candidates(words, dense, None)


# --- page fill -------------------------------------------------------------------------


def test_page_fill_takes_the_other_sections_of_the_page_before_the_next_page():
    chunks = _chunks()
    # order: section 5 (page b), then section 2 (page a)
    assert S.page_fill([5, 2], chunks, budget=450) == [5, 4, 2, 0]
    assert S.page_fill([5, 2], chunks, budget=10_000) == [5, 4, 2, 0, 1, 3]


def test_page_fill_keeps_the_page_limit_and_the_budget():
    chunks = _chunks()
    out = S.page_fill([2, 5, 6], chunks, budget=10_000, page_cap=250)
    assert out == [2, 0, 5, 4, 6]  # two sections of page a, two of page b, one of page c
    assert sum(chunks[i].tokens for i in S.page_fill([2, 5, 6], chunks, budget=350)) <= 350


def test_page_fill_leaves_out_a_section_that_is_too_large_and_tries_the_next():
    chunks = _chunks()
    chunks[1] = S.Chunk(file="a.md", section="A > two", text="big", tokens=5000)
    assert S.page_fill([0], chunks, budget=1000) == [0, 2, 3]


def test_page_fill_refuses_a_budget_of_zero():
    with pytest.raises(S.SearchError):
        S.page_fill([0], _chunks(), budget=0)


def test_shown_order_keeps_the_page_order_and_sorts_inside_a_page():
    assert S.shown_order([5, 4, 2, 0], _chunks()) == [4, 5, 0, 2]


def test_select_uses_the_reranker_only_when_the_option_asks_for_it():
    chunks = _chunks()
    calls = []

    def rerank(query, pool):
        calls.append(list(pool))
        return list(reversed(pool))

    words = S.select(S.OPTIONS["word_3000"], "q", chunks, dense_order=None, word_order=[6, 0], rerank=rerank)
    assert calls == [] and words[0] == 6
    both = S.select(S.OPTIONS["both_3000"], "q", chunks, dense_order=[0, 6], word_order=[0, 6], rerank=rerank)
    assert len(calls) == 1 and both[0] == 6  # the reranker turned the order
    with pytest.raises(S.SearchError):
        S.select(S.OPTIONS["both_3000"], "q", chunks, dense_order=[0], word_order=[0], rerank=None)


# --- block format ----------------------------------------------------------------------


def test_format_block_gives_a_block_that_the_lab_reads():
    chunks = _chunks()
    block = S.format_block(chunks, [4, 5, 6])
    passages = split_context(block)
    assert [p.kind for p in passages] == ["doc", "doc", "doc"]
    assert passages[0].source == "b.md > B > one" and passages[2].source == "c.md > General"
    assert {p.relevance for p in passages} == {S.SHOWN_RELEVANCE}  # one number for each passage
    assert block.split("\n")[1] == "Section: B > one"
    with pytest.raises(S.SearchError):
        S.format_block(chunks, [])


# --- the index, with stand-ins for the models ------------------------------------------


class _FakeEmbedder:
    """Bag of words over a tiny word list, so the order is easy to predict."""

    WORDS = ("tls", "proxy", "token")

    def encode(self, texts, normalize_embeddings=True, **_):
        rows = []
        for text in texts:
            v = np.array([float(text.lower().count(w)) for w in self.WORDS]) + 1e-6
            rows.append(v / np.linalg.norm(v))
        return np.array(rows)


class _FakeCrossEncoder:
    def predict(self, pairs, **_):
        return np.array([float(len(text)) for _, text in pairs])


def test_search_index_runs_an_option_from_query_to_shown_sections():
    chunks = [
        S.Chunk("tls.md", "TLS", "Section: TLS\n\ntls tls certificate", 10),
        S.Chunk("tls.md", "Keystore", "Section: Keystore\n\nkeystore file", 10),
        S.Chunk("proxy.md", "Proxy", "Section: Proxy\n\nproxy headers", 10),
    ]
    index = S.SearchIndex(chunks, _FakeEmbedder(), _FakeCrossEncoder())
    order, sims = index.dense("tls")
    assert order[0] == 0 and set(sims) == {0, 1, 2}
    assert index.words("proxy headers")[0] == 2
    shown = index.run(S.OPTIONS["word_3000"], "certificate")
    assert shown[:2] == [0, 1]  # the hit and then the other section of the same page
    assert split_context(S.format_block(chunks, shown))


# --- the stored search files -----------------------------------------------------------


def _store(tmp_path: Path, option: str = "word_3000", *, damage: bool = False) -> tuple[Path, str]:
    story_text = "Title\nAs a user, I want X.\n- first"
    block = S.format_block(_chunks(), [4, 5])
    entry = {
        "project": "keycloak",
        "story_text_sha256": S.sha_text(story_text),
        "doc_block_sha256": S.sha_text(block),
        "doc_block": block + ("x" if damage else ""),
    }
    path = S.frozen_path(option, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(S.frozen_text(S.OPTIONS[option], {"KC-X": entry}), encoding="utf-8")
    return tmp_path, story_text


def test_frozen_entry_finds_the_story_by_the_hash_of_its_text(tmp_path):
    root, story_text = _store(tmp_path)
    entry = S.frozen_entry("word_3000", "keycloak", story_text, root)
    assert len(split_context(entry["doc_block"])) == 2
    data = json.loads(S.frozen_path("word_3000", root).read_text())
    assert data["option"]["budget"] == 3000 and data["reranker"] is None


def test_frozen_entry_refuses_an_unknown_story_another_product_and_a_damaged_block(tmp_path):
    root, story_text = _store(tmp_path)
    with pytest.raises(S.SearchError):
        S.frozen_entry("word_3000", "keycloak", story_text + " changed", root)
    with pytest.raises(S.SearchError):
        S.frozen_entry("word_3000", "mastodon", story_text, root)
    S._CACHE.clear()
    bad_root, story_text = _store(tmp_path / "bad", damage=True)
    with pytest.raises(S.SearchError):
        S.frozen_entry("word_3000", "keycloak", story_text, bad_root)


def test_frozen_name_keeps_one_file_for_the_lab_and_gives_each_other_product_its_own_file(tmp_path):
    assert S.frozen_name() == S.FROZEN_NAME
    for project in ("mastodon", "keycloak", "bitwarden"):
        assert S.frozen_path("word_3000", tmp_path, project) == S.frozen_path("word_3000", tmp_path)
    path = S.frozen_path("word_3000", tmp_path, "mattermost")
    assert path == tmp_path / "word_3000" / "retrieval_frozen_mattermost.json"
    with pytest.raises(S.SearchError):
        S.frozen_name("../outside")


def test_frozen_entry_of_an_outside_product_reads_only_the_file_of_that_product(tmp_path):
    S._CACHE.clear()
    option = S.OPTIONS["word_3000"]
    block = "### [1] a.md > A (Relevance: 0.50)\nSection: A\ntext of a"
    entry = {
        "project": "mattermost",
        "story_text_sha256": S.sha_text("a mattermost story"),
        "doc_block_sha256": S.sha_text(block),
        "doc_block": block,
    }
    path = S.frozen_path("word_3000", tmp_path, "mattermost")
    path.parent.mkdir(parents=True)
    path.write_text(S.frozen_text(option, {"MM-1": entry}), encoding="utf-8")
    assert S.frozen_entry("word_3000", "mattermost", "a mattermost story", tmp_path)["doc_block"] == block
    with pytest.raises(S.SearchError):  # the file of the lab does not exist in this folder
        S.frozen_entry("word_3000", "keycloak", "a mattermost story", tmp_path)
    S._CACHE.clear()


def test_frozen_entry_finds_an_older_story_of_a_lab_product_in_the_second_file(tmp_path):
    S._CACHE.clear()
    option = S.OPTIONS["word_3000"]
    block = "### [1] a.md > A (Relevance: 0.50)\nSection: A\ntext of a"

    def entry(story):
        return {
            "project": "keycloak",
            "story_text_sha256": S.sha_text(story),
            "doc_block_sha256": S.sha_text(block),
            "doc_block": block,
        }

    lab = S.frozen_path("word_3000", tmp_path, "keycloak")
    lab.parent.mkdir(parents=True)
    lab.write_text(S.frozen_text(option, {"KC-S2-001": entry("a lab story")}), encoding="utf-8")
    with pytest.raises(S.SearchError):  # no second file yet
        S.frozen_entry("word_3000", "keycloak", "an older story", tmp_path)
    other = S.other_stories_path("word_3000", "keycloak", tmp_path)
    assert other.name == "retrieval_frozen_keycloak_other_stories.json" and other.parent == lab.parent
    other.write_text(S.frozen_text(option, {"KC-US-001": entry("an older story")}), encoding="utf-8")
    assert S.frozen_entry("word_3000", "keycloak", "an older story", tmp_path)["doc_block"] == block
    assert S.frozen_entry("word_3000", "keycloak", "a lab story", tmp_path)["doc_block"] == block
    with pytest.raises(S.SearchError):  # a product outside the lab has no second file
        S.other_stories_path("word_3000", "mattermost", tmp_path)
    S._CACHE.clear()


def test_frozen_path_refuses_an_unknown_option_and_a_missing_file_gives_a_clear_error(tmp_path):
    with pytest.raises(S.SearchError):
        S.frozen_path("nope", tmp_path)
    with pytest.raises(S.SearchError, match="search_freeze"):
        S.read_frozen("both_3000", tmp_path / "empty")


# --- the passage change ----------------------------------------------------------------

_OLD_BLOCK = SEPARATOR.join(
    [
        "### [1] old.md > Old one (Relevance: 0.47)\nSection: Old one\n\nold text",
        "### [2] Thing.java:12 > Thing.java:12 (Relevance: 0.55)\n### code search hit (pattern: 'proxy')\n"
        "File: src/Thing.java:12\n\n    proxy = 1;",
        "### [3] old.md > Old two (Relevance: 0.45)\nSection: Old two\n\nold text two",
    ]
)


def test_each_search_option_has_a_passage_change():
    assert {f"search_{name}" for name in S.OPTIONS} <= set(P.PASSAGE_CHANGES)


def test_search_change_puts_the_new_passages_first_and_keeps_the_code_hits(monkeypatch):
    new_block = S.format_block(_chunks(), [4, 5, 6])
    seen = {}

    def fake_entry(option, project, story_text, root=None):
        seen.update(option=option, project=project, story_text=story_text)
        return {"doc_block": new_block}

    monkeypatch.setattr(P._search, "frozen_entry", fake_entry)
    context = P.PassageContext(project="keycloak", story_text="the story")
    out = P.PASSAGE_CHANGES["search_both_3000"](_OLD_BLOCK, context)
    passages = split_context(out)
    assert [p.kind for p in passages] == ["doc", "doc", "doc", "code"]
    assert [p.index for p in passages] == [1, 2, 3, 4]
    assert "old text" not in out and "proxy = 1;" in out
    assert seen == {"option": "both_3000", "project": "keycloak", "story_text": "the story"}
    # the block passes the checks of the lab, also with the other changes of the best version
    assert P.apply_passage_changes(_OLD_BLOCK, ["search_both_3000", "render_markup"], context)


def test_search_change_needs_a_context_and_reports_a_missing_store(monkeypatch):
    with pytest.raises(P.PromptError, match="PassageContext"):
        P.PASSAGE_CHANGES["search_word_3000"](_OLD_BLOCK)

    def missing(option, project, story_text, root=None):
        raise S.SearchError("no stored search")

    monkeypatch.setattr(P._search, "frozen_entry", missing)
    with pytest.raises(P.PromptError, match="no stored search"):
        P.PASSAGE_CHANGES["search_word_3000"](_OLD_BLOCK, P.PassageContext("keycloak", "s"))


def test_search_change_refuses_a_stored_block_with_a_code_hit(monkeypatch):
    monkeypatch.setattr(P._search, "frozen_entry", lambda *a, **k: {"doc_block": _OLD_BLOCK})
    with pytest.raises(P.PromptError, match="not documentation"):
        P.PASSAGE_CHANGES["search_word_3000"](_OLD_BLOCK, P.PassageContext("keycloak", "s"))


# --- equal text, empty passages -------------------------------------------------------


def test_page_fill_leaves_out_a_section_with_the_same_text_as_a_selected_one():
    chunks = _chunks()
    chunks[4] = S.Chunk(file="b.md", section="A > one", text=chunks[0].text, tokens=100)  # a copy of section 0
    assert S.page_fill([0, 4], chunks, budget=10_000) == [0, 1, 2, 3, 5]


def test_drop_empty_removes_a_documentation_passage_with_no_text_and_numbers_again():
    block = SEPARATOR.join(
        [
            "### [1] a.md > General (Relevance: 0.50)\n",
            "### [2] a.md > Part (Relevance: 0.50)\nSection: Part\n\ntext",
            "### [3] T.java:1 > T.java:1 (Relevance: 0.55)\n### code search hit (pattern: 'x')\nFile: T.java:1\n\nx",
        ]
    )
    out = P.PASSAGE_CHANGES["drop_empty"](block)
    passages = split_context(out)
    assert [(p.index, p.kind) for p in passages] == [(1, "doc"), (2, "code")]
    assert passages[0].source == "a.md > Part"
    full = "### [1] a.md > Part (Relevance: 0.50)\nSection: Part\n\ntext"
    assert P.PASSAGE_CHANGES["drop_empty"](full) is full


# --- the template rules that the new passages need -------------------------------------


def _kc(body: str) -> str:
    block = f"### [1] page.md > General (Relevance: 0.50)\n{body}"
    out = P.PASSAGE_CHANGES["render_markup"](block, P.PassageContext("keycloak", "s"))
    return out.partition("\n")[2]


def test_render_markup_removes_a_run_of_imports_that_the_converter_wrapped():
    body = (
        '\\<#import "/templates/guide.adoc" as tmpl\\> \\<#import\n'
        '"/templates/kc.adoc" as kc\\> \\<#import "/templates/links.adoc" as\n'
        "links\\>\n"
        "\n"
        "Real text of the page."
    )
    out = _kc(body)
    assert "import" not in out and "templates" not in out
    assert out.strip() == "Real text of the page."
    plain = '<#import "/templates/guide.adoc" as tmpl> <#import\n"/templates/kc.adoc" as kc>\n\nText.'
    assert _kc(plain).strip() == "Text."


def test_render_markup_drops_an_include_line_and_keeps_the_text_around_it():
    out = _kc('Before.\n\n\\<#include "partials/oidc/available-endpoints.adoc" /\\>\n\nAfter.')
    assert "include" not in out and "Before." in out and "After." in out


@pytest.mark.parametrize(
    "guide, title",
    [("hostname", "Configuring the hostname (v2)"), ("enabletls", "Configuring TLS"), ("all-config", "All configuration")],
)
def test_render_markup_writes_the_title_of_a_linked_server_guide(guide, title):
    assert _kc(f'See \\<@links.server id="{guide}"/\\> for details.') == f"See {title} for details."
    assert _kc(f'See <@links.server\nid="{guide}"/> for details.') == f"See {title} for details."


def test_render_markup_leaves_a_link_to_an_unknown_guide_as_it_is():
    assert "links.server" in _kc('See \\<@links.server id="no-such-guide"/\\>.')


def test_render_markup_writes_the_names_of_an_include_options_call():
    out = _kc('\\<@opts.includeOptions includedOptions="https-management-\\*"\nexcludedOptions="https-management-client-auth"/\\>')
    assert out == "Relevant options: https-management-*"
    assert _kc('\\<@opts.includeOptions includedOptions="log-file log-file-\\*"/\\>') == "Relevant options: log-file, log-file-*"


def test_render_markup_hides_product_only_text_and_keeps_community_text():
    body = (
        "Use \\<@profile.ifProduct\\> one of these databases \\</@profile.ifProduct\\>"
        "\\<@profile.ifCommunity\\> this database \\</@profile.ifCommunity\\> or skip."
    )
    out = _kc(body)
    assert "one of these databases" not in out and "profile" not in out
    assert "this database" in out and out.startswith("Use ") and out.endswith("or skip.")
    block = "A.\n\n\\<@profile.ifProduct\\>\nProduct text.\n\\</@profile.ifProduct\\>\n\nB."
    out = _kc(block)
    assert "Product text." not in out and "A." in out and "B." in out
