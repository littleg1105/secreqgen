# tuning_lib and run_tuning.py: design of step 0

Status: design, 2026-11-16. Five builders work from this file at the same time. Each interface below is exact: names, argument names, types and error classes. If a builder needs a change, the builder tells the main session. The builder does not change the interface alone.

All paths in this file are relative to the workspace root (the thesis folder) unless they start with `scripts/` or `tests/`. Those two start in the code repository `coded_project/secreqgen/`. No absolute path and no account name may appear in any file that the builders write (see the privacy guard, section 2.6).

## 0. Summary

Step 0 builds a runner. It sends the old configuration, unchanged, to two hosted models through OpenRouter and measures the answers with scripts. Its first use is the anchor run: the stored prompts of Study 2b, arms A2 and B2, 15 stories, 2 models, 2 repeats = 120 generations. The result is a table: hosted "name use" against the local values in `real_test_cases/study2b/analysis_s2b.json`, with the limits 8.0 points (B2) and 5.0 points (A2).

Main decisions:

1. The runner does not use the old generator class. For the anchor, the prompts are stored byte for byte in the Study 2b records, so the runner sends them as they are. The old generator cannot record each attempt, keeps the raw reply nowhere, and has three silent faults (section 11). The runner has its own attempt loop that mirrors the old retry rule. It reuses the old validation code (`_parse_requirement`, `_parse_threat_mapping`) so that a requirement is accepted or refused exactly as before.
2. The client has the call surface of the Ollama client (`generate(prompt, system_prompt, format)` returning `LLMResponse`, and `model_name`), so the old generator can run with it in a later step. The runner calls the richer method `generate_reply`.
3. Registered code is imported only when the import is light and has no side effect (`scripts/analyze_study2.py`: stdlib only, loaded by file path). The heavy drivers (`run_study2.py`, `run_study2b.py`) are never imported. They load the embedder and the vector store at import time. Their few constants and one function are copied, with tests that read the source text of the registered file and compare.
4. Every write goes through the guard (`guarded_write_text`, `guarded_append_line`, `guarded_mkdir`). Only `real_test_cases/tuning/` and `real_test_cases/final_test/` can be written.
5. No fallback is silent. Each unexpected state raises a named error with a plain message. Where the old code has a fallback (for example a requirement that is dropped), the new code counts it and writes the count in the record and in the report.
6. The cost cap (default 1.00 USD) and a time cap (default 60 minutes, new) stop the run. A stopped run can be resumed. This follows the request of the author: "if it takes too long or is too expensive, cut it".

Expected size of the anchor run: 120 generations, about 660k prompt tokens and 270k answer tokens in all. At the model-record prices this is roughly 0.40 USD. That is under the cap.

## 1. Files, owners and build order

| Builder | Files (all new) | Test file |
|---|---|---|
| 1 | `scripts/tuning_lib/paths.py`, `scripts/tuning_lib/guard.py` | `tests/unit/test_tuning_guard.py` |
| 2 | `scripts/tuning_lib/openrouter_client.py` | `tests/unit/test_tuning_openrouter_client.py` |
| 3 | `scripts/tuning_lib/passages.py`, `scripts/tuning_lib/parse.py` | `tests/unit/test_tuning_passages.py`, `tests/unit/test_tuning_parse.py` |
| 4 | `scripts/tuning_lib/records.py`, `scripts/tuning_lib/measures.py` | `tests/unit/test_tuning_records.py`, `tests/unit/test_tuning_measures.py` |
| 5 | `scripts/run_tuning.py` | `tests/unit/test_tuning_runner.py` |

`paths.py` is small and fixed in section 2.1. Builder 1 writes it first. Everyone else can code against the names in section 2.1 at once.

Dependency direction (an arrow means "imports"): `run_tuning` to everything; `measures` to `records`, `paths`; `records` to `guard`; `passages`, `parse`, `openrouter_client` to `guard` and `paths`; `guard` to `paths`. No cycle.

Import rules for every module:
- Importable with no side effect and with no network. At import time: no file read, no environment read, no subprocess, no keychain call, no directory creation. Values that need the environment (home folder, account name) are computed inside functions.
- Standard library plus `httpx`, `pydantic`, `secreqgen.generation.*` only. No new dependency. `secreqgen.generation` is light (httpx, pydantic, stdlib). Never import `secreqgen.retrieval`, `secreqgen.ingestion`, `chromadb`, `sentence_transformers`, `run_study2`, `run_study2b`, `run_cross_family_winning_config`.
- Tests put `scripts/` and `src/` on `sys.path` at the top of the test file (`sys.path.insert(0, ...)`). The test command from the task sets `PYTHONPATH=src` already.
- `from __future__ import annotations` at the top. Python 3.13. Type hints on every public function.

## 2. guard.py (and paths.py)

### 2.1 paths.py (exact content)

```python
from pathlib import Path

WORKSPACE: Path = Path(__file__).resolve().parents[4]        # the thesis root
CODED: Path = WORKSPACE / "coded_project"
SECREQGEN: Path = CODED / "secreqgen"
SCRIPTS: Path = SECREQGEN / "scripts"
RTC: Path = CODED / "real_test_cases"
STUDY2: Path = RTC / "study2"
STUDY2B: Path = RTC / "study2b"
TUNING_ROOT: Path = RTC / "tuning"
FINAL_TEST_ROOT: Path = RTC / "final_test"
DATA: Path = SECREQGEN / "data"
EVIDENCE: Path = WORKSPACE / "thesis" / "evidence"
HASH_LISTS: dict[str, Path] = {
    "study2": EVIDENCE / "study2_registered_hashes_2026-11-14.sha256",
    "study2b": EVIDENCE / "study2b_hashes_2026-11-16.sha256",
    "vectordb": EVIDENCE / "vectordb_hashes_2026-11-14.sha256",
}
PROJECTS: tuple[str, ...] = ("mastodon", "keycloak", "bitwarden")
MODELS: tuple[str, ...] = ("qwen3.8:27b", "gemma4:26b")   # local names; they are the "model" key of every record
FALLBACK_CONTEXT: str = "No relevant security documentation found."   # 41 characters; retriever.py:343
ANALYSIS_S2B: Path = STUDY2B / "analysis_s2b.json"

def model_slug(model: str) -> str:
    """Folder name of a model. Same rule as _slug in run_cross_family_winning_config.py."""
    return model.replace(":", "_").replace(".", "_").replace("/", "_")
```

`parents[4]` holds because the file is `scripts/tuning_lib/paths.py` (0 tuning_lib, 1 scripts, 2 secreqgen, 3 coded_project, 4 thesis root). A test checks that `WORKSPACE / "thesis"` and `STUDY2B` exist, and that `model_slug("qwen3.8:27b") == "qwen3_8_27b"` and that folder exists below `STUDY2B / "bitwarden"`.

### 2.2 Errors and the write guard

```python
class GuardError(Exception):
    """A guard refused. The message says which rule and which path. It never holds a secret."""

class WriteGuard:
    def __init__(
        self,
        allowed_roots: Sequence[Path] = (TUNING_ROOT, FINAL_TEST_ROOT),
        forbidden_roots: Sequence[Path] = (STUDY2, STUDY2B, DATA),
        forbidden_names: Sequence[str] = ("study2", "study2b"),
    ) -> None: ...

    def check(self, path: str | Path) -> Path:
        """Return the resolved path, or raise GuardError."""

DEFAULT_GUARD: WriteGuard          # built with the defaults above, at import time, from constants only (no file access)

def assert_writable(path: str | Path) -> Path: ...        # DEFAULT_GUARD.check(path)
def guarded_mkdir(path: str | Path, *, guard: WriteGuard = DEFAULT_GUARD) -> Path: ...                  # parents=True, exist_ok=True
def guarded_write_text(path: str | Path, text: str, *, guard: WriteGuard = DEFAULT_GUARD, encoding: str = "utf-8") -> Path: ...
def guarded_append_line(path: str | Path, line: str, *, guard: WriteGuard = DEFAULT_GUARD, fsync: bool = True) -> Path: ...
```

Rules of `WriteGuard.check` (all must hold, in this order):

1. The path must be absolute. A relative path is refused. It is never resolved against the current folder.
2. `resolved = Path(path).expanduser().resolve(strict=False)`. This follows every symbolic link that exists.
3. Forbidden roots. If `resolved` is a forbidden root or below one, refuse. Use the same method as `is_inside` in `run_study2b.py:277-297`: compare the resolved paths, compare them again in lower case (this Mac ignores case: `STUDY2` and `study2` are one folder), and for each existing ancestor use `os.path.samefile` against the root. Copy this logic. Do not import it.
4. Forbidden names. If any part of `resolved.parts` equals, ignoring case, one of `forbidden_names`, refuse. This catches `tuning/study2/...` and a run named `study2b`.
5. Allowed roots. `resolved` must be an allowed root or below one (same inside test as rule 3). Else refuse.
6. The message names the rule number, the path and the root. It never prints file content.

`guarded_write_text` writes to a temporary file in the same folder (the name is `<name>.tmp-<pid>`, also checked by the guard), calls `fsync`, then `os.replace`. `guarded_append_line` opens with `"a"`, writes `line` (it adds `"\n"` when missing), flushes, and calls `os.fsync` when `fsync` is true (this copies `append_record` of `run_study2b.py:1033-1037`). Each of the three guarded functions calls `guard.check` first. Nothing in the new code calls `open(..., "w")`, `Path.write_text`, `os.makedirs` or `shutil` on a target path outside these functions. A test greps the source of `run_tuning.py` and the `tuning_lib` modules for `write_text(`, `open(` with a write mode, `mkdir(`, `os.makedirs`, `shutil`, `os.replace` and `os.rename`, and accepts only the lines in `guard.py`.

### 2.3 Run lock

```python
LOCK_NAME = "run_tuning.lock"

class RunLock:
    def __init__(self, run_dir: Path, *, guard: WriteGuard = DEFAULT_GUARD,
                 pid_alive: Callable[[int], bool] = _pid_alive) -> None: ...
    def acquire(self) -> None: ...
    def release(self) -> None: ...
    def __enter__(self) -> "RunLock": ...      # acquire
    def __exit__(self, *exc) -> None: ...      # release
```

- `acquire` creates `run_dir / LOCK_NAME` with `os.open(..., O_CREAT | O_EXCL | O_WRONLY)` (through the guard) and writes JSON `{"pid": int, "started_utc": str, "token": str}`. The folder is created first with `guarded_mkdir`.
- If the file exists, `acquire` raises `GuardError`. The message says the process number and whether it is alive (`pid_alive`). It never deletes the file. For a dead process it says: "the process N is gone; delete this file by hand and start again".
- `release` deletes the file only when its `token` is the token of this object. Else it raises `GuardError`.
- `_pid_alive(pid)` uses `os.kill(pid, 0)`. `PermissionError` means alive.

### 2.4 Other experiment processes

```python
OTHER_SCRIPT_NAMES: tuple[str, ...] = (
    "run_study2.py", "run_study2b.py", "run_tuning.py", "run_final_test.py",
    "run_cross_family_winning_config.py", "run_study2_ai_raters.py",
)

@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    command: str

def list_processes() -> list[ProcessRow]: ...    # /bin/ps -axo pid=,ppid=,command= (as run_study2b.py:316-323)
def find_other_experiment_processes(rows: Sequence[ProcessRow], *, own_pid: int | None = None,
                                    names: Sequence[str] = OTHER_SCRIPT_NAMES) -> list[ProcessRow]: ...
def assert_no_other_experiment(rows: Sequence[ProcessRow] | None = None) -> None: ...   # raises GuardError
```

Matching rule (a substring regex is not allowed: `grep run_tuning.py` must not match):
1. Split `command` with `shlex.split`. On `ValueError` use `str.split`.
2. Look at the first 6 tokens. Find the first token whose base name matches `^python[0-9.]*$` (ignoring case). Wrappers such as `caffeinate`, `nohup`, `time`, `env`, `arch` before it are fine.
3. After it, skip tokens that start with `-`. If a skipped token is `-m` or `-c`, the process is not a script run: no match. The first remaining token is the script. Match when its base name is in `names`.
4. A command whose first token is itself a script path (a shebang start) matches the same way.

