"""Plain code that collects the facts of a codebase folder.

No model runs here. The scan never opens a secret file and never names one.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

SKIPPED_FOLDERS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "third_party",
        "dist",
        "build",
        "target",
        "out",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".idea",
        ".vscode",
        "coverage",
        "site-packages",
        "bower_components",
        "obj",
        "Pods",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".terraform",
        ".gradle",
        ".dart_tool",
        ".cache",
        # folders of tools that hold keys of a person
        ".ssh",
        ".aws",
        ".gnupg",
        ".kube",
        ".azure",
    }
)
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    "*.kdbx",
    "credentials*",
    "secrets.*",
    "*.secret",
    "htpasswd",
    ".htpasswd",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".pgpass",
    ".dockercfg",
    ".git-credentials",
    ".vault-token",
    "id_dsa*",
    "id_ecdsa*",
    "*.ppk",
    "*.p8",
    "*.gpg",
    "*.asc",
    "*.ovpn",
    "*.tfstate",
    "*.tfstate.*",
    "*.tfvars",
    "*.tfvars.json",
    "*.secrets",
    "kubeconfig*",
    "service-account*.json",
    "*serviceaccount*.json",
    "firebase-adminsdk*.json",
    "google-services.json",
    "auth.json",
    "wallet.dat",
    "*.env",
    ".envrc",
    "env.*",
    ".dockerconfigjson",
    ".terraformrc",
    "terraform.rc",
    "deploy_key*",
    "*_rsa",
    "*_ed25519",
    "*.key.*",
    "*vault*.yml",
    "*vault*.yaml",
    "gradle.properties",
    "local.properties",
)
# Code files that hold the secrets of an installation.
CODE_SECRET_NAMES = frozenset({"wp-config.php", "local_settings.py"})
# A file of these folders is a secret file, when it is not a code file.
SECRET_FOLDERS = frozenset(
    {"secrets", ".secrets", "secret", "private_keys", ".private"}
)
# A data file with one of these words in its name is a secret file too.
SECRET_NAME_WORDS = (
    "password",
    "passwd",
    "secret",
    "credential",
    "private_key",
    "private-key",
    "privatekey",
    "token",
    "apikey",
    "api_key",
    "api-key",
)
# The whole name of a data file (with no suffix) that is a secret file.
SECRET_STEMS = frozenset({"pass", "pwd", "pw", "key", "keys", "auth"})
DATA_SUFFIXES = frozenset(
    " .txt .json .yml .yaml .xml .ini .cfg .conf .properties .toml .csv .bak .old "
    ".backup .dump .db .sqlite .enc .zip .tar .gz .tgz .7z .rar".split(" ")
)
MAX_FILES = 20_000
FOLDER_CHUNK = 2_000  # the files of one folder that the walk takes in one turn
MAX_FILE_BYTES = 1_000_000
TOTAL_WORDS = 6_500
MAX_LINE_CHARS = 2_000
TREE_LINES = 90
TREE_SUBFOLDERS = 8
TREE_CHILDREN = 6
README_WORDS = 900
MANIFEST_LINES, MANIFEST_FILES, MANIFEST_DEPTH = 80, 8, 3
CONTAINER_LINES, CONTAINER_FILES = 60, 4
API_LINES, API_FILES = 60, 2
DOCS_WORDS, DOCS_FILES = 400, 6
PATH_LINES = 80
PART_WORDS, PART_CHARS = 600, 6_000  # the limit of one manifest, container or API part
CODE_FOLDERS, CODE_NAMES = 12, 12
MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Gemfile",
        "composer.json",
        "mix.exs",
        "go.work",
        "settings.gradle",
        "settings.gradle.kts",
        "libs.versions.toml",
        "build.sbt",
        "Package.swift",
        "pubspec.yaml",
        "Podfile",
        "environment.yml",
        "deno.json",
        "pnpm-workspace.yaml",
        "CMakeLists.txt",
        "packages.config",
        "Directory.Packages.props",
        "global.json",
    }
)
MANIFEST_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln", ".slnx", ".gemspec")
TEST_WORDS = frozenset(
    {
        "test",
        "tests",
        "testing",
        "spec",
        "specs",
        "e2e",
        "bench",
        "benchmark",
        "benchmarks",
        "perf",
        "fixture",
        "fixtures",
        "mock",
        "mocks",
    }
)
NOT_CODE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".webp",
        ".css",
        ".scss",
        ".less",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".map",
        ".snap",
        ".lock",
    }
)
CONTAINER_PATTERNS = (
    "Dockerfile*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose.yaml",
    "Chart.yaml",
)
API_RE = re.compile(r"^(openapi|swagger).*\.(ya?ml|json)$", re.IGNORECASE)
DOC_FOLDERS = ("docs", "doc", "documentation")
DOC_NAMES = ("readme", "index", "overview", "architecture", "security")
SECURITY_WORDS = (
    "auth",
    "login",
    "session",
    "token",
    "password",
    "permission",
    "role",
    "crypto",
    "oauth",
    "saml",
    "jwt",
    "csrf",
    "cors",
    "tls",
    "secret",
)
CODE_SUFFIXES = frozenset(
    ".py .rb .go .java .kt .scala .cs .fs .vb .js .jsx .mjs .ts .tsx .vue .svelte "
    ".php .rs .swift .m .c .cc .cpp .h .hpp .ex .exs .erl .clj .dart .lua .pl "
    ".graphql .proto".split()
)
NOT_SOURCE_WORDS = ("migrat", "locale", "i18n", "generated")
DIRECT_SUFFIXES = ("", ".md", ".txt")
README_NAMES = ("readme.md", "readme", "readme.rst", "readme.txt", "readme.adoc")
_SECURITY_RE = re.compile("|".join(SECURITY_WORDS).replace("auth", "auth(?!or)"))

REMOVED = "<removed>"
_KEY_WORDS = (
    r"password|passwd|passphrase|pwd|secret|token|credential|"
    r"api[_-]?key|private[_-]?key|access[_-]?key|auth[_-]?key|signing[_-]?key|"
    r"encryption[_-]?key|master[_-]?key|license[_-]?key"
)
# A key name with a secret word, then ":" or "=", then the value on the same line.
# The name must start where a run of name characters starts: the search stays
# fast on a very long line.
_KEY_RE = re.compile(
    r"(?<![\w.-])"
    r"(?P<key>[\w.-]*(?:" + _KEY_WORDS + r")[\w.-]*|[\w.-]+[_-](?:pass|key|salt|pat))"
    r"(?P<sep>[\"']?[ \t]*[:=][ \t]*)"
    r"(?P<val>\"[^\"\n]*\"|'[^'\n]*'|\S+)",
    re.IGNORECASE,
)
_URL_RE = re.compile(
    r"(?<![a-z0-9+.-])(?P<head>[a-z][a-z0-9+.-]*://[^\s/:@]*):(?P<pw>[^\s/@]+)@",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]+-----.*?(?:-----END [A-Z0-9 ]+-----|\Z)", re.DOTALL
)
_AUTH_RE = re.compile(
    r"\b(?P<kind>bearer|basic)(?P<gap>[ \t]+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE
)
# Keys of well-known services. They have a fixed start.
_KNOWN_RE = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bglpat-[A-Za-z0-9_-]{16,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\b[srp]k_(?:live|test)_[A-Za-z0-9]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{30,}"
    r"|\bnpm_[A-Za-z0-9]{30,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*)?"
)
_LONG_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_WORD_RUN_RE = re.compile(r"[a-z]{3,}|[A-Z]{4,}")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_PIECE_RE = re.compile(r"[a-z0-9]+")
_TEST_NAME_RE = re.compile(
    r"^test_|[_.](?:test|tests|spec)$|[a-z0-9](?:Test|Tests|Spec)$"
)
NOT_NAMES = frozenset({"package-info", "__init__", "index", "mod", "main"})
NAME_KINDS = ("tree", "security_paths", "code_folders")  # lists of names, no content
PATHS_LABEL = "(file paths with security words)"


class ContextError(Exception):
    """The system context could not be made."""


@dataclass
class Part:
    """One piece of a file that the model will see."""

    ref: str
    path: str
    kind: str
    text: str


@dataclass
class CodebaseFacts:
    """The facts of one codebase folder."""

    name: str
    files_seen: int
    secret_files_skipped: int
    languages: dict[str, int]
    parts: list[Part] = field(default_factory=list)
    stopped_early: bool = False  # the folder has more files than the scan reads
    suffixes: dict[str, int] = field(default_factory=dict)  # every file type


def is_secret_name(name: str) -> bool:
    """True when the file name is one of the secret file names.

    A data file (.txt, .json, .yml and so on) with a word such as "password"
    or "secret" in its name counts too. A code file does not, with two known
    exceptions: its name says what the product does (credentials_controller.rb).
    """
    lower = name.lower()
    suffix = Path(lower).suffix
    if suffix in CODE_SUFFIXES:
        return lower in CODE_SECRET_NAMES
    if any(fnmatch.fnmatchcase(lower, p.lower()) for p in SECRET_PATTERNS):
        return True
    if suffix not in DATA_SUFFIXES:
        return False
    if Path(lower).stem in SECRET_STEMS:
        return True
    return any(word in lower for word in SECRET_NAME_WORDS)


def has_control_character(name: str) -> bool:
    """True for a name with a line break or another control character."""
    return any(ord(ch) < 32 or ch in "\x7f\x85\u2028\u2029" for ch in name)


def looks_random(token: str) -> bool:
    """True for a long token that looks like a key or a hash, not like a name."""
    if not any(ch.isdigit() for ch in token):
        return False
    core = token.rstrip("=")
    if "+" in core or not any(ch in core for ch in "/_-"):
        return True  # base64 text, or one solid run of letters and digits
    if _HEX_RE.fullmatch(core.replace("-", "")):
        return True  # a hash or a UUID
    in_words = sum(len(run) for run in _WORD_RUN_RE.findall(token))
    return in_words / len(token) < 0.6  # names and paths are mostly words


def remove_secrets(text: str) -> str:
    """Replace values that look like secrets with <removed>."""
    text = _BLOCK_RE.sub(REMOVED, text)
    text = _URL_RE.sub(lambda m: f"{m['head']}:{REMOVED}@", text)
    text = _KEY_RE.sub(lambda m: f"{m['key']}{m['sep']}{REMOVED}", text)
    text = _AUTH_RE.sub(lambda m: f"{m['kind']}{m['gap']}{REMOVED}", text)
    text = _KNOWN_RE.sub(REMOVED, text)
    return _LONG_RE.sub(lambda m: REMOVED if looks_random(m[0]) else m[0], text)


def cut_long_lines(text: str) -> str:
    """Cut each line at MAX_LINE_CHARS. A cut never leaves a part of a word."""
    out: list[str] = []
    for line in text.splitlines():
        if len(line) > MAX_LINE_CHARS:
            head = line[:MAX_LINE_CHARS]
            pieces = head.rsplit(None, 1)
            line = pieces[0] if len(pieces) > 1 else head
        out.append(line)
    return "\n".join(out)


def remove_secrets_in_names(text: str) -> str:
    """The rule for lists of file names: only keys with a fixed start."""
    return _KNOWN_RE.sub(REMOVED, text)


def is_test_path(rel: str) -> bool:
    """True when a folder of the path is a test or benchmark folder."""
    for piece in rel.lower().split("/")[:-1]:
        if any(word in TEST_WORDS for word in _PIECE_RE.findall(piece)):
            return True
    return False


def cut_words(text: str, limit: int) -> str:
    """Keep whole lines while the text has at most `limit` words."""
    out: list[str] = []
    used = 0
    for line in text.splitlines():
        count = len(line.split())
        if used + count > limit:
            if not out:
                out.append(" ".join(line.split()[:limit]))
            break
        out.append(line)
        used += count
    return "\n".join(out)


def _walk(folder: Path) -> tuple[list[str], int, int, bool]:
    """Return the usable files, the count, the secret files, and "stopped early".

    The walk goes level by level and takes at most FOLDER_CHUNK files of one
    folder before it goes on to the next folder. So one very large folder
    cannot hide the folders beside it when the limit ends the walk.
    """
    root = folder.resolve()
    files: list[str] = []
    seen = secrets = 0
    # one entry: a folder, and its files that are left (None: not listed yet)
    queue: deque[tuple[Path, list | None]] = deque([(root, None)])
    while queue:
        base, left = queue.popleft()
        if left is None:
            try:
                entries = sorted(os.scandir(base), key=lambda e: e.name)
            except OSError:
                continue
            left = []
            for entry in entries:
                if has_control_character(entry.name):
                    continue  # such a name could forge lines in the text for the model
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIPPED_FOLDERS:
                            queue.append((Path(entry.path), None))
                    else:
                        left.append(entry)
                except OSError:
                    continue
        if len(left) > FOLDER_CHUNK:
            queue.append((base, left[FOLDER_CHUNK:]))
            left = left[:FOLDER_CHUNK]
        pieces = base.relative_to(root).parts
        secret_folder = any(piece.lower() in SECRET_FOLDERS for piece in pieces)
        for entry in left:
            try:
                if entry.is_symlink():
                    target = Path(entry.path).resolve()
                    inside = target.relative_to(root)
                    if not target.is_file():
                        continue
                    if any(piece in SKIPPED_FOLDERS for piece in inside.parts):
                        continue  # a link into a folder that the scan never reads
                    if any(is_secret_name(piece) for piece in inside.parts):
                        seen += 1
                        secrets += 1  # a link with a harmless name to a secret file
                        continue
                elif not entry.is_file(follow_symlinks=False):
                    continue  # a pipe, a socket or a device
            except (ValueError, OSError):
                continue
            if seen >= MAX_FILES:
                return files, seen, secrets, True
            seen += 1
            in_secret_folder = (
                secret_folder and Path(entry.name).suffix.lower() not in CODE_SUFFIXES
            )
            if is_secret_name(entry.name) or in_secret_folder:
                secrets += 1
                continue
            files.append(Path(entry.path).relative_to(root).as_posix())
    return files, seen, secrets, False


def _safe_file(folder: Path, rel: str) -> Path | None:
    """The path of a file that the scan may open now, or None.

    The check runs again at the time of the read: a file that became a link
    to a secret file or to a place outside the folder is not opened.
    """
    path = folder / rel
    try:
        if path.is_symlink():
            root = folder.resolve()
            inside = path.resolve().relative_to(root)
            if any(piece in SKIPPED_FOLDERS for piece in inside.parts):
                return None
            if any(is_secret_name(piece) for piece in inside.parts):
                return None
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
    except (ValueError, OSError):
        return None
    return path


def _read(folder: Path, rel: str) -> str | None:
    """Read a text file, or return None when it is too large or unreadable."""
    path = _safe_file(folder, rel)
    if path is None:
        return None
    try:
        return _decode(path.read_bytes())
    except OSError:
        return None


def _decode(raw: bytes) -> str | None:
    """The text of a file, or None when the file is not text."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
        except UnicodeError:
            return None
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
    return None if "\x00" in text else text


