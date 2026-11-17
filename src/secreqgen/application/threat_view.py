"""Reading helpers for the result view of a threat list.

The rules repeat the rules of the review page (threats.md). The page code is
frozen, so a test compares both. Domain packages load inside the functions.
"""

from __future__ import annotations

import json
import re

from secreqgen.application.errors import InputError

DISCLAIMER_NOTE = (
    "The second model read the passage and named a fault in these rows "
    "(a different feature than the story, or a threat that says more than the "
    "passage). In the tests such rows were wrong more often than the rows "
    "above. Read them with more care."
)
NO_VERDICT_NOTE = "The check call gave no answer for these rows."
WEAK_SIGN_NOTE = (
    "A script found a sign of a weak row. In the tests such rows were wrong "
    "more often. Read them with more care."
)
KIND_TEXT = {
    "attack": "attack that the documentation describes",
    "rule": "breach of a security rule of the documentation",
}
WEAK_SIGN_TEXTS = [
    "the row is a rule row, but its quote has no rule word "
    "(must, never, only ...) and no risk word",
    "the threat sentence holds words that are not in the passage: w1, w2, ...",
    "the quote is a fragment of 5 words or fewer",
    "the label step gave no STRIDE category: possibly no security threat",
]
_ORDER = ("attack", "rule")


def threat_labels() -> dict:
    """Return the label texts of the review page."""
    from secreqgen.threats.actors import ACTORS, STRIDE
    from secreqgen.threats.pipeline import HARMS

    return {
        "actors": dict(ACTORS),
        "stride": dict(STRIDE),
        "harms": list(HARMS),
        "kinds": dict(KIND_TEXT),
        "weak_sign_texts": list(WEAK_SIGN_TEXTS),
    }


def threat_limits() -> dict:
    """Return the limits and the tested names of the threat list."""
    from secreqgen.threats.pipeline import (
        ABOUT_MAX_WORDS,
        PROMPT_SET,
        TESTED_CHECKER,
        TESTED_WRITER,
    )

    return {
        "about_max_words": ABOUT_MAX_WORDS,
        "tested_writer": TESTED_WRITER,
        "tested_checker": TESTED_CHECKER,
        "prompt_set": PROMPT_SET,
    }


def _numbered(rows: list[dict], number: int) -> tuple[list[dict], int]:
    """Number the rows: all attacks first, then all rules."""
    out = []
    for kind in _ORDER:
        for row in rows:
            if row["type"] == kind:
                number += 1
                out.append({"number": number, "row": row})
    return out, number


def group_threat_rows(model: dict) -> dict:
    """Group and number the rows as the review page does."""
    rows = model["rows"]
    checker = model.get("checker")
    if checker:
        first = [r for r in rows if r.get("second_model") == "accepts"]
        second = [r for r in rows if r.get("second_model") == "refuses"]
        third = [r for r in rows if r.get("second_model") not in ("accepts", "refuses")]
        keys = ("accepts", "refuses", "no_verdict")
        titles = (
            f"Rows that the second model ({checker}) accepts ({len(first)})",
            f"Rows where the second model sees a fault ({len(second)})",
            f"Rows with no verdict of the second model ({len(third)})",
        )
        notes = (None, DISCLAIMER_NOTE, NO_VERDICT_NOTE)
    else:
        first = [r for r in rows if not r.get("weak_signs")]
        second = [r for r in rows if r.get("weak_signs")]
        third = []
        keys = ("no_weak_sign", "weak_sign", "")
        titles = (
            f"Rows with no weak sign ({len(first)})",
            f"Rows with a weak sign ({len(second)})",
            "",
        )
        notes = (None, WEAK_SIGN_NOTE, None)
    groups = []
    number = 0
    for index, members in enumerate((first, second, third)):
        if index > 0 and not members:
            continue  # only the first group shows when it is empty
        numbered, number = _numbered(members, number)
        groups.append(
            {
                "key": keys[index],
                "title": titles[index],
                "note": notes[index],
                "rows": numbered,
            }
        )
    calls = model["calls"]
    failed = sum(1 for c in calls if not c.get("ok"))
    notices = []
    if failed:
        notices.append(
            f"WARNING: {failed} of {len(calls)} model calls gave no valid "
            "answer. The list is not complete."
        )
    if not model["passages"]:
        notices.append("The word search found no documentation passage for this story.")
    elif not rows and not failed:
        notices.append(
            "The documentation passages state no threat and no security rule "
            "for this story."
        )
    elif not first:
        notices.append("No row of this kind.")
    footer = (
        f"Passages read: {len(model['passages'])} in {len(calls)} model calls. "
        f"Model: {model.get('model')}. Time: {model['seconds']} s. "
        f"Rows that the script removed: {len(model['dropped'])}."
    )
    if model.get("about"):
        footer += (
            "\nThe model that writes the rows also read one paragraph about the "
            f"product from the user ({len(model['about'].split())} words). "
            "It is background and not a source: each row has its quote from a "
            "documentation passage."
        )
    return {"groups": groups, "notices": notices, "footer": footer}


# ---------------------------------------------------------------- the quote