Own process and its ancestors (walk `ppid` up to 1) are never reported. `assert_no_other_experiment` lists at most 3 rows (pid and the first 120 characters of the command) in the message. `own_pid` defaults to `os.getpid()`.

### 2.5 Hash lists

```python
@dataclass(frozen=True)
class HashListResult:
    name: str                      # "study2", "study2b" or "vectordb" (or the file stem)
    path: str                      # workspace-relative path of the list
    ok: bool
    n_listed: int
    n_hashed: int                  # files that were read and hashed
    n_existence_only: int          # sealed files: only is_file() was called
    changed: tuple[str, ...]       # listed paths whose hash differs
    missing: tuple[str, ...]       # listed paths that do not exist
    detail: str

def is_sealed_path(path: Path) -> bool: ...   # True for: below STUDY2/"review", or any part == "key_do_not_open", or below WORKSPACE/"final_week"/"reports"/"_sealed"

def check_hash_list(path: Path, *, root: Path = WORKSPACE,
                    existence_only: Callable[[Path], bool] = is_sealed_path,
                    chunk_size: int = 1 << 20) -> HashListResult: ...
def check_all_hash_lists(names: Sequence[str] = ("study2", "study2b", "vectordb")) -> dict[str, HashListResult]: ...
def assert_hash_lists_ok(results: Mapping[str, HashListResult]) -> None: ...    # raises GuardError listing at most 10 bad paths
```

Format of the three lists (read on 2026-11-16): one line per file, `<64 hex digits><two spaces><path relative to the workspace root>`. The paths start with `coded_project/`. The study2 list holds 38 files and includes the sealed paths `.../study2/review/*` and `.../key_do_not_open/review_key.json`. The study2b list holds 18 files. The vectordb list holds 764 files (about 700 MB). `check_hash_list` rules:

- A line that does not match `^[0-9a-f]{64}  (\S.*)$` raises `GuardError` (a malformed list is never skipped). An empty list raises. A path that is absolute or has a `..` part raises.
- For a sealed path: call only `Path.is_file()`. Never open it. This is the rule of `check_hash_list` in `run_study2b.py:591-618`.
- Other files: hash with `hashlib.sha256` in chunks. A missing file goes to `missing`. A different hash goes to `changed`.
- Returns a result. It does not raise for changed or missing files. `assert_hash_lists_ok` raises.
- The study2 list in `run_study2b.py` also requires 8 base names to be present. That check is not copied: it belongs to the old script.
- Reading the vectordb files is a plain file read. It never opens ChromaDB. Note: the folder is in iCloud Drive. An evicted file makes a read wait while it downloads. The manifest records the time of each check.

### 2.6 Privacy guard

```python
def private_markers() -> tuple[str, ...]: ...
def assert_no_private_text(text: str, *, what: str = "text", markers: Sequence[str] | None = None) -> None: ...
```

- `private_markers()` returns, at call time and without duplicates: `"/Users/"`, `str(Path.home())`, `Path.home().name`, `getpass.getuser()`, and `os.environ.get("USER")` when set. Matching is case-insensitive (`casefold`). A marker with fewer than 3 characters raises `GuardError` ("too short to check safely"). It is never skipped without a message.
- `assert_no_private_text` raises `GuardError` when a marker is in `text`. The message says `what`, and the kind of marker ("home folder path", "account name", "users path"). It never prints the matched text or the marker.
- It is called: (a) on the full JSON body of each request before it is sent (`build_request_body` does this), (b) on the JSON text of each record before it is written (`records.new_record` does this), (c) on the manifest text before it is written, (d) on `DESIGN`-like report files before they are written. Hence the manifest stores workspace-relative paths only.
- The design file and the code must not contain the account name as a literal. Tests build their sample text from `getpass.getuser()` at run time.

## 3. openrouter_client.py

### 3.1 Configuration constant (the main session can edit this)

```python
@dataclass(frozen=True)
class ModelPin:
    local_name: str                  # the key used in records: "qwen3.8:27b" or "gemma4:26b"
    model_id: str                    # OpenRouter model id
    provider_tag: str                # one endpoint tag, for example "parasail/bf16"
    expected_provider_name: str      # what the answer must say, for example "Parasail"
    top_p: float
    top_k: int
    extra_sampling: Mapping[str, float]   # more sampling values to send (empty for Gemma)
    price_in_per_mtok: float         # USD per 1M input tokens, for a cost ESTIMATE only
    price_out_per_mtok: float        # USD per 1M output tokens, for a cost ESTIMATE only
    confirmed: bool                  # False until the first call showed that the pin works

HOSTED_MODELS: dict[str, ModelPin] = {
    "gemma4:26b": ModelPin(
        local_name="gemma4:26b", model_id="google/gemma-4-26b-a4b-it",
        provider_tag="parasail/bf16", expected_provider_name="Parasail",   # TO CONFIRM AT THE FIRST CALL
        top_p=0.95, top_k=64, extra_sampling={},
        price_in_per_mtok=0.09, price_out_per_mtok=0.30, confirmed=False),
    "qwen3.8:27b": ModelPin(
        local_name="qwen3.8:27b", model_id="qwen/qwen3.8-27b",
        provider_tag="deepinfra/bf16", expected_provider_name="DeepInfra",  # TO CONFIRM AT THE FIRST CALL
        top_p=0.95, top_k=20,
        extra_sampling={"min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0},
        price_in_per_mtok=0.15, price_out_per_mtok=1.875, confirmed=False),
}
SAMPLING_COMMON = {"temperature": 0.1, "max_completion_tokens": 4096}
KEYCHAIN_SERVICE = "openrouter-api-key"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"
```

Where the sampling values come from (read on 2026-11-16 from the Ollama manifests on disk, no Ollama call): the model file of `gemma4:26b` sets `temperature 1, top_k 64, top_p 0.95`. The model file of `qwen3.8:27b` sets `min_p 0, presence_penalty 0, repeat_penalty 1, temperature 1, top_k 20, top_p 0.95`. The local client overrides the temperature with 0.1 and sets 4,096 output tokens, so the other values of the model file were in force during Study 2 and Study 2b. The hosted request sends them explicitly, because a value that is not sent takes the provider default, and the provider default can differ (the Qwen model card advises other values). The name of the OpenRouter field for the repeat penalty is `repetition_penalty`. Whether the pinned endpoint accepts `min_p`, `presence_penalty` and `repetition_penalty` is one of the things to confirm at the first call. With `require_parameters: true`, an endpoint that does not support a field makes the request fail with a clear error. That is the wanted behaviour (no silent drop).

The price fields are the prices in the model record of 2026-11-16 (one endpoint). They are only for the dry-run cost estimate. The real cost always comes from `usage.cost` of the answer.

### 3.2 Request

```python
def build_request_body(pin: ModelPin, system_prompt: str | None, user_prompt: str) -> dict: ...
def build_headers(key: "ApiKey") -> dict[str, str]: ...
```

`build_request_body` returns exactly these keys (a test compares the key set):

```json
{
  "model": "<pin.model_id>",
  "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
  "stream": false,
  "temperature": 0.1,
  "top_p": 0.95,
  "top_k": 64,
  "max_completion_tokens": 4096,
  "reasoning": {"effort": "none"},
  "provider": {"order": ["<tag>"], "only": ["<tag>"], "allow_fallbacks": false,
               "require_parameters": true, "data_collection": "deny"},
  "plugins": [{"id": "context-compression", "enabled": false}]
}
```

plus the keys of `pin.extra_sampling` at the top level. The system message is left out when `system_prompt` is `None` or empty (the old Ollama client does the same: it sends `system` only when set). `build_request_body` calls `assert_no_private_text` on the JSON text. It never adds: `response_format`, `user`, `session_id`, `metadata`, `trace`, `models`, `seed`, `transforms`, `route`. Reasons: the local runs send no format (the MLX runner returns 501 for structured output), no seed (the local client sends none, and a seed does not make a hosted run repeatable), and any identifier field can reveal the author.

`build_headers` returns exactly `Authorization: Bearer <key>`, `Content-Type: application/json`, `X-OpenRouter-Metadata: enabled` (this adds `openrouter_metadata`, which tells the provider), `X-OpenRouter-Cache: false`. No `HTTP-Referer`, no `X-Title`, no `X-OpenRouter-Title`, no `X-OpenRouter-Categories`. The `User-Agent` stays the generic one of httpx.

### 3.3 The key

```python
class ApiKey:
    def __init__(self, value: str) -> None: ...
    def reveal(self) -> str: ...              # used only inside build_headers
    def __repr__(self) -> str: ...            # "ApiKey(***)"
    __str__ = __repr__

def load_api_key(*, environ: Mapping[str, str] = os.environ,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 account: str | None = None) -> ApiKey: ...
```

- First `environ["OPENROUTER_API_KEY"]` (a non-empty value after strip).
- Else `run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account or getpass.getuser(), "-w"], capture_output=True, text=True, timeout=20)`. The key is `stdout.strip()`. A return code other than 0, a timeout, a missing binary or an empty output raises `FatalHostedError("the OpenRouter key was not found ...")` with advice how to store it (the `security add-generic-password` form with `-w` at the end so it prompts). The error text never holds stdout or the key.
- `load_api_key` is called only by `run_tuning.py` in a real run, once, in the main thread, after the dry-run checks. Never in `--dry-run`. Tests give `environ={}` and a fake `run`. No test calls the real `security` command or `keyring`.
- The key is never printed, logged, put into an exception message, a record, a manifest or a report. `HostedReply.to_dict()` never holds headers.

### 3.4 Errors

```python
class HostedError(Exception):
    status: int | None
    reply: "HostedReply | None"       # a partial reply, when a paid answer arrived but was refused

class TransientHostedError(HostedError): ...   # 429, 5xx, timeout (after the retries), connection failure
class FatalHostedError(HostedError): ...       # the run must stop
class ContractViolation(FatalHostedError): ... # the answer broke a rule of the design (reasoning tokens above 0, provider other than the pin, wrong model)
```

### 3.5 The reply and the client

```python
@dataclass(frozen=True)
class HostedReply:
    text: str
    generation_id: str                 # "gen-..."
    response_model: str                # the "model" field of the answer
    provider: str                      # for example "Parasail"
    provider_source: str               # "metadata" or "generation_endpoint"
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int             # as OpenRouter counts it (reasoning tokens included)
    reasoning_tokens: int
    reasoning_tokens_source: str       # "usage" or "absent_assumed_zero"
    cached_tokens: int
    cost_usd: float
    latency_ms: float
    transport_retries: int
    def to_dict(self) -> dict: ...     # JSON-safe; no headers, no key, no body

class OpenRouterClient:
    model_name: str                    # = pin.model_id (the old generator reads this)
    last_reply: HostedReply | None

    def __init__(self, pin: ModelPin, key: ApiKey, *,
                 transport: httpx.BaseTransport | None = None,
                 timeout_s: float = 300.0,
                 max_transport_retries: int = 3,
                 backoff_s: Sequence[float] = (2.0, 4.0, 8.0),
                 sleep: Callable[[float], None] = time.sleep,
                 structured_output: bool = False) -> None: ...
    def generate_reply(self, prompt: str, system_prompt: str | None = None) -> HostedReply: ...
    def generate(self, prompt: str, system_prompt: str | None = None, format: str | None = None) -> LLMResponse: ...
    def close(self) -> None: ...
```

