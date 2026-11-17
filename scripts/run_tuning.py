"""Test runner for SecReqGen configurations on hosted models (step 0).

Subcommands:
  anchor  send the stored Study 2b prompts (arms A2 and B2) to the hosted models
  report  build report.json and report.md for a finished or stopped run
  run     the prompt lab: several prompt versions on a list of stories, mixed in one session

Every write goes through the guard of tuning_lib. No fallback is silent:
an unexpected state raises an error or is counted in the record.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.metadata
import json
import platform
import random
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from tuning_lib import lab_measures, measures
from tuning_lib.guard import (
    DEFAULT_GUARD,
    LOCK_NAME,
    GuardError,
    RunLock,
    WriteGuard,
    assert_no_other_experiment,
    assert_no_private_text,
    check_all_hash_lists,
    list_processes,
)
from tuning_lib.guard import guarded_append_line
from tuning_lib.guard import guarded_mkdir as make_run_folder
from tuning_lib.guard import guarded_write_text as safe_write
from tuning_lib.openrouter_client import (
    HOSTED_MODELS,
    SAMPLING_COMMON,
    ContractViolation,
    FatalHostedError,
    HostedError,
    ModelPin,
    OpenRouterClient,
    TransientHostedError,
    build_request_body,
    load_api_key,
)
from tuning_lib.parse import (
    NoRequirementsList,
    UnreadableAnswer,
    parse_answer,
    requirement_dicts,
)
from tuning_lib.passages import (
    MAX_PROMPT_TOKENS_EST,
    PassagesError,
    StoredPrompt,
    estimate_prompt_tokens,
    read_study2b_context,
    read_study2b_prompts,
    split_context,
    verify_stored_prompt,
)
from tuning_lib.pairstats import paired_summary
from tuning_lib.prompts import (
    BASELINE,
    TEMPLATE_FILES,
    VERSIONS_ROOT,
    GeneralityError,
    PromptBuilder,
    PromptError,
    assert_general,
    check_generality,
    load_version,
)
from tuning_lib.paths import (
    ANALYSIS_S2B,
    FALLBACK_CONTEXT,
    MODELS,
    PROJECTS,
    RTC,
    SCRIPTS,
    SECREQGEN,
    TUNING_ROOT,
    model_slug,
)
from tuning_lib.records import (
    append_record,
    is_final,
    key_states,
    new_record,
    next_attempt,
    read_jsonl,
    validate_manifest,
)

LIMIT_POINTS: dict[str, float] = {"B2": 8.0, "A2": 5.0}
ANCHOR_ARMS: tuple[str, ...] = ("A2", "B2")
RUN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FORBIDDEN_RUN_NAMES = ("study2", "study2b")
LOCAL_WINDOW_TOKENS = 16384
MAX_ANSWER_TOKENS = 4096
FAILED_IN_A_ROW_LIMIT = 4
SMOKE_JOB_LIMIT = 4
PROMPT_CHECK_TEXT = "stored prompt, hashes and context verified"
SCHEMA_VERSION = 1
HEADERS_SENT = ["Authorization", "Content-Type", "X-OpenRouter-Metadata", "X-OpenRouter-Cache"]
RETRY_RULE = "same prompt again, 3 attempts at most, waits 1 s and 2 s"
PARSER_TEXT = "whole text, outer object, code fence"


class SelectionError(ValueError):
    """The selection (stories, projects, models, arms) cannot be turned into jobs."""


def utc_now() -> str:
    """Current UTC time as YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    """SHA-256 of a text, as hex."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Selection, jobs and configurations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    """What to run: projects, models, arms, an optional story filter, repeats."""

    projects: tuple[str, ...]
    models: tuple[str, ...]
    arms: tuple[str, ...]
    stories: frozenset[str] | None
    repeats: int


@dataclass(frozen=True)
class Job:
    """One generation: a stored prompt, a model and a repeat number."""

    config_id: str
    step: str
    project: str
    model: str
    arm: str
    story_id: str
    repeat: int
    system: str
    user_sent: str
    user_non_doc: str
    doc_context_ref: str | None
    doc_context_sha256: str | None
    stored: StoredPrompt

    @property
    def key(self) -> tuple[str, str, str, str, int]:
        """Key of the job: (config_id, model, story_id, arm, repeat)."""
        return (self.config_id, self.model, self.story_id, self.arm, self.repeat)


@dataclass(frozen=True)
class LabJob(Job):
    """A job of the prompt lab: one prompt version on one story, model and repeat."""

    version: str
    version_parent: str | None
    template_sha256: tuple[tuple[str, str], ...]
    passage_changes: tuple[str, ...]
    passage_block_sha256: str
    n_doc_passages: int
    n_code_hits: int


class Configuration(Protocol):
    """A configuration turns a selection into prompt jobs."""

    config_id: str
    step: str

    def jobs(self, sel: Selection) -> list[Job]: ...


class OldConfigurationAnchor:
    """The anchor: stored prompts of Study 2b, run 1, sent unchanged."""

    config_id = "old"
    step = "anchor"

    def jobs(self, sel: Selection) -> list[Job]:
        """Build, verify and order the jobs of a selection."""
        for arm in sel.arms:
            if arm not in ANCHOR_ARMS:
                raise SelectionError(f"the anchor knows only the arms {ANCHOR_ARMS}, not {arm!r}")
        stored = read_study2b_prompts(
            projects=sel.projects, models=sel.models, arms=sel.arms, run=1
        )
        if sel.stories is not None:
            known = {sp.story_id for sp in stored}
            unknown = sorted(sel.stories - known)
            if unknown:
                raise SelectionError(f"unknown story id(s) for this selection: {', '.join(unknown)}")
            stored = [sp for sp in stored if sp.story_id in sel.stories]
        if not stored:
            raise SelectionError("the selection holds no stored prompt")
        verified: list[StoredPrompt] = []
        for sp in stored:
            if sp.user_sent is None:
                raise SelectionError(f"stored prompt without a sent user prompt: {sp.story_id} {sp.arm}")
            context = None
            if sp.doc_context_ref == "frozen":
                context = read_study2b_context(sp.project, sp.model, sp.story_id)
            verify_stored_prompt(sp, context)
            verified.append(sp)
        model_order = {m: i for i, m in enumerate(MODELS)}
        jobs: list[Job] = []
        for repeat in range(1, sel.repeats + 1):
            for sp in verified:
                assert sp.user_sent is not None
                jobs.append(
                    Job(
                        config_id=self.config_id,
                        step=self.step,
                        project=sp.project,
                        model=sp.model,
                        arm=sp.arm,
                        story_id=sp.story_id,
                        repeat=repeat,
                        system=sp.system,
                        user_sent=sp.user_sent,
                        user_non_doc=sp.user_non_doc,
                        doc_context_ref=sp.doc_context_ref,
                        doc_context_sha256=sp.doc_context_sha256,
                        stored=sp,
                    )
                )
        jobs.sort(
            key=lambda j: (j.repeat, model_order.get(j.model, len(model_order)), j.arm, j.project, j.story_id)
        )
        keys = [j.key for j in jobs]
        if len(set(keys)) != len(keys):
            raise SelectionError("two jobs have the same key")
        return jobs


# --------------------------------------------------------------------------
# One generation
# --------------------------------------------------------------------------


@dataclass
class JobOutcome:
    """Result of one job: the records of every attempt, the cost and the state."""

    records: list[dict]
    cost_usd: float
    ok: bool
    fatal: HostedError | None
    transport_failed: bool


def _cut(text: str, n: int = 500) -> str:
    return text if len(text) <= n else text[:n]


def _record_base(job: Job, pin: ModelPin, run_name: str, attempt: int, max_attempts: int,
                 ts: str, request_sha: str) -> dict[str, Any]:
    sp = job.stored
    base = {
        "schema_version": SCHEMA_VERSION,
        "study": "tuning",
        "step": job.step,
        "run_name": run_name,
        "config_id": job.config_id,
        "project": job.project,
        "model": job.model,
        "hosted_model": pin.model_id,
        "story_id": job.story_id,
        "arm": job.arm,
        "run": job.repeat,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "ts": ts,
        "source_study": "study2b",
        "source_run": 1,
        "source_file": sp.source_file,
        "source_line": sp.source_line,
        "arm_description": f"stored prompt of Study 2b arm {job.arm}, run 1",
        "prompt_system": job.system,
        "prompt_user_sent": job.user_sent,
        "prompt_user_non_doc": job.user_non_doc,
        "doc_context_ref": job.doc_context_ref,
        "doc_context_sha256": job.doc_context_sha256,
        "prompt_system_sha256": sha256_text(job.system),
        "prompt_user_sha256": sha256_text(job.user_sent),
        "prompt_est_tokens": estimate_prompt_tokens(job.system, job.user_sent),
        "prompt_check": PROMPT_CHECK_TEXT,
        "n_calls_sent": 1,
        "request_sha256": request_sha,
        "provider_tag": pin.provider_tag,
    }
    if isinstance(job, LabJob):
        base.update(_lab_record_fields(job))
    return base


def _lab_record_fields(job: "LabJob") -> dict[str, Any]:
    """Values that a lab record takes instead of the anchor values.

    The record schema is closed (records.py), so the lab facts (parent, passage changes,
    template hashes, passage counts) are stored as JSON text in arm_description.
    """
    facts = {
        "version": job.version,
        "version_parent": job.version_parent,
        "passage_changes": list(job.passage_changes),
        "template_sha256": dict(job.template_sha256),
        "passage_block_sha256": job.passage_block_sha256,
        "n_doc_passages": job.n_doc_passages,
        "n_code_hits": job.n_code_hits,
    }
    check = (
        "built from the template folder; equal to the stored prompt of Study 2b arm B2 (checked)"
        if job.version == BASELINE
        else "built from the template folder and the stored passages"
    )
    return {
        "arm_description": (
            f"prompt version {job.version}, RAG prompt with the stored Study 2b passages; lab facts: "
            + json.dumps(facts, sort_keys=True)
        ),
        "prompt_check": check,
    }


_EMPTY_REPLY_FIELDS: dict[str, Any] = {
    "provider": None,
    "provider_source": None,
    "generation_id": None,
    "response_model": None,
    "finish_reason": None,
    "transport_retries": 0,
    "latency_ms": None,
    "prompt_tokens": None,
    "completion_tokens": None,
    "reasoning_tokens": None,
    "reasoning_tokens_source": None,
    "cached_tokens": None,
    "cost_usd": None,
    "llm_calls": [],
    "raw_reply": None,
}

_EMPTY_PARSE_FIELDS: dict[str, Any] = {
    "parse_method": None,
    "parsed_by_fallback": None,
    "old_parser_result": None,
    "requirements": [],
    "n_requirements": 0,
    "n_requirements_raw": 0,
    "n_requirements_failed_validation": 0,
    "validation_failures": [],
    "n_threat_mappings": 0,
    "n_threat_mappings_failed": 0,
    "asvs_defaulted": 0,
    "error": None,
    "error_kind": None,
}


_RETRIES_RE = re.compile(r"after (\d+) retries")


def _retries_in_message(message: str) -> int:
    """Transport retries that the client made before it gave up (it writes them in its message)."""
    m = _RETRIES_RE.search(message)
    return int(m.group(1)) if m else 0


def _reply_fields(reply: Any) -> dict[str, Any]:
    return {
        "provider": reply.provider,
        "provider_source": reply.provider_source,
        "generation_id": reply.generation_id,
        "response_model": reply.response_model,
        "finish_reason": reply.finish_reason,
        "transport_retries": reply.transport_retries,
        "latency_ms": reply.latency_ms,
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": reply.completion_tokens,
        "reasoning_tokens": reply.reasoning_tokens,
        "reasoning_tokens_source": reply.reasoning_tokens_source,
        "cached_tokens": reply.cached_tokens,
        "cost_usd": reply.cost_usd,
        "llm_calls": [
            {
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
                "latency_ms": reply.latency_ms,
            }
        ],
        "raw_reply": reply.text,
    }


def run_job(
    job: Job,
    client: OpenRouterClient,
    pin: ModelPin,
    *,
    run_name: str,
    start_attempt: int = 1,
    max_attempts: int = 3,
    retry_delay_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], str] = utc_now,
) -> JobOutcome:
    """Run one generation with the old retry rule and record every attempt.

    The function writes no file. The main thread writes the returned records.
    """
    body = build_request_body(pin, job.system, job.user_sent)
    request_sha = sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True))
    outcome = JobOutcome(records=[], cost_usd=0.0, ok=False, fatal=None, transport_failed=False)
    attempt = start_attempt
    while True:
        fields = _record_base(job, pin, run_name, attempt, max_attempts, now(), request_sha)
        fields.update(_EMPTY_REPLY_FIELDS)
        fields.update(_EMPTY_PARSE_FIELDS)
        try:
            reply = client.generate_reply(job.user_sent, job.system)
        except TransientHostedError as exc:
            fields.update(error=f"{type(exc).__name__}: {_cut(str(exc))}", error_kind="transport",
                          transport_retries=_retries_in_message(str(exc)))
            outcome.records.append(new_record(**fields))
            outcome.transport_failed = True
            return outcome
        except (FatalHostedError, ContractViolation) as exc:
            partial = getattr(exc, "reply", None)
            if partial is not None:
                fields.update(_reply_fields(partial))
                outcome.cost_usd += float(partial.cost_usd)
            kind = "contract" if isinstance(exc, ContractViolation) else "fatal"
            fields.update(error=f"{type(exc).__name__}: {_cut(str(exc))}", error_kind=kind)
            outcome.records.append(new_record(**fields))
            outcome.fatal = exc
            return outcome
        fields.update(_reply_fields(reply))
        outcome.cost_usd += float(reply.cost_usd)
        try:
            parsed = parse_answer(reply.text, job.story_id)
        except (UnreadableAnswer, NoRequirementsList) as exc:
            kind = "no_requirements_list" if isinstance(exc, NoRequirementsList) else "unreadable_json"
            fields.update(error=f"{type(exc).__name__}: {_cut(str(exc))}", error_kind=kind)
            outcome.records.append(new_record(**fields))
            if attempt < max_attempts:
                sleep(retry_delay_s * 2 ** (attempt - start_attempt))
                attempt += 1
                continue
            return outcome
        fields.update(
            parse_method=parsed.method,
            parsed_by_fallback=parsed.parsed_by_fallback,
            old_parser_result=parsed.old_parser_result,
            requirements=requirement_dicts(parsed.requirements),
            n_requirements=len(parsed.requirements),
            n_requirements_raw=parsed.n_requirements_raw,
            n_requirements_failed_validation=parsed.n_requirements_failed_validation,
            validation_failures=[
                {"index": f.index, "error": f.error} for f in parsed.validation_failures
            ],
            n_threat_mappings=len(parsed.threat_mappings),
            n_threat_mappings_failed=parsed.n_threat_mappings_failed,
            asvs_defaulted=parsed.asvs_defaulted,
            error=None,
            error_kind=None,
        )
        outcome.records.append(new_record(**fields))
        outcome.ok = True
        return outcome


# --------------------------------------------------------------------------
# Executing a run
# --------------------------------------------------------------------------


class CostMeter:
    """Thread-safe sum of costs in USD."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0.0

    def add(self, usd: float) -> float:
        """Add a cost and return the new total."""
        with self._lock:
            self._total += usd
            return self._total

    @property
    def total(self) -> float:
        """Total so far."""
        with self._lock:
            return self._total


