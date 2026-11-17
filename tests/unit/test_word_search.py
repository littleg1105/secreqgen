"""The word search of the product must equal the tested lab code (configuration "s1_word_3000").

1. Each copied definition has the same source text, or the same value, as its lab definition.
2. The three prompt files of the package are byte copies of the lab files.
3. For the 15 stories of Study 2, the block of the product equals the stored block of the lab, byte for
   byte, and the edited block equals the block that the lab passage changes give. These tests need the
   documentation folders and the stored search file; they are skipped when a file is missing.
4. The retriever works with the generator (a stand-in for the model), and the command line shows --docs.
5. For the 15 stories of Study 2, the WHOLE prompt of the product (system prompt and user prompt) equals
   the prompt that the lab run "s1b_search" sent with the tested configuration. One known difference:
   the lab kept a code-search hit when the story holds a code name (Mastodon stories). The product has
   no code search. In the final test, no prompt of the new tool held such a hit (0 of 222).

No network and no model.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))

from secreqgen.generation import LLMResponse, SecurityRequirementsGenerator, SecurityRequirementsPromptBuilder  # noqa: E402
from secreqgen.generation.prompt_sets import TESTED_PROMPT_SET  # noqa: E402
from secreqgen.generation.schemas import UserStory  # noqa: E402
from secreqgen.retrieval import passage_edits as PE  # noqa: E402
from secreqgen.retrieval import word_search as WS  # noqa: E402
from tuning_lib import passages as LAB_PASSAGES  # noqa: E402
from tuning_lib import paths as LAB_PATHS  # noqa: E402
from tuning_lib import prompts as LAB_PROMPTS  # noqa: E402
from tuning_lib import search as LAB_SEARCH  # noqa: E402

RTC = _ROOT.parent / "real_test_cases"
FROZEN = RTC / "tuning" / "_search" / "word_3000" / "retrieval_frozen.json"
DOCS = {
    "mastodon": RTC / "mastodon" / "docs",
    "keycloak": RTC / "extracted_docs_md" / "keycloak",
    "bitwarden": RTC / "bitwarden-test-data",
}
STORIES = {p: RTC / "study2" / p / "stories.json" for p in DOCS}
_HAVE_DATA = FROZEN.is_file() and all(d.is_dir() for d in DOCS.values()) and all(f.is_file() for f in STORIES.values())
needs_data = pytest.mark.skipif(not _HAVE_DATA, reason="the stored search file, a documentation folder or a story file is missing")
S1B = RTC / "tuning" / "s1b_search"
CONTEXTS = {p: RTC / p / "system_context.yaml" for p in DOCS}
_HAVE_PROMPTS = _HAVE_DATA and all((S1B / p).is_dir() for p in DOCS) and all(f.is_file() for f in CONTEXTS.values())
needs_prompts = pytest.mark.skipif(not _HAVE_PROMPTS, reason="the stored prompts of the lab run s1b_search or a system context file is missing")

SEARCH_NAMES = [
    "POOL", "PAGE_CAP", "STOP_AT", "SHOWN_RELEVANCE", "SearchError", "Chunk", "_STOP", "_TOKEN_RE", "_CAMEL_RE", "_SPLIT_RE",
    "clean_query", "word_tokens", "page_fill", "shown_order", "format_block", "_INCLUDE_ONE_LINE_RE", "_INCLUDE_PATH_RE",
    "_DIV_OPEN_RE", "_DIV_CLOSE", "strip_include_wrappers", "load_chunks",
]
PASSAGE_NAMES = ["SEPARATOR", "_HEADER_RE", "_CODE_LINE_RE", "PassagesError", "Passage", "split_context", "doc_passages"]
PROMPT_NAMES = [
    "_HEADER_NUMBER_RE", "PromptError", "PassageContext", "MARKUP_PROJECT", "OPTIONS_NO_NAME_LINE", "_KC_COMMANDS", "_GUIDE_TITLES",
    "_MD_ESCAPABLE", "_MD_ESCAPE_RE", "_MANGLED_LINK_RE", "_BLOCK", "_KC_MACRO_RE", "_LINKS_MACRO_RE", "_OPTS_MACRO_RE", "_WS1",
    "_IMPORT_RUN_RE", "_IF_PRODUCT_RE", "_IF_COMMUNITY_RE", "_RAW_STRING_RE", "_ATTR_RE", "_AUTOLINK_RE", "_FENCE_RE", "_INLINE_CODE_RE",
    "_DROP_LINE_RES", "_WRAPPER_OPEN_RE", "_WRAPPER_END_RE", "_WRAPPER_MAX_LINES", "_md_unescape", "_parse_attrs", "_plain_parameters",
    "_render_kc", "_option_names", "_render_options", "_render_links", "_drop_markup_lines", "_code_segments", "_render_markup_body",
    "_render_markup", "_drop_empty",
]


def _same(lab: object, product: object) -> bool:
    """True when a copied definition equals the lab definition."""
    if inspect.isfunction(lab) or inspect.isclass(lab):
        return inspect.getsource(lab) == inspect.getsource(product)
    if isinstance(lab, re.Pattern):
        return isinstance(product, re.Pattern) and (lab.pattern, lab.flags) == (product.pattern, product.flags)
    if isinstance(lab, tuple) and lab and all(isinstance(x, re.Pattern) for x in lab):
        return [(x.pattern, x.flags) for x in lab] == [(x.pattern, x.flags) for x in product]
    if isinstance(lab, MappingProxyType):
        return dict(lab) == dict(product)
    return lab == product


# ---------------------------------------------------------------------------
# 1 and 2. The copies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lab_module", "product_module", "names"),
    [
        (LAB_SEARCH, WS, SEARCH_NAMES),
        (LAB_PASSAGES, PE, PASSAGE_NAMES),
        (LAB_PROMPTS, PE, PROMPT_NAMES),
    ],
)
def test_each_copied_definition_equals_the_lab_definition(lab_module, product_module, names) -> None:
    different = [n for n in names if not _same(getattr(lab_module, n), getattr(product_module, n))]
    assert not different, f"copies that differ from the lab: {different}"


def test_the_fallback_sentence_equals_the_lab_constant() -> None:
    assert PE.FALLBACK_CONTEXT == LAB_PATHS.FALLBACK_CONTEXT and len(PE.FALLBACK_CONTEXT) == 41


def test_the_prompt_files_are_byte_copies_of_the_lab_files() -> None:
    lab_dir = _ROOT / "scripts" / "tuning_lib" / "prompt_versions" / "s1_word_3000"
    for name in ("system_prompt.txt", "rag_user_prompt.txt", "zero_shot_user_prompt.txt"):
        assert (TESTED_PROMPT_SET / name).read_bytes() == (lab_dir / name).read_bytes(), name


def test_the_word_order_equals_the_lab_index() -> None:
    """word_order of the product is SearchIndex.words of the lab (the one copied method)."""
    assert "np.argsort(-scores, kind=\"stable\")" in inspect.getsource(WS.WordSearchRetriever.word_order)
    assert "np.argsort(-scores, kind=\"stable\")" in inspect.getsource(LAB_SEARCH.SearchIndex.words)
    assert "word_tokens(query, drop_stop=True)" in inspect.getsource(WS.WordSearchRetriever.word_order)


# ---------------------------------------------------------------------------
# 3. The blocks of the 15 Study 2 stories
# ---------------------------------------------------------------------------


def _stories(product: str) -> list[dict]:
    return json.loads(STORIES[product].read_text(encoding="utf-8"))["stories"]


@needs_data
@pytest.mark.parametrize("product", sorted(DOCS))
def test_the_block_equals_the_stored_block_of_the_lab(product: str) -> None:
    retriever = WS.WordSearchRetriever(DOCS[product], product=product)
    stories = _stories(product)
    assert len(stories) == 5
    for item in stories:
        story = UserStory(id=item["id"], title=item["title"], story=item["story"], acceptance_criteria=item["acceptance_criteria"])
        lab_story = LAB_PROMPTS.Story(
            story_id=item["id"], project=product, title=item["title"], story=item["story"],
            acceptance_criteria=tuple(item["acceptance_criteria"]), identifiers=(),
        )
        entry = LAB_SEARCH.frozen_entry("word_3000", product, lab_story.prompt_text(), path_root())
        result = retriever.retrieve_for_user_story(story)
        assert result.raw_block == entry["doc_block"], f"{item['id']}: the block differs from the stored block"
        assert hashlib.sha256(result.raw_block.encode("utf-8")).hexdigest() == entry["doc_block_sha256"]
        assert result.tokens == entry["tokens"] and result.files == entry["files"]
        assert result.tokens <= WS.DEFAULT_BUDGET
        # The edits of the tested version, made by the lab code on the stored block.
        context = LAB_PROMPTS.PassageContext(project=product, story_text=lab_story.prompt_text())
        lab_block = LAB_PROMPTS.apply_passage_changes(entry["doc_block"], ["story_name_hits", "render_markup", "drop_empty"], context)
        assert result.block == lab_block, f"{item['id']}: the edited block differs from the lab block"
        assert retriever.format_result_for_prompt(result) == lab_block


def path_root() -> Path:
    """The folder of the stored searches next to this repository."""
    return RTC / "tuning" / "_search"


@needs_data
def test_the_keycloak_blocks_change_under_render_markup_and_the_others_do_not() -> None:
    changed = {}
    for product in sorted(DOCS):
        retriever = WS.WordSearchRetriever(DOCS[product], product=product)
        item = _stories(product)[0]
        story = UserStory(id=item["id"], title=item["title"], story=item["story"], acceptance_criteria=item["acceptance_criteria"])
        result = retriever.retrieve_for_user_story(story)
        changed[product] = result.block != result.raw_block
    assert changed["mastodon"] is False and changed["bitwarden"] is False
    assert changed["keycloak"] is True


# ---------------------------------------------------------------------------
# 4. The retriever with small made-up documentation
# ---------------------------------------------------------------------------


@pytest.fixture()
def small_docs(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "sessions.md").write_text(
        "# Sessions\n\n## Idle timeout\n\nSet `session-idle-timeout` to end a session after a time with no activity.\n\n"
        "## Cookies\n\nThe session cookie has the Secure flag.\n",
        encoding="utf-8",
    )
    (docs / "backup.md").write_text("# Backup\n\n## Schedule\n\nA backup runs each night.\n", encoding="utf-8")
    return docs


STORY = UserStory(id="US-1", title="Session timeout", story="As a user, I want my session to end after idle time, so that nobody uses it.",
                  acceptance_criteria=["The session ends after a time with no activity."])


def test_the_retriever_gives_a_block_in_the_passage_format(small_docs: Path) -> None:
    retriever = WS.WordSearchRetriever(small_docs)
    result = retriever.retrieve_for_user_story(STORY)
    passages = PE.split_context(result.block)
    assert passages and passages[0].source.startswith("sessions.md")
    assert "session-idle-timeout" in result.block
    assert result.n_passages == len(passages) and result.retrieval_time_ms >= 0
    assert result.query_text == WS.clean_query(STORY.title, STORY.story, STORY.acceptance_criteria)
    assert retriever.format_result_for_prompt(result) == result.block
    assert result.block != PE.FALLBACK_CONTEXT


def test_a_missing_documentation_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WS.SearchError):
        WS.WordSearchRetriever(tmp_path / "nothing")


def test_a_folder_with_no_markdown_text_is_refused(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(WS.SearchError):
        WS.WordSearchRetriever(tmp_path / "empty")


def test_the_system_context_comes_from_the_file(small_docs: Path, tmp_path: Path) -> None:
    assert WS.WordSearchRetriever(small_docs).get_system_context() is None
    context = tmp_path / "system_context.yaml"
    context.write_text("name: ExampleApp\n", encoding="utf-8")
    assert WS.WordSearchRetriever(small_docs, system_context_file=context).get_system_context() == "name: ExampleApp\n"


def test_the_generator_works_with_the_retriever_and_the_tested_prompt(small_docs: Path) -> None:
    answer = {
        "passage_notes": [{"passage": 1, "about": "idle timeout of sessions", "fits_story": "yes"}],
        "requirements": [{
            "id": "SR-001", "title": "Idle timeout", "description": "Set session-idle-timeout.", "asvs_category": "V3",
            "priority": "high", "rationale": "The documentation states the setting (passage 1).",
            "acceptance_criteria": ["The session ends."], "implementation_notes": "", "source_references": ["sessions.md"],
        }],
    }
    llm = MagicMock()
    llm.model_name = "stand-in"
    llm.generate.return_value = LLMResponse(
        content=json.dumps(answer), model="stand-in", prompt_tokens=10, completion_tokens=10, total_tokens=20, latency_ms=1.0,
        raw_response={},
    )
    generator = SecurityRequirementsGenerator(
        llm_client=llm, retriever=WS.WordSearchRetriever(small_docs),
        prompt_builder=SecurityRequirementsPromptBuilder(prompts_dir=TESTED_PROMPT_SET),
    )
    result = generator.generate(user_story=STORY, system_context="name: ExampleApp")
    assert len(result.requirements) == 1 and result.requirements[0].title == "Idle timeout"
    sent = llm.generate.call_args
    prompt_text = " ".join(str(v) for v in list(sent.args) + list(sent.kwargs.values()))
    assert "session-idle-timeout" in prompt_text and "passage_notes" in prompt_text and "name: ExampleApp" in prompt_text
    assert result.retrieval_context is not None and "### [1] sessions.md" in result.retrieval_context


def test_the_command_line_shows_the_docs_option() -> None:
    from typer.testing import CliRunner

    from secreqgen.cli import app

    for command in ("requirements", "single"):
        result = CliRunner().invoke(app, ["generate", command, "--help"])
        assert result.exit_code == 0, result.output
        text = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--docs" in text and "--product" in text


def test_the_command_line_refuses_a_missing_documentation_folder(tmp_path: Path) -> None:
    """A short error and exit code 1, before any model call. No Python error text."""
    from typer.testing import CliRunner

    from secreqgen.cli import app

    stories = tmp_path / "stories.json"
    stories.write_text(
        json.dumps({"project": "x", "user_stories": [{"id": "US-1", "title": "t", "story": "s", "acceptance_criteria": []}]}),
        encoding="utf-8",
    )
    missing = str(tmp_path / "nothing")
    for args in (
        ["generate", "requirements", str(stories), "--docs", missing],
        ["generate", "single", "--title", "t", "--story", "s", "--docs", missing],
    ):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 1, result.output
        assert "Documentation folder not found" in result.output
        assert "Traceback" not in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)


def test_the_product_name_is_accepted_in_any_letter_case(small_docs: Path) -> None:
    assert WS.WordSearchRetriever(small_docs, product="Keycloak").product == "keycloak"
    assert WS.WordSearchRetriever(small_docs, product=" KEYCLOAK ").product == "keycloak"
    assert WS.WordSearchRetriever(small_docs, product="").product is None
    assert WS.WordSearchRetriever(small_docs).product is None


def test_no_usable_text_after_the_edits_is_a_search_error(tmp_path: Path) -> None:
    """A Keycloak file that holds only template markup: the edits leave nothing. The error is a SearchError."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text('<#import "/templates/guide.adoc" as tmpl>\n', encoding="utf-8")
    retriever = WS.WordSearchRetriever(docs, product="keycloak")
    with pytest.raises(WS.SearchError):
        retriever.retrieve_for_user_story(STORY)


