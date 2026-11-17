"""Prompt versions, passage changes, the prompt builder and the generality check of the prompt lab.

The builder makes the full RAG prompt of the old tool again (arm B2 of Study 2b) from a template
folder and the stored passages. It uses the registered prompt builder of the tool, so the text
substitution cannot drift. The only function that writes is `new_version`, and it writes through
the guard. Importing this module reads no file, writes no file and uses no network.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder
from secreqgen.generation.schemas import UserStory
from tuning_lib import search as _search
from tuning_lib.guard import GuardError, WriteGuard, assert_no_private_text, guarded_mkdir, guarded_write_text
from tuning_lib.passages import (
    FALLBACK_CONTEXT,
    SEPARATOR,
    PassagesError,
    doc_passages,
    read_study2b_context,
    read_study2b_prompts,
    split_context,
)
from tuning_lib.paths import (
    DATA,
    MODELS,
    PROJECTS,
    REPO_DIR_OVERRIDE,
    REPOS_ROOT as _REPOS_ROOT,
    RTC,
    SCRIPTS,
    STUDY2,
    STUDY2B,
)

VERSIONS_ROOT: Path = SCRIPTS / "tuning_lib" / "prompt_versions"
TEMPLATE_FILES: tuple[str, ...] = (
    "system_prompt.txt",
    "rag_user_prompt.txt",
    "zero_shot_user_prompt.txt",
)
VERSION_FILE: str = "version.json"
BASELINE: str = "v0_old"
VERSION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
KNOWN_PLACEHOLDERS: tuple[str, ...] = (
    "story_id",
    "story_title",
    "story_text",
    "acceptance_criteria",
    "system_context",
    "retrieved_context",
    "json_schema",
)
PRODUCT_WORDS: tuple[str, ...] = ("keycloak", "bitwarden", "mastodon", "mattermost", "gitlab")
PHRASE_WORDS: int = 5
MAX_DESCRIPTION = 300
MAX_PARENT_DEPTH = 20

_VERSION_KEYS: tuple[str, ...] = ("name", "parent", "description", "passage_changes")
_DOLLAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_HEADER_NUMBER_RE = re.compile(r"^### \[(\d+)\] ")
_STORY_ID_RE = re.compile(r"\b(?:BW|KC|MT)-(?:S2|ADV)-\d{3}\b", re.IGNORECASE)
_WORD_RE = re.compile(r"\w+")


class PromptError(Exception):
    """A version, a template, a story or a prompt is not as expected."""


class PromptMismatch(PromptError):
    """A rebuilt prompt differs from the stored one."""


class NoDocumentationAfterChange(PromptError):
    """No documentation passage is left after the passage changes."""


class GeneralityError(PromptError):
    """A version has new generality hits."""


VERSIONS_GUARD: WriteGuard = WriteGuard(
    allowed_roots=(VERSIONS_ROOT,),
    forbidden_roots=(STUDY2, STUDY2B, DATA),
    forbidden_names=("study2", "study2b"),
)


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Passage changes
# ---------------------------------------------------------------------------

# Root of the product repositories. A test can point it to a temporary folder.
REPOS_ROOT: Path = _REPOS_ROOT


@dataclass(frozen=True)
class PassageContext:
    """What a passage change may need besides the block: the product and the story text.

    story_text is the title, the story sentence and the acceptance criteria as the prompt shows them.
    """

    project: str
    story_text: str


PassageChange = Callable[[str, "PassageContext | None"], str]


def _identity(block: str, context: PassageContext | None = None) -> str:
    """Return the block unchanged (the same object)."""
    return block


def _drop_code_hits(block: str, context: PassageContext | None = None) -> str:
    """Keep the documentation passages, drop the code-search hits, number the passages from 1."""
    kept = doc_passages(split_context(block))
    if not kept:
        raise NoDocumentationAfterChange("drop_code_hits: the block holds no documentation passage")
    parts: list[str] = []
    for number, passage in enumerate(kept, start=1):
        text, n = _HEADER_NUMBER_RE.subn(f"### [{number}] ", passage.text, count=1)
        if n != 1:
            raise PromptError("drop_code_hits: a passage has no numbered header line")
        parts.append(text)
    return SEPARATOR.join(parts)


# --- story_name_hits: keep a code hit only for a name that the story holds ---------------
# A one-line hit is kept only when its search pattern is one literal code name (or a list of
# such names split at "|") and the story holds one of the names as a whole token, the file is
# not a test file, the hit line is really in the file of the product repository and that line
# holds the name. A kept hit is shown with the lines around it, so the model can see what the
# code does. Every other hit is dropped. The function reads files and writes nothing.

_LITERAL_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_SEPARATED_RE = re.compile(r"[A-Za-z0-9][_.:/-][A-Za-z0-9]")
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
_UPPER_RE = re.compile(r"^[A-Z0-9_]+$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:/-]+")
_TOKEN_EDGE = ".:/-"
_TEST_FOLDERS = frozenset(
    {"test", "tests", "spec", "specs", "__tests__", "testsuite", "testing", "e2e", "cypress",
     "fixtures", "mocks", "__mocks__"}
)
_TEST_FOLDER_STARTS = ("test-", "test_", "tests-", "tests_")
_TEST_FOLDER_ENDS = ("-test", "_test", "-tests", "_tests")
_TEST_SUFFIXES_CASED = ("Test", "Tests", "Spec", "Specs")
_TEST_SUFFIXES_LOWER = ("_test", "_tests", "_spec", ".test", ".spec", "-test", "-spec")
_OPENING_RE = re.compile(
    r"^\s*(?:def|class|module|function|public|private|protected|internal|static|async"
    r"|override|export|fun|func|sub)\b"
)
MIN_PATTERN_LENGTH = 4
MIN_PATTERN_LETTERS = 4
CUT_MARK = " ..."
NEIGHBOUR_LINES = 3
OPENING_SEARCH_LINES = 400
MAX_SHOWN_LINE = 240
HIT_REASONS: tuple[str, ...] = (
    "not_literal", "not_in_story", "test_file", "not_found", "name_not_in_line", "privacy"
)


def _is_code_like_name(pattern: str) -> bool:
    """True for one literal name that looks like code (4 characters and 4 letters or more)."""
    if len(pattern) < MIN_PATTERN_LENGTH or not _LITERAL_RE.match(pattern):
        return False
    if sum(c.isascii() and c.isalpha() for c in pattern) < MIN_PATTERN_LETTERS:
        return False
    if _SEPARATED_RE.search(pattern) or _CAMEL_RE.search(pattern):
        return True
    return bool(_UPPER_RE.match(pattern))


def _pattern_names(pattern: str) -> list[str] | None:
    """The literal names of a pattern split at "|", or None when a part is not a code-like name."""
    names = pattern.split("|")
    return names if all(_is_code_like_name(name) for name in names) else None


def _story_tokens(story_text: str) -> set[str]:
    """Tokens of the story: runs of [A-Za-z0-9_.:/-] without . : / - at the start and the end."""
    return {token.strip(_TOKEN_EDGE) for token in _TOKEN_RE.findall(story_text)}


def _story_holds_name(story_text: str, name: str) -> bool:
    """True when a token of the story is exactly the name (not a part of a longer name)."""
    return name in _story_tokens(story_text)


def _is_test_folder(folder: str) -> bool:
    low = folder.lower()
    return low in _TEST_FOLDERS or low.startswith(_TEST_FOLDER_STARTS) or low.endswith(_TEST_FOLDER_ENDS)


def _is_test_file_name(stem: str) -> bool:
    """The file name without its extension looks like a test file."""
    if stem.endswith(_TEST_SUFFIXES_CASED) or stem.endswith(_TEST_SUFFIXES_LOWER):
        return True
    if len(stem) > 2 and stem.endswith("IT") and "a" <= stem[-3] <= "z":
        return True
    return stem.startswith("test_") or stem.lower() in ("test", "spec")


def _is_test_path(path: str) -> bool:
    """True for a test file by the folder names or the file name."""
    parts = path.split("/")
    if any(_is_test_folder(part) for part in parts[:-1]):
        return True
    return _is_test_file_name(Path(parts[-1]).stem)


def _repo_dir(project: str) -> Path:
    return REPOS_ROOT / REPO_DIR_OVERRIDE.get(project, project)


def _read_repo_lines(project: str, rel_path: str) -> list[str] | None:
    """Lines of a file inside the repository of the product, or None when it cannot be used."""
    if not rel_path or rel_path.startswith(("/", "\\")) or Path(rel_path).is_absolute():
        return None
    root = _repo_dir(project).resolve()
    try:
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        text = target.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _stored_hit_line(passage_text: str) -> str | None:
    """The code line of a stored hit (the text of its fenced block)."""
    lines = passage_text.rstrip("\n").split("\n")
    for i, line in enumerate(lines):
        if line.startswith("File: "):
            body = lines[i + 1 :]
            if len(body) >= 3 and body[0] == "```" and body[-1] == "```":
                return "\n".join(body[1:-1])
            return None
    return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _cut(text: str) -> str:
    """Cut a text after 240 characters and end it with " ..." so that the reader sees the cut."""
    return text[:MAX_SHOWN_LINE] + CUT_MARK if len(text) > MAX_SHOWN_LINE else text


def _is_comment(line: str) -> bool:
    return line.strip().startswith(("#", "//", "*", "/*"))


def _opening_line(lines: list[str], first: int, hit: int) -> tuple[int, str] | None:
    """The block line that opens the hit line, above line first: (number, text) or None.

    Walk up the levels of indentation (400 lines at most): the nearest line above with less
    indentation, not blank and not a comment. If it starts with a keyword it is the opening
    line. If not, its indentation is the new level and the walk goes on from it.
    """
    level = _indent(lines[hit - 1])
    position = hit
    budget = OPENING_SEARCH_LINES
    while budget > 0:
        found = 0
        for number in range(position - 1, 0, -1):
            if budget == 0:
                return None
            budget -= 1
            line = lines[number - 1]
            if line.strip() and not _is_comment(line) and _indent(line) < level:
                found = number
                break
        if not found:
            return None
        line = lines[found - 1]
        if _OPENING_RE.match(line):
            return (found, _cut(line.strip())) if found < first else None
        level, position = _indent(line), found
    return None


def _shown(line: str) -> str:
    return _cut(line.rstrip("\r"))


def _neighbour_lines(lines: list[str], hit: int) -> list[str]:
    """The new lines of a kept hit: a note, an optional opening line and a fenced block."""
    first = max(1, hit - NEIGHBOUR_LINES)
    last = min(len(lines), hit + NEIGHBOUR_LINES)
    out = [f"Source code, not documentation. Lines {first} to {last} of the file. The search found line {hit}."]
    opening = _opening_line(lines, first, hit)
    if opening is not None:
        out.append(f"Line {opening[0]} starts the enclosing block: {opening[1]}")
    out.append("```")
    out.extend(f"{n}: {_shown(lines[n - 1])}" for n in range(first, last + 1))
    out.append("```")
    return out


def _classify_code_hit(passage, context: PassageContext) -> tuple[str | None, str | None]:
    """Return (reason, None) for a dropped hit or (None, new text) for a kept hit."""
    names = _pattern_names(passage.code_pattern or "")
    if names is None:
        return "not_literal", None
    held = [name for name in names if _story_holds_name(context.story_text, name)]
    if not held:
        return "not_in_story", None
    location = passage.code_location or ""
    path, _, number_text = location.rpartition(":")
    if _is_test_path(path):
        return "test_file", None
    lines = _read_repo_lines(context.project, path)
    if lines is None or not number_text.isdigit() or not 1 <= int(number_text) <= len(lines):
        return "not_found", None
    hit = int(number_text)
    stored = _stored_hit_line(passage.text)
    if stored != lines[hit - 1].strip():
        return "not_found", None
    if not any(name in stored for name in held):
        return "name_not_in_line", None
    head = passage.text.split("\n")
    head = head[: next(i for i, ln in enumerate(head) if ln.startswith("File: ")) + 1]
    added = "\n".join(_neighbour_lines(lines, hit))
    try:
        assert_no_private_text(added, what="a code hit")
    except GuardError:
        return "privacy", None
    return None, "\n".join(head) + "\n" + added


def classify_code_hits(block: str, context: PassageContext) -> list[tuple[str | None, str | None]]:
    """For each code hit of the block, in order: (drop reason, None) or (None, new text)."""
    return [_classify_code_hit(p, context) for p in split_context(block) if p.kind == "code"]


def _story_name_hits(block: str, context: PassageContext | None = None) -> str:
    """Keep the documentation, keep a code hit only for a name of the story, show its neighbour lines."""
    if context is None:
        raise PromptError("story_name_hits needs a PassageContext (the product and the story text)")
    kept: list[str] = []
    n_hits_kept = 0
    for passage in split_context(block):
        if passage.kind == "doc":
            kept.append(passage.text)
            continue
        reason, new_text = _classify_code_hit(passage, context)
        if reason is None and new_text is not None:
            kept.append(new_text)
            n_hits_kept += 1
    if n_hits_kept == 0:
        return _drop_code_hits(block)
    parts: list[str] = []
    for number, text in enumerate(kept, start=1):
        text, n = _HEADER_NUMBER_RE.subn(f"### [{number}] ", text, count=1)
        if n != 1:
            raise PromptError("story_name_hits: a passage has no numbered header line")
        parts.append(text)
    return SEPARATOR.join(parts)


# --- render_markup: template markup of the Keycloak documentation -------------------------
# The Keycloak documentation was stored as template source (FreeMarker macros and AsciiDoc
# attributes) that went through a Markdown converter. This change writes the plain form that a
# reader of the rendered page sees. It rewrites only what has a known meaning. Sources:
#  - kc.CMD macros: docs/guides/templates/kc.adoc of the Keycloak repository (each macro prints
#    "bin/kc.[sh|bat] <command> ${parameters}"; "bootstrapadmin" prints "bootstrap-admin").
#  - links.server: docs/guides/templates/links.adoc (prints the title of the guide with that id)
#    and the title attributes of docs/guides/server/<id>.adoc.
#  - {project_name}: ":project_name: Keycloak" in docs/guides/attributes.adoc.
#  - ${r"TEXT"}: FreeMarker raw string; the project labelling rules say it means TEXT.
#  - Markdown escapes: a backslash before one of the characters in _MD_ESCAPABLE.
#  - opts.printRelevantOptions: docs/guides/templates/options.adoc. On the published page the call
#    prints a table of options built at build time. The change writes one plain line with the
#    names of includedOptions that are not in excludedOptions (a name with * is a pattern and
#    stays as listed). The names of excludedOptions and deniedCategories are not shown, because
#    the published table does not show them. Single and double quotes are both accepted. The
#    nested text is not touched. The closing tag line is dropped.
# Macros without a known meaning stay as they are.
# The rules belong to this documentation set, so they act only for the product named below.

MARKUP_PROJECT = "keycloak"
OPTIONS_NO_NAME_LINE = "(The published page shows a table of the relevant options here.)"

_KC_COMMANDS: Mapping[str, str] = MappingProxyType(
    {
        "start": "bin/kc.sh start",
        "startdev": "bin/kc.sh start-dev",
        "build": "bin/kc.sh build",
        "export": "bin/kc.sh export",
        "import": "bin/kc.sh import",
        "bootstrapadmin": "bin/kc.sh bootstrap-admin",
        "updatecompatibility": "bin/kc.sh update-compatibility",
        "admin": "bin/kcadm.sh",
    }
)
# Titles of the server guides: the title attribute of docs/guides/server/<id>.adoc of the Keycloak
# repository, with {project_name} written as Keycloak (the guide "index" has no title).
_GUIDE_TITLES: Mapping[str, str] = MappingProxyType(
    {
        "vault": "Using a vault",
        "configuration": "Configuring Keycloak",
        "configuration-production": "Configuring Keycloak for production",
        "all-config": "All configuration",
        "all-provider-config": "All provider configuration",
        "bootstrap-admin-recovery": "Bootstrapping and recovering an admin account",
        "caching": "Configuring distributed caches",
        "configuration-provider": "Configuring providers",
        "containers": "Running Keycloak in a container",
        "db": "Configuring the database",
        "directory-structure": "Directory Structure",
        "enabletls": "Configuring TLS",
        "features": "Enabling and disabling features",
        "fips": "FIPS 140-2 support",
        "hostname": "Configuring the hostname (v2)",
        "importExport": "Importing and exporting realms",
        "keycloak-truststore": "Configuring trusted certificates",
        "logging": "Configuring logging",
        "management-interface": "Configuring the Management Interface",
        "mutual-tls": "Configuring trusted certificates for mTLS",
        "outgoinghttp": "Configuring outgoing HTTP requests",
        "reverseproxy": "Configuring a reverse proxy",
        "update-compatibility": "Checking if rolling updates are possible",
        "windows-service": "Run Keycloak as a Windows Service",
    }
)
_MD_ESCAPABLE = "<>*_[]#"
_MD_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
_MANGLED_LINK_RE = re.compile(r'\[(https?://[^\]\s"]*)"/\]\(\1"/\)')
_BLOCK = r"(?:(?!\n[ \t]*\n).)*?"
_KC_MACRO_RE = re.compile(
    r"\\?<@kc\.(\w+)(" + _BLOCK + r")\s*/\\?>", re.DOTALL
)
_LINKS_MACRO_RE = re.compile(
    r"\\?<@links\.server(" + _BLOCK + r")\s*/\\?>", re.DOTALL
)
_OPTS_MACRO_RE = re.compile(
    r"\\?<@opts\.(?:printRelevantOptions|includeOptions)"
    r"((?:\s+\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>\\/]+))*)\s*(/?)\\?>"
)
# White space with one line break at most (a converter wraps long lines).
_WS1 = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
# A run of FreeMarker imports, also when the converter wrapped it over several lines. An import prints nothing.
_IMPORT_RUN_RE = re.compile(
    r"(?:\\?<#import" + _WS1 + r'"[^"\n]*"' + _WS1 + r"as" + _WS1 + r"\w+[ \t]*\\?>(?:" + _WS1 + r")?)+"
)
# docs/guides/templates/profile.adoc: ifProduct prints its text only in the product build, ifCommunity
# only in the community build. The stored documentation is the community one.
_IF_PRODUCT_RE = re.compile(r"\\?<@profile\.ifProduct\\?>.*?\\?</@profile\.ifProduct\\?>[ \t]*", re.DOTALL)
_IF_COMMUNITY_RE = re.compile(
    r"\\?<@profile\.ifCommunity\\?>[ \t]*\n?(.*?)[ \t]*\n?[ \t]*\\?</@profile\.ifCommunity\\?>", re.DOTALL
)
_RAW_STRING_RE = re.compile(r'\$\{r"([^"\n]*)"\}')
_ATTR_RE = re.compile(r'\s*(\w+)="([^"]*)"')
_AUTOLINK_RE = re.compile(r"<(https?://[^<>\s]+)>")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*[^`]*$")
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL)
_DROP_LINE_RES = (
    re.compile(r"^[ \t]*\\?<#(?:import|list)\b.*\\?>[ \t]*$"),
    re.compile(r"^[ \t]*\\?</#list\\?>[ \t]*$"),
    re.compile(r"^[ \t]*\\?</@tmpl\.guide\\?>[ \t]*$"),
    re.compile(r"^[ \t]*\\?</@opts\.printRelevantOptions\\?>[ \t]*$"),
    # An include of another template file: the passage cannot show that file, so the line says nothing.
    re.compile(r'^[ \t]*\\?<#include\s+"[^"\n]*"\s*/?\\?>[ \t]*$'),
    re.compile(r"^[ \t]*\\?</@opts\.includeOptions\\?>[ \t]*$"),
)
_WRAPPER_OPEN_RE = re.compile(r"^[ \t]*\\?<@tmpl\.guide\b")
_WRAPPER_END_RE = re.compile(r"\\?>[ \t]*$")
_WRAPPER_MAX_LINES = 15


def _md_unescape(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        char = m.group(1)
        return char if char in _MD_ESCAPABLE else m.group(0)

    return _MD_ESCAPE_RE.sub(one, text)


def _parse_attrs(text: str) -> dict[str, str] | None:
    """Parse name="value" pairs. Return None when anything else is in the text."""
    attrs: dict[str, str] = {}
    pos = 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        m = _ATTR_RE.match(text, pos)
        if m is None or m.group(1) in attrs:
            return None
        attrs[m.group(1)] = m.group(2)
        pos = m.end()
    return attrs


def _plain_parameters(value: str) -> str:
    value = _AUTOLINK_RE.sub(r"\1", _md_unescape(value))
    return " ".join(value.split())


def _render_kc(m: re.Match[str]) -> str:
    command = _KC_COMMANDS.get(m.group(1))
    attrs = _parse_attrs(m.group(2))
    if command is None or attrs is None or not set(attrs) <= {"parameters", "rootParameters"}:
        return m.group(0)
    root = _plain_parameters(attrs.get("rootParameters", ""))
    params = _plain_parameters(attrs.get("parameters", ""))
    head, _, tail = command.partition(" ")
    parts = [head] + ([root] if root else []) + ([tail] if tail else []) + ([params] if params else [])
    return " ".join(parts)


def _option_names(attrs: str, key: str) -> list[str]:
    found = re.search(rf"""\b{key}\s*=\s*(?:"([^"]*)"|'([^']*)')""", attrs)
    if not found:
        return []
    return _md_unescape(found.group(1) if found.group(1) is not None else found.group(2)).split()