@dataclass
class ExecSummary:
    """What an execution did. exit_code is 0 (done) or 3 (stopped, can be resumed)."""

    exit_code: int
    stop_reason: str | None
    cost_usd: float
    planned: int
    skipped_final: int
    submitted: int
    final_ok: int
    final_failed: int
    not_final: int
    records_written: int


def cell_records_path(run_dir: Path, project: str, model: str) -> Path:
    """Path of the records file of one cell (project and model)."""
    return run_dir / project / model_slug(model) / "records.jsonl"


def _read_cells(run_dir: Path, jobs: Sequence[Job]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for job in jobs:
        cell = (job.project, job.model)
        if cell in seen:
            continue
        seen.add(cell)
        out.extend(read_jsonl(cell_records_path(run_dir, *cell)))
    return out


def execute_detailed(
    jobs: Sequence[Job],
    run_dir: Path,
    *,
    workers: int,
    max_cost_usd: float,
    max_minutes: float,
    max_attempts: int,
    client_factory: Callable[[ModelPin], OpenRouterClient],
    guard: WriteGuard = DEFAULT_GUARD,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_start: Callable[[], None] | None = None,
    on_finish: Callable[[ExecSummary], None] | None = None,
) -> ExecSummary:
    """Run the jobs with a thread pool and return a summary.

    New jobs are submitted only while the cost, the time, the error rules allow it.
    Jobs in flight always finish and their records are written. on_finish runs while the
    run lock is still held (the final manifest and the report are written there); it may
    change exit_code and stop_reason of the summary.
    """
    guard.check(run_dir)
    with RunLock(run_dir, guard=guard):
        if on_start is not None:
            on_start()
        summary = _execute_locked(
            jobs, run_dir, workers=workers, max_cost_usd=max_cost_usd, max_minutes=max_minutes,
            max_attempts=max_attempts, client_factory=client_factory, guard=guard, sleep=sleep,
            clock=clock,
        )
        if on_finish is not None:
            on_finish(summary)
        return summary


def execute(
    jobs: Sequence[Job],
    run_dir: Path,
    *,
    workers: int,
    max_cost_usd: float,
    max_minutes: float,
    max_attempts: int,
    client_factory: Callable[[ModelPin], OpenRouterClient],
    guard: WriteGuard = DEFAULT_GUARD,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the jobs and return the exit code (0 done, 3 stopped early)."""
    return execute_detailed(
        jobs, run_dir, workers=workers, max_cost_usd=max_cost_usd, max_minutes=max_minutes,
        max_attempts=max_attempts, client_factory=client_factory, guard=guard, sleep=sleep,
        clock=clock,
    ).exit_code


def _execute_locked(
    jobs: Sequence[Job],
    run_dir: Path,
    *,
    workers: int,
    max_cost_usd: float,
    max_minutes: float,
    max_attempts: int,
    client_factory: Callable[[ModelPin], OpenRouterClient],
    guard: WriteGuard,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> ExecSummary:
    stored = _read_cells(run_dir, jobs)
    states = key_states(stored)
    todo: list[tuple[Job, int]] = []
    skipped = 0
    for job in jobs:
        state = states.get(job.key)
        if is_final(state, max_attempts):
            skipped += 1
            continue
        todo.append((job, next_attempt(state)))

    meter = CostMeter()
    # The cap is for the whole run: count what earlier sessions already spent.
    spent_before = sum(float(r.get("cost_usd") or 0.0) for r in stored)
    meter.add(spent_before)
    if spent_before > 0.0:
        print(f"cost so far in this run folder: {spent_before:.4f} USD (cap {max_cost_usd:.2f} USD)")
    tls = threading.local()
    created: list[OpenRouterClient] = []
    created_lock = threading.Lock()

    def worker(job: Job, start_attempt: int) -> JobOutcome:
        clients = getattr(tls, "clients", None)
        if clients is None:
            clients = {}
            tls.clients = clients
        pin = HOSTED_MODELS.get(job.model)
        if pin is None:
            raise GuardError(f"no hosted pin for the model {job.model!r}")
        client = clients.get(job.model)
        if client is None:
            client = client_factory(pin)
            clients[job.model] = client
            with created_lock:
                created.append(client)
        return run_job(
            job, client, pin, run_name=run_dir.name, start_attempt=start_attempt,
            max_attempts=max_attempts, sleep=sleep,
        )

    pending: deque[tuple[Job, int]] = deque(todo)
    in_flight: dict[Future, Job] = {}
    stop_reason: str | None = None
    failed_in_a_row = 0
    records_written = 0
    t0 = clock()

    def stop_check() -> str | None:
        if meter.total >= max_cost_usd:
            return f"cost cap reached ({meter.total:.4f} USD of {max_cost_usd:.2f} USD)"
        if (clock() - t0) / 60.0 >= max_minutes:
            return f"time cap reached ({max_minutes:g} minutes)"
        return None

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while pending or in_flight:
                while pending and len(in_flight) < workers and stop_reason is None:
                    reason = stop_check()
                    if reason is not None:
                        stop_reason = reason
                        break
                    job, start = pending.popleft()
                    in_flight[pool.submit(worker, job, start)] = job
                if not in_flight:
                    break
                done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                for fut in done:
                    job = in_flight.pop(fut)
                    exc = fut.exception()
                    if exc is not None:
                        if stop_reason is None:
                            stop_reason = f"internal error in a worker: {type(exc).__name__}: {_cut(str(exc))}"
                        failed_in_a_row += 1
                        continue
                    outcome = fut.result()
                    meter.add(outcome.cost_usd)
                    cell = cell_records_path(run_dir, job.project, job.model)
                    make_run_folder(cell.parent, guard=guard)
                    for rec in outcome.records:
                        append_record(cell, rec, guard=guard)
                        records_written += 1
                    if outcome.fatal is not None and stop_reason is None:
                        stop_reason = f"fatal error: {_cut(str(outcome.fatal))}"
                    if outcome.ok:
                        failed_in_a_row = 0
                    else:
                        failed_in_a_row += 1
                        if failed_in_a_row >= FAILED_IN_A_ROW_LIMIT and stop_reason is None:
                            stop_reason = f"{FAILED_IN_A_ROW_LIMIT} failed generations in a row"
    finally:
        for client in created:
            client.close()

    if pending and stop_reason is None:
        stop_reason = "the run ended with jobs that were not started"
    after = key_states(_read_cells(run_dir, jobs))
    final_ok = final_failed = not_final = 0
    for job in jobs:
        st = after.get(job.key)
        if st is not None and st.ok:
            final_ok += 1
        elif is_final(st, max_attempts):
            final_failed += 1
        else:
            not_final += 1
    if stop_reason is None and not_final > 0:
        stop_reason = f"{not_final} generation(s) are not final (transport failures); use --resume"
    return ExecSummary(
        exit_code=3 if stop_reason is not None else 0,
        stop_reason=stop_reason,
        cost_usd=meter.total,
        planned=len(jobs),
        skipped_final=skipped,
        submitted=len(todo) - len(pending),
        final_ok=final_ok,
        final_failed=final_failed,
        not_final=not_final,
        records_written=records_written,
    )


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


@dataclass
class Check:
    """One preflight check. data may carry a result for the manifest."""

    name: str
    status: str  # PASS, FAIL, WARN, SKIP
    detail: str = ""
    data: Any = field(default=None, repr=False)


def estimate_cost(jobs: Sequence[Job]) -> tuple[int, int, float]:
    """Estimate (input tokens, output tokens, USD) from the stored local token counts."""
    tin = tout = 0
    usd = 0.0
    for job in jobs:
        pin = HOSTED_MODELS.get(job.model)
        a, b = job.stored.local_prompt_tokens, job.stored.local_completion_tokens
        if pin is None or a is None or b is None:
            raise GuardError(f"cannot estimate the cost of {job.model} {job.story_id} {job.arm}: pin or local token counts missing")
        tin += a
        tout += b
        usd += a * pin.price_in_per_mtok / 1e6 + b * pin.price_out_per_mtok / 1e6
    return tin, tout, usd


def _request_template() -> dict[str, Any]:
    return {
        "temperature": SAMPLING_COMMON["temperature"],
        "max_completion_tokens": SAMPLING_COMMON["max_completion_tokens"],
        "reasoning": {"effort": "none"},
        "provider_fields": ["order", "only", "allow_fallbacks", "require_parameters", "data_collection"],
        "response_format": None,
        "seed": None,
    }


def _configuration_block() -> dict[str, Any]:
    return {
        "config_id": "old",
        "source": "study2b stored prompts, run 1",
        "retry_rule": RETRY_RULE,
        "parser": PARSER_TEXT,
        "request_template": _request_template(),
    }


def _pin_block(pin: ModelPin) -> dict[str, Any]:
    return {
        "model_id": pin.model_id,
        "provider_tag": pin.provider_tag,
        "expected_provider_name": pin.expected_provider_name,
        "top_p": pin.top_p,
        "top_k": pin.top_k,
        "extra_sampling": dict(pin.extra_sampling),
        "pin_confirmed": pin.confirmed,
    }


_PIN_COMPARE = ("model_id", "provider_tag", "expected_provider_name", "top_p", "top_k", "extra_sampling")


def _resume_mismatches(manifest: Mapping[str, Any], sel: Selection, max_attempts: int = 3) -> list[str]:
    """Differences between a stored manifest and the wanted configuration."""
    bad: list[str] = []
    opts = manifest.get("options", {})
    if opts.get("repeats") != sel.repeats:
        bad.append("repeats")
    if list(opts.get("arms", [])) != list(sel.arms):
        bad.append("arms")
    if list(opts.get("models", [])) != list(sel.models):
        bad.append("models")
    if list(opts.get("projects", [])) != list(sel.projects):
        bad.append("projects")
    old_stories = sorted(opts["stories"]) if opts.get("stories") else None
    if old_stories != (sorted(sel.stories) if sel.stories else None):
        bad.append("stories")
    if opts.get("max_attempts") != max_attempts:
        bad.append("max_attempts")
    if manifest.get("configuration") != _configuration_block():
        bad.append("configuration")
    for model in sel.models:
        pin = HOSTED_MODELS.get(model)
        old = manifest.get("models", {}).get(model)
        if pin is None or old is None:
            bad.append(f"pin of {model}")
            continue
        now = _pin_block(pin)
        if any(old.get(k) != now[k] for k in _PIN_COMPARE):
            bad.append(f"pin of {model}")
    return bad


def _hash_summary(result: Any, ts: str) -> dict[str, Any]:
    return {
        "ok": bool(result.ok),
        "n_listed": result.n_listed,
        "n_hashed": result.n_hashed,
        "n_existence_only": result.n_existence_only,
        "changed": list(result.changed),
        "missing": list(result.missing),
        "checked_utc": ts,
    }


def _check_step(name: str, fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except Exception as exc:  # shown as a FAIL line, never hidden
        return Check(name, "FAIL", f"{type(exc).__name__}: {_cut(str(exc), 400)}")


def preflight(
    sel: Selection,
    jobs: Sequence[Job],
    run_dir: Path,
    *,
    dry_run: bool,
    resume: bool,
    max_cost_usd: float,
    skip_hash_lists: bool,
    guard: WriteGuard = DEFAULT_GUARD,
    max_attempts: int = 3,
) -> list[Check]:
    """Run every check before any request. Nothing is written or created."""
    checks: list[Check] = []

    def c_folder() -> Check:
        guard.check(run_dir)
        if not RUN_NAME_RE.match(run_dir.name) or run_dir.name.casefold() in FORBIDDEN_RUN_NAMES:
            return Check("run folder", "FAIL", "the run name is not allowed")
        exists = run_dir.exists()
        if exists and not resume:
            return Check("run folder", "FAIL", "the run folder exists; use --resume or another --run-name")
        if resume and not exists:
            return Check("run folder", "FAIL", "--resume needs an existing run folder")
        if resume:
            mpath = run_dir / "manifest.json"
            if not mpath.is_file():
                return Check("run folder", "FAIL", "--resume needs manifest.json in the run folder")
            bad = _resume_mismatches(json.loads(mpath.read_text(encoding="utf-8")), sel, max_attempts)
            if bad:
                return Check("run folder", "FAIL", "the configuration differs from the manifest: " + ", ".join(bad))
        return Check("run folder", "PASS", "allowed by the guard")

    checks.append(_check_step("run folder", c_folder))

    def c_hash() -> Check:
        if skip_hash_lists:
            return Check("hash lists (before)", "SKIP", "skipped by --skip-hash-lists (dry run only)")
        results = check_all_hash_lists()
        bad = [n for n, r in results.items() if not r.ok]
        detail = ", ".join(f"{n}: {r.n_listed} listed" for n, r in results.items())
        if bad:
            return Check("hash lists (before)", "FAIL", "not ok: " + ", ".join(bad), data=results)
        return Check("hash lists (before)", "PASS", detail, data=results)

    checks.append(_check_step("hash lists (before)", c_hash))

    def c_procs() -> Check:
        assert_no_other_experiment(list_processes())
        if (run_dir / LOCK_NAME).exists():
            return Check("other processes and lock", "FAIL", "a lock file exists in the run folder")
        return Check("other processes and lock", "PASS", "no other experiment process, no lock file")

    checks.append(_check_step("other processes and lock", c_procs))

    contexts_done: dict[tuple[str, str, str], int] = {}

    def c_prompts() -> Check:
        for job in jobs:
            if job.doc_context_ref != "frozen":
                continue
            ck = (job.project, job.model, job.story_id)
            if ck in contexts_done:
                continue
            ctx = read_study2b_context(*ck)
            if ctx.text == FALLBACK_CONTEXT:
                raise PassagesError(f"the context of {job.story_id} is the fallback text")
            passages = split_context(ctx.text)
            if not any(p.kind == "doc" for p in passages):
                raise PassagesError(f"the context of {job.story_id} has no documentation passage")
            contexts_done[ck] = len(passages)
        return Check("stored prompts", "PASS", f"{len(jobs)} jobs, {len(contexts_done)} contexts split")

    checks.append(_check_step("stored prompts", c_prompts))

    def c_bodies() -> Check:
        n = 0
        for job in jobs:
            pin = HOSTED_MODELS.get(job.model)
            if pin is None:
                raise GuardError(f"no hosted pin for {job.model}")
            body = build_request_body(pin, job.system, job.user_sent)
            # A model with no reasoning mode sends no reasoning field (its endpoint refuses the parameter).
            # The client still stops when an answer holds reasoning.
            if body.get("reasoning") != ({"effort": "none"} if pin.reasoning_off else None):
                raise GuardError("reasoning is not switched off in the request")
            prov = body.get("provider", {})
            if len(prov.get("only", [])) != 1 or prov.get("allow_fallbacks") is not False:
                raise GuardError("the provider is not pinned to one endpoint")
            n += 1
        return Check("request bodies", "PASS", f"{n} bodies built, privacy guard passed")

    checks.append(_check_step("request bodies", c_bodies))

    def c_size() -> Check:
        longest = 0
        warn32 = 0
        for job in jobs:
            est = estimate_prompt_tokens(job.system, job.user_sent, method="bytes4")
            longest = max(longest, est)
            if est > MAX_PROMPT_TOKENS_EST:
                raise GuardError(f"prompt of {job.story_id} {job.arm} has about {est} tokens (limit {MAX_PROMPT_TOKENS_EST})")
            local = job.stored.local_prompt_tokens
            if local is None:
                raise GuardError(f"no local prompt token count for {job.story_id} {job.arm}")
            if local + MAX_ANSWER_TOKENS > LOCAL_WINDOW_TOKENS:
                raise GuardError(f"local tokens of {job.story_id} {job.arm} are above the local window")
            if estimate_prompt_tokens(job.system, job.user_sent, method="bytes32") > MAX_PROMPT_TOKENS_EST:
                warn32 += 1
        if warn32:
            return Check("prompt size", "WARN", f"longest {longest} tokens (bytes4); {warn32} prompt(s) above the limit by the cautious estimate")
        return Check("prompt size", "PASS", f"longest {longest} tokens (bytes4)")

    checks.append(_check_step("prompt size", c_size))

    def c_pins() -> None:
        for model in sel.models:
            pin = HOSTED_MODELS.get(model)
            if pin is None:
                checks.append(Check(f"pin {model}", "FAIL", "no hosted pin for this model"))
            elif not pin.confirmed:
                checks.append(Check(f"pin {model}", "WARN", f"{pin.provider_tag} is not confirmed by a first call"))
            else:
                checks.append(Check(f"pin {model}", "PASS", f"{pin.provider_tag} confirmed"))
        unconfirmed = [m for m in sel.models if m in HOSTED_MODELS and not HOSTED_MODELS[m].confirmed]
        if unconfirmed and len(jobs) > SMOKE_JOB_LIMIT:
            checks.append(Check(
                "pins confirmed", "FAIL",
                f"{len(jobs)} generations with unconfirmed pins. Run a smoke test first: "
                "anchor --run-name smoke1 --stories BW-S2-001 --repeats 1 (4 generations), "
                "read the provider names in the records, then set confirmed=True."))

    c_pins()

    def c_cost() -> Check:
        tin, tout, usd = estimate_cost(jobs)
        if usd > max_cost_usd:
            return Check("cost estimate", "FAIL", f"{usd:.4f} USD is above the cap {max_cost_usd:.2f} USD")
        return Check("cost estimate", "PASS", f"about {usd:.4f} USD ({tin} input, {tout} output tokens; estimate only)")

    checks.append(_check_step("cost estimate", c_cost))

    def c_ref() -> Check:
        ref = measures.load_local_reference()
        for model in MODELS:
            for arm in ANCHOR_ARMS:
                if (model, arm) not in ref:
                    raise GuardError(f"local reference lacks {model} {arm}")
        return Check("local reference", "PASS", f"{len(ref)} values in analysis_s2b.json")

    checks.append(_check_step("local reference", c_ref))
    return checks


def print_checks(checks: Sequence[Check]) -> None:
    """Print one line for each check."""
    for c in checks:
        print(f"[{c.status}] {c.name}: {c.detail}" if c.detail else f"[{c.status}] {c.name}")


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def git_info() -> dict[str, Any]:
    """Commit and dirty flag of the code repository, read only. Errors are reported."""
    base = ["git", "--no-optional-locks", "-C", str(SECREQGEN)]
    try:
        head = subprocess.run(base + ["rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        stat = subprocess.run(base + ["status", "--porcelain", "--untracked-files=no"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"commit": None, "dirty": None, "error": f"git failed: {type(exc).__name__}"}
    if head.returncode != 0 or stat.returncode != 0:
        return {"commit": None, "dirty": None,
                "error": f"git returned codes {head.returncode} and {stat.returncode}"}
    return {"commit": head.stdout.strip(), "dirty": bool(stat.stdout.strip()), "error": None}


def _rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def code_hashes() -> dict[str, str]:
    """SHA-256 of the runner, the tuning_lib modules and the generation modules."""
    files = [SECREQGEN / "scripts" / "run_tuning.py"]
    files += sorted((SECREQGEN / "scripts" / "tuning_lib").glob("*.py"))
    files += sorted((SECREQGEN / "src" / "secreqgen" / "generation").glob("*.py"))
    return {_rel(p, SECREQGEN): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def input_hashes(jobs: Sequence[Job]) -> dict[str, Any]:
    """SHA-256 of the Study 2b records files that the jobs read, and of analysis_s2b.json."""
    cells = sorted({(j.project, j.model) for j in jobs})
    recs = {}
    for project, model in cells:
        p = RTC / "study2b" / project / model_slug(model) / "records.jsonl"
        recs[_rel(p, RTC)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {
        "study2b_records_sha256": recs,
        "analysis_s2b_sha256": hashlib.sha256(ANALYSIS_S2B.read_bytes()).hexdigest(),
    }


def build_manifest(
    *, run_name: str, state: str, stop_reason: str | None, started_utc: str, ended_utc: str | None,
    argv: Sequence[str], args: argparse.Namespace, sel: Selection, jobs: Sequence[Job],
    estimate_usd: float, hash_before: Mapping[str, Any], hash_after: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]], summary: ExecSummary | None, notes: Sequence[str],
    git: Mapping[str, Any], code: Mapping[str, str], inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the manifest dict (section 8.2 of the design)."""
    per_model_arm: dict[str, int] = {}
    for j in jobs:
        k = f"{j.model}/{j.arm}"
        per_model_arm[k] = per_model_arm.get(k, 0) + 1
    models: dict[str, Any] = {}
    for model in sel.models:
        pin = HOSTED_MODELS[model]
        mine = [r for r in records if r.get("model") == model]
        block = _pin_block(pin)
        block["providers_seen"] = sorted({r["provider"] for r in mine if r.get("provider")})
        block["response_models_seen"] = sorted({r["response_model"] for r in mine if r.get("response_model")})
        models[model] = block
    totals = measures.run_totals(records) if records else None
    return {
        "schema_version": SCHEMA_VERSION,
        "study": "tuning",
        "step": "anchor",
        "run_name": run_name,
        "state": state,
        "stop_reason": stop_reason,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "command_line": list(argv),
        "options": {
            "repeats": sel.repeats, "workers": args.workers, "max_cost_usd": args.max_cost_usd,
            "max_minutes": args.max_minutes, "max_attempts": args.max_attempts,
            "stories": sorted(sel.stories) if sel.stories is not None else None,
            "projects": list(sel.projects), "models": list(sel.models), "arms": list(sel.arms),
        },
        "configuration": _configuration_block(),
        "models": models,
        "jobs": {
            "planned": len(jobs),
            "final_ok": summary.final_ok if summary else 0,
            "final_failed": summary.final_failed if summary else 0,
            "not_final": summary.not_final if summary else len(jobs),
            "records_written": len(records),
            "per_model_arm": per_model_arm,
        },
        "cost": {
            "total_usd": totals.cost_usd if totals else 0.0,
            "cap_usd": args.max_cost_usd,
            "estimate_usd": estimate_usd,
            "max_overshoot_note": "up to one generation per worker",
        },
        "tokens": {
            "prompt": totals.prompt_tokens if totals else 0,
            "completion": totals.completion_tokens if totals else 0,
            "reasoning": totals.reasoning_tokens if totals else 0,
        },
        "versions": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "httpx": importlib.metadata.version("httpx"),
            "pydantic": importlib.metadata.version("pydantic"),
        },
        "code": {"git": dict(git), "file_sha256": dict(code)},
        "inputs": dict(inputs),
        "hash_lists": {"before": dict(hash_before), "after": dict(hash_after)},
        "client": {"headers_sent": list(HEADERS_SENT), "timeout_s": 300.0, "max_transport_retries": 3},
        "notes": list(notes),
    }


def write_manifest(run_dir: Path, manifest: Mapping[str, Any], guard: WriteGuard) -> None:
    """Validate the manifest, check it for private text and write it through the guard."""
    validate_manifest(manifest)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    assert_no_private_text(text, what="manifest")
    safe_write(run_dir / "manifest.json", text, guard=guard)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _fmean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _records_of_cells_with_value(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Keep the records of the cells (config, model, arm) that have at least one good final record.

    measures.arm_rates raises for a cell with no good record. A report must not stop for that:
    such a cell becomes a row with no value and a count of failures.
    """
    good = {
        (r.get("config_id"), r["model"], r["arm"])
        for r in measures.latest_records(records).values() if r.get("error") is None
    }
    return [r for r in records if (r.get("config_id"), r["model"], r["arm"]) in good]


def _totals_dict(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Run totals with the parse retries counted without the transport failures.

    measures.run_totals counts every key with more than one line as a parse retry. A
    transport failure that a later --resume repeats also makes a second line, but it is no
    parse retry. Here a parse retry is a key with more than one non-transport line. The
    transport failures and the retries that the client made are counted on their own.
    """
    totals = dataclasses.asdict(measures.run_totals(records))
    lines: dict[tuple, int] = {}
    n_transport_lines = 0
    n_transport_retries = 0
    for rec in records:
        n_transport_retries += int(rec.get("transport_retries") or 0)
        if rec.get("error_kind") == "transport":
            n_transport_lines += 1
            continue
        key = (rec.get("config_id"), rec["model"], rec["story_id"], rec["arm"], rec["run"])
        lines[key] = lines.get(key, 0) + 1
    totals["n_parse_retries"] = sum(1 for c in lines.values() if c > 1)
    totals["n_transport_failed_lines"] = n_transport_lines
    totals["n_transport_retries"] = n_transport_retries
    return totals


def build_report(
    run_dir: Path,
    *,
    reference: Mapping[tuple[str, str], float] | None = None,
    fact_sheets: Mapping[str, Sequence[str]] | None = None,
    local_prompt_tokens: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Score all records of a run folder and compare them with the local values."""
    manifest: dict[str, Any] | None = None
    mpath = run_dir / "manifest.json"
    if mpath.is_file():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    records: list[dict] = []
    for path in sorted(run_dir.glob("*/*/records.jsonl")):
        records.extend(read_jsonl(path))
    if not records:
        raise GuardError("the run folder holds no records")
    opts = manifest["options"] if manifest else {}
    projects = tuple(opts.get("projects") or ()) or None
    if fact_sheets is None:
        fact_sheets = measures.load_fact_sheets(projects) if projects else measures.load_fact_sheets()
    if reference is None:
        reference = measures.load_local_reference()
    planned_stories = sorted(fact_sheets)
    if opts.get("stories"):
        planned_stories = sorted(set(opts["stories"]))
    # The local rate is the 15-story rate. A verdict is only fair for the full selection.
    full_selection = (not opts.get("stories")) and (
        set(opts.get("projects") or PROJECTS) == set(PROJECTS)
    )
    repeats = opts.get("repeats") or max(int(r["run"]) for r in records)
    models = [m for m in MODELS if m in (opts.get("models") or {r["model"] for r in records})]
    arms = [a for a in ANCHOR_ARMS if a in (opts.get("arms") or {r["arm"] for r in records})]
    config_ids = sorted({r.get("config_id") for r in records}, key=str)
    if len(config_ids) != 1:
        raise GuardError(f"records of more than one configuration: {config_ids}")
    cid = config_ids[0]
    usable = _records_of_cells_with_value(records)
    rates = measures.arm_rates(usable, fact_sheets) if usable else {}
    per_repeat = measures.repeat_rates(records, fact_sheets)
    states = key_states(records)
    rows: list[dict[str, Any]] = []
    for model in models:
        for arm in arms:
            ar = rates.get((cid, model, arm))
            local = reference[(model, arm)]
            if ar is None:
                rows.append({
                    "model": model, "arm": arm, "local_rate": local, "hosted_rate": None,
                    "difference_points": None, "limit_points": LIMIT_POINTS[arm],
                    "inside_limit": None, "repeat_rates": [], "repeat_gap_points": None,
                    "n_stories": 0, "n_failed": sum(
                        1 for r in records if r["model"] == model and r["arm"] == arm
                        and r.get("error") is not None),
                    "complete": False,
                })
                continue
            diff = 100.0 * (ar.rate - local)
            complete = all(
                (st := states.get((cid, model, s, arm, r))) is not None and st.ok
                for s in planned_stories for r in range(1, repeats + 1)
            )
            limit = LIMIT_POINTS[arm]
            rep = [per_repeat[(cid, model, arm, r)] for r in range(1, repeats + 1)
                   if (cid, model, arm, r) in per_repeat]
            rows.append({
                "model": model, "arm": arm, "local_rate": local, "hosted_rate": ar.rate,
                "difference_points": diff, "limit_points": limit,
                "inside_limit": (abs(diff) <= limit + 1e-9) if (complete and full_selection) else None,
                "repeat_rates": rep,
                "repeat_gap_points": 100.0 * (max(rep) - min(rep)) if rep else None,
                "n_stories": ar.n_stories, "n_failed": ar.n_failed, "complete": complete,
            })
    verdicts = [r["inside_limit"] for r in rows]
    if any(v is False for v in verdicts):
        all_inside: bool | None = False
    elif any(v is None for v in verdicts):
        all_inside = None
    else:
        all_inside = True
    per_model: dict[str, Any] = {}
    notes = [
        "The local rate is one local run. The hosted rate is the mean of the repeats.",
        "The limits are the Study 2b drift limits (B2 8.0 points, A2 5.0 points).",
        "A row that is not complete gets no verdict.",
    ]
    if not full_selection:
        notes.append("subset of stories: no verdict against the 15-story local rate")
    for model in models:
        ok_recs = [r for r in records if r["model"] == model and r.get("error") is None
                   and r.get("prompt_tokens") is not None]
        hp = _fmean([float(r["prompt_tokens"]) for r in ok_recs])
        lp = local_prompt_tokens.get(model) if local_prompt_tokens else None
        per_model[model] = {
            "mean_prompt_tokens_hosted": hp,
            "mean_prompt_tokens_local": lp,
            "prompt_token_ratio": (hp / lp) if (hp is not None and lp) else None,
            "mean_completion_tokens": _fmean([float(r["completion_tokens"]) for r in ok_recs]),
            "mean_n_requirements": _fmean([float(r["n_requirements"]) for r in ok_recs]),
        }
    if local_prompt_tokens is None:
        notes.append("The local prompt token means were not given, so the token ratio is empty.")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_name": run_dir.name,
        "generated_utc": utc_now(),
        "state": manifest["state"] if manifest else "no manifest",
        "rows": rows,
        "all_inside_limits": all_inside,
        "totals": _totals_dict(records),
        "per_model": per_model,
        "providers": {m: manifest["models"][m].get("providers_seen") for m in models
                      if manifest and m in manifest.get("models", {})},
        "notes": notes,
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100.0 * x:.2f}"


def render_report_md(report: Mapping[str, Any]) -> str:
    """Render the report as Markdown."""
    lines = [f"# Run {report['run_name']}", "",
             f"State: {report['state']}. Generated: {report['generated_utc']}.", ""]
    for model, prov in (report.get("providers") or {}).items():
        lines.append(f"- {model}: providers seen {prov}")
    lines += ["", "## Name use, hosted against local", "",
              "| model | arm | local rate % | hosted rate % | difference (points) | limit (points) | inside | repeat 1 % | repeat 2 % | repeat gap (points) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        reps = list(r["repeat_rates"]) + [None, None]
        inside = "no verdict" if r["inside_limit"] is None else ("yes" if r["inside_limit"] else "no")
        gap = "n/a" if r["repeat_gap_points"] is None else f"{r['repeat_gap_points']:.2f}"
        dpts = "n/a" if r["difference_points"] is None else f"{r['difference_points']:+.2f}"
        lines.append(
            f"| {r['model']} | {r['arm']} | {_pct(r['local_rate'])} | {_pct(r['hosted_rate'])} | "
            f"{dpts} | {r['limit_points']:.1f} | {inside} | "
            f"{_pct(reps[0])} | {_pct(reps[1])} | {gap} |")
    t = report["totals"]
    lines += ["", "## Counts", "", "| item | value |", "|---|---|"]
    for label, key in [
        ("records (attempt lines)", "n_records"), ("failed attempt lines", "n_attempt_lines_failed"),
        ("parse retries", "n_parse_retries"), ("parsed by fallback", "n_parsed_by_fallback"),
        ("old parser different", "n_old_parser_different"), ("old parser failed", "n_old_parser_failed"),
        ("finish_reason length", "n_finish_length"), ("reasoning tokens", "reasoning_tokens"),
        ("reasoning assumed zero", "n_reasoning_assumed_zero"),
        ("requirements failed validation", "n_requirements_failed_validation"),
        ("prompt tokens", "prompt_tokens"), ("completion tokens", "completion_tokens"),
        ("cost (USD)", "cost_usd"),
    ]:
        lines.append(f"| {label} | {t[key]} |")
    lines.append("")
    v = report["all_inside_limits"]
    lines.append({True: "All four rows are inside their limit.",
                  False: "At least one row is outside its limit.",
                  None: "No verdict for all rows: at least one row is not complete, or the run covers a subset of stories."}[v])
    lines += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def _local_prompt_token_means(sel: Selection) -> dict[str, float]:
    stored = read_study2b_prompts(projects=sel.projects, models=sel.models, arms=sel.arms, run=1)
    if sel.stories is not None:
        stored = [sp for sp in stored if sp.story_id in sel.stories]
    out: dict[str, float] = {}
    for model in sel.models:
        vals = [sp.local_prompt_tokens for sp in stored if sp.model == model]
        if vals and all(v is not None for v in vals):
            out[model] = sum(vals) / len(vals)  # type: ignore[arg-type]
    return out


def cmd_report(run_name: str, *, guard: WriteGuard = DEFAULT_GUARD, runs_root: Path | None = None,
               reference: Mapping[tuple[str, str], float] | None = None,
               fact_sheets: Mapping[str, Sequence[str]] | None = None,
               local_prompt_tokens: Mapping[str, float] | None = None,
               builder: PromptBuilder | None = None) -> int:
    """Write report.json and report.md into the run folder and print the table.

    A lab run (step 'lab') gets the lab report. A folder that mixes steps raises GuardError.
    """
    run_dir = (runs_root or TUNING_ROOT) / run_name
    if not run_dir.is_dir():
        print(f"report: the run folder {run_name} does not exist")
        return 2
    if run_step(run_dir) == "lab":
        return _cmd_lab_report(run_dir, guard, fact_sheets, builder)
    if local_prompt_tokens is None and (run_dir / "manifest.json").is_file():
        o = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["options"]
        sel = Selection(tuple(o["projects"]), tuple(o["models"]), tuple(o["arms"]),
                        frozenset(o["stories"]) if o["stories"] else None, o["repeats"])
        local_prompt_tokens = _local_prompt_token_means(sel)
    report = build_report(run_dir, reference=reference, fact_sheets=fact_sheets,
                          local_prompt_tokens=local_prompt_tokens)
    jtext = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    mtext = render_report_md(report)
    assert_no_private_text(jtext, what="report.json")
    assert_no_private_text(mtext, what="report.md")
    safe_write(run_dir / "report.json", jtext, guard=guard)
    safe_write(run_dir / "report.md", mtext, guard=guard)
    print(mtext)
    return 0


# --------------------------------------------------------------------------
# The prompt lab: stories, jobs, preflight, manifest
# --------------------------------------------------------------------------

SPLITS_PATH: Path = SCRIPTS / "tuning_lib" / "splits.json"
HOLDOUT_LOG_NAME = "holdout_use_log.jsonl"
LAB_METRICS: tuple[str, ...] = (
    "failed_share", "name_use", "n_with_name", "n_requirements", "outside_share", "outside_code_share",
    "outside_none_share", "all_inside_share")


class SplitError(ValueError):
    """The split of development and hold-out stories is wrong, or a story choice is not allowed."""


@dataclass(frozen=True)
class Splits:
    """The development stories, the hold-out stories and the rule that made the split."""

    dev: tuple[str, ...]
    holdout: tuple[str, ...]
    rule: str


def load_splits(path: Path = SPLITS_PATH, *, known_ids: Sequence[str] | None = None) -> Splits:
    """Read the split file (read only) and check it. Raise SplitError on any fault."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SplitError(f"cannot read the split file: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or set(data) != {"dev", "holdout", "rule"}:
        raise SplitError("the split file must hold exactly the keys dev, holdout and rule")
    dev, holdout, rule = data["dev"], data["holdout"], data["rule"]
    for name, ids, n in (("dev", dev, 10), ("holdout", holdout, 5)):
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise SplitError(f"{name} must be a list of ids")
        if len(ids) != n or len(set(ids)) != n:
            raise SplitError(f"{name} must hold {n} distinct ids")
    if set(dev) & set(holdout):
        raise SplitError("dev and holdout share an id")
    if not isinstance(rule, str) or not rule.strip():
        raise SplitError("the rule must be a non-empty text")
    if known_ids is not None:
        unknown = sorted((set(dev) | set(holdout)) - set(known_ids))
        if unknown:
            raise SplitError(f"unknown story id(s) in the split file: {', '.join(unknown)}")
    return Splits(tuple(dev), tuple(holdout), rule)


def resolve_stories(spec: str, splits: Splits, *, final_check: bool) -> tuple[tuple[str, ...], bool]:
    """Turn --stories into (ids in a fixed order, used_holdout). Hold-out needs --final-check."""
    order = splits.dev + splits.holdout
    if spec == "@dev":
        ids: list[str] = list(splits.dev)
    elif spec == "@holdout":
        ids = list(splits.holdout)
    else:
        ids = spec.split(",")
        if not all(i.strip() == i and i for i in ids):
            raise SplitError("the story list has an empty item or a space")
        if len(set(ids)) != len(ids):
            raise SplitError("the story list has a duplicate")
        unknown = [i for i in ids if i not in order]
        if unknown:
            raise SplitError(f"unknown story id(s): {', '.join(unknown)}")
        ids = [i for i in order if i in ids]
    used = any(i in splits.holdout for i in ids)
    if used and not final_check:
        raise SplitError("the hold-out stories need --final-check")
    if final_check and not used:
        raise SplitError("--final-check is only for runs that use hold-out stories")
    return tuple(ids), used


def _bytes4(*texts: str) -> int:
    return estimate_prompt_tokens(*texts, method="bytes4")


def plan_lab_jobs(
    sel: Selection,
    versions: Sequence[str],
    *,
    order_seed: int = 1,
    builder: PromptBuilder | None = None,
) -> list[LabJob]:
    """Build, check and order the jobs of a lab run. The versions are mixed inside each group."""
    builder = builder or PromptBuilder()
    if not versions or len(set(versions)) != len(versions):
        raise SelectionError("--versions needs at least one name and no duplicate")
    if sel.stories is None or not sel.stories:
        raise SelectionError("a lab run must name its stories")
    infos = {}
    for name in versions:
        infos[name] = builder.version(name)
        assert_general(name, builder=builder)
    stored: list[StoredPrompt] = []
    for project in sel.projects:  # read_study2b_prompts refuses a story that is not in the project
        ids = {sid for sid in sel.stories if builder.story(sid).project == project}
        if ids:
            stored += read_study2b_prompts(
                projects=(project,), models=sel.models, arms=("B2",), run=1, stories=ids)
    by_cell = {(sp.story_id, sp.model): sp for sp in stored}
    if len(by_cell) != len(stored) or len(by_cell) != len(sel.stories) * len(sel.models):
        raise SelectionError("the stored prompts do not match the stories and models of the selection")
    for sp in stored:
        verify_stored_prompt(sp, read_study2b_context(sp.project, sp.model, sp.story_id))
    for story_id, model in sorted(by_cell, key=lambda k: (k[0], MODELS.index(k[1]))):
        builder.verify_v0_old(story_id, model)
    jobs: list[LabJob] = []
    for repeat in range(1, sel.repeats + 1):
        for story_id in sorted(sel.stories):
            for model in sel.models:
                sp = by_cell[(story_id, model)]
                order = list(versions)
                random.Random(f"{order_seed}|{repeat}|{story_id}|{model}").shuffle(order)
                for name in order:
                    built = builder.build(story_id, model, name)
                    info = infos[name]
                    jobs.append(LabJob(
                        config_id=name, step="lab", project=sp.project, model=model, arm="B2",
                        story_id=story_id, repeat=repeat, system=built.system, user_sent=built.user,
                        user_non_doc=built.user_non_doc, doc_context_ref="frozen",
                        doc_context_sha256=built.block_sha256, stored=sp, version=name,
                        version_parent=info.parent,
                        template_sha256=tuple((f, info.template_sha256[f]) for f in TEMPLATE_FILES),
                        passage_changes=tuple(info.passage_changes),
                        passage_block_sha256=built.block_sha256,
                        n_doc_passages=built.n_doc_passages, n_code_hits=built.n_code_hits,
                    ))
    keys = [j.key for j in jobs]
    if len(set(keys)) != len(keys):
        raise SelectionError("two jobs have the same key")
    return jobs


def lab_estimate_cost(jobs: Sequence[Job]) -> tuple[int, int, float]:
    """Estimate (input tokens, output tokens, USD). Input tokens scale with the prompt size."""
    tin = tout = 0
    usd = 0.0
    for job in jobs:
        pin = HOSTED_MODELS.get(job.model)
        a, b = job.stored.local_prompt_tokens, job.stored.local_completion_tokens
        if pin is None or a is None or b is None or job.stored.user_sent is None:
            raise GuardError(f"cannot estimate the cost of {job.model} {job.story_id}: pin or token counts missing")
        scaled = a * _bytes4(job.system, job.user_sent) / _bytes4(job.stored.system, job.stored.user_sent)
        tin += round(scaled)
        tout += b
        usd += scaled * pin.price_in_per_mtok / 1e6 + b * pin.price_out_per_mtok / 1e6
    return tin, tout, usd


def _lab_versions_block(builder: PromptBuilder, versions: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in versions:
        info = builder.version(name)
        out[name] = {
            "parent": info.parent,
            "description": info.description,
            "passage_changes": list(info.passage_changes),
            "template_sha256": dict(info.template_sha256),
            "version_json_sha256": info.version_json_sha256,
        }
    return out


def _lab_configuration_block(builder: PromptBuilder, versions: Sequence[str]) -> dict[str, Any]:
    return {
        "config_id": "lab",
        "source": "prompt versions below scripts/tuning_lib/prompt_versions, with the stored Study 2b passages",
        "retry_rule": RETRY_RULE,
        "parser": PARSER_TEXT,
        "request_template": _request_template(),
        "prompt_versions": _lab_versions_block(builder, versions),
    }


def _lab_resume_mismatches(manifest: Mapping[str, Any], *, sel: Selection, versions: Sequence[str],
                           stories: Sequence[str], spec: str, order_seed: int, max_attempts: int,
                           builder: PromptBuilder) -> list[str]:
    """Differences between a stored lab manifest and the wanted run."""
    bad: list[str] = []
    if manifest.get("step") != "lab":
        return ["step"]
    o = manifest.get("options", {})
    if list(o.get("versions", [])) != list(versions):
        bad.append("versions")
    if sorted(o.get("stories") or []) != sorted(stories):
        bad.append("stories")
    if o.get("stories_spec") != spec:
        bad.append("stories_spec")
    if list(o.get("models", [])) != list(sel.models):
        bad.append("models")
    if o.get("repeats") != sel.repeats:
        bad.append("repeats")
    if o.get("max_attempts") != max_attempts:
        bad.append("max_attempts")
    if o.get("order_seed") != order_seed:
        bad.append("order_seed")
    old_pv = (manifest.get("configuration") or {}).get("prompt_versions") or {}
    now_pv = _lab_versions_block(builder, versions)
    for name in versions:
        a, b = old_pv.get(name) or {}, now_pv[name]
        if a.get("template_sha256") != b["template_sha256"] or a.get("passage_changes") != b["passage_changes"]:
            bad.append(f"version {name} was edited after its first records")
    for model in sel.models:
        pin = HOSTED_MODELS.get(model)
        old = manifest.get("models", {}).get(model)
        if pin is None or old is None or any(old.get(k) != _pin_block(pin)[k] for k in _PIN_COMPARE):
            bad.append(f"pin of {model}")
    return bad


def preflight_lab(
    sel: Selection,
    jobs: Sequence[LabJob],
    versions: Sequence[str],
    run_dir: Path,
    *,
    dry_run: bool,
    resume: bool,
    max_cost_usd: float,
    skip_hash_lists: bool,
    guard: WriteGuard = DEFAULT_GUARD,
    max_attempts: int = 3,
    builder: PromptBuilder | None = None,
    stories_spec: str = "",
    order_seed: int = 1,
    holdout_used: Sequence[str] = (),
    holdout_reuse_reason: str | None = None,
) -> list[Check]:
    """Run every check of a lab run before any request. Nothing is written or created."""
    builder = builder or PromptBuilder()
    checks: list[Check] = []

    def c_folder() -> Check:
        guard.check(run_dir)
        if not RUN_NAME_RE.match(run_dir.name) or run_dir.name.casefold() in FORBIDDEN_RUN_NAMES:
            return Check("run folder", "FAIL", "the run name is not allowed")
        exists = run_dir.exists()
        if exists and not resume:
            return Check("run folder", "FAIL", "the run folder exists; use --resume or another --run-name")
        if resume and not exists:
            return Check("run folder", "FAIL", "--resume needs an existing run folder")
        if resume:
            mpath = run_dir / "manifest.json"
            if not mpath.is_file():
                return Check("run folder", "FAIL", "--resume needs manifest.json in the run folder")
            bad = _lab_resume_mismatches(
                json.loads(mpath.read_text(encoding="utf-8")), sel=sel, versions=versions,
                stories=sorted(sel.stories or ()), spec=stories_spec, order_seed=order_seed,
                max_attempts=max_attempts, builder=builder)
            if bad:
                return Check("run folder", "FAIL", "the run differs from the manifest: " + ", ".join(bad))
        return Check("run folder", "PASS", "allowed by the guard")

    checks.append(_check_step("run folder", c_folder))

    def c_hash() -> Check:
        if skip_hash_lists:
            return Check("hash lists (before)", "SKIP", "skipped by --skip-hash-lists (dry run only)")
        results = check_all_hash_lists()
        bad = [n for n, r in results.items() if not r.ok]
        detail = ", ".join(f"{n}: {r.n_listed} listed" for n, r in results.items())
        if bad:
            return Check("hash lists (before)", "FAIL", "not ok: " + ", ".join(bad), data=results)
        return Check("hash lists (before)", "PASS", detail, data=results)

    checks.append(_check_step("hash lists (before)", c_hash))

    def c_procs() -> Check:
        assert_no_other_experiment(list_processes())
        if (run_dir / LOCK_NAME).exists():
            return Check("other processes and lock", "FAIL", "a lock file exists in the run folder")
        return Check("other processes and lock", "PASS", "no other experiment process, no lock file")

    checks.append(_check_step("other processes and lock", c_procs))

    def c_versions() -> Check:
        lines = []
        for name in versions:
            rep = check_generality(name, builder=builder)
            if not rep.ok:
                hits = "; ".join(f"{h.kind} {h.term!r} {h.file}:{h.line}" for h in rep.new_hits[:10])
                return Check("prompt versions", "FAIL", f"{name} has new generality hits: {hits}")
            lines.append(f"{name} ({len(rep.inherited_hits)} inherited hit(s))")
        return Check("prompt versions", "PASS", ", ".join(lines))

    checks.append(_check_step("prompt versions", c_versions))
    pairs = {(j.story_id, j.model) for j in jobs}
    checks.append(Check("v0_old rebuild", "PASS", f"equal to the stored prompt for {len(pairs)} story and model pairs"))

    def c_passages() -> Check:
        for job in jobs:
            if job.n_doc_passages < 1:
                raise PassagesError(f"{job.story_id} {job.model} {job.version}: no documentation passage")
            if job.doc_context_sha256 == sha256_text(FALLBACK_CONTEXT):
                raise PassagesError(f"{job.story_id} {job.version}: the block is the fallback text")
        return Check("passages", "PASS", f"{len(jobs)} jobs hold at least one documentation passage")

    checks.append(_check_step("passages", c_passages))
    if holdout_used:
        def c_holdout_log() -> Check:
            earlier = sorted({
                str(e.get("run_name")) for e in read_jsonl(run_dir.parent / HOLDOUT_LOG_NAME)
                if e.get("run_name") != run_dir.name})
            if earlier and not (holdout_reuse_reason or "").strip():
                return Check("hold-out log", "FAIL",
                             f"hold-out used before by run {', '.join(earlier)}; "
                             "a repeat needs --holdout-reuse-reason TEXT")
            if earlier:
                return Check("hold-out log", "WARN", f"hold-out used before by run {', '.join(earlier)}; "
                                                     f"reason: {_cut(holdout_reuse_reason or '', 200)}")
            return Check("hold-out log", "PASS", "no earlier use of the hold-out stories")

        checks.append(_check_step("hold-out log", c_holdout_log))
        checks.append(Check("hold-out", "WARN", "HOLD-OUT STORIES USED: " + ", ".join(holdout_used)))
    else:
        checks.append(Check("hold-out", "PASS", "no hold-out story"))

    def c_bodies() -> Check:
        n = 0
        for job in jobs:
            pin = HOSTED_MODELS.get(job.model)
            if pin is None:
                raise GuardError(f"no hosted pin for {job.model}")
            body = build_request_body(pin, job.system, job.user_sent)
            # A model with no reasoning mode sends no reasoning field (its endpoint refuses the parameter).
            # The client still stops when an answer holds reasoning.
            if body.get("reasoning") != ({"effort": "none"} if pin.reasoning_off else None):
                raise GuardError("reasoning is not switched off in the request")
            prov = body.get("provider", {})
            if len(prov.get("only", [])) != 1 or prov.get("allow_fallbacks") is not False:
                raise GuardError("the provider is not pinned to one endpoint")
            n += 1
        return Check("request bodies", "PASS", f"{n} bodies built, privacy guard passed")

    checks.append(_check_step("request bodies", c_bodies))

    def c_size() -> Check:
        longest = 0
        warn = 0
        for job in jobs:
            est = _bytes4(job.system, job.user_sent)
            longest = max(longest, est)
            if est > MAX_PROMPT_TOKENS_EST:
                raise GuardError(f"prompt of {job.story_id} {job.version} has about {est} tokens (limit {MAX_PROMPT_TOKENS_EST})")
            if estimate_prompt_tokens(job.system, job.user_sent, method="bytes32") > MAX_PROMPT_TOKENS_EST:
                warn += 1
            local = job.stored.local_prompt_tokens
            if local is not None and job.stored.user_sent is not None and job.version != BASELINE:
                scaled = local * est / _bytes4(job.stored.system, job.stored.user_sent)
                if scaled + MAX_ANSWER_TOKENS > LOCAL_WINDOW_TOKENS:
                    warn += 1
        if warn:
            return Check("prompt size", "WARN", f"longest {longest} tokens (bytes4); {warn} prompt(s) are near a limit by a cautious or scaled estimate")
        return Check("prompt size", "PASS", f"longest {longest} tokens (bytes4)")

    checks.append(_check_step("prompt size", c_size))
    for model in sel.models:
        pin = HOSTED_MODELS.get(model)
        if pin is None:
            checks.append(Check(f"pin {model}", "FAIL", "no hosted pin for this model"))
        elif not pin.confirmed:
            checks.append(Check(f"pin {model}", "WARN", f"{pin.provider_tag} is not confirmed by a first call"))
        else:
            checks.append(Check(f"pin {model}", "PASS", f"{pin.provider_tag} confirmed"))
    unconfirmed = [m for m in sel.models if m in HOSTED_MODELS and not HOSTED_MODELS[m].confirmed]
    if unconfirmed and len(jobs) > SMOKE_JOB_LIMIT:
        checks.append(Check("pins confirmed", "FAIL",
                            f"{len(jobs)} generations with unconfirmed pins. Confirm the pins with a small run first."))

    def c_cost() -> Check:
        tin, tout, usd = lab_estimate_cost(jobs)
        if usd > max_cost_usd:
            return Check("cost estimate", "FAIL", f"{usd:.4f} USD is above the cap {max_cost_usd:.2f} USD")
        return Check("cost estimate", "PASS", f"about {usd:.4f} USD ({tin} input, {tout} output tokens; estimate only)")

    checks.append(_check_step("cost estimate", c_cost))
    return checks


def lab_code_hashes(builder: PromptBuilder, versions: Sequence[str]) -> dict[str, str]:
    """Code hashes of the anchor plus every file of the used prompt version folders."""
    out = code_hashes()
    for name in versions:
        folder = builder.version(name).folder
        for path in sorted(p for p in folder.iterdir() if p.is_file()):
            out[f"prompt_versions/{name}/{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def build_lab_manifest(
    *, run_name: str, state: str, stop_reason: str | None, started_utc: str, ended_utc: str | None,
    argv: Sequence[str], args: argparse.Namespace, sel: Selection, jobs: Sequence[LabJob],
    estimate_usd: float, hash_before: Mapping[str, Any], hash_after: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]], summary: ExecSummary | None, notes: Sequence[str],
    git: Mapping[str, Any], code: Mapping[str, str], inputs: Mapping[str, Any],
    builder: PromptBuilder, versions: Sequence[str], stories_spec: str, holdout_used: Sequence[str],
    final_check: bool,
) -> dict[str, Any]:
    """The manifest of a lab run. The key 'versions' stays the Python versions."""
    m = build_manifest(
        run_name=run_name, state=state, stop_reason=stop_reason, started_utc=started_utc,
        ended_utc=ended_utc, argv=argv, args=args, sel=sel, jobs=jobs, estimate_usd=estimate_usd,
        hash_before=hash_before, hash_after=hash_after, records=records, summary=summary,
        notes=notes, git=git, code=code, inputs=inputs)
    m["step"] = "lab"
    m["options"].update({
        "versions": list(versions), "stories_spec": stories_spec, "order_seed": args.order_seed,
        "final_check": bool(final_check), "holdout_used": list(holdout_used),
    })
    m["configuration"] = _lab_configuration_block(builder, versions)
    per: dict[str, int] = {}
    for j in jobs:
        k = f"{j.model}/{j.config_id}"
        per[k] = per.get(k, 0) + 1
    m["jobs"]["per_model_arm"] = per
    return m


def lab_input_hashes(jobs: Sequence[Job]) -> dict[str, Any]:
    """SHA-256 of the Study 2b records files that hold the stored passages, and of the split file."""
    cells = sorted({(j.project, j.model) for j in jobs})
    recs = {}
    for project, model in cells:
        p = RTC / "study2b" / project / model_slug(model) / "records.jsonl"
        recs[_rel(p, RTC)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {
        "study2b_records_sha256": recs,
        "splits_sha256": hashlib.sha256(SPLITS_PATH.read_bytes()).hexdigest(),
    }


def _print_lab_dry_run(sel: Selection, jobs: Sequence[LabJob], versions: Sequence[str]) -> None:
    print(f"dry run: {len(jobs)} generations, {sel.repeats} repeat(s), {len(versions)} version(s)")
    counts: dict[str, int] = {}
    for j in jobs:
        key = f"{j.model} {j.version}"
        counts[key] = counts.get(key, 0) + 1
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    print("order of the first 12 jobs (repeat, story, model, version):")
    for j in jobs[:12]:
        print(f"  {j.repeat} {j.story_id} {j.model} {j.version}")
    tin, tout, usd = lab_estimate_cost(jobs)
    longest = max(_bytes4(j.system, j.user_sent) for j in jobs)
    print(f"estimated tokens: {tin} input, {tout} output")
    print(f"estimated cost: {usd:.4f} USD (estimate only)")
    print(f"longest prompt: about {longest} tokens (bytes4)")


def _lab(args: argparse.Namespace, argv: Sequence[str], *, guard: WriteGuard, runs_root: Path | None,
         client_factory: Callable[[ModelPin], OpenRouterClient] | None,
         sleep: Callable[[float], None], clock: Callable[[], float],
         builder: PromptBuilder | None) -> int:
    """The subcommand 'run'. Return 0, 2 (refused, nothing sent) or 3 (stopped, can be resumed)."""
    root = runs_root or TUNING_ROOT
    run_dir = root / args.run_name
    versions = list(args.versions)
    try:
        builder = builder or PromptBuilder()
        splits = load_splits(known_ids=list(builder.stories()))
        stories, used_holdout = resolve_stories(args.stories, splits, final_check=args.final_check)
        if args.models == ["both"]:
            models = MODELS
        else:
            bad = [m for m in args.models if m not in MODELS or m not in HOSTED_MODELS]
            if bad:
                print(f"[FAIL] --models: {', '.join(bad)} not allowed; choose from {', '.join(MODELS)}")
                return 2
            models = tuple(m for m in MODELS if m in args.models)
        projects = {builder.story(sid).project for sid in stories}
        sel = Selection(tuple(p for p in PROJECTS if p in projects), models, ("B2",),
                        frozenset(stories), args.repeats)
        if BASELINE not in versions:
            print("WARN: no baseline in this run; the report has no comparison")
        jobs = plan_lab_jobs(sel, versions, order_seed=args.order_seed, builder=builder)
    except (PromptError, SplitError, SelectionError, PassagesError, GuardError) as exc:
        print(f"[FAIL] plan: {type(exc).__name__}: {_cut(str(exc), 400)}")
        return 2
    holdout_used = [s for s in stories if s in splits.holdout]
    reuse_reason = (args.holdout_reuse_reason or "").strip() or None
    if args.holdout_reuse_reason is not None and not reuse_reason:
        print("[FAIL] --holdout-reuse-reason must not be empty")
        return 2
    if reuse_reason and not used_holdout:
        print("[FAIL] --holdout-reuse-reason is only for runs that use hold-out stories")
        return 2
    if used_holdout:
        print(f"HOLD-OUT STORIES USED: {', '.join(holdout_used)} (versions: {', '.join(versions)}, run: {args.run_name})")
    if args.dry_run:
        try:
            _print_lab_dry_run(sel, jobs, versions)
        except GuardError as exc:
            print(f"[FAIL] cost estimate: {exc}")
            return 2
    checks = preflight_lab(
        sel, jobs, versions, run_dir, dry_run=args.dry_run, resume=args.resume,
        max_cost_usd=args.max_cost_usd, skip_hash_lists=args.skip_hash_lists, guard=guard,
        max_attempts=args.max_attempts, builder=builder, stories_spec=args.stories,
        order_seed=args.order_seed, holdout_used=holdout_used,
        holdout_reuse_reason=reuse_reason)
    print_checks(checks)
    if any(c.status == "FAIL" for c in checks):
        print("refused: a check failed. Nothing was sent.")
        return 2
    if args.dry_run:
        print("key: not read, no network call made")
        return 0

    factory = client_factory
    if factory is None:
        try:
            key = load_api_key()
        except FatalHostedError as exc:
            print(f"[FAIL] key: {_cut(str(exc), 400)}")
            return 2
        factory = lambda pin: OpenRouterClient(pin, key)  # noqa: E731

    hash_before = {}
    for c in checks:
        if c.name == "hash lists (before)" and c.data is not None:
            ts = utc_now()
            hash_before = {n: _hash_summary(r, ts) for n, r in c.data.items()}
    _, _, estimate_usd = lab_estimate_cost(jobs)
    git = git_info()
    code = lab_code_hashes(builder, versions)
    inputs = lab_input_hashes(jobs)
    started = utc_now()
    notes = ["resumed run"] if args.resume else []
    if used_holdout:
        notes.append("hold-out stories used with --final-check: " + ", ".join(holdout_used))
        if reuse_reason:
            notes.append("hold-out used again; reason: " + reuse_reason)
    if BASELINE not in versions:
        notes.append("no baseline in this run; the report has no comparison")

    def manifest(state: str, stop: str | None, ended: str | None, recs: Sequence[Mapping[str, Any]],
                 summary: ExecSummary | None, after: Mapping[str, Any]) -> dict[str, Any]:
        return build_lab_manifest(
            run_name=args.run_name, state=state, stop_reason=stop, started_utc=started, ended_utc=ended,
            argv=argv, args=args, sel=sel, jobs=jobs, estimate_usd=estimate_usd, hash_before=hash_before,
            hash_after=after, records=recs, summary=summary, notes=notes, git=git, code=code, inputs=inputs,
            builder=builder, versions=versions, stories_spec=args.stories, holdout_used=holdout_used,
            final_check=args.final_check)

    def on_start() -> None:
        if used_holdout:
            make_run_folder(root, guard=guard)
            line = json.dumps({"ts": utc_now(), "run_name": args.run_name, "versions": versions,
                               "stories": holdout_used, "command_line": list(argv),
                               "reuse_reason": reuse_reason}, ensure_ascii=False)
            assert_no_private_text(line, what="hold-out log line")
            guarded_append_line(root / HOLDOUT_LOG_NAME, line, guard=guard)
        write_manifest(run_dir, manifest("running", None, None, _read_cells(run_dir, jobs), None, {}), guard)

    def finish(summary: ExecSummary) -> None:
        _finalize(summary, run_dir=run_dir, jobs=jobs, manifest=manifest, guard=guard, runs_root=runs_root,
                  run_name=args.run_name, builder=builder)

    summary = execute_detailed(
        jobs, run_dir, workers=args.workers, max_cost_usd=args.max_cost_usd, max_minutes=args.max_minutes,
        max_attempts=args.max_attempts, client_factory=factory, guard=guard, sleep=sleep, clock=clock,
        on_start=on_start, on_finish=finish)
    return summary.exit_code


# --------------------------------------------------------------------------
# The lab report
# --------------------------------------------------------------------------

LAB_NOTES: tuple[str, ...] = (
    "Script measures only. They show where names come from. They do not show whether a requirement is right. The judges do that.",
    "One fixed reference for every version: the documentation passages of the v0_old block, the story and the system text.",
    "Names from the code-search hits of the v0_old block count as outside. A version that drops or changes the hits changes the outside share by construction. "
    "So outside_share is split: outside_code_share (all outside names are in the v0_old code hits) and outside_none_share (an outside name is in no given text). Both texts are the same for every version.",
    "failed_share is the share of final failed generations (all attempts used) for each story and model. A failed generation has no requirement, so every other measure leaves it out. Read failed_share beside them.",
    "The standard error is over stories, not over requirements.",
    "No sentence in this file says that a version is better.",
)


GIVEN_NOTE = ("The name measure uses the given set of v0_old for every version, so an identifier that v0_old gives "
              "in its examples is never counted as name use.")


def _read_run_records(run_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(run_dir.glob("*/*/records.jsonl")):
        records.extend(read_jsonl(path))
    return records


def run_step(run_dir: Path) -> str | None:
    """The step of a run folder ('anchor' or 'lab'). A folder that mixes steps raises GuardError."""
    steps = {r.get("step") for r in _read_run_records(run_dir)}
    mpath = run_dir / "manifest.json"
    if mpath.is_file():
        steps.add(json.loads(mpath.read_text(encoding="utf-8")).get("step"))
    steps.discard(None)
    if len(steps) > 1:
        raise GuardError(f"the run folder mixes steps: {sorted(steps)}")
    return next(iter(steps)) if steps else None


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_lab_report(
    run_dir: Path,
    *,
    fact_sheets: Mapping[str, Sequence[str]] | None = None,
    builder: PromptBuilder | None = None,
    baseline: str = BASELINE,
) -> dict[str, Any]:
    """Score the records of a lab run and compare each version with the baseline, story by story."""
    manifest: dict[str, Any] | None = None
    mpath = run_dir / "manifest.json"
    if mpath.is_file():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    records = _read_run_records(run_dir)
    if not records:
        raise GuardError("the run folder holds no records")
    steps = {r["step"] for r in records}
    if steps != {"lab"}:
        raise GuardError(f"a lab report needs lab records only, found steps {sorted(steps)}")
    opts: dict[str, Any] = dict(manifest["options"]) if manifest else {}
    versions = list(opts.get("versions") or sorted({r["config_id"] for r in records}))
    models = [m for m in MODELS if m in (opts.get("models") or {r["model"] for r in records})]
    stories = sorted(opts.get("stories") or {r["story_id"] for r in records})
    repeats = int(opts.get("repeats") or max(int(r["run"]) for r in records))
    max_attempts = int(opts.get("max_attempts") or 3)
    sheets = fact_sheets if fact_sheets is not None else measures.load_fact_sheets()
    base = baseline if baseline in versions else None
    lazy: dict[str, PromptBuilder] = {}
    refs: dict[tuple[str, str], Any] = {}

    def reference(story_id: str, model: str) -> Any:
        key = (story_id, model)
        if key not in refs:
            if builder is None and "b" not in lazy:
                lazy["b"] = PromptBuilder()
            refs[key] = lab_measures.build_reference(story_id, model, builder=builder or lazy["b"])
        return refs[key]

    latest = measures.latest_records(records)
    states = key_states(records)
    # One fixed "given" set for the name measure: the identifiers that the v0_old prompt holds.
    reg = measures.registered_module()
    given_v0: dict[tuple[str, str], set[str]] = {}

    def baseline_given(story_id: str, model: str) -> set[str]:
        key = (story_id, model)
        if key not in given_v0:
            if builder is None and "b" not in lazy:
                lazy["b"] = PromptBuilder()
            text = lab_measures.baseline_prompt_text(story_id, model, builder=builder or lazy["b"])
            given_v0[key] = set(reg.found(text, list(sheets[story_id])))
        return given_v0[key]

    given_diffs: dict[tuple[str, str, str], tuple[set[str], set[str]]] = {}
    failed_by: dict[str, dict[str, dict[str, float]]] = {}
    cells: list[dict[str, Any]] = []
    per_story: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for version in versions:
        per_story[version] = {}
        for model in models:
            lines = [r for r in records if r["config_id"] == version and r["model"] == model]
            finals = [r for k, r in latest.items() if k[0] == version and k[1] == model]
            ok_recs = [r for r in finals if r.get("error") is None]
            by_story: dict[str, list[dict[str, float]]] = {}
            for rec in ok_recs:
                sid = rec["story_id"]
                if sid not in sheets:
                    raise GuardError(f"no fact sheet for story {sid}")
                own = set(measures.score_record(rec, sheets[sid]).prompt_given)
                fixed = baseline_given(sid, model)
                if own != fixed:
                    if version == BASELINE:
                        diff = sorted(own ^ fixed)
                        raise GuardError(
                            f"the 'given' identifiers of the {BASELINE} records, story {sid}, model {model} differ from "
                            f"those of the {BASELINE} baseline text: {', '.join(diff)}. The records or the baseline text are wrong.")
                    given_diffs.setdefault((version, sid, model), (own, fixed))
                # The name measure uses ONE reference: the given set of v0_old (its prompt text), not the own prompt.
                ref_rec = dict(rec)
                ref_rec["prompt_system"] = lab_measures.baseline_prompt_text(
                    sid, model, builder=builder or lazy["b"])
                ref_rec["prompt_user_non_doc"] = ""
                score = measures.score_record(ref_rec, sheets[sid])
                check = lab_measures.check_requirements(rec["requirements"], reference(sid, model))
                by_story.setdefault(sid, []).append({
                    "name_use": score.name_use, "n_with_name": float(score.n_with_name),
                    "n_requirements": float(score.n_requirements), "outside_share": check.outside_share,
                    "outside_code_share": check.outside_code_share, "outside_none_share": check.outside_none_share,
                    "all_inside_share": check.all_inside_share, "no_name_share": check.no_name_share,
                })
            stories_out: dict[str, dict[str, Any]] = {}
            for sid in sorted(by_story):
                rows = by_story[sid]
                stories_out[sid] = {"n_runs": len(rows)} | {
                    k: sum(r[k] for r in rows) / len(rows)
                    for k in ("name_use", "n_with_name", "n_requirements", "outside_share", "outside_code_share",
                              "outside_none_share", "all_inside_share", "no_name_share")
                }
            per_story[version][model] = stories_out

            def cell_mean(metric: str) -> float | None:
                return _mean_or_none([v[metric] for v in stories_out.values()])

            keys = {k for k in latest if k[0] == version and k[1] == model}
            n_failed = sum(
                1 for k in keys if latest[k].get("error") is not None and is_final(states.get(k), max_attempts))
            n_planned = len(stories) * repeats
            failed_story: dict[str, float] = {}
            for sid in stories:
                n_f = sum(1 for k in keys if k[2] == sid and latest[k].get("error") is not None
                          and is_final(states.get(k), max_attempts))
                failed_story[sid] = n_f / repeats
            failed_by.setdefault(version, {})[model] = failed_story
            ok_with_tokens = [r for r in ok_recs if r.get("prompt_tokens") is not None]
            cells.append({
                "version": version, "model": model, "n_planned": n_planned,
                "n_final_ok": len(ok_recs), "n_final_failed": n_failed,
                "n_not_final": max(0, n_planned - len(ok_recs) - n_failed),
                "parse_failures": {
                    "attempt_lines_failed": sum(1 for r in lines if r.get("error") is not None),
                    "transport_lines": sum(1 for r in lines if r.get("error_kind") == "transport"),
                    "final_failed_keys": sum(1 for r in finals if r.get("error") is not None),
                },
                "requirements_per_answer": _mean_or_none([float(r["n_requirements"]) for r in ok_recs]),
                "failed_share": n_failed / n_planned if n_planned else None,
                "name_use": cell_mean("name_use"),
                "n_with_name_per_story": {s: v["n_with_name"] for s, v in stories_out.items()},
                "outside_share": cell_mean("outside_share"),
                "outside_code_share": cell_mean("outside_code_share"),
                "outside_none_share": cell_mean("outside_none_share"),
                "all_inside_share": cell_mean("all_inside_share"),
                "no_name_share": cell_mean("no_name_share"),
                "cost_usd": sum(float(r.get("cost_usd") or 0.0) for r in lines),
                "mean_prompt_tokens": _mean_or_none([float(r["prompt_tokens"]) for r in ok_with_tokens]),
                "mean_completion_tokens": _mean_or_none(
                    [float(r["completion_tokens"]) for r in ok_recs if r.get("completion_tokens") is not None]),
            })

    comparisons: list[dict[str, Any]] = []
    if base is not None:
        for version in versions:
            if version == base:
                continue
            usable = [
                s for s in stories
                if all(s in per_story[v][m] for v in (version, base) for m in models)
            ]
            missing = [s for s in stories if s not in usable]
            for metric in LAB_METRICS:
                diffs: dict[str, float] = {}
                model_diffs: dict[str, list[float]] = {m: [] for m in models}
                if metric == "failed_share":  # every story counts: a failed story is a value, not a gap
                    use_stories, left_out = list(stories), []

                    def val(v: str, m: str, s: str, metric: str = metric) -> float:
                        return failed_by[v][m][s]
                else:
                    use_stories, left_out = usable, missing

                    def val(v: str, m: str, s: str, metric: str = metric) -> float:
                        return per_story[v][m][s][metric]
                for s in use_stories:
                    a = [val(version, m, s) for m in models]
                    b = [val(base, m, s) for m in models]
                    diffs[s] = sum(a) / len(a) - sum(b) / len(b)
                    for m, x, y in zip(models, a, b):
                        model_diffs[m].append(x - y)
                ps = paired_summary(list(diffs.values()))
                comparisons.append({
                    "version": version, "baseline": base, "metric": metric, "n_stories": len(use_stories),
                    "stories_missing": left_out, "per_story": diffs,
                    "mean": ps.mean, "se": ps.se, "up": ps.up, "equal": ps.equal, "down": ps.down,
                    "per_model": {
                        m: dataclasses.asdict(paired_summary(model_diffs[m])) for m in models
                    },
                })
    notes = list(LAB_NOTES)
    given_differences: list[dict[str, Any]] = []
    for (version, sid, model), (own, fixed) in sorted(given_diffs.items()):
        for ident in sorted(own ^ fixed):
            given_differences.append({
                "version": version, "story": sid, "model": model, "identifier": ident,
                "direction": "v0_old gives it, this version does not" if ident in fixed
                else "this version gives it, v0_old does not"})
    if given_differences:
        notes.append(GIVEN_NOTE)
    if base is None:
        notes.append("No baseline in this run: there is no comparison.")
    for c in cells:
        if c["n_final_ok"] < c["n_planned"]:
            notes.append(f"Incomplete cell: {c['version']} on {c['model']} has {c['n_final_ok']} good of {c['n_planned']} planned.")
    for c in cells:
        if c["n_final_failed"]:
            fs = {s: v for s, v in failed_by[c["version"]][c["model"]].items() if v}
            notes.append(f"Failed generations: {c['version']} on {c['model']} has {c['n_final_failed']} final failed "
                         f"of {c['n_planned']} (by story: " + ", ".join(f"{s} {v:.2f}" for s, v in fs.items()) + ").")
    pv = ((manifest or {}).get("configuration") or {}).get("prompt_versions") or {}
    return {
        "schema_version": SCHEMA_VERSION, "run_name": run_dir.name, "generated_utc": utc_now(),
        "state": manifest["state"] if manifest else "no manifest", "baseline": base,
        "options": opts,
        "versions": {v: {k: pv[v][k] for k in ("parent", "description", "passage_changes", "template_sha256")}
                     for v in versions if v in pv},
        "models": models, "stories": stories, "repeats": repeats, "cells": cells,
        "given_differences": given_differences,
        "per_story": per_story, "failed_share_by_story": failed_by, "comparisons": comparisons, "totals": _totals_dict(records),
        "providers": {m: manifest["models"][m].get("providers_seen") for m in models
                      if manifest and m in manifest.get("models", {})},
        "notes": notes,
    }


def _num(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def render_lab_report_md(report: Mapping[str, Any]) -> str:
    """Render the lab report as Markdown. It states numbers and never says that a version is better."""
    o = report.get("options") or {}
    L = [f"# Lab run {report['run_name']}", "",
         f"State: {report['state']}. Generated: {report['generated_utc']}.", "",
         f"- models: {', '.join(report['models'])}",
         f"- stories: {', '.join(report['stories'])}",
         f"- repeats: {report['repeats']}"]
    for model, prov in (report.get("providers") or {}).items():
        L.append(f"- {model}: providers seen {prov}")
    if o.get("holdout_used"):
        L.append(f"- HOLD-OUT STORIES USED: {', '.join(o['holdout_used'])}")
    L += ["", "## Versions", "", "| version | parent | description | passage changes |", "|---|---|---|---|"]
    for name, v in report["versions"].items():
        L.append(f"| {name} | {v['parent'] or '-'} | {v['description']} | {', '.join(v['passage_changes']) or '-'} |")
    L += ["", "## Cells", "",
          "| version | model | good of planned | failed attempt lines | failed share % | requirements per answer | name use % | outside share % | outside in v0_old code hits % | outside in no given text % | all-inside share % | cost USD |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in report["cells"]:
        L.append(
            f"| {c['version']} | {c['model']} | {c['n_final_ok']} of {c['n_planned']} | "
            f"{c['parse_failures']['attempt_lines_failed']} (final failed: {c['n_final_failed']}) | "
            f"{_pct(c['failed_share'])} | "
            f"{_num(c['requirements_per_answer'], 2)} | {_pct(c['name_use'])} | {_pct(c['outside_share'])} | "
            f"{_pct(c['outside_code_share'])} | {_pct(c['outside_none_share'])} | "
            f"{_pct(c['all_inside_share'])} | {c['cost_usd']:.4f} |")
    L += ["", "## Requirements with an attributable name, for each story", ""]
    cols = [(c["version"], c["model"]) for c in report["cells"]]
    L.append("| story | " + " | ".join(f"{v} {m}" for v, m in cols) + " |")
    L.append("|---|" + "---|" * len(cols))
    by = {(c["version"], c["model"]): c["n_with_name_per_story"] for c in report["cells"]}
    for s in report["stories"]:
        L.append(f"| {s} | " + " | ".join(_num(by[k].get(s), 2) for k in cols) + " |")
    base = report["baseline"]
    for version in dict.fromkeys(c["version"] for c in report["comparisons"]):
        comps = [c for c in report["comparisons"] if c["version"] == version]
        L += ["", f"## {version} against {base}", "",
              "| metric | stories | mean difference | standard error | up | equal | down | sign per model |",
              "|---|---|---|---|---|---|---|---|"]
        for c in comps:
            signs = ", ".join(f"{m} {d['sign'] or 'n/a'}" for m, d in c["per_model"].items())
            L.append(f"| {c['metric']} | {c['n_stories']} | {_num(c['mean'])} | {_num(c['se'])} | "
                     f"{c['up']} | {c['equal']} | {c['down']} | {signs} |")
        left = next(x for x in comps if x["metric"] == "name_use")["stories_missing"]
        if left:
            L += ["", "Stories left out of the other measures (no good answer for a model): " + ", ".join(left)]
        for metric in ("name_use", "outside_share"):
            c = next(x for x in comps if x["metric"] == metric)
            L += ["", f"Difference by story, {metric}:", "", "| story | difference |", "|---|---|"]
            L += [f"| {s} | {d:+.3f} |" for s, d in c["per_story"].items()]
    gd = report.get("given_differences") or []
    if gd:
        L += ["", "## Identifiers that the prompt of a version gives differently from v0_old", "",
              "| version | story | model | identifier | direction |", "|---|---|---|---|---|"]
        L += [f"| {d['version']} | {d['story']} | {d['model']} | {d['identifier']} | {d['direction']} |" for d in gd]
        L += ["", GIVEN_NOTE]
    t = report["totals"]
    L += ["", "## Counts", "", "| item | value |", "|---|---|"]
    for label, key in [("records (attempt lines)", "n_records"), ("failed attempt lines", "n_attempt_lines_failed"),
                       ("parse retries", "n_parse_retries"), ("transport failed lines", "n_transport_failed_lines"),
                       ("transport retries made by the client", "n_transport_retries"),
                       ("prompt tokens", "prompt_tokens"), ("completion tokens", "completion_tokens"),
                       ("cost (USD)", "cost_usd")]:
        L.append(f"| {label} | {t[key]} |")
    L += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(L) + "\n"


def _cmd_lab_report(run_dir: Path, guard: WriteGuard, fact_sheets: Mapping[str, Sequence[str]] | None,
                    builder: PromptBuilder | None) -> int:
    report = build_lab_report(run_dir, fact_sheets=fact_sheets, builder=builder)
    jtext = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    mtext = render_lab_report_md(report)
    assert_no_private_text(jtext, what="report.json")
    assert_no_private_text(mtext, what="report.md")
    safe_write(run_dir / "report.json", jtext, guard=guard)
    safe_write(run_dir / "report.md", mtext, guard=guard)
    print(mtext)
    return 0


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _run_name(value: str) -> str:
    if not RUN_NAME_RE.match(value):
        raise argparse.ArgumentTypeError("the run name must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    if value.casefold() in FORBIDDEN_RUN_NAMES:
        raise argparse.ArgumentTypeError("the run name must not be study2 or study2b")
    return value


def _csv(value: str) -> list[str]:
    items = [v.strip() for v in value.split(",")]
    if not all(items):
        raise argparse.ArgumentTypeError("empty item in a comma list")
    return items


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line. A bad value exits with code 2."""
    p = argparse.ArgumentParser(prog="run_tuning.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("anchor", help="send the stored Study 2b prompts to the hosted models")
    a.add_argument("--run-name", required=True, type=_run_name)
    a.add_argument("--repeats", type=int, default=2)
    a.add_argument("--workers", type=int, default=6)
    a.add_argument("--max-cost-usd", type=float, default=1.00)
    a.add_argument("--max-minutes", type=float, default=60.0)
    a.add_argument("--max-attempts", type=int, default=3)
    a.add_argument("--stories", type=_csv, default=None)
    a.add_argument("--projects", type=_csv, default=None)
    a.add_argument("--models", type=_csv, default=None)
    a.add_argument("--arms", type=_csv, default=None)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--resume", action="store_true")
    a.add_argument("--skip-hash-lists", action="store_true")
    r = sub.add_parser("report", help="build report.json and report.md")
    r.add_argument("--run-name", required=True, type=_run_name)
    n = sub.add_parser("run", help="the prompt lab: prompt versions on a list of stories")
    n.add_argument("--run-name", required=True, type=_run_name)
    n.add_argument("--versions", type=_csv, default=None)
    n.add_argument("--stories", default=None, help="@dev, @holdout or a comma list of story ids")
    n.add_argument("--repeats", type=int, default=3)
    n.add_argument("--models", type=_csv, default=["both"])
    n.add_argument("--workers", type=int, default=6)
    n.add_argument("--max-cost-usd", type=float, default=1.00)
    n.add_argument("--max-minutes", type=float, default=60.0)
    n.add_argument("--max-attempts", type=int, default=3)
    n.add_argument("--order-seed", type=int, default=1)
    n.add_argument("--dry-run", action="store_true")
    n.add_argument("--resume", action="store_true")
    n.add_argument("--skip-hash-lists", action="store_true")
    n.add_argument("--final-check", action="store_true")
    n.add_argument("--holdout-reuse-reason", default=None,
                   help="needed for a second use of the hold-out stories; stored in the log and the manifest")
    n.add_argument("--config", default=None, help=argparse.SUPPRESS)  # old stub option, kept for an old test
    args = p.parse_args(argv)
    if args.command == "anchor":
        if args.repeats < 1:
            p.error("--repeats must be at least 1")
        if not 1 <= args.workers <= 16:
            p.error("--workers must be from 1 to 16")
        if not args.max_cost_usd > 0:
            p.error("--max-cost-usd must be above 0")
        if not args.max_minutes > 0:
            p.error("--max-minutes must be above 0")
        if not 1 <= args.max_attempts <= 3:
            p.error("--max-attempts must be from 1 to 3")
        if args.skip_hash_lists and not args.dry_run:
            p.error("--skip-hash-lists is allowed only together with --dry-run")
        for opt, allowed in (("projects", PROJECTS), ("models", MODELS), ("arms", ANCHOR_ARMS)):
            vals = getattr(args, opt)
            if vals is not None:
                bad = [v for v in vals if v not in allowed]
                if bad:
                    p.error(f"--{opt}: {', '.join(bad)} not allowed; choose from {', '.join(allowed)}")
    if args.command == "run":
        if args.config is not None:
            raise NotImplementedError("run: the option --config is removed; use --versions and --stories")
        if args.versions is None:
            p.error("--versions is required")
        if len(set(args.versions)) != len(args.versions):
            p.error("--versions has a duplicate")
        if args.stories is None:
            p.error("--stories is required (@dev, @holdout or a comma list)")
        if args.repeats < 1:
            p.error("--repeats must be at least 1")
        if not 1 <= args.workers <= 16:
            p.error("--workers must be from 1 to 16")
        if not args.max_cost_usd > 0:
            p.error("--max-cost-usd must be above 0")
        if not args.max_minutes > 0:
            p.error("--max-minutes must be above 0")
        if not 1 <= args.max_attempts <= 3:
            p.error("--max-attempts must be from 1 to 3")
        if args.skip_hash_lists and not args.dry_run:
            p.error("--skip-hash-lists is allowed only together with --dry-run")
        if args.models != ["both"]:
            bad = [m for m in args.models if m not in MODELS]
            if bad:
                p.error(f"--models: {', '.join(bad)} not allowed; choose from both or {', '.join(MODELS)}")
    return args


def build_selection(args: argparse.Namespace) -> Selection:
    """Turn the parsed options into a Selection (filters keep the standard order)."""
    def pick(vals: list[str] | None, order: Sequence[str]) -> tuple[str, ...]:
        return tuple(order) if vals is None else tuple(v for v in order if v in vals)
    return Selection(
        projects=pick(args.projects, PROJECTS),
        models=pick(args.models, MODELS),
        arms=pick(args.arms, ANCHOR_ARMS),
        stories=frozenset(args.stories) if args.stories else None,
        repeats=args.repeats,
    )


def _print_dry_run_summary(sel: Selection, jobs: Sequence[Job]) -> None:
    print(f"dry run: {len(jobs)} generations, {sel.repeats} repeat(s)")
    counts: dict[str, int] = {}
    for j in jobs:
        counts[f"{j.model} {j.arm}"] = counts.get(f"{j.model} {j.arm}", 0) + 1
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    tin, tout, usd = estimate_cost(jobs)
    longest = max(estimate_prompt_tokens(j.system, j.user_sent) for j in jobs)
    print(f"estimated tokens: {tin} input, {tout} output (from the stored local counts)")
    print(f"estimated cost: {usd:.4f} USD (estimate only)")
    print(f"longest prompt: about {longest} tokens (bytes4)")


def _anchor(args: argparse.Namespace, argv: Sequence[str], *, guard: WriteGuard, runs_root: Path | None,
            client_factory: Callable[[ModelPin], OpenRouterClient] | None,
            sleep: Callable[[float], None], clock: Callable[[], float]) -> int:
    sel = build_selection(args)
    run_dir = (runs_root or TUNING_ROOT) / args.run_name
    try:
        jobs = OldConfigurationAnchor().jobs(sel)
    except (PassagesError, SelectionError) as exc:
        print(f"[FAIL] stored prompts: {type(exc).__name__}: {_cut(str(exc), 400)}")
        return 2
    if args.dry_run:
        try:
            _print_dry_run_summary(sel, jobs)
        except GuardError as exc:
            print(f"[FAIL] cost estimate: {exc}")
            return 2
    checks = preflight(sel, jobs, run_dir, dry_run=args.dry_run, resume=args.resume,
                       max_cost_usd=args.max_cost_usd, skip_hash_lists=args.skip_hash_lists, guard=guard,
                       max_attempts=args.max_attempts)
    print_checks(checks)
    if any(c.status == "FAIL" for c in checks):
        print("refused: a check failed. Nothing was sent.")
        return 2
    if args.dry_run:
        print("key: not read, no network call made")
        return 0

    factory = client_factory
    if factory is None:
        try:
            key = load_api_key()
        except FatalHostedError as exc:
            print(f"[FAIL] key: {_cut(str(exc), 400)}")
            return 2
        factory = lambda pin: OpenRouterClient(pin, key)  # noqa: E731

    hash_before = {}
    for c in checks:
        if c.name == "hash lists (before)" and c.data is not None:
            ts = utc_now()
            hash_before = {n: _hash_summary(r, ts) for n, r in c.data.items()}
    _, _, estimate_usd = estimate_cost(jobs)
    git = git_info()
    code = code_hashes()
    inputs = input_hashes(jobs)
    started = utc_now()
    notes = ["resumed run"] if args.resume else []

    def manifest(state: str, stop: str | None, ended: str | None, recs: Sequence[Mapping[str, Any]],
                 summary: ExecSummary | None, after: Mapping[str, Any]) -> dict[str, Any]:
        return build_manifest(
            run_name=args.run_name, state=state, stop_reason=stop, started_utc=started, ended_utc=ended,
            argv=argv, args=args, sel=sel, jobs=jobs, estimate_usd=estimate_usd, hash_before=hash_before,
            hash_after=after, records=recs, summary=summary, notes=notes, git=git, code=code, inputs=inputs)

    def on_start() -> None:
        write_manifest(run_dir, manifest("running", None, None, _read_cells(run_dir, jobs), None, {}), guard)

    def finish(summary: ExecSummary) -> None:
        _finalize(summary, run_dir=run_dir, jobs=jobs, manifest=manifest, guard=guard, runs_root=runs_root,
                  run_name=args.run_name)

    summary = execute_detailed(
        jobs, run_dir, workers=args.workers, max_cost_usd=args.max_cost_usd, max_minutes=args.max_minutes,
        max_attempts=args.max_attempts, client_factory=factory, guard=guard, sleep=sleep, clock=clock,
        on_start=on_start, on_finish=finish)
    return summary.exit_code


def _finalize(summary: ExecSummary, *, run_dir: Path, jobs: Sequence[Job],
              manifest: Callable[..., dict[str, Any]], guard: WriteGuard, runs_root: Path | None,
              run_name: str, builder: PromptBuilder | None = None) -> None:
    """After the last job, with the run lock still held: hash lists, final manifest, report."""
    exit_code, stop = summary.exit_code, summary.stop_reason
    after_results = check_all_hash_lists()
    ts = utc_now()
    hash_after = {n: _hash_summary(r, ts) for n, r in after_results.items()}
    bad = [p for r in after_results.values() for p in (*r.changed, *r.missing)]
    if any(not r.ok for r in after_results.values()):
        stop, exit_code = "registered files changed", 3
        print("registered files changed or missing: " + ", ".join(bad[:10]))
    state = "complete" if exit_code == 0 else "stopped"
    recs = _read_cells(run_dir, jobs)
    write_manifest(run_dir, manifest(state, stop, utc_now(), recs, summary, hash_after), guard)
    print(f"{state}: {summary.final_ok} ok, {summary.final_failed} failed, {summary.not_final} not final, "
          f"cost {summary.cost_usd:.4f} USD (can pass the cap by up to one generation per worker)")
    if stop:
        print(f"stop reason: {stop}")
    if summary.not_final == 0 and summary.final_ok + summary.final_failed == len(jobs):
        cmd_report(run_name, guard=guard, runs_root=runs_root, builder=builder)
    summary.exit_code, summary.stop_reason = exit_code, stop


def main(
    argv: Sequence[str] | None = None,
    *,
    guard: WriteGuard = DEFAULT_GUARD,
    runs_root: Path | None = None,
    client_factory: Callable[[ModelPin], OpenRouterClient] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    lab_builder: PromptBuilder | None = None,
) -> int:
    """Entry point. The keyword parameters are for tests only (guard, output root, fake client)."""
    argv_list = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(argv_list)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if args.command == "anchor":
        return _anchor(args, argv_list, guard=guard, runs_root=runs_root, client_factory=client_factory,
                       sleep=sleep, clock=clock)
    if args.command == "report":
        return cmd_report(args.run_name, guard=guard, runs_root=runs_root)
    return _lab(args, argv_list, guard=guard, runs_root=runs_root, client_factory=client_factory,
                sleep=sleep, clock=clock, builder=lab_builder)


if __name__ == "__main__":
    sys.exit(main())