def test_a_story_with_no_word_in_the_documentation_gives_a_warning(small_docs: Path, caplog: pytest.LogCaptureFixture) -> None:
    retriever = WS.WordSearchRetriever(small_docs)
    with caplog.at_level("WARNING", logger="secreqgen.retrieval.word_search"):
        retriever.retrieve("zzzqqq xxyyzz")
    assert any("no word of the story is in the documentation" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level("WARNING", logger="secreqgen.retrieval.word_search"):
        retriever.retrieve_for_user_story(STORY)
    assert not caplog.records


def test_the_command_line_refuses_baseline_with_docs(small_docs: Path, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from secreqgen.cli import app

    stories = tmp_path / "stories.json"
    stories.write_text(json.dumps({"project": "x", "user_stories": [{"id": "US-1", "title": "t", "story": "s"}]}), encoding="utf-8")
    for args in (
        ["generate", "requirements", str(stories), "--docs", str(small_docs), "--baseline"],
        ["generate", "single", "--title", "t", "--story", "s", "--docs", str(small_docs), "--baseline"],
    ):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 1, result.output
        assert "--baseline cannot be used with --docs" in result.output


def test_the_command_line_runs_the_tested_setup_from_end_to_end(
    small_docs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generate requirements --docs: the stand-in model gets the tested prompt with the passages, and the
    output file holds the requirement. No vector store and no embedder are built."""
    from typer.testing import CliRunner

    from secreqgen import cli

    answer = {
        "passage_notes": [{"passage": 1, "about": "idle timeout", "fits_story": "yes"}],
        "requirements": [{
            "id": "SR-001", "title": "Idle timeout", "description": "Set session-idle-timeout.", "asvs_category": "V3",
            "priority": "high", "rationale": "Passage 1 states the setting.", "acceptance_criteria": ["The session ends."],
        }],
    }
    llm = MagicMock()
    llm.model_name = "stand-in"
    llm.check_health.return_value = True
    llm.generate.return_value = LLMResponse(
        content=json.dumps(answer), model="stand-in", prompt_tokens=10, completion_tokens=10, total_tokens=20, latency_ms=1.0,
        raw_response={},
    )
    seen: dict = {}

    def stand_in_client(cfg, tested_setup: bool):
        seen["tested_setup"] = tested_setup
        return llm

    def must_not_be_built(*args, **kwargs):
        raise AssertionError("the word search must not build an embedder or a vector store")

    monkeypatch.setattr(cli, "_ollama_client", stand_in_client)
    monkeypatch.setattr("secreqgen.ingestion.DocumentEmbedder", must_not_be_built)
    monkeypatch.setattr("secreqgen.retrieval.SecReqGenVectorStore", must_not_be_built)
    stories = tmp_path / "stories.json"
    stories.write_text(
        json.dumps({"project": "ExampleApp", "user_stories": [{
            "id": "US-1", "title": STORY.title, "story": STORY.story, "acceptance_criteria": STORY.acceptance_criteria,
        }]}),
        encoding="utf-8",
    )
    context = tmp_path / "system_context.yaml"
    context.write_text("name: ExampleApp\n", encoding="utf-8")
    out_file = tmp_path / "out.json"
    result = CliRunner().invoke(cli.app, [
        "generate", "requirements", str(stories), "--docs", str(small_docs), "--context", str(context),
        "--format", "json", "--output", str(out_file),
    ])
    assert result.exit_code == 0, result.output
    assert seen["tested_setup"] is True
    sent = llm.generate.call_args.kwargs
    assert "### [1] sessions.md" in sent["prompt"] and "session-idle-timeout" in sent["prompt"]
    assert "name: ExampleApp" in sent["prompt"] and "passage_notes" in sent["prompt"]
    assert sent["system_prompt"] == SecurityRequirementsPromptBuilder(prompts_dir=TESTED_PROMPT_SET).build_system_prompt()
    assert "Rules for the passages" in sent["system_prompt"]
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert [r["title"] for r in written["requirements"]] == ["Idle timeout"]
    assert written["metadata"]["rag_enabled"] is True and written["metadata"]["retrieval_context_length"] > 41


def test_the_word_search_calls_the_model_as_the_tested_runs_did(monkeypatch: pytest.MonkeyPatch) -> None:
    """No JSON-mode option, 900 s and a window of 16384 tokens, as scripts/run_study2.py. A setting wins."""
    from types import SimpleNamespace

    from secreqgen import cli
    from secreqgen.config import LLMConfig

    for name in ("NUM_CTX", "TIMEOUT", "STRUCTURED_OUTPUT"):
        monkeypatch.delenv(f"SECREQGEN_LLM_{name}", raising=False)
    study2 = (_ROOT / "scripts" / "run_study2.py").read_text(encoding="utf-8")
    assert "timeout=900.0, num_ctx=num_ctx, structured_output=False" in study2

    tested = cli._ollama_client(SimpleNamespace(llm=LLMConfig()), tested_setup=True)
    assert (tested.structured_output, tested.num_ctx, tested.timeout) == (False, 16384, 900.0)
    before = cli._ollama_client(SimpleNamespace(llm=LLMConfig()), tested_setup=False)
    assert (before.structured_output, before.num_ctx, before.timeout) == (True, None, 120.0)
    chosen = LLMConfig(structured_output=True, num_ctx=8192, timeout=60.0)
    client = cli._ollama_client(SimpleNamespace(llm=chosen), tested_setup=True)
    assert (client.structured_output, client.num_ctx, client.timeout) == (True, 8192, 60.0)


# ---------------------------------------------------------------------------
# 5. The whole prompt of the 15 Study 2 stories
# ---------------------------------------------------------------------------


def _stored_prompts(product: str) -> dict[str, tuple[str, str, str]]:
    """Story id -> (system prompt, user prompt, user prompt without the block) of the lab run s1b_search."""
    stored: dict[str, set[tuple[str, str, str]]] = {}
    for rec_file in sorted((S1B / product).glob("*/records.jsonl")):
        for line in rec_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["config_id"] == "s1_word_3000":
                stored.setdefault(rec["story_id"], set()).add(
                    (rec["prompt_system"], rec["prompt_user_sent"], rec["prompt_user_non_doc"])
                )
    assert all(len(v) == 1 for v in stored.values()), "a story has more than one stored prompt"
    return {story_id: next(iter(v)) for story_id, v in stored.items()}


def _block_of(sent: str, rest: str) -> str:
    """The part of the sent prompt that the prompt without the block does not hold."""
    i = 0
    while i < min(len(sent), len(rest)) and sent[i] == rest[i]:
        i += 1
    j = 0
    while j < min(len(sent), len(rest)) - i and sent[-1 - j] == rest[-1 - j]:
        j += 1
    return sent[i : len(sent) - j]


def _stand_in_model() -> MagicMock:
    llm = MagicMock()
    llm.model_name = "stand-in"
    llm.generate.return_value = LLMResponse(
        content=json.dumps({"passage_notes": [], "requirements": []}), model="stand-in", prompt_tokens=1,
        completion_tokens=1, total_tokens=2, latency_ms=1.0, raw_response={},
    )
    return llm


@needs_prompts
@pytest.mark.parametrize("product", sorted(DOCS))
def test_the_whole_prompt_equals_the_prompt_that_the_lab_sent(product: str) -> None:
    stored = _stored_prompts(product)
    context_text = CONTEXTS[product].read_text(encoding="utf-8")
    retriever = WS.WordSearchRetriever(DOCS[product], product=product)
    builder = SecurityRequirementsPromptBuilder(prompts_dir=TESTED_PROMPT_SET)
    stories = _stories(product)
    assert len(stories) == 5 and all(item["id"] in stored for item in stories)
    with_code_hits: list[str] = []
    for item in stories:
        llm = _stand_in_model()
        generator = SecurityRequirementsGenerator(llm_client=llm, retriever=retriever, prompt_builder=builder)
        story = UserStory(id=item["id"], title=item["title"], story=item["story"], acceptance_criteria=item["acceptance_criteria"])
        # As "generate requirements --docs ... --context ..." does: the text of the context file is passed in.
        generator.generate(user_story=story, system_context=context_text)
        sent = llm.generate.call_args.kwargs
        lab_system, lab_user, lab_rest = stored[item["id"]]
        assert sent["system_prompt"] == lab_system, f"{item['id']}: the system prompt differs"
        if sent["prompt"] == lab_user:
            continue
        # The one known difference: code-search hits in the lab block. Without them the prompts are equal.
        lab_block = _block_of(lab_user, lab_rest)
        assert any(p.kind == "code" for p in LAB_PASSAGES.split_context(lab_block)), (
            f"{item['id']}: the user prompt differs and the lab block holds no code-search hit"
        )
        assert sent["prompt"] == lab_user.replace(lab_block, LAB_PROMPTS._drop_code_hits(lab_block), 1), (
            f"{item['id']}: the user prompt differs also without the code-search hits"
        )
        with_code_hits.append(item["id"])
    assert not with_code_hits or product == "mastodon", with_code_hits
