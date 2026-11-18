"""Turn a documentation file into Markdown text.

The one rule: a conversion changes markup only. It adds no word, removes no
word and changes no word of the text that a person reads. Heading marks, list
marks and the 4 spaces in front of a code line are markup. Page furniture
(navigation, headers and footers of a page, metadata, labels of admonitions)
may be left out.

The chunker of the tool does not know code fences. So every code block comes
out as lines with 4 spaces in front.
"""

from __future__ import annotations

import codecs
import logging
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

OTHER_SUFFIXES = (
    ".markdown",
    ".mdx",
    ".txt",
    ".rst",
    ".adoc",
    ".asciidoc",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
)

CODE_INDENT = "    "

# A DOCX file may unpack to far more than its size on disk.
DOCX_MAX_UNPACKED_BYTES = 200 * 1024 * 1024


class FormatError(Exception):
    """The file cannot be read, or it holds no text."""


def decode_text(raw: bytes) -> str:
    """Decode the bytes of a text file. Raise FormatError for a binary file.

    A UTF-8 BOM is removed. A UTF-16 BOM means UTF-16. Else the bytes should be
    UTF-8. A file that is mostly UTF-8 with a few bad bytes keeps its UTF-8
    words: only the bad bytes become U+FFFD. Any other file is read as
    Windows-1252. Text that was read with replacement must have 1 in 100 bad
    characters at most.
    """
    checked = True
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16", errors="replace")
    else:
        if raw.startswith(codecs.BOM_UTF8):
            raw = raw[len(codecs.BOM_UTF8) :]
        try:
            text = raw.decode("utf-8")
            checked = False
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            bad = text.count("\ufffd")
            good = sum(1 for char in text if char > "\x7f" and char != "\ufffd")
            if good > bad:
                checked = False
            else:
                text = raw.decode("cp1252", errors="replace")
    if "\x00" in text or (checked and text.count("\ufffd") * 100 > len(text)):
        raise FormatError("not a text file")
    return text


def to_markdown(path: Path) -> str:
    """Return the Markdown text of a file. Raise FormatError when that fails."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in OTHER_SUFFIXES:
        raise FormatError(f"file type {suffix or '(none)'} is not supported")
    try:
        if suffix == ".pdf":
            text = _pdf_to_text(path)
        elif suffix == ".docx":
            text = _docx_to_markdown(path)
        else:
            try:
                raw = path.read_bytes()
            except OSError as error:
                raise FormatError(f"cannot read the file: {error}") from error
            text = _TEXT_CONVERTERS[suffix](decode_text(raw))
    except RecursionError as error:
        raise FormatError("the file is nested too deep") from error
    return _finish(text)


def _finish(text: str) -> str:
    """Strip trailing spaces, keep one empty line at most, end with one newline."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    if not out:
        raise FormatError("no text in the file")
    return "\n".join(out) + "\n"


def _code_block(lines: list[str]) -> list[str]:
    """Return the lines of a code block, with empty lines around it."""
    return ["", *[CODE_INDENT + line if line.strip() else "" for line in lines], ""]


def _dedent(lines: list[str]) -> list[str]:
    """Remove the common indent of the lines that are not empty."""
    widths = [len(x) - len(x.lstrip()) for x in lines if x.strip()]
    cut = min(widths) if widths else 0
    return [x[cut:] if x.strip() else "" for x in lines]


def _plain(text: str) -> str:
    return text


# ------------------------------------------------------------------ MDX

_JSX_TAG = re.compile(r"</?([A-Za-z][\w.]*)(?:\s[^<>]*?)?\s*/?>|</?>")
_MDX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.DOTALL)
# An inline code span: equal runs of backticks, no empty line inside.
_MDX_CODE_SPAN = re.compile(
    r"(?<!`)(`+)(?!`)((?:(?!\n\s*\n).){1,1000}?)(?<!`)\1(?!`)", re.DOTALL
)
_MDX_SAVED = re.compile(r"\x00(\d+)\x00")
_MDX_LINE_END = ("=", ",", "+", "=>", "&&", "||", "from")
# A name in capitals (with digits or underscores) is a placeholder.
_MDX_PLACEHOLDER = re.compile(r"[A-Z][A-Z0-9_]*")
# The first line of a real import or export statement. Prose does not match.
_MDX_ESM_START = re.compile(
    r"""^(?:import\s*(?:\{|\*|['"]|type\s+[\w${*]|[\w$]+\s*(?:,|from\b|$))"""
    r"""|export\s*(?:\{|\*|(?:const|let|var|function|async|class|default|type"""
    r"""|interface|enum)\b))"""
)
# An import or export may run over this number of lines at most.
_MDX_ESM_MAX_LINES = 200
# After this number of statements that never end, only one line is removed.
_MDX_ESM_MAX_FAILS = 20

