"""Tests for the corrections of secreqgen.documents.formats (plan 2, step P7)."""

from __future__ import annotations

import html
import logging
import re
import time
import zipfile
from pathlib import Path

import pytest
from secreqgen.documents import FormatError, decode_text, formats, to_markdown

WORD = re.compile(r"[A-Za-z0-9']+")


def _find_real_docs() -> Path:
    """Find the Mattermost documentation, in the thesis folder or beside it."""
    for parent in Path(__file__).resolve().parents[2:6]:
        for rel in (
            "real_test_cases/mattermost/docs",
            "coded_project/real_test_cases/mattermost/docs",
        ):
            if (parent / rel).is_dir():
                return parent / rel
    return Path("missing")


REAL_DOCS = _find_real_docs()


def convert(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return to_markdown(path)


# ------------------------------------------------------------------ 1. decoding


def test_decode_text_removes_utf8_bom() -> None:
    """R1-3: a UTF-8 BOM stayed in front of the first heading."""
    assert decode_text(b"\xef\xbb\xbfTitle\n") == "Title\n"


def test_decode_text_reads_utf16_with_bom() -> None:
    """R1-2: a UTF-16 file came out with NUL bytes between the letters."""
    raw = "Caf\u00e9 text\n".encode("utf-16")
    assert decode_text(raw) == "Caf\u00e9 text\n"
    assert decode_text("a b".encode("utf-16-be").join([b"\xfe\xff", b""])) == "a b"


def test_decode_text_falls_back_to_cp1252() -> None:
    """R1-2: Latin text became U+FFFD characters."""
    assert decode_text("Caf\u00e9 r\u00e9sum\u00e9".encode("cp1252")) == (
        "Caf\u00e9 r\u00e9sum\u00e9"
    )


def test_decode_text_rejects_binary() -> None:
    """R1-2: a binary file was accepted as a text file."""
    with pytest.raises(FormatError, match="not a text file"):
        decode_text(b"abc\x00def ghi\x00\n")
    with pytest.raises(FormatError, match="not a text file"):
        decode_text(bytes(range(128, 256)) * 4)


def test_decode_text_keeps_valid_utf8_with_replacement_characters() -> None:
    """C1-4: valid UTF-8 with real U+FFFD characters was rejected."""
    for text in (
        "\ufffd\n",
        "Normal text with a replacement char \ufffd inside and more words here to "
        "pad it out long.\n",
        "a" * 98 + "\ufffd\ufffd",
    ):
        assert decode_text(text.encode("utf-8")) == text


def test_decode_text_keeps_utf8_words_with_a_stray_byte() -> None:
    """C1-3: one stray byte made every accented character wrong."""
    raw = b"Caf\xc3\xa9 and r\xc3\xa9sum\xc3\xa9 here \x92 end"
    assert decode_text(raw) == "Caf\u00e9 and r\u00e9sum\u00e9 here \ufffd end"
    long_text = (
        "Caf\u00e9 r\u00e9sum\u00e9 na\u00efve \u2013 \u201cquoted\u201d. " * 5
    ).encode()
    result = decode_text(long_text + b"\n\x92 stray byte\n")
    assert result == long_text.decode() + "\n\ufffd stray byte\n"
    # A cp1252 file has no valid UTF-8 word, so it is still read as cp1252.
    assert decode_text(b"caf\xe9 and more") == "caf\u00e9 and more"


@pytest.mark.parametrize(
    ("name", "source", "first"),
    [
        ("a.rst", "Title\n=====\n\nBody text.\n", "# Title"),
        ("a.adoc", "= Title\n\nBody text.\n", "# Title"),
        ("a.txt", "Title\n", "Title"),
    ],
)
def test_bom_does_not_break_the_first_heading(
    tmp_path: Path, name: str, source: str, first: str
) -> None:
    """R1-3: with a BOM the first heading of .rst and .adoc was not a heading."""
    path = tmp_path / name
    path.write_bytes(b"\xef\xbb\xbf" + source.encode("utf-8"))
    assert to_markdown(path).splitlines()[0] == first


def test_binary_and_utf16_files_use_the_decoder(tmp_path: Path) -> None:
    """R1-2: to_markdown returned garbage for a binary file and for UTF-16."""
    binary = tmp_path / "binary.txt"
    binary.write_bytes(bytes(range(256)) * 12)
    with pytest.raises(FormatError, match="not a text file"):
        to_markdown(binary)
    wide = tmp_path / "wide.txt"
    wide.write_bytes("User must authenticate.\n".encode("utf-16"))
    assert to_markdown(wide) == "User must authenticate.\n"
    latin = tmp_path / "latin.html"
    latin.write_bytes("<p>Caf\u00e9 r\u00e9sum\u00e9 here.</p>".encode("cp1252"))
    assert to_markdown(latin) == "Caf\u00e9 r\u00e9sum\u00e9 here.\n"


# ------------------------------------------------------------------ 2. PDF


class _FakePage:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text


class _FakeReader:
    pages = [
        _FakePage("The con\ufb01guration of work\ufb02ow \ufb00 o\ufb03ce e\ufb04uent"),
        _FakePage("authentica-\ntion \ufb05 \ufb06"),
    ]

    def __init__(self, _path: str) -> None:
        pass


def test_pdf_ligatures_become_letters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-1: ligature characters stayed in the PDF text; hyphens stay as they are."""
    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    result = to_markdown(tmp_path / "a.pdf")
    assert "The configuration of workflow ff office effluent" in result
    assert "st st" in result
    assert not re.search("[\ufb00-\ufb06]", result)
    # No join of words at line ends.
    assert "authentica-\ntion" in result


def test_pdf_quiets_the_pypdf_logger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-1: the pypdf library printed warnings to the console."""
    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
    logger = logging.getLogger("pypdf")
    old = logger.level
    logger.setLevel(logging.NOTSET)
    try:
        to_markdown(tmp_path / "a.pdf")
        assert logger.level == logging.ERROR
    finally:
        logger.setLevel(old)


# ------------------------------------------------------------------ 3. DOCX

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def rewrite_docx(
    source: Path, target: Path, body: str = "", extra_parts: dict | None = None
) -> None:
    """Copy a DOCX file. Add `body` XML before the section end and add parts."""
    with zipfile.ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    xml = parts["word/document.xml"].decode("utf-8")
    parts["word/document.xml"] = xml.replace(
        "<w:sectPr", body + "<w:sectPr", 1
    ).encode()
    for name, data in (extra_parts or {}).items():
        parts[name] = data.encode("utf-8") if isinstance(data, str) else data
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in parts.items():
            out.writestr(name, data)


def base_docx(tmp_path: Path):
    from docx import Document

    document = Document()
    document.add_heading("Doc Title", level=0)
    document.add_paragraph("First paragraph.")
    path = tmp_path / "base.docx"
    document.save(str(path))
    return document, path


def test_docx_reads_insertions_but_not_deletions(tmp_path: Path) -> None:
    """R1-4: tracked insertions were lost. Deleted text must stay out."""
    _, base = base_docx(tmp_path)
    body = (
        "<w:p><w:r><w:t>Start </w:t></w:r>"
        '<w:ins w:id="1" w:author="a"><w:r><w:t>Inserted words.</w:t></w:r></w:ins>'
        '<w:del w:id="2" w:author="a"><w:r><w:delText>Deleted words.</w:delText>'
        "</w:r></w:del></w:p>"
    )
    target = tmp_path / "ins.docx"
    rewrite_docx(base, target, body)
    result = to_markdown(target)
    assert "Start Inserted words." in result
    assert "Deleted" not in result


def test_docx_reads_content_controls_and_tabs(tmp_path: Path) -> None:
    """R1-4: the text of a content control was lost."""
    _, base = base_docx(tmp_path)
    body = (
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>Content control para.</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
        "<w:p><w:r><w:t>Before tab</w:t><w:tab/><w:t>after tab</w:t></w:r></w:p>"
    )
    target = tmp_path / "sdt.docx"
    rewrite_docx(base, target, body)
    result = to_markdown(target)
    assert "Content control para." in result
    assert "Before tab after tab" in result


def test_docx_reads_nested_tables(tmp_path: Path) -> None:
    """R1-4: a table in a table cell was lost."""
    from docx import Document

    document = Document()
    outer = document.add_table(rows=1, cols=2)
    outer.cell(0, 0).text = "Outer left"
    outer.cell(0, 1).paragraphs[0].text = "Outer cell para"
    inner = outer.cell(0, 1).add_table(rows=1, cols=1)
    inner.cell(0, 0).text = "Nested table text"
    path = tmp_path / "nested.docx"
    document.save(str(path))
    assert to_markdown(path).splitlines() == [
        "Outer left | Outer cell para Nested table text"
    ]


def test_docx_reads_text_boxes_once(tmp_path: Path) -> None:
    """R1-4: text boxes were lost. The Fallback branch must not repeat them."""
    _, base = base_docx(tmp_path)
    plain = (
        "<w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r>"
        "<w:t>Plain box words.</w:t></w:r></w:p></w:txbxContent></v:textbox>"
        "</v:shape></w:pict></w:r></w:p>"
    )
    choice = (
        "<w:p><w:r><mc:AlternateContent><mc:Choice Requires='wps'><w:drawing>"
        "<w:txbxContent><w:p><w:r><w:t>Choice box words.</w:t></w:r></w:p>"
        "</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape>"
        "<v:textbox><w:txbxContent><w:p><w:r><w:t>Choice box words.</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )
    target = tmp_path / "box.docx"
    rewrite_docx(base, target, plain + choice)
    result = to_markdown(target)
    assert "Plain box words." in result
    assert result.count("Choice box words.") == 1


NOTES_XML = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:{{kind}}s {W_NS}>'
    '<w:{kind} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p>'
    "</w:{kind}>"
    '<w:{kind} w:type="continuationSeparator" w:id="0"><w:p><w:r>'
    "<w:continuationSeparator/></w:r></w:p></w:{kind}>"
    '<w:{kind} w:id="1"><w:p><w:r><w:t>{first}</w:t></w:r></w:p></w:{kind}>'
    '<w:{kind} w:id="2"><w:p><w:r><w:t>{second}</w:t></w:r></w:p></w:{kind}>'
    "</w:{kind}s>"
)


def test_docx_reads_footnotes_and_endnotes_in_order(tmp_path: Path) -> None:
    """R1-4: footnote and endnote text was lost. Separator notes stay out."""
    _, base = base_docx(tmp_path)
    footnotes = NOTES_XML.format(kind="footnote", first="Foot one.", second="Foot two.")
    endnotes = NOTES_XML.format(kind="endnote", first="End one.", second="End two.")
    with zipfile.ZipFile(base) as archive:
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        types = archive.read("[Content_Types].xml").decode("utf-8")
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="rId90" Type="{REL}/endnotes" Target="endnotes.xml"/>'
        f'<Relationship Id="rId91" Type="{REL}/footnotes" Target="footnotes.xml"/>'
        "</Relationships>",
    )
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    types = types.replace(
        "</Types>",
        f'<Override PartName="/word/footnotes.xml" ContentType="{ct}.footnotes+xml"/>'
        f'<Override PartName="/word/endnotes.xml" ContentType="{ct}.endnotes+xml"/>'
        "</Types>",
    )
    target = tmp_path / "notes.docx"
    rewrite_docx(
        base,
        target,
        "<w:p><w:r><w:t>Sentence.</w:t></w:r>"
        '<w:r><w:footnoteReference w:id="1"/></w:r></w:p>',
        {
            "word/footnotes.xml": footnotes,
            "word/endnotes.xml": endnotes,
            "word/_rels/document.xml.rels": rels,
            "[Content_Types].xml": types,
        },
    )
    lines = [x for x in to_markdown(target).splitlines() if x]
    assert lines[-4:] == ["Foot one.", "Foot two.", "End one.", "End two."]
    assert "Sentence." in lines