- One instance for each worker thread. An instance holds its own `httpx.Client(transport=transport, timeout=timeout_s)`, its own `last_reply`, and no module-level mutable state. The runner builds clients through a factory and keeps one in a `threading.local()`.
- `generate` is the old surface. It calls `generate_reply`, stores `self.last_reply`, and returns `secreqgen.generation.llm_client.LLMResponse(content=reply.text, model=reply.response_model, prompt_tokens=..., completion_tokens=..., total_tokens=..., latency_ms=..., raw_response=reply.to_dict())`. The `format` argument is accepted. As in the local client with `structured_output=False`, it is not sent. If `structured_output=True`, `generate` raises `NotImplementedError` (the design has no JSON mode, and the old local runs had none).
- There is no `check_health` (the generator does not call it and it would be a network call).
- `generate_reply` steps:
  1. `body = build_request_body(...)` (privacy check inside).
  2. POST `API_URL` with `build_headers(key)`.
  3. Retry rule. Retry on HTTP 429, on any status from 500 to 599, and on `httpx.TimeoutException`. At most `max_transport_retries` retries. The wait before retry `i` is `Retry-After` (integer seconds, at most 60) when the header exists, else `backoff_s[i]`. Call the injected `sleep`. After the last retry raise `TransientHostedError` with the status. Count the retries in `transport_retries`.
  4. Other failures raise at once: `httpx.ConnectError` and other `httpx.HTTPError` raise `TransientHostedError` (no retry in the client; the runner decides). HTTP 400 raises `FatalHostedError` with the `error.message` of the body (for example "a parameter is not supported by the endpoint"). 401 and 403: `FatalHostedError("the key was refused or its limit is spent")`. 402: `FatalHostedError("not enough credit")`. 404: `FatalHostedError("no endpoint matches the pin (check model id and provider tag)")`. Any other 4xx: `FatalHostedError`. The text of a message from the server is cut at 500 characters. The headers and the request body are never in the message.
  5. A 200 answer that is not JSON raises `FatalHostedError`. A 200 answer with a top-level `error` object: if `error.code` is 429 or from 500 to 599, treat it as step 3 (retry); else `FatalHostedError` with code and message.
  6. `choices` missing or empty raises `FatalHostedError`. `choices[0].error` present raises `FatalHostedError`. `choices[0].message.content` that is `None` becomes `""` (the old client also returns an empty string; the runner then sees an unreadable answer and retries, and the attempt is recorded).
  7. `usage.cost` missing or not a number raises `FatalHostedError` ("cost is missing, so the cost cap cannot work"). `usage.prompt_tokens` and `usage.completion_tokens` must be integers, else `FatalHostedError`.
  8. Reasoning check. `reasoning_tokens = usage.completion_tokens_details.reasoning_tokens`. If the field is missing: it counts as 0 only when the message holds no `reasoning` and no `reasoning_details` text, and then `reasoning_tokens_source = "absent_assumed_zero"` (the report shows how many). If `reasoning_tokens > 0`, or reasoning text exists, raise `ContractViolation` with the partial reply attached (the call was paid; the runner adds its cost).
  9. Provider. Read `openrouter_metadata` of the body: the entry of `endpoints.available[]` with `selected` true gives `provider`. If there is no usable metadata, call `GET GENERATION_URL` with `params={"id": generation_id}` and read `provider_name` (up to 3 tries, waits 1 s, 2 s, 4 s through `sleep`, because the record can lag), `provider_source = "generation_endpoint"`. If neither works raise `FatalHostedError("the provider cannot be read")`. The generation call carries only the id, no prompt. The exact shape of the metadata is from the documentation of 2026-11-16 and is untested: confirm at the first call.
  10. Checks that raise `ContractViolation` (with the partial reply): provider (ignoring case and spaces) is not `pin.expected_provider_name`; `response_model` does not start with `pin.model_id`; more than one entry in `openrouter_metadata.attempts` (the pin allows exactly one).
  11. `finish_reason` is stored as given. It is not a client error: a value `"length"` is an unreadable answer for the runner (the outer object is cut), and the report counts it.
- `latency_ms` is the wall time of the successful request, with `time.perf_counter`.

## 4. passages.py

Purpose: read-only access to stored prompts and stored passages; split a stored context into passages.

### 4.1 What is stored, and where

Study 2b record (`real_test_cases/study2b/<project>/<model slug>/records.jsonl`, one line per story, arm, run and attempt): the fields `prompt_system`, `prompt_user_sent` (the whole user prompt as sent), `prompt_user_non_doc` (the sent prompt with the context removed), `doc_context_ref` (`"frozen"` for B2, `null` for A2), `doc_context_sha256`, `prompt_system_sha256`, `prompt_user_sha256`, `prompt_tokens`, `completion_tokens`, `n_requirements`, `error`, `llm_calls`. The A2 and B2 arms have one run (`run == 1`) and one line each. One A2 record has `n_calls_sent == 2` (a local parse retry). It is still one line.

Stored passages: `real_test_cases/study2b/<project>/<model slug>/retrieval_frozen.json` (a byte copy of the registered file in `study2/<project>/results/<model slug>/`). It maps `story_id` to an object with `query_text`, `n_chunks`, `n_code_chunks_guess`, `above_threshold`, `retrieval_ms`, `context_chars`, `context_sha256`, `context`. `n_code_chunks_guess` is wrong in many stories (4 stored where the text holds 0). Do not use it. Count the marks in the text instead.

Study 2 records (`study2/<project>/results/<model slug>/records.jsonl`) hold `prompt_system` and `prompt_user_non_doc` and `doc_context_ref`, but no `prompt_user_sent`. A Study 2 B or C prompt needs the prompt builder to rebuild it. Step 0 does not need that.

In a B2 user prompt the context occurs exactly one time. It is preceded by `"...You MUST reference this documentation when generating requirements.\n\n"` and followed by `"\n\n---\n\n## User Story to Analyze"`.

### 4.2 Text marks of a stored context (checked on all 60 stored contexts: 252 documentation passages, 216 code hits)

- A passage starts at a line that matches `^### \[(\d+)\] (.+) \(Relevance: ([0-9]+\.[0-9]+)\)$`. The numbers run 1, 2, 3 ... with no gap. The line holds the source label and the relevance.
- Passages are separated by exactly `"\n\n---\n\n"` (a blank line, `---`, a blank line). The last passage has no separator after it. Do not split on `---` alone: documentation text can hold a Markdown rule.
- A documentation passage: header line, then a line `Section: <heading path>`, then the text.
- A code-search hit: header line, then the second line `### code search hit (pattern: '<grep pattern>')`, then `File: <repo path>:<line>`, then a fenced block of one line. Its source label is `<file>:<line> > <file>:<line>` and its relevance is always `0.55`.
- A passage is a code hit when its second line matches `^### code search hit \(pattern: '(.*)'\)$`. Else it is a documentation passage.

### 4.3 API

```python
class PassagesError(Exception): ...

@dataclass(frozen=True)
class Passage:
    index: int                        # 1-based, from the header
    kind: str                         # "doc" or "code"
    source: str                       # label between "] " and " (Relevance"
    relevance: float
    text: str                         # the passage with its header line, without the trailing separator
    code_pattern: str | None          # grep pattern, code hits only
    code_location: str | None         # "path:line" from the "File:" line, code hits only

SEPARATOR = "\n\n---\n\n"

def split_context(text: str) -> list[Passage]: ...
def doc_passages(passages: Sequence[Passage]) -> list[Passage]: ...
def code_passages(passages: Sequence[Passage]) -> list[Passage]: ...
def estimate_prompt_tokens(*texts: str, method: str = "bytes4") -> int: ...
```

`split_context` raises `PassagesError` when: the text is empty or equal to `FALLBACK_CONTEXT`; there is text before the first header; there is no header; the numbers are not 1..n; a segment except the last does not end with `SEPARATOR`; a code hit lacks its `File:` line. It never returns an empty list. For the 60 stored contexts the result must hold `n_chunks` passages, and at least one documentation passage (the 41-character rule of the project: an empty or fallback context stops the run).

`estimate_prompt_tokens(*texts, method)` returns an integer.
- `"bytes4"` (default): `ceil(total UTF-8 bytes / 4)`. It equals `est_tokens` of the registered driver (`run_study2.py:97-100`) and the limit constant `MAX_PROMPT_TOKENS_EST = 12000` of `run_study2b.py:129`. Add the constant `MAX_PROMPT_TOKENS_EST = 12000` to this module.
- `"bytes32"`: `ceil(total bytes / 3.2)`. This is the cautious version (code-like names use about 3 bytes per token).
- `"tiktoken"`: allowed only when the `cl100k_base` file is already in the tiktoken cache folder (`TIKTOKEN_CACHE_DIR`, or `data-gym-cache` in the temporary folder). Otherwise raise `PassagesError`. It must never download (that would be a network call). Unit tests do not use it.
- Another value raises `ValueError`.

```python
@dataclass(frozen=True)
class StoredContext:
    story_id: str
    text: str
    sha256: str                       # the stored context_sha256, checked against sha256(text)
    n_chunks_stored: int

@dataclass(frozen=True)
class StoredPrompt:
    study: str                        # "study2b" or "study2"
    project: str
    model: str                        # local name
    story_id: str
    arm: str
    run: int
    attempt: int
    system: str
    user_sent: str | None             # None for Study 2
    user_non_doc: str
    doc_context_ref: str | None
    doc_context_sha256: str | None
    system_sha256: str | None         # None for Study 2
    user_sha256: str | None           # None for Study 2
    local_prompt_tokens: int | None
    local_completion_tokens: int | None
    local_n_requirements: int
    source_file: str                  # workspace-relative path of records.jsonl
    source_line: int                  # 1-based line number

def study2b_cell_dir(project: str, model: str) -> Path: ...                 # STUDY2B / project / model_slug(model)
def read_study2b_prompts(*, projects: Sequence[str] = PROJECTS, models: Sequence[str] = MODELS,
                         arms: Sequence[str] = ("A2", "B2"), run: int = 1,
                         stories: Collection[str] | None = None) -> list[StoredPrompt]: ...
def read_study2b_context(project: str, model: str, story_id: str) -> StoredContext: ...
def read_study2_context(project: str, model: str, story_id: str) -> StoredContext: ...
def read_study2_prompts(*, projects=PROJECTS, models=MODELS, arms: Sequence[str] = ("A", "B", "C", "D"),
                        stories: Collection[str] | None = None) -> list[StoredPrompt]: ...
def verify_stored_prompt(sp: StoredPrompt, context: StoredContext | None) -> None: ...
```

- `read_study2b_prompts` reads each `records.jsonl` once. For each key (story, arm, run) it takes the last line (the rule of `analyze_study2.py:109-113`). If that line has `error` not `None`, or if a requested key is missing, raise `PassagesError` (no silent skip). The result order is sorted by (project, model, arm, story id). With the default arguments it returns 60 prompts. A `stories` filter with an unknown id raises. The files are opened read-only. Nothing in this module writes.
- `verify_stored_prompt` raises `PassagesError` unless: `sha256(user_sent) == user_sha256`; `sha256(system) == system_sha256`; for a record with a context (`doc_context_ref == "frozen"`): `context is not None`, `sha256(context.text) == doc_context_sha256`, `user_sent.count(context.text) == 1`, `user_sent.replace(context.text, "", 1) == user_non_doc`, and `split_context(context.text)` holds at least one documentation passage; for a record without a context: `doc_context_ref is None`, `user_non_doc == user_sent`. These are the same checks as `verify_new_record` in `analyze_study2b.py:263-296`.
- The three registered folders are read only. The module never writes under them and never opens `review/`, `review_v2/key_do_not_open/` or `study2_threat/`.

## 5. parse.py

Purpose: read the model answer, validate it as the old generator does, never hide a failure.

### 5.1 API

