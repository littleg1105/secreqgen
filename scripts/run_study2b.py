"""Study 2b driver: the documents and the "use and cite" instructions, switched on and off.

The question (plan section 6): did the documents make the models name real
project settings, or did the instruction to cite them?

A 2 by 2 table on the same 15 stories, the same stored passages and the same
two generator models as the registered Study 2 run:

                     no instructions        "use and cite" instructions
    no documents     A  (registered)        I0 (new)
    with documents   N1 (new)               B  (registered)

Arms A and B are read from the registered records. They never run again as
registered data. This script runs five new arms, in this order:

    N1  3 runs  arm A prompt plus the stored passages in a plain block      (prompts n1/)
    B2  1 run   repeat of arm B, for the drift check                         (prompts rep/)
    A2  1 run   repeat of arm A, for the drift check                         (prompts rep/)
    I0  3 runs  arm B prompt, passages replaced by one fixed line            (prompts i0/)
    N0  3 runs  N1 with the cite texts S1, S2, S3, U0 and U1 removed         (prompts n0/)

15 stories x 2 models x (3 + 1 + 1 + 3 + 3) runs = 330 generations. The order
is arm first, then model, then run, then project, then story. So a stop leaves
complete arms. The run stops itself after 5 hours (--hard-stop-hours).

The script does no ingestion and no retrieval. It loads no embedder, no code
agent and no vector store. It reads the stored passages of the registered run
(real_test_cases/study2/<project>/results/<model>/retrieval_frozen.json) and
never writes under real_test_cases/study2/.

Output: real_test_cases/study2b/<project>/<model_slug>/
    records.jsonl          one line per (story, arm, run, attempt); resumable
    manifest_s2b.json      arms, times (UTC), Ollama version and digests, hashes, options
    retrieval_frozen.json  a byte copy of the stored passages that were used

Each record holds the fields of a registered record, plus the arm name, the
exact system prompt and user prompt that were sent, and their SHA-256.

Usage (from coded_project/secreqgen; never run two experiment scripts at once):
    TOKENIZERS_PARALLELISM=false PYTHONPATH=src .venv/bin/python scripts/run_study2b.py --dry-run
    ... scripts/run_study2b.py                       # the real run (all guards must pass)
    ... scripts/run_study2b.py --out-root <scratch> --stories MT-ADV-001 --max-runs 1   # smoke run

Guards (each one stops the run before any model is loaded, with a plain message):
    the output folder is inside real_test_cases/study2 (in any upper or lower case), inside the data
    folder or inside the real output folder, or a folder is named "results";
    the real output folder gets a part of the plan (--arms, --models, --projects, --stories, --max-runs);
    a prompt file is missing, or scripts/check_s2b_prompts.py fails;
    a stored context is missing, has the wrong story ids or hash, or is the 41-character fallback;
    the Ollama version or a model digest differs from the registered manifests (unless --allow-drift);
    another experiment script runs, or the lock file of this script exists;
    the human label file of the review is not locked (plan section 6, condition 2);
    for the real output folder only: lab notebook section 16 is missing or not committed (condition 6).
--dry-run prints the plan and the result of each check. It writes nothing and calls no model.

During the run, a failed generation is tried once more at once. The run stops (exit 3, resume later)
after a connection error, when GET /api/tags fails, after an HTTP error from Ollama (for example a
model that cannot load), or when 4 generations in a row fail. So an Ollama fault cannot turn the rest
of the plan into recorded failures.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # importing the registered scripts must not write files next to them

import argparse  # noqa: E402
import hashlib  # noqa: E402
import inspect  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import platform  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import check_s2b_prompts as prompt_check  # noqa: E402
from run_study2 import (  # noqa: E402  (the registered driver; its main() runs only as __main__)
    CROSS_FAMILY,
    PROMPTS_DIR,
    REAL_TEST_CASES,
    SECREQGEN,
    STUDY2,
    FrozenRetriever,
    RecordingOllamaClient,
    _slug,
    empty_result,
    est_tokens,
    load_stories,
    ollama_digests,
    ollama_version,
    requirement_dicts,
    sha256_text,
)
from secreqgen.config import SecReqGenConfig  # noqa: E402
from secreqgen.exceptions import LLMConnectionError  # noqa: E402
from secreqgen.generation.generator import (  # noqa: E402
    GenerationMode,
    SecurityRequirementsGenerator,
)
from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder  # noqa: E402

THESIS = REAL_TEST_CASES.parent.parent
STUDY2B = REAL_TEST_CASES / "study2b"
PROMPTS_S2B = prompt_check.S2B
REVIEW_V2 = STUDY2 / "review_v2"
NOTEBOOK = SECREQGEN / "docs" / "RAG_ABLATION_LAB_NOTEBOOK.md"
HASH_LIST = THESIS / "thesis" / "evidence" / "study2_registered_hashes_2026-11-14.sha256"
LOCK_NAME = "run_study2b.lock"
PROJECTS = ("mastodon", "keycloak", "bitwarden")
MODELS = tuple(CROSS_FAMILY)  # ("qwen3.8:27b", "gemma4:26b"), the two registered generators
MAX_HOURS = 5.0
MAX_ATTEMPTS = 2  # one retry, as registered (analysis lock 4.7)
MAX_PROMPT_TOKENS_EST = 12000  # bytes / 4; num_ctx 16384 minus room for the answer

# Arm I0 gets this line in place of the passages. It is not the 41-character
# fallback of the retriever, so the 41-character check still means broken retrieval.
FIXED_LINE = "No project documentation was retrieved for this story."
FALLBACK = "No relevant security documentation found."  # retriever.py:343, prompts.py:362

# Generation options. They must equal the registered run (run_study2.py).
#   base_url, temperature, max_tokens: SecReqGenConfig().llm, the same call as run_study2.py:424,
#       used as in run_study2.py:269-270. They are compared with the six registered manifests.
#   num_ctx: the --num-ctx default of run_study2.py:414 (16384). It is read from the six registered
#       manifests (config.num_ctx), which record the value that was used.
#   timeout 900 s and structured_output=False: literals in run_study2.py:271. There is no constant to
#       import, so they are copied here. A unit test reads run_study2.py and checks the literals.
#   think=False and no "format" field: OllamaClient.generate in src/secreqgen/generation/llm_client.py,
#       the same class (through RecordingOllamaClient).
#   3 attempts on a JSON parse failure, waits of 1 s and 2 s: the defaults of
#       SecurityRequirementsGenerator, built with the same arguments as run_study2.py:339-340.
GEN_TIMEOUT_S = 900.0
STRUCTURED_OUTPUT = False

# A process whose command line matches this is another script that calls a local model or writes
# the vector store. "run_study2.py" matches the registered driver only, not run_study2_ai_raters.py
# (that script calls no local model). "ollama run" does not match the "ollama runner" processes
# that the Ollama server starts.
OTHER_SCRIPTS = re.compile(
    r"run_study2b?\.py|run_cross_family|run_adversarial|run_idea21|run_threat_mapping"
    r"|rag_ablation_|judge_content_quality|judge_retrieval_quality|bootstrap_dataset"
    r"|(?:-m\s+|/)secreqgen(?:\.cli)?\s+(?:ingest|generate|evaluate)\b|\bollama\s+run\b"
)
# A failed generation with this error text is an HTTP error from Ollama (llm_client.py), for example
# a model that cannot load. It is not an answer of the model, so the run stops at once.
SERVER_ERROR_PREFIX = "GenerationError: Ollama API error"
# Two stories in a row with a failure after the one retry: the run stops (check Ollama, then resume).
MAX_FAILED_IN_A_ROW = 4
NOTEBOOK_HEADING = re.compile(r"^## (§)?16(?![0-9])")


@dataclass(frozen=True)
class Arm:
    name: str
    folder: str  # prompt folder under data/winning_prompts_s2b/
    mode: str  # "rag" or "zero_shot"
    context: str  # "frozen" (stored passages), "fixed_line" or "none"
    runs: int
    description: str


# The order of this tuple is the order of the night (plan section 6).
ARMS = (
    Arm("N1", "n1", "rag", "frozen", 3,
        "documents only: the arm A prompt plus the stored passages in a plain block"),
    Arm("B2", "rep", "rag", "frozen", 1,
        "drift check: arm B again, byte copies of the registered prompt files"),
    Arm("A2", "rep", "zero_shot", "none", 1,
        "drift check: arm A again, byte copies of the registered prompt files"),
    Arm("I0", "i0", "rag", "fixed_line", 3,
        "instructions only: the arm B prompt with the passages replaced by one fixed line"),
    Arm("N0", "n0", "rag", "frozen", 3,
        "documents, no cite sentence: arm N1 with the deletions S1, S2, S3, U0 and U1"),
)
ARM_BY_NAME = {a.name: a for a in ARMS}
DOC_CONTEXT_REF = {"frozen": "frozen", "fixed_line": "fixed_line", "none": None}


class UsageError(Exception):
    """A wrong option. Nothing was written."""


class PlanError(Exception):
    """A prompt cannot be built in the exact way the design says."""


class RunStop(Exception):
    """The run must stop now. The records that exist are complete."""


@dataclass
class Check:
    condition: str  # "1" to "7" (plan section 6) or "-" for a guard of this script
    name: str
    status: str  # PASS, FAIL, WARN (does not block), MANUAL (a person checks), SKIP
    detail: str = ""


@dataclass(frozen=True)
class Step:
    arm: Arm
    model: str
    run: int
    project: str
    story_id: str

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.story_id, self.arm.name, self.run)


@dataclass
class Selection:
    arms: list[Arm]
    models: list[str]
    projects: list[str]
    stories: set[str] | None
    max_runs: int | None


@dataclass
class Cell:
    """One (project, model): its stories, system context and stored passages."""

    project: str
    model: str
    dataset: dict
    stories: list
    all_story_ids: list[str]
    system_context: str | None
    frozen: dict
    frozen_src: Path
    out_dir: Path
    _by_id: dict = field(default_factory=dict)

    def story(self, story_id: str):
        if not self._by_id:
            self._by_id = {s.id: s for s in self.stories}
        return self._by_id[story_id]


# ─────────────────────────────────────────────────────────────────────
# Small helpers (tests replace some of them)
# ─────────────────────────────────────────────────────────────────────

_clock = time.monotonic


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered_dir(project: str, model: str) -> Path:
    return STUDY2 / project / "results" / _slug(model)


def frozen_path(project: str, model: str) -> Path:
    return registered_dir(project, model) / "retrieval_frozen.json"


def registered_manifests() -> list[dict]:
    return [json.loads((registered_dir(p, m) / "manifest.json").read_text()) for p in PROJECTS for m in MODELS]


def is_inside(path: Path, base: Path) -> bool:
    """True when path is base or a folder below it.

    The file system of this Mac does not see a difference between upper and lower case, so
    real_test_cases/STUDY2 is the same folder as real_test_cases/study2. The test compares the
    resolved paths, the same paths in one case, and each existing folder with the same-file test.
    """
    p, b = path.expanduser().resolve(), base.expanduser().resolve()
    if p == b or p.is_relative_to(b):
        return True
    pc, bc = str(p).casefold(), str(b).casefold()
    if pc == bc or pc.startswith(bc.rstrip("/") + "/"):
        return True
    if b.exists():
        for folder in (p, *p.parents):
            try:
                if folder.exists() and os.path.samefile(folder, b):
                    return True
            except OSError:
                continue
    return False


def is_real_root(out_root: Path) -> bool:
    """True when out_root is the real output folder (also when it is written in another case)."""
    p, b = out_root.expanduser().resolve(), STUDY2B.resolve()
    if p == b or str(p).casefold() == str(b).casefold():
        return True
    try:
        return p.exists() and b.exists() and os.path.samefile(p, b)
    except OSError:
        return False


def ollama_state(base_url: str) -> tuple[str, dict]:
    """GET /api/version and GET /api/tags. No model is loaded."""
    return ollama_version(base_url), ollama_digests(base_url)


def list_processes() -> list[tuple[int, int, str]]:
    r = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=30)
    rows = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return rows


def git_porcelain(path: Path) -> str | None:
    """Output of 'git status --porcelain' for one file, or None when git fails. Read only."""
    try:
        r = subprocess.run(["git", "-C", str(path.parent), "status", "--porcelain", "--", path.name],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


class SentPromptClient(RecordingOllamaClient):
    """The registered RecordingOllamaClient. It also keeps the exact prompts that each call sends."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[tuple[str, str | None]] = []

    def generate(self, prompt: str, system_prompt: str | None = None, format: str | None = None):
        self.sent.append((prompt, system_prompt))
        return super().generate(prompt, system_prompt=system_prompt, format=format)

    def take_sent(self) -> list[tuple[str, str | None]]:
        sent, self.sent = self.sent, []
        return sent