def _render_options(m: re.Match[str]) -> str:
    """One plain line for a printRelevantOptions call: the included names that are not excluded."""
    excluded = set(_option_names(m.group(1), "excludedOptions"))
    shown = [name for name in _option_names(m.group(1), "includedOptions") if name not in excluded]
    return f"Relevant options: {', '.join(shown)}" if shown else OPTIONS_NO_NAME_LINE


def _render_links(m: re.Match[str]) -> str:
    attrs = _parse_attrs(m.group(1))
    if attrs is None or not set(attrs) <= {"id", "anchor"} or attrs.get("id") not in _GUIDE_TITLES:
        return m.group(0)
    return _GUIDE_TITLES[attrs["id"]]


def _drop_markup_lines(text: str) -> str:
    """Drop the lines that hold only an import, a list tag or a wrapper macro tag."""
    lines = text.split("\n")
    out: list[str] = []
    in_fence: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if in_fence is not None:
            if fence and fence.group(1)[0] == in_fence[0] and len(fence.group(1)) >= len(in_fence):
                if not line.strip().strip(fence.group(1)[0]):
                    in_fence = None
            out.append(line)
            i += 1
            continue
        if fence:
            in_fence = fence.group(1)
            out.append(line)
            i += 1
            continue
        end = None
        if any(rx.match(line) for rx in _DROP_LINE_RES):
            end = i
        elif _WRAPPER_OPEN_RE.match(line):
            for j in range(i, min(i + _WRAPPER_MAX_LINES, len(lines))):
                if _WRAPPER_END_RE.search(lines[j]):
                    end = j
                    break
        if end is None:
            out.append(line)
            i += 1
            continue
        i = end + 1
        # Do not leave two blank lines, or a blank line at the end, where a line was removed.
        before_blank = bool(out) and not out[-1].strip()
        after_blank = i >= len(lines) or not lines[i].strip()
        if before_blank and after_blank and i < len(lines):
            i += 1
    return "\n".join(out)