# The normal HTML tag names. A placeholder such as <user> is not in this list.
_HTML_TAGS = frozenset(
    """a abbr area article aside audio b bdi bdo blockquote body br button canvas
    caption center cite code col colgroup dd del details dfn dialog div dl dt em
    embed fieldset figcaption figure font footer form h1 h2 h3 h4 h5 h6 head
    header hgroup hr html i iframe img ins kbd legend li main mark nav noscript
    ol optgroup p param picture pre progress q rp rt ruby s samp script section
    small span strike strong style sub summary sup svg table tbody td textarea
    tfoot th thead tr track u ul video wbr""".split()
)


def _mdx_tag(match: re.Match[str]) -> str:
    """Remove a component tag or an HTML tag. Keep a placeholder such as <user>."""
    name = match.group(1)
    if name is None:
        return ""
    if _MDX_PLACEHOLDER.fullmatch(name) or ("_" in name and "." not in name):
        # <TOKEN>, <API_KEY> and <Your_Host> are placeholders, not components.
        if not match.group(0).rstrip(">").endswith("/") and " " not in match.group(0):
            return match.group(0)
    if name[0].isupper():
        # List<String> is a type in prose, not a component.
        before = match.string[match.start() - 1 : match.start()]
        generic = before.isalnum() and not match.group(0).startswith("</")
        return match.group(0) if generic else ""
    if "." in name or name.lower() in _HTML_TAGS:
        return ""
    return match.group(0)


def _mdx_esm_end(lines: list[str], start: int) -> int | None:
    """Return the index after the last line of the import or export at `start`.

    The statement ends where the brackets close and the line does not go on.
    Return None when it does not end within the line limit.
    """
    depth = 0
    template = False
    i = start
    stop = min(len(lines), start + _MDX_ESM_MAX_LINES)
    while i < stop:
        line = lines[i]
        quote = ""
        j = 0
        while j < len(line):
            char = line[j]
            if template:
                if char == "\\":
                    j += 1
                elif char == "`":
                    template = False
            elif quote:
                if char == "\\":
                    j += 1
                elif char == quote:
                    quote = ""
            elif char in "'\"":
                quote = char
            elif char == "`":
                template = True
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth = max(0, depth - 1)
            elif char == "/" and line[j + 1 : j + 2] == "/":
                break
            j += 1
        i += 1
        if template or depth > 0 or line.rstrip().endswith(_MDX_LINE_END):
            continue
        return i
    return None


def _mdx_drop_esm(lines: list[str]) -> list[str]:
    """Remove import and export statements. Prose that starts with "import" stays."""
    kept: list[str] = []
    i = 0
    fails = 0
    while i < len(lines):
        if lines[i].startswith(("import ", "export ")):
            end = None
            if fails < _MDX_ESM_MAX_FAILS and _MDX_ESM_START.match(lines[i]):
                end = _mdx_esm_end(lines, i)
                if end is None:
                    fails += 1
            i = end if end is not None else i + 1
        else:
            kept.append(lines[i])
            i += 1
    return kept


def _mdx_outside(lines: list[str]) -> list[str]:
    text = "\n".join(_mdx_drop_esm(lines))
    saved: list[str] = []

    def keep(match: re.Match[str]) -> str:
        saved.append(match.group(0))
        return f"\x00{len(saved) - 1}\x00"

    text = _MDX_CODE_SPAN.sub(keep, text)
    text = _MDX_COMMENT.sub("", text)
    text = _JSX_TAG.sub(_mdx_tag, text)
    text = _MDX_SAVED.sub(lambda m: saved[int(m.group(1))], text)
    return text.split("\n")