def make_client(model: str, options: dict) -> SentPromptClient:
    """The generator client, built as in run_study2.py:269-271."""
    return SentPromptClient(base_url=options["base_url"], model_name=model,
                            temperature=options["temperature"], max_tokens=options["max_tokens"],
                            timeout=options["timeout_s"], num_ctx=options["num_ctx"],
                            structured_output=options["structured_output"])


def generation_options(cfg: SecReqGenConfig, manifests: list[dict]) -> dict:
    nums = {m["config"]["num_ctx"] for m in manifests}
    if len(nums) != 1:
        raise PlanError(f"the registered manifests do not agree on num_ctx: {sorted(nums)}")
    params = inspect.signature(SecurityRequirementsGenerator.__init__).parameters
    return {
        "base_url": cfg.llm.base_url,
        "temperature": cfg.llm.temperature,
        "max_tokens": cfg.llm.max_tokens,
        "num_ctx": nums.pop(),
        "timeout_s": GEN_TIMEOUT_S,
        "structured_output": STRUCTURED_OUTPUT,
        "think": False,
        "format_sent": None,
        "generator_max_retries": params["max_retries"].default,
        "generator_retry_delay_s": params["retry_delay"].default,
    }


OPTION_SOURCES = {
    "base_url": "SecReqGenConfig().llm.base_url, as run_study2.py:269",
    "temperature": "SecReqGenConfig().llm.temperature, as run_study2.py:270; equal to the registered manifests",
    "max_tokens": "SecReqGenConfig().llm.max_tokens, as run_study2.py:270; equal to the registered manifests",
    "num_ctx": "config.num_ctx of the six registered manifests (run_study2.py:414 default 16384)",
    "timeout_s": "literal timeout=900.0 at run_study2.py:271 (checked by a unit test)",
    "structured_output": "literal structured_output=False at run_study2.py:271 (checked by a unit test)",
    "think": "OllamaClient.generate payload (src/secreqgen/generation/llm_client.py)",
    "format_sent": "not sent, because structured_output is False (llm_client.py)",
    "generator_max_retries": "SecurityRequirementsGenerator default, as run_study2.py:339-340",
    "generator_retry_delay_s": "SecurityRequirementsGenerator default, as run_study2.py:339-340",
}


