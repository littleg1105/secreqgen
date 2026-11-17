"""Checks and readers for the input files of the use cases.

The functions raise errors. They never print.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from secreqgen.application.errors import InputError


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
