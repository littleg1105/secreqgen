"""Two script measures with ONE fixed reference, for the prompt lab.

The measures say whether the code-like names that a requirement uses are in the material that the
OLD tool gave the model. The reference is the same for every prompt version, so a version that
changes the passage block is not helped or hurt by its own block. The name rules are copies of
`scripts/study2_names.py` (not imported, not changed). Importing this module has no side effect.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass

from secreqgen.generation.prompts import get_json_schema
from tuning_lib.passages import code_passages, doc_passages, split_context
from tuning_lib.prompts import BASELINE, PromptBuilder, PromptError


class LabMeasureError(Exception):
    """A lab measure cannot be computed."""


# Standard technology words. They are not names of one project.
GENERIC_WORDS = {
    "JavaScript", "TypeScript", "GitHub", "GitLab", "PostgreSQL", "MySQL", "MariaDB", "OpenID", "OpenSSL",
    "OpenSSH", "mTLS", "IdP", "IdPs", "WebAuthn", "macOS", "iOS", "DevOps", "DevSecOps", "OAuth", "OAuth2",
    "NodeJS", "WebSocket", "WebSockets", "OpenAPI", "SaaS", "PaaS", "IaaS", "JWTs", "JWKs", "URLs", "URIs",
    "APIs", "IDs", "IPs", "CIDRs", "PINs", "OTPs", "TOTPs", "CAs", "ACLs", "SDKs", "HSMs", "CDNs", "VMs",
    "PKCE", "eIDAS", "OWASP", "CloudFront", "CloudFlare", "Cloudflare", "DynamoDB", "MongoDB", "SQLite",
    "LinkedIn", "YouTube", "PayPal", "OpenTelemetry", "JSON", "YAML", "WebDAV", "IPv4", "IPv6", "HTTPs",
    "MitM", "MITM", "DoS", "DDoS", "TLSv1", "TLSv1.0", "TLSv1.1", "TLSv1.2", "TLSv1.3", "SSLv3",
    # plain words that models put in backticks
    "true", "false", "null", "none", "env", "database", "Host", "host", "issuer", "Key", "key", "Organization",
    "Account", "user", "User", "admin", "password", "token", "secret", "config", "default",
}
# Parts that show an ordinary English compound ("end-to-end", "man-in-the-middle").
SMALL_WORDS = {
    "in", "of", "to", "the", "and", "by", "on", "or", "at", "for", "a", "an", "as", "is", "be", "per",
    "with", "from", "up", "out", "off", "all", "one", "two", "non", "not", "no", "re", "pre", "post",
}
SKIP_DOTTED = {"e.g", "i.e", "etc", "vs", "a.k.a"}

_BACKTICK = re.compile(r"`([^`\n]{2,100})`")
_FLAG = re.compile(r"(?<![\w-])--[A-Za-z][\w-]*")
_ENV = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_MIXED = re.compile(r"\b[A-Za-z0-9]*[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_DOTTED = re.compile(r"\b[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*)+\b")
_PATH = re.compile(r"(?<![\w./:])/[A-Za-z0-9_{}<>:.\-]+(?:/[A-Za-z0-9_{}<>:.\-]+)+")
_KEBAB = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?![\w-])")
_FILE = re.compile(
    r"(?<![\w./-])(?:[\w.-]+/)*[\w-]+\.(?:rb|java|cs|ts|tsx|js|py|go|rs|md|ya?ml|conf|json|xml|properties|sh|erb|haml)\b"
)
_STRIP = " \t.,;:()[]{}\"'`*"
# A placeholder for a value that the user fills in: <S3_BUCKET_NAME>, <realm>, <bucket-name>.
_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>|<[a-z][a-z0-9_-]*>")
_REGEX_CHARS = set("[]\\^*+|()")


def whole_rx(form: str) -> re.Pattern:
    """A pattern for `form` as a whole name. The character before it must not be a letter, a digit or
    an underscore. The text after it must not continue the name: no letter, digit, underscore or
    hyphen, and no dot that a letter or digit follows. So "keycloak.conf" does not match
    "keycloak.config.resolver", and "--import" does not match "--import-realm"."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(form) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])")


def _is_domain(token: str) -> bool:
    return "example." in token or token.endswith((".com", ".org", ".net", ".io")) or token.startswith("www.")