# ─────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────

def make_builders(prompts_root: Path | None = None) -> dict[str, SecurityRequirementsPromptBuilder]:
    root = prompts_root or PROMPTS_S2B
    return {folder: SecurityRequirementsPromptBuilder(prompts_dir=root / folder) for folder in prompt_check.FOLDERS}


def context_for(arm: Arm, cell: Cell, story_id: str) -> str | None:
    if arm.context == "frozen":
        return cell.frozen[story_id]["context"]
    if arm.context == "fixed_line":
        return FIXED_LINE
    return None


def remove_context(prompt: str, context: str) -> str:
    """The sent prompt with the exact context string removed (the non-documentation prompt)."""
    if not context or prompt.count(context) != 1:
        raise PlanError("the context must occur exactly one time in the prompt")
    return prompt.replace(context, "", 1)


def expected_prompts(arm: Arm, builder: SecurityRequirementsPromptBuilder, story,
                     system_context: str | None, context: str | None) -> dict:
    """The system prompt, the user prompt and the non-documentation prompt of one generation."""
    system = builder.build_system_prompt()
    if arm.mode == "zero_shot":
        user = builder.build_zero_shot_prompt(user_story=story, system_context=system_context)
        return {"system": system, "user": user, "non_doc": user}
    user = builder.build_rag_prompt(user_story=story, retrieved_context=context, system_context=system_context)
    non_doc = remove_context(user, context)
    if non_doc != builder.build_rag_prompt(user_story=story, retrieved_context="", system_context=system_context):
        raise PlanError("removing the context does not give the prompt with an empty context")
    return {"system": system, "user": user, "non_doc": non_doc}


def removed_texts() -> tuple[str, ...]:
    """Texts that the deletions S1, S2, S3, U0 and U1 remove. None may be in an N0 prompt."""
    return (
        prompt_check.S1[0].strip(),
        "or provided documentation",
        prompt_check.S3_START.strip(),
        prompt_check.TARGET_SENTENCE,
        prompt_check.U0[0].strip(", )\n"),
        prompt_check.U1A[0].strip(", "),
        prompt_check.U1B_START.strip(", "),
    )


def check_one_prompt(arm: Arm, p: dict, story, system_context, context, reg_pb) -> str | None:
    """None when the prompt of this arm is what the design says, else the reason."""
    reg_system = reg_pb.build_system_prompt()
    reg_a = reg_pb.build_zero_shot_prompt(user_story=story, system_context=system_context)
    if arm.name == "A2":
        ok = p["user"] == reg_a and p["system"] == reg_system
        return None if ok else "A2 differs from the registered arm A prompt"
    if arm.name == "B2":
        reg_b = reg_pb.build_rag_prompt(user_story=story, retrieved_context=context, system_context=system_context)
        return None if p["user"] == reg_b and p["system"] == reg_system else "B2 differs from the registered arm B prompt"
    if arm.name == "I0":
        reg_i = reg_pb.build_rag_prompt(user_story=story, retrieved_context=FIXED_LINE, system_context=system_context)
        ok = p["user"] == reg_i and p["system"] == reg_system and FIXED_LINE in p["user"]
        return None if ok else "I0 differs from the registered arm B prompt with the fixed line"
    if arm.name == "N1":
        if reg_a.count(prompt_check.ANCHOR) != 1:
            return "the user story heading is not in the arm A prompt one time"
        i = reg_a.index(prompt_check.ANCHOR)
        want = reg_a[:i] + prompt_check.BLOCK.replace("$retrieved_context", context) + reg_a[i:]
        ok = p["user"] == want and p["system"] == reg_system
        return None if ok else "N1 is not the arm A prompt plus the block with the stored passages"
    if arm.name == "N0":
        hits = [t for t in removed_texts() if t in p["system"] or t in p["non_doc"]]
        if hits:
            return f"N0 still holds removed text: {hits}"
        return None if context in p["user"] else "N0 does not hold the stored passages"
    return f"unknown arm {arm.name}"


# ─────────────────────────────────────────────────────────────────────
# Selection, cells and plan
# ─────────────────────────────────────────────────────────────────────

def parse_selection(args) -> Selection:
    def split(text: str) -> list[str]:
        return [x.strip() for x in text.split(",") if x.strip()]

    want_arms = [x.upper() for x in split(args.arms)]
    bad = [a for a in want_arms if a not in ARM_BY_NAME]
    if bad or not want_arms:
        raise UsageError(f"unknown arm {bad}. The arms are: {', '.join(ARM_BY_NAME)}")
    arms = [a for a in ARMS if a.name in want_arms]  # always in the order of the night
    models = split(args.models)
    bad = [m for m in models if m not in MODELS]
    if bad or not models:
        raise UsageError(f"unknown model {bad}. The models are: {', '.join(MODELS)}")
    models = [m for m in MODELS if m in models]
    projects = split(args.projects)
    bad = [p for p in projects if p not in PROJECTS]
    if bad or not projects:
        raise UsageError(f"unknown project {bad}. The projects are: {', '.join(PROJECTS)}")
    projects = [p for p in PROJECTS if p in projects]
    stories = set(split(args.stories)) if args.stories else None
    if stories:
        known = set()
        for p in projects:
            known |= {s["id"] for s in json.loads((STUDY2 / p / "stories.json").read_text())["stories"]}
        bad = sorted(stories - known)
        if bad:
            raise UsageError(f"unknown story {bad} for the projects {', '.join(projects)}")
    if args.max_runs is not None and args.max_runs < 1:
        raise UsageError("--max-runs must be 1 or more")
    return Selection(arms, models, projects, stories, args.max_runs)


