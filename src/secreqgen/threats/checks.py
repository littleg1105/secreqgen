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


_STOP = frozenset(
    """a an the of to and or in on for with is are be as that this it its by at from so if then not no do does did each all any which who whom
what how when where there their them his her our your my me us into than too very only also both more most such own same other some these those
has have had having been being was were am but nor over under again once here about against between through during before after above below up
down out off further can could may might will would should must shall cannot without within while because until via per using used use uses""".split()
)
# Words that a threat sentence can add with no source: the actor and the plain verbs of a breach.
_FREE = frozenset(
    """attacker attackers attack attacks user users administrator administrators admin admins operator operators developer developers client
clients server servers system systems application applications service services person persons somebody someone anyone organisation organization
product feature features does perform performs performed allow allows allowed allowing fail fails failed failing leading lead leads result results
resulting cause causes causing access accesses accessed accessing gain gains obtain obtains read reads send sends sent use request requests data
information security threat threats breach instead rather properly correctly incorrectly improperly required requirement rule rules documentation
passage story""".split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_\-]{3,}")
_SUFFIXES = ("ations", "ation", "ingly", "ments", "ment", "ness", "ing", "ies", "ied", "ed", "es", "ly", "s")


def _stem(word: str) -> str:
    """A rough word stem: up to two endings are removed ("settings" and "setting" give "sett"), and a final "e"
    ("store", "stores" and "stored" give "stor"). It only has to give the same stem for the forms of one word."""
    word = word.lower().strip("-_")
    for _ in range(2):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word.rstrip("e") if len(word) > 4 else word


_FREE_STEMS = frozenset(_stem(w) for w in _FREE)


def new_words(threat: str, passage: str, story: str) -> list[str]:
    """The content words of a threat sentence that are not in its passage and not in the story (as word stems)."""
    known = {_stem(w) for w in _WORD_RE.findall(passage + " " + story)}
    out: list[str] = []
    for word in _WORD_RE.findall(threat):
        stem = _stem(word)
        if word.lower() in _STOP or word.lower() in _FREE or stem in _FREE_STEMS or stem in known:
            continue
        if stem not in out:
            out.append(stem)
    return out


def weak_signs(row_type: str, quote: str, threat: str, passage: str, story: str, stride: str = "") -> list[str]:
    """The signs of a weak row that a script can see. They order the rows of the page; they remove no row.

    In the tests of 17 November 2026 (215 judged rows) a row with no such sign was wrong for 12 to 32 of 100 rows,
    a row with a sign for 38 to 62 of 100. Most faults that no script can see are sentences about a part of the
    product that the story does not use.
    """
    plain = quote.replace("*", "").replace("`", "")
    signs: list[str] = []
    if row_type != "attack" and not (_RULE_RE.search(plain) or _RISK_RE.search(plain)):
        signs.append("the row is a rule row, but its quote has no rule word (must, never, only ...) and no risk word")
    added = new_words(threat, passage, story)
    if len(added) >= 2:
        signs.append("the threat sentence holds words that are not in the passage: " + ", ".join(added[:4]))
    if len(plain.split()) <= 5:
        signs.append("the quote is a fragment of 5 words or fewer")
    if stride == "none":
        signs.append("the label step gave no STRIDE category: possibly no security threat")
    return signs


_VERDICT_RE = re.compile(r"\b(OK|FAULT)\b")


def keep_or_drop(answer: str) -> str:
    """The last OK or FAULT of a check answer, as "accepts" or "refuses". "" when the answer has neither.

    The words must be in capital letters, as the prompt asks: "ok" in a sentence is not a verdict.
    """
    found = _VERDICT_RE.findall(answer or "")
    return {"OK": "accepts", "FAULT": "refuses"}[found[-1]] if found else ""