def _tidy(token: str) -> str | None:
    """Clean one candidate. Return None when it is not a usable name."""
    token = token.strip(" \t.,;:\"'`*")
    if token.startswith("${") and not token.endswith("}"):
        token += "}"
    if len(token) > 1 and token.endswith("/"):
        token = token.rstrip("/")
    if sum(ch.isalnum() for ch in token) < 3:
        return None
    if any(ch in _REGEX_CHARS for ch in token):
        return None  # a regular expression or a code fragment, not a name
    if token.count("<") != token.count(">"):
        return None  # a piece of a generic type such as "Handler<TRequirement"
    if token in GENERIC_WORDS or token in SKIP_DOTTED:
        return None
    if token.replace(".", "").replace("-", "").isdigit() or _is_domain(token):
        return None
    return token


def extract_names(text: str, vocab: AbstractSet[str]) -> list[str]:
    """Return the code-like names in `text`, sorted, each one time.

    `vocab` is the set of names that the project documentation writes as code.
    A lower-case hyphen word ("proxy-headers") counts as a name only when it is
    in `vocab`, when it stands in backticks, when it starts with "--", or when
    it has three or more parts and no small English word among them.
    For "name=value" the name alone is also a candidate.
    """
    found: set[str] = set()
    text = _PLACEHOLDER.sub(" ", text)  # a placeholder is not a name
    for span in _BACKTICK.findall(text):
        if re.search(r"[<>(){}]", span) and ("," in span or re.search(r"\s", span)):
            continue  # a generic type or a call such as `Foo<A, B>`: the patterns below take its parts
        for part in span.split(","):
            part = part.strip()
            if part and not re.search(r"\s", part):
                found.add(part)
    plain = text.replace("`", " ")
    for rx in (_FLAG, _ENV, _SNAKE, _PATH, _FILE):
        found.update(m.group(0) for m in rx.finditer(plain))
    for m in _MIXED.finditer(plain):
        found.add(m.group(0))
    for m in _DOTTED.finditer(plain):
        token = m.group(0)
        # "-Dkeycloak.x=y" is a Java option: the name is "keycloak.x".
        if token.startswith("D") and m.start() > 0 and plain[m.start() - 1] == "-" and len(token) > 1 and token[1].islower():
            token = token[1:]
        found.add(token)
    for m in _KEBAB.finditer(plain):
        token = m.group(0)
        parts = token.split("-")
        if token in vocab or (len(parts) >= 3 and not any(p in SMALL_WORDS for p in parts)):
            found.add(token)
    for token in list(found):
        if "=" in token:
            left = token.split("=", 1)[0]
            if len(left.lstrip("-")) >= 3:
                found.add(left)
    out = set()
    for token in found:
        token = _tidy(token)
        if token:
            out.add(token)
    return sorted(out)


def search_forms(name: str) -> list[str]:
    """The text forms to search for one name: as written, and without leading dashes.
    The form without dashes is used only when it still looks like a name ("proxy-headers"),
    not when it is an ordinary word ("--log" gives "log")."""
    forms = [name]
    bare = name.lstrip("-")
    if bare != name and len(bare) >= 3 and re.search(r"[-_.]", bare):
        forms.append(bare)
    return forms


def requirement_text(req: Mapping) -> str:
    """The fields of a requirement that a rater reads (the same fields that the sheet shows)."""
    parts = [req.get("title") or "", req.get("description") or "", req.get("rationale") or ""]
    parts.extend(req.get("acceptance_criteria") or [])
    parts.append(req.get("implementation_notes") or "")
    return "\n".join(str(p) for p in parts)


@dataclass(frozen=True)
class Reference:
    """The fixed reference text of one story and model, and the vocabulary of its documentation."""

    story_id: str
    model: str
    text: str
    sha256: str
    vocab: frozenset[str]
    code_text: str = ""  # the code-search hits of the v0_old block (a second fixed text, not in `text`)


def _vocab(doc_texts: Sequence[str]) -> frozenset[str]:
    out: set[str] = set()
    for text in doc_texts:
        for line in text.splitlines():
            for span in _BACKTICK.findall(line):
                span = span.strip(_STRIP)
                if span and not re.search(r"\s", span):
                    out.add(span)
                    out.add(span.lstrip("-"))
            for m in _FLAG.finditer(line):
                out.add(m.group(0).lstrip("-"))
    return frozenset(out)