def load_cells(sel: Selection, out_root: Path) -> list[Cell]:
    cells = []
    for project in sel.projects:
        dataset, stories = load_stories(project, sel.stories)
        if not stories:
            continue
        sc_path = REAL_TEST_CASES / project / "system_context.yaml"  # as run_study2.py:214-215
        system_context = sc_path.read_text() if sc_path.exists() else None
        for model in sel.models:
            src = frozen_path(project, model)
            cells.append(Cell(project=project, model=model, dataset=dataset, stories=stories,
                              all_story_ids=[s["id"] for s in dataset["stories"]],
                              system_context=system_context, frozen=json.loads(src.read_text()),
                              frozen_src=src, out_dir=out_root / project / _slug(model)))
    return cells


def build_plan(sel: Selection, cells: list[Cell]) -> list[Step]:
    """Arm first, then model, then run, then project, then story (plan section 6)."""
    by_cell = {(c.project, c.model): c for c in cells}
    plan = []
    for arm in sel.arms:
        runs = arm.runs if sel.max_runs is None else min(arm.runs, sel.max_runs)
        for model in sel.models:
            for run in range(1, runs + 1):
                for project in sel.projects:
                    cell = by_cell.get((project, model))
                    if cell is None:
                        continue
                    for s in cell.stories:
                        plan.append(Step(arm, model, run, project, s.id))
    return plan


