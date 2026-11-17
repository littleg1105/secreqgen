"""Tests for secreqgen.documents (other file types become Markdown)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from secreqgen.documents import (
    MAX_FILE_BYTES,
    FormatError,
    build_mirror,
    other_files,
    to_markdown,
)

WORD = re.compile(r"[A-Za-z0-9']+")


def words(text: str) -> list[str]:
    return WORD.findall(text)


# Each case: file name, source text, the words a person reads in the source.
CASES = {
    "mdx": (
        "guide.mdx",
        "import Tabs from '@theme/Tabs'\n\n# Install guide\n\n"
        '<Tabs>\n<Note title="x" />\n\n## First part\n\n{/* hidden */}\n'
        "Read the [docs](https://example.org) now.\n\n- one item\n- two item\n\n"
        "```bash\n# a comment\nrun now\n```\n\n</Tabs>\n\n## Second part\n\n"
        "Last text.\n",
        "Install guide First part Read the docs https example org now one item "
        "two item "
        "a comment run now Second part Last text",
    ),
    "rst": (
        "guide.rst",
        "Install guide\n=============\n\nFirst part\n----------\n\n"
        "Read :doc:`the docs <intro>` and ``code`` and "
        "`site <https://example.org>`_ now.\n\n* one item\n* two item\n\n"
        "Run this::\n\n    # a comment\n    run now\n\n"
        ".. note:: Keep it safe.\n\n"
        ".. toctree::\n   :maxdepth: 2\n\n   other\n\n"
        "Second part\n-----------\n\nLast text.\n",
        "Install guide First part Read the docs and code and site "
        "https://example.org now one item two item Run this a comment run now "
        "Keep it safe Second part Last text",
    ),
    "adoc": (
        "guide.adoc",
        "= Install guide\n:toc:\n:author: x\n\n== First part\n\n"
        "See link:https://example.org[the site] now.\n\n* one item\n* two item\n\n"
        "[source,bash]\n----\n# a comment\nrun now\n----\n\n"
        "NOTE: Keep it safe.\n\nimage::pic.png[]\n\n== Second part\n\nLast text.\n",
        "Install guide First part See the site https://example.org now one item "
        "two item a comment run now Keep it safe Second part Last text",
    ),
    "html": (
        "guide.html",
        "<html><head><title>skip me</title><style>p{}</style></head><body>"
        "<h1>Install guide</h1><h2>First part</h2>"
        '<p>Read <a href="https://example.org">the docs</a> &amp; go.</p>'
        "<ul><li>one item</li><li>two item</li></ul>"
        "<pre># a comment\nrun now</pre><p>Keep it safe.</p>"
        "<h2>Second part</h2><p>Last text.</p><script>var x = 1;</script>"
        "</body></html>",
        "Install guide First part Read the docs go one item two item "
        "a comment run now Keep it safe Second part Last text",
    ),
}
# The words that the "amp" entity gives: "&" has no word, so none is listed.


@pytest.mark.parametrize("kind", sorted(CASES))
def test_text_formats_keep_words_and_mark_headings(tmp_path: Path, kind: str) -> None:
    name, source, expected = CASES[kind]
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    result = to_markdown(path)
    assert words(result) == words(expected)
    lines = result.splitlines()
    assert "# Install guide" in lines
    assert "## First part" in lines
    assert "## Second part" in lines
    assert "    # a comment" in lines
    assert "    run now" in lines
    assert result.endswith("\n") and not result.endswith("\n\n")
    assert "\n\n\n" not in result
    assert all(line == line.rstrip() for line in lines)


def test_text_and_markdown_files_stay_as_they_are(tmp_path: Path) -> None:
    for name in ("a.txt", "b.markdown"):
        path = tmp_path / name
        path.write_text("# Title  \n\ntext\n\n\n\nmore\n", encoding="utf-8")
        assert to_markdown(path) == "# Title\n\ntext\n\nmore\n"


def test_mdx_drops_imports_and_tags(tmp_path: Path) -> None:
    path = tmp_path / "a.mdx"
    path.write_text(
        "import X from 'y'\nexport const z = 1\n\n<Tabs>\n<TabItem>\nkeep text\n"
        "</TabItem>\n</Tabs>\n<https://example.org>\n",
        encoding="utf-8",
    )
    assert to_markdown(path) == "keep text\n\n<https://example.org>\n"


def test_rst_heading_levels_follow_first_appearance(tmp_path: Path) -> None:
    path = tmp_path / "a.rst"
    path.write_text(
        "=====\nTitle\n=====\n\nPart\n----\n\nSub\n~~~\n\nPart two\n--------\n",
        encoding="utf-8",
    )
    assert to_markdown(path).splitlines() == [
        "# Title",
        "",
        "## Part",
        "",
        "### Sub",
        "",
        "## Part two",
    ]


def test_rst_code_block_directive(tmp_path: Path) -> None:
    path = tmp_path / "a.rst"
    path.write_text(
        "Intro\n\n.. code-block:: python\n   :linenos:\n\n   # c\n   x = 1\n\nAfter\n",
        encoding="utf-8",
    )
    assert to_markdown(path).splitlines() == [
        "Intro",
        "",
        "    # c",
        "    x = 1",
        "",
        "After",
    ]


def test_html_table_and_entities(tmp_path: Path) -> None:
    path = tmp_path / "a.htm"
    path.write_text(
        "<table><tr><th>Name</th><th>Role</th></tr>"
        "<tr><td>A &lt; B</td><td>x</td></tr></table>",
        encoding="utf-8",
    )
    assert to_markdown(path).splitlines() == ["Name | Role", "A < B | x"]


def test_unknown_suffix_and_empty_text(tmp_path: Path) -> None:
    other = tmp_path / "a.xyz"
    other.write_text("x")
    with pytest.raises(FormatError):
        to_markdown(other)
    empty = tmp_path / "a.txt"
    empty.write_text(" \n\n")
    with pytest.raises(FormatError):
        to_markdown(empty)
    with pytest.raises(FormatError):
        to_markdown(tmp_path / "missing.txt")


@pytest.mark.slow
def test_chunker_reads_converted_files(tmp_path: Path) -> None:
    from secreqgen.ingestion.chunker import ArchitectureAwareChunker

    chunker = ArchitectureAwareChunker()
    for kind, (name, source, _expected) in CASES.items():
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        sections = chunker._parse_markdown_structure(to_markdown(path))
        # A heading with no text of its own gives no section.
        assert [s.title for s in sections] == ["First part", "Second part"], kind
        assert sections[0].hierarchy == ["Install guide", "First part"], kind
        assert "# a comment" in sections[0].content, kind


def test_docx(tmp_path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_heading("Install guide", level=0)
    document.add_heading("First part", level=2)
    document.add_paragraph("Some text.")
    document.add_paragraph("one item", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    for row, cells in enumerate([("Name", "Role"), ("A", "B")]):
        for col, text in enumerate(cells):
            table.cell(row, col).text = text
    path = tmp_path / "a.docx"
    document.save(str(path))
    assert to_markdown(path).splitlines() == [
        "# Install guide",
        "",
        "## First part",
        "",
        "Some text.",
        "",
        "- one item",
        "",
        "Name | Role",
        "A | B",
    ]


def test_pdf_errors_and_blank_page(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(FormatError):
        to_markdown(bad)
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    blank = tmp_path / "blank.pdf"
    with blank.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(FormatError, match="no text in the PDF"):
        to_markdown(blank)


# ------------------------------------------------------------------ mirror


def listing(folder: Path) -> list[tuple[str, int]]:
    return sorted(
        (p.relative_to(folder).as_posix(), p.stat().st_size)
        for p in folder.rglob("*")
        if p.is_file()
    )


def make_source(root: Path) -> Path:
    source = root / "src_docs"
    (source / "guide").mkdir(parents=True)
    (source / ".hidden").mkdir()
    (source / "node_modules").mkdir()
    (source / "a.md").write_bytes(b"# A\r\n\r\ntext  \n")
    (source / ".hidden" / "h.md").write_text("# Hidden\n\ntext\n")
    (source / "guide" / "install.rst").write_text("Install\n=======\n\nRun it.\n")
    (source / "clash.txt").write_text("from txt\n")
    (source / "clash.txt.md").write_text("# real\n")
    (source / "node_modules" / "x.txt").write_text("skip me\n")
    (source / "empty.txt").write_text("\n")
    (source / "big.txt").write_text("x")
    return source


def test_other_files_counts_without_skipped_folders(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    assert other_files(source) == {".rst": 1, ".txt": 3}


def test_build_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_source(tmp_path)
    before = listing(source)
    monkeypatch.setattr("secreqgen.documents.mirror.MAX_FILE_BYTES", 0)
    # A zero limit makes every file "too large": check that path first.
    too_big = build_mirror(source, tmp_path / "m0")
    assert {name for name, _ in too_big.skipped} == {
        "guide/install.rst",
        "clash.txt",
        "empty.txt",
        "big.txt",
    }
    assert all("larger than 5 MB" in why for _, why in too_big.skipped)
    monkeypatch.undo()
    assert MAX_FILE_BYTES == 5_000_000
    target = tmp_path / "mirror"
    report = build_mirror(source, target)
    assert (target / "a.md").read_bytes() == (source / "a.md").read_bytes()
    assert (target / ".hidden" / "h.md").exists()
    assert (target / "guide" / "install.rst.md").read_text() == (
        "# Install\n\nRun it.\n"
    )
    assert (target / "clash.txt.md").read_text() == "# real\n"
    assert (target / "clash.txt.converted.md").read_text() == "from txt\n"
    assert not (target / "node_modules" / "x.txt.md").exists()
    assert report.markdown == 3
    assert report.converted == {".rst": 1, ".txt": 2}
    assert [name for name, _ in report.skipped] == ["empty.txt"]
    assert "no text" in report.skipped[0][1]
    assert report.folder == target
    assert listing(source) == before


def test_build_mirror_refuses_bad_targets(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    with pytest.raises(ValueError):
        build_mirror(source, source / "mirror")
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x")
    with pytest.raises(ValueError):
        build_mirror(source, full)
    empty = tmp_path / "empty"
    empty.mkdir()
    build_mirror(source, empty)


def test_large_file_is_skipped(tmp_path: Path) -> None:
    source = tmp_path / "s"
    source.mkdir()
    (source / "big.txt").write_text("word " * (MAX_FILE_BYTES // 5 + 10))
    report = build_mirror(source, tmp_path / "m")
    assert report.skipped == [("big.txt", "the file is larger than 5 MB")]
    assert report.converted == {}


def test_symlink_out_of_source_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    source = tmp_path / "s"
    source.mkdir()
    (source / "link.txt").symlink_to(outside)
    assert other_files(source) == {}
    report = build_mirror(source, tmp_path / "m")
    assert report.converted == {}
    assert not list((tmp_path / "m").rglob("*"))


def chunk_keys(chunks) -> list[tuple[str, str, str]]:
    return [(c.file, c.section, c.text) for c in chunks]


@pytest.mark.slow
def test_search_reads_a_mirror(tmp_path: Path) -> None:
    from secreqgen.retrieval.word_search import load_chunks

    source = tmp_path / "s"
    (source / "guide").mkdir(parents=True)
    (source / "a.md").write_text("# A\n\nAlpha text.\n\n## B\n\nBeta text.\n")
    (source / "guide" / "install.rst").write_text("Install\n=======\n\nRun it.\n")
    report = build_mirror(source, tmp_path / "m")
    files = {c.file for c in load_chunks(report.folder)}
    assert files == {"a.md", "guide/install.rst.md"}

    only_md = tmp_path / "md"
    only_md.mkdir()
    (only_md / "a.md").write_text("# A\n\nAlpha text.\n\n## B\n\nBeta text.\n")
    (only_md / "b.md").write_text("Plain text without heading.\n")
    mirror = build_mirror(only_md, tmp_path / "m2")
    assert chunk_keys(load_chunks(mirror.folder)) == chunk_keys(load_chunks(only_md))