def _read_doc(folder: Path, rel: str) -> str | None:
    """Read a documentation file. Other file types use the documents package."""
    if Path(rel).suffix.lower() in DIRECT_SUFFIXES:
        return _read(folder, rel)
    try:
        from secreqgen.documents import formats  # noqa: PLC0415
    except ImportError:
        return None
    suffix = Path(rel).suffix.lower()
    if suffix not in getattr(formats, "OTHER_SUFFIXES", ()):
        return None
    path = _safe_file(folder, rel)
    if path is None:
        return None
    try:
        return formats.to_markdown(path)
    except Exception:  # noqa: BLE001
        return None


def _first_lines(text: str, count: int) -> str:
    """The first lines of a file, inside the limits of one part."""
    lines = [line[:MAX_LINE_CHARS] for line in text.splitlines()[:count]]
    return cut_words("\n".join(lines), PART_WORDS)[:PART_CHARS]


def _tree_text(files: list[str]) -> str:
    """The top of the file tree. The largest folders come first."""
    root_files: list[str] = []
    totals: dict[str, int] = {}
    weight: dict[str, int] = {}  # files that are not pictures, fonts or styles
    subs: dict[str, dict[str, int]] = {}
    children: dict[tuple[str, str], dict[str, int]] = {}
    for rel in files:
        pieces = rel.split("/")
        if len(pieces) == 1:
            root_files.append(rel)
            continue
        top = pieces[0]
        totals[top] = totals.get(top, 0) + 1
        if Path(rel).suffix.lower() not in NOT_CODE_SUFFIXES:
            weight[top] = weight.get(top, 0) + 1
        if len(pieces) > 2:
            level = subs.setdefault(top, {})
            level[pieces[1]] = level.get(pieces[1], 0) + 1
        if len(pieces) > 3:
            below = children.setdefault((top, pieces[1]), {})
            below[pieces[2]] = below.get(pieces[2], 0) + 1

    def by_size(counts: dict[str, int]) -> list[str]:
        return sorted(counts, key=lambda name: (-counts[name], name.lower()))

    lines: list[str] = []

    def rank(name: str) -> tuple:
        last = is_test_path(name + "/x") or name.startswith(".")
        return (last, -weight.get(name, 0), name.lower())

    for top in sorted(totals, key=rank):
        lines.append(f"{top}/ ({totals[top]} files)")
        names = by_size(subs.get(top, {}))
        for sub in names[:TREE_SUBFOLDERS]:
            line = f"  {sub}/ ({subs[top][sub]} files)"
            below = by_size(children.get((top, sub), {}))[:TREE_CHILDREN]
            if below:
                line += ": " + ", ".join(below)
            lines.append(line)
        if len(names) > TREE_SUBFOLDERS:
            lines.append(f"  and {len(names) - TREE_SUBFOLDERS} more folders")
    plain = sorted((f for f in root_files if not f.startswith(".")), key=str.lower)
    dotted = sorted((f for f in root_files if f.startswith(".")), key=str.lower)
    return "\n".join((lines + plain + dotted)[:TREE_LINES])