def read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def key_states(records: list[dict]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for r in records:
        st = out.setdefault((r["story_id"], r["arm"], r["run"]), {"ok": False, "errors": 0})
        if r.get("error"):
            st["errors"] += 1
        else:
            st["ok"] = True
    return out


def is_final(state: dict | None) -> bool:
    """A parsed answer, or a recorded failure after the one retry."""
    return state is not None and (state["ok"] or state["errors"] >= MAX_ATTEMPTS)


def complete_arms(states: dict, story_ids: list[str]) -> list[str]:
    """Arms where each story has each planned run (of the design, not of --max-runs) final."""
    return [a.name for a in ARMS
            if all(is_final(states.get((sid, a.name, r))) for sid in story_ids for r in range(1, a.runs + 1))]


# ─────────────────────────────────────────────────────────────────────
# Checks (plan section 6: conditions before the first generation)
# ─────────────────────────────────────────────────────────────────────

def check_out_root(out_root: Path) -> Check:
    name = "the output folder is allowed"
    r = out_root.expanduser().resolve()
    parts = [p.lower() for p in r.parts]
    if is_inside(r, STUDY2):
        return Check("-", name, "FAIL", f"{r} is inside {STUDY2}. Study 2b never writes there.")
    if "results" in parts:
        return Check("-", name, "FAIL", f"{r} has a folder named 'results'. Use a new folder, for example {STUDY2B}.")
    if is_inside(r, SECREQGEN / "data"):
        return Check("-", name, "FAIL", f"{r} is inside the data folder (prompts and vector store).")
    if is_inside(r, STUDY2B) and not is_real_root(r):
        return Check("-", name, "FAIL", f"{r} is a folder inside the real output folder {STUDY2B}. "
                                        "Use the real folder itself, or a scratch folder outside it.")
    return Check("-", name, "PASS", str(r))


def check_hash_list(path: Path | None = None) -> Check:
    name = "a hash list of the registered Study 2 files is saved, and the files are unchanged"
    path = path or HASH_LIST
    if not path.is_file():
        return Check("1", name, "FAIL", f"the hash list is missing: {path}")
    bad, n, existence_only = [], 0, 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        p = THESIS / rel.strip()
        n += 1
        # Files in the old review folder and in key_do_not_open are not opened: existence only.
        if p.is_relative_to(STUDY2 / "review") or "key_do_not_open" in p.parts:
            existence_only += 1
            if not p.is_file():
                bad.append(rel.strip())
            continue
        if not p.is_file() or sha256_file(p) != digest.strip():
            bad.append(rel.strip())
    need = {"run_study2.py", "analyze_study2.py", "rag_user_prompt.txt", "system_prompt.txt",
            "zero_shot_user_prompt.txt", "retrieval_frozen.json", "stories.json", "manifest.json"}
    listed = {Path(line.partition("  ")[2].strip()).name for line in path.read_text().splitlines() if line.strip()}
    missing = sorted(need - listed)
    if bad or missing or n == 0:
        return Check("1", name, "FAIL", f"changed or missing: {bad[:10]}; not in the list: {missing}")
    return Check("1", name, "PASS", f"{n} files listed, {n - existence_only} hashes equal, "
                                    f"{existence_only} in sealed folders checked for existence only")


def check_label_lock(review_dir: Path | None = None) -> Check:
    name = "the human label file of the review is locked"
    review_dir = review_dir or REVIEW_V2
    try:
        import score_study2_review as review
        locks = review.read_locks(review_dir)
        state = review.lock_state(review_dir, locks, review.HUMAN)
    except Exception as e:  # score_study2_review.Refuse and file errors
        return Check("2", name, "FAIL", f"the lock state cannot be read: {e}")
    if state == "locked_ok":
        return Check("2", name, "PASS", f"{review.HUMAN}: locked_ok")
    return Check("2", name, "FAIL", f"{review.HUMAN}: {state}. Lock the labels first "
                                    "(scripts/score_study2_review.py lock).")


def check_prompt_files(prompts_root: Path | None = None) -> list[Check]:
    root = prompts_root or PROMPTS_S2B
    out = []
    missing = [f"{folder}/{f}" for folder in prompt_check.FOLDERS for f in prompt_check.FILES
               if not (root / folder / f).is_file()]
    out.append(Check("3", "each prompt folder holds its three files", "FAIL" if missing else "PASS",
                     f"missing: {missing}. A missing file falls back to a built-in default without a message."
                     if missing else ", ".join(prompt_check.FOLDERS)))
    if missing:
        out.append(Check("3", "scripts/check_s2b_prompts.py passes", "FAIL", "not run: a prompt file is missing"))
        return out
    results = prompt_check.run_checks(s2b=root)
    fails = [n for ok, n, _ in results if not ok]
    out.append(Check("3", "scripts/check_s2b_prompts.py passes", "FAIL" if fails else "PASS",
                     f"failing: {fails}" if fails else f"{len(results)} checks pass"))
    return out


def check_contexts() -> Check:
    """Condition 4: the 30 stored contexts (15 stories for each of the 2 models)."""
    name = "the 30 stored contexts are verified (file, story ids, hash, none of 41 characters)"
    problems, n, shortest = [], 0, None
    for project in PROJECTS:
        ids = [s["id"] for s in json.loads((STUDY2 / project / "stories.json").read_text())["stories"]]
        for model in MODELS:
            p = frozen_path(project, model)
            if not p.is_file():
                problems.append(f"missing {p}")
                continue
            saved = json.loads(p.read_text())
            if sorted(saved) != sorted(ids):
                problems.append(f"{project}/{model}: story ids differ from stories.json")
            for sid in ids:
                entry = saved.get(sid) or {}
                ctx = entry.get("context")
                if not isinstance(ctx, str):
                    problems.append(f"{sid}/{model}: no context")
                    continue
                n += 1
                shortest = len(ctx) if shortest is None else min(shortest, len(ctx))
                if "context_sha256" in entry and sha256_text(ctx) != entry["context_sha256"]:
                    problems.append(f"{sid}/{model}: SHA-256 differs from the stored hash")
                if "context_chars" in entry and entry["context_chars"] != len(ctx):
                    problems.append(f"{sid}/{model}: length differs from the stored length")
                if len(ctx) <= 41 or ctx.strip() == FALLBACK:
                    problems.append(f"{sid}/{model}: the 41-character fallback (retrieval was broken)")
    if FIXED_LINE == FALLBACK or len(FIXED_LINE) == 41:
        problems.append("the fixed line of arm I0 looks like the fallback string")
    if problems or n != 30:
        return Check("4", name, "FAIL", f"{n} contexts read; problems: {problems[:10]}")
    return Check("4", name, "PASS", f"30 contexts; the shortest has {shortest} characters")


def registered_record_prompts(project: str, model: str) -> dict[tuple[str, str], set]:
    """The prompts that the registered records of arms A and B hold: {(arm, story): {(system, non_doc)}}.

    Only the two prompt fields are read. The system context file (real_test_cases/<project>/system_context.yaml)
    is not on the hash list, so this is the check that A2 and B2 send what the registered run sent.
    """
    out: dict[tuple[str, str], set] = {}
    path = registered_dir(project, model) / "records.jsonl"
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("arm") in ("A", "B"):
                out.setdefault((r["arm"], r["story_id"]), set()).add((r.get("prompt_system"),
                                                                       r.get("prompt_user_non_doc")))
    return out


def check_prompt_build(cells: list[Cell], arms: list[Arm], builders: dict) -> Check:
    """Build each planned prompt and compare it with what the registered builder makes.

    For A2 and B2 the prompt is also compared with the prompt in the registered records of A and B.
    """
    name = "each planned prompt is what the design says, and fits the context window"
    reg_pb = SecurityRequirementsPromptBuilder(prompts_dir=PROMPTS_DIR)
    problems, n, longest = [], 0, 0
    for cell in cells:
        stored = None
        if any(a.name in ("A2", "B2") for a in arms):
            try:
                stored = registered_record_prompts(cell.project, cell.model)
            except (OSError, ValueError, KeyError) as e:
                problems.append(f"{cell.project}/{cell.model}: the registered records cannot be read: {e}")
        for story in cell.stories:
            for arm in arms:
                try:
                    ctx = context_for(arm, cell, story.id)
                    p = expected_prompts(arm, builders[arm.folder], story, cell.system_context, ctx)
                    why = check_one_prompt(arm, p, story, cell.system_context, ctx, reg_pb)
                    if not why and stored is not None and arm.name in ("A2", "B2"):
                        base = {"A2": "A", "B2": "B"}[arm.name]
                        if stored.get((base, story.id)) != {(p["system"], p["non_doc"])}:
                            why = f"{arm.name} differs from the prompt in the registered records of arm {base}"
                except (PlanError, KeyError) as e:
                    why = str(e)
                    p = None
                if why:
                    problems.append(f"{arm.name}/{cell.model}/{story.id}: {why}")
                if p:
                    n += 1
                    longest = max(longest, est_tokens(p["system"] + p["user"]))
    if longest > MAX_PROMPT_TOKENS_EST:
        problems.append(f"the longest prompt is about {longest} tokens, above {MAX_PROMPT_TOKENS_EST}")
    if problems:
        return Check("3", name, "FAIL", f"{len(problems)} problems, first: {problems[:3]}")
    return Check("3", name, "PASS", f"{n} prompts built; the longest is about {longest:,} tokens (bytes / 4)")


def check_existing_outputs(cells: list[Cell]) -> Check:
    name = "files that exist in the output folders belong to this study"
    problems, n_records = [], 0
    for cell in cells:
        copy = cell.out_dir / "retrieval_frozen.json"
        if copy.is_file() and copy.read_bytes() != cell.frozen_src.read_bytes():
            problems.append(f"{copy} differs from {cell.frozen_src}")
        try:
            recs = read_records(cell.out_dir / "records.jsonl")
        except (ValueError, OSError) as e:
            problems.append(f"{cell.out_dir / 'records.jsonl'} cannot be read: {e}")
            continue
        for r in recs:
            n_records += 1
            if r.get("study") != "study2b" or r.get("arm") not in ARM_BY_NAME or r.get("model") != cell.model \
                    or r.get("project") != cell.project:
                problems.append(f"{cell.out_dir}: a record is not a Study 2b record of this cell")
                break
    if problems:
        return Check("-", name, "FAIL", "; ".join(problems[:5]))
    return Check("-", name, "PASS", f"{n_records} records exist already (they are not generated again)")


def check_notebook(out_root: Path, notebook: Path | None = None) -> Check:
    name = "lab notebook section 16 exists, is approved and is committed"
    if not is_real_root(out_root):
        return Check("6", name, "SKIP", "only checked for the real output folder")
    nb = notebook or NOTEBOOK
    if not nb.is_file():
        return Check("6", name, "FAIL", f"the notebook is missing: {nb}")
    lines = nb.read_text().splitlines()
    idx = [i for i, line in enumerate(lines) if NOTEBOOK_HEADING.match(line)]
    if not idx:
        return Check("6", name, "FAIL", "no heading that starts with '## 16' (or '## §16') in the notebook")
    section = []
    for line in lines[idx[0] + 1:]:
        if line.startswith("## "):
            break
        section.append(line)
    approved = [x for x in section if "Approved by the author on" in x]
    if not approved or any("____" in x for x in approved):
        return Check("6", name, "FAIL", "section 16 has no filled line 'Approved by the author on ...'")
    porcelain = git_porcelain(nb)
    if porcelain is None:
        return Check("6", name, "FAIL", "git status did not work for the notebook")
    if porcelain.strip():
        return Check("6", name, "FAIL", f"the notebook has a change that is not committed ({porcelain.strip()[:2]})")
    return Check("6", name, "PASS", "heading found, approval line filled, no uncommitted change")


def check_ollama(base_url: str, allow_drift: bool, manifests: list[dict]) -> tuple[Check, dict]:
    name = "the Ollama version and both model digests equal the registered manifests"
    version, digests = ollama_state(base_url)
    info = {"version": version, "digests": {m: digests.get(m) for m in MODELS}, "drift": []}
    if version in ("unreachable", "?") or "error" in digests:
        return Check("7", name, "FAIL", f"Ollama does not answer at {base_url}"), info
    reg_versions = sorted({m["ollama"]["version"] for m in manifests})
    if reg_versions != [version]:
        info["drift"].append(f"Ollama version: registered {reg_versions}, now {version}")
    for model in MODELS:
        reg = sorted({m["ollama"]["digests"].get(model) or "" for m in manifests})
        now = digests.get(model) or ""
        if reg != [now]:
            info["drift"].append(f"{model} digest: registered {reg}, now {now or 'not installed'}")
    if not info["drift"]:
        return Check("7", name, "PASS", f"version {version}; digests equal"), info
    if allow_drift:
        return Check("7", name, "WARN", "difference allowed by --allow-drift and written into the manifest: "
                     + "; ".join(info["drift"])), info
    return Check("7", name, "FAIL", "; ".join(info["drift"]) + ". Write the difference in the note, then "
                 "use --allow-drift."), info


def check_generation_options(options: dict, manifests: list[dict]) -> Check:
    name = "the generation options equal the registered run"
    bad = []
    for key in ("temperature", "max_tokens", "num_ctx"):
        reg = {m["config"][key] for m in manifests}
        if reg != {options[key]}:
            bad.append(f"{key}: registered {sorted(reg)}, now {options[key]}")
    if bad:
        return Check("7", name, "FAIL", "; ".join(bad))
    return Check("7", name, "PASS", ", ".join(f"{k} {options[k]}" for k in ("temperature", "max_tokens", "num_ctx",
                                                                         "timeout_s", "structured_output")))


def check_processes() -> Check:
    name = "no other experiment script is running"
    procs = list_processes()
    parent = {pid: ppid for pid, ppid, _ in procs}
    mine, pid = set(), os.getpid()
    while pid and pid not in mine:  # this process and the shells that started it
        mine.add(pid)
        pid = parent.get(pid, 0)
    others = [f"{pid}: {cmd[:120]}" for pid, _, cmd in procs if pid not in mine and OTHER_SCRIPTS.search(cmd)]
    if others:
        return Check("7", name, "FAIL", "running: " + " | ".join(others[:3]))
    return Check("7", name, "PASS", "none found in the process list")


def check_lock_file(out_root: Path) -> Check:
    lock = out_root / LOCK_NAME
    if lock.exists():
        return Check("7", "the lock file of this script does not exist", "FAIL",
                     f"{lock} exists. Another run may be active. If no run is active "
                     "(check with: ps -ax | /usr/bin/grep run_study2b), delete the file and start again.")
    return Check("7", "the lock file of this script does not exist", "PASS", str(lock))


def check_hard_stop(hours: float, out_root: Path) -> Check:
    name = "the run stops itself after at most 5 hours"
    if not hours > 0:
        return Check("7", name, "FAIL", "--hard-stop-hours must be above 0")
    if is_real_root(out_root) and hours > MAX_HOURS:
        return Check("7", name, "FAIL", f"--hard-stop-hours {hours} is above {MAX_HOURS} for the real run")
    return Check("7", name, "PASS", f"hard stop after {hours:g} hours; the analysis uses complete arms only")


def manual_checks() -> list[Check]:
    return [
        Check("3", "a second model read the prompt differences and found no hidden instruction", "MANUAL",
              "final_week/study2b_prompt_differences.md"),
        Check("5", "a small run of one story into a scratch folder passed (nobody reads the text)", "MANUAL",
              "--out-root <scratch> --stories MT-ADV-001 --max-runs 1"),
        Check("7", "the tests T5 to T8 have passed", "MANUAL", "plan section 11"),
        Check("7", "a dry run writes nothing", "MANUAL",
              "compare a listing of the output folder before and after --dry-run"),
    ]


def preflight(sel: Selection, out_root: Path, *, allow_drift: bool, hard_stop_hours: float) -> tuple[list[Check], dict]:
    """Every check of the run. Nothing is written. No model is called."""
    checks: list[Check] = [check_out_root(out_root)]
    state: dict = {"cells": [], "builders": None, "options": None, "ollama": None, "manifests": []}
    if sel.max_runs is not None and is_real_root(out_root):
        checks.append(Check("-", "--max-runs is only for a smoke run", "FAIL",
                            "--max-runs cannot be used with the real output folder"))
    full_plan = ([a.name for a in sel.arms] == list(ARM_BY_NAME) and sel.models == list(MODELS)
                 and sel.projects == list(PROJECTS) and sel.stories is None)
    if is_real_root(out_root) and not full_plan:
        checks.append(Check("-", "the real output folder gets the full plan only", "FAIL",
                            "--arms, --models, --projects and --stories are only for a scratch folder. "
                            "The real run keeps the order of the night (plan section 6)."))
    checks.append(check_hash_list())
    checks.append(check_label_lock())
    prompt_checks = check_prompt_files()
    checks += prompt_checks
    checks.append(check_contexts())
    try:
        state["cells"] = load_cells(sel, out_root)
    except (OSError, ValueError, KeyError) as e:
        checks.append(Check("4", "the stories and stored passages load", "FAIL", str(e)))
    if all(c.status == "PASS" for c in prompt_checks) and state["cells"]:
        state["builders"] = make_builders()
        checks.append(check_prompt_build(state["cells"], sel.arms, state["builders"]))
    else:
        checks.append(Check("3", "each planned prompt is what the design says", "FAIL",
                            "not checked: the prompt files or the stories did not load"))
    if state["cells"]:
        checks.append(check_existing_outputs(state["cells"]))
    checks.append(check_notebook(out_root))
    try:
        state["manifests"] = registered_manifests()
        cfg = SecReqGenConfig()
        state["options"] = generation_options(cfg, state["manifests"])
        checks.append(check_generation_options(state["options"], state["manifests"]))
        ollama_check, state["ollama"] = check_ollama(state["options"]["base_url"], allow_drift, state["manifests"])
        checks.append(ollama_check)
    except (OSError, ValueError, KeyError, PlanError) as e:
        checks.append(Check("7", "the registered manifests and options load", "FAIL", str(e)))
    checks.append(check_processes())
    checks.append(check_lock_file(out_root))
    checks.append(check_hard_stop(hard_stop_hours, out_root))
    checks += manual_checks()
    order = {str(i): i for i in range(1, 8)}
    checks.sort(key=lambda c: order.get(c.condition, 0))
    return checks, state


def print_checks(checks: list[Check]) -> None:
    print("Conditions before the first generation (plan section 6) and guards of this script:")
    for c in checks:
        label = "guard" if c.condition == "-" else f"cond {c.condition}"
        print(f"  [{label:6s}] {c.status:6s} {c.name}" + (f"\n                  {c.detail}" if c.detail else ""))
    n = {s: sum(1 for c in checks if c.status == s) for s in ("PASS", "FAIL", "WARN", "MANUAL", "SKIP")}
    print(f"  {n['PASS']} pass, {n['FAIL']} fail, {n['WARN']} warn, {n['MANUAL']} need a person, {n['SKIP']} skipped.")


def print_plan(sel: Selection, cells: list[Cell], plan: list[Step], out_root: Path, hours: float) -> None:
    print(f"Output folder: {out_root}")
    print("Order: arm first, then model, then run, then project, then story.")
    existing = {}
    for c in cells:
        existing[(c.project, c.model)] = key_states(read_records(c.out_dir / "records.jsonl"))
    todo = sum(1 for s in plan if not is_final(existing.get((s.project, s.model), {}).get(s.key)))
    for i, arm in enumerate(sel.arms, 1):
        steps = [s for s in plan if s.arm is arm]
        runs = arm.runs if sel.max_runs is None else min(arm.runs, sel.max_runs)
        n_stories = len({s.story_id for s in steps})
        print(f"  {i}. {arm.name}  runs {runs}  stories {n_stories}  models {len(sel.models)}  "
              f"generations {len(steps)}  prompts {arm.folder}/  ({arm.description})")
    print(f"  Total: {len(plan)} generations planned; {len(plan) - todo} exist already; {todo} to do.")
    print(f"  Models: {', '.join(sel.models)}. Projects: {', '.join(sel.projects)}.")
    print(f"  Hard stop after {hours:g} hours.")


# ─────────────────────────────────────────────────────────────────────
# The run
# ─────────────────────────────────────────────────────────────────────

def prompt_hashes() -> dict[str, str]:
    return {f"{folder}/{f}": sha256_file(PROMPTS_S2B / folder / f)
            for folder in prompt_check.FOLDERS for f in prompt_check.FILES}


def script_hashes() -> dict[str, str]:
    out = {}
    for name in ("run_study2.py", "analyze_study2.py", "run_study2b.py", "check_s2b_prompts.py",
                 "analyze_study2b.py"):
        p = SCRIPTS / name
        out[name] = sha256_file(p) if p.is_file() else None
    gen = SECREQGEN / "src" / "secreqgen" / "generation"
    out.update({f"src/secreqgen/generation/{p.name}": sha256_file(p) for p in sorted(gen.glob("*.py"))})
    return out


def generate_one(step: Step, cell: Cell, builder, generator, client, attempt: int) -> tuple[dict, bool]:
    """One generation and its record. Returns (record, prompt_differs)."""
    story = cell.story(step.story_id)
    arm = step.arm
    ctx = context_for(arm, cell, story.id)
    exp = expected_prompts(arm, builder, story, cell.system_context, ctx)
    client.take_log()
    client.take_sent()
    t0 = time.perf_counter()
    out: dict = {}
    try:
        mode = GenerationMode.ZERO_SHOT if arm.mode == "zero_shot" else GenerationMode.RAG
        result = generator.generate(story, mode=mode, system_context=cell.system_context)  # as run_study2.py:374
        out["requirements"] = requirement_dicts(result)
        out["n_requirements"] = len(result.requirements)
        out["n_threat_mappings"] = len(result.threat_mappings)
        out["error"] = None
    except Exception as e:
        out["requirements"] = []
        out["n_requirements"] = 0
        out["error"] = f"{type(e).__name__}: {e}"
    sent = client.take_sent()
    calls = client.take_log()
    differs = False
    if sent:
        sent_user, sent_system = sent[-1]
        same = all(u == exp["user"] and s == exp["system"] for u, s in sent)
        differs = not same
        prompt_check_text = "equal to the expected prompt" if same else "DIFFERENT from the expected prompt"
    else:
        sent_user, sent_system = exp["user"], exp["system"]
        prompt_check_text = "no call was sent; the expected prompt is stored"
    try:
        non_doc = sent_user if ctx is None else remove_context(sent_user, ctx)
    except PlanError:
        non_doc, differs = exp["non_doc"], True
    rec = {
        "study": "study2b",
        "project": cell.project, "model": step.model,
        "agent_model": CROSS_FAMILY[step.model],  # the code agent that made the stored passages (no call now)
        "story_id": story.id, "arm": arm.name, "run": step.run, "attempt": attempt,
        "ts": utc_now(),
        "prompt_system": sent_system, "prompt_user_non_doc": non_doc,
        "doc_context_ref": DOC_CONTEXT_REF[arm.context],
        "doc_context_sha256": None if ctx is None else sha256_text(ctx),
        "arm_description": arm.description,
        "prompts_dir": str(PROMPTS_S2B / arm.folder),
        "prompt_user_sent": sent_user,
        "prompt_system_sha256": sha256_text(sent_system or ""),
        "prompt_user_sha256": sha256_text(sent_user),
        "n_calls_sent": len(sent),
        "prompt_check": prompt_check_text,
        **out,
        "llm_calls": calls,
        "prompt_tokens": calls[-1]["prompt_tokens"] if calls else None,
        "completion_tokens": sum(c["completion_tokens"] for c in calls),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    return rec, differs


def append_record(path: Path, rec: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def cell_manifest(cell: Cell, sel: Selection, state: dict, run_info: dict) -> dict:
    states = key_states(read_records(cell.out_dir / "records.jsonl"))
    return {
        "study": "study2b", "project": cell.project, "model": cell.model,
        "agent_model_of_stored_passages": CROSS_FAMILY[cell.model],
        "arms_planned": {a.name: {"runs": a.runs if sel.max_runs is None else min(a.runs, sel.max_runs),
                                  "prompts_dir": str(PROMPTS_S2B / a.folder), "mode": a.mode,
                                  "context": a.context, "description": a.description} for a in sel.arms},
        "arms_complete": complete_arms(states, cell.all_story_ids),
        "complete_means": "each story of the project has each run of the design (N1, I0, N0: 3; A2, B2: 1) "
                          "with a parsed answer, or with a failure after the one retry",
        "stories_planned": [s.id for s in cell.stories],
        "run_order": "arm first, then model, then run, then project, then story",
        "dataset_sha256": sha256_text((STUDY2 / cell.project / "stories.json").read_text()),
        "frozen_source": str(cell.frozen_src), "frozen_sha256": sha256_file(cell.frozen_src),
        "frozen_copy": str(cell.out_dir / "retrieval_frozen.json"),
        "fixed_line_I0": FIXED_LINE,
        **run_info,
    }


def write_manifests(cells: list[Cell], sel: Selection, state: dict, run_info: dict) -> None:
    by_model: dict[str, list[set]] = {}
    manifests = {}
    for cell in cells:
        m = cell_manifest(cell, sel, state, run_info)
        manifests[(cell.project, cell.model)] = m
        by_model.setdefault(cell.model, []).append(set(m["arms_complete"]))
    for (project, model), m in manifests.items():
        sets = by_model[model]
        all_projects = len(sets) == len(PROJECTS)
        m["arms_complete_for_model"] = sorted(set.intersection(*sets), key=list(ARM_BY_NAME).index) \
            if all_projects else []
        cell = next(c for c in cells if c.project == project and c.model == model)
        (cell.out_dir / "manifest_s2b.json").write_text(json.dumps(m, indent=2) + "\n")


def execute(sel: Selection, out_root: Path, state: dict, hard_stop_hours: float, allow_drift: bool) -> int:
    cells: list[Cell] = state["cells"]
    builders = state["builders"]
    options = state["options"]
    plan = build_plan(sel, cells)
    out_root.mkdir(parents=True, exist_ok=True)
    lock = out_root / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"refused: {lock} exists. Another run may be active.")
        return 1
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"pid": os.getpid(), "started_at_utc": utc_now(), "host": platform.node()}) + "\n")
    start = _clock()
    deadline = start + hard_stop_hours * 3600.0
    run_info = {
        "started_at_utc": utc_now(), "finished_at_utc": None, "stop_reason": None,
        "hard_stop_hours": hard_stop_hours,
        "ollama": state["ollama"], "allow_drift": allow_drift,
        "drift_from_registered": (state["ollama"] or {}).get("drift", []),
        "prompt_sha256": prompt_hashes(), "script_sha256": script_hashes(),
        "generation_options": options, "generation_options_source": OPTION_SOURCES,
        "host": platform.node(), "argv": sys.argv[1:],
    }
    stop_reason = "the plan is done"
    n_new = 0
    try:
        for cell in cells:
            cell.out_dir.mkdir(parents=True, exist_ok=True)
            copy = cell.out_dir / "retrieval_frozen.json"
            if not copy.exists():
                shutil.copyfile(cell.frozen_src, copy)
            if copy.read_bytes() != cell.frozen_src.read_bytes():
                raise RunStop(f"{copy} differs from the stored passages")
        write_manifests(cells, sel, state, run_info)
        by_cell = {(c.project, c.model): c for c in cells}
        states = {k: key_states(read_records(c.out_dir / "records.jsonl")) for k, c in by_cell.items()}
        clients: dict[str, SentPromptClient] = {}
        generators: dict[tuple, SecurityRequirementsGenerator] = {}
        total = len(plan)
        failed_in_a_row = 0
        for i, step in enumerate(plan, 1):
            cell = by_cell[(step.project, step.model)]
            st = states[(step.project, step.model)]
            if is_final(st.get(step.key)):
                continue
            if step.model not in clients:
                clients[step.model] = make_client(step.model, options)
            client = clients[step.model]
            gkey = (step.arm.name, step.project, step.model)
            if gkey not in generators:
                builder = builders[step.arm.folder]
                if step.arm.mode == "zero_shot":
                    generators[gkey] = SecurityRequirementsGenerator(llm_client=client, retriever=None,
                                                                     prompt_builder=builder)
                else:
                    results = {s.id: empty_result(cell.frozen.get(s.id, {}).get("query_text", ""))
                               for s in cell.stories}
                    contexts = {s.id: context_for(step.arm, cell, s.id) for s in cell.stories}
                    retriever = FrozenRetriever(results, contexts, cell.system_context)
                    generators[gkey] = SecurityRequirementsGenerator(llm_client=client, retriever=retriever,
                                                                     prompt_builder=builder)
            while not is_final(st.get(step.key)):
                if _clock() >= deadline:
                    raise RunStop(f"hard stop: {hard_stop_hours:g} hours have passed")
                attempt = (st.get(step.key) or {"errors": 0})["errors"] + 1
                rec, differs = generate_one(step, cell, builders[step.arm.folder], generators[gkey], client, attempt)
                append_record(cell.out_dir / "records.jsonl", rec)
                n_new += 1
                s = st.setdefault(step.key, {"ok": False, "errors": 0})
                if rec["error"]:
                    s["errors"] += 1
                else:
                    s["ok"] = True
                status = "parsed" if not rec["error"] else f"failed ({rec['error'].split(':')[0]})"
                print(f"  [{i}/{total}] {step.arm.name} {step.model} run {step.run} {step.story_id} "
                      f"attempt {attempt}: {status}, {rec['latency_ms'] / 1000:.0f} s", flush=True)
                if differs:
                    raise RunStop("a sent prompt differs from the expected prompt; see the record")
                if rec["error"]:
                    failed_in_a_row += 1
                    if rec["error"].startswith(LLMConnectionError.__name__) or not client.check_health():
                        raise RunStop("Ollama does not answer; the run stops (resume later)")
                    if rec["error"].startswith(SERVER_ERROR_PREFIX):
                        raise RunStop("Ollama gave an HTTP error, for example for a model that cannot load; "
                                      "the run stops (check Ollama, then resume)")
                    if failed_in_a_row >= MAX_FAILED_IN_A_ROW:
                        raise RunStop(f"{failed_in_a_row} generations in a row failed; the run stops "
                                      "(check Ollama, then resume)")
                else:
                    failed_in_a_row = 0
    except RunStop as e:
        stop_reason = str(e)
    except KeyboardInterrupt:
        stop_reason = "stopped by the user (Ctrl-C)"
    finally:
        run_info["finished_at_utc"] = utc_now()
        run_info["stop_reason"] = stop_reason
        run_info["new_records"] = n_new
        try:
            write_manifests([c for c in cells if c.out_dir.is_dir()], sel, state, run_info)
        finally:
            lock.unlink(missing_ok=True)
    print(f"Stop: {stop_reason}. New records: {n_new}. Lock file removed.")
    for model in sel.models:
        sets = [set(complete_arms(key_states(read_records(c.out_dir / "records.jsonl")), c.all_story_ids))
                for c in cells if c.model == model]
        done = sorted(set.intersection(*sets), key=list(ARM_BY_NAME).index) if len(sets) == len(PROJECTS) else []
        print(f"  {model}: complete arms (all three projects): {', '.join(done) or 'none'}")
    return 0 if stop_reason == "the plan is done" else 3


