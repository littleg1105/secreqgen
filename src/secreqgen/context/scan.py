"""Plain code that collects the facts of a codebase folder.

No model runs here. The scan never opens a secret file and never names one.
"""

from __future__ import annotations

import fnmatch
import os
import re
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
)
MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000
TOTAL_WORDS = 6_500
TREE_LINES = 80
README_WORDS = 900
MANIFEST_LINES, MANIFEST_FILES = 80, 8
CONTAINER_LINES, CONTAINER_FILES = 60, 4
API_LINES, API_FILES = 60, 2
DOCS_WORDS, DOCS_FILES = 400, 6
PATH_LINES = 80
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
DIRECT_SUFFIXES = ("", ".md", ".txt")

_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|api-key|private_key)"
    r"([\"']?\s*[:=]\s*)([\"']?)([^\s\"',;]+)",
    re.IGNORECASE,
)
_LONG_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


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


def is_secret_name(name: str) -> bool:
    """True when the file name is one of the secret file names."""
    return any(fnmatch.fnmatchcase(name.lower(), p.lower()) for p in SECRET_PATTERNS)


def remove_secrets(text: str) -> str:
    """Replace values that look like secrets with <removed>."""
    text = _KEY_RE.sub(lambda m: f"{m[1]}{m[2]}{m[3]}<removed>", text)
    return _LONG_RE.sub("<removed>", text)


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


def _walk(folder: Path) -> tuple[list[str], int, int]:
    """Return the relative paths of the usable files, the count and the secrets."""
    root = folder.resolve()
    files: list[str] = []
    seen = secrets = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_FOLDERS)
        for name in sorted(names):
            if seen >= MAX_FILES:
                return files, seen, secrets
            seen += 1
            full = Path(base) / name
            if is_secret_name(name):
                secrets += 1
                continue
            if full.is_symlink():
                try:
                    full.resolve().relative_to(root)
                except (ValueError, OSError):
                    continue
            files.append(full.relative_to(root).as_posix())
    return files, seen, secrets


def _read(folder: Path, rel: str) -> str | None:
    """Read a text file, or return None when it is too large or unreadable."""
    path = folder / rel
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


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
    try:
        return formats.to_markdown(folder / rel)
    except Exception:  # noqa: BLE001
        return None


def _first_lines(text: str, count: int) -> str:
    return "\n".join(text.splitlines()[:count])


def _tree_text(files: list[str]) -> str:
    """The top of the file tree: the root and the first level below it."""
    root_files: list[str] = []
    folders: dict[str, dict[str, int]] = {}
    for rel in files:
        pieces = rel.split("/")
        if len(pieces) == 1:
            root_files.append(rel)
            continue
        level = folders.setdefault(pieces[0], {})
        key = pieces[1] if len(pieces) > 2 else ""
        level[key] = level.get(key, 0) + 1
    lines: list[str] = []
    for name in sorted(set(root_files) | set(folders), key=str.lower):
        if name in folders:
            total = sum(folders[name].values())
            lines.append(f"{name}/ ({total} files)")
            for sub in sorted(k for k in folders[name] if k):
                lines.append(f"  {sub}/ ({folders[name][sub]} files)")
        else:
            lines.append(name)
    return "\n".join(lines[:TREE_LINES])


def _by_depth(files: list[str], depth: int = 1) -> list[str]:
    """Files of the root and of the levels below it up to `depth`, root first."""
    chosen = [f for f in files if f.count("/") <= depth]
    return sorted(chosen, key=lambda f: (f.count("/"), f.lower()))


def scan_codebase(folder: Path) -> CodebaseFacts:
    """Collect the facts of a codebase folder inside a size budget."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ContextError("The folder does not exist or is not a folder.")
    files, seen, secrets = _walk(folder)
    counts: dict[str, int] = {}
    for rel in files:
        suffix = Path(rel).suffix.lower()
        if suffix:
            counts[suffix] = counts.get(suffix, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    candidates: list[tuple[str, str, str]] = []  # kind, path, text
    candidates.append(("tree", "(file tree)", _tree_text(files)))
    candidates += _file_parts(folder, files)
    hits = [f for f in files if any(w in f.lower() for w in SECURITY_WORDS)]
    if hits:
        candidates.append(("security_paths", "(paths)", "\n".join(hits[:PATH_LINES])))
    facts = CodebaseFacts(folder.resolve().name, seen, secrets, dict(top))
    budget = TOTAL_WORDS
    for kind, path, text in candidates:
        text = remove_secrets(text)
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
    if not any(p.kind != "tree" for p in facts.parts):
        raise ContextError(
            "The folder has no README, no manifest and no documentation overview."
        )
    return facts


def _file_parts(folder: Path, files: list[str]) -> list[tuple[str, str, str]]:
    """The file parts in the fixed order: readme, manifest, container, api, docs."""
    out: list[tuple[str, str, str]] = []
    roots = sorted((f for f in files if "/" not in f), key=str.lower)
    readmes = [f for f in roots if f.lower().startswith("readme")]
    for rel in readmes[:1]:
        text = _read_doc(folder, rel)
        if text:
            out.append(("readme", rel, cut_words(text, README_WORDS)))
    near = _by_depth(files)
    manifests = [
        f
        for f in near
        if Path(f).name in MANIFEST_NAMES or f.lower().endswith(".csproj")
    ]
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