```python
class UnreadableAnswer(ValueError):
    errors: tuple[str, ...]            # one short message per extraction step

class NoRequirementsList(ValueError):  # the JSON object is fine but "requirements" is missing or not a list

@dataclass(frozen=True)
class ExtractResult:
    data: dict
    method: str                        # "whole", "object" or "fence"
    parsed_by_fallback: bool           # True when method != "whole"

def extract_json(text: str) -> ExtractResult: ...
def old_order_extract(text: str) -> dict: ...
def compare_with_old(text: str, new_data: dict) -> str: ...        # "same", "different" or "failed"

@dataclass(frozen=True)
class ValidationFailure:
    index: int
    error: str                          # "<ExceptionClass>: message", cut at 300 characters

@dataclass(frozen=True)
class ParsedAnswer:
    requirements: tuple[SecurityRequirement, ...]
    threat_mappings: tuple[ThreatAgentMapping, ...]
    n_requirements_raw: int
    n_requirements_failed_validation: int
    validation_failures: tuple[ValidationFailure, ...]
    n_threat_mappings_raw: int
    n_threat_mappings_failed: int
    asvs_defaulted: int
    method: str
    parsed_by_fallback: bool
    old_parser_result: str

def parse_answer(text: str, story_id: str) -> ParsedAnswer: ...
def requirement_dicts(requirements: Iterable[SecurityRequirement]) -> list[dict]: ...    # r.model_dump(mode="json"), as run_study2.py:198-199
```

### 5.2 Extraction order (differs from the old order on purpose)

`extract_json(text)`:
1. `"whole"`: `json.loads(text.strip())`. Accept only when the result is a `dict`.
2. `"object"`: from the first `{` to the last `}` (`str.find` and `str.rfind`). `json.loads` of that slice. Accept only a `dict`.
3. `"fence"`: each block of the pattern ```` ```(?:json)?\s*([\s\S]*?)\s*``` ```` (case-insensitive, as the old pattern), in order. The first block that parses to a `dict` wins.
4. If all three fail, raise `UnreadableAnswer` with the three messages.

The old generator (`generator.py:279-300`) tries the code fence first, then the greedy object, then the whole text. Its fence-first order is a fault: a raw JSON answer whose strings hold a fenced command (the Keycloak passages are full of commands) gives the inner snippet, `json.loads` fails, and the same prompt is sent again up to 3 times. In the 1,749 stored requirements no requirement holds a fence, so on the anchor the two orders should agree. The record shows it: `old_parser_result` is `"same"` when `old_order_extract` returns an equal dict, `"different"` when it returns another dict, `"failed"` when it raises. `old_order_extract` is a copy of the old order for this diagnosis only.

### 5.3 Validation (the same as the old generator)

`parse_answer` calls `extract_json`, then reads `data["requirements"]`:
- Missing key or not a list: raise `NoRequirementsList`. (The old code returns zero requirements and no error. This is a deliberate change. The runner treats it as an unreadable answer and retries with the old rule. The record says `error_kind = "no_requirements_list"`.)
- For each item: call the old code. Build one private object `_Validator(SecurityRequirementsGenerator)` with `llm_client=None` and `prompt_builder=object()` (a stand-in that is never called), and call its `_parse_requirement(raw, UserStory(id=story_id, title="", story=""), index)`. This is the same code, so the same fields, the same enum normalisation and the same defaults. An item that is not a `dict`, or that raises any exception (`ValidationError`, `KeyError`, `ValueError`, `TypeError`, `AttributeError`), is counted in `n_requirements_failed_validation` and listed in `validation_failures`. (The old code logs a warning and drops it, and it crashes on a non-dict item. The new code never drops without a count.)
- `threat_mappings` (optional key): the same with `_parse_threat_mapping`. A missing key is zero. A key that is not a list counts as one failure.
- `asvs_defaulted`: the old `_normalize_asvs_category` falls back to V1 and logs a warning when it does not know the text. `_Validator` overrides `_normalize_asvs_category`: call `super()`, and when the result is `V1_ARCHITECTURE` and the lower-case input does not match `v?1\b` or contain `architecture`, `design` or `threat`, add 1 to `self.defaulted`. A `_Validator` is created for each call of `parse_answer`, so there is no shared state.
- A test proves that for fixtures `requirement_dicts(parse_answer(...).requirements)` equals the requirements of `SecurityRequirementsGenerator._parse_response` called with the same text (the old method, used as the oracle). The test may build the old generator with `llm_client=None`.

The runner never calls the old `_generate_with_retry`. The retry loop is in `run_tuning.py` (section 7.3).

## 6. records.py and measures.py

### 6.1 records.py: record and manifest

```python
SCHEMA_VERSION = 1
REQUIRED_RECORD_FIELDS: dict[str, tuple[type, ...]]    # field name to allowed types (NoneType included where None is allowed); the table in section 8.1

def new_record(**fields) -> dict: ...                  # validates, runs assert_no_private_text on the JSON text, returns the dict
def validate_record(rec: Mapping) -> None: ...         # raises RecordError
def read_jsonl(path: Path) -> list[dict]: ...          # [] when the file does not exist; a bad line raises RecordError with the line number
def append_record(path: Path, rec: Mapping, *, guard: WriteGuard = DEFAULT_GUARD) -> None: ...   # validate, then guarded_append_line(json.dumps(rec, ensure_ascii=False, sort_keys=False))
def record_key(rec: Mapping) -> tuple[str | None, str, str, str, int]: ...   # (config_id, model, story_id, arm, run)

@dataclass(frozen=True)
class KeyState:
    ok: bool
    errors: int
    last_attempt: int

def key_states(records: Iterable[Mapping]) -> dict[tuple, KeyState]: ...
def is_final(state: KeyState | None, max_attempts: int) -> bool: ...    # state.ok or state.errors >= max_attempts
def next_attempt(state: KeyState | None) -> int: ...                    # 1 for None, else last_attempt + 1

def validate_manifest(m: Mapping) -> None: ...                           # section 8.2
class RecordError(Exception): ...
```

`key_states` counts a line with `error` not `None` as an error and any other line as ok. This is the rule of `run_study2b.py:549-557`, but the key includes `config_id` and `model`, and the limit is a parameter (`max_attempts`, default 3 in the runner), because the hosted rule has 3 attempts and the registered rule has 2.

### 6.2 measures.py: name use

Name use is the registered measure. Definition, as `analyze()` of `scripts/analyze_study2.py:101-153` computes it and `analyze_study2b.py` reports it as `rate`:

1. The identifiers of a story are `stories.json` of the project, `stories[].fact_sheet.identifiers` (Study 2 folder, read only).
2. For a record: `prompt_given = found(prompt_system + "\n" + prompt_user_non_doc, ids)`. For each requirement: `cited = found(req_text(req), ids)`; `attributable = cited - prompt_given`; the requirement counts 1 when `attributable` is not empty. The record value is `sum(flags) / len(flags)`, or `0.0` when the record has no requirements.
3. Records with an error are left out. Per `(model, arm, story)` the value is `statistics.fmean` of the record values over the runs that have one. A story with no good run has no value.
4. The arm rate is `sum(values) / len(values)` over the story values (this is `mean` of `thesis/numbers/stats.py:91-94`).

```python
class MeasureError(Exception): ...

def load_fact_sheets(projects: Sequence[str] = PROJECTS, study2_root: Path = STUDY2) -> dict[str, tuple[str, ...]]: ...   # story_id to identifiers; duplicate story id, missing key or empty list raises MeasureError

@dataclass(frozen=True)
class RecordScore:
    n_requirements: int
    n_with_name: int                   # requirements with an attributable name
    name_use: float                    # n_with_name / n_requirements, 0.0 when no requirement
    prompt_given: tuple[str, ...]
    cited: tuple[str, ...]             # union over requirements
    attributable: tuple[str, ...]      # union over requirements

def score_record(rec: Mapping, identifiers: Sequence[str]) -> RecordScore: ...
def latest_records(records: Iterable[Mapping]) -> dict[tuple, Mapping]: ...     # last line per record_key

@dataclass(frozen=True)
class StoryValue:
    config_id: str | None
    model: str
    arm: str
    story_id: str
    n_runs: int                        # runs with a value
    name_use: float                    # fmean over runs
    n_with_name_mean: float            # fmean of n_with_name: "requirements with an attributable name, for each story"
    n_requirements_mean: float

def story_values(records: Iterable[Mapping], fact_sheets: Mapping[str, Sequence[str]]) -> dict[tuple, StoryValue]: ...   # key (config_id, model, arm, story_id)

@dataclass(frozen=True)
class ArmRate:
    config_id: str | None
    model: str
    arm: str
    rate: float
    n_stories: int
    n_records: int                     # final records (last line per key), error ones included
    n_failed: int                      # final records with an error
    n_stories_without_value: int

def arm_rates(records: Iterable[Mapping], fact_sheets: Mapping[str, Sequence[str]]) -> dict[tuple, ArmRate]: ...   # key (config_id, model, arm)
def repeat_rates(records, fact_sheets) -> dict[tuple, float]: ...   # key (config_id, model, arm, run): the arm rate of one repeat alone

@dataclass(frozen=True)
class RunTotals:
    n_records: int                     # lines, attempts included
    n_attempt_lines_failed: int
    n_parse_retries: int               # keys with more than one attempt line
    n_requirements_failed_validation: int
    n_parsed_by_fallback: int
    n_old_parser_different: int
    n_old_parser_failed: int
    n_finish_length: int
    n_reasoning_assumed_zero: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float

def run_totals(records: Iterable[Mapping]) -> RunTotals: ...         # counts all lines, hosted records only

def load_local_reference(path: Path = ANALYSIS_S2B) -> dict[tuple[str, str], float]: ...   # (model, arm) to the "rate" in analysis_s2b.json; arms A2 and B2 for both models; a missing key raises
```

`score_record` raises `MeasureError` when `prompt_system`, `prompt_user_non_doc` or `requirements` is missing or has the wrong type. The registered code uses `.get(..., "")`, which hides a missing field. For valid records the result is equal.

Acceptance test (must pass): on the real Study 2b records of arms A2 and B2 (60 records), `arm_rates` gives, for each model and arm, the `rate` of `analysis_s2b.json` to 9 decimals. Today the two give the same value exactly (checked on 2026-11-16 with a scratch script that uses the registered `found` and `req_text`): qwen3.8:27b A2 0.07777777777777778, B2 0.4755555555555555; gemma4:26b A2 0.04000000000000001, B2 0.5533333333333333. Sum the story values in order of story id so that the result does not depend on file order. The test reads the stored values from the JSON file. It does not hard-code them (the four numbers above are only a second, hard-coded check to 9 decimals).

## 7. run_tuning.py

### 7.1 Command line

```
python scripts/run_tuning.py anchor --run-name NAME [--repeats 2] [--workers 6] [--max-cost-usd 1.00]
        [--max-minutes 60] [--max-attempts 3] [--stories ID,ID] [--projects a,b] [--models m1,m2]
        [--arms A2,B2] [--dry-run] [--resume] [--skip-hash-lists]
python scripts/run_tuning.py report --run-name NAME
python scripts/run_tuning.py run --run-name NAME --config NAME     # stub
```

Run it from `coded_project/secreqgen` as `PYTHONPATH=src TOKENIZERS_PARALLELISM=false .venv/bin/python scripts/run_tuning.py ...`. The script adds its own folder to `sys.path` only through Python's normal rule (the script folder is `sys.path[0]`), and sets nothing else at import.

```python
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Exit codes: 0 done; 2 usage error or a guard or check refused (nothing was sent); 3 the run stopped early and can be resumed (cost cap, time cap, a fatal client error, 4 failed generations in a row).

Options:
- `--run-name`: must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`; and must not equal, ignoring case, `study2` or `study2b`. The run folder is `real_test_cases/tuning/<run-name>/`.
- `--repeats` default 2 (at least 1). `--workers` default 6 (1 to 16). `--max-cost-usd` default 1.00 (above 0). `--max-minutes` default 60. `--max-attempts` default 3 (1 to 3).
- `--stories`: a filter; each id must exist, else exit 2. `--projects`, `--models`, `--arms`: filters (anchor accepts only `A2`, `B2`).
- `--dry-run`: builds every request, runs every guard and check, prints the counts, then returns 0. It makes no network call, reads no key, creates no folder, no lock and no file. It does hash the three lists (about 700 MB of reads) unless `--skip-hash-lists` (allowed only together with `--dry-run`).
- `--resume`: continue a run folder that exists. Without it, an existing run folder is refused (exit 2). With it, the manifest of the folder must have the same configuration (models, pins, repeats, arms, sampling): else exit 2.
- Pins not confirmed: while `ModelPin.confirmed` is false for a selected model, a real run with more than 4 generations in all is refused (exit 2). The message tells the main session to run a smoke test first: `anchor --run-name smoke1 --stories BW-S2-001 --repeats 1` (4 generations: 2 models x 2 arms), read the provider names in the records, then edit the constants (`confirmed=True`).