def _code_segments(text: str) -> list[tuple[str, str]]:
    """Split text into ("fence" | "span" | "text", piece) parts. The parts join to the text."""
    parts: list[tuple[str, str]] = []
    block: list[str] = []
    in_fence: str | None = None
    kind = "text"

    def flush() -> None:
        if block:
            parts.append((kind, "".join(block)))
            block.clear()

    lines = text.split("\n")
    for number, line in enumerate(lines):
        piece = line + ("\n" if number < len(lines) - 1 else "")
        fence = _FENCE_RE.match(line)
        if in_fence is None:
            if fence:
                flush()
                kind, in_fence = "fence", fence.group(1)
            block.append(piece)
        else:
            block.append(piece)
            if (
                fence
                and fence.group(1)[0] == in_fence[0]
                and len(fence.group(1)) >= len(in_fence)
                and not line.strip().strip(fence.group(1)[0])
            ):
                flush()
                kind, in_fence = "text", None
    flush()
    out: list[tuple[str, str]] = []
    for kind_, piece in parts:
        if kind_ == "fence":
            out.append((kind_, piece))
            continue
        pos = 0
        for m in _INLINE_CODE_RE.finditer(piece):
            if m.start() > pos:
                out.append(("text", piece[pos : m.start()]))
            out.append(("span", m.group(0)))
            pos = m.end()
        if pos < len(piece):
            out.append(("text", piece[pos:]))
    return out