def _code_folders_text(files: list[str]) -> str:
    """The folders with the most code files, each with some of its file names."""
    folders: dict[str, list[str]] = {}
    for rel in files:
        path = Path(rel)
        if path.suffix.lower() not in CODE_SUFFIXES or is_test_path(rel):
            continue
        folder = path.parent.as_posix()
        if any(word in folder.lower() for word in NOT_SOURCE_WORDS):
            continue
        if _TEST_NAME_RE.search(path.stem) or path.stem in NOT_NAMES:
            continue
        folders.setdefault(folder, []).append(path.stem)
    largest = sorted(folders, key=lambda f: (-len(folders[f]), f.lower()))
    lines = []
    for folder in largest[:CODE_FOLDERS]:
        names = _spread(sorted(set(folders[folder]), key=str.lower), CODE_NAMES)
        shown = "(top folder)" if folder == "." else folder
        lines.append(f"{shown} ({len(folders[folder])} code files): {', '.join(names)}")
    return "\n".join(lines)


def _spread(items: list[str], limit: int) -> list[str]:
    """At most `limit` items, taken at even steps over the whole list."""
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def _by_depth(files: list[str], depth: int = 1) -> list[str]:
    """Files of the root and of the levels below it up to `depth`, root first."""
    chosen = [f for f in files if f.count("/") <= depth]
    return sorted(chosen, key=lambda f: (f.count("/"), f.lower()))


