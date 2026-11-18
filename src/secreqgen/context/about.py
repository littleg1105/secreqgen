"""The paragraph "About the product", made from a checked system context.

The paragraph is made by a fixed rule, with plain code. No model writes it.
It is the rule of the test of 17 November 2026: name, description, main
parts and features, about 80 words.

The rule:

- "<name>: <description>." (the description loses its end period)
- " Main parts: <group>, <group>." (each "_" in a group name is a space)
- " Features: <feature>; <feature>." (each feature loses its end period)

The budget is ABOUT_BUDGET. Each word costs 1. Each feature costs 1 more.
The features come in the order of the groups. The first feature that does not
fit ends the list. The paragraph never has more than ABOUT_MAX_WORDS words.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from secreqgen.context.scan import ContextError

ABOUT_BUDGET = 90
ABOUT_DRAFT_NOTICE = (
    "DRAFT for a person to check. A model wrote the facts of this paragraph "
    "from the files of the product. Check each line against its source before "
    "you use it."
)
ABOUT_FILE_NOTICE = (
    "SecReqGen made this paragraph from the system context file. No model was used."
)


@dataclass(frozen=True)
class AboutDraft:
    """The paragraph, the lines that it uses and the features left out."""

    paragraph: str  # ABOUT_MAX_WORDS words at most
    lines: tuple[dict, ...]  # {"where", "text", "path"}, in order
    left_out: tuple[dict, ...]  # the features that are not in the paragraph


def _max_words() -> int:
    from secreqgen.threats.pipeline import ABOUT_MAX_WORDS  # noqa: PLC0415

    return ABOUT_MAX_WORDS


def _group_names(groups: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    names = [" ".join(str(group).replace("_", " ").split()) for group, _ in groups]
    return [name for name in names if name]


def _head(name: str, description_words: list[str], names: list[str]) -> list[str]:
    """The words of "<name>: <description>." and of the main parts."""
    description = " ".join(description_words)
    text = f"{name}: {description}." if description else f"{name}."
    if names:
        text += " Main parts: " + ", ".join(names) + "."
    return text.split()


def _build(
    name: str, description: str, groups: Sequence[tuple[str, Sequence[str]]]
) -> tuple[str, list[tuple[int, int]], bool]:
    """Apply the rule. Return the paragraph, the used features and a flag.

    Each used feature is a pair (group number, item number). The flag tells
    if the paragraph still holds words of the description.
    """
    limit = _max_words()
    name = " ".join(str(name).split())
    description_words = str(description).strip().rstrip(".").split()
    names = _group_names(groups)
    words = _head(name, description_words, names)
    # A head that is too long loses words at the end of the description first.
    while len(words) > limit and description_words:
        description_words.pop()
        words = _head(name, description_words, names)
    # Fallbacks that no real context needs: fewer main parts, then fewer words.
    while len(words) > limit and names:
        names.pop()
        words = _head(name, description_words, names)
    words = words[:limit]
    cost = len(words)
    used: list[tuple[int, int]] = []
    parts: list[str] = []
    full = False
    for g, (_group, items) in enumerate(groups):
        for i, item in enumerate(items):
            add = str(item).strip().rstrip(".").split()
            if not add:
                continue
            extra = len(add) + 1 + (0 if used else 1)  # "Features:" costs 1, one time
            if cost + extra > ABOUT_BUDGET:
                full = True
                break
            cost += extra
            used.append((g, i))
            parts.append(" ".join(add))
        if full:
            break
    text = " ".join(words)
    if used:
        text += " Features: " + "; ".join(parts) + "."
    return text, used, bool(description_words)


def about_paragraph(
    name: str, description: str, groups: Sequence[tuple[str, Sequence[str]]]
) -> tuple[str, int]:
    """The fixed rule. Return the paragraph and the number of features used."""
    text, used, _kept = _build(name, description, groups)
    return text, len(used)


def about_from_context(context: dict) -> AboutDraft:
    """Make the draft from the context of SystemContextWriter.generate."""
    if not isinstance(context, dict):
        raise ContextError("The result has no system context.")
    name = str(context.get("name") or "").strip()
    description = str(context.get("description") or "").strip()
    groups = [
        (g["group"], [i["text"] for i in g["items"]])
        for g in context.get("features", [])
    ]
    paragraph, used, kept = _build(name, description, groups)
    chosen = set(used)
    lines: list[dict] = []
    if description and kept:
        lines.append(
            {
                "where": "description",
                "text": description,
                "path": str(context.get("description_path") or ""),
            }
        )
    left_out: list[dict] = []
    for g, group in enumerate(context.get("features", [])):
        where = f"features: {group['group']}"
        for i, item in enumerate(group["items"]):
            line = {"where": where, "text": item["text"], "path": item["path"]}
            if (g, i) in chosen:
                lines.append(line)
            elif " ".join(str(item["text"]).strip().rstrip(".").split()):
                left_out.append(line)
    return AboutDraft(paragraph, tuple(lines), tuple(left_out))


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def about_from_file_data(data: object) -> AboutDraft:
    """Make the draft from the parsed YAML of a system context file.

    Raise ContextError when the file has no name or no feature list.
    """
    head: dict = {}
    if isinstance(data, dict):
        for key in ("project", "system"):
            if isinstance(data.get(key), dict):
                head = data[key]
                break
    name = _text(head.get("name"))
    if not name:
        raise ContextError("The file has no product name.")
    description = _text(head.get("description"))
    table = data.get("features") if isinstance(data, dict) else None
    groups: list[tuple[str, list[str]]] = []
    if isinstance(table, dict):
        for group, items in table.items():
            if not isinstance(items, list):
                continue
            texts = [t for t in (_text(item) for item in items) if t]
            if texts:
                groups.append((_text(group), texts))
    if not groups:
        raise ContextError("The file has no feature list.")
    paragraph, used, kept = _build(name, description, groups)
    chosen = set(used)
    lines: list[dict] = []
    if description and kept:
        lines.append({"where": "description", "text": description, "path": ""})
    left_out: list[dict] = []
    for g, (group, texts) in enumerate(groups):
        for i, text in enumerate(texts):
            line = {"where": f"features: {group}", "text": text, "path": ""}
            if (g, i) in chosen:
                lines.append(line)
            elif " ".join(text.rstrip(".").split()):
                left_out.append(line)
    return AboutDraft(paragraph, tuple(lines), tuple(left_out))