def _render_markup_body(body: str) -> str:
    body = _MANGLED_LINK_RE.sub(r'\1"/', body)
    body = _IMPORT_RUN_RE.sub("", body)
    body = _IF_PRODUCT_RE.sub("", body)
    body = _IF_COMMUNITY_RE.sub(lambda m: m.group(1), body)
    body = _KC_MACRO_RE.sub(_render_kc, body)
    body = _LINKS_MACRO_RE.sub(_render_links, body)
    body = _OPTS_MACRO_RE.sub(_render_options, body)
    body = _RAW_STRING_RE.sub(lambda m: m.group(1), body)
    body = _drop_markup_lines(body)
    out: list[str] = []
    for kind, piece in _code_segments(body):
        if kind != "fence":
            piece = piece.replace("{project_name}", "Keycloak")
        if kind == "text":
            piece = _md_unescape(piece)
        out.append(piece)
    return "".join(out)


def _render_markup(block: str, context: PassageContext | None = None) -> str:
    """Write the template markup of the documentation passages in the plain form of the page.

    Headers, code-search hits and the separators stay as they are. See the notes above.
    The rules belong to the Keycloak documentation: with a context for another product the
    block is returned unchanged. Without a context the rules apply (as before).
    """
    if context is not None and context.project != MARKUP_PROJECT:
        return block
    try:
        passages = split_context(block)
    except PassagesError as exc:
        raise PromptError(f"render_markup: the block is not a passage block: {exc}") from exc
    parts: list[str] = []
    for passage in passages:
        if passage.kind != "doc":
            parts.append(passage.text)
            continue
        header, newline, body = passage.text.partition("\n")
        header = header.replace("{project_name}", "Keycloak")
        parts.append(header + newline + _render_markup_body(body))
    return SEPARATOR.join(parts)


