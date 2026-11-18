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
_SOURCE_MARK_RE = re.compile(r"\s*[\[(]F\d+(?:,\s*F\d+)*[\])]")
# Words that many product names share. Alone they are no proof of a technology.
GENERIC_WORDS = frozenset(
    "apache amazon aws google microsoft azure oracle cloud server service services "
    "core framework database the and for with open source engine platform storage "
    "queue cache web api sdk library net sql data app application".split()
)
# Kinds of part whose text is structured evidence: lists of dependencies,
# file names and settings. The other kinds (readme, docs) are prose.
STRUCTURED_KINDS = frozenset(
    {"manifest", "container", "api", "tree", "code_folders", "security_paths"}
)
PROSE_KINDS = frozenset({"readme", "docs"})
SHORT_NAME = 2  # a name of this length or less is never taken from prose
JOINED_MIN = 6  # the joined name (tailwindcss) counts from this length
NET_VERSION = "<netver>"  # a word such as net8
POSTGRES = tuple(
    "postgres psql pg psycopg psycopg2 npgsql pgx postgresql asyncpg pgsql pq".split()
)
NODE = ("node", "nodejs", "nvmrc", "npmrc")
ASPNET = ("aspnetcore", "aspnetsdk", "aspnet")
# Other names of a technology. A key is the whole name (lower case, no
# version) or one word of it. A value that starts with "." is a file type;
# the other values are words. Only structured evidence uses this table.
ALIASES: dict[str, tuple[str, ...]] = {
    "postgresql": POSTGRES,
    "postgres": POSTGRES,
    "sql server": ("mssql", "sqlserver", "sqlclient"),
    "mssql": ("mssql", "sqlserver", "sqlclient"),
    "ms sql server": ("mssql", "sqlserver", "sqlclient"),
    "microsoft sql server": ("mssql", "sqlserver", "sqlclient"),
    "mongodb": ("mongo", "mongoose", "pymongo", "mongoid", "mongoengine"),
    "elasticsearch": ("elastic", "searchkick"),
    # Client libraries: the manifest names the driver, not the product.
    "mysql": tuple(
        "mysqlclient pymysql aiomysql mysqlconnector mysqli mysqldb mysqlx".split()
    ),
    "redis": tuple(
        "ioredis jedis lettuce redigo redisson aioredis hiredis predis bullmq".split()
    ),
    "kafka": ("kafkajs", "aiokafka", "rdkafka", "librdkafka", "sarama"),
    "rabbitmq": ("amqp", "amqplib", "pika", "bunny"),
    "oracle": ("ojdbc", "oracledb"),
    "kubernetes": ("k8s", "kubectl"),
    "amazon s3": ("s3", "boto3", "boto"),
    "aws s3": ("s3", "boto3", "boto"),
    "s3": ("s3", "boto3", "boto"),
    "node.js": NODE,
    "node": NODE,
    "nodejs": NODE,
    "c++": ("c++", "cpp", "cxx", "cxxflags"),
    "c#": ("c#", "csharp", ".cs", ".csproj"),
    ".net": ("dotnet", "netcore", "aspnetcore", "aspnetsdk", NET_VERSION, ".csproj"),
    # The Web SDK holds ASP.NET Core with no package line.
    "asp": ASPNET,
    "asp.net": ASPNET,
    "asp.net core": ASPNET,
    "go": ("golang", ".go"),
    "golang": ("go", ".go"),
    "docker": ("dockerfile", "compose"),
    "rust": ("cargo", ".rs"),
    "gradle": ("gradle",),
    "maven": ("pom",),
    "javascript": ("js", "jsx", "mjs", ".js", ".jsx", ".mjs"),
    "typescript": ("ts", "tsx", "tsconfig", ".ts", ".tsx"),
    "python": ("py", "pyproject", ".py"),
    "ruby": ("rb", "gemfile", "rubygems", "bundler", ".rb"),
    "java": (".java", "javabuild"),
    "php": ("composer", ".php"),
    "kotlin": ("kt", ".kt"),
    "swift": (".swift",),
    "vue.js": ("vue",),
    "vue": ("vue",),
    "next.js": ("next",),
    "express.js": ("express",),
    "express": ("express",),
    "oauth": ("oauth", "oauth2"),
    # A file name or a file content that proves the technology.
    "dart": ("pubspec", ".dart"),
    "helm": ("helmchart",),
    "jenkins": ("jenkinsfile",),
    "github actions": ("githubworkflows",),
    "terraform": (".tf",),
    # More names that the reviewer lists need.
    "redux": ("reduxjs",),
    "tailwind css": ("tailwind", "tailwindcss"),
    "json web tokens": ("jwt", "jjwt", "jsonwebtoken", "pyjwt"),
    "json web token": ("jwt", "jjwt", "jsonwebtoken", "pyjwt"),
    "jwt": ("jwt", "jjwt", "jsonwebtoken", "pyjwt"),
}
# Names that a file name or a word does not prove alone: only the table counts.
ALIAS_ONLY = frozenset({".net"})
# Words that a camel-case split must not break: JavaScript is not Java.
WHOLE_WORDS = frozenset({"javascript", "typescript", "coffeescript"})
# Lines that show the kind of a file when the path is not known.
CONTENT_HINTS = (
    (re.compile(r"^FROM\s+\S", re.MULTILINE), "dockerfile"),
    (re.compile(r"^services:", re.MULTILINE), "compose"),
    (re.compile(r"^\s*edition\s*=\s*[\"']20\d\d", re.MULTILINE), "cargo"),
    (re.compile(r"^\s*plugins\s*\{", re.MULTILINE), "gradle"),
    (re.compile(r"^\[project\]", re.MULTILINE), "pyproject"),
    (re.compile(r"^\s*gem\s+['\"]", re.MULTILINE), "gemfile"),
    (re.compile(r"[\"']scripts[\"']\s*:", re.MULTILINE), "npm"),
    (re.compile(r"Microsoft\.NET\.Sdk\b"), "dotnet"),
    (re.compile(r"Microsoft\.NET\.Sdk\.Web\b"), "aspnetsdk"),
    (re.compile(r"^\s*sdk:\s*[\"']?>=\d[\d.]*\s+<\d", re.MULTILINE), "pubspec"),
    (
        re.compile(r"^apiVersion:\s*v2\b|^appVersion:|\{\{\s*\.Values\.", re.MULTILINE),
        "helmchart",
    ),
    (re.compile(r"\bpipeline\s*\{\s*(?:agent|stages|options)\b"), "jenkinsfile"),
    (re.compile(r"^\s*runs-on:\s*\S", re.MULTILINE), "githubworkflows"),
    (re.compile(r"[\"']compilerOptions[\"']\s*:"), "tsconfig"),
    (
        re.compile(
            r"^kind:\s*(?:Deployment|StatefulSet|DaemonSet|Ingress|ConfigMap|CronJob)\b",
            re.MULTILINE,
        ),
        "k8s",
    ),
    (
        re.compile(
            r"\b(?:sourceCompatibility|targetCompatibility|JavaVersion|"
            r"JavaLanguageVersion|maven\.compiler\.(?:source|target|release))\b"
        ),
        "javabuild",
    ),
    (re.compile(r"(?<![a-z0-9])c\+\+", re.IGNORECASE), "c++"),
    (re.compile(r"(?<![a-z0-9])c#", re.IGNORECASE), "c#"),
)
# File paths that prove a technology: the path gives a mark word.
PATH_MARKS = (
    (re.compile(r"(?:^|/)chart\.ya?ml$"), "helmchart"),
    (re.compile(r"(?:^|/)\.github/workflows/"), "githubworkflows"),
)
# Kinds of part that hold settings and code, not a list of names.
SETTING_KINDS = frozenset({"manifest", "container", "api"})
_SENTENCE_RE = re.compile(r"[A-Za-z .,;'!?]+")
SENTENCE_WORDS = 4  # a line of this many plain words is a sentence
# A plain line is a sentence only when it starts with a capital letter or holds
# one of these words. A list of packages (apt install redis nginx) holds none.
STOP_WORDS = frozenset(
    "the a an and or of to in on for with is are was were be been it this that "
    "these those we you our your by as at from not no use uses used using can "
    "will if its their they into than then but which when where who how".split()
)
_RAW_RE = re.compile(r"[A-Za-z0-9]+")
_PIECE_RE = re.compile(r"[A-Z]+[0-9]*(?![a-z])|[A-Z]?[a-z]+[0-9]*|[0-9]+")
_VERSION_RE = re.compile(r"\d+(\.\d+)*")
_LETTERS_RE = re.compile(r"([a-z]{2,})\d+")
MAX_EVIDENCE_CHARS = 400_000


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


