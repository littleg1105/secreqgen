"""A mirror folder: every documentation file that the search can read is `.md`.

Each `.md` file is copied as it is, to the same relative path. Each file of
another type is converted and written as `<relative path>.md`. Nothing is ever
written into the source folder. A file that cannot be read is skipped: one bad
file never stops the work.

Files of other types are left out when they are not documentation: files in
dependency, build, test and template folders, and files with a name such as
LICENSE.txt or requirements.txt.
"""

from __future__ import annotations

import fnmatch
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
# Folders of a codebase whose files of other types are not documentation:
# test data, sample data and the page templates of an application.
NOT_DOCS_FOLDERS = frozenset(
    "test tests spec specs fixtures __tests__ testdata mocks seed seeds seeder "
    "templates _templates views layouts partials".split()
)
NOT_DOCS_NAMES = (
    "license*",
    "licence*",
    "copying*",
    "notice*",
    "ofl.txt",
    "requirements*.txt",
    "constraints*.txt",
    "robots.txt",
    "humans.txt",
    "cmakelists.txt",
)
MAX_FILE_BYTES = 5_000_000
# Raise this number when the conversion or the choice of files changes: a
# stored mirror of an older version is then built again.
MIRROR_VERSION = 2
CANNOT_READ = "the file cannot be read"


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


def _is_documentation_name(name: str) -> bool:
    lower = name.lower()
    return not any(fnmatch.fnmatchcase(lower, pattern) for pattern in NOT_DOCS_NAMES)


def _is_documentation_folder(name: str) -> bool:
    lower = name.lower()
    if name in SKIPPED_FOLDERS or lower in NOT_DOCS_FOLDERS:
        return False
    return not lower.endswith(".egg-info")


def _convertible(source: Path) -> list[Path]:
    """Files that to_markdown can read, sorted. No file is opened."""
    found: list[Path] = []
    for folder, names, files in os.walk(source):
        names[:] = sorted(n for n in names if _is_documentation_folder(n))
        for name in sorted(files):
            if Path(name).suffix.lower() not in OTHER_SUFFIXES:
                continue
            if not _is_documentation_name(name):
                continue
            path = Path(folder) / name
            if path.is_symlink() and not _inside(path, source):
                continue
            found.append(path)
    return found


def markdown_files(source: Path) -> list[Path]:
    """The `.md` files that the search reads, sorted. No file is opened."""
    source = Path(source)
    return sorted(
        path
        for path in source.rglob("*.md")
        if path.is_file() and not (path.is_symlink() and not _inside(path, source))
    )


def convertible_paths(source: Path) -> list[Path]:
    """The files of other types that a mirror converts, sorted."""
    return _convertible(Path(source))


def other_files(source: Path) -> dict[str, int]:
    """Count the files that to_markdown can read, by suffix (small letters)."""
    counts: dict[str, int] = {}
    for path in _convertible(Path(source)):
        suffix = path.suffix.lower()
        counts[suffix] = counts.get(suffix, 0) + 1
    return counts


def _free_name(source: Path, target: Path, relative: Path) -> Path:
    """The name of a converted file: one that no file of the folder has."""
    name = relative.with_name(relative.name + ".md")
    number = 0
    while (source / name).is_file() or (target / name).exists():
        number += 1
        end = ".converted.md" if number == 1 else f".converted{number}.md"
        name = relative.with_name(relative.name + end)
    return name


def build_mirror(source: Path, target: Path) -> MirrorReport:
    """Write the mirror of `source` into `target`. `target` must be empty."""
    source, target = Path(source), Path(target)
    if _inside(target, source):
        raise ValueError("the mirror folder must not be inside the source folder")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("the mirror folder must be empty or missing")
    target.mkdir(parents=True, exist_ok=True)
    report = MirrorReport(source=source, folder=target, markdown=0)
    for path in markdown_files(source):
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(path, destination)
        except OSError:
            report.skipped.append((relative.as_posix(), CANNOT_READ))
            continue
        report.markdown += 1
    for path in _convertible(source):
        relative = path.relative_to(source)
        shown = relative.as_posix()
        try:
            if not path.is_file():  # a broken link, a pipe or a device
                report.skipped.append((shown, CANNOT_READ))
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                report.skipped.append((shown, "the file is larger than 5 MB"))
                continue
            text = to_markdown(path)
        except FormatError as error:
            report.skipped.append((shown, str(error)))
            continue
        except OSError:  # no permission to read
            report.skipped.append((shown, CANNOT_READ))
            continue
        except Exception as error:  # noqa: BLE001  one bad file never stops the work
            reason = f"the file cannot be converted ({type(error).__name__})"
            report.skipped.append((shown, reason))
            continue
        destination = target / _free_name(source, target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        suffix = path.suffix.lower()
        report.converted[suffix] = report.converted.get(suffix, 0) + 1
    return report