# --- drop_empty: a documentation passage with no text ------------------------------------
# A page can start with template lines only. After render_markup such a first part is empty.
# This change removes a documentation passage that holds only its header line.


def _drop_empty(block: str, context: PassageContext | None = None) -> str:
    try:
        passages = split_context(block)
    except PassagesError as exc:
        raise PromptError(f"drop_empty: the block is not a passage block: {exc}") from exc
    kept = [p.text for p in passages if p.kind != "doc" or p.text.partition("\n")[2].strip()]
    if len(kept) == len(passages):
        return block
    parts: list[str] = []
    for number, text in enumerate(kept, start=1):
        text, n = _HEADER_NUMBER_RE.subn(f"### [{number}] ", text, count=1)
        if n != 1:
            raise PromptError("drop_empty: a passage has no numbered header line")
        parts.append(text)
    return SEPARATOR.join(parts)


# --- search_<option>: documentation passages of a new search step -------------------------
# The stored block of Study 2b holds the 5 best sections of the present search. This change puts
# the passages of another search option in their place (scripts/tuning_lib/search.py; the passages
# are made before the run by scripts/search_freeze.py and stored below tuning/_search/). The
# code-search hits of the stored block stay, after the documentation. The prompt text does not
# change, so a version with this change tests the search alone.


def _use_search(option: str) -> PassageChange:
    def change(block: str, context: PassageContext | None = None) -> str:
        if context is None:
            raise PromptError(f"search_{option} needs a PassageContext (the product and the story text)")
        try:
            entry = _search.frozen_entry(option, context.project, context.story_text)
            new_docs = split_context(entry["doc_block"])
            old = split_context(block)
        except (_search.SearchError, PassagesError) as exc:
            raise PromptError(f"search_{option}: {exc}") from exc
        if any(p.kind != "doc" for p in new_docs):
            raise PromptError(f"search_{option}: the stored search holds a passage that is not documentation")
        texts = [p.text for p in new_docs] + [p.text for p in old if p.kind == "code"]
        parts: list[str] = []
        for number, text in enumerate(texts, start=1):
            text, n = _HEADER_NUMBER_RE.subn(f"### [{number}] ", text, count=1)
            if n != 1:
                raise PromptError(f"search_{option}: a passage has no numbered header line")
            parts.append(text)
        return SEPARATOR.join(parts)

    return change


PASSAGE_CHANGES: dict[str, PassageChange] = {
    "identity": _identity,
    "drop_code_hits": _drop_code_hits,
    "render_markup": _render_markup,
    "story_name_hits": _story_name_hits,
    "drop_empty": _drop_empty,
    **{f"search_{name}": _use_search(name) for name in _search.OPTIONS},
}


def apply_passage_changes(
    block: str, names: Sequence[str], context: PassageContext | None = None
) -> str:
    """Apply the named passage changes in order. Check that documentation is left at the end.

    The context goes to each change. A change that needs it raises PromptError when it is None.
    """
    for name in names:
        if name not in PASSAGE_CHANGES:
            raise PromptError(
                f"unknown passage change {name!r}; known: {sorted(PASSAGE_CHANGES)}"
            )
    result = block
    for name in names:
        result = PASSAGE_CHANGES[name](result, context)
    if not isinstance(result, str) or not result.strip() or result == FALLBACK_CONTEXT:
        raise NoDocumentationAfterChange("the block is empty or the fallback text after the changes")
    try:
        passages = split_context(result)
    except PassagesError as exc:
        raise PromptError(f"the block is broken after the passage changes: {exc}") from exc
    if not doc_passages(passages):
        raise NoDocumentationAfterChange("no documentation passage is left after the changes")
    return result


# ---------------------------------------------------------------------------
# Version folders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionInfo:
    """A loaded and checked version folder."""

    name: str
    parent: str | None
    description: str
    passage_changes: tuple[str, ...]
    folder: Path
    template_sha256: Mapping[str, str]
    version_json_sha256: str