def _mdx_to_markdown(raw: str) -> str:
    out: list[str] = []
    outside: list[str] = []
    code: list[str] = []
    in_code = False
    for line in raw.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            if in_code:
                out.extend(_code_block(_dedent(code)))
                code = []
            else:
                out.extend(_mdx_outside(outside))
                outside = []
            in_code = not in_code
        elif in_code:
            code.append(line)
        else:
            outside.append(line)
    if in_code:
        out.extend(_code_block(_dedent(code)))
    out.extend(_mdx_outside(outside))
    return "\n".join(out)


# ------------------------------------------------------------------ RST

_RST_ADORN = re.compile(r"^([!-/:-@\[-`{-~])\1+$")
_RST_DIRECTIVE = re.compile(r"^(\s*)\.\. ([\w:+-]+)::\s*(.*)$")
_RST_TARGET = re.compile(r"^\s*\.\. _[^:]+:")
_RST_COMMENT = re.compile(r"^(\s*)\.\.(\s.*)?$")
# A footnote or a citation: ".. [1] text". Its text is text for the reader.
_RST_NOTE = re.compile(r"^(\s*)\.\. (\[[^\]]+\])\s*(.*)$")
# A substitution: ".. |name| replace:: text". Only "replace" holds text.
_RST_SUBSTITUTION = re.compile(r"^(\s*)\.\. \|([^|]+)\| ([\w-]+)::\s*(.*)$")
_RST_SUBST_REF = re.compile(r"\|(?=\S)([^|\n]*?\S)\|(?!\w)")
_RST_OPTION = re.compile(r"^\s*:[\w-]+:(\s.*)?$")
_RST_CODE = ("code-block", "sourcecode", "code")
# Directives that carry no text for the reader.
_RST_DROP = frozenset(
    {
        "toctree",
        "image",
        "include",
        "contents",
        "raw",
        "highlight",
        "currentmodule",
        "module",
        "py:module",
        "rst-class",
        "cssclass",
        "default-role",
        "default-domain",
        "index",
        "meta",
        "title",
        "sectionauthor",
        "codeauthor",
        "tabularcolumns",
        "role",
    }
)
# Directives whose argument is no text, but whose body is text.
_RST_BODY_ONLY = frozenset({"only", "figure"})
_RST_ROLE = re.compile(r":[\w.+-]+(?::[\w.+-]+)?:`([^`]+)`")
_RST_LINK = re.compile(r"`([^`<]+?)\s*<([^`>]+)>`__?")
_RST_LITERAL = re.compile(r"``([^`]+)``")
# A named reference: `Python`_ or `Python`__ (not a literal, not a role).
_RST_NAMED = re.compile(r"(?<![`:\w])`([^`<>\n]+?)`__?(?!\w)")
# Text in literal marks is code. A substitution does not work inside it.
_RST_CODE_SPAN = re.compile(r"(``[^`]+``|`[^`\n]+`)")
_RST_ROLE_TARGET = re.compile(r"^(.*?)\s*<[^<>]*>$", re.DOTALL)


def _rst_role(match: re.Match[str]) -> str:
    inner = match.group(1)
    found = _RST_ROLE_TARGET.match(inner)
    return found.group(1) if found else inner


def _subst_key(name: str) -> str:
    return " ".join(name.lower().split())


def _rst_substitutions(lines: list[str]) -> dict[str, str]:
    """Return the text of every `replace::` definition, by name."""
    found: dict[str, str] = {}
    for i, line in enumerate(lines):
        match = _RST_SUBSTITUTION.match(line)
        if not match or match.group(3).lower() != "replace":
            continue
        block, _ = _rst_block(lines, i + 1, len(match.group(1)))
        parts = [match.group(4).strip(), *(x.strip() for x in block)]
        found.setdefault(_subst_key(match.group(2)), " ".join(x for x in parts if x))
    # A replace text may use other substitutions. Resolve them a few times.
    for _ in range(5):
        changed = False
        for key, value in found.items():
            if "|" not in value:
                continue
            others = {k: v for k, v in found.items() if k != key}
            new = _rst_replace_refs(value, others)
            if new != value:
                found[key] = new
                changed = True
        if not changed:
            break
    return found


def _rst_replace_refs(line: str, subs: dict[str, str]) -> str:
    """Replace |name| in the parts of a line that are not code."""
    parts = _RST_CODE_SPAN.split(line)
    for index in range(0, len(parts), 2):
        if "|" in parts[index]:
            parts[index] = _RST_SUBST_REF.sub(
                lambda m: subs.get(_subst_key(m.group(1)), m.group(0)), parts[index]
            )
    return "".join(parts)