def evidence_words(text: str) -> tuple[frozenset[str], str]:
    """The words of a text, and the same words in order for a phrase search.

    The text is split at each character that is no letter and no digit, and
    at CamelCase: Azure.Messaging.ServiceBus gives azure, messaging, service,
    bus. A word with digits at the end also gives its letters: mysql2 gives
    mysql. Each whole piece of text between two separators is a word too.
    """
    text = text[:MAX_EVIDENCE_CHARS]
    words: set[str] = set()
    order: list[str] = []
    for raw in _RAW_RE.findall(text):
        low = raw.lower()
        words.add(low)
        if low in WHOLE_WORDS:
            order.append(low)
            continue
        pieces = [p.lower() for p in _PIECE_RE.findall(raw)] or [low]
        order.extend(pieces)
        for piece in [low, *pieces]:
            words.add(piece)
            match = _LETTERS_RE.fullmatch(piece)
            if match:
                words.add(match[1])
            if re.match(r"net\d", piece):
                words.add(NET_VERSION)
    for pattern, word in CONTENT_HINTS:
        if pattern.search(text):
            words.add(word)
    return frozenset(words), " " + " ".join(order) + " "


def clean_name(value: str) -> list[str]:
    """The words of a technology name in lower case, without the versions."""
    parts = value.lower().split()
    return [p for p in parts if not _VERSION_RE.fullmatch(p)]


