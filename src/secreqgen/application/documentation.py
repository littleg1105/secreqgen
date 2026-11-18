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
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from secreqgen.application.errors import InputError

if TYPE_CHECKING:
    from secreqgen.documents.mirror import MirrorReport

CONVERTED_END = ".converted.md"
_CONVERTED_RE = re.compile(r"\.converted\d*\.md$", re.IGNORECASE)
SHOWN_SKIPPED = 5  # the skipped files that a note names
HALF_BUILT_SECONDS = 3600  # a mirror with no report that is older is removed
KEPT_MIRRORS = 3  # the stored mirrors of other documentation folders that stay


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


@dataclass
class DocsCount:
    """What a documentation folder holds, for a form. No file is opened."""

    markdown: int = 0  # the .md files that the search reads
    in_dependencies: int = 0  # of these, the files in dependency and build folders
    dependency_folders: tuple[str, ...] = ()  # their names, the largest first
    others: dict[str, int] = field(default_factory=dict)  # files a run can convert


def count_documentation(docs: Path) -> DocsCount:
    """Count the files of a documentation folder in one walk.

    The search reads every file whose name ends in ".md", also in a folder
    such as node_modules. The count says how many of them are in such folders,
    so that a person can pick a better folder. An unreadable folder gives an
    empty count.
    """
    from secreqgen.documents.mirror import SKIPPED_FOLDERS

    docs = Path(docs)
    count = DocsCount(others=convertible_files(docs))
    folders: dict[str, int] = {}
    try:
        for base, _dirs, names in os.walk(docs):
            found = sum(1 for name in names if name.endswith(".md"))
            if not found:
                continue
            count.markdown += found
            pieces = Path(base).relative_to(docs).parts
            inside = next((p for p in pieces if p in SKIPPED_FOLDERS), None)
            if inside:
                count.in_dependencies += found
                folders[inside] = folders.get(inside, 0) + found
    except OSError:
        return DocsCount()
    order = sorted(folders, key=lambda name: (-folders[name], name))
    count.dependency_folders = tuple(order)
    return count


def shown_name(file_name: str) -> str:
    """Return the name of the real file for the name of a mirror file.

    "guide/install.rst.md" gives "guide/install.rst". The name of a file that
    got a clash name ("install.rst.converted.md") gives "install.rst". Any
    other name stays as it is.
    """
    from secreqgen.documents.formats import OTHER_SUFFIXES

    clash = _CONVERTED_RE.search(file_name)
    real = file_name[: clash.start()] if clash else file_name[:-3]
    if file_name.lower().endswith(".md") and real.lower().endswith(OTHER_SUFFIXES):
        return real
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
        text += f" {_plural(skipped, 'file')} {verb} skipped: {_skipped_text(report)}."
    return text + " This is outside the tested setup."


def _skipped_text(report: MirrorReport) -> str:
    """The first skipped files with the reason of each."""
    names = [
        f"{name} ({' '.join(str(reason).split())[:60]})"
        for name, reason in report.skipped[:SHOWN_SKIPPED]
    ]
    more = len(report.skipped) - len(names)
    if more > 0:
        names.append(f"and {more} more")
    return ", ".join(names)


def _cache_key(docs: Path) -> str:
    """Hash what a mirror depends on: its files and the version of the code.

    The hash holds the folder, the names, sizes and times of the files that a
    mirror reads, and MIRROR_VERSION. A file that cannot be read counts with
    its name only.
    """
    from secreqgen.documents.mirror import (
        MIRROR_VERSION,
        convertible_paths,
        markdown_files,
    )

    entries = []
    for path in [*markdown_files(docs), *convertible_paths(docs)]:
        relative = path.relative_to(docs).as_posix()
        try:
            stat = path.stat()
            times = (stat.st_mtime_ns, stat.st_ctime_ns)
            entries.append((relative, stat.st_size, *times))
        except OSError:
            entries.append((relative, -1, -1, -1))
    entries.sort()
    head = [MIRROR_VERSION, str(docs.resolve())]
    digest = hashlib.sha256(json.dumps([head, entries]).encode("utf-8"))
    return digest.hexdigest()[:16]


def _remove_old_mirrors(cache_dir: Path, key: str, source: Path) -> None:
    """Keep the disk use small: remove the mirrors that are not needed.

    An older mirror of the same documentation folder is removed at once. Of
    the mirrors of other folders, the newest KEPT_MIRRORS stay. A problem
    with a file here never stops a run.
    """
    others: list[tuple[float, str]] = []
    try:
        reports = sorted(cache_dir.glob("*.json"))
    except OSError:
        return
    for report_file in reports:
        name = report_file.stem
        if name == key:
            continue
        try:
            data = json.loads(report_file.read_text(encoding="utf-8"))
            same = data.get("source") == str(source.resolve())
            age = report_file.stat().st_mtime
        except (OSError, ValueError, AttributeError):
            same, age = True, 0.0  # a damaged entry: remove it
        if same:
            _remove_mirror(cache_dir, name)
        else:
            others.append((age, name))
    for _age, name in sorted(others, reverse=True)[KEPT_MIRRORS:]:
        _remove_mirror(cache_dir, name)
    try:  # a build that stopped in the middle left a folder with no report
        for folder in cache_dir.iterdir():
            if not folder.is_dir() or folder.name == key:
                continue
            if (cache_dir / f"{folder.name}.json").is_file():
                continue
            if time.time() - folder.stat().st_mtime > HALF_BUILT_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


def _remove_mirror(cache_dir: Path, name: str) -> None:
    shutil.rmtree(cache_dir / name, ignore_errors=True)
    try:
        (cache_dir / f"{name}.json").unlink(missing_ok=True)
    except OSError:
        pass


def _report_to_json(report: MirrorReport) -> str:
    return json.dumps(
        {
            "source": str(Path(report.source).resolve()),
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


def _need_text(report: MirrorReport, docs: Path) -> None:
    """Raise InputError when the mirror holds no file that the search can read."""
    if report.markdown or report.converted:
        return
    raise InputError(
        f"No file of the documentation folder can be read: {docs}. "
        f"Skipped: {_skipped_text(report)}."
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
                _need_text(report, docs)
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
                os.utime(report_file)  # this mirror is in use: it stays
                return PreparedDocs(target, report)
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # a damaged report: build the mirror again
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        # Build beside the final place and move at the end: a second run never
        # sees a half-built mirror.
        building = Path(cache_dir) / f"{key}.building-{os.getpid()}"
        shutil.rmtree(building, ignore_errors=True)
        try:
            report = build_mirror(docs, building)
            _need_text(report, docs)
            report_file.unlink(missing_ok=True)
            if target.exists():
                shutil.rmtree(target)
            os.replace(building, target)
        except BaseException:
            shutil.rmtree(building, ignore_errors=True)
            raise
        report.folder = target
        report_file.write_text(_report_to_json(report), encoding="utf-8")
        _remove_old_mirrors(Path(cache_dir), key, docs)
        return PreparedDocs(target, report)
    except InputError:
        raise
    except (ValueError, OSError) as e:
        raise InputError(f"Cannot prepare the documentation folder: {e}") from e