def _rst_inline(line: str, subs: dict[str, str] | None = None) -> str:
    if subs and "|" in line:
        line = _rst_replace_refs(line, subs)
    line = _RST_ROLE.sub(_rst_role, line)
    line = _RST_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
    line = _RST_NAMED.sub(lambda m: m.group(1), line)
    return _RST_LITERAL.sub(lambda m: f"`{m.group(1)}`", line)


def _rst_block(lines: list[str], start: int, indent: int) -> tuple[list[str], int]:
    """Collect the lines after `start` that are empty or indented more."""
    block: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
        i += 1
    while block and not block[-1].strip():
        block.pop()
    return block, i


def _rst_convert(
    lines: list[str], styles: list[tuple[str, str]], subs: dict[str, str]
) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(lines)

    def inline(text: str) -> str:
        return _rst_inline(text, subs)

    def level(style: tuple[str, str]) -> int:
        if style not in styles:
            styles.append(style)
        return min(styles.index(style) + 1, 6)

    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            out.append("")
            i += 1
            continue
        # A heading with a line above and below.
        if (
            _RST_ADORN.match(stripped)
            and i + 2 < n
            and lines[i + 1].strip()
            and not _RST_ADORN.match(lines[i + 1].strip())
            and lines[i + 2].strip() == stripped
            and len(stripped) >= len(lines[i + 1].strip())
        ):
            title = lines[i + 1].strip()
            out.extend(["", "#" * level(("over", stripped[0])) + " " + title, ""])
            i += 3
            continue
        # A heading with a line below.
        if (
            not line[0].isspace()
            and not _RST_ADORN.match(stripped)
            and i + 1 < n
            and _RST_ADORN.match(lines[i + 1].strip())
            and len(lines[i + 1].strip()) >= len(stripped)
        ):
            style = ("under", lines[i + 1].strip()[0])
            out.extend(["", "#" * level(style) + " " + inline(stripped), ""])
            i += 2
            continue
        if _RST_TARGET.match(line):
            i += 1
            continue
        found = _RST_DIRECTIVE.match(line)
        if found:
            indent = len(found.group(1))
            name = found.group(2).lower()
            argument = found.group(3)
            block, i = _rst_block(lines, i + 1, indent)
            while block and (not block[0].strip() or _RST_OPTION.match(block[0])):
                block.pop(0)
            if name in _RST_DROP:
                continue
            if name in _RST_CODE:
                out.extend(_code_block(_dedent(block)))
                continue
            if argument and name not in _RST_BODY_ONLY:
                out.extend(["", inline(argument)])
            out.append("")
            out.extend(_rst_convert(_dedent(block), styles, subs))
            out.append("")
            continue
        found = _RST_NOTE.match(line)
        if found:
            block, i = _rst_block(lines, i + 1, len(found.group(1)))
            first = f"{found.group(2)} {inline(found.group(3))}".rstrip()
            out.extend(["", first])
            out.extend(inline(part.strip()) for part in block if part.strip())
            out.append("")
            continue
        found = _RST_SUBSTITUTION.match(line)
        if found:
            # The text of a "replace" definition goes into the text that uses it.
            _, i = _rst_block(lines, i + 1, len(found.group(1)))
            continue
        found = _RST_COMMENT.match(line)
        if found:
            _, i = _rst_block(lines, i + 1, len(found.group(1)))
            continue
        if stripped == "::" or stripped.endswith("::"):
            if stripped != "::":
                out.append(inline(line[:-1].rstrip()))
            block, i = _rst_block(lines, i + 1, len(line) - len(line.lstrip()))
            if block:
                out.extend(_code_block(_dedent(block)))
            continue
        out.append(inline(line))
        i += 1
    return out


def _rst_to_markdown(raw: str) -> str:
    lines = raw.splitlines()
    return "\n".join(_rst_convert(lines, [], _rst_substitutions(lines)))


# ------------------------------------------------------------------ AsciiDoc