### 7.2 Jobs and configurations

The runner takes a list of configurations. A configuration turns a selection into prompt jobs.

```python
@dataclass(frozen=True)
class Selection:
    projects: tuple[str, ...]
    models: tuple[str, ...]
    arms: tuple[str, ...]
    stories: frozenset[str] | None
    repeats: int

@dataclass(frozen=True)
class Job:
    config_id: str                     # "old" for the anchor
    step: str                          # "anchor"
    project: str
    model: str                         # local name
    arm: str
    story_id: str
    repeat: int                        # becomes record["run"]
    system: str
    user_sent: str
    user_non_doc: str
    doc_context_ref: str | None
    doc_context_sha256: str | None
    stored: StoredPrompt               # the source of the prompts
    @property
    def key(self) -> tuple[str, str, str, str, int]: ...    # (config_id, model, story_id, arm, repeat)

class Configuration(Protocol):
    config_id: str
    step: str
    def jobs(self, sel: Selection) -> list[Job]: ...

class OldConfigurationAnchor:           # the anchor: stored prompts of Study 2b, run 1
    config_id = "old"
    step = "anchor"
    def jobs(self, sel: Selection) -> list[Job]: ...
```

`OldConfigurationAnchor.jobs` calls `read_study2b_prompts(arms=sel.arms, run=1, ...)`, verifies each prompt (`verify_stored_prompt`), and returns one job per prompt per repeat. Order of the list (so that a stop leaves complete repeats): repeat, then model (in `MODELS` order), then arm, then project, then story id. Default selection: 15 stories x 2 models x 2 arms x 2 repeats = 120 jobs.

`cmd_run` (the subcommand `run`) raises `NotImplementedError("run: new configurations are not built yet. Step 0 has the subcommand 'anchor' only.")`.

### 7.3 One generation

```python
@dataclass
class JobOutcome:
    records: list[dict]                # one record for each attempt that ran
    cost_usd: float                    # sum over the attempts (also of a refused paid reply)
    ok: bool
    fatal: HostedError | None
    transport_failed: bool

def run_job(job: Job, client: OpenRouterClient, pin: ModelPin, *, run_name: str, start_attempt: int = 1,
            max_attempts: int = 3, retry_delay_s: float = 1.0,
            sleep: Callable[[float], None] = time.sleep,
            now: Callable[[], str] = utc_now) -> JobOutcome: ...
```

Loop (mirrors `_generate_with_retry` of the old generator: 3 attempts, the same prompt again, waits of 1 s and 2 s):
1. `attempt = start_attempt`. Build the request body (`build_request_body`) and its `request_sha256`.
2. `reply = client.generate_reply(job.user_sent, job.system)`.
3. `parse_answer(reply.text, job.story_id)`. If it works: write a record with `error = None`, and stop (`ok`).
4. `UnreadableAnswer` or `NoRequirementsList`: write a record with the raw reply, `error = "<Class>: <message>"`, `error_kind = "unreadable_json"` or `"no_requirements_list"`. If `attempt < max_attempts`: wait `retry_delay_s * 2 ** (attempt - start_attempt)` and go on with `attempt + 1`. Else stop (`ok = False`).
5. `TransientHostedError` after the client retries: write a record (`error_kind = "transport"`, no reply fields), `transport_failed = True`, stop. No further attempt in this job (the cause is not the answer). The key is not final, so `--resume` tries it again with the next attempt number.
6. `FatalHostedError` and `ContractViolation`: write a record (`error_kind = "fatal"` or `"contract"`; when `exc.reply` exists, fill the reply fields and the raw text so that the cost and the evidence are kept), set `fatal`, stop. The cost of `exc.reply` is added.
7. Every record goes through `records.new_record`. `run_job` does not write files: the main thread writes the returned records (one writer, no write lock).

### 7.4 Executing a run

```python
class CostMeter:                       # thread-safe
    def add(self, usd: float) -> float: ...
    @property
    def total(self) -> float: ...

def execute(jobs: Sequence[Job], run_dir: Path, *, workers: int, max_cost_usd: float, max_minutes: float,
            max_attempts: int, client_factory: Callable[[ModelPin], OpenRouterClient],
            guard: WriteGuard = DEFAULT_GUARD, sleep=time.sleep, clock=time.monotonic) -> int: ...
```

- Resume: read the records of each cell (`run_dir/<project>/<model slug>/records.jsonl`), build `key_states`, skip a job whose key is final, and start the others at `next_attempt(state)`.
- Pool: `ThreadPoolExecutor(max_workers=workers)`. At most `workers` jobs are in flight. The main thread submits the next job only when (a) a worker is free, (b) `meter.total < max_cost_usd`, (c) the time since the start is below `max_minutes`, (d) no fatal error was seen, (e) fewer than 4 failed jobs in a row. In-flight jobs finish and their records are written. So the cost can pass the cap by at most `workers` generations (about 0.01 USD each here). The summary says so.
- Clients: each worker thread has its own client for each model (a `threading.local()` dict `model -> client`), built with `client_factory(pin)`. In a real run the factory is `lambda pin: OpenRouterClient(pin, key)` with the one `ApiKey` read in the main thread. In tests the factory gives a client with a `httpx.MockTransport`.
- The main thread writes the records with `append_record` as outcomes arrive, to `run_dir/<project>/<model slug>/records.jsonl` (created with `guarded_mkdir`).
- Stop reasons and exit code: all jobs final: 0. Cost cap, time cap, fatal error, 4 failed jobs in a row: print one plain line with the reason, write the end of the manifest, return 3.
- A job whose last record is an error after `max_attempts` counts as failed for the "4 in a row" rule. A job that is ok resets the counter. A transport failure counts as failed.

### 7.5 Preflight (also run by `--dry-run`)

```python
@dataclass
class Check:
    name: str
    status: str            # "PASS", "FAIL", "WARN", "SKIP"
    detail: str = ""

def preflight(sel: Selection, jobs: Sequence[Job], run_dir: Path, *, dry_run: bool, resume: bool,
              max_cost_usd: float, skip_hash_lists: bool, guard: WriteGuard = DEFAULT_GUARD) -> list[Check]: ...
def print_checks(checks: Sequence[Check]) -> None: ...
```

Each check, and what makes it FAIL:
1. Run folder: `guard.check(run_dir)` passes; the name rule; the folder does not exist (or `--resume` and it exists).
2. Hash lists (before): `check_all_hash_lists()`; any list not ok.
3. No other experiment process (`assert_no_other_experiment`). Lock file absent (dry-run only looks, it does not create).
4. Stored prompts: 60 (or the filtered number) found, each verified. Each context splits (`split_context`) with at least one documentation passage; no context is the 41-character fallback.
5. Request bodies: for every job `build_request_body` succeeds (the privacy guard runs on each body). The body has `reasoning.effort == "none"`, one provider tag, and `allow_fallbacks` false.
6. Prompt size: `estimate_prompt_tokens(system, user, method="bytes4") <= 12000`, and the stored `local_prompt_tokens` + 4096 is at most 16384 (the local limits). The `bytes32` value is printed as a WARN when it is above 12000.
7. Pins: each selected model has a `ModelPin`; a WARN line for each unconfirmed pin; the rule about more than 4 generations (section 7.1).
8. Cost estimate: from the stored local token counts, times repeats, times `price_in_per_mtok` and `price_out_per_mtok`. FAIL when the estimate is above `max_cost_usd`. Print the estimate (it is an estimate only).
9. Reference: `load_local_reference()` holds the four values (A2 and B2, two models).

`--dry-run` prints: job counts for each model and arm, repeats, the estimated tokens (input, output), the estimated cost, the longest prompt (bytes4), the result of each check, and the line "key: not read, no network call made". It returns 0 when no check failed, else 2.

### 7.6 Manifest and report

`run_dir/manifest.json` is written at the start (`"state": "running"`) and rewritten at the end (`"state"`: `"complete"` or `"stopped"`). Schema in section 8.2. All paths in it are relative to the workspace root. The manifest text goes through `assert_no_private_text`.

Git information: `git -C <secreqgen> rev-parse HEAD` and `git --no-optional-locks -C <secreqgen> status --porcelain --untracked-files=no` (read only; the second form does not refresh the index). The result: `{"commit": str | None, "dirty": bool | None}`. If git fails, the fields are `null` and the manifest says so in `git.error`. The commit of the code repository is old (the audit fixes are uncommitted), so `dirty` will be true. The manifest therefore also stores the SHA-256 of each file of `scripts/tuning_lib/`, of `run_tuning.py`, and of `src/secreqgen/generation/*.py`.

```python
def build_report(run_dir: Path, *, reference: Mapping[tuple[str, str], float] | None = None) -> dict: ...
def render_report_md(report: Mapping) -> str: ...
def cmd_report(run_name: str, *, guard: WriteGuard = DEFAULT_GUARD) -> int: ...
LIMIT_POINTS = {"B2": 8.0, "A2": 5.0}
```

`build_report` reads all `records.jsonl` below the run folder, scores them with `measures`, and compares with `load_local_reference()`. For each model and arm (4 rows): `local_rate`, `hosted_rate` (the arm rate over the stories; a story value is the mean of its repeats), `difference_points = 100 * (hosted - local)`, `limit_points`, `inside_limit` (`abs(difference_points) <= limit_points + 1e-9`; `null` when the arm is not complete), the rate of each repeat (`repeat_rates`), `repeat_gap_points` (largest minus smallest repeat rate, in points; the hosted run-to-run noise), `n_stories`, `n_failed`, `complete`. A cell is complete when every planned key is final and ok. An incomplete cell gets no verdict. Also per model: mean `prompt_tokens` hosted against local (ratio; a check of the tokenizer and template; no limit), mean `completion_tokens`, mean `n_requirements`, and `RunTotals`. The limits are the Study 2b drift limits (`DRIFT_LIMIT` in `analyze_study2b.py`: B2 0.08, A2 0.05). The local rate is one local run and the hosted rate is the mean of 2 repeats; the table says so in a note. `cmd_report` writes `report.json` and `report.md` into the run folder through the guard and prints the table. `anchor` calls `cmd_report` at the end when every cell is complete.

`report.md` has: a header (run name, date, models, provider pins, state), the table (columns: model, arm, local rate, hosted rate, difference in points, limit, inside or not, repeat 1, repeat 2, repeat gap in points), a "counts" table (records, failed, parse retries, parsed by fallback, old parser different, finish_reason length, reasoning tokens, assumed-zero reasoning, tokens, cost), and one line that says whether all four rows are inside their limit. No sentence claims more than the numbers show.

## 8. Schemas

### 8.1 Record (one JSON line per attempt)