def test_docx_leaves_headers_out(tmp_path: Path) -> None:
    """Headers and footers of the pages stay out, as the docstring says."""
    document, _ = base_docx(tmp_path)
    document.sections[0].header.paragraphs[0].text = "Header words"
    path = tmp_path / "head.docx"
    document.save(str(path))
    assert "Header words" not in to_markdown(path)
    assert "headers and footers" in formats._docx_to_markdown.__doc__.lower()


def test_docx_too_large_when_unpacked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-12: a small DOCX file could unpack to a very large size."""
    assert formats.DOCX_MAX_UNPACKED_BYTES == 200 * 1024 * 1024
    _, base = base_docx(tmp_path)
    monkeypatch.setattr(formats, "DOCX_MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(FormatError, match="the DOCX file is too large"):
        to_markdown(base)


def test_docx_not_a_zip_is_a_format_error(tmp_path: Path) -> None:
    """A file that is no ZIP file gives a FormatError."""
    path = tmp_path / "bad.docx"
    path.write_bytes(b"not a zip")
    with pytest.raises(FormatError):
        to_markdown(path)


# ------------------------------------------------------------------ 4. MDX


def test_mdx_keeps_placeholders(tmp_path: Path) -> None:
    """R1-5: <user>, <host> and <token> were removed from prose."""
    text = convert(
        tmp_path,
        "a.mdx",
        "Replace <user> and <host> and <token> with your values.\n"
        "Run curl -H 'Authorization: Bearer <token>' https://x\n",
    )
    assert "Replace <user> and <host> and <token> with your values." in text
    assert "Bearer <token>' https://x" in text


def test_mdx_keeps_inline_code_spans(tmp_path: Path) -> None:
    """R1-5: a tag inside an inline code span was removed."""
    text = convert(
        tmp_path, "a.mdx", "Use the `<Tabs>` component and ``<div> `x` </div>``.\n"
    )
    assert text == "Use the `<Tabs>` component and ``<div> `x` </div>``.\n"


def test_mdx_removes_components_and_html_tags_only(tmp_path: Path) -> None:
    """R1-5: only a component or an HTML tag goes."""
    text = convert(
        tmp_path,
        "a.mdx",
        "<Callout type='x'>Careful</Callout> a<b>c</b> List<String> <br />\n"
        "<> fragment </>\n",
    )
    assert text == "Careful ac List<String>\n fragment\n"


def test_mdx_removes_multi_line_import_and_export(tmp_path: Path) -> None:
    """R1-5: lines after the first line of an import or export stayed."""
    text = convert(
        tmp_path,
        "a.mdx",
        'import {A,\n  B} from "x";\nexport const meta = {\n  title: "x"\n};\n'
        "export const y =\n  5\n\nKept text.\n",
    )
    assert text == "Kept text.\n"


def test_mdx_code_block_text_is_not_changed(tmp_path: Path) -> None:
    """R1-5: text in a code block keeps its tags and its import lines."""
    text = convert(
        tmp_path, "a.mdx", "```jsx\nimport A from 'a'\n<Tabs>x</Tabs> <user>\n```\n"
    )
    assert text.splitlines() == ["    import A from 'a'", "    <Tabs>x</Tabs> <user>"]


# ------------------------------------------------------------------ 5. RST


def test_rst_directives_without_text_add_nothing(tmp_path: Path) -> None:
    """R1-6: the arguments of Sphinx directives became text."""
    names = (
        "highlight:: python|currentmodule:: mypkg|module:: m|py:module:: m2|"
        "rst-class:: lead|cssclass:: wide|default-role:: code|default-domain:: py|"
        "index:: single: term|meta::|title:: Page|sectionauthor:: Me|"
        "codeauthor:: You|tabularcolumns:: ll|role:: custom(emphasis)"
    ).split("|")
    source = "Before.\n\n" + "".join(f".. {name}\n\n" for name in names) + "After.\n"
    assert convert(tmp_path, "a.rst", source) == "Before.\n\nAfter.\n"


def test_rst_only_keeps_its_body(tmp_path: Path) -> None:
    """R1-6: only: the argument goes, the body stays as normal text."""
    text = convert(
        tmp_path, "a.rst", "Before.\n\n.. only:: html\n\n   Body of only.\n\nAfter.\n"
    )
    assert text == "Before.\n\nBody of only.\n\nAfter.\n"


def test_rst_figure_keeps_caption_and_legend(tmp_path: Path) -> None:
    """R1-6: the caption and the legend of a figure were lost."""
    text = convert(
        tmp_path,
        "a.rst",
        ".. figure:: a.png\n   :alt: x\n   :width: 50%\n\n"
        "   The caption text visible to readers.\n\n   Legend paragraph here.\n\n"
        "After.\n",
    )
    assert "a.png" not in text and ":alt:" not in text
    assert text == (
        "The caption text visible to readers.\n\nLegend paragraph here.\n\nAfter.\n"
    )


def test_rst_named_reference_loses_its_marks(tmp_path: Path) -> None:
    """R1-6: `Python`_ kept the backticks and the underscore."""
    text = convert(
        tmp_path,
        "a.rst",
        "See `Python`_ and `Rust`__ and ``code``_x for `a b <http://x>`_ details.\n",
    )
    assert text.startswith("See Python and Rust and `code`_x for ")


def test_rst_substitution_goes_into_the_text(tmp_path: Path) -> None:
    """R1-6, 0e33481: |name| must become its replace text. No extra paragraph."""
    text = convert(
        tmp_path,
        "a.rst",
        ".. |prod| replace:: SuperProd\n\nUse |prod| here and |other| there.\n\n"
        ".. |other| image:: x.png\n",
    )
    assert text == "Use SuperProd here and |other| there.\n"


def test_rst_nested_too_deep_is_a_format_error(tmp_path: Path) -> None:
    """R1-11: a RecursionError was not a FormatError."""
    depth = 1500
    lines = [" " * i + ".. note::" for i in range(depth)]
    lines.append(" " * depth + "Deep text.")
    path = tmp_path / "deep.rst"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(FormatError, match="the file is nested too deep"):
        to_markdown(path)


# ------------------------------------------------------------------ 6. HTML


def test_html_body_tag_ends_a_head_without_end_tag(tmp_path: Path) -> None:
    """R1-7: a page with <head> and no </head> lost all text."""
    text = convert(
        tmp_path,
        "a.html",
        "<html><head><title>T</title><body><h1>Visible heading</h1>"
        "<p>Body text of page.</p></body></html>",
    )
    assert text.splitlines() == ["# Visible heading", "", "Body text of page."]


@pytest.mark.parametrize(
    "tag",
    "aside figure figcaption summary details hr form fieldset legend address "
    "caption option button select textarea header footer main section article "
    "nav dd dt".split(),
)
def test_html_block_tags_do_not_join_words(tmp_path: Path, tag: str) -> None:
    """R1-7: the words of two blocks joined in minified HTML."""
    if tag == "hr":
        source = "<p>Left words</p>Before<hr>After words"
        expected = ["Before", "After words"]
    else:
        source = f"Before<{tag}>Inside words</{tag}>After words"
        expected = ["Before", "Inside words", "After words"]
    lines = [x for x in convert(tmp_path, "a.html", source).splitlines() if x]
    assert [x for x in lines if x != "Left words"] == expected


def test_html_heading_permalink_adds_nothing(tmp_path: Path) -> None:
    """R1-7: a permalink anchor added a character to the heading."""
    text = convert(
        tmp_path,
        "a.html",
        "<h2>Title<a class='headerlink' href='#t'>\u00b6</a></h2>"
        "<h3>Sub<a href='#s'>#</a></h3><h3><a href='#p'>\u00a7</a> Para</h3>"
        "<h3>Real <a href='#r'>link</a></h3><p>x<a href='#q'>#</a></p>",
    )
    assert text.splitlines() == [
        "## Title",
        "",
        "### Sub",
        "",
        "### Para",
        "",
        "### Real link",
        "",
        "x#",
    ]


# ------------------------------------------------------------------ 7. AsciiDoc


def test_adoc_long_lines_are_fast(tmp_path: Path) -> None:
    """R1-8: the inline patterns were quadratic on a long line."""
    for name, line in (("a.adoc", "<< " * 20000), ("b.adoc", "link:" * 20000)):
        path = tmp_path / name
        path.write_text(line + "\n", encoding="utf-8")
        start = time.perf_counter()
        to_markdown(path)
        assert time.perf_counter() - start < 2


def test_adoc_patterns_still_convert_short_forms() -> None:
    """The bounded patterns still work for normal links."""
    line = "link:http://x.org[the site] <<id,Text>> xref:a.adoc[Doc] https://y.org[Y]"
    assert formats._adoc_inline(line) == (
        "the site (http://x.org) Text Doc Y (https://y.org)"
    )


def test_adoc_attribute_reference_becomes_its_value(tmp_path: Path) -> None:
    """R1-9: {product} stayed, and the value was lost."""
    text = convert(
        tmp_path,
        "a.adoc",
        ":product: SuperProd\n:full: {product} Pro\n\n{product} supports SSO.\n"
        "{full} and {unknown} and \\{product}.\n",
    )
    assert (
        text
        == "SuperProd supports SSO.\nSuperProd Pro and {unknown} and \\{product}.\n"
    )


def test_adoc_comments_go(tmp_path: Path) -> None:
    """R1-9: comment lines and comment blocks stayed as text."""
    text = convert(
        tmp_path,
        "a.adoc",
        "// line comment\nKept one.\n////\nblock comment text\n////\nKept two.\n"
        "----\n// code comment\n----\n",
    )
    assert text.splitlines() == ["Kept one.", "Kept two.", "", "    // code comment"]


def test_adoc_conditionals_go_and_text_stays(tmp_path: Path) -> None:
    """R1-9: ifdef, ifndef, ifeval and endif lines stayed as text."""
    text = convert(
        tmp_path,
        "a.adoc",
        "ifdef::env-github[]\nGithub text.\nendif::[]\n"
        "ifndef::x[]\nNot x text.\nendif::x[]\n"
        "ifeval::[{n} > 1]\nEval text.\nendif::[]\n"
        "ifdef::flag[One line text.]\nEnd.\n",
    )
    assert text.splitlines() == [
        "Github text.",
        "Not x text.",
        "Eval text.",
        "One line text.",
        "End.",
    ]


def test_adoc_block_title_rule_stays(tmp_path: Path) -> None:
    """R1-9: a line with a dot and a letter is a title for AsciiDoc."""
    assert convert(tmp_path, "a.adoc", ".Title here\ntext\n") == "Title here\ntext\n"


# ------------------------------------------------------------------ main rule


def md_to_html(source: str) -> tuple[str, list[str]]:
    """A small writer: Markdown to HTML. Return the HTML and the body text."""
    html_parts: list[str] = []
    body: list[str] = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False

    def flush() -> None:
        if paragraph:
            text = " ".join(paragraph)
            html_parts.append(f"<p>{html.escape(text)}</p>")
            body.append(text)
            paragraph.clear()

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            if in_code:
                text = "\n".join(code)
                html_parts.append(f"<pre>{html.escape(text)}</pre>")
                body.append(text)
                code.clear()
            in_code = not in_code
        elif in_code:
            code.append(line)
        elif not stripped:
            flush()
        elif heading := re.match(r"(#{1,6})\s+(.*)", stripped):
            flush()
            level = len(heading.group(1))
            html_parts.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
            body.append(heading.group(2))
        elif item := re.match(r"(?:[-*+]|\d+\.)\s+(.*)", stripped):
            flush()
            html_parts.append(f"<ul><li>{html.escape(item.group(1))}</li></ul>")
            body.append(item.group(1))
        else:
            paragraph.append(stripped)
    flush()
    if code:
        body.append("\n".join(code))
        html_parts.append(f"<pre>{html.escape(chr(10).join(code))}</pre>")
    return "\n".join(html_parts), body


@pytest.mark.skipif(not REAL_DOCS.is_dir(), reason="no real documentation folder")
def test_real_text_keeps_its_words(tmp_path: Path) -> None:
    """The main rule: a conversion changes no word of the body text."""
    files = [p for p in sorted(REAL_DOCS.rglob("*.md")) if p.stat().st_size < 50_000]
    assert files
    checked = 0
    for index, source in enumerate(files):
        text = source.read_text(encoding="utf-8", errors="replace")
        page, body = md_to_html(text)
        if not body:
            continue
        target = tmp_path / f"page{index}.html"
        target.write_text(page, encoding="utf-8")
        try:
            result = to_markdown(target)
        except FormatError:
            pytest.fail(f"no text for {source.name}")
        assert WORD.findall(result) == WORD.findall("\n".join(body)), source.name
        checked += 1
    assert checked > 0


# ------------------------------------------------------------------ C1 round 2


def _timed(tmp_path: Path, name: str, text: str) -> float:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    start = time.perf_counter()
    to_markdown(path)
    return time.perf_counter() - start


@pytest.mark.parametrize("unit", ["link:a[ ", "http://a[ ", "xref:a[ "])
def test_adoc_open_bracket_lines_are_fast(tmp_path: Path, unit: str) -> None:
    """C1-1: the text part of a link pattern had no bound and was quadratic."""
    assert _timed(tmp_path, "a.adoc", unit * 20000 + "\n") < 2


def test_mdx_unended_imports_are_fast(tmp_path: Path) -> None:
    """C1-2: many lines that start an import that never ends were quadratic."""
    assert _timed(tmp_path, "a.mdx", "import a (\n" * 20000 + "Kept.\n") < 2
    assert _timed(tmp_path, "b.mdx", "import {\n" * 20000 + "Kept.\n") < 2
    assert _timed(tmp_path, "c.mdx", "export const a = (\n" * 20000 + "Kept.\n") < 2


def test_docx_line_break_stays_a_new_line(tmp_path: Path) -> None:
    """C1-5: w:br became a space and the lines of a block joined."""
    _, base = base_docx(tmp_path)
    body = (
        "<w:p><w:r><w:t>line one</w:t><w:br/><w:t>line two</w:t><w:br/>"
        "<w:t>line three</w:t><w:tab/><w:t>tabbed</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Head</w:t>'
        "<w:br/><w:t>line</w:t></w:r></w:p>"
    )
    target = tmp_path / "br.docx"
    rewrite_docx(base, target, body)
    result = to_markdown(target)
    assert "line one\nline two\nline three tabbed" in result


def test_docx_reads_a_content_control_around_a_cell(tmp_path: Path) -> None:
    """C1-6: a w:sdt around a table cell lost the cell text."""
    _, base = base_docx(tmp_path)
    cell = "<w:tc><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:tc>"
    body = (
        "<w:tbl><w:tr><w:sdt><w:sdtContent>"
        + cell.format("RowSdt cell")
        + "</w:sdtContent></w:sdt>"
        + cell.format("Plain cell")
        + "</w:tr></w:tbl>"
    )
    target = tmp_path / "rowsdt.docx"
    rewrite_docx(base, target, body)
    assert "RowSdt cell | Plain cell" in to_markdown(target)


def test_mdx_keeps_upper_case_placeholders(tmp_path: Path) -> None:
    """C1-7: <TOKEN>, <API_KEY> and <YOUR_HOST> were removed from prose."""
    text = convert(
        tmp_path,
        "a.mdx",
        "Set <TOKEN> to the value and <YOUR_HOST> too\n"
        "Use <name> and <API_KEY> or <Your_Host> or <K2>\n"
        "--legal-hold-secret <KEY>\n<Callout>Real</Callout> <Tab x='1'>y</Tab>\n",
    )
    assert text.splitlines() == [
        "Set <TOKEN> to the value and <YOUR_HOST> too",
        "Use <name> and <API_KEY> or <Your_Host> or <K2>",
        "--legal-hold-secret <KEY>",
        "Real y",
    ]


def test_mdx_prose_that_starts_with_import_keeps_next_lines(tmp_path: Path) -> None:
    """C1-8: prose that starts with "import" took the next lines or the file."""
    text = convert(
        tmp_path, "a.mdx", "import statements are common,\nand they matter.\n"
    )
    assert text == "and they matter.\n"
    text = convert(
        tmp_path,
        "b.mdx",
        "import the data (see below,\nthen continue) with care.\n\nBody\n",
    )
    assert text == "then continue) with care.\n\nBody\n"
    text = convert(
        tmp_path,
        "c.mdx",
        "import type { A,\n  B } from 'x'\nimport 'a.css'\nexport default {\n a: 1\n}\n"
        "Kept.\n",
    )
    assert text == "Kept.\n"


def test_html_head_without_end_tags_ends_at_flow_content(tmp_path: Path) -> None:
    """C1-9: <head> with no </head> and no <body> lost the whole page."""
    text = convert(
        tmp_path,
        "a.html",
        "<head><title>T</title><meta charset=x><h1>Heading</h1><p>P</p>",
    )
    assert text.splitlines() == ["# Heading", "", "P"]
    text = convert(tmp_path, "b.html", "<head><title>T</title><p>Paragraph in head</p>")
    assert text == "Paragraph in head\n"
    text = convert(tmp_path, "c.html", "<head><title>Hidden</title></head><p>Shown</p>")
    assert text == "Shown\n"


def test_rst_substitution_is_not_replaced_inside_code(tmp_path: Path) -> None:
    """C1-10: |prod| inside a literal or a code span was replaced."""
    text = convert(
        tmp_path,
        "a.rst",
        ".. |prod| replace:: SuperProd\n\n"
        "Use ``|prod|`` literally and `|prod|` and |prod|.\n",
    )
    assert text == "Use `|prod|` literally and `|prod|` and SuperProd.\n"


def test_rst_substitution_inside_a_replace_text(tmp_path: Path) -> None:
    """C1-10: a substitution inside a replace text stayed as |b|."""
    text = convert(
        tmp_path,
        "a.rst",
        ".. |a| replace:: see |b|\n.. |b| replace:: Beta\n.. |c| replace:: |c| |d|\n"
        "\nText |a| and |c|.\n",
    )
    assert text == "Text see Beta and |c| |d|.\n"


def test_adoc_empty_and_built_in_attributes(tmp_path: Path) -> None:
    """C1-11: an empty attribute and {lt} or {empty} stayed as braces."""
    text = convert(
        tmp_path,
        "a.adoc",
        ":flag:\n\n{flag}Text and {empty}text and {lt}div{gt} {unknown}\n",
    )
    assert text == "Text and text and <div> {unknown}\n"


def test_adoc_attribute_value_goes_over_lines(tmp_path: Path) -> None:
    """C1-11: a value that ends with a backslash was cut."""
    text = convert(
        tmp_path,
        "a.adoc",
        ":desc: first line + \\\nsecond line\n\n{desc}\n",
    )
    assert text == "first line + second line\n"
