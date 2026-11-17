"""Turn a documentation file into Markdown text.

The one rule: a conversion changes markup only. It adds no word, removes no
word and changes no word of the text that a person reads. Heading marks, list
marks and the 4 spaces in front of a code line are markup.

The chunker of the tool does not know code fences. So every code block comes
out as lines with 4 spaces in front.
"""

from __future__ import annotations

import re
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


class FormatError(Exception):
    """The file cannot be read, or it holds no text."""


def to_markdown(path: Path) -> str:
    """Return the Markdown text of a file. Raise FormatError when that fails."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in OTHER_SUFFIXES:
        raise FormatError(f"file type {suffix or '(none)'} is not supported")
    if suffix == ".pdf":
        text = _pdf_to_text(path)
    elif suffix == ".docx":
        text = _docx_to_markdown(path)
    else:
        try:
            raw = path.read_bytes().decode("utf-8", errors="replace")
        except OSError as error:
            raise FormatError(f"cannot read the file: {error}") from error
        text = _TEXT_CONVERTERS[suffix](raw)
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

_JSX_TAG = re.compile(r"</?[A-Za-z][\w.]*(?:\s[^<>]*?)?\s*/?>")
_MDX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.DOTALL)


def _mdx_outside(lines: list[str]) -> list[str]:
    kept = [x for x in lines if not x.startswith(("import ", "export "))]
    text = _MDX_COMMENT.sub("", "\n".join(kept))
    text = _JSX_TAG.sub("", text)
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
_RST_SUBSTITUTION = re.compile(r"^(\s*)\.\. \|[^|]+\| ([\w-]+)::\s*(.*)$")
_RST_OPTION = re.compile(r"^\s*:[\w-]+:(\s.*)?$")
_RST_CODE = ("code-block", "sourcecode", "code")
_RST_DROP = ("toctree", "image", "figure", "include", "contents", "raw")
_RST_ROLE = re.compile(r":[\w.+-]+(?::[\w.+-]+)?:`([^`]+)`")
_RST_LINK = re.compile(r"`([^`<]+?)\s*<([^`>]+)>`__?")
_RST_LITERAL = re.compile(r"``([^`]+)``")
_RST_ROLE_TARGET = re.compile(r"^(.*?)\s*<[^<>]*>$", re.DOTALL)


def _rst_role(match: re.Match[str]) -> str:
    inner = match.group(1)
    found = _RST_ROLE_TARGET.match(inner)
    return found.group(1) if found else inner


def _rst_inline(line: str) -> str:
    line = _RST_ROLE.sub(_rst_role, line)
    line = _RST_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
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


def _rst_convert(lines: list[str], styles: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(lines)

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
            out.extend(["", "#" * level(style) + " " + _rst_inline(stripped), ""])
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
            if argument:
                out.extend(["", _rst_inline(argument)])
            out.append("")
            out.extend(_rst_convert(_dedent(block), styles))
            out.append("")
            continue
        found = _RST_NOTE.match(line)
        if found:
            block, i = _rst_block(lines, i + 1, len(found.group(1)))
            first = f"{found.group(2)} {_rst_inline(found.group(3))}".rstrip()
            out.extend(["", first])
            out.extend(_rst_inline(part.strip()) for part in block if part.strip())
            out.append("")
            continue
        found = _RST_SUBSTITUTION.match(line)
        if found:
            block, i = _rst_block(lines, i + 1, len(found.group(1)))
            if found.group(2).lower() == "replace":
                out.extend(["", _rst_inline(found.group(3))])
                out.extend(_rst_inline(part.strip()) for part in block if part.strip())
                out.append("")
            continue
        found = _RST_COMMENT.match(line)
        if found:
            _, i = _rst_block(lines, i + 1, len(found.group(1)))
            continue
        if stripped == "::" or stripped.endswith("::"):
            if stripped != "::":
                out.append(_rst_inline(line[:-1].rstrip()))
            block, i = _rst_block(lines, i + 1, len(line) - len(line.lstrip()))
            if block:
                out.extend(_code_block(_dedent(block)))
            continue
        out.append(_rst_inline(line))
        i += 1
    return out


def _rst_to_markdown(raw: str) -> str:
    return "\n".join(_rst_convert(raw.splitlines(), []))


# ------------------------------------------------------------------ AsciiDoc

_ADOC_HEADING = re.compile(r"^(=+)\s+(.*)$")
_ADOC_ATTR = re.compile(r"^:[\w-]+!?:(\s.*)?$")
_ADOC_BLOCK_ATTR = re.compile(r"^\[[^\]]*\]$")
_ADOC_MACRO = re.compile(r"^(image|include|toc)::\S*\[.*\]$")
_ADOC_NOTE = re.compile(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s+(.*)$")
_ADOC_DELIM = re.compile(r"^(=|\*|_){4,}$")
_ADOC_LINK = re.compile(r"\blink:(\S+?)\[([^\]]*)\]")
_ADOC_URL = re.compile(r"(?<![\w:])(https?://[^\s\[\]]+)\[([^\]]*)\]")
_ADOC_XREF = re.compile(r"\bxref:[^\[\s]+\[([^\]]*)\]")
_ADOC_ANGLE = re.compile(r"<<([^,>]+)(?:,([^>]*))?>>")


def _adoc_link(match: re.Match[str]) -> str:
    url, text = match.group(1), match.group(2).strip()
    return f"{text} ({url})" if text else url


def _adoc_inline(line: str) -> str:
    line = _ADOC_LINK.sub(_adoc_link, line)
    line = _ADOC_URL.sub(_adoc_link, line)
    line = _ADOC_XREF.sub(lambda m: m.group(1), line)
    return _ADOC_ANGLE.sub(lambda m: (m.group(2) or m.group(1)).strip(), line)


def _adoc_to_markdown(raw: str) -> str:
    out: list[str] = []
    code: list[str] = []
    fence = ""
    in_table = False
    for line in raw.splitlines():
        stripped = line.strip()
        if fence:
            if stripped == fence:
                out.extend(_code_block(_dedent(code)))
                code, fence = [], ""
            else:
                code.append(line)
            continue
        if re.fullmatch(r"-{4,}|\.{4,}", stripped):
            fence = stripped
            continue
        if stripped.startswith("|==="):
            in_table = not in_table
            out.append("")
            continue
        if in_table and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:]]
            out.append(" | ".join(_adoc_inline(c) for c in cells))
            continue
        if _ADOC_ATTR.match(stripped) or _ADOC_BLOCK_ATTR.match(stripped):
            continue
        if _ADOC_MACRO.match(stripped) or _ADOC_DELIM.match(stripped):
            continue
        found = _ADOC_HEADING.match(line)
        if found:
            marks = "#" * min(len(found.group(1)), 6)
            out.extend(["", f"{marks} {_adoc_inline(found.group(2))}", ""])
            continue
        found = _ADOC_NOTE.match(stripped)
        if found:
            stripped = found.group(2)
            line = stripped
        if re.match(r"^\.[A-Za-z]", line):
            line = line[1:]
        out.append(_adoc_inline(line))
    if fence:
        out.extend(_code_block(_dedent(code)))
    return "\n".join(out)


# ------------------------------------------------------------------ HTML

_HTML_SKIP = frozenset({"script", "style", "head", "template", "svg"})
_HTML_BREAK = frozenset(
    {"div", "section", "article", "header", "footer", "nav", "main", "dt", "dd"}
)
_HTML_BLOCK = frozenset({"p", "ul", "ol", "table", "blockquote", "dl"})


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.skip = 0
        self.pre: list[str] | None = None
        self.cells = 0

    def end_line(self, blank: bool = False) -> None:
        text = " ".join("".join(self.current).split())
        self.current = []
        if text:
            self.lines.append(text)
        if blank and self.lines and self.lines[-1]:
            self.lines.append("")

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _HTML_SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.end_line(True)
            self.current.append("#" * int(tag[1]) + " ")
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
        if tag in _HTML_SKIP:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol"):
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
        if self.skip:
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


def _pdf_to_text(path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as error:  # pypdf raises many kinds of error
        raise FormatError(f"cannot read the PDF: {error}") from error
    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise FormatError("no text in the PDF (a scan?)")
    return text


def _docx_to_markdown(path: Path) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(str(path))
    except Exception as error:  # python-docx raises many kinds of error
        raise FormatError(f"cannot read the DOCX file: {error}") from error
    out: list[str] = []
    for element in document.element.body.iterchildren():
        name = element.tag.rsplit("}", 1)[-1]
        if name == "p":
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if not text:
                continue
            try:
                style = paragraph.style.name or ""
            except Exception:
                style = ""
            heading = re.fullmatch(r"Heading (\d)", style)
            if style == "Title":
                out.extend(["", f"# {text}", ""])
            elif heading:
                out.extend(["", "#" * min(int(heading.group(1)), 6) + f" {text}", ""])
            elif style.startswith("List"):
                out.append(f"- {text}")
            else:
                out.extend(["", text, ""])
        elif name == "tbl":
            out.append("")
            for row in Table(element, document).rows:
                seen: set[int] = set()
                cells: list[str] = []
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cells.append(" ".join(cell.text.split()))
                out.append(" | ".join(cells))
            out.append("")
    return "\n".join(out)