File: `real_test_cases/tuning/<run>/<project>/<model slug>/records.jsonl`. All fields are always present. `None` means JSON `null`.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | 1 |
| `study` | str | `"tuning"` (Study 2b records have `"study2b"`) |
| `step` | str | `"anchor"` |
| `run_name` | str | the run folder name |
| `config_id` | str | `"old"` for the anchor |
| `project` | str | `mastodon`, `keycloak` or `bitwarden` |
| `model` | str | local name, for example `gemma4:26b`. It is the key that `measures` and the registered analysis use |
| `hosted_model` | str | OpenRouter model id |
| `story_id` | str | for example `BW-S2-001` |
| `arm` | str | `A2` or `B2` |
| `run` | int | repeat number, 1 to N (Study 2b field name kept) |
| `attempt` | int | 1 to `max_attempts` |
| `max_attempts` | int | 3 |
| `ts` | str | UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `source_study` | str | `"study2b"` |
| `source_run` | int | 1 |
| `source_file` | str | workspace-relative path of the Study 2b `records.jsonl` |
| `source_line` | int | line number in that file |
| `arm_description` | str | text of the arm, copied from the stored record when it exists, else `""` is not allowed: copy `"stored prompt of Study 2b arm <arm>, run 1"` |
| `prompt_system` | str | exact system prompt sent |
| `prompt_user_sent` | str | exact user prompt sent |
| `prompt_user_non_doc` | str | the user prompt with the context removed (from the stored record, verified) |
| `doc_context_ref` | str or None | `"frozen"` (B2) or `None` (A2) |
| `doc_context_sha256` | str or None | |
| `prompt_system_sha256` | str | |
| `prompt_user_sha256` | str | |
| `prompt_est_tokens` | int | `bytes4` estimate of system plus user |
| `prompt_check` | str | `"stored prompt, hashes and context verified"` |
| `n_calls_sent` | int | 1 (field kept for the Study 2b form) |
| `request_sha256` | str | SHA-256 of the exact JSON request body |
| `provider_tag` | str | the pin, for example `parasail/bf16` |
| `provider` | str or None | provider read from the answer; `None` when no reply arrived |
| `provider_source` | str or None | `"metadata"` or `"generation_endpoint"` |
| `generation_id` | str or None | `gen-...` |
| `response_model` | str or None | |
| `finish_reason` | str or None | |
| `transport_retries` | int | retries done inside the client |
| `latency_ms` | float or None | |
| `prompt_tokens` | int or None | hosted count |
| `completion_tokens` | int or None | hosted count |
| `reasoning_tokens` | int or None | must be 0 |
| `reasoning_tokens_source` | str or None | `"usage"` or `"absent_assumed_zero"` |
| `cached_tokens` | int or None | |
| `cost_usd` | float or None | from `usage.cost` |
| `llm_calls` | list | `[{"prompt_tokens", "completion_tokens", "latency_ms"}]` for one call, `[]` when no reply (Study 2b form) |
| `raw_reply` | str or None | the full answer text, always kept when a reply arrived |
| `parse_method` | str or None | `"whole"`, `"object"`, `"fence"` |
| `parsed_by_fallback` | bool or None | true when `parse_method != "whole"` |
| `old_parser_result` | str or None | `"same"`, `"different"`, `"failed"` |
| `requirements` | list | `model_dump(mode="json")` of each accepted requirement; `[]` on failure |
| `n_requirements` | int | accepted |
| `n_requirements_raw` | int | in the answer |
| `n_requirements_failed_validation` | int | raw minus accepted |
| `validation_failures` | list | `[{"index": int, "error": str}]` |
| `n_threat_mappings` | int | |
| `n_threat_mappings_failed` | int | |
| `asvs_defaulted` | int | requirements whose ASVS text fell back to V1 |
| `error` | str or None | `"<ExceptionClass>: <message>"`, or `None` for a parsed answer |
| `error_kind` | str or None | `unreadable_json`, `no_requirements_list`, `transport`, `fatal`, `contract` |

An attempt that failed to parse still holds `raw_reply`, `prompt_tokens` and `cost_usd` (it was paid). A transport failure has `None` for the reply fields. The registered analysis function reads `model`, `story_id`, `arm`, `run`, `error`, `prompt_system`, `prompt_user_non_doc`, `requirements`, `prompt_tokens`, `completion_tokens`, `latency_ms`. All of them are present with the same meaning.

### 8.2 Manifest

File: `real_test_cases/tuning/<run>/manifest.json`.

```json
{
  "schema_version": 1,
  "study": "tuning",
  "step": "anchor",
  "run_name": "...",
  "state": "running | complete | stopped",
  "stop_reason": null,
  "started_utc": "...",
  "ended_utc": null,
  "command_line": ["anchor", "--run-name", "..."],
  "options": {"repeats": 2, "workers": 6, "max_cost_usd": 1.0, "max_minutes": 60.0,
               "max_attempts": 3, "stories": null, "projects": [], "models": [], "arms": []},
  "configuration": {"config_id": "old", "source": "study2b stored prompts, run 1",
                    "retry_rule": "same prompt again, 3 attempts at most, waits 1 s and 2 s",
                    "parser": "whole text, outer object, code fence",
                    "request_template": {"temperature": 0.1, "max_completion_tokens": 4096,
                                         "reasoning": {"effort": "none"},
                                         "provider_fields": ["order", "only", "allow_fallbacks",
                                                             "require_parameters", "data_collection"],
                                         "response_format": null, "seed": null}},
  "models": {
    "gemma4:26b": {"model_id": "...", "provider_tag": "...", "expected_provider_name": "...",
                   "top_p": 0.95, "top_k": 64, "extra_sampling": {}, "pin_confirmed": false,
                   "providers_seen": ["Parasail"], "response_models_seen": ["..."]},
    "qwen3.8:27b": {}
  },
  "jobs": {"planned": 120, "final_ok": 0, "final_failed": 0, "not_final": 0,
           "records_written": 0, "per_model_arm": {"gemma4:26b/A2": 30}},
  "cost": {"total_usd": 0.0, "cap_usd": 1.0, "estimate_usd": 0.0, "max_overshoot_note": "up to one generation per worker"},
  "tokens": {"prompt": 0, "completion": 0, "reasoning": 0},
  "versions": {"python": "...", "platform": "...", "httpx": "...", "pydantic": "..."},
  "code": {"git": {"commit": null, "dirty": null, "error": null},
           "file_sha256": {"scripts/run_tuning.py": "...", "scripts/tuning_lib/guard.py": "...",
                           "src/secreqgen/generation/generator.py": "..."}},
  "inputs": {"study2b_records_sha256": {"study2b/bitwarden/gemma4_26b/records.jsonl": "..."},
             "analysis_s2b_sha256": "..."},
  "hash_lists": {"before": {"study2": {"ok": true, "n_listed": 38, "n_hashed": 0, "n_existence_only": 0,
                                       "changed": [], "missing": [], "checked_utc": "..."}},
                 "after": {}},
  "client": {"headers_sent": ["Authorization", "Content-Type", "X-OpenRouter-Metadata", "X-OpenRouter-Cache"],
             "timeout_s": 300.0, "max_transport_retries": 3},
  "notes": []
}
```

The `hash_lists.after` block is filled at the end of the run, also when the run stops early. If a list is not ok after the run, the manifest says `"state": "stopped"`, `"stop_reason": "registered files changed"`, and the runner returns 3 and prints the paths. The key is never in the manifest. `endpoint_snapshot` (the endpoint list of the model with tag, quantisation and price) is not collected in step 0 (it needs a network call that the design avoids); the quantisation is the one in the pin tag.

### 8.3 Report

`report.json` fields: `schema_version`, `run_name`, `generated_utc`, `state`, `rows` (4 rows, fields in 7.6), `all_inside_limits` (bool or null), `totals` (`RunTotals` as a dict), `per_model` (token ratios and means), `notes`. `report.md` is the same data as text.

## 9. Unit tests

No test uses the network, the real key, the Keychain, `keyring`, Ollama, an embedder or ChromaDB. Tests that write use `tmp_path` and a `WriteGuard` built with `allowed_roots=[tmp_path]`. Tests that read registered data read only: `study2/<project>/stories.json`, `study2b/**/records.jsonl`, `study2b/**/retrieval_frozen.json`, `study2b/analysis_s2b.json`. Each test file starts by putting `scripts/` and `src/` on `sys.path`. Tests mark the registered-data reads with `pytest.mark.skipif(not STUDY2B.exists(), ...)`? No: a missing registered folder must fail the test, not skip it (no silent skip).

### 9.1 tests/unit/test_tuning_guard.py

- `assert_writable` accepts a file below `tuning/` and below `final_test/` (paths built from `paths`; no file is created).
- Refuses: `study2/x`, `study2b/x`, `secreqgen/data/x`, the repository root, the workspace root, `/tmp/x`, a relative path, `tuning/../study2/x`.
- Refuses by name: `tuning/study2/a`, `tuning/STUDY2B/a`, a path with a part `Study2`.
- Case: `real_test_cases/STUDY2/x` is refused (macOS ignores case).
- Symbolic link: in a `tmp_path` guard, a link inside the allowed root that points to a folder outside is refused; a link that points to a forbidden root is refused.
- `guarded_write_text`, `guarded_append_line`, `guarded_mkdir` write inside the allowed root and raise `GuardError` outside, and write nothing then (check the file does not exist). `guarded_write_text` leaves no `.tmp-` file after success.
- Source scan: no write call in `run_tuning.py` or any `tuning_lib` module except `guard.py` (section 2.2).
- `RunLock`: first `acquire` works; second `acquire` raises; the message names the pid and says alive or gone (inject `pid_alive`); `release` removes the file; `release` with another token raises; the context manager releases after an exception; a stale lock is never deleted by `acquire`.
- Process check with fake `ProcessRow` lists: matches `python scripts/run_tuning.py anchor`, `/x/python3.13 scripts/run_study2b.py --dry-run`, `caffeinate -i python run_final_test.py`, `./run_cross_family_winning_config.py`; does not match `grep run_tuning.py`, `vim run_study2b.py`, `python -m pytest tests/unit/test_tuning_guard.py`, `python -c "..."`, `python scripts/analyze_study2b.py`; ignores the own pid and its ancestor chain; the message lists at most 3 rows.
- Hash lists in `tmp_path`: a good list gives `ok`; a changed file goes to `changed`; a missing file goes to `missing`; a malformed line, an empty list, an absolute path and a `..` path raise `GuardError`; a path under a sealed rule (a `key_do_not_open` part; a folder named `review` under a fake `study2` root passed through the `existence_only` argument) is not opened: monkeypatch `Path.read_bytes`, `open` and `hashlib.sha256` for that path and assert no call, only `is_file`.
- The three real lists: each parses (every line matches the format, 38, 18 and 764 lines, all paths relative and start with `coded_project/`). A test must not hash the 700 MB of vectordb files: it only parses the lists and, for the study2b list (small), runs `check_hash_list` and expects `ok` (those 18 files are registered and must be unchanged).
- Privacy: text with `"/Users/"` is refused; text with the home folder path is refused; text with the account name in any letter case is refused (built at run time from `getpass.getuser()`); clean text passes; the error message does not contain the matched text (assert the account name is not in `str(exc)`); a marker shorter than 3 characters raises (monkeypatch `getpass.getuser`).
- Import test: `import tuning_lib.guard` in a subprocess with the network blocked is not needed. Instead assert that importing does not call `subprocess.run`, `socket.socket`, `open` (monkeypatch before `importlib.reload`).

### 9.2 tests/unit/test_tuning_openrouter_client.py

