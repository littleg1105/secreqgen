# ruff: noqa
# fmt: off
# (The copied lab definitions must stay equal to their lab source, so no tool reformats this file.)
"""Edits of a passage block before it goes into the prompt (tested configuration "s1_word_3000").

Every definition below the line "COPIED CODE" is a copy, word for word, of the lab code that made the final
test of 17 November 2026 (scripts/tuning_lib/passages.py and scripts/tuning_lib/prompts.py). Do not edit a
copied definition here: tests/unit/test_word_search.py compares each one with its lab source.

- split_context   reads a block in the passage format of the tool
- render_markup   writes the template markup of the Keycloak documentation in the plain form of the page
                  (only for the product "keycloak"; each other block stays as it is)
- drop_empty      removes a documentation passage that holds only its header line
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

# ----------------------------------------------------------------------------- COPIED CODE
FALLBACK_CONTEXT: str = "No relevant security documentation found."  # 41 characters; retriever.py:343
SEPARATOR = "\n\n---\n\n"
_HEADER_RE = re.compile(
    r"^### \[(\d+)\] (.+) \(Relevance: ([0-9]+\.[0-9]+)\)$", re.MULTILINE
)
_CODE_LINE_RE = re.compile(r"^### code search hit \(pattern: '(.*)'\)$")


class PassagesError(Exception):
    """A stored prompt or context is missing, damaged or not as expected."""


@dataclass(frozen=True)
class Passage:
    """One passage of a context."""

    index: int
    kind: str
    source: str
    relevance: float
    text: str
    code_pattern: str | None
    code_location: str | None


def split_context(text: str) -> list[Passage]:
    """Split a stored context into passages. Raise PassagesError on any odd shape."""
    if not isinstance(text, str) or not text.strip():
        raise PassagesError("the context is empty")
    if text == FALLBACK_CONTEXT:
        raise PassagesError("the context is the 41-character fallback string")
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        raise PassagesError("the context has no passage header")
    if matches[0].start() != 0:
        raise PassagesError("there is text before the first passage header")
    numbers = [int(m.group(1)) for m in matches]
    if numbers != list(range(1, len(matches) + 1)):
        raise PassagesError(f"passage numbers are not 1..n: {numbers}")
    passages: list[Passage] = []
    for i, m in enumerate(matches):
        last = i == len(matches) - 1
        end = len(text) if last else matches[i + 1].start()
        segment = text[m.start() : end]
        if not last:
            if not segment.endswith(SEPARATOR):
                raise PassagesError(f"passage {i + 1} does not end with the separator")
            segment = segment[: -len(SEPARATOR)]
        lines = segment.split("\n")
        second = lines[1] if len(lines) > 1 else ""
        code = _CODE_LINE_RE.match(second)
        pattern: str | None = None
        location: str | None = None
        kind = "doc"
        if code:
            kind = "code"
            pattern = code.group(1)
            for line in lines[2:]:
                if line.startswith("File: "):
                    location = line[len("File: ") :].strip()
                    break
            if not location:
                raise PassagesError(f"code hit {i + 1} has no 'File:' line")
        passages.append(
            Passage(
                index=int(m.group(1)),
                kind=kind,
                source=m.group(2),
                relevance=float(m.group(3)),
                text=segment,
                code_pattern=pattern,
                code_location=location,
            )
        )
    return passages


def doc_passages(passages: Sequence[Passage]) -> list[Passage]:
    """Return the documentation passages."""
    return [p for p in passages if p.kind == "doc"]
_HEADER_NUMBER_RE = re.compile(r"^### \[(\d+)\] ")


class PromptError(Exception):
    """A version, a template, a story or a prompt is not as expected."""


@dataclass(frozen=True)
class PassageContext:
    """What a passage change may need besides the block: the product and the story text.

    story_text is the title, the story sentence and the acceptance criteria as the prompt shows them.
    """

    project: str
    story_text: str
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
_WS1 = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
_IMPORT_RUN_RE = re.compile(
    r"(?:\\?<#import" + _WS1 + r'"[^"\n]*"' + _WS1 + r"as" + _WS1 + r"\w+[ \t]*\\?>(?:" + _WS1 + r")?)+"
)
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


# ----------------------------------------------------------------------------- END OF COPIED CODE


def render_markup(block: str, product: str | None) -> str:
    """Plain form of Keycloak template markup. The block of each other product is returned unchanged."""
    return _render_markup(block, PassageContext(project=product or "", story_text=""))


def drop_empty(block: str) -> str:
    """Remove a documentation passage that holds only its header line."""
    return _drop_empty(block)