def needed_words(parts: list[str]) -> list[str]:
    """The words of a name that the files must hold.

    A word needs 3 letters or more. Generic words (server, cloud, core) do not
    count when the name has other words. A name with no such word (Go, C#,
    H2) counts as it is.
    """
    words = [w for p in parts for w in re.findall(r"[a-z0-9]+", p) if len(w) >= 3]
    special = [w for w in words if w not in GENERIC_WORDS]
    return special or words or ["".join(parts)]


def _alternatives(word: str) -> list[str]:
    """A needed word, its plural and its other names."""
    plural = [word + "s"] if len(word) > SHORT_NAME + 1 else []
    return [word, *plural, *ALIASES.get(word, ())]


def joined_name(parts: list[str]) -> str:
    """The letters and digits of the name with nothing between them."""
    return "".join(re.findall(r"[a-z0-9]+", "".join(parts)))


def without_prose(text: str) -> str:
    """The text without comment lines and without lines that are sentences.

    A comment or a sentence in a manifest talks about things; it does not
    name what the product uses. A line of plain words with no code character
    (no colon, quote, slash, bracket or equal sign) is a sentence when it
    starts with a capital letter or holds a stop word. A list of packages in
    lower case (apt install redis nginx curl) is evidence, so it stays.
    """
    keep: list[str] = []
    for line in text.splitlines():
        part = line.strip()
        if part.startswith(("#", "//")) and not part.startswith("#!"):
            continue
        words = part.split()
        if (
            len(words) >= SENTENCE_WORDS
            and _SENTENCE_RE.fullmatch(part)
            and not (len(words[0]) > 1 and words[0].isupper())
            and (part[0].isupper() or any(w.lower() in STOP_WORDS for w in words))
        ):
            continue
        keep.append(line)
    return "\n".join(keep)


class _Evidence:
    """The structured evidence of one part: its text and its path."""

    def __init__(self, ref: str, path: str, text: str, kind: str = "") -> None:
        self.ref = ref
        if kind in SETTING_KINDS:
            text = without_prose(text)
        words, self.phrase = evidence_words(f"{path}\n{text}")
        low = path.lower().replace("\\", "/")
        marks = {word for pattern, word in PATH_MARKS if pattern.search(low)}
        self.words = words | marks


def _alias_hit(alias: str, words: frozenset[str], suffixes: frozenset[str]) -> bool:
    """True when an alias is a word of the evidence or a file type."""
    if alias.startswith("."):
        return alias in suffixes or alias[1:] in words
    return alias in words


def prose_names(parts: list[str], prose: str) -> bool:
    """True when the name stands in the prose as a whole phrase, as a name.

    A name of one plain word must have a capital letter in the prose at least
    one time (Swift, Redis): "a swift response" does not name Swift. A name
    with more words, a digit or a sign (next.js, c++, ruby on rails) is no
    normal word, so its letter case does not count.
    """
    name = " ".join(parts)
    if len(re.sub(r"\s", "", name)) <= SHORT_NAME:
        return False
    pattern = rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"
    found = [m[0] for m in re.finditer(pattern, prose, re.IGNORECASE)]
    if not name.isalpha():
        return bool(found)
    return any(any(ch.isupper() for ch in text) for text in found)