All with `httpx.MockTransport`. A helper builds a fake 200 body (`id`, `model`, `choices`, `usage`, `openrouter_metadata`).
- Request body: the exact key set; `model`, `messages` order and roles; `reasoning == {"effort": "none"}`; `provider` has the five fields with the pin tag in `order` and `only`; `temperature` 0.1, `top_p`, `top_k`, `max_completion_tokens` 4096; the Qwen extras are present and the Gemma extras are not; no system message when `system_prompt` is `None`; none of the forbidden keys (`response_format`, `user`, `session_id`, `metadata`, `trace`, `models`, `seed`, `transforms`) is in the body.
- Headers: exact set of four names; no `HTTP-Referer`, no `X-Title`; the key appears only in `Authorization`.
- Key: `repr(ApiKey("sk-x"))` and `str()` hide the value; an exception raised for a 401 contains no key text; `HostedReply.to_dict()` contains no key; `load_api_key` prefers the environment variable; with an empty environment it calls the injected `run` with exactly `["/usr/bin/security", "find-generic-password", "-s", "openrouter-api-key", "-a", <user>, "-w"]`; a return code of 44 raises `FatalHostedError`; an empty stdout raises; the error text has no stdout content.
- Success: a normal body gives the `HostedReply` fields; `generate()` returns an `LLMResponse` whose `content`, `prompt_tokens`, `completion_tokens`, `total_tokens` match and whose `raw_response` equals `to_dict()`; `model_name == pin.model_id`; `last_reply` is set; the `format="json"` argument is not sent.
- Retry: 429 then 200 succeeds with `transport_retries == 1`; the injected `sleep` got `backoff_s[0]`; `Retry-After: 7` is used; 3 times 503 then 200 succeeds; 4 times 503 raises `TransientHostedError` with status 503; `httpx.ReadTimeout` is retried and then raises `TransientHostedError`; `ConnectError` raises `TransientHostedError` with no retry (assert one request).
- Fatal: 400 (message of the body is in the error, cut at 500 characters), 401, 402, 403, 404 each raise `FatalHostedError` with no retry (assert one request).
- Error in a 200 body: `{"error": {"code": 500, ...}}` is retried; `{"error": {"code": 400, ...}}` raises `FatalHostedError`; body not JSON raises; `choices` empty raises; `choices[0].error` raises.
- `content` null gives `text == ""` and no exception.
- Cost: `usage.cost` missing raises `FatalHostedError`.
- Reasoning: `reasoning_tokens: 12` raises `ContractViolation` and `exc.reply.cost_usd` is set; reasoning text in the message raises; the field missing and no reasoning text gives `reasoning_tokens_source == "absent_assumed_zero"`; the field `0` gives `"usage"`.
- Provider: from `openrouter_metadata.endpoints.available[]` (`selected`); without metadata the client calls the generation endpoint (assert the second request URL and the `id` parameter) and sets `provider_source == "generation_endpoint"`; neither source raises `FatalHostedError`; a provider other than the pin raises `ContractViolation`; `response_model` that does not start with the model id raises `ContractViolation`; two entries in `attempts` raises `ContractViolation`.
- Thread safety: 8 threads, each with its own client on the same `MockTransport` that echoes a request marker; each `last_reply` holds its own marker; no attribute of the module changes (compare a snapshot of `vars(module)` before and after).
- Privacy: a prompt with the account name or `/Users/` raises `GuardError` before any request (assert the transport was not called).
- Import test: importing the module does not read the environment variable and does not call `subprocess.run` (monkeypatch both and reload).

### 9.3 tests/unit/test_tuning_passages.py

- `split_context` on the real stored contexts: for all 60 stored contexts (both models, 3 projects, 5 stories): the number of passages equals `n_chunks`; the numbers are 1..n; at least one documentation passage; the join of `text` with `SEPARATOR` equals the stored context (round trip, byte for byte; the code-hit passages end with a newline that belongs to the text).
- Hand-built fixtures: a code hit is found by its second line; a documentation passage that contains a line `---` and a `#### sub heading` is not split; a documentation text that mentions `code search hit` in its body is not a code hit.
- Errors: empty text, the 41-character fallback, text before the first header, a gap in the numbers (1, 3), a missing separator, a code hit without `File:` each raise `PassagesError`.
- Counts: the sum over the 60 contexts is 252 documentation passages and 216 code hits (this fixes the text marks).
- `read_study2b_prompts()` returns 60 prompts (2 models x 3 projects x 5 stories x 2 arms); each `verify_stored_prompt` passes with its context (B2) or `None` (A2); sorted order; filter by `stories` works; an unknown story raises.
- `verify_stored_prompt` fails when: the user prompt is changed by one character; the context hash differs; the context is in the prompt twice (build a fixture); `user_non_doc` is wrong; an A2 prompt has a context ref.
- Error handling with fixture files in `tmp_path` (the module takes a root override through a module constant that the test monkeypatches): the last line of a key has an error; a requested key is missing; both raise `PassagesError`.
- `estimate_prompt_tokens`: `bytes4` equals `len(bytes) // 4` rounded up on a known text; `bytes32` is larger; an unknown method raises `ValueError`; `tiktoken` without cache raises `PassagesError` (monkeypatch the cache folder lookup, never download).
- The module opens no path under `review/`, `key_do_not_open` or `study2_threat` (monkeypatch `builtins.open` and `Path.open` to record paths and assert none matches).

### 9.4 tests/unit/test_tuning_parse.py

- `extract_json`: clean JSON gives `whole` and `parsed_by_fallback` false; JSON with text before and after gives `object` and true; JSON inside a fence gives `fence` and true; a fence with `json` tag and one without; first fence is invalid and the second is valid gives the second; a list at top level (`[...]`) raises `UnreadableAnswer`; empty string raises; plain text raises; the error holds three messages.
- The key regression: a raw JSON answer whose string holds a fenced command (```` ```bash\nkc.sh start\n``` ````) gives `whole` and the full dict. Also check that `old_order_extract` fails or gives another dict on the same text, and `compare_with_old` returns `"failed"` or `"different"`. For ordinary answers `compare_with_old` returns `"same"`.
- `old_order_extract` equals `SecurityRequirementsGenerator._extract_json` on a set of 10 texts, including the faulty one (use the old method as the oracle; build the old generator with `llm_client=None`).
- `parse_answer` equals the old `_parse_response` on fixtures: take `requirements` of 3 stored Study 2b records (read from the real file), serialise them as an answer text, parse again, and compare `requirement_dicts` with the stored ones (the stored dicts came from the old code, so this is a regression test).
- Failure counts: one of 5 requirements has `acceptance_criteria` as a list of objects: 4 accepted, `n_requirements_failed_validation == 1`, `validation_failures[0].index` correct, `n_requirements_raw == 5`. An item that is a string counts as a failure and does not crash. An unknown ASVS text increments `asvs_defaulted`; `"V2"` does not; `"V1: Architecture"` does not.
- `NoRequirementsList` when the key is missing, when it is `null`, when it is a dict.
- Threat mappings: valid ones are parsed; a bad one is counted; the key missing gives zero and no failure.
- Records of 2 threads parsing different texts at once do not mix counts (the `_Validator` has no shared state).
- Import test: the module imports with no side effect and does not import `secreqgen.retrieval` (assert not in `sys.modules` after import in a clean subprocess check or by comparing `sys.modules` keys).

### 9.5 tests/unit/test_tuning_records.py

- `new_record` accepts a full valid record; a missing field raises `RecordError` naming the field; a wrong type raises; an extra unknown field raises (the schema is closed); a value that is not JSON-safe raises; a record with the account name in `raw_reply` raises `GuardError`.
- `read_jsonl`: missing file gives `[]`; blank lines are skipped; a bad line raises with its number; a Study 2b record can be read (real file) even though it is not a valid tuning record (read does not validate; `validate_record` does).
- `append_record` writes one line through the guard, refuses a path outside the guard, and the line round-trips.
- `key_states` and `is_final` with `max_attempts` 3: ok on attempt 1 is final; 2 errors are not final; 3 errors are final; ok after 2 errors is final; `next_attempt`. Different `config_id` or `model` do not mix.
- `validate_manifest`: a full valid manifest passes; a missing top-level key raises; `hash_lists.before` missing raises; a key named like an API key field (`api_key`, `Authorization`) anywhere raises; a string with the account name raises.

### 9.6 tests/unit/test_tuning_measures.py

- Equality with the registered code: load `analyze_study2.py` by file path (the test does it the same way as `measures`), build a tiny fake study (2 stories, fact sheets, 3 records with prompts and requirements, one with an error, one with 0 requirements) in `tmp_path`, point the registered module at it (`registered.STUDY2 = tmp_path`, restored in a `finally`), run `registered.analyze(project)` and compare every per-story `frac_reqs_citing` with `story_values`. This is the same method as `score_with_registered_analyze` in `analyze_study2b.py`.
- `score_record`: a name that is in the prompt is not counted; a name only in the answer is counted; a name in `source_references` counts (`req_text` includes it); no requirements gives 0.0 and `n_requirements == 0`; missing `prompt_user_non_doc` raises `MeasureError`.
- `latest_records` keeps the last line; an error line after an ok line becomes the final state (and the story loses the value); `story_values` skips errors; a story with 2 runs gives the `fmean`; `n_with_name_mean` is right.
- `arm_rate`: the sum order does not change the result (shuffle the input); an empty input raises `MeasureError`.
- The acceptance test (section 6.2): `arm_rates` over the real Study 2b A2 and B2 records equals `analysis_s2b.json` `rate` to 9 decimals for the four (model, arm) pairs, and equals the four hard-coded numbers to 9 decimals. `n_stories == 15` for each.
- `load_local_reference` returns the four values; a missing arm raises.
- `run_totals` sums tokens and cost over lines; counts retries and flags correctly on fixture lines.
- The registered module is loaded with `sys.dont_write_bytecode = True` set, and no `__pycache__` appears in `scripts/` because of this module (assert the folder listing is equal before and after).
- Equality of copied constants: `LIMIT` values used by `run_tuning` equal `DRIFT_LIMIT` read from the text of `analyze_study2b.py` by a regex (`DRIFT_LIMIT = {"B2": 0.08, "A2": 0.05}`).

### 9.7 tests/unit/test_tuning_runner.py

All with a fake client (a stub class with `generate_reply`) or an `OpenRouterClient` on a `MockTransport`, a `WriteGuard` on `tmp_path`, an injected `sleep` that records waits, and no registered folder as output.
- `parse_args`: defaults (repeats 2, workers 6, max cost 1.00, max attempts 3, max minutes 60); bad run name (`../x`, `study2`, `STUDY2B`, empty, 65 characters) exits 2; `--skip-hash-lists` without `--dry-run` exits 2; `--arms` outside A2 and B2 exits 2.
- `build` of jobs: 120 jobs with defaults; the order is repeat, model, arm, project, story; `--stories` filters; an unknown story exits 2; `job.key` is unique.
- `--dry-run` (call `main` with a patched `OpenRouterClient` that fails the test if it is constructed, a patched `socket.socket` that fails if used, and `load_api_key` patched to fail if called): returns 0; creates no file under `tmp_path` and no folder; prints the job count, tokens, cost estimate, and the line about the key; each check is listed; the test also counts that `build_request_body` ran for every job. Hash lists are patched to a fixed ok result in this test (a separate test runs the real study2b list).
- Dry-run FAIL cases: a stored prompt with a wrong hash; a context that is the 41-character fallback; a body with the account name; an estimate above the cap; two experiment processes in the fake process list; unconfirmed pins with more than 4 jobs (exit 2).
- `run_job`: parse ok on attempt 1 gives one record with `error` `None`; unreadable twice then ok gives 3 records (attempt 1, 2 errors with `raw_reply` kept, attempt 3 ok) and the waits 1 s and 2 s; unreadable 3 times gives 3 error records, `ok` false; `finish_reason` `"length"` with a cut object is unreadable and retried; empty reply is unreadable; `NoRequirementsList` retried; transport error gives one record with `error_kind` `transport` and stops; `FatalHostedError` stops with `fatal` set; `ContractViolation` with reasoning tokens writes a record with the reply fields and adds the cost; every record passes `validate_record`; cost is summed over attempts.
- `execute`: a full run over 8 jobs with 3 workers on a fake client writes 8 final-ok records in the right cell files, exit 0; the cost cap stops submission (fake cost 0.30 each, cap 1.00: at most 4 jobs started plus the in-flight ones; exit 3; the in-flight records are written); the time cap (injected `clock`) stops with exit 3; a fatal error stops with exit 3; 4 failed jobs in a row stop; `--resume` skips final keys and continues failed keys at the next attempt number (build a partly filled folder); a resume with another configuration exits 2; two runs on one run folder: the second raises `GuardError` (lock).
- Thread safety: with 6 workers each job uses a client created for its thread; no client is shared across threads (count the clients and the thread ids); the records file has no broken lines (each line parses).
- Writes: the runner wrote nothing outside the `tmp_path` run folder; a guard on `tmp_path` refuses an `--out`-like path (the runner has no such option; the test calls `execute` with a `run_dir` outside the guard and expects `GuardError`).
- Hash lists: the "after" check runs also when the run stops; a changed registered file (fake list) makes the final state `stopped` and the exit code 3.
- Manifest: written at start with `state: running`, rewritten at the end; passes `validate_manifest`; contains no key text and no `/Users/`; `git` fields are null with an error text when the git call is patched to fail (no silent default); `hash_lists.before` and `after` are present.
- Report: from a fixture folder with known records, `build_report` gives the expected rates, the difference in points, `inside_limit` at the border (exactly 8.0 points is inside, 8.01 is outside), `repeat_gap_points`, and `complete` false for a missing story (no verdict, `inside_limit` null). `render_report_md` has the four rows. A real-data test: build a fake hosted run folder by copying the Study 2b A2 and B2 records of run 1 into `tmp_path` with `run` 1 and 2 and `model` unchanged; the report must show differences of 0.0 points and all rows inside.
- `cmd_run` raises `NotImplementedError` whose text starts with `run:`.
- Source scan: `run_tuning.py` does not contain the strings `run_study2b`, `import run_study2`, `chromadb`, `ingest`, `ollama` as an import or a call (comments may mention them), and does not import `secreqgen.retrieval` or `secreqgen.ingestion`.

