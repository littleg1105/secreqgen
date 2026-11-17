"""Checks and readers for the input files of the use cases.

The functions raise errors. They never print.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from secreqgen.application.errors import InputError, NothingToDoError

if TYPE_CHECKING:
    from secreqgen.generation.schemas import UserStory

# The limit of the paragraph about the product (the same as the threat list
# pipeline has). It is a copy, so that this module loads no domain package.
ABOUT_MAX_WORDS = 120
UPLOAD_EXTENSIONS = (".json", ".yaml", ".yml")


def check_input_file(path: Path) -> None:
    """Raise InputError when the input file does not exist."""
    if not path.exists():
        raise InputError(f"Input file not found: {path}")


def check_docs_folder(docs: Path) -> None:
    """Raise InputError when the documentation folder is not a folder."""
    if not docs.is_dir():
        raise InputError(f"Documentation folder not found: {docs}")


def check_docs_option(docs: Path | None, baseline: bool) -> None:
    """Check the --docs option and its use with --baseline.

    The tested setup has no zero-shot mode. No test ran its prompt without
    the passages.
    """
    if docs is None:
        return
    check_docs_folder(docs)
    if baseline:
        raise InputError("--baseline cannot be used with --docs.")


def read_story_data(
    path: Path, *, encoding: str | None = None, require_object: bool = False
) -> Any:
    """Read a YAML or JSON file. Every error passes through unchanged."""
    import json

    import yaml

    content = path.read_text(encoding=encoding)
    if path.suffix.lower() in [".yaml", ".yml"]:
        data = yaml.safe_load(content)
    else:
        data = json.loads(content)
    if require_object and not isinstance(data, dict):
        raise ValueError("the file must hold an object")
    return data


def build_user_stories(items: list) -> tuple[list, list[tuple[int, Exception]]]:
    """Build UserStory objects from the entries of the file.

    Return the stories and the skipped entries. A skipped entry is a pair:
    the position of the entry (from 1) and the error.
    """
    from secreqgen.generation.schemas import UserStory

    stories = []
    skipped: list[tuple[int, Exception]] = []
    for i, story_data in enumerate(items):
        try:
            story = UserStory(
                id=story_data.get("id", f"US-{i+1:03d}"),
                title=story_data.get("title", "Untitled"),
                story=story_data.get("story", ""),
                acceptance_criteria=story_data.get("acceptance_criteria", []),
            )
            stories.append(story)
        except Exception as e:
            skipped.append((i + 1, e))
    return stories, skipped


@dataclass(frozen=True)
class StoryFile:
    """The content of a story file, read with the parser of the run."""

    project: str | None
    stories: list[UserStory]
    skipped: list[tuple[int, str]]  # position from 1, message


def read_story_file(path: Path) -> StoryFile:
    """Read a story file and return its product name and its stories.

    It uses the same parser as the run functions. It raises InputError when
    the file is missing, cannot be parsed or is not an object. It raises
    NothingToDoError when the file has no stories.
    """
    from secreqgen.application.threats import threat_product_name

    check_input_file(path)
    try:
        data = read_story_data(path, encoding="utf-8", require_object=True)
        items = data.get("user_stories", []) or []
        if not isinstance(items, list):
            raise ValueError("user_stories must be a list")
    except Exception as e:
        raise InputError(f"Error parsing input file: {e}") from e
    stories, skipped = build_user_stories(items)
    if not stories:
        raise NothingToDoError("No user stories found in input file.")
    return StoryFile(
        project=threat_product_name(None, data),
        stories=stories,
        skipped=[(position, str(error)) for position, error in skipped],
    )


def check_about(text: str | None) -> str | None:
    """Check the paragraph about the product.

    Return the text with its white space folded. Return None when the text is
    empty or blank. Raise InputError when it has more than 120 words.
    """
    if text is None or not text.strip():
        return None
    folded = " ".join(text.split())
    words = len(folded.split())
    if words > ABOUT_MAX_WORDS:
        raise InputError(
            f"The paragraph about the product has {words} words. "
            f"The limit is {ABOUT_MAX_WORDS}."
        )
    return folded


def check_story_upload(name: str, size: int, max_bytes: int = 1_000_000) -> None:
    """Check the name and the size of an uploaded story file.

    Raise InputError for an extension other than .json, .yaml or .yml, or for
    a file that is larger than max_bytes.
    """
    if Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
        raise InputError("The file must be a .json, .yaml or .yml file.")
    if size > max_bytes:
        raise InputError(
            f"The file is too large: {size} bytes. The limit is {max_bytes} bytes."
        )