class _Files:
    """The evidence of all parts, ready for the check of technology names."""

    def __init__(self, facts: CodebaseFacts) -> None:
        self.structured = [
            _Evidence(p.ref, p.path, p.text, p.kind)
            for p in facts.parts
            if p.kind in STRUCTURED_KINDS
        ]
        # A readme path is evidence of structure too: the path is a name.
        self.paths = [
            _Evidence(p.ref, p.path, "") for p in facts.parts if p.kind in PROSE_KINDS
        ]
        self.prose = {  # with its capital letters: a name has one
            p.ref: " ".join(p.text[:MAX_EVIDENCE_CHARS].split())
            for p in facts.parts
            if p.kind in PROSE_KINDS
        }
        self.suffixes = frozenset(s.lower() for s in facts.suffixes)
        self.tree = next((p.ref for p in facts.parts if p.kind == "tree"), None)

    def holders(self, alternatives: list[str], named: str) -> list[str]:
        """The refs of the parts whose evidence holds one of the alternatives."""
        refs = [
            e.ref
            for e in self.structured + self.paths
            if any(_alias_hit(a, e.words, frozenset()) for a in alternatives)
        ]
        if not refs and any(a in self.suffixes for a in alternatives):
            refs.append(self.tree or named)  # a file type: the tree part
        return refs


def find_part(value: str, named: str, files: _Files) -> str | None:
    """The ref of the part that names the technology, or None.

    (A) Structured evidence names it: each needed word of the name, or an
    other name of it, is a word of the evidence. (B) The prose holds the name
    as a whole phrase. The source is the part that holds the evidence; the
    part that the model named wins when it holds as much.
    """
    parts = clean_name(value)
    if not parts:
        return None
    key = " ".join(parts)
    needed = needed_words(parts)
    whole = list(ALIASES.get(key, ()))
    votes: dict[str, int] = {}

    def vote(refs: list[str]) -> None:
        for ref in refs:
            votes[ref] = votes.get(ref, 0) + 1

    if key not in ALIAS_ONLY:
        groups = [_alternatives(w) for w in needed]
        found = [files.holders(g, named) for g in groups]
        all_generic = len(needed) > 1 and all(w in GENERIC_WORDS for w in needed)
        if all_generic:
            phrase = " " + " ".join(needed) + " "
            found = [[e.ref for e in files.structured if phrase in e.phrase]] * len(
                needed
            )
        if all(found):
            for refs in found:
                vote(refs)
    if not votes and whole:
        refs = files.holders(whole, named)
        vote(refs)
    joined = joined_name(parts)
    # The joined name helps a name of several words (Tailwind CSS). A name of
    # one word is a plain word: "docker" is not a part of "dockerized".
    several = len(re.findall(r"[a-z0-9]+", key)) > 1
    if not votes and several and len(joined) >= JOINED_MIN:
        vote(
            [
                e.ref
                for e in files.structured + files.paths
                if any(joined in w for w in e.words)
            ]
        )
    if votes:
        order = [named] + [r for r in votes if r != named]
        return max((r for r in order if r in votes), key=lambda r: votes[r])
    hits = [ref for ref, prose in files.prose.items() if prose_names(parts, prose)]
    if hits:
        return named if named in hits else hits[0]
    return None


def check_answer(
    answer: dict, facts: CodebaseFacts, product: str | None
) -> tuple[dict, list[dict]]:
    """Return the checked context and the list of dropped items."""
    answer = _dict(answer)
    paths = {p.ref: p.path for p in facts.parts}
    files = _Files(facts)
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
    source = _text(answer.get("description_source"))
    description_path = paths.get(source, "") if description else ""
    tech: list[dict] = []
    for raw in _list(answer.get("tech_stack")):
        item = _dict(raw)
        value, kind = _text(item.get("value")), _text(item.get("kind"))
        what = f"tech_stack: {value}"
        if not value or not known(item, what):
            continue
        if not short(value, what):
            continue
        ref = find_part(value, _text(item.get("source")), files)
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
    # The model sometimes ends the rating with a source name: "High - x [F2]".
    profile = _SOURCE_MARK_RE.sub("", _text(answer.get("security_profile")))
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
        "description_path": description_path,
        "tech_stack": tech,
        "security_profile": profile,
        "features": features,
        "data_sensitivity": data,
    }
    return context, dropped