## 10. What is imported and what is copied

| Item | Source | Decision | Reason and test |
|---|---|---|---|
| `req_text`, `found`, `REQ_FIELDS` | `scripts/analyze_study2.py` | Import, loaded by file path with `importlib.util.spec_from_file_location` and `sys.dont_write_bytecode = True` set first | The module has stdlib imports only and its `main()` runs under `__main__` only. It is in the registered hash list (38 files), so it cannot change unnoticed. This is the registered definition of "cited". Test: equality with `registered.analyze` on a fake study; the acceptance test on real records. The constant `STUDY2` of that module is never used for reading. |
| `analyze()` of `analyze_study2.py` | same | Not called by the runner. Called only inside the equality test | It reads a fixed folder. The test points it to `tmp_path` in memory, as `analyze_study2b.py` does. |
| per-story mean and arm mean | `analyze_study2.py` (`statistics.fmean`), `thesis/numbers/stats.py` (`mean`) | Copy (two lines) | `stats.py` is loaded by path only to get `mean`. Not worth the load. Test: equality to 9 decimals with `analysis_s2b.json`. |
| `SecurityRequirementsGenerator._parse_requirement`, `_parse_threat_mapping`, `_normalize_*` | `src/secreqgen/generation/generator.py` | Import and call through a subclass | Same validation, no copy to drift. `src/` is not changed. Test: equality with the old `_parse_response`. |
| `LLMResponse`, `UserStory`, `SecurityRequirement` | `src/secreqgen/generation/` | Import | Light modules. The client returns the old response type. |
| `_extract_json` order | `generator.py:279-300` | Copy as `old_order_extract` (diagnosis only) | The new order is different on purpose. Test: equal to the old method on 10 texts. |
| `check_hash_list` | `run_study2b.py:591-618` | Copy and extend (3 lists, sealed paths, strict format, result object) | `run_study2b.py` cannot be imported (section 11, item 1). The old one is bound to one list and one check of base names. |
| `is_inside` | `run_study2b.py:277-297` | Copy | Same reason. The case-insensitive and `samefile` logic matters on this Mac. |
| `list_processes` | `run_study2b.py:316-323` | Copy | Same reason. The match rule is new (section 2.4), because the old regex `OTHER_SCRIPTS` does not know the new drivers and matches `grep run_study2b`. |
| `append_record` (fsync) | `run_study2b.py:1033-1037` | Copy into `guarded_append_line` | Same reason. |
| `key_states`, `is_final` | `run_study2b.py:549-562` | Copy and extend (config id, `max_attempts` parameter) | Same reason. The old limit 2 is the registered rule. The hosted rule is 3. |
| `sha256_text` | `run_study2.py:93-94` | Copy (one line, stdlib) | `run_study2.py` imports the embedder and the vector store. |
| `est_tokens` and `MAX_PROMPT_TOKENS_EST` | `run_study2.py:97-100`, `run_study2b.py:129` | Copy | Same reason. Test: a regex check of the value 12000 in the source text of `run_study2b.py` (read the file as text, do not import it). |
| `model_slug` | `run_cross_family_winning_config.py:329-330` | Copy | That module imports the retrieval stack. Test: the folder names on disk. |
| `FALLBACK_CONTEXT` | `run_study2b.py:138` | Copy | Test: regex on the source text. |
| `DRIFT_LIMIT` (0.08, 0.05) | `analyze_study2b.py` | Copy as `LIMIT_POINTS` (8.0, 5.0) | `analyze_study2b.py` modifies `sys.path` and loads more at import. Test: regex on the source text. |
| `FrozenRetriever`, `RecordingOllamaClient`, `SentPromptClient`, `OllamaClient` | `run_study2.py`, `run_study2b.py`, `src` | Not used | They are bound to Ollama and share a call log between threads. |
| `SecurityRequirementsGenerator.generate`, `_generate_with_retry` | `generator.py` | Not used in step 0 | See section 11, items 3 to 5. |
| Prompt builders and prompt folders | `src/.../prompts.py`, `data/winning_prompts*` | Not used in step 0 | The anchor sends stored prompts. Later steps will use new prompt folders. |
| `study2_names.py` | `scripts/study2_names.py` | Not used in step 0 | It runs `git` and greps the repositories (3.5 minutes for the registered census). "Name use" needs only `found` and `req_text`. Later measures may use it. |

## 11. What in the existing code makes a requirement impossible or unsafe, and the nearest safe form

1. `run_study2b.py` and `run_study2.py` cannot be imported with no side effect. `run_study2b.py` imports `run_study2`, which imports `run_cross_family_winning_config` and then `secreqgen.ingestion` and `secreqgen.retrieval` (the embedder and the vector store packages). Also `run_study2b.py` sets `sys.dont_write_bytecode` and changes `sys.path` at import. The task says "import the registered functions if that has no side effect". Only `analyze_study2.py` qualifies. Nearest safe form: copy the few helpers (section 10) and guard each copy with a test that reads the registered source text.
2. `analyze_study2.analyze()` reads a global folder (`STUDY2`), so it cannot score records that live in `tuning/`. Nearest safe form: `measures.py` scores records through the registered `found` and `req_text`. The equality test points `analyze()` at a temporary folder, in memory, as `analyze_study2b.py` does. A temporary folder is outside the allowed write folders for new code, so only the test (pytest `tmp_path`) does it. The runner never does.
3. The old generator hides three failures. (a) `_extract_json` takes the code fence first, so JSON that contains a fenced command fails and is sent again. (b) `_parse_response` drops a requirement that fails validation with a log line only, and crashes on an item that is not an object. (c) `_normalize_asvs_category` falls back to V1 with a log line only. Also a missing `requirements` key gives zero requirements and no error. The source folder `src/` must not change. Nearest safe form: `parse.py` reuses the validation code and adds the counts and the new extraction order (section 5). The records show the counts, and the report prints them.
4. `_generate_with_retry` retries only `json.JSONDecodeError`, keeps only the last reply, and stores no raw text. A network error ends the story at once. Nearest safe form: the runner has its own loop (section 7.3) that records every attempt with the raw reply. The retry rule (same prompt, 3 attempts, 1 s and 2 s) is the old rule. The client adds the transport retries (429, 5xx, timeout) that the old code lacks. Note the old driver (`run_study2b.py`, `MAX_ATTEMPTS = 2`) adds one more try at driver level, so a registered record can have up to 6 local calls. The hosted rule is 3 in all, as the task says.
5. The old clients share state. `RecordingOllamaClient.call_log` and `SentPromptClient.sent` are shared lists, and `generate_one` calls `take_log()` and `take_sent()` around each generation. With parallel workers the logs mix. Nearest safe form: one client per worker thread, and the usage data comes back in each `HostedReply`.
6. `OllamaClient` cannot be a base class for the hosted client: its `__init__` builds a local `httpx.Client` and its `generate` posts to `/api/generate`. The hosted client is a separate class with the same call surface (`generate`, `model_name`). `generate` always receives `format="json"` from the generator. The hosted client does not send it (the local run did not send it either, `structured_output=False`).
7. Study 2 records do not hold the sent user prompt (only `prompt_user_non_doc` and the context reference). The anchor uses Study 2b arms A2 and B2, which store `prompt_user_sent`, so there is no problem for step 0. A replay of Study 2 arms B, C, D would need the prompt builder.
8. `n_code_chunks_guess` in `retrieval_frozen.json` is not a count (it disagrees with the text in about 50 of 60 stories). Nearest safe form: count the marks in the text.
9. The hosted tokenizer and chat template differ from the local ones. The local run used a 16,384 token window and stopped nothing silently only because the prompts were short (at most 7,761 tokens, and at most 4,041 answer tokens). A hosted run cannot show a local overflow. Nearest safe form: the preflight checks the stored local token counts (`local_prompt_tokens + 4096 <= 16384`) and the `bytes4` limit of 12,000, and the report shows the ratio of hosted to local prompt tokens.
10. Provider and quantisation cannot be read from the answer in the way the task assumes. The schema of 2026-11-16 has no top-level provider field, and no field gives the quantisation. The provider can be read from `openrouter_metadata` (request header `X-OpenRouter-Metadata: enabled`) or from `GET /api/v1/generation?id=...`. The quantisation is known only from the pinned tag. Nearest safe form: section 3.5 step 9 (two documented sources, the second recorded as such, an error when both fail) and the quantisation in the manifest from the tag. The exact shape of the metadata is from the documentation and is untested.
11. A seed would not make the hosted run repeatable, and some endpoints do not accept it. Not sent. The noise between two repeats is measured instead (`repeat_gap_points`).
12. The research notes suggest a retry on HTTP 402 with `Retry-After`. The task lists 429, 5xx and timeouts only. The client treats 402 as fatal (not enough credit), which is simpler and safe: the run stops and can be resumed.
13. The hash list of the vector store has 764 files and about 700 MB. It is read twice for each run (before and after). The folder is in iCloud Drive, and an evicted file makes the read wait. It is read as plain files and never with ChromaDB. If this takes too long, the main session can run `--dry-run --skip-hash-lists` for a quick look. A real run always checks all three lists.
14. The test command of the task (`pytest tests/unit/test_tuning_<name>.py -q`) writes `.pytest_cache` in the repository root and `__pycache__` folders in `tests/unit/` and `scripts/tuning_lib/`. This is a write outside the named files (a cache, not data). Nearest safe form: run with `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`. `tuning_lib/__pycache__` is inside an allowed folder anyway.
15. The root `conftest.py` forces `CliRunner(mix_stderr=False)` and `tests/unit/test_cli.py` fails at collection (Click 8.3). New tests do not use `CliRunner`. Run the new test files one by one, as the task says.

## 12. Open points for the main session

1. The provider tags and expected provider names in `HOSTED_MODELS` are proposals. They must be confirmed with the smoke run (4 generations) before the 120-generation run. The same smoke run shows whether the pinned endpoints accept `min_p`, `presence_penalty` and `repetition_penalty` for Qwen with `require_parameters: true`, and whether `max_completion_tokens` and the `plugins` field are accepted.
2. Zero data retention (`provider.zdr: true`) is not in the request. Research found 14 of 15 Gemma endpoints and 18 of 23 Qwen endpoints in the public ZDR list, but the list does not say for the two proposed endpoints. `data_collection: "deny"` is in the request. If the author wants ZDR, add the field and run the smoke test again.
3. A valid JSON answer with no `requirements` list is a retry and a failure here. The old code counts it as zero requirements without an error. This can change the name-use rate (a zero record counts as 0.0 in the old rule, and a failed record is left out here). The report shows the count. It is expected to be zero on the anchor.
4. The anchor compares hosted with local. The local value comes from one local run (arms A2 and B2 have one run each). The hosted value is the mean of 2 repeats. The limits are the drift limits of Study 2b. A hosted value inside the limit means "the hosted model behaves like the local one for this measure". It does not prove the models are equal.
5. The cap of 60 minutes and the cap of 1.00 USD stop the submission of new jobs. In-flight jobs finish. Overshoot is at most one generation for each worker.
6. `finish_reason` other than `stop` is recorded and counted. It is not a client error. Gemma needed up to 4,041 answer tokens locally, so a `"length"` stop at 4,096 is possible. The runner retries it as an unreadable answer only if the cut makes the JSON unreadable.
