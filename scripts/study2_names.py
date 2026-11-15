"""Code-like names in generated requirements, and where the project sources mention them.

Text search only: no model and no judgment. Used by
  - scripts/build_study2_review_sheet_v2.py (the table of names in each evidence file)
  - the open screen for invented names (analysis of all Study 2 requirements)

A "name" is a piece of text that looks like a setting, an option, a command, a
file, an endpoint or a class. The search is case-sensitive and looks for the
whole name. It reads the documentation folder of the project (file contents
and file names), the system description that each generator received
(system_context.yaml), and the source repository (file contents and file
names). On request it also reads the passages that the retrieval step stored
for a story. The review sheet never uses those passages, because only some
study arms received them.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

RTC = Path("/Users/georgeg/Documents/Uni Docs/Thesis/coded_project/real_test_cases")
STUDY2 = RTC / "study2"
PROJECTS = ["mastodon", "keycloak", "bitwarden"]

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


def extract_names(text: str, vocab: set[str]) -> list[str]:
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


class ProjectSources:
    """The documentation, the stored passages and the repository of one project."""

    def __init__(self, project: str):
        data = json.loads((STUDY2 / project / "stories.json").read_text(encoding="utf-8"))
        self.project = project
        self.docs_rel = data["docs_dir"]
        self.docs_dir = RTC / self.docs_rel
        self.repo_dir = RTC / "repos" / data["repo_dir"]
        if not (self.repo_dir / ".git").exists():
            raise FileNotFoundError(f"not a git repository: {self.repo_dir}")
        self.stories = {s["id"]: s for s in data["stories"]}
        self.docs: dict[str, list[str]] = {
            str(p.relative_to(self.docs_dir)): p.read_text(encoding="utf-8", errors="replace").splitlines()
            for p in sorted(self.docs_dir.rglob("*.md"))
        }
        self.vocab: set[str] = set()
        for lines in self.docs.values():
            for line in lines:
                for span in _BACKTICK.findall(line):
                    span = span.strip(_STRIP)
                    if span and not re.search(r"\s", span):
                        self.vocab.add(span)
                        self.vocab.add(span.lstrip("-"))
                for m in _FLAG.finditer(line):
                    self.vocab.add(m.group(0).lstrip("-"))
        for s in self.stories.values():
            self.vocab.update(s["fact_sheet"]["identifiers"])
        # Passages stored by the retrieval step: one context for each generator model.
        self.frozen: dict[str, list[str]] = {sid: [] for sid in self.stories}
        for rdir in sorted((STUDY2 / project / "results").glob("*/")):
            fp = rdir / "retrieval_frozen.json"
            if fp.exists():
                for sid, entry in json.loads(fp.read_text(encoding="utf-8")).items():
                    self.frozen.setdefault(sid, []).append(entry.get("context", ""))
        # The system description that each generator received, the same for each arm.
        sc = RTC / project / "system_context.yaml"
        self.system_text: str = sc.read_text(encoding="utf-8") if sc.exists() else ""
        self._repo_cache: dict[str, list[str]] = {}
        self._repo_files: list[str] | None = None

    # -- documentation -------------------------------------------------------
    def doc_hits(self, form: str, files: list[str] | None = None, whole: bool = False) -> list[tuple[str, int]]:
        """Lines that contain `form`. With whole=True the match must be a whole name (see whole_rx)."""
        rx = whole_rx(form) if whole else None
        hits = []
        for fname, lines in self.docs.items():
            if files is not None and fname not in files:
                continue
            for n, line in enumerate(lines, start=1):
                if form in line and (rx is None or rx.search(line)):
                    hits.append((fname, n))
        return hits

    def doc_files(self, form: str) -> list[str]:
        """Files of the documentation folder whose path or file name equals `form`."""
        f = form.lstrip("/")
        return [p for p in self.docs if p == f or p.endswith("/" + f)]

    def in_system_description(self, form: str) -> bool:
        return whole_rx(form).search(self.system_text) is not None

    def window(self, fname: str, line: int, before: int = 1, after: int = 1) -> str:
        lines = self.docs[fname]
        lo, hi = max(1, line - before), min(len(lines), line + after)
        return " ".join(part.strip() for part in lines[lo - 1 : hi] if part.strip())

    # -- repository ----------------------------------------------------------
    def _git(self, *args: str) -> str:
        res = subprocess.run(
            ["git", "-C", str(self.repo_dir), *args], capture_output=True, text=True, errors="replace", timeout=180
        )
        if res.returncode not in (0, 1):  # 1 means "no match" for git grep
            raise RuntimeError(f"git {args[0]} failed in {self.repo_dir}: {res.stderr.strip()[:300]}")
        return res.stdout

    def repo_hits(self, form: str, limit: int = 3) -> list[str]:
        """Lines 'path:line:text' of the repository that hold `form` as a whole name (text files only)."""
        if form not in self._repo_cache:
            rx = whole_rx(form)
            lines = self._git("grep", "-I", "-n", "-F", "-w", "-e", form).splitlines()
            self._repo_cache[form] = [ln for ln in lines if rx.search(ln.split(":", 2)[-1])]
        return self._repo_cache[form][:limit] if limit else self._repo_cache[form]

    def repo_file_count(self, form: str) -> int:
        return len({line.split(":", 1)[0] for line in self.repo_hits(form, 0)})

    def repo_paths(self, form: str) -> list[str]:
        """Files of the repository whose path or file name equals `form`."""
        if self._repo_files is None:
            self._repo_files = self._git("ls-files").splitlines()
        f = form.lstrip("/")
        return [p for p in self._repo_files if p == f or p.endswith("/" + f)]

    # -- one name ------------------------------------------------------------
    def lookup(self, name: str, story_id: str, use_frozen: bool = False) -> dict:
        """Where is this name? Status: documentation, documentation file, system description,
        stored passage, source code, not found.

        "source code" means the repository of the project: the content of a file or the name of a
        file. The repository can hold documentation sources that are not in the documentation folder.
        "stored passage" is used only with use_frozen=True (the open screen). The review sheet calls
        this function with use_frozen=False, so its evidence does not depend on a study arm.
        """
        forms = search_forms(name)
        in_system = any(self.in_system_description(form) for form in forms)
        for form in forms:
            hits = self.doc_hits(form, whole=True)
            if hits:
                return {"name": name, "status": "documentation", "form": form, "doc_hits": hits, "in_system": in_system}
        for form in forms:
            files = self.doc_files(form)
            if files:
                return {"name": name, "status": "documentation file", "form": form, "doc_files": files, "in_system": in_system}
        if in_system:
            return {"name": name, "status": "system description", "form": name}
        if use_frozen:
            for form in forms:
                rx = whole_rx(form)
                if any(rx.search(ctx) for ctx in self.frozen.get(story_id, [])):
                    return {"name": name, "status": "stored passage", "form": form}
        # A file name: show the file itself before a line that only mentions it.
        if _FILE.fullmatch(name):
            paths = self.repo_paths(name)
            if paths:
                return {"name": name, "status": "source code", "form": name,
                        "repo_lines": [f"file: {p}" for p in paths[:2]], "repo_files": len(paths)}
        for form in forms:
            if self.repo_hits(form, 1):
                return {"name": name, "status": "source code", "form": form,
                        "repo_lines": self.repo_hits(form, 2), "repo_files": self.repo_file_count(form)}
        for form in forms:
            paths = self.repo_paths(form)
            if paths:
                return {"name": name, "status": "source code", "form": form,
                        "repo_lines": [f"file: {p}" for p in paths[:2]], "repo_files": len(paths)}
        return {"name": name, "status": "not found", "form": name}


def requirement_text(req: dict) -> str:
    """The fields of a requirement that a rater reads (the same fields that the sheet shows)."""
    parts = [req.get("title") or "", req.get("description") or "", req.get("rationale") or ""]
    parts.extend(req.get("acceptance_criteria") or [])
    parts.append(req.get("implementation_notes") or "")
    return "\n".join(str(p) for p in parts)
