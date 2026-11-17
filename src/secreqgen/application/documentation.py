"""Use case: prepare a documentation folder that holds other file types.

The search reads `.md` files only. With other_formats on, a mirror folder
holds each file of another type as a `.md` file. With it off, or with no file
to convert, the folder is used as it is. That is the tested path. A run with
converted files is outside the tested setup.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from secreqgen.application.errors import InputError

if TYPE_CHECKING:
    from secreqgen.documents.mirror import MirrorReport

CONVERTED_END = ".converted.md"


@dataclass
class PreparedDocs:
    """A documentation folder that is ready for the search."""

    folder: Path  # give this folder to the search
    report: MirrorReport | None  # None: the folder is used as it is
    temporary: bool = False  # True: close() removes the folder

    def close(self) -> None:
        """Remove a temporary mirror. Safe to call two times."""
        if self.temporary:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.temporary = False


def convertible_files(docs: Path) -> dict[str, int]:
    """Count the files of other types that a run would convert, by suffix.

    It opens no file. An unreadable folder gives an empty result.
    """
    from secreqgen.documents.mirror import other_files

    try:
        return other_files(Path(docs))
    except OSError:
        return {}


def shown_name(file_name: str) -> str:
    """Return the name of the real file for the name of a mirror file.

    "guide/install.rst.md" gives "guide/install.rst". The name of a file that
    got a clash name ("install.rst.converted.md") gives "install.rst". Any
    other name stays as it is.
    """
    from secreqgen.documents.formats import OTHER_SUFFIXES

    lower = file_name.lower()
    for suffix in OTHER_SUFFIXES:
        if lower.endswith(suffix + CONVERTED_END):
            return file_name[: -len(CONVERTED_END)]
        if lower.endswith(suffix + ".md"):
            return file_name[:-3]
    return file_name


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def docs_note(report: MirrorReport | None) -> str | None:
    """Return one plain sentence about converted files, or None."""
    if report is None:
        return None
    total = sum(report.converted.values())
    if total:
        parts = ", ".join(
            f"{suffix} {count}"
            for suffix, count in sorted(
                report.converted.items(), key=lambda item: (-item[1], item[0])
            )
        )
        if total == 1:
            start = "1 file of another type was"
        else:
            start = f"{total} files of other types were"
        text = f"{start} converted to Markdown ({parts})."
    else:
        text = "No file of other types could be converted to Markdown."
    skipped = len(report.skipped)
    if skipped:
        verb = "was" if skipped == 1 else "were"
        text += f" {_plural(skipped, 'file')} {verb} skipped."
    return text + " This is outside the tested setup."


def _cache_key(docs: Path) -> str:
    """Hash the names, sizes and times of the files that a mirror reads."""
    from secreqgen.documents.formats import OTHER_SUFFIXES
    from secreqgen.documents.mirror import SKIPPED_FOLDERS

    found: dict[str, Path] = {}
    for path in docs.rglob("*.md"):  # a .md file in a skipped folder is copied too
        if path.is_file():
            found[path.relative_to(docs).as_posix()] = path
    for folder, names, files in os.walk(docs):
        names[:] = sorted(n for n in names if n not in SKIPPED_FOLDERS)
        for name in sorted(files):
            if Path(name).suffix.lower() in OTHER_SUFFIXES:
                path = Path(folder) / name
                found[path.relative_to(docs).as_posix()] = path
    entries = []
    for relative, path in found.items():
        stat = path.stat()
        entries.append((relative, stat.st_size, stat.st_mtime_ns))
    entries.sort()
    digest = hashlib.sha256(json.dumps(entries).encode("utf-8"))
    return digest.hexdigest()[:16]


def _report_to_json(report: MirrorReport) -> str:
    return json.dumps(
        {
            "markdown": report.markdown,
            "converted": report.converted,
            "skipped": [list(item) for item in report.skipped],
        }
    )


def _report_from_json(text: str, source: Path, folder: Path) -> MirrorReport:
    from secreqgen.documents.mirror import MirrorReport

    data = json.loads(text)
    return MirrorReport(
        source=source,
        folder=folder,
        markdown=int(data["markdown"]),
        converted={str(k): int(v) for k, v in data["converted"].items()},
        skipped=[(str(a), str(b)) for a, b in data["skipped"]],
    )


def prepare_documentation(
    docs: Path,
    *,
    other_formats: bool = False,
    cache_dir: Path | None = None,
) -> PreparedDocs:
    """Return the folder that the search must read.

    With other_formats off, or with no file to convert, the folder is returned
    as it is. Else a mirror is built. With cache_dir, the mirror stays in
    cache_dir/<key> and a second call with the same files uses it again. A
    ValueError or an OSError of the mirror becomes InputError.
    """
    docs = Path(docs)
    if not other_formats:
        return PreparedDocs(docs, None)
    from secreqgen.documents.mirror import build_mirror, other_files

    try:
        if not other_files(docs):
            return PreparedDocs(docs, None)
        if cache_dir is None:
            target = Path(tempfile.mkdtemp(prefix="secreqgen-docs-"))
            try:
                report = build_mirror(docs, target)
            except BaseException:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return PreparedDocs(target, report, temporary=True)
        key = _cache_key(docs)
        target = Path(cache_dir) / key
        report_file = Path(cache_dir) / f"{key}.json"
        if target.is_dir() and report_file.is_file():
            try:
                report = _report_from_json(
                    report_file.read_text(encoding="utf-8"), docs, target
                )
                return PreparedDocs(target, report)
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # a damaged report: build the mirror again
        if target.exists():
            shutil.rmtree(target)
        report_file.unlink(missing_ok=True)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        report = build_mirror(docs, target)
        report_file.write_text(_report_to_json(report), encoding="utf-8")
        return PreparedDocs(target, report)
    except (ValueError, OSError) as e:
        raise InputError(f"Cannot prepare the documentation folder: {e}") from e
