"""The rules that replace secret values in the text of a codebase.

Plain code. No model runs here. remove_secrets takes the text of a file that
the scan reads (a README, a manifest, a container file) and writes <removed>
in the place of each value that looks like a secret: a value after a name
such as password or token, a password in a URL or in a command line, a key
block, a key of a known service, a long random text. Harmless text must stay:
names of dependencies, versions, references to variables, and prose.

The rules lower the risk that a secret reaches the model. They are not a
proof. The tests hold the lists of forms that are replaced, of forms that
still pass, and of harmless lines that must not change.
"""

from __future__ import annotations

import re

REMOVED = "<removed>"
# Words that mark a name as a secret. Other languages are in the list too.
_KEY_WORDS = (
    r"password|passwd|passphrase|passcode|pwd|secret|token|credential|"
    r"api[_-]?key|private[_-]?key|access[_-]?key|auth[_-]?key|signing[_-]?key|"
    r"encryption[_-]?key|master[_-]?key|license[_-]?key|requirepass|"
    r"storepass|keypass|passwort|kennwort|contrase[ñn]a|schl[üu]ssel|"
    r"mot[ ]de[ ]passe|κωδικ\w*|συνθηματικ\w*"
)
_WORD_RE = re.compile(_KEY_WORDS, re.IGNORECASE)
# A name with a secret word. The group is atomic: a long word with many key
# words in it is matched once, with no backtracking (the search stays linear).
_NAME_CORE = r"[\w.-]*?(?:" + _KEY_WORDS + r")[\w.-]*+"
_NAME = r"(?>" + _NAME_CORE + r")"
_QUOTED = r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'"
# One argument of a command line: a quoted text, or one word.
_ARG = r"(?:" + _QUOTED + r"|[^\s\"'-]\S*)"
# A key name with a secret word, a separator, then the value on the same line.
# The name must start where a run of name characters starts: the search stays
# fast on a very long line. The key words pass, pw and key count only when the
# value is in quotes. The value may be quoted (a backslash escapes a quote) or
# one word. "last" is set when the value is the last thing on its line (a
# comment may follow).
_KEY_RE = re.compile(
    r"(?<![\w.-])"
    r"(?P<key>(?>" + _NAME_CORE + r"|[\w.-]+[_-](?:pass|pw|key|salt|pat)|pass|pw|key))"
    r"(?P<q>[\"']?)"
    r"(?:(?P<eq>[ \t]*(?<![!<>=])=(?![=~>])[ \t]*)"
    r"|(?P<arrow>[ \t]*=>[ \t]*)"
    r"|(?P<colon>:(?=[ \t\"'])[ \t]*))"
    r"(?P<val>" + _QUOTED + r"|\S+)"
    r"(?P<last>(?=[ \t\r]*\\?[ \t\r]*(?:(?:\#|//)[^\n]*)?$))?",
    re.IGNORECASE | re.MULTILINE,
)
_SHORT_KEYS = frozenset({"pass", "pw", "key"})
_HEADINGS = frozenset({"passwords", "secrets", "tokens", "credentials", "keys"})
# A name that tells what the value is for, not the secret itself.
_PLAIN_END_RE = re.compile(
    r"[_.-](?:id|ids|length|len|size|count|ttl|timeout|expiry|expiration|"
    r"lifetime|type|algorithm|alg|path|file|dir|url|uri|endpoint|header|name|"
    r"provider|policy|ref|prefix|label|minutes|seconds|hours|days|hasher|"
    r"hashers|encoder|encoders|manager|store|storage|backend|source|engine|"
    r"method|format|complexity|strategy|scheme|generator|placeholder|message|"
    r"msg|error|title|hint|help|text|field|invalid|mismatch|required|"
    r"supported|enabled|allowed|short|long|weak)$",
    re.IGNORECASE,
)
# The key of a text for a screen: msg_password_too_short, error.password.invalid.
_TEXT_NAME_RE = re.compile(r"^(?:msg|message|error|err|label|forgot)[_.-]", re.I)
# A name that points to a Kubernetes Secret object, not to a secret value.
_PLAIN_START_RE = re.compile(r"^existing", re.IGNORECASE)
_PLAIN_CAMEL_RE = re.compile(
    r"(?:Id|Ids|Length|Size|Count|Ttl|Timeout|Expiry|Expiration|Lifetime|Type|"
    r"Algorithm|Path|File|Dir|Url|Uri|Endpoint|Header|Name|Provider|Policy|"
    r"Ref|Prefix|Label|Hasher|Hashers|Encoder|Encoders|Manager|Store|Storage|"
    r"Backend|Source|Engine|Method|Format|Complexity|Strategy|Scheme|Generator|"
    r"Placeholder|Message|Error|Title|Hint|Help|Text|Field|Required|Supported|"
    r"Enabled|Allowed)$"
)
_PUBLIC_RE = re.compile(r"public[_-]?key", re.IGNORECASE)
_WORD_VALUES = frozenset(
    {"true", "false", "yes", "no", "on", "off", "null", "none", "nil"}
)
_TYPE_WORDS = frozenset(
    {
        "string", "str", "int", "integer", "number", "boolean", "bool", "object",
        "array", "any", "void", "bytes", "byte", "char", "float", "double", "long",
        "uuid", "date", "datetime", "text", "json", "required", "optional",
    }
)  # fmt: skip
_REF_RE = re.compile(
    r"^(?:\$\{[^{}]*\}\S*|\$[A-Za-z_]\w*[,;)]*|%\w+%|\{\{.*|\{\w+\}|:\w+|"
    r"<[^<>]*>|process\.env\b|os\.environ\b|os\.getenv\b|System\.getenv\b|"
    r"ENV\[|getenv\(|env\(|file:|vault:|ref\+|op://|ssm:|\$\(|ENC\[)",
    re.IGNORECASE,
)
_REDACTED_RE = re.compile(
    r"^\[?(?:redacted|hidden|masked|omitted|removed)\]?$", re.IGNORECASE
)
# A version range with more than one part: ">=8.5.1 <10", "^1.2 || ^2.0".
_RANGE_RE = re.compile(
    r"^(?=.*[.<>^~|])(?:[\^~<>=!]{1,3}[ ]?|v?)\d[\w.*+-]*"
    r"(?:[ ]*(?:\|\||,|-)?[ ]*(?:[\^~<>=!]{1,3}[ ]?)?v?\d[\w.*+-]*)+$",
    re.IGNORECASE,
)
# Where a dependency comes from: a git URL, a workspace, a tag word.
_SOURCE_RE = re.compile(
    r"^(?:git\+[a-z]+://|git://|git@[\w.-]+:|[a-z]+\+https?://|"
    r"(?:github|gitlab|bitbucket|file|link|workspace|npm|portal|patch|jsr):|"
    r"(?:latest|next|canary|beta|alpha|stable|nightly|\*|x)$)",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"^[\^~<>=!\[(]{0,3}[ ]?v?\d+(?:\.[0-9a-z*]+)+(?:[-+.][0-9a-z.+-]*)?[\])]?$",
    re.IGNORECASE,
)
_SMALL_NUMBER_RE = re.compile(
    r"^[\^~<>=!]{0,3}\d{1,2}(?:[-_ ]?(?:bits?|bytes?|chars?|characters?|mins?|"
    r"minutes?|secs?|seconds?|hours?|days?|ms|s|h|m|d|x))?$",
    re.IGNORECASE,
)
_OPERATOR_RE = re.compile(
    r"^(?:[\^~<>=!]+|[|>][-+0-9]*|[.*•#x_-]{3,}|[\[\]{}()<>|,;:]+)$"
)
# A name that counts things: max_new_tokens, num_keys, total_tokens.
_COUNT_NAME_RE = re.compile(
    r"(?:^|[_.-])(?:max|min|num|n|total|count)[_.-]|^(?:max|min|num|total)[A-Z]"
)
_PLURAL_END_RE = re.compile(r"(?:tokens|keys|secrets|passwords|credentials)$", re.I)
_PLACEHOLDER_RE = re.compile(
    r"^(?:replace[_-]?me|(?:your|insert)[_-][\w-]+)$|\.\.\.$", re.IGNORECASE
)
_PASCAL_RE = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+$")
_DOTTED_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_CALL_RE = re.compile(r"^[A-Za-z_][\w.]*\(\S*$")
_PATH_RE = re.compile(
    r"^(?:(?:~|\.{1,2})?/\S*|[a-z][a-z0-9+.-]*://\S*)$", re.IGNORECASE
)
_CAMEL_RE = re.compile(r"^[a-z_][a-z0-9_]*[A-Z]\w*$")
_TRIM_CHARS = "`.,;:)]}'\"\\"
_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "to", "is", "are", "in", "on", "of", "for", "and", "or",
        "with", "as", "by", "it", "if", "this", "that", "flag", "option", "options",
        "argument", "arguments", "parameter", "parameters", "value", "values",
        "env", "variable", "file", "instead", "then", "when", "must", "can", "will",
        "should", "use", "set", "from", "not", "your", "you", "be", "has", "have",
        "but", "also", "only", "which", "see",
    }
)  # fmt: skip
_TABLE_STOP = _TYPE_WORDS | _WORD_VALUES | {"n/a", "na", "-", "--", "\u2014", "default"}
_URL_PW_RE = re.compile(
    r"(?<![a-z0-9+.-])(?P<scheme>[a-z][a-z0-9+.-]*)://(?P<user>[^\s/:@]*):"
    r"(?!\d+(?:[/?#'\"\s)]|$))(?P<pw>\S{1,128}?)@"
    r"(?=[^\s@/?#:'\"<>]+(?:[/:?#'\"\s)\]>,;]|$))",
    re.IGNORECASE,
)
_URL_USER_RE = re.compile(
    r"(?<![a-z0-9+.-])(?P<scheme>https?://)(?P<user>[^\s/:@?#'\"<>()]+)@"
    r"(?=[\w.\[-])",
    re.IGNORECASE,
)
# Example values of documentation: "?key=value".
_DUMMY_VALUES = frozenset({"value", "val", "foo", "bar", "baz", "xxx", "abc", "123"})
QUERY_VALUE_CHARS = 6  # a shorter value of "key" or "token" is no key
_QUERY_ALWAYS = frozenset({"password", "pw", "pwd", "secret", "client_secret"})
_QUERY_RE = re.compile(
    r"(?P<head>[?&;](?:sig|signature|token|access_token|refresh_token|id_token|"
    r"client_secret|key|api_key|apikey|password|pw|pwd|secret)=)"
    r"(?P<val>[^&;\s#'\"<>]+)",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"-{4,5}BEGIN [A-Z0-9 ]+-{4,5}.*?(?:-{4,5}END [A-Z0-9 ]+-{4,5}|\Z)", re.DOTALL
)
_END_RE = re.compile(r"-{4,5}END [A-Z0-9 ]+-{4,5}")
_AUTH_RE = re.compile(
    r"\b(?P<kind>bearer|basic)(?P<gap>[ \t]+)(?P<val>[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_DIGEST_RE = re.compile(
    r"(?P<head>authorization[\"']?[ \t]*:[ \t]*digest[ \t]+)(?P<val>[^\r\n]*[^\s\r\n])",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"(?<![\w-])(?P<head>(?:proxy-)?authorization|set-cookie|cookie)"
    r"(?P<sep>[\"']?[ \t]*:[ \t]*)"
    r"(?P<scheme>(?:basic|bearer|token|digest|negotiate|ntlm)[ \t]+)?"
    r"(?P<val>[^\r\n\"']+)",
    re.IGNORECASE,
)
# Command lines. Each rule keeps the group "head" and replaces the group "val".
_COMMAND_RULES = (
    (
        "",
        re.compile(
            r"(?<![\w-])(?P<head>-{1,2}[\w-]*?(?:password|passwd|passphrase|"
            r"requirepass|pwd|token|secret|api[-_]?key|apikey|storepass|keypass|"
            r"credential)[ \t]+)(?P<val>" + _ARG + r")",
            re.IGNORECASE,
        ),
    ),
    (
        "",
        re.compile(
            r"(?<![\w-])(?P<head>(?:--(?:user|auth|proxy-user|userpass|basic-auth)"
            r"(?:[ \t]+|=)|\b(?:curl|wget|http|https|httpie)\b[^\n|]{0,200}?"
            r"[ \t]-u[ \t]+)[\"']?[^\s:'\"]*:)(?P<val>[^\s'\"]+)"
        ),
    ),
    ("sshpass", re.compile(r"\b(?P<head>sshpass[ \t]+-p[ \t]*)(?P<val>" + _ARG + r")")),
    (
        "mysql",
        re.compile(
            r"\b(?P<head>(?:mysql|mysqldump|mysqladmin)\b[^\n]{0,200}?[ \t]-p)"
            r"(?P<val>[^\s-]\S*)"
        ),
    ),
    (
        "mariadb",
        re.compile(
            r"\b(?P<head>(?:mariadb|mariadb-dump)\b[^\n]{0,200}?[ \t]-p)"
            r"(?P<val>[^\s-]\S*)"
        ),
    ),
    (
        "docker",
        re.compile(
            r"\b(?P<head>docker[ \t]+login\b[^\n]{0,200}?[ \t]-p[ \t]+)"
            r"(?P<val>" + _ARG + r")"
        ),
    ),
    (
        "redis-cli",
        re.compile(
            r"\b(?P<head>redis-cli\b[^\n]{0,200}?[ \t]-a[ \t]+)(?P<val>" + _ARG + r")"
        ),
    ),
    ("-pass", re.compile(r"(?P<head>-pass(?:in|out)[ \t]+pass:)(?P<val>\S+)")),
    (
        "ENV",
        re.compile(
            r"^(?P<head>[ \t]*ENV[ \t]+(?P<name>"
            + _NAME
            + r")[ \t]+)(?P<val>"
            + _ARG
            + r")",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "chpasswd",
        re.compile(
            r"(?P<head>\becho[ \t]+[\"']?[\w.-]+:)(?P<val>[^\s\"'|]+)"
            r"(?P<tail>[\"']?[ \t]*\|[ \t]*chpasswd)"
        ),
    ),
    (
        "htpasswd",
        re.compile(
            r"\b(?P<head>htpasswd[ \t]+(?:-\w+[ \t]+){0,4}?-[a-zA-Z]*b[a-zA-Z]*[ \t]+"
            r"(?:-\w+[ \t]+){0,4}[^\s-]\S*[ \t]+[^\s-]\S*[ \t]+)(?P<val>" + _ARG + r")"
        ),
    ),
    (
        "openssl",
        re.compile(
            r"\b(?P<head>openssl[ \t]+passwd\b(?:[ \t]+-[\w-]+)*[ \t]+)"
            r"(?P<val>[^\s)'\"-][^\s)'\"]*)"
        ),
    ),
    (
        "docker",
        re.compile(
            r"(?P<head>\becho[ \t]+)(?P<val>" + _QUOTED + r"|[^\s\"'|]+)"
            r"(?P<tail>[ \t]*\|[ \t]*docker[ \t]+login)"
        ),
    ),
)
# More command forms. The first part of each pair is a list of words in lower
# case: the rule runs only when the text has one of them.
_LOWER_RULES = (
    (
        ("identified",),
        re.compile(
            r"\b(?P<head>identified[ \t]+(?:with[ \t]+\w+[ \t]+)?by[ \t]+"
            r"(?:password[ \t]+)?)(?P<val>" + _QUOTED + r")",
            re.IGNORECASE,
        ),
    ),
    (
        ("setenv", "fastcgi_param", "uwsgi_param", "scgi_param", "set $"),
        re.compile(
            r"^(?P<head>[ \t]*(?:(?:setenv|fastcgi_param|uwsgi_param|scgi_param)"
            r"[ \t]+|set[ \t]+\$)(?P<name>" + _NAME + r")[ \t]+)(?P<val>" + _ARG + r")",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        ("/pass", "/pwd"),
        re.compile(
            r"(?<![\w/])(?P<head>/(?:pass|password|pwd):)(?P<val>"
            + _QUOTED
            + r"|[^\s\"']+)",
            re.IGNORECASE,
        ),
    ),
    (
        ("net use",),
        re.compile(
            r"\b(?P<head>net[ \t]+use\b[^\n]{0,120}?[ \t]\\\\[^\s\\]+\\\S*[ \t]+)"
            r"(?P<val>[A-Za-z0-9][^\s\"'/]*)",
            re.IGNORECASE,
        ),
    ),
    (
        ("convertto-securestring",),
        re.compile(
            r"(?P<head>\bconvertto-securestring[ \t]+(?:-string[ \t]+)?)"
            r"(?P<val>" + _QUOTED + r"|[^\s\"'-]\S*)(?=[^\n]{0,60}-asplaintext)",
            re.IGNORECASE,
        ),
    ),
    (
        ("convertto-securestring",),
        re.compile(
            r"(?P<head>\bconvertto-securestring[ \t]+-asplaintext[ \t]+"
            r"(?:-force[ \t]+)?(?:-string[ \t]+)?)(?P<val>"
            + _QUOTED
            + r"|[^\s\"'-]\S*)",
            re.IGNORECASE,
        ),
    ),
    (
        ("jwt", "jws", "jsonwebtoken", "jose", "signwith"),
        re.compile(
            r"(?P<head>(?:\b(?:jwt|jws|jsonwebtoken|jose)\.(?:sign|verify|encode|"
            r"decode)|\.signWith)\([ \t]*[^,()\n]{1,80},[ \t]*)(?P<val>"
            + _QUOTED
            + r")",
            re.IGNORECASE,
        ),
    ),
)
_INDEX_RE = re.compile(
    r"(?P<head>\[[ \t]*(?P<q>[\"'])(?P<key>" + _NAME + r"|pass|pw|key)(?P=q)"
    r"[ \t]*\][ \t]*(?:=(?![=~])|=>)[ \t]*)(?P<val>" + _QUOTED + r")",
    re.IGNORECASE,
)
_CALL_NAME_RE = re.compile(
    r"(?<![\w.])(?P<name>[\w.]++)[ \t]*\([ \t]*"
    r"(?P<val>(?:" + _QUOTED + r")(?:[ \t]*,[ \t]*(?:" + _QUOTED + r"))*)",
    re.IGNORECASE,
)
_NOT_SETTER_RE = re.compile(
    r"^(?:get|is|has|check|verify|validate|generate|hash|encode|decode|read|"
    r"load|find|delete|remove|reset|require|from|to|as|parse|count|num|max|min)",
    re.IGNORECASE,
)
# The name of a call that takes a secret: the secret word ends it.
_SETTER_END_RE = re.compile(
    r"(?:" + _KEY_WORDS + r")s?(?:str|string|value)?$", re.IGNORECASE
)
_PAIR_RE = re.compile(
    r"(?P<head>(?P<q>[\"'])(?P<key>" + _NAME + r")(?P=q)[ \t]*,[ \t]*)"
    r"(?P<val>" + _QUOTED + r")(?![ \t]*:)",
    re.IGNORECASE,
)
_GRADLE_RE = re.compile(
    r"^(?P<head>[ \t]*(?P<key>[\w.]++)[ \t]+)(?P<val>" + _QUOTED + r")",
    re.IGNORECASE | re.MULTILINE,
)
_TERRAFORM_RE = re.compile(
    r"(?P<head>\bvariable[ \t]+\"(?P<key>" + _NAME + r")\"[ \t]*\{[^{}]{0,300}?"
    r"\bdefault[ \t]*=[ \t]*)(?P<val>" + _QUOTED + r")",
    re.IGNORECASE,
)
_YAML_LIST_RE = re.compile(
    r"^(?P<head>[ \t]*-[ \t]+[\"']?--?(?P<key>" + _NAME + r")[\"']?[ \t]*\r?\n"
    r"[ \t]*-[ \t]+)(?P<val>" + _QUOTED + r"|[^\s#-]\S*)"
    r"(?P<tail>[ \t]*(?:\#[^\r\n]*)?\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
# A login pair in prose: "Log in with admin / Pw123".
_LOGIN_RE = re.compile(
    r"(?P<head>\b(?:log[ ]?in|sign[ ]in|logins?|credentials?|user[ ]?name)\b"
    r"[^\n`|]{0,40}?(?<![\w.@-])(?P<name>[\w.@-]{2,32})[ \t]+/[ \t]+)"
    r"(?P<val>[^\s`'\"<>|,;()]+)",
    re.IGNORECASE,
)
_COMMON_USERS = frozenset({"admin", "administrator", "root", "user", "test", "demo"})
_SETTING_WORD_RE = re.compile(r"[A-Za-z][A-Za-z.]*")
_VALUE_NEXT_RE = re.compile(r"[ \t]+value[ \t]*=", re.IGNORECASE)
# The login of a registry in a config file: "auth": "dXNlcjpwYXNz...".
_AUTH_BASE64_RE = re.compile(
    r"(?P<head>(?<![\w.-])[\"']?_?auth[\"']?[ \t]*[:=][ \t]*)"
    r"(?P<q>[\"'])(?P<val>[A-Za-z0-9+/]{12,}={0,2})(?P=q)",
    re.IGNORECASE,
)
_XML_ELEMENT_RE = re.compile(
    r"<(?P<tag>[\w.:-]+)(?P<attrs>\s[^<>]{0,400})?>(?P<val>[^<>\n]{1,400})</(?P=tag)>"
)
_XML_TAG_RE = re.compile(r"<[\w.:-]+(?P<attrs>\s[^<>]{0,400})>")
_XML_ATTR_RE = re.compile(
    r"(?P<name>[\w.:-]+)(?P<eq>\s*=\s*)(?P<q>[\"'])(?P<val>[^\"'<>]*)(?P=q)"
)
_K8S_RE = re.compile(
    r"^(?P<head>[ \t]*(?:-[ \t]+)?name:[ \t]*[\"']?(?P<key>" + _NAME + r")[\"']?"
    r"[ \t]*\r?\n[ \t]*value:[ \t]*)(?P<val>" + _QUOTED + r"|[^\s#][^\r\n]*?)"
    r"(?P<tail>[ \t]*(?:\#[^\r\n]*)?\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_BLOCK_KEY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:-[ \t]+)?[\"']?(?P<key>" + _NAME + r")[\"']?[ \t]*:"
    r"[ \t]*(?P<block>[|>][-+0-9]*)?[ \t]*(?:#.*)?\r?$",
    re.IGNORECASE,
)
_BLOCK_TAIL_RE = re.compile(r":[ \t]*(?:[|>][-+0-9]*)?[ \t]*(?:#.*)?$")
_MAPPING_RE = re.compile(r"^[\"']?[\w./-]+[\"']?:(?:[ \t]|$)")
MAX_BLOCK_LINES = 200
_TABLE_RE = re.compile(
    r"(?P<head>\|[ \t]*`?(?P<key>" + _NAME + r")`?[ \t]*\|[ \t]*)"
    r"(?P<val>`[^`|\n]+`|[^\s|`][^\s|]*)(?=[ \t]*\|)",
    re.IGNORECASE,
)
_PROSE_RE = re.compile(
    r"(?<![\w.-])(?P<key>(?:password|passwd|passphrase|passcode|secret|token|"
    r"api[ _-]?key|apikey|passwort|kennwort|contrase[ñn]a|mot[ ]de[ ]passe|"
    r"κωδικ(?:ός|ος|ο[ύυ])(?:[ ]πρόσβασης)?|συνθηματικ(?:ό|ο))(?![\w-]))"
    r"(?P<gap>[ \t]+(?P<copula>(?:is|are|was|ist|est|είναι)[ \t]+)?)"
    r"(?P<val>`[^`\n]{1,200}`|\"[^\"\n]{1,200}\"|'[^'\n]{1,200}'|"
    r"[^\s`\"'()<>,;]+)",
    re.IGNORECASE,
)
_WEBHOOK_RE = re.compile(
    r"(?P<head>hooks\.slack\.com/services/\w+/\w+/|"
    r"discord(?:app)?\.com/api/webhooks/\d+/)(?P<val>[\w-]{8,})"
)
_AUTH_PAIR_RE = re.compile(
    r"(?<![\w.-])(?P<head>(?:basic[_-]?)?auth[\"']?:[ \t]+[\"']?[\w.@-]+:)"
    r"(?P<val>[^\s'\"/][^\s'\"]*)",
    re.IGNORECASE,
)
_HASH_RE = re.compile(
    r"(?<![\w$])(?:\$(?:apr1|2[abxy]|y|argon2\w*)\$[\w./$+=,-]{6,}"
    r"|\$[156]\$[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{20,})"
)
# Keys of well-known services. They have a fixed start.
_KNOWN_RE = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bglpat-[A-Za-z0-9_-]{16,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bxapp-[A-Za-z0-9-]{10,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\b[srp]k_(?:live|test)_[A-Za-z0-9]{10,}"
    r"|\bwhsec_[A-Za-z0-9]{16,}"
    r"|\bGOCSPX-[A-Za-z0-9_-]{16,}"
    r"|\bdop_v1_[A-Za-z0-9]{16,}"
    r"|\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"|\bhf_[A-Za-z0-9]{20,}"
    r"|\bshp(?:at|ca|pa|ss)_[A-Za-z0-9]{16,}"
    r"|\bpypi-AgE[A-Za-z0-9_-]{20,}"
    r"|\bAIza[0-9A-Za-z_-]{30,}"
    r"|\bnpm_[A-Za-z0-9]{30,}"
    r"|\bya29\.[A-Za-z0-9_-]{20,}"
    r"|\b[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*)?"
)
_LONG_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_DIGEST_BEFORE_RE = re.compile(
    r"(?:\bsha(?:256|512)[:=]|\b(?:commit|rev|revision|ref|sha|digest)[\"']?"
    r"[ \t]*[:=][ \t]*[\"']?)$",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[/_=-]+|\d+")
_SOLID_WORD_RE = re.compile(r"(?:[A-Z][a-z]+)+|[A-Z]{4,}|[a-z]{8,}")
_CONSONANTS_RE = re.compile(r"[^aeiouy]{5}")
_PIECE_WORD_RE = re.compile(r"(?:[A-Z][a-z]+)+|[A-Z]{3,}|[a-z]{3,}")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")


def looks_random(token: str) -> bool:
    """True for a long token that looks like a key or a hash, not like a name."""
    if not any(ch.isdigit() for ch in token):
        return False
    core = token.rstrip("=")
    if "+" in core:
        return True  # base64 text
    if _HEX_RE.fullmatch(core.replace("-", "")):
        return True  # a hash or a UUID
    solid = not any(ch in core for ch in "/_=-")
    word_re = _SOLID_WORD_RE if solid else _PIECE_WORD_RE
    in_words = sum(len(p) for p in _SPLIT_RE.split(core) if _is_word(p, word_re, solid))
    return in_words / len(core) < (0.7 if solid else 0.6)  # names are made of words


def _is_word(piece: str, word_re: re.Pattern[str], solid: bool) -> bool:
    """True when the piece is a word. A long run of lower-case letters must read
    like a word: random letters have few vowels and many consonants in a row."""
    if not word_re.fullmatch(piece):
        return False
    if solid and piece.islower():
        vowels = sum(ch in "aeiouy" for ch in piece)
        return vowels / len(piece) >= 0.28 and not _CONSONANTS_RE.search(piece)
    return True


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _trim(value: str) -> tuple[str, str]:
    """Split a value into its core and the closing punctuation after it."""
    core = value.rstrip(_TRIM_CHARS)
    tail = value[len(core) :]
    return core.lstrip("`\"'"), tail


def _is_harmless(value: str, quoted: bool = False) -> bool:
    """True for a value that is no secret: a flag word, a reference, a version."""
    v = value.strip().strip("`\"'")
    if not v or REMOVED in v:
        return True
    if v.lower() in _WORD_VALUES or _REF_RE.match(v):
        return True
    if _VERSION_RE.match(v) or _SMALL_NUMBER_RE.match(v) or _OPERATOR_RE.match(v):
        return True
    if _RANGE_RE.match(v) or _SOURCE_RE.match(v) or _REDACTED_RE.match(v):
        return True
    if _PLACEHOLDER_RE.search(v):
        return True
    if quoted:
        return False
    return bool(
        v.lower() in _TYPE_WORDS
        or _DOTTED_RE.match(v)
        or _CALL_RE.match(v)
        or _PATH_RE.match(v)
    )


def _is_plain_name(name: str) -> bool:
    """True for a name that tells what the value is for (a file, a length)."""
    return bool(
        _COUNT_NAME_RE.search(name)
        or _TEXT_NAME_RE.search(name)
        or _PLAIN_END_RE.search(name)
        or _PLAIN_START_RE.search(name)
        or _PLAIN_CAMEL_RE.search(name)
        or _PUBLIC_RE.search(name)
    )


def _value_is_secret(val: str) -> bool:
    """The decision for a value that follows a secret name (quoted or not)."""
    quoted = len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]
    if quoted:
        return not _is_harmless(val[1:-1], True)
    core, _ = _trim(val)
    return not (_is_harmless(val) or _is_harmless(core))


def _is_setting_word(value: str, json_style: bool) -> bool:
    """True for a plain word after the short key "key": a name, not a secret.

    A JSON pair ("Key": "...") is judged with a shorter limit.
    """
    return bool(_SETTING_WORD_RE.fullmatch(value)) and len(value) <= (
        11 if json_style else 24
    )


def _key_sub(m: re.Match[str]) -> str:
    key = m["key"]
    low = key.lower()
    val = m["val"]
    quoted = len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]
    if (m["arrow"] is not None or low in _SHORT_KEYS) and not quoted:
        return m[0]
    if m["colon"] is not None and low in _HEADINGS:
        return m[0]
    if _is_plain_name(key):
        return m[0]
    prefix = m.string[m.start() : m.start("val")]
    if quoted:
        if low == "key" and _is_setting_word(
            val[1:-1], bool(m["q"]) and bool(m["colon"])
        ):
            return m[0]  # <add key="name" value="x"/>, { key: 'name' }
        if low == "key" and _VALUE_NEXT_RE.match(m.string, m.end()):
            return m[0]  # <add key="webpages:Version" value="3.0.0.0"/>
        return m[0] if _is_harmless(val[1:-1], True) else prefix + REMOVED
    core, tail = _trim(val)
    if _is_harmless(val) or _is_harmless(core):
        return m[0]
    if core.isdigit() and _PLURAL_END_RE.search(key):
        return m[0]  # a count: tokens = 512
    if ";" in tail and _CAMEL_RE.match(core):
        return m[0]  # a line of code: token = otpTokenProvider;
    attached = m["eq"] == "="  # KEY=value with no space: a shell or env line
    if m["last"] is None and not _has_digit(core) and not attached:
        return m[0]  # words follow: it is prose
    return prefix + REMOVED + tail


def _structured_sub(m: re.Match[str]) -> str:
    """The rule for the group "val" after a key named in the group "key"."""
    key = m["key"]
    if _is_plain_name(key) or not _value_is_secret(m["val"]):
        return m[0]
    return (
        m.string[m.start() : m.start("val")]
        + REMOVED
        + (m.groupdict().get("tail") or "")
    )


def _url_pw_sub(m: re.Match[str]) -> str:
    if m["scheme"].lower() in ("http", "https"):
        return f"{m['scheme']}://{REMOVED}@"
    return f"{m['scheme']}://{m['user']}:{REMOVED}@"


def _query_sub(m: re.Match[str]) -> str:
    val = m["val"]
    if _is_harmless(val) or val.lower() in _DUMMY_VALUES:
        return m[0]
    name = m["head"].strip("?&;=").lower()
    if name not in _QUERY_ALWAYS and len(val) < QUERY_VALUE_CHARS:
        return m[0]  # "?key=v1": too short for a key
    return m["head"] + REMOVED


def _header_sub(m: re.Match[str]) -> str:
    head = m["head"].lower()
    scheme = m["scheme"] or ""
    val = m["val"]
    if head.endswith("cookie"):
        if "=" not in val or REMOVED in val:
            return m[0]
        if head == "set-cookie":
            # the value of the cookie goes; its attributes (HttpOnly) stay
            pair, mark, rest = val.partition(";")
            name = pair.partition("=")[0]
            return m["head"] + m["sep"] + name + "=" + REMOVED + mark + rest
        return m["head"] + m["sep"] + REMOVED
    if scheme and scheme.strip().lower() != "digest":
        first, _, rest = val.partition(" ")
        if _is_harmless(first, True):
            return m[0]
        return m["head"] + m["sep"] + scheme + REMOVED + (" " + rest if rest else "")
    if scheme:
        return m["head"] + m["sep"] + scheme + REMOVED
    word = val.strip()
    if (
        " " in word
        or len(word) < 10
        or not _has_digit(word)
        or _is_harmless(word, True)
    ):
        return m[0]
    return m["head"] + m["sep"] + REMOVED


def _digest_sub(m: re.Match[str]) -> str:
    return m[0] if REMOVED in m["val"] else m["head"] + REMOVED


def _command_sub(m: re.Match[str]) -> str:
    val = m["val"]
    groups = m.groupdict()
    name = groups.get("name")
    if name is not None and (_is_plain_name(name) or not _WORD_RE.search(name)):
        return m[0]
    quoted = len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]
    if quoted:
        inner = val[1:-1]
    else:
        inner = val
        if val.lower() in _STOP_WORDS:
            return m[0]
        user, sep, pw = val.partition(":")
        if sep and user.isdigit() and pw.isdigit():
            return m[0]  # a user and group number: -u 1000:1000
    tail = groups.get("tail") or ""
    if not quoted and inner.endswith(";"):
        inner, tail = inner[:-1], ";" + tail  # nginx: fastcgi_param X value;
    if _is_harmless(inner, quoted):
        return m[0]
    return m["head"] + REMOVED + tail


def _index_sub(m: re.Match[str]) -> str:
    key = m["key"]
    inner = m["val"][1:-1]
    if _is_plain_name(key) or _is_harmless(inner, True):
        return m[0]
    if key.lower() == "key" and _is_setting_word(inner, False):
        return m[0]
    return m["head"] + REMOVED


def _call_sub(m: re.Match[str]) -> str:
    """The rule for a call such as setPassword("v") or Secret("v").

    The secret word must end the name of the call: token_sort_ratio("a", "b")
    is no setter. With one text argument, that argument goes. With more, the
    last one goes: set_password("service", "user", "password"). A call with
    "credentials" in its name loses every text argument.
    """
    name = m["name"]
    last = name.rsplit(".", 1)[-1]
    if not _SETTER_END_RE.search(last) or _NOT_SETTER_RE.match(last):
        return m[0]
    if _is_plain_name(last):
        return m[0]
    spans = [a.span() for a in re.finditer(_QUOTED, m["val"])]
    if "credentials" not in name.lower():
        spans = spans[-1:]
    val = m["val"]
    out, done = [], 0
    for start, end in spans:
        if _is_harmless(val[start + 1 : end - 1], True):
            continue
        out.append(val[done:start] + REMOVED)
        done = end
    if not out:
        return m[0]
    return m.string[m.start() : m.start("val")] + "".join(out) + val[done:]


def _xml_element_sub(m: re.Match[str]) -> str:
    tag = m["tag"]
    attrs = m["attrs"] or ""
    secret_tag = bool(_WORD_RE.search(tag)) and not _is_plain_name(tag)
    if not secret_tag:
        for attr in _XML_ATTR_RE.finditer(attrs):
            name = attr["name"].lower()
            if name in ("name", "key", "id") and _WORD_RE.search(attr["val"]):
                if not _is_plain_name(attr["val"]):
                    secret_tag = True
    if not secret_tag or not _value_is_secret(m["val"].strip()):
        return m[0]
    whole = m[0]
    start = m.start("val") - m.start()
    return whole[:start] + REMOVED + whole[start + len(m["val"]) :]


def _xml_tag_sub(m: re.Match[str]) -> str:
    attrs = m["attrs"]
    found = [
        a
        for a in _XML_ATTR_RE.finditer(attrs)
        if a["name"].lower() in ("name", "key") and _WORD_RE.search(a["val"])
    ]
    if not found or any(_is_plain_name(a["val"]) for a in found):
        return m[0]
    changed = False

    def fix(attr: re.Match[str]) -> str:
        nonlocal changed
        if attr["name"].lower() != "value" or not _value_is_secret(attr["val"]):
            return attr[0]
        changed = True
        return f"{attr['name']}{attr['eq']}{attr['q']}{REMOVED}{attr['q']}"

    new = _XML_ATTR_RE.sub(fix, attrs)
    if not changed:
        return m[0]
    start = m.start("attrs") - m.start()
    return m[0][:start] + new + m[0][start + len(attrs) :]


def _table_sub(m: re.Match[str]) -> str:
    key = m["key"]
    val = m["val"].strip("`")
    if _is_plain_name(key) or val.lower() in _TABLE_STOP or set(val) <= set("-:"):
        return m[0]
    if _is_harmless(val) or _PASCAL_RE.match(val) or val.isalpha() and val[0].isupper():
        return m[0]  # a word that describes the column, not a value
    return m["head"] + REMOVED


def _prose_sub(m: re.Match[str]) -> str:
    val = m["val"]
    quoted = val[0] in "`\"'"
    core = val.strip("`\"'") if quoted else _trim(val)[0]
    if _is_harmless(core, quoted):
        return m[0]
    if m["copula"]:
        secret = quoted or _has_digit(core)
    elif quoted:
        secret = _has_digit(core)
    else:
        secret = len(core) >= 8 and _has_digit(core) and any(c.isalpha() for c in core)
    if not secret:
        return m[0]
    tail = "" if quoted else _trim(val)[1]
    return m["key"] + m["gap"] + REMOVED + tail


def _gradle_sub(m: re.Match[str]) -> str:
    if not _WORD_RE.search(m["key"].rsplit(".", 1)[-1]):
        return m[0]
    return _structured_sub(m)


def _login_sub(m: re.Match[str]) -> str:
    val = m["val"]
    core = _trim(val)[0]
    letters = any(ch.isalpha() for ch in core)
    if not (letters and _has_digit(core)) or _is_harmless(core):
        return m[0]  # "Login with GitHub / GitLab" and "/ 100" stay
    return m["head"] + REMOVED + _trim(val)[1]


def _pair_sub(m: re.Match[str]) -> str:
    if _PASCAL_RE.match(m["key"]):
        return m[0]  # a text such as "EmailToken", not the name of a setting
    return _structured_sub(m)


def _auth_pair_sub(m: re.Match[str]) -> str:
    return m[0] if _is_harmless(m["val"]) else m["head"] + REMOVED


def _auth_sub(m: re.Match[str]) -> str:
    if not _has_digit(m["val"]):
        return m[0]  # the word "basic" or "bearer" in prose
    return f"{m['kind']}{m['gap']}{REMOVED}"


def _long_sub(m: re.Match[str]) -> str:
    token = m[0]
    if _DIGEST_BEFORE_RE.search(m.string[max(0, m.start() - 24) : m.start()]):
        return token  # an image digest or a commit id: a name, not a secret
    if "/" in token:  # a path: judge each part alone
        parts = token.split("/")
        return "/".join(
            REMOVED if len(p) >= 24 and looks_random(p) else p for p in parts
        )
    return REMOVED if looks_random(token) else token


def _drop_orphan_ends(text: str) -> str:
    """Remove a key body that lost its BEGIN line: the lines above an END line."""
    if "END " not in text:
        return text
    out: list[str] = []
    pos = 0
    for m in _END_RE.finditer(text):
        window = max(pos, m.start() - 6000)
        cut = text.rfind("\n\n", window, m.start())
        if cut != -1:
            begin = cut + 2
        elif window > pos:
            nl = text.find("\n", window, m.start())
            begin = nl + 1 if nl != -1 else m.start()
        else:
            begin = pos
        out.append(text[pos:begin])
        out.append(REMOVED)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _yaml_values(text: str) -> str:
    """The values of a YAML key on the next lines: a block or one word."""
    if ":" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    count = len(lines)
    while i < count:
        line = lines[i]
        out.append(line)
        i += 1
        stripped = line.rstrip()
        if not stripped or ":" not in stripped or not _BLOCK_TAIL_RE.search(stripped):
            continue
        m = _BLOCK_KEY_RE.match(line)
        if m is None:
            continue
        key = m["key"]
        if key.lower() in _HEADINGS or _is_plain_name(key):
            continue
        base = len(m["indent"])
        if m["block"]:
            j = i
            while j < count and j - i < MAX_BLOCK_LINES:
                body = lines[j]
                if body.strip() and _indent(body) <= base:
                    break
                j += 1
            while j > i and not lines[j - 1].strip():
                j -= 1
            if j > i:
                first = next(b for b in lines[i:j] if b.strip())
                out.append(first[: _indent(first)] + REMOVED)
                i = j
            continue
        if i >= count:
            continue
        nxt = lines[i]
        word = nxt.strip()
        if _indent(nxt) <= base or not word:
            continue
        if word[0] in "-#{[" or _MAPPING_RE.match(word):
            continue
        if word[0] not in "\"'" and " " in word:
            continue  # prose, not a value
        if not _value_is_secret(word):
            continue
        out.append(nxt[: _indent(nxt)] + REMOVED + ("\r" if nxt.endswith("\r") else ""))
        i += 1
    return "\n".join(out)


def remove_secrets(text: str) -> str:
    """Replace values that look like secrets with <removed>."""
    low = text.lower()
    text = _BLOCK_RE.sub(REMOVED, text)
    text = _drop_orphan_ends(text)
    if "://" in text:
        text = _URL_PW_RE.sub(_url_pw_sub, text)
        text = _URL_USER_RE.sub(lambda m: f"{m['scheme']}{REMOVED}@", text)
    text = _QUERY_RE.sub(_query_sub, text)
    if "<" in text:
        text = _XML_ELEMENT_RE.sub(_xml_element_sub, text)
        text = _XML_TAG_RE.sub(_xml_tag_sub, text)
    if "name:" in low:
        text = _K8S_RE.sub(_structured_sub, text)
    text = _yaml_values(text)
    if "authorization" in low or "cookie" in low:
        text = _DIGEST_RE.sub(_digest_sub, text)
        text = _HEADER_RE.sub(_header_sub, text)
    for gate, rule in _COMMAND_RULES:
        if gate in text:
            text = rule.sub(_command_sub, text)
    for gates, rule in _LOWER_RULES:
        if any(g in low for g in gates):
            text = rule.sub(_command_sub, text)
    text = _YAML_LIST_RE.sub(_structured_sub, text)
    if "variable" in low:
        text = _TERRAFORM_RE.sub(_structured_sub, text)
    text = _INDEX_RE.sub(_index_sub, text)
    text = _CALL_NAME_RE.sub(_call_sub, text)
    text = _PAIR_RE.sub(_pair_sub, text)
    text = _GRADLE_RE.sub(_gradle_sub, text)
    if "auth" in low:
        text = _AUTH_BASE64_RE.sub(lambda m: m["head"] + REMOVED, text)
    text = _KEY_RE.sub(_key_sub, text)
    text = _AUTH_PAIR_RE.sub(_auth_pair_sub, text)
    if "|" in text:
        text = _TABLE_RE.sub(_table_sub, text)
    text = _PROSE_RE.sub(_prose_sub, text)
    if "/" in text:
        text = _LOGIN_RE.sub(_login_sub, text)
    text = _AUTH_RE.sub(_auth_sub, text)
    if "hooks.slack.com" in text or "webhooks/" in text:
        text = _WEBHOOK_RE.sub(lambda m: m["head"] + REMOVED, text)
    text = _HASH_RE.sub(REMOVED, text)
    text = _KNOWN_RE.sub(REMOVED, text)
    return _LONG_RE.sub(_long_sub, text)


def remove_secrets_in_names(text: str) -> str:
    """The rule for lists of file names: only keys with a fixed start."""
    return _KNOWN_RE.sub(REMOVED, text)