def parse_args(argv: list[str] | None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-root", default=str(STUDY2B), help=f"output folder (default {STUDY2B})")
    ap.add_argument("--arms", default=",".join(ARM_BY_NAME), help="subset of N1,B2,A2,I0,N0 (order is fixed)")
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--projects", default=",".join(PROJECTS))
    ap.add_argument("--stories", default=None, help="comma-separated story ids (smoke run)")
    ap.add_argument("--max-runs", type=int, default=None,
                    help="smoke run only: at most this many runs per arm (not with the real output folder)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the checks; write nothing")
    ap.add_argument("--hard-stop-hours", type=float, default=MAX_HOURS)
    ap.add_argument("--allow-drift", action="store_true",
                    help="run although the Ollama version or a digest differs; the difference goes into the manifest")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sel = parse_selection(args)
    except UsageError as e:
        print(f"error: {e}")
        return 2
    out_root = Path(args.out_root).expanduser()
    checks, state = preflight(sel, out_root, allow_drift=args.allow_drift, hard_stop_hours=args.hard_stop_hours)
    failed = [c for c in checks if c.status == "FAIL"]
    if args.dry_run:
        print("Study 2b dry run. No model is called and nothing is written.")
        print_plan(sel, state["cells"], build_plan(sel, state["cells"]), out_root, args.hard_stop_hours)
        print()
        print_checks(checks)
        if failed:
            print(f"A real run would refuse to start: {len(failed)} checks fail.")
        print("Dry run done. Nothing was written.")
        return 1 if failed else 0
    if failed:
        print_checks(checks)
        print(f"The run is refused: {len(failed)} checks fail. Nothing was written. No model was called.")
        return 1
    return execute(sel, out_root, state, args.hard_stop_hours, args.allow_drift)


if __name__ == "__main__":
    sys.exit(main())