def _read_version_json(folder: Path) -> tuple[dict, bytes]:
    """Read and check version.json of a folder (keys, types, name, description)."""
    path = folder / VERSION_FILE
    if not path.is_file():
        raise PromptError(f"{folder.name}: {VERSION_FILE} not found")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PromptError(f"{folder.name}/{VERSION_FILE}: not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptError(f"{folder.name}/{VERSION_FILE}: not a JSON object")
    keys = set(data)
    if keys != set(_VERSION_KEYS):
        missing = sorted(set(_VERSION_KEYS) - keys)
        extra = sorted(keys - set(_VERSION_KEYS))
        raise PromptError(
            f"{folder.name}/{VERSION_FILE}: wrong keys (missing {missing}, extra {extra})"
        )
    name = data["name"]
    if not isinstance(name, str) or not VERSION_NAME_RE.match(name) or name != folder.name:
        raise PromptError(
            f"{folder.name}/{VERSION_FILE}: key 'name' must equal the folder name and match "
            f"{VERSION_NAME_RE.pattern}"
        )
    parent = data["parent"]
    if name == BASELINE:
        if parent is not None:
            raise PromptError(f"{name}/{VERSION_FILE}: key 'parent' must be null for {BASELINE}")
    elif not isinstance(parent, str) or not VERSION_NAME_RE.match(parent):
        raise PromptError(f"{name}/{VERSION_FILE}: key 'parent' must be a version name")
    _check_description(data["description"], f"{name}/{VERSION_FILE}: key 'description'")
    changes = data["passage_changes"]
    if not isinstance(changes, list) or not all(isinstance(c, str) for c in changes):
        raise PromptError(f"{name}/{VERSION_FILE}: key 'passage_changes' must be a list of strings")
    for change in changes:
        if change not in PASSAGE_CHANGES:
            raise PromptError(
                f"{name}/{VERSION_FILE}: unknown passage change {change!r}; "
                f"known: {sorted(PASSAGE_CHANGES)}"
            )
    return data, raw


def _check_description(description: object, where: str) -> None:
    if not isinstance(description, str):
        raise PromptError(f"{where} must be a string")
    if not description.strip():
        raise PromptError(f"{where} must not be empty")
    if len(description) > MAX_DESCRIPTION:
        raise PromptError(f"{where} is longer than {MAX_DESCRIPTION} characters")
    if "\n" in description or "\r" in description:
        raise PromptError(f"{where} must be one line")


def _check_template(folder_name: str, file_name: str, text: str) -> None:
    where = f"{folder_name}/{file_name}"
    if "\r" in text:
        raise PromptError(f"{where}: holds a carriage return")
    if file_name == "system_prompt.txt":
        return
    matches = list(_DOLLAR_RE.finditer(text))
    if len(matches) != text.count("$"):
        raise PromptError(
            f"{where}: a '$' is not part of a placeholder such as $name or ${{name}}"
        )
    counts: dict[str, int] = {}
    for m in matches:
        name = m.group(1) or m.group(2)
        if name not in KNOWN_PLACEHOLDERS:
            raise PromptError(f"{where}: unknown placeholder {name!r}")
        counts[name] = counts.get(name, 0) + 1
    if file_name == "rag_user_prompt.txt":
        if counts.get("retrieved_context", 0) != 1:
            raise PromptError(
                f"{where}: retrieved_context must occur exactly one time "
                f"(found {counts.get('retrieved_context', 0)})"
            )
    elif counts.get("retrieved_context", 0) != 0:
        raise PromptError(f"{where}: retrieved_context must not occur")
    for name in KNOWN_PLACEHOLDERS:
        if name != "retrieved_context" and counts.get(name, 0) < 1:
            raise PromptError(f"{where}: placeholder {name!r} is missing")


def list_versions(root: Path = VERSIONS_ROOT) -> list[str]:
    """Sorted names of the folders that hold a version.json."""
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / VERSION_FILE).is_file())


def load_version(name: str, root: Path = VERSIONS_ROOT) -> VersionInfo:
    """Load and check one version folder. Raise PromptError on any problem."""
    if not isinstance(name, str) or not VERSION_NAME_RE.match(name):
        raise PromptError(f"bad version name {name!r}; rule: {VERSION_NAME_RE.pattern}")
    folder = root / name
    if not folder.is_dir():
        raise PromptError(f"version {name!r}: folder not found")
    expected = set(TEMPLATE_FILES) | {VERSION_FILE}
    present = {p.name for p in folder.iterdir()}
    if present != expected:
        raise PromptError(
            f"version {name!r}: wrong files (missing {sorted(expected - present)}, "
            f"extra {sorted(present - expected)})"
        )
    data, raw = _read_version_json(folder)
    hashes: dict[str, str] = {}
    for file_name in TEMPLATE_FILES:
        data_bytes = (folder / file_name).read_bytes()
        try:
            text = data_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PromptError(f"{name}/{file_name}: not UTF-8") from exc
        _check_template(name, file_name, text)
        hashes[file_name] = _sha_bytes(data_bytes)
    # The parent chain: every parent exists, and there is no loop.
    seen = {name}
    current = data["parent"]
    depth = 0
    while current is not None:
        depth += 1
        if depth > MAX_PARENT_DEPTH:
            raise PromptError(f"version {name!r}: the parent chain is longer than {MAX_PARENT_DEPTH}")
        if current in seen:
            raise PromptError(f"version {name!r}: the parent chain has a loop at {current!r}")
        seen.add(current)
        parent_folder = root / current
        if not parent_folder.is_dir():
            raise PromptError(f"version {name!r}: parent {current!r} does not exist")
        parent_data, _ = _read_version_json(parent_folder)
        current = parent_data["parent"]
    return VersionInfo(
        name=name,
        parent=data["parent"],
        description=data["description"],
        passage_changes=tuple(data["passage_changes"]),
        folder=folder,
        template_sha256=MappingProxyType(hashes),
        version_json_sha256=_sha_bytes(raw),
    )


def new_version(
    name: str,
    parent: str,
    description: str,
    *,
    root: Path = VERSIONS_ROOT,
    guard: WriteGuard = VERSIONS_GUARD,
) -> Path:
    """Create a version folder as a copy of the parent. This is the only function that writes."""
    if not isinstance(name, str) or not VERSION_NAME_RE.match(name):
        raise PromptError(f"bad version name {name!r}; rule: {VERSION_NAME_RE.pattern}")
    if name == BASELINE:
        raise PromptError(f"the name {BASELINE!r} is reserved")
    _check_description(description, "description")
    folder = root / name
    if folder.exists():
        raise PromptError(f"version {name!r} exists already")
    parent_info = load_version(parent, root)
    texts = {
        file_name: (parent_info.folder / file_name).read_bytes().decode("utf-8")
        for file_name in TEMPLATE_FILES
    }
    meta = {
        "name": name,
        "parent": parent,
        "description": description,
        "passage_changes": list(parent_info.passage_changes),
    }
    guarded_mkdir(folder, guard=guard)
    for file_name in TEMPLATE_FILES:
        guarded_write_text(folder / file_name, texts[file_name], guard=guard)
    guarded_write_text(
        folder / VERSION_FILE, json.dumps(meta, ensure_ascii=False, indent=1) + "\n", guard=guard
    )
    return folder


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Story:
    """A user story of Study 2."""

    story_id: str
    project: str
    title: str
    story: str
    acceptance_criteria: tuple[str, ...]
    identifiers: tuple[str, ...]

    def prompt_text(self) -> str:
        """Title, story sentence and acceptance criteria, as the prompt shows them."""
        criteria = "\n".join(f"- {ac}" for ac in self.acceptance_criteria)
        return "\n".join(part for part in (self.title, self.story, criteria) if part)


