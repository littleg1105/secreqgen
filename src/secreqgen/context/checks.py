"""Plain code that checks the answer of the model against the files.

What the checks prove: each item names a real part of the files, and each
technology is named in the files. What they do not prove: that the part says
what a feature or a kind of data says. A person checks that.
"""

from __future__ import annotations

import re
from typing import Any

from secreqgen.context.scan import CodebaseFacts

MAX_DESCRIPTION_WORDS = 40
MAX_ITEM_WORDS = 12
MAX_TEXT_CHARS = 300
MAX_TECH = 12
MAX_GROUPS = 6
MAX_GROUP_ITEMS = 6
MAX_LEVEL_ITEMS = 8
LEVELS = ("critical", "high", "medium")
_PROFILE_RE = re.compile(r"^\s*(critical|high|medium|low)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z]{3,}")
# Words that many product names share. Alone they are no proof of a technology.
GENERIC_WORDS = frozenset(
    "apache amazon aws google microsoft azure oracle cloud server service services "
    "core framework database the and for with open source engine platform storage "
    "queue cache web api sdk library net sql data app application".split()
)
# Other names of a technology that files use.
ALIASES = {"postgresql": "postgres", "kubernetes": "k8s"}
SHORT_WORD = 3  # a word of this length or less must stand alone in the text
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def _text(value: Any) -> str:
    """The value as one short line of text; anything that is not text gives ''."""
    if not isinstance(value, str):
        return ""
    return " ".join(value[: 4 * MAX_TEXT_CHARS].split())[:MAX_TEXT_CHARS]


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _cut(text: str, limit: int) -> str:
    return " ".join(text.split()[:limit])


def _needed_words(value: str) -> list[str]:
    """The words of a technology name that the files must hold.

    Generic words (server, cloud, core) do not count when the name has other
    words. A name with no word of three letters (Go, C#, H2) counts as it is.
    """
    words = [w.lower() for w in _WORD_RE.findall(value)]
    special = [w for w in words if w not in GENERIC_WORDS]
    return special or words or [value.lower()]


class _Text:
    """The text of one part, ready for the search of a technology name."""

    def __init__(self, text: str) -> None:
        self.lower = text.lower()
        # The words of the text. A joined name gives its words: ServiceBus
        # gives "service" and "bus".
        self.words = frozenset(w.lower() for w in _CAMEL_RE.findall(text))


def _holds(word: str, text: _Text) -> bool:
    """True when the text names the word. A short word must stand alone."""
    for name in (word, ALIASES.get(word, "")):
        if not name:
            continue
        if not name.isalpha():  # H2, C#: the name as it is, with a clear end
            pattern = rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9+#])"
            if re.search(pattern, text.lower):
                return True
        elif len(name) > SHORT_WORD:
            if name in text.lower:
                return True
        elif name in text.words:
            return True
    return False


def _find_part(value: str, named: str, texts: dict[str, _Text]) -> str | None:
    """The ref of the part that names the technology, or None.

    Each needed word of the name must be in the files. The source is the part
    that holds the most of these words; the part that the model named wins
    when it holds as many.
    """
    words = _needed_words(value)
    if not all(any(_holds(w, text) for text in texts.values()) for w in words):
        return None
    order = [named] + [r for r in texts if r != named]
    return max(order, key=lambda ref: sum(_holds(w, texts[ref]) for w in words))


def check_answer(
    answer: dict, facts: CodebaseFacts, product: str | None
) -> tuple[dict, list[dict]]:
    """Return the checked context and the list of dropped items."""
    answer = _dict(answer)
    paths = {p.ref: p.path for p in facts.parts}
    texts = {p.ref: _Text(p.text) for p in facts.parts}
    dropped: list[dict] = []
    seen: set[str] = set()

    def known(item: dict, what: str) -> str | None:
        source = _text(item.get("source"))
        if source not in paths:
            dropped.append({"what": what, "why": "unknown source name"})
            return None
        return source

    def fresh(what: str) -> bool:
        key = what.lower()
        if key in seen:
            dropped.append({"what": what, "why": "two times"})
            return False
        seen.add(key)
        return True

    def short(text: str, what: str) -> bool:
        if not text or len(text.split()) > MAX_ITEM_WORDS:
            dropped.append({"what": what, "why": "too long" if text else "empty"})
            return False
        return True

    name = _text(answer.get("name")) or _text(product) or facts.name
    description = _cut(_text(answer.get("description")), MAX_DESCRIPTION_WORDS)
    tech: list[dict] = []
    for raw in _list(answer.get("tech_stack")):
        item = _dict(raw)
        value, kind = _text(item.get("value")), _text(item.get("kind"))
        what = f"tech_stack: {value}"
        if not value or not known(item, what):
            continue
        if not short(value, what):
            continue
        ref = _find_part(value, _text(item.get("source")), texts)
        if ref is None:
            dropped.append({"what": what, "why": "not in the files"})
            continue
        if not fresh(f"tech:{value}"):
            continue
        if len(tech) >= MAX_TECH:
            dropped.append({"what": what, "why": "over the limit"})
            continue
        tech.append(
            {"kind": kind or "other", "value": value, "source": ref, "path": paths[ref]}
        )
    features: list[dict] = []
    for raw in _list(answer.get("features")):
        group = _dict(raw)
        label = _cut(_text(group.get("group")), 6)
        items: list[dict] = []
        for entry in _list(group.get("items")):
            item = _dict(entry)
            text = _text(item.get("text"))
            what = f"feature: {text}"
            ref = known(item, what)
            if ref is None or not short(text, what) or not fresh(f"f:{text}"):
                continue
            if len(items) >= MAX_GROUP_ITEMS:
                dropped.append({"what": what, "why": "over the limit"})
                continue
            items.append({"text": text, "source": ref, "path": paths[ref]})
        if not items or not label:
            continue
        if len(features) >= MAX_GROUPS:
            dropped.append({"what": f"group: {label}", "why": "over the limit"})
            continue
        features.append({"group": label, "items": items})
    data: dict[str, list[dict]] = {level: [] for level in LEVELS}
    sensitivity = _dict(answer.get("data_sensitivity"))
    for level in LEVELS:
        for entry in _list(sensitivity.get(level)):
            item = _dict(entry)
            text = _text(item.get("text"))
            what = f"data_sensitivity {level}: {text}"
            ref = known(item, what)
            if ref is None or not short(text, what) or not fresh(f"d:{text}"):
                continue
            if len(data[level]) >= MAX_LEVEL_ITEMS:
                dropped.append({"what": what, "why": "over the limit"})
                continue
            data[level].append({"text": text, "source": ref, "path": paths[ref]})
    profile = _text(answer.get("security_profile"))
    match = _PROFILE_RE.match(profile)
    if match:
        profile = match[1].capitalize() + profile[match.end() :]
    else:
        dropped.append(
            {
                "what": "security_profile",
                "why": "must start with Critical, High, Medium or Low",
            }
        )
        profile = ""
    context = {
        "name": name,
        "description": description,
        "tech_stack": tech,
        "security_profile": profile,
        "features": features,
        "data_sensitivity": data,
    }
    return context, dropped