def scan_codebase(folder: Path) -> CodebaseFacts:
    """Collect the facts of a codebase folder inside a size budget."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ContextError("The folder does not exist or is not a folder.")
    if not os.access(folder, os.R_OK | os.X_OK):
        raise ContextError("The folder cannot be read: no permission.")
    files, seen, secrets, stopped = _walk(folder)
    counts: dict[str, int] = {}
    for rel in files:
        suffix = Path(rel).suffix.lower()
        if suffix:
            counts[suffix] = counts.get(suffix, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    candidates: list[tuple[str, str, str]] = []  # kind, path, text
    candidates.append(("tree", "(file tree)", _tree_text(files)))
    parts = _file_parts(folder, files)
    candidates += [p for p in parts if p[0] == "readme"]
    hits = sorted(
        f
        for f in files
        if _SECURITY_RE.search(f.lower())
        and Path(f).suffix.lower() not in NOT_CODE_SUFFIXES
        and not is_test_path(f)
    )
    if hits:
        text = "\n".join(_spread(hits, PATH_LINES))
        candidates.append(("security_paths", PATHS_LABEL, text))
    code = _code_folders_text(files)
    if code:
        candidates.append(("code_folders", "(largest code folders)", code))
    candidates += [p for p in parts if p[0] != "readme"]
    facts = CodebaseFacts(
        folder.resolve().name,
        seen,
        secrets,
        dict(top),
        stopped_early=stopped,
        suffixes=counts,
    )
    budget = TOTAL_WORDS
    for kind, path, raw in candidates:
        if kind in NAME_KINDS:
            text = remove_secrets_in_names(raw)
        else:
            text = remove_secrets(cut_long_lines(raw))
        if not text.strip():
            continue
        words = len(text.split())
        full = words <= budget
        if not full:
            text = cut_words(text, budget)
            if not text.strip():
                break
        facts.parts.append(Part(f"F{len(facts.parts) + 1}", path, kind, text))
        budget -= len(text.split())
        if not full or budget <= 0:
            break
    if not any(p.kind not in NAME_KINDS for p in facts.parts):
        raise ContextError(
            "The folder has no README, no manifest and no documentation overview."
        )
    return facts


def _file_parts(folder: Path, files: list[str]) -> list[tuple[str, str, str]]:
    """The file parts in the fixed order: readme, manifest, container, api, docs."""
    out: list[tuple[str, str, str]] = []
    roots = sorted((f for f in files if "/" not in f), key=str.lower)
    readmes = sorted(
        (f for f in roots if f.lower().startswith("readme")),
        key=lambda f: (f.lower() not in README_NAMES, f.lower()),
    )
    for rel in readmes[:1]:
        text = _read_doc(folder, rel)
        if text:
            out.append(("readme", rel, cut_words(text, README_WORDS)))
    near = _by_depth(files)
    manifests = sorted(
        (
            f
            for f in files
            if f.count("/") <= MANIFEST_DEPTH
            and (
                Path(f).name in MANIFEST_NAMES
                or f.lower().endswith(MANIFEST_SUFFIXES)
                or fnmatch.fnmatchcase(Path(f).name.lower(), "requirements*.txt")
            )
        ),
        key=lambda f: (is_test_path(f), f.count("/"), f.lower()),
    )
    for rel in manifests[:MANIFEST_FILES]:
        text = _read(folder, rel)
        if text:
            out.append(("manifest", rel, _first_lines(text, MANIFEST_LINES)))
    containers = [
        f
        for f in near
        if any(fnmatch.fnmatchcase(Path(f).name, p) for p in CONTAINER_PATTERNS)
    ]
    for rel in containers[:CONTAINER_FILES]:
        text = _read(folder, rel)
        if text:
            out.append(("container", rel, _first_lines(text, CONTAINER_LINES)))
    apis = [f for f in sorted(files, key=str.lower) if API_RE.match(Path(f).name)]
    for rel in apis[:API_FILES]:
        text = _read(folder, rel)
        if text:
            out.append(("api", rel, _first_lines(text, API_LINES)))
    out += _doc_parts(folder, files)
    return out


def _doc_parts(folder: Path, files: list[str]) -> list[tuple[str, str, str]]:
    """Documentation overview files: SECURITY*, ARCHITECTURE*, docs/index and so on."""
    picked: list[str] = []
    for rel in sorted(files, key=str.lower):
        pieces = rel.split("/")
        stem = pieces[-1].lower()
        if len(pieces) == 1 and stem.startswith(("security", "architecture")):
            picked.append(rel)
        elif len(pieces) == 2 and pieces[0].lower() in DOC_FOLDERS:
            if stem.split(".")[0] in DOC_NAMES:
                picked.append(rel)
    out: list[tuple[str, str, str]] = []
    for rel in picked:
        if len(out) >= DOCS_FILES:
            break
        text = _read_doc(folder, rel)
        if text:
            out.append(("docs", rel, cut_words(text, DOCS_WORDS)))
    return out