def load_stories(
    projects: Sequence[str] = PROJECTS, study2_root: Path = STUDY2
) -> dict[str, Story]:
    """Read the stories of the projects (read only). A bad file raises PromptError."""
    out: dict[str, Story] = {}
    for project in projects:
        path = study2_root / project / "stories.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data["stories"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PromptError(f"cannot read stories of {project}: {type(exc).__name__}") from exc
        for item in items:
            try:
                sid = item["id"]
                ids = item["fact_sheet"]["identifiers"]
                story = Story(
                    story_id=sid,
                    project=project,
                    title=item["title"],
                    story=item["story"],
                    acceptance_criteria=tuple(item["acceptance_criteria"]),
                    identifiers=tuple(ids),
                )
            except (KeyError, TypeError) as exc:
                raise PromptError(f"a story of {project} has a missing key: {exc!r}") from exc
            if sid in out:
                raise PromptError(f"duplicate story id {sid}")
            if not ids or not all(isinstance(i, str) for i in ids):
                raise PromptError(f"story {sid}: identifiers must be a non-empty list of strings")
            out[sid] = story
    return out


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltPrompt:
    """The full prompt of one generation and the facts about its passage block."""

    story_id: str
    project: str
    model: str
    version: str
    system: str
    user: str
    user_non_doc: str
    block_original: str
    block: str
    block_sha256: str
    n_doc_passages: int
    n_code_hits: int
    info: VersionInfo


