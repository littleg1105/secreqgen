"""A mirror folder: every documentation file that the search can read is `.md`.

Each `.md` file is copied as it is, to the same relative path. Each file of
another type is converted and written as `<relative path>.md`. Nothing is ever
written into the source folder.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from secreqgen.documents.formats import OTHER_SUFFIXES, FormatError, to_markdown

SKIPPED_FOLDERS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        "site-packages",
        ".tox",
    }
)
MAX_FILE_BYTES = 5_000_000


@dataclass
class MirrorReport:
    """What a mirror holds."""

    source: Path
    folder: Path  # give this folder to the search
    markdown: int  # .md files, copied as they are
    converted: dict[str, int] = field(default_factory=dict)  # suffix -> count
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _convertible(source: Path) -> list[Path]:
    """Files that to_markdown can read, sorted. No file is opened."""
    found: list[Path] = []
    for folder, names, files in os.walk(source):
        names[:] = sorted(n for n in names if n not in SKIPPED_FOLDERS)
        for name in sorted(files):
            if Path(name).suffix.lower() not in OTHER_SUFFIXES:
                continue
            path = Path(folder) / name
            if path.is_symlink() and not _inside(path, source):
                continue
            found.append(path)
    return found


def other_files(source: Path) -> dict[str, int]:
    """Count the files that to_markdown can read, by suffix (small letters)."""
    counts: dict[str, int] = {}
    for path in _convertible(Path(source)):
        suffix = path.suffix.lower()
        counts[suffix] = counts.get(suffix, 0) + 1
    return counts


def build_mirror(source: Path, target: Path) -> MirrorReport:
    """Write the mirror of `source` into `target`. `target` must be empty."""
    source, target = Path(source), Path(target)
    if _inside(target, source):
        raise ValueError("the mirror folder must not be inside the source folder")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("the mirror folder must be empty or missing")
    target.mkdir(parents=True, exist_ok=True)
    report = MirrorReport(source=source, folder=target, markdown=0)
    for path in sorted(source.rglob("*.md")):
        if not path.is_file() or (path.is_symlink() and not _inside(path, source)):
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        report.markdown += 1
    for path in _convertible(source):
        relative = path.relative_to(source)
        shown = relative.as_posix()
        if path.stat().st_size > MAX_FILE_BYTES:
            report.skipped.append((shown, "the file is larger than 5 MB"))
            continue
        try:
            text = to_markdown(path)
        except FormatError as error:
            report.skipped.append((shown, str(error)))
            continue
        name = relative.with_name(relative.name + ".md")
        if (source / name).is_file():
            name = relative.with_name(relative.name + ".converted.md")
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        suffix = path.suffix.lower()
        report.converted[suffix] = report.converted.get(suffix, 0) + 1
    return report
