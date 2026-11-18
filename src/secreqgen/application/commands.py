"""The command line command that gives the same run as a GUI form.

The GUI does not run the command line. It calls the functions of this
package. For each run there is one command of the program secreqgen that
gives the same run. These builders make that command from the values that the
GUI gives to the run.

The first rule: the command must be true. An option is left out only when the
default of the command line gives the same value. Where the command line
cannot do what the GUI does, the code of the difference is in
Command.differences. No file is read or written here.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

PROGRAM = "secreqgen"

# The codes of Command.differences.
STORIES_SUBSET = "stories_subset"  # the command cannot pick these stories
PROMPT_SET = "prompt_set"  # the command has no option for a prompt set

# The variables that the tool reads (the prefix of the LLM settings).
BASE_URL_VARIABLE = "SECREQGEN_LLM_BASE_URL"
MODEL_VARIABLE = "SECREQGEN_LLM_MODEL_NAME"

# The rest of a path below the home folder may be written with a ~ when it
# has only these characters.
_TILDE_REST = re.compile(r"[A-Za-z0-9._/-]+")


@dataclass(frozen=True)
class Command:
    """One command for a shell.

    env: the variables in front of the command, in order.
    argv: the program and its arguments, as real values (no quotes, no ~).
    text: one line for a shell.
    differences: what the command cannot do.
    """

    env: tuple[tuple[str, str], ...]
    argv: tuple[str, ...]
    text: str
    differences: tuple[str, ...]


def _quote(value: str) -> str:
    """Return the value as one word for a shell.

    shlex.quote leaves a word as it is when it has only safe characters. The
    equals sign is one of them. But zsh, the shell of macOS, reads a word that
    starts with = as the name of a program. So such a word gets quotes too.
    """
    quoted = shlex.quote(value)
    if quoted == value and value.startswith("="):
        return f"'{value}'"
    return quoted


def _shell_path(path: Path, home: Path) -> str:
    """Return the text of a path for a shell.

    A path below the home folder is written as ~/rest when the rest has only
    safe characters. Any other path is written whole and quoted when needed.
    """
    try:
        if str(home) != "/" and path.is_absolute() and path.is_relative_to(home):
            rest = path.relative_to(home).as_posix()
            if rest != "." and _TILDE_REST.fullmatch(rest):
                return f"~/{rest}"
    except (ValueError, OSError):
        pass
    return _quote(str(path))


def _path_value(path: Path) -> str:
    """Return the argument for a path. A path that starts with - gets ./ in front.

    Without it the parser would read a first argument as an option.
    """
    text = str(path)
    return "./" + text if text.startswith("-") else text


class _Line:
    """The pieces of one command: each piece has its real value and its shell text."""

    def __init__(self, home: Path) -> None:
        self._home = home
        self.env: list[tuple[str, str]] = []
        self.argv: list[str] = [PROGRAM]
        self._shown: list[str] = [PROGRAM]

    def word(self, *words: str) -> None:
        """Add words that are plain values."""
        for word in words:
            self.argv.append(word)
            self._shown.append(_quote(word))

    def path(self, path: Path) -> None:
        """Add a path."""
        value = _path_value(path)
        self.argv.append(value)
        if value != str(path):
            self._shown.append(_quote(value))
        else:
            self._shown.append(_shell_path(path, self._home))

    def variable(self, name: str, value: str) -> None:
        self.env.append((name, value))

    def build(self, differences: Sequence[str]) -> Command:
        front = [f"{name}={_quote(value)}" for name, value in self.env]
        return Command(
            env=tuple(self.env),
            argv=tuple(self.argv),
            text=" ".join(front + self._shown),
            differences=tuple(differences),
        )


def _home(home: Path | None) -> Path:
    return Path.home() if home is None else Path(home)


def _settings_default(name: str) -> str:
    """Return the default of a field of the LLM settings of the tool."""
    from secreqgen.config import LLMConfig

    return LLMConfig.model_fields[name].default


def _add_address(line: _Line, base_url: str) -> None:
    """Put the address of Ollama in front, when it is not the default.

    The tool builds its client with the address of its settings, so the
    variable SECREQGEN_LLM_BASE_URL wins over OLLAMA_HOST.
    """
    if base_url != _settings_default("base_url"):
        line.variable(BASE_URL_VARIABLE, base_url)


def _story_option(
    story_ids: Sequence[str] | None, total_stories: int, *, one_id_option: bool
) -> tuple[str | None, list[str]]:
    """Return the id for --story-id (or None) and the codes of the differences.

    No ids, or all ids: no option. One id: --story-id (threats only). Any
    other choice cannot be said on the command line.
    """
    ids = list(dict.fromkeys(story_ids or ()))
    if not ids or len(ids) >= total_stories:
        return None, []
    if len(ids) == 1 and one_id_option:
        return ids[0], []
    return None, [STORIES_SUBSET]


def threat_command(
    *,
    stories_file: Path,
    docs: Path,
    product: str | None,
    default_product: str | None,
    total_stories: int,
    story_ids: Sequence[str] | None,
    writer_model: str,
    check: bool,
    checker_model: str | None,
    labels: bool,
    about_file: Path | None,
    prompt_set: str | None,
    other_formats: bool,
    base_url: str,
    home: Path | None = None,
) -> Command:
    """Build the command of `secreqgen generate threats`."""
    from secreqgen.threats import TESTED_WRITER, ThreatListError, choose_models

    line = _Line(_home(home))
    _add_address(line, base_url)
    line.word("generate", "threats")
    line.path(Path(stories_file))
    line.word("--docs")
    line.path(Path(docs))
    if product is not None and product != default_product:
        line.word("--product", product)
    story_id, differences = _story_option(story_ids, total_stories, one_id_option=True)
    if story_id is not None:
        line.word("--story-id", story_id)
    writer = (writer_model or "").strip()
    if writer and writer != TESTED_WRITER:
        line.word("--model", writer_model)
    if not check:
        line.word("--no-check")
    elif checker_model and checker_model.strip():
        try:
            _, default_checker = choose_models(writer or None, None, None, True)
        except ThreatListError:
            default_checker = None
        if checker_model != default_checker:
            line.word("--checker-model", checker_model)
    if not labels:
        line.word("--no-labels")
    if about_file is not None:
        line.word("--about-file")
        line.path(Path(about_file))
    if other_formats:
        line.word("--other-formats")
    if prompt_set is not None:
        differences.append(PROMPT_SET)
    return line.build(differences)


def requirements_command(
    *,
    stories_file: Path,
    docs: Path,
    product: str | None,
    default_product: str | None,
    total_stories: int,
    story_ids: Sequence[str] | None,
    model: str,
    system_context: Path | None,
    prompt_set: str | None,
    other_formats: bool,
    base_url: str,
    home: Path | None = None,
) -> Command:
    """Build the command of `secreqgen generate requirements`.

    The command has no option for the model. It reads the setting
    SECREQGEN_LLM_MODEL_NAME, so the variable stands in front of it.

    default_product is what the command takes with no --product: the key
    project of the file, as the command reads it. When that key is not text
    the command gives the raw value to the search, so the caller passes None
    and the command names the product.
    """
    line = _Line(_home(home))
    _add_address(line, base_url)
    if model != _settings_default("model_name"):
        line.variable(MODEL_VARIABLE, model)
    line.word("generate", "requirements")
    line.path(Path(stories_file))
    line.word("--docs")
    line.path(Path(docs))
    if product is not None and product != default_product:
        line.word("--product", product)
    if system_context is not None:
        line.word("--context")
        line.path(Path(system_context))
    if other_formats:
        line.word("--other-formats")
    _, differences = _story_option(story_ids, total_stories, one_id_option=False)
    if prompt_set is not None:
        differences.append(PROMPT_SET)
    return line.build(differences)


def context_command(
    *,
    folder: Path,
    product: str | None,
    writer_model: str,
    base_url: str,
    home: Path | None = None,
) -> Command:
    """Build the command of `secreqgen generate context`.

    The command writes the draft to the console. It has no output option here.
    """
    from secreqgen.threats import TESTED_WRITER

    line = _Line(_home(home))
    _add_address(line, base_url)
    line.word("generate", "context")
    line.path(Path(folder))
    writer = (writer_model or "").strip()
    if writer and writer != TESTED_WRITER:
        line.word("--model", writer_model)
    if product is not None and product.strip():
        line.word("--product", product)
    return line.build([])


def about_command(
    *,
    folder: Path,
    product: str | None,
    writer_model: str,
    base_url: str,
    home: Path | None = None,
) -> Command:
    """Build the command of `secreqgen generate about` for a codebase folder.

    The rules are those of context_command. The paragraph goes to the console.
    """
    from secreqgen.threats import TESTED_WRITER

    line = _Line(_home(home))
    _add_address(line, base_url)
    line.word("generate", "about")
    line.path(Path(folder))
    writer = (writer_model or "").strip()
    if writer and writer != TESTED_WRITER:
        line.word("--model", writer_model)
    if product is not None and product.strip():
        line.word("--product", product)
    return line.build([])