def _first_difference(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


class PromptBuilder:
    """Builds prompts from version folders, the stories and the stored passages."""

    def __init__(
        self,
        versions_root: Path = VERSIONS_ROOT,
        study2_root: Path = STUDY2,
        rtc_root: Path = RTC,
    ) -> None:
        self.versions_root = versions_root
        self.study2_root = study2_root
        self.rtc_root = rtc_root
        self._stories: dict[str, Story] | None = None
        self._versions: dict[str, VersionInfo] = {}
        self._blocks: dict[tuple[str, str], str] = {}
        self._contexts: dict[str, str] = {}

    def stories(self) -> dict[str, Story]:
        """All stories, loaded once."""
        if self._stories is None:
            self._stories = load_stories(study2_root=self.study2_root)
        return self._stories

    def story(self, story_id: str) -> Story:
        """One story. An unknown id raises PromptError."""
        stories = self.stories()
        if story_id not in stories:
            raise PromptError(f"unknown story id {story_id!r}")
        return stories[story_id]

    def system_context(self, project: str) -> str:
        """The text of system_context.yaml of a project. A missing file raises PromptError."""
        if project not in self._contexts:
            path = self.rtc_root / project / "system_context.yaml"
            try:
                self._contexts[project] = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PromptError(f"system context of {project!r} not readable: {exc}") from exc
        return self._contexts[project]

    def version(self, name: str) -> VersionInfo:
        """A loaded version, kept in the object."""
        if name not in self._versions:
            self._versions[name] = load_version(name, self.versions_root)
        return self._versions[name]

    def stored_block(self, story_id: str, model: str) -> str:
        """The stored passage block of Study 2b for a story and a model."""
        if model not in MODELS:
            raise PromptError(f"unknown model {model!r}; known: {list(MODELS)}")
        key = (story_id, model)
        if key not in self._blocks:
            project = self.story(story_id).project
            self._blocks[key] = read_study2b_context(project, model, story_id).text
        return self._blocks[key]

    def build(self, story_id: str, model: str, version: str) -> BuiltPrompt:
        """Build the system prompt and the user prompt of one generation."""
        info = self.version(version)
        story = self.story(story_id)
        block_original = self.stored_block(story_id, model)
        passage_context = PassageContext(project=story.project, story_text=story.prompt_text())
        block = apply_passage_changes(block_original, info.passage_changes, passage_context)
        pb = SecurityRequirementsPromptBuilder(prompts_dir=info.folder)
        system = pb.build_system_prompt()
        user_story = UserStory(
            id=story.story_id,
            title=story.title,
            story=story.story,
            acceptance_criteria=list(story.acceptance_criteria),
        )
        context = self.system_context(story.project)
        user = pb.build_rag_prompt(
            user_story=user_story, retrieved_context=block, system_context=context
        )
        where = f"{story_id}/{model}/{version}"
        if user.count(block) != 1:
            raise PromptError(f"{where}: the passage block is not in the prompt exactly one time")
        user_non_doc = user.replace(block, "", 1)
        empty = pb.build_rag_prompt(
            user_story=user_story, retrieved_context="", system_context=context
        )
        if user_non_doc != empty:
            raise PromptError(
                f"{where}: removing the block does not give the prompt with an empty block"
            )
        passages = split_context(block)
        n_doc = len(doc_passages(passages))
        return BuiltPrompt(
            story_id=story_id,
            project=story.project,
            model=model,
            version=version,
            system=system,
            user=user,
            user_non_doc=user_non_doc,
            block_original=block_original,
            block=block,
            block_sha256=_sha_text(block),
            n_doc_passages=n_doc,
            n_code_hits=len(passages) - n_doc,
            info=info,
        )

    def verify_v0_old(self, story_id: str, model: str) -> None:
        """Check that v0_old gives the stored B2 prompt. Raise PromptMismatch if not."""
        built = self.build(story_id, model, BASELINE)
        project = self.story(story_id).project
        stored_list = read_study2b_prompts(
            projects=(project,), models=(model,), arms=("B2",), run=1, stories={story_id}
        )
        if len(stored_list) != 1:
            raise PromptMismatch(f"{story_id}/{model}: expected one stored B2 prompt")
        stored = stored_list[0]
        pairs = (
            ("system", built.system, stored.system),
            ("user", built.user, stored.user_sent or ""),
            ("user_non_doc", built.user_non_doc, stored.user_non_doc),
            ("block_sha256", built.block_sha256, stored.doc_context_sha256 or ""),
        )
        problems = []
        for label, mine, theirs in pairs:
            if mine != theirs:
                problems.append(
                    f"{label} differs (first difference at index "
                    f"{_first_difference(mine, theirs)}, lengths {len(mine)} and {len(theirs)})"
                )
        if problems:
            raise PromptMismatch(f"{story_id}/{model}: " + "; ".join(problems))


_DEFAULT_BUILDER: PromptBuilder | None = None


def build_prompt(story_id: str, model: str, version: str) -> BuiltPrompt:
    """Build a prompt with the default builder (made at the first call)."""
    global _DEFAULT_BUILDER
    if _DEFAULT_BUILDER is None:
        _DEFAULT_BUILDER = PromptBuilder()
    return _DEFAULT_BUILDER.build(story_id, model, version)


# ---------------------------------------------------------------------------
# The generality check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneralityHit:
    """A product word, identifier, story id or story phrase in a template file."""

    kind: str
    term: str
    file: str
    line: int
    count: int
    baseline_count: int

    @property
    def inherited(self) -> bool:
        """True when v0_old holds the term at least as often in the same file."""
        return self.count <= self.baseline_count


@dataclass(frozen=True)
class GeneralityReport:
    """All hits of one version."""

    version: str
    hits: tuple[GeneralityHit, ...]

    @property
    def new_hits(self) -> tuple[GeneralityHit, ...]:
        """Hits that are not inherited from v0_old."""
        return tuple(h for h in self.hits if not h.inherited)

    @property
    def inherited_hits(self) -> tuple[GeneralityHit, ...]:
        """Hits that v0_old holds already."""
        return tuple(h for h in self.hits if h.inherited)

    @property
    def ok(self) -> bool:
        """True when there is no new hit."""
        return not self.new_hits


def _whole_rx(form: str) -> re.Pattern[str]:
    """A pattern for `form` as a whole name (same rule as whole_rx of study2_names.py)."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(form) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])")


def _words(text: str) -> list[tuple[str, int]]:
    return [(m.group(0).casefold(), m.start()) for m in _WORD_RE.finditer(text)]


class _Scanner:
    """Finds the four kinds of hits in a text. Built from the stories only."""

    def __init__(self, stories: Mapping[str, Story]) -> None:
        self.identifier_rx = {
            ident: _whole_rx(ident) for ident in sorted({i for s in stories.values() for i in s.identifiers})
        }
        self.identifier_any_case_rx = {ident: re.compile(re.escape(ident), re.IGNORECASE) for ident in self.identifier_rx}
        self.story_ids = sorted(stories)
        self.id_rx = {sid: re.compile(re.escape(sid), re.IGNORECASE) for sid in self.story_ids}
        phrases: set[tuple[str, ...]] = set()
        for story in stories.values():
            for source in (story.title, story.story, *story.acceptance_criteria):
                words = [w for w, _ in _words(source)]
                for i in range(len(words) - PHRASE_WORDS + 1):
                    phrases.add(tuple(words[i : i + PHRASE_WORDS]))
        self.phrases = phrases

    def scan(self, text: str) -> dict[tuple[str, str], tuple[int, int]]:
        """Map (kind, term) to (count, first line)."""
        found: dict[tuple[str, str], tuple[int, int]] = {}

        def add(kind: str, term: str, pos: int) -> None:
            line = text.count("\n", 0, pos) + 1
            count, first = found.get((kind, term), (0, line))
            found[(kind, term)] = (count + 1, min(first, line))

        for word in PRODUCT_WORDS:
            for m in re.finditer(re.escape(word), text, flags=re.IGNORECASE):
                add("product", word, m.start())
        whole: dict[str, list[int]] = {}
        for ident, rx in self.identifier_rx.items():
            whole[ident] = [m.start() for m in rx.finditer(text)]
        for ident, rx in self.identifier_any_case_rx.items():
            # A substring in any case is at least as wide as the registered name measure (found()).
            starts = [m.start() for m in rx.finditer(text)]
            if len(whole[ident]) > len(starts):  # cannot happen; keep the wider list
                starts = whole[ident]
            for pos in starts:
                add("fact_identifier", ident, pos)
        seen_positions: set[int] = set()
        for m in _STORY_ID_RE.finditer(text):
            seen_positions.add(m.start())
            add("story_id", m.group(0).upper(), m.start())
        for sid, rx in self.id_rx.items():
            for m in rx.finditer(text):
                if m.start() not in seen_positions:
                    add("story_id", sid, m.start())
        words = _words(text)
        for i in range(len(words) - PHRASE_WORDS + 1):
            gram = tuple(w for w, _ in words[i : i + PHRASE_WORDS])
            if gram in self.phrases:
                add("story_phrase", " ".join(gram), words[i][1])
        return found


def check_generality(version: str, *, builder: PromptBuilder | None = None) -> GeneralityReport:
    """List all hits of a version. Each is compared with the same file of v0_old."""
    b = builder or PromptBuilder()
    info = b.version(version)
    base = b.version(BASELINE)
    scanner = _Scanner(b.stories())
    hits: list[GeneralityHit] = []
    for file_name in TEMPLATE_FILES:
        mine = scanner.scan((info.folder / file_name).read_text(encoding="utf-8"))
        theirs = scanner.scan((base.folder / file_name).read_text(encoding="utf-8"))
        for (kind, term), (count, line) in mine.items():
            hits.append(
                GeneralityHit(
                    kind=kind,
                    term=term,
                    file=file_name,
                    line=line,
                    count=count,
                    baseline_count=theirs.get((kind, term), (0, 0))[0],
                )
            )
    hits.sort(key=lambda h: (h.file, h.line, h.kind, h.term))
    return GeneralityReport(version=version, hits=tuple(hits))


def assert_general(version: str, *, builder: PromptBuilder | None = None) -> GeneralityReport:
    """Return the report, or raise GeneralityError when a version has new hits."""
    report = check_generality(version, builder=builder)
    if not report.ok:
        shown = report.new_hits[:10]
        lines = "; ".join(f"{h.kind} {h.term!r} at {h.file}:{h.line}" for h in shown)
        more = len(report.new_hits) - len(shown)
        tail = f" (and {more} more)" if more > 0 else ""
        raise GeneralityError(f"version {version!r} has new generality hits: {lines}{tail}")
    return report
