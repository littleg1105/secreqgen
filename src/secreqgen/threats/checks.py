# ruff: noqa: E501
"""Script checks of the threat list. No model is called here."""

from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_BLOCK_MARK_RE = re.compile(r"(?m)^[ \t]*(?:(?:>[ \t]?)+|#{1,6}[ \t]+)")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
_SPACE_RE = re.compile(r"\s+")
_ELLIPSIS_RE = re.compile(r"\.\.\.|…")
# General English and RFC 2119 words. The two lists were written before the test stories were run and were not changed after.
_RISK_WORDS = (
    r"attack\w*|malicious\w*|rogue|untrusted|vulnerab\w*|exploit\w*|expos\w*|leak\w*|steal\w*|stolen|spoof\w*|forg\w*|"
    r"tamper\w*|hijack\w*|imperson\w*|bypass\w*|brute\w*|compromis\w*|unauthori[sz]\w*|insecure\w*|unsafe|risk\w*|"
    r"danger\w*|warning|caution|trick\w*|fraud\w*|prevent\w*|protect\w*"
)
_RULE_WORDS = (
    r"must|shall|should|required|recommended|never|do not|don't|cannot|can't|only|ensure|make sure|avoid\w*|"
    r"not allowed|forbidden|mandatory|important\w*"
)
_RISK_RE = re.compile(r"(?<![A-Za-z])(?:" + _RISK_WORDS + r")(?![A-Za-z])", re.I)
_RULE_RE = re.compile(r"(?<![A-Za-z])(?:" + _RULE_WORDS + r")(?![A-Za-z])", re.I)


def extract_json(text: str) -> dict:
    """The JSON object of an answer. Raise ValueError when there is none."""
    candidates: list[str] = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    candidates.extend(match.group(1).strip() for match in _FENCE_RE.finditer(text))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found in the answer")


def normalise(text: str) -> str:
    """The form of a text for the quote check: small letters, straight quote marks, no Markdown marks."""
    text = text.lower()
    for curly, straight in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"')):
        text = text.replace(curly, straight)
    text = _BLOCK_MARK_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _ESCAPE_RE.sub(r"\1", text)
    text = text.replace("*", "").replace("`", "").replace("|", " ")
    return _SPACE_RE.sub(" ", text).strip()


def check_quote(quote: str, source_text: str) -> bool:
    """True when the quote is a part of the source text. An ellipsis in the quote splits it into parts that must follow each other."""
    if not isinstance(quote, str) or not quote.strip():
        return False
    source = normalise(source_text)
    if not _ELLIPSIS_RE.search(quote):
        return normalise(quote) in source
    position = 0
    checked = 0
    for part in _ELLIPSIS_RE.split(quote):
        part = normalise(part)
        if len(part.split()) < 2:
            continue
        found = source.find(part, position)
        if found == -1:
            return False
        position = found + len(part)
        checked += 1
    return checked > 0


def has_risk_word(text: str) -> bool:
    """True when the text holds a word that names a risk (attack, bypass, expose and the like)."""
    return _RISK_RE.search(text.replace("*", "").replace("`", "")) is not None


def signal_of(quote: str) -> str:
    """"risk" when the quote holds a risk word, "rule" when it holds a rule word (must, never, only ...), else "".

    In the tests of 17 November 2026 a row with no such word was wrong about two times more often.
    The value orders the rows of the page. It removes no row.
    """
    plain = quote.replace("*", "").replace("`", "")
    if _RISK_RE.search(plain):
        return "risk"
    return "rule" if _RULE_RE.search(plain) else ""