_ADOC_HEADING = re.compile(r"^(=+)\s+(.*)$")
_ADOC_ATTR = re.compile(r"^:[\w-]+!?:(\s.*)?$")
_ADOC_BLOCK_ATTR = re.compile(r"^\[[^\]]*\]$")
_ADOC_MACRO = re.compile(r"^(image|include|toc)::\S*\[.*\]$")
_ADOC_NOTE = re.compile(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s+(.*)$")
_ADOC_DELIM = re.compile(r"^(=|\*|_){4,}$")
_ADOC_LINK = re.compile(r"\blink:(\S{1,2000}?)\[([^\]]{0,500})\]")
_ADOC_URL = re.compile(r"(?<![\w:])(https?://[^\s\[\]]{1,2000})\[([^\]]{0,500})\]")
_ADOC_XREF = re.compile(r"\bxref:[^\[\s]{1,2000}\[([^\]]{0,500})\]")
_ADOC_ANGLE = re.compile(r"<<([^,>]{1,500})(?:,([^>]{0,500}))?>>")
_ADOC_ATTR_DEF = re.compile(r"^:(!?)([\w-]+)(!?):(?:[ \t]+(.*?))?[ \t]*$")
_ADOC_ATTR_REF = re.compile(r"(?<!\\)\{([\w-]+)\}")
_ADOC_COMMENT = re.compile(r"^//(?!/)")
_ADOC_COMMENT_BLOCK = re.compile(r"^/{4,}$")
_ADOC_IF_LINE = re.compile(r"^(ifdef|ifndef)::[^\[\]]*\[(.*)\]$")
_ADOC_IF_BLOCK = re.compile(r"^(ifeval::\[.*\]|endif::[^\[\]]*\[.*\])$")


# Attributes that AsciiDoc defines itself. The reader sees their values.
_ADOC_BUILTIN = {
    "empty": "",
    "blank": "",
    "nbsp": " ",
    "lt": "<",
    "gt": ">",
    "amp": "&",
    "plus": "+",
    "vbar": "|",
    "startsb": "[",
    "endsb": "]",
    "caret": "^",
    "tilde": "~",
    "apos": "'",
    "quot": '"',
    "backtick": "`",
    "backslash": "\\",
    "two-colons": "::",
    "two-semicolons": ";;",
    "deg": "\u00b0",
}


def _adoc_join_attribute_lines(lines: list[str]) -> list[str]:
    """Join an attribute value that goes on the next line (it ends with " \\")."""
    out: list[str] = []
    fence = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1
        if fence:
            if stripped == fence:
                fence = ""
        elif re.fullmatch(r"-{4,}|\.{4,}|/{4,}", stripped):
            fence = stripped
        elif _ADOC_ATTR_DEF.match(line):
            while line.endswith((" \\", "\t\\")) and i < len(lines):
                line = line[:-1].rstrip() + " " + lines[i].strip()
                i += 1
        out.append(line)
    return out


def _adoc_link(match: re.Match[str]) -> str:
    url, text = match.group(1), match.group(2).strip()
    return f"{text} ({url})" if text else url


def _adoc_inline(line: str, attrs: dict[str, str] | None = None) -> str:
    if attrs is not None and "{" in line:
        line = _ADOC_ATTR_REF.sub(
            lambda m: attrs.get(m.group(1), _ADOC_BUILTIN.get(m.group(1), m.group(0))),
            line,
        )
    if "link:" in line:
        line = _ADOC_LINK.sub(_adoc_link, line)
    if "://" in line:
        line = _ADOC_URL.sub(_adoc_link, line)
    if "xref:" in line:
        line = _ADOC_XREF.sub(lambda m: m.group(1), line)
    if "<<" in line:
        line = _ADOC_ANGLE.sub(lambda m: (m.group(2) or m.group(1)).strip(), line)
    return line


def _adoc_attributes(lines: list[str]) -> dict[str, str]:
    """Return the first value of every `:name: value` line."""
    found: dict[str, str] = {}
    for line in lines:
        match = _ADOC_ATTR_DEF.match(line)
        if match and not match.group(1) and not match.group(3):
            found.setdefault(match.group(2), match.group(4) or "")
    return found


def _adoc_to_markdown(raw: str) -> str:
    out: list[str] = []
    code: list[str] = []
    fence = ""
    comment = ""
    in_table = False
    lines = _adoc_join_attribute_lines(raw.splitlines())
    attrs = _adoc_attributes(lines)
    for line in lines:
        stripped = line.strip()
        if fence:
            if stripped == fence:
                out.extend(_code_block(_dedent(code)))
                code, fence = [], ""
            else:
                code.append(line)
            continue
        if comment:
            if stripped == comment:
                comment = ""
            continue
        if _ADOC_COMMENT_BLOCK.match(stripped):
            comment = stripped
            continue
        if _ADOC_COMMENT.match(line):
            continue
        if re.fullmatch(r"-{4,}|\.{4,}", stripped):
            fence = stripped
            continue
        if _ADOC_IF_BLOCK.match(stripped):
            continue
        found = _ADOC_IF_LINE.match(stripped)
        if found:
            if found.group(2).strip():
                out.append(_adoc_inline(found.group(2), attrs))
            continue
        if stripped.startswith("|==="):
            in_table = not in_table
            out.append("")
            continue
        if in_table and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:]]
            out.append(" | ".join(_adoc_inline(c, attrs) for c in cells))
            continue
        found = _ADOC_ATTR_DEF.match(line)
        if found:
            name = found.group(2)
            if found.group(1) or found.group(3):
                attrs.pop(name, None)
            else:
                attrs[name] = _adoc_inline(found.group(4) or "", attrs)
            continue
        if _ADOC_ATTR.match(stripped) or _ADOC_BLOCK_ATTR.match(stripped):
            continue
        if _ADOC_MACRO.match(stripped) or _ADOC_DELIM.match(stripped):
            continue
        found = _ADOC_HEADING.match(line)
        if found:
            marks = "#" * min(len(found.group(1)), 6)
            out.extend(["", f"{marks} {_adoc_inline(found.group(2), attrs)}", ""])
            continue
        found = _ADOC_NOTE.match(stripped)
        if found:
            stripped = found.group(2)
            line = stripped
        if re.match(r"^\.[A-Za-z]", line):
            line = line[1:]
        out.append(_adoc_inline(line, attrs))
    if fence:
        out.extend(_code_block(_dedent(code)))
    return "\n".join(out)