_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_LINK_TARGET_RE = re.compile(r"(?<=\]\()[^)]*(?=\))")
_QUOTE_FOLD = str.maketrans(
    {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'"}
)
_SPLIT_RE = re.compile(r"\.\.\.|…")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    """Return (word, start, end) for each word. Offsets are in the given text."""
    folded = text.translate(_QUOTE_FOLD)
    folded = _LINK_TARGET_RE.sub(lambda m: " " * len(m.group()), folded)
    return [(m.group().lower(), m.start(), m.end()) for m in _WORD_RE.finditer(folded)]


def locate_quote(passage_text: str, quote: str) -> list[tuple[int, int]]:
    """Return the character spans of the quote in the passage text.

    The match ignores spacing, letter case, curly quotes, Markdown marks and
    the target of a Markdown link. A quote with "..." or an ellipsis gives one
    span for each part. The result is empty when a part is not found.
    """
    words = _tokens(passage_text)
    names = [w[0] for w in words]
    spans: list[tuple[int, int]] = []
    position = 0
    for part in _SPLIT_RE.split(quote):
        wanted = [w[0] for w in _tokens(part)]
        if len(wanted) < 2:
            continue
        found = -1
        for i in range(position, len(names) - len(wanted) + 1):
            if names[i : i + len(wanted)] == wanted:
                found = i
                break
        if found < 0:
            return []
        last = found + len(wanted) - 1
        spans.append((words[found][1], words[last][2]))
        position = last + 1
    return spans


def split_passage(passage: dict) -> dict:
    """Split a passage into page, section and body.

    The body has no "### [n] ..." line. Its relevance number is a fixed
    filler, so it is never shown.
    """
    header = passage.get("header", "")
    page, _, section = header.partition(" > ")
    text = passage.get("text", "")
    if text.startswith("### ["):
        _, _, text = text.partition("\n")
    return {
        "ref": passage.get("ref", ""),
        "page": page,
        "section": section,
        "group": passage.get("group"),
        "body": text,
    }


def story_status(model: dict) -> str:
    """Return "ok", "partial", "failed" or "empty"."""
    if not model.get("passages"):
        return "empty"
    calls = model.get("calls") or []
    failed = sum(1 for c in calls if not c.get("ok"))
    if calls and failed == len(calls):
        return "failed"
    return "partial" if failed else "ok"


# ---------------------------------------------------------------- export


def _kept_rows(model: dict, verdicts: dict[str, str]) -> list[dict]:
    pages = {p["ref"]: split_passage(p) for p in model["passages"]}
    out = []
    for group in group_threat_rows(model)["groups"]:
        for item in group["rows"]:
            row = item["row"]
            if verdicts.get(row["id"]) != "keep":
                continue
            part = pages.get(row["source"], {})
            out.append(
                {
                    **row,
                    "number": item["number"],
                    "page": part.get("page", ""),
                    "section": part.get("section", ""),
                }
            )
    return out


def _markdown_row(row: dict, labels: dict, header: str) -> list[str]:
    lines = [f"**{row['number']}. {row['threat']}**", ""]
    lines.append(f"- Kind: {labels['kinds'].get(row['type'], row['type'])}")
    if row.get("actor"):
        text = labels["actors"].get(row["actor"], row["actor"])
        lines.append(
            f"- Threat actor: {text} (the model's choice, not from the documentation)"
        )
    if row.get("stride") in labels["stride"]:
        text = labels["stride"][row["stride"]]
        lines.append(f"- STRIDE: {text} (the model's choice)")
    if row.get("harm"):
        lines.append(f"- Harm: {row['harm']}")
    if row.get("story_part"):
        lines.append(f'- Part of your story: "{row["story_part"]}"')
    quote = " ".join(row["quote"].split())
    lines.append(f'- Evidence: "{quote}" ({header})')
    lines.append(f"- Requirement: {row['requirement']}")
    for sign in row.get("weak_signs") or []:
        lines.append(f"- Weak sign: {sign}")
    lines.append("")
    return lines


def export_kept(model: dict, verdicts: dict[str, str], *, fmt: str) -> str:
    """Return the rows that the reader kept, as JSON or Markdown text."""
    if fmt not in ("json", "markdown"):
        raise InputError(f"Unknown export format: {fmt}")
    kept = _kept_rows(model, verdicts)
    if fmt == "json":
        data = {
            "draft_notice": model["draft_notice"],
            "story": model["story"],
            "product": model.get("product"),
            "model": model.get("model"),
            "checker": model.get("checker"),
            "prompt_set": model.get("prompt_set"),
            "kept": kept,
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    labels = threat_labels()
    story = model["story"]
    out = [
        f"# Kept rows for user story {story['id']}: {story['title']}",
        "",
        model["draft_notice"],
        "",
        f"**Story:** {story['story']}",
        "",
        f"## Rows that the reader kept ({len(kept)})",
        "",
    ]
    for row in kept:
        header = row["page"]
        if row["section"]:
            header = f"{header} > {row['section']}"
        out += _markdown_row(row, labels, header)
    return "\n".join(out) + "\n"
