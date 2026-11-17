"""Prompt sets for a prompt editor.

A prompt set is a folder of prompt files. The tested sets are packaged and
only read. A user set lives under a root folder that the caller gives, as
root/kind/name. Every path stays inside the root. The checks protect a run:
a wrong placeholder in a prompt would crash it or change it in silence.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import string
import tempfile
from dataclasses import dataclass
from pathlib import Path

from secreqgen.application.errors import InputError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_BYTES = 64 * 1024
FILE_NAMES = {
    "threats": (
        "system_prompt.txt",
        "user_prompt.txt",
        "labels_prompt.txt",
        "check_prompt.txt",
    ),
    "requirements": (
        "system_prompt.txt",
        "rag_user_prompt.txt",
        "zero_shot_user_prompt.txt",
    ),
}
UNUSED_FILES = {("requirements", "zero_shot_user_prompt.txt")}
# The code reads these words from the answer. A prompt should still name them.
ANSWER_WORDS = {
    ("threats", "user_prompt.txt"): (
        "rows",
        "passages",
        "source",
        "fits_story",
        "quote",
        "type",
        "threat",
        "harm",
        "story_part",
        "requirement",
    ),
    ("threats", "labels_prompt.txt"): ("actor", "STRIDE"),
    ("threats", "check_prompt.txt"): ("OK", "FAULT"),
    ("requirements", "rag_user_prompt.txt"): ("passage_notes", "requirements"),
}
ERROR_PREFIX = "Error: "
WARNING_PREFIX = "Warning: "


@dataclass(frozen=True)
class PromptFile:
    """One prompt file of a set."""

    name: str
    sha256: str
    size: int
    placeholders: tuple[str, ...]
    used: bool


@dataclass(frozen=True)
class PromptSetInfo:
    """One prompt set."""

    kind: str
    name: str
    folder: Path
    tested: bool
    files: tuple[PromptFile, ...]


@dataclass(frozen=True)
class PromptProblem:
    """A problem in a prompt text. A hard problem stops the save."""

    filename: str
    text: str
    hard: bool


# ---------------------------------------------------------------- helpers


def _check_kind(kind: str) -> tuple[str, ...]:
    if kind not in FILE_NAMES:
        raise InputError(f"Unknown kind of prompt set: {kind}")
    return FILE_NAMES[kind]


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise InputError(
            f"Bad name of a prompt set: {name!r}. Use a to z, 0 to 9, _ and -, "
            "at most 40 characters, and start with a letter or a digit."
        )


def _check_filename(kind: str, filename: str) -> None:
    if filename not in _check_kind(kind):
        raise InputError(f"{filename} is not a prompt file of the kind {kind}.")


def _inside(root: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _set_path(root: Path, kind: str, name: str) -> Path:
    """Return root/kind/name. It must stay inside the root and not be a link."""
    _check_kind(kind)
    _check_name(name)
    path = root / kind / name
    if (root / kind).is_symlink() or path.is_symlink() or not _inside(root, path):
        raise InputError("The prompt set is outside the root folder.")
    return path


def _tested_folder(kind: str) -> Path:
    _check_kind(kind)
    if kind == "threats":
        from secreqgen.threats.pipeline import PROMPT_DIR

        return PROMPT_DIR
    from secreqgen.generation.prompt_sets import TESTED_PROMPT_SET

    return TESTED_PROMPT_SET


def _tested_name(kind: str) -> str:
    if kind == "threats":
        from secreqgen.threats.pipeline import PROMPT_SET

        return PROMPT_SET
    return _tested_folder(kind).name


def _identifiers(text: str) -> tuple[str, ...]:
    try:
        return tuple(sorted(set(string.Template(text).get_identifiers())))
    except ValueError:
        return ()


def _describe(kind: str, folder: Path) -> tuple[PromptFile, ...]:
    files = []
    for filename in FILE_NAMES[kind]:
        path = folder / filename
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        try:
            names = _identifiers(data.decode("utf-8"))
        except UnicodeError:
            names = ()
        if filename == "system_prompt.txt":
            names = ()  # a raw text: no placeholder is replaced
        files.append(
            PromptFile(
                name=filename,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                placeholders=names,
                used=(kind, filename) not in UNUSED_FILES,
            )
        )
    return tuple(files)


def _info(kind: str, name: str, folder: Path) -> PromptSetInfo:
    return PromptSetInfo(
        kind=kind,
        name=name,
        folder=folder,
        tested=is_tested(kind, folder),
        files=_describe(kind, folder),
    )


# ---------------------------------------------------------------- listing


def tested_prompt_sets() -> list[PromptSetInfo]:
    """Return the two tested sets: the threat list and the requirements."""
    return [_info(k, _tested_name(k), _tested_folder(k)) for k in FILE_NAMES]


def list_user_prompt_sets(root: Path, kind: str) -> list[PromptSetInfo]:
    """Return the user sets of a kind, sorted by name."""
    _check_kind(kind)
    parent = root / kind
    if parent.is_symlink() or not parent.is_dir() or not _inside(root, parent):
        return []
    sets = []
    for child in sorted(parent.iterdir()):
        if child.is_symlink() or not child.is_dir() or not NAME_RE.match(child.name):
            continue
        if _inside(root, child):
            sets.append(_info(kind, child.name, child))
    return sets


def is_tested(kind: str, folder: Path) -> bool:
    """Tell if every file is byte-equal to the tested file."""
    tested = _tested_folder(kind)
    for filename in FILE_NAMES[kind]:
        path = folder / filename
        try:
            if path.is_symlink():
                return False
            if path.read_bytes() != (tested / filename).read_bytes():
                return False
        except OSError:
            return False
    return True


# ---------------------------------------------------------------- copy, delete


def copy_prompt_set(kind: str, name: str, root: Path) -> Path:
    """Copy the tested files to root/kind/name. Return the new folder."""
    target = _set_path(root, kind, name)
    if target.exists():
        raise InputError(f"A prompt set with the name {name} exists.")
    source = _tested_folder(kind)
    parent = root / kind
    try:
        parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=".copy-", dir=parent))
        try:
            for filename in FILE_NAMES[kind]:
                shutil.copyfile(source / filename, temp / filename)
            os.replace(temp, target)
        except BaseException:
            shutil.rmtree(temp, ignore_errors=True)
            raise
    except OSError as exc:
        raise InputError(f"Cannot copy the prompt set: {exc}") from exc
    return target


def delete_user_prompt_set(kind: str, name: str, root: Path) -> None:
    """Delete one user set."""
    target = _set_path(root, kind, name)
    if not target.is_dir():
        raise InputError(f"There is no prompt set with the name {name}.")
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise InputError(f"Cannot delete the prompt set: {exc}") from exc


# ---------------------------------------------------------------- checks


def check_prompt_text(kind: str, filename: str, text: str) -> list[PromptProblem]:
    """Check the text of one prompt file against the tested file."""
    _check_filename(kind, filename)

    def hard(message: str) -> PromptProblem:
        return PromptProblem(filename, message, True)

    def soft(message: str) -> PromptProblem:
        return PromptProblem(filename, message, False)

    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        return [hard("The text is not valid UTF-8.")]
    if size > MAX_BYTES:
        return [hard(f"The text is {size} bytes. The limit is {MAX_BYTES} bytes.")]
    if not text.strip():
        return [hard("The text is empty.")]
    problems: list[PromptProblem] = []
    names = _identifiers(text)
    if filename == "system_prompt.txt":
        # The text is sent as it is. A $name is not replaced.
        if names:
            listed = ", ".join("$" + n for n in names)
            problems.append(
                soft(f"{listed} stays in the prompt as written: it is not replaced.")
            )
        return problems
    if not string.Template(text).is_valid():
        return [hard("A $ starts no name. Write $$ for a dollar sign.")]
    tested = (_tested_folder(kind) / filename).read_text(encoding="utf-8")
    expected = _identifiers(tested)
    for name in expected:
        if name not in names:
            problems.append(hard(f"The placeholder ${name} is missing."))
    for name in names:
        if name not in expected:
            problems.append(hard(f"The placeholder ${name} is not known."))
    for word in ANSWER_WORDS.get((kind, filename), ()):
        if word not in text:
            problems.append(
                soft(f'The word "{word}" is missing. The code reads it.')
            )
    return problems


def check_prompt_folder(kind: str, folder: Path) -> None:
    """Raise InputError when a file is missing or has a hard problem."""
    names = _check_kind(kind)
    if not Path(folder).is_dir():
        raise InputError(f"The prompt folder does not exist: {folder}")
    messages = []
    for filename in names:
        path = Path(folder) / filename
        if not path.is_file() or path.is_symlink():
            messages.append(f"{filename}: the file is missing.")
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError):
            messages.append(f"{filename}: the file cannot be read as UTF-8 text.")
            continue
        messages += [
            f"{p.filename}: {p.text}"
            for p in check_prompt_text(kind, filename, text)
            if p.hard
        ]
    if messages:
        raise InputError("The prompt set has problems. " + " ".join(messages))


def write_prompt_file(folder: Path, kind: str, filename: str, text: str) -> list[str]:
    """Check a prompt text, then write it. The write is atomic.

    Return the problems as texts. A hard problem starts with "Error: ". A soft
    problem starts with "Warning: ". With a hard problem nothing is written.
    A bad file name or folder raises InputError.
    """
    _check_filename(kind, filename)
    folder = Path(folder)
    path = folder / filename
    if not folder.is_dir() or folder.is_symlink():
        raise InputError("The prompt folder does not exist.")
    if path.is_symlink() or path.resolve().parent != folder.resolve():
        raise InputError("The prompt file is outside the prompt folder.")
    problems = check_prompt_text(kind, filename, text)
    messages = [
        (ERROR_PREFIX if p.hard else WARNING_PREFIX) + p.text for p in problems
    ]
    if any(p.hard for p in problems):
        return messages
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=folder, prefix=".tmp-", delete=False
        ) as handle:
            temp_name = handle.name
            handle.write(text.encode("utf-8"))
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise InputError(f"Cannot write the prompt file: {exc}") from exc
    return messages


# ---------------------------------------------------------------- the threat prompts


def load_threat_prompts(folder: Path) -> tuple:
    """Read the four threat prompt files.

    Return (system text, user template, labels template, check template).
    """
    texts = []
    for filename in FILE_NAMES["threats"]:
        try:
            texts.append((Path(folder) / filename).read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise InputError(f"Cannot read {filename}: {exc}") from exc
    system, user, labels, check = texts
    return (
        system,
        string.Template(user),
        string.Template(labels),
        string.Template(check),
    )


_GENERATOR_CLASS: list = []


def build_threat_generator_class():
    """Return a ThreatListGenerator class that reads a user prompt folder.

    The tested class is not edited. The new class builds the tested parts
    first, then it replaces the four prompt attributes. It marks its result
    as not the tested prompts.
    """
    if _GENERATOR_CLASS:
        return _GENERATOR_CLASS[0]
    from secreqgen.threats import ThreatListGenerator

    class PromptSetThreatListGenerator(ThreatListGenerator):
        """A threat list generator with the prompts of a user set."""

        def __init__(
            self, llm_client, docs_dir, *, prompts_dir, prompt_label, **kwargs
        ):
            super().__init__(llm_client, docs_dir, **kwargs)
            system, user, labels, check = load_threat_prompts(prompts_dir)
            self._system, self._user = system, user
            self._labels, self._check = labels, check
            self.prompt_label = prompt_label

        def generate(self, user_story):
            model = super().generate(user_story)
            model["prompt_set"] = self.prompt_label
            model["tested_prompts"] = False
            return model

    _GENERATOR_CLASS.append(PromptSetThreatListGenerator)
    return PromptSetThreatListGenerator