# ------------------------------------------------------------------ HTML

_HTML_SKIP = frozenset({"script", "style", "template", "svg"})
# In HTML5 the end tag of <head> is optional. A start tag of this kind ends it.
_HTML_AFTER_HEAD = frozenset(
    """a article aside blockquote br code dd details div dl dt em figure footer form
    h1 h2 h3 h4 h5 h6 header hr img li main nav ol p pre section span strong table
    td th tr ul""".split()
)
_HTML_BREAK = frozenset(
    {
        "address",
        "article",
        "aside",
        "button",
        "caption",
        "dd",
        "details",
        "div",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hr",
        "legend",
        "main",
        "nav",
        "option",
        "section",
        "select",
        "summary",
        "textarea",
    }
)
_HTML_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# An anchor in a heading with only one of these marks is a permalink.
_HTML_PERMALINKS = frozenset({"\u00b6", "#", "\u00a7"})
_HTML_BLOCK = frozenset({"p", "ul", "ol", "table", "blockquote", "dl"})


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.skip = 0
        self.pre: list[str] | None = None
        self.cells = 0
        self.in_heading = False
        self.anchor_at: int | None = None
        self.in_head = False

    def end_line(self, blank: bool = False) -> None:
        text = " ".join("".join(self.current).split())
        self.current = []
        if text:
            self.lines.append(text)
        if blank and self.lines and self.lines[-1]:
            self.lines.append("")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "body":
            # The body starts here, also when </head> is missing.
            self.skip = 0
            self.in_head = False
        elif tag == "head":
            self.in_head = True
            return
        elif self.in_head and not self.skip and tag in _HTML_AFTER_HEAD:
            self.in_head = False
        if tag in _HTML_SKIP:
            self.skip += 1
            return
        if self.skip or self.in_head:
            return
        if tag in _HTML_HEADINGS:
            self.end_line(True)
            self.current.append("#" * int(tag[1]) + " ")
            self.in_heading = True
            self.anchor_at = None
        elif tag == "a" and self.in_heading:
            self.anchor_at = len(self.current)
        elif tag == "pre":
            self.end_line(True)
            self.pre = []
        elif tag == "li":
            self.end_line()
            self.current.append("- ")
        elif tag in ("td", "th"):
            if self.cells:
                self.current.append(" | ")
            self.cells += 1
        elif tag == "tr":
            self.end_line()
            self.cells = 0
        elif tag == "br" or tag in _HTML_BREAK:
            self.end_line()
        elif tag in _HTML_BLOCK:
            self.end_line(True)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag in _HTML_SKIP or tag == "head":
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False
            return
        if tag in _HTML_SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or self.in_head:
            return
        if tag in _HTML_HEADINGS:
            self.in_heading = False
            self.anchor_at = None
            self.end_line(True)
        elif tag == "a" and self.anchor_at is not None:
            if self.anchor_at <= len(self.current):
                mark = "".join(self.current[self.anchor_at :]).strip()
                if mark in _HTML_PERMALINKS:
                    del self.current[self.anchor_at :]
            self.anchor_at = None
        elif tag in ("p", "ul", "ol"):
            self.end_line(True)
        elif tag == "pre" and self.pre is not None:
            body = "".join(self.pre).strip("\n").split("\n")
            self.lines.extend(_code_block(body))
            self.pre = None
        elif tag in ("tr", "li") or tag in _HTML_BREAK:
            self.end_line()
            self.cells = 0
        elif tag in ("table", "blockquote", "dl"):
            self.end_line(True)

    def handle_data(self, data: str) -> None:
        if self.skip or self.in_head:
            return
        if self.pre is not None:
            self.pre.append(data)
        else:
            self.current.append(data)