def build_reference(story_id: str, model: str, *, builder: PromptBuilder | None = None) -> Reference:
    """Build the reference of a story and a model. An unknown story raises PromptError."""
    b = builder if builder is not None else PromptBuilder()
    story = b.story(story_id)  # raises PromptError for an unknown id
    block = b.stored_block(story_id, model)
    passages = split_context(block)
    docs = [p.text for p in doc_passages(passages)]
    code_hits = [p.text for p in code_passages(passages)]
    story_text = "\n".join([story.title, story.story, *story.acceptance_criteria])
    system_prompt_path = b.version(BASELINE).folder / "system_prompt.txt"
    try:
        system_prompt = system_prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LabMeasureError(f"cannot read {system_prompt_path.name} of {BASELINE}: {exc}") from exc
    parts = [
        "\n\n".join(docs),
        story_text,
        b.system_context(story.project),
        system_prompt,
        get_json_schema(),
    ]
    text = "\n\n".join(parts)
    return Reference(
        story_id=story_id,
        model=model,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        vocab=_vocab(docs),
        code_text="\n\n".join(code_hits),
    )


def baseline_prompt_text(story_id: str, model: str, *, builder: PromptBuilder | None = None) -> str:
    """The system text and the user text without the passage block of the v0_old prompt of one story and model."""
    b = builder if builder is not None else PromptBuilder()
    built = b.build(story_id, model, BASELINE)
    return built.system + "\n" + built.user_non_doc


def name_in_reference(name: str, ref: Reference) -> bool:
    """True when any search form of the name is a whole name in the reference text."""
    return any(whole_rx(form).search(ref.text) for form in search_forms(name))


def name_in_code_hits(name: str, ref: Reference) -> bool:
    """True when any search form of the name is a whole name in the code-search hits of the v0_old block."""
    return bool(ref.code_text) and any(whole_rx(form).search(ref.code_text) for form in search_forms(name))


@dataclass(frozen=True)
class RequirementNames:
    """The names of one requirement and those that are outside the reference."""

    index: int
    names: tuple[str, ...]
    outside: tuple[str, ...]
    outside_code: tuple[str, ...] = ()  # the outside names that the v0_old code hits hold


@dataclass(frozen=True)
class NameCheck:
    """Counts over the requirements of one record. The denominator is always n_requirements."""

    n_requirements: int
    n_with_names: int
    n_outside: int
    n_all_inside: int
    per_requirement: tuple[RequirementNames, ...]
    n_outside_code: int = 0

    @property
    def outside_share(self) -> float:
        return self.n_outside / self.n_requirements if self.n_requirements else 0.0

    @property
    def outside_code_share(self) -> float:
        """Requirements whose outside names are all in the v0_old code hits (a part of outside_share)."""
        return self.n_outside_code / self.n_requirements if self.n_requirements else 0.0

    @property
    def outside_none_share(self) -> float:
        """Requirements with an outside name that is in no given text (a part of outside_share)."""
        return (self.n_outside - self.n_outside_code) / self.n_requirements if self.n_requirements else 0.0

    @property
    def all_inside_share(self) -> float:
        return self.n_all_inside / self.n_requirements if self.n_requirements else 0.0

    @property
    def no_name_share(self) -> float:
        if not self.n_requirements:
            return 0.0
        return (self.n_requirements - self.n_with_names) / self.n_requirements


def check_requirements(requirements: Sequence[Mapping], ref: Reference) -> NameCheck:
    """Count requirements with names outside the reference, with all names inside, and with none."""
    rows: list[RequirementNames] = []
    for i, req in enumerate(requirements):
        names = extract_names(requirement_text(req), ref.vocab)
        outside = [n for n in names if not name_in_reference(n, ref)]
        in_code = [n for n in outside if name_in_code_hits(n, ref)]
        rows.append(RequirementNames(i, tuple(names), tuple(outside), tuple(in_code)))
    with_names = [r for r in rows if r.names]
    n_outside = sum(1 for r in with_names if r.outside)
    return NameCheck(
        n_requirements=len(rows),
        n_with_names=len(with_names),
        n_outside=n_outside,
        n_all_inside=len(with_names) - n_outside,
        per_requirement=tuple(rows),
        n_outside_code=sum(1 for r in with_names if r.outside and len(r.outside_code) == len(r.outside)),
    )