def _html_to_markdown(raw: str) -> str:
    parser = _HtmlText()
    parser.feed(raw)
    parser.close()
    parser.end_line()
    return "\n".join(parser.lines)


_TEXT_CONVERTERS = {
    ".markdown": _plain,
    ".txt": _plain,
    ".mdx": _mdx_to_markdown,
    ".rst": _rst_to_markdown,
    ".adoc": _adoc_to_markdown,
    ".asciidoc": _adoc_to_markdown,
    ".html": _html_to_markdown,
    ".htm": _html_to_markdown,
}


# ------------------------------------------------------------------ PDF, DOCX

# The ligature characters U+FB00 to U+FB06 and the letters that they stand for.
_PDF_LIGATURES = str.maketrans(
    {
        0xFB00: "ff",
        0xFB01: "fi",
        0xFB02: "fl",
        0xFB03: "ffi",
        0xFB04: "ffl",
        0xFB05: "st",
        0xFB06: "st",
    }
)


def _pdf_to_text(path: Path) -> str:
    """Read the text of a PDF. Ligature characters become their letters.

    The text stays approximate: words at line ends and in columns are not
    joined or repaired.
    """
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as error:  # pypdf raises many kinds of error
        raise FormatError(f"cannot read the PDF: {error}") from error
    text = "\n\n".join(page for page in pages if page).translate(_PDF_LIGATURES)
    if not text.strip():
        raise FormatError("no text in the PDF (a scan?)")
    return text


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_W_TEXT = f"{{{_W_NS}}}t"
_W_SPACING = frozenset(f"{{{_W_NS}}}{name}" for name in ("tab", "ptab"))
# A line break keeps the line structure of a code block or a list in a paragraph.
_W_LINE_BREAK = frozenset(f"{{{_W_NS}}}{name}" for name in ("br", "cr"))
_W_HYPHEN = f"{{{_W_NS}}}noBreakHyphen"
# Deleted text: a tracked deletion, and the old place of a moved text.
_W_DELETED = frozenset(f"{{{_W_NS}}}{name}" for name in ("del", "moveFrom"))
_W_BOX = f"{{{_W_NS}}}txbxContent"
_MC_FALLBACK = f"{{{_MC_NS}}}Fallback"
_W_NOTE_SKIP = frozenset({"separator", "continuationSeparator", "continuationNotice"})


def _w(name: str) -> str:
    return f"{{{_W_NS}}}{name}"


def _docx_inline(element, texts: list[str], boxes: list) -> None:
    """Collect the text of a paragraph and the text boxes inside it.

    Tracked insertions count. Deleted text does not. The `mc:Fallback` branch
    of a text box is left out, because it repeats the `mc:Choice` branch.
    """
    for child in element.iterchildren():
        tag = child.tag
        if not isinstance(tag, str) or tag in _W_DELETED or tag == _MC_FALLBACK:
            continue
        if tag == _W_TEXT:
            texts.append(child.text or "")
        elif tag in _W_SPACING:
            texts.append(" ")
        elif tag in _W_LINE_BREAK:
            texts.append("\n")
        elif tag == _W_HYPHEN:
            texts.append("-")
        elif tag == _W_BOX:
            boxes.append(child)
        else:
            _docx_inline(child, texts, boxes)


def _docx_blocks(container, styled: bool) -> list[tuple]:
    """Return blocks: ("p", element or None, text) or ("tbl", rows)."""
    items: list[tuple] = []
    for child in container.iterchildren():
        tag = child.tag
        if tag == _w("p"):
            texts: list[str] = []
            boxes: list = []
            _docx_inline(child, texts, boxes)
            items.append(("p", child if styled else None, "".join(texts).strip()))
            for box in boxes:
                items.extend(_docx_blocks(box, False))
        elif tag == _w("tbl"):
            items.append(("tbl", _docx_rows(child)))
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                items.extend(_docx_blocks(content, styled))
        elif tag == _w("customXml"):
            items.extend(_docx_blocks(child, styled))
    return items


def _docx_flat(items: list[tuple]) -> str:
    """Return the text of blocks on one line."""
    parts: list[str] = []
    for item in items:
        if item[0] == "p":
            parts.append(item[2])
        else:
            for row in item[1]:
                parts.extend(row)
    return " ".join(" ".join(parts).split())


def _docx_cells(container) -> list:
    """Return the cells of a row. A cell may stand inside a content control."""
    cells: list = []
    for child in container.iterchildren():
        if child.tag == _w("tc"):
            cells.append(child)
        elif child.tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                cells.extend(_docx_cells(content))
    return cells


def _docx_rows(table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.iterchildren(_w("tr")):
        cells = [_docx_flat(_docx_blocks(cell, False)) for cell in _docx_cells(row)]
        if any(cells):
            rows.append(cells)
    return rows


def _docx_notes(document) -> list[str]:
    """Return the text of the footnotes, then the endnotes, one text each."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    notes: list[str] = []
    for kind in ("footnotes", "endnotes"):
        for rel in document.part.rels.values():
            if rel.is_external or rel.reltype != f"{_REL_NS}/{kind}":
                continue
            try:
                root = etree.fromstring(rel.target_part.blob, parser)
            except etree.XMLSyntaxError:
                continue
            for note in root.iterchildren(_w(kind[:-1])):
                if note.get(_w("type")) in _W_NOTE_SKIP:
                    continue
                text = _docx_flat(_docx_blocks(note, False))
                if text:
                    notes.append(text)
    return notes


def _docx_check_size(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except (OSError, zipfile.BadZipFile) as error:
        raise FormatError(f"cannot read the DOCX file: {error}") from error
    if total > DOCX_MAX_UNPACKED_BYTES:
        raise FormatError("the DOCX file is too large")


def _docx_to_markdown(path: Path) -> str:
    """Read the text of a DOCX file.

    This covers paragraphs, tracked insertions, content controls, tables (also
    tables in cells), text boxes, footnotes and endnotes. Deleted text is left
    out. Headers and footers of the pages are left out.
    """
    from docx import Document
    from docx.text.paragraph import Paragraph

    _docx_check_size(path)
    try:
        document = Document(str(path))
    except Exception as error:  # python-docx raises many kinds of error
        raise FormatError(f"cannot read the DOCX file: {error}") from error
    out: list[str] = []
    for item in _docx_blocks(document.element.body, True):
        if item[0] == "tbl":
            out.append("")
            out.extend(" | ".join(row) for row in item[1])
            out.append("")
            continue
        _, element, text = item
        if not text:
            continue
        style = ""
        if element is not None:
            try:
                style = Paragraph(element, document).style.name or ""
            except Exception:  # noqa: BLE001
                style = ""
        heading = re.fullmatch(r"Heading (\d)", style)
        if style == "Title" or heading:
            # A heading stays on one line.
            text = " ".join(text.split())
        if style == "Title":
            out.extend(["", f"# {text}", ""])
        elif heading:
            out.extend(["", "#" * min(int(heading.group(1)), 6) + f" {text}", ""])
        elif style.startswith("List"):
            out.append(f"- {text}")
        else:
            out.extend(["", text, ""])
    for note in _docx_notes(document):
        out.extend(["", note, ""])
    return "\n".join(out)
