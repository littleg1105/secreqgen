"""The generations on the hosted models for an outside product (a product that tuning never used).

It sends the same kind of request as the prompt lab (run_tuning.py, subcommand "run"): same client,
same model pins, same privacy guard, same retries, same parser, same record fields. The passages
come from the frozen file made by scripts/xproject_retrieve.py. Records go to
tuning/<run name>/<project>/<model folder>/records.jsonl, so tuning_lib.judging.build_batches works
on the run with no change.

Not made for an outside product: the check of v0_old against a stored prompt, the split of
development and hold-out stories, the lab report with name measures (no fact sheet exists).
Use --dry-run to make all checks with no key read and no network call.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import random
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_tuning as rt  # noqa: E402
from tuning_lib.guard import (  # noqa: E402
    DEFAULT_GUARD,
    OTHER_SCRIPT_NAMES,
    LOCK_NAME,
    GuardError,
    WriteGuard,
    find_other_experiment_processes,
)
from tuning_lib.openrouter_client import FatalHostedError, ModelPin, OpenRouterClient, build_request_body  # noqa: E402
from tuning_lib.passages import PassagesError, StoredPrompt, estimate_prompt_tokens  # noqa: E402
from tuning_lib.paths import EXTRA_MODELS, FALLBACK_CONTEXT, MODELS, RTC, SECREQGEN, TUNING_ROOT, model_slug  # noqa: E402
from tuning_lib.prompts import BASELINE, TEMPLATE_FILES, PromptError, check_generality  # noqa: E402
from tuning_lib.records import key_states, read_jsonl  # noqa: E402
from tuning_lib import search as search_lib  # noqa: E402
from tuning_lib.xproject import (  # noqa: E402
    block_model,
    ALLOWED_PRODUCTS,
    XPROJECT_ROOT,
    XProjectBuilder,
    XProjectError,
    frozen_path,
)

ANSWER_TOKENS_ESTIMATE = 2100  # answer tokens per generation (anchor run: 252k tokens / 120 answers)
OUR_SCRIPTS: tuple[str, ...] = ("xproject_retrieve.py", "xproject_run.py")
X_ARM_NOTE = "with the frozen passages of an outside product"
X_CHECK_TEXT = "built from the template folder and the frozen passages of an outside product; no stored prompt to compare"


def _bytes4(*texts: str) -> int:
    return estimate_prompt_tokens(*texts, method="bytes4")


def _rel_rtc(path: Path) -> str:
    """Path relative to real_test_cases (records and manifests hold no absolute path)."""
    resolved = path.resolve()
    if RTC.resolve() in resolved.parents:
        return resolved.relative_to(RTC.resolve()).as_posix()
    return "/".join(resolved.parts[-3:])


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def plan_x_jobs(
    project: str,
    versions: Sequence[str],
    models: Sequence[str],
    repeats: int,
    builder: XProjectBuilder,
    *,
    order_seed: int = 1,
    xroot: Path = XPROJECT_ROOT,
    stories: Sequence[str] | None = None,
) -> list[rt.LabJob]:
    """Build and order the jobs. Same loop and same job type as plan_lab_jobs of run_tuning.py.

    The argument stories limits the run to these story ids (default: each story of the product).

    plan_lab_jobs needs stored Study 2b prompts, so it cannot be used. A job holds a StoredPrompt
    of the new product only for its source fields and for the cost estimate.
    """
    if not versions or len(set(versions)) != len(versions):
        raise rt.SelectionError("--versions needs at least one name and no duplicate")
    infos = {name: builder.version(name) for name in versions}
    story_ids = sorted(builder.stories())
    if stories is not None:
        unknown = sorted(set(stories) - set(story_ids))
        if unknown or not stories or len(set(stories)) != len(stories):
            raise rt.SelectionError(f"--stories: unknown, empty or duplicate story ids: {unknown}")
        story_ids = sorted(stories)
    jobs: list[rt.LabJob] = []
    for repeat in range(1, repeats + 1):
        for story_id in story_ids:
            for model in models:
                order = list(versions)
                random.Random(f"{order_seed}|{repeat}|{story_id}|{model}").shuffle(order)
                for name in order:
                    built = builder.build(story_id, model, name)
                    info = infos[name]
                    tokens = _bytes4(built.system, built.user)
                    stored = StoredPrompt(
                        study="xproject", project=project, model=model, story_id=story_id, arm="B2", run=1,
                        attempt=1, system=built.system, user_sent=built.user, user_non_doc=built.user_non_doc,
                        doc_context_ref="frozen", doc_context_sha256=built.block_sha256, system_sha256=None,
                        user_sha256=None, local_prompt_tokens=tokens, local_completion_tokens=ANSWER_TOKENS_ESTIMATE,
                        local_n_requirements=0,
                        source_file=_rel_rtc(frozen_path(project, block_model(model), xroot)), source_line=1,
                    )
                    jobs.append(rt.LabJob(
                        config_id=name, step="lab", project=project, model=model, arm="B2",
                        story_id=story_id, repeat=repeat, system=built.system, user_sent=built.user,
                        user_non_doc=built.user_non_doc, doc_context_ref="frozen",
                        doc_context_sha256=built.block_sha256, stored=stored, version=name,
                        version_parent=info.parent,
                        template_sha256=tuple((f, info.template_sha256[f]) for f in TEMPLATE_FILES),
                        passage_changes=tuple(info.passage_changes), passage_block_sha256=built.block_sha256,
                        n_doc_passages=built.n_doc_passages, n_code_hits=built.n_code_hits,
                    ))
    keys = [j.key for j in jobs]
    if len(set(keys)) != len(keys):
        raise rt.SelectionError("two jobs have the same key")
    return jobs


# ---------------------------------------------------------------------------
# Records: the lab code writes "study2b" as source; an outside product says so
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def outside_product_records() -> Iterator[None]:
    """Inside this block, run_job of run_tuning.py marks its records as records of an outside product.

    Only the text fields source_study, arm_description and prompt_check change. The request, the
    retries and the parsing stay as they are.
    """
    original = rt.run_job

    def wrapped(job: rt.Job, client: Any, pin: ModelPin, **kw: Any) -> rt.JobOutcome:
        outcome = original(job, client, pin, **kw)
        for rec in outcome.records:
            rec["source_study"] = "xproject"
            rec["arm_description"] = rec["arm_description"].replace("with the stored Study 2b passages", X_ARM_NOTE)
            rec["prompt_check"] = X_CHECK_TEXT
        return outcome

    rt.run_job = wrapped  # type: ignore[assignment]
    try:
        yield
    finally:
        rt.run_job = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def preflight_x(
    sel: rt.Selection, jobs: Sequence[rt.LabJob], versions: Sequence[str], run_dir: Path, *,
    resume: bool, max_cost_usd: float, skip_hash_lists: bool, guard: WriteGuard, max_attempts: int,
    builder: XProjectBuilder, stories_spec: str, order_seed: int,
) -> list[rt.Check]:
    """Every check before any request. Nothing is written."""
    checks: list[rt.Check] = []

    def add(name: str, fn: Callable[[], rt.Check]) -> None:
        checks.append(rt._check_step(name, fn))

    def c_folder() -> rt.Check:
        guard.check(run_dir)
        if not rt.RUN_NAME_RE.match(run_dir.name) or run_dir.name.casefold() in rt.FORBIDDEN_RUN_NAMES:
            return rt.Check("run folder", "FAIL", "the run name is not allowed")
        exists = run_dir.exists()
        if exists and not resume:
            return rt.Check("run folder", "FAIL", "the run folder exists; use --resume or another --run-name")
        if resume and not exists:
            return rt.Check("run folder", "FAIL", "--resume needs an existing run folder")
        if resume:
            mpath = run_dir / "manifest.json"
            if not mpath.is_file():
                return rt.Check("run folder", "FAIL", "--resume needs manifest.json in the run folder")
            bad = rt._lab_resume_mismatches(
                json.loads(mpath.read_text(encoding="utf-8")), sel=sel, versions=versions,
                stories=sorted(sel.stories or ()), spec=stories_spec, order_seed=order_seed,
                max_attempts=max_attempts, builder=builder)  # type: ignore[arg-type]
            if bad:
                return rt.Check("run folder", "FAIL", "the run differs from the manifest: " + ", ".join(bad))
        return rt.Check("run folder", "PASS", "allowed by the guard")

    def c_hash() -> rt.Check:
        if skip_hash_lists:
            return rt.Check("hash lists (before)", "SKIP", "skipped (dry run only)")
        results = rt.check_all_hash_lists()
        bad = [n for n, r in results.items() if not r.ok]
        detail = ", ".join(f"{n}: {r.n_listed} listed" for n, r in results.items())
        if bad:
            return rt.Check("hash lists (before)", "FAIL", "not ok: " + ", ".join(bad), data=results)
        return rt.Check("hash lists (before)", "PASS", detail, data=results)

    def c_procs() -> rt.Check:
        found = find_other_experiment_processes(rt.list_processes(), names=(*OTHER_SCRIPT_NAMES, *OUR_SCRIPTS))
        if found:
            return rt.Check("other processes and lock", "FAIL", f"another experiment process runs: pid {found[0].pid}")
        if (run_dir / LOCK_NAME).exists():
            return rt.Check("other processes and lock", "FAIL", "a lock file exists in the run folder")
        return rt.Check("other processes and lock", "PASS", "no other experiment process, no lock file")

    def c_versions() -> rt.Check:
        lines = []
        for name in versions:
            rep = check_generality(name, builder=builder)
            if not rep.ok:
                hits = "; ".join(f"{h.kind} {h.term!r} {h.file}:{h.line}" for h in rep.new_hits[:10])
                return rt.Check("prompt versions", "FAIL", f"{name} has new generality hits: {hits}")
            lines.append(f"{name} ({len(rep.inherited_hits)} inherited hit(s))")
        return rt.Check("prompt versions", "PASS", ", ".join(lines))

    def c_passages() -> rt.Check:
        for job in jobs:
            if job.n_doc_passages < 1:
                raise PassagesError(f"{job.story_id} {job.model} {job.version}: no documentation passage")
            if job.doc_context_sha256 == rt.sha256_text(FALLBACK_CONTEXT):
                raise PassagesError(f"{job.story_id} {job.version}: the block is the fallback text")
        return rt.Check("frozen passages", "PASS", f"{len(jobs)} jobs hold at least one documentation passage")

    def c_bodies() -> rt.Check:
        for job in jobs:
            pin = rt.HOSTED_MODELS.get(job.model)
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
        return rt.Check("request bodies", "PASS", f"{len(jobs)} bodies built, privacy guard passed")

    def c_size() -> rt.Check:
        longest = 0
        for job in jobs:
            est = _bytes4(job.system, job.user_sent)
            longest = max(longest, est)
            if est > rt.MAX_PROMPT_TOKENS_EST:
                raise GuardError(f"prompt of {job.story_id} {job.version} has about {est} tokens (limit {rt.MAX_PROMPT_TOKENS_EST})")
        return rt.Check("prompt size", "PASS", f"longest {longest} tokens (bytes4)")

    def c_cost() -> rt.Check:
        tin, tout, usd = rt.lab_estimate_cost(jobs)
        if usd > max_cost_usd:
            return rt.Check("cost estimate", "FAIL", f"{usd:.4f} USD is above the cap {max_cost_usd:.2f} USD")
        return rt.Check("cost estimate", "PASS", f"about {usd:.4f} USD ({tin} input, {tout} output tokens; estimate only)")

    add("run folder", c_folder)
    add("hash lists (before)", c_hash)
    add("other processes and lock", c_procs)
    add("prompt versions", c_versions)
    add("frozen passages", c_passages)
    add("request bodies", c_bodies)
    add("prompt size", c_size)
    for model in sel.models:
        pin = rt.HOSTED_MODELS.get(model)
        if pin is None:
            checks.append(rt.Check(f"pin {model}", "FAIL", "no hosted pin for this model"))
        elif not pin.confirmed:
            checks.append(rt.Check(f"pin {model}", "WARN", f"{pin.provider_tag} is not confirmed by a first call"))
        else:
            checks.append(rt.Check(f"pin {model}", "PASS", f"{pin.provider_tag} confirmed"))
    unconfirmed = [m for m in sel.models if m in rt.HOSTED_MODELS and not rt.HOSTED_MODELS[m].confirmed]
    if unconfirmed and len(jobs) > rt.SMOKE_JOB_LIMIT:
        checks.append(rt.Check("pins confirmed", "FAIL", f"{len(jobs)} generations with unconfirmed pins."))
    add("cost estimate", c_cost)
    return checks


# ---------------------------------------------------------------------------
# Manifest and report
# ---------------------------------------------------------------------------


def x_input_hashes(project: str, models: Sequence[str], xroot: Path, rtc_root: Path = RTC) -> dict[str, Any]:
    """SHA-256 of the frozen files and of the story file (paths relative to real_test_cases)."""
    out: dict[str, str] = {}
    for model in models:
        path = frozen_path(project, block_model(model), xroot)
        out[_rel_rtc(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    stories = rtc_root / project / "user_stories.json"
    search: dict[str, str] = {}  # the stored passages of the new search options of this product, if made
    for option in sorted(search_lib.OPTIONS):
        paths = [search_lib.frozen_path(option, project=project)]
        if project in search_lib.PROJECTS:
            paths.append(search_lib.other_stories_path(option, project))
        for path in paths:
            if path.is_file():
                search[_rel_rtc(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "frozen_sha256": out,
        "search_frozen_sha256": search,
        "user_stories_sha256": hashlib.sha256(stories.read_bytes()).hexdigest(),
    }


def x_code_hashes(builder: XProjectBuilder, versions: Sequence[str]) -> dict[str, str]:
    """Code hashes of the lab, the version folders and the two outside-product scripts."""
    out = rt.lab_code_hashes(builder, versions)
    for name in ("xproject_run.py", "xproject_retrieve.py"):
        path = SECREQGEN / "scripts" / name
        if path.is_file():
            out[f"scripts/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    path = SECREQGEN / "scripts" / "tuning_lib" / "xproject.py"
    out["scripts/tuning_lib/xproject.py"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def render_x_report(project: str, jobs: Sequence[rt.LabJob], records: Sequence[Mapping[str, Any]], max_attempts: int) -> str:
    """A short report: for each version and model, good answers of planned, failed attempts, requirements, cost."""
    states = key_states(records)
    by_key = {(r["config_id"], r["model"], r["story_id"], r["arm"], r["run"]): r for r in records if r["error"] is None}
    lines = [f"# Report of an outside-product run ({project})", "",
             "No name measures: this product has no fact sheet.", "",
             "| Version | Model | Good answers of planned | Failed attempts | Requirements per answer | Cost (USD) |",
             "|---|---|---|---|---|---|"]
    groups = sorted({(j.version, j.model) for j in jobs})
    for version, model in groups:
        mine = [j for j in jobs if (j.version, j.model) == (version, model)]
        good = [j for j in mine if (st := states.get(j.key)) is not None and st.ok]
        failed = sum(1 for r in records if r["config_id"] == version and r["model"] == model and r["error"] is not None)
        counts = [by_key[j.key]["n_requirements"] for j in good if j.key in by_key]
        mean = f"{sum(counts) / len(counts):.2f}" if counts else "n/a"
        cost = sum(float(r.get("cost_usd") or 0.0) for r in records if r["config_id"] == version and r["model"] == model)
        lines.append(f"| {version} | {model} | {len(good)} of {len(mine)} | {failed} | {mean} | {cost:.4f} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line and main
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line. A bad value exits with code 2."""
    p = argparse.ArgumentParser(prog="xproject_run.py", description=__doc__.splitlines()[0])
    p.add_argument("--project", required=True, choices=sorted(ALLOWED_PRODUCTS))
    p.add_argument("--run-name", required=True, type=rt._run_name)
    p.add_argument("--versions", required=True, type=rt._csv)
    p.add_argument("--stories", type=rt._csv, default=None, help="only these story ids (default: each story of the product)")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--models", type=rt._csv, default=["both"])
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--max-cost-usd", type=float, default=1.00)
    p.add_argument("--max-minutes", type=float, default=60.0)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--order-seed", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args(argv)
    if len(set(args.versions)) != len(args.versions):
        p.error("--versions has a duplicate")
    if args.repeats < 1:
        p.error("--repeats must be at least 1")
    if not 1 <= args.workers <= 16:
        p.error("--workers must be from 1 to 16")
    if not args.max_cost_usd > 0 or not args.max_minutes > 0:
        p.error("--max-cost-usd and --max-minutes must be above 0")
    if not 1 <= args.max_attempts <= 3:
        p.error("--max-attempts must be from 1 to 3")
    if args.models != ["both"]:
        bad = [m for m in args.models if m not in MODELS + EXTRA_MODELS]
        if bad:
            p.error(f"--models: {', '.join(bad)} not allowed; choose from both or {', '.join(MODELS + EXTRA_MODELS)}")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    guard: WriteGuard = DEFAULT_GUARD,
    runs_root: Path | None = None,
    client_factory: Callable[[ModelPin], OpenRouterClient] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    builder: XProjectBuilder | None = None,
    xroot: Path = XPROJECT_ROOT,
) -> int:
    """Entry point. Returns 0, 2 (refused, nothing sent) or 3 (stopped, can be resumed)."""
    argv_list = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(argv_list)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    root = runs_root or TUNING_ROOT
    run_dir = root / args.run_name
    versions = list(args.versions)
    try:
        builder = builder or XProjectBuilder((args.project,), xroot=xroot)
        models = MODELS if args.models == ["both"] else tuple(m for m in MODELS + EXTRA_MODELS if m in args.models)
        bad = [m for m in models if m not in rt.HOSTED_MODELS]
        if bad:
            print(f"[FAIL] --models: no hosted pin for {', '.join(bad)}")
            return 2
        story_ids = sorted(builder.stories())
        if args.stories is not None:
            unknown = sorted(set(args.stories) - set(story_ids))
            if unknown or not args.stories:
                print(f"[FAIL] --stories: unknown or empty story ids: {unknown}")
                return 2
            story_ids = sorted(set(args.stories))
        sel = rt.Selection((args.project,), models, ("B2",), frozenset(story_ids), args.repeats)
        jobs = plan_x_jobs(
            args.project, versions, models, args.repeats, builder, order_seed=args.order_seed, xroot=xroot,
            stories=story_ids if args.stories is not None else None,
        )
    except (PromptError, XProjectError, rt.SelectionError, PassagesError, GuardError) as exc:
        print(f"[FAIL] plan: {type(exc).__name__}: {rt._cut(str(exc), 400)}")
        return 2
    spec = f"xproject:{args.project}" + (":" + ",".join(story_ids) if args.stories is not None else "")
    if args.dry_run:
        try:
            rt._print_lab_dry_run(sel, jobs, versions)
        except GuardError as exc:
            print(f"[FAIL] cost estimate: {exc}")
            return 2
    checks = preflight_x(
        sel, jobs, versions, run_dir, resume=args.resume, max_cost_usd=args.max_cost_usd,
        skip_hash_lists=False, guard=guard, max_attempts=args.max_attempts, builder=builder,
        stories_spec=spec, order_seed=args.order_seed)
    rt.print_checks(checks)
    if any(c.status == "FAIL" for c in checks):
        print("refused: a check failed. Nothing was sent.")
        return 2
    if args.dry_run:
        print("key: not read, no network call made")
        return 0

    factory = client_factory
    if factory is None:
        try:
            key = rt.load_api_key()
        except FatalHostedError as exc:
            print(f"[FAIL] key: {rt._cut(str(exc), 400)}")
            return 2
        factory = lambda pin: rt.OpenRouterClient(pin, key)  # noqa: E731

    hash_before: dict[str, Any] = {}
    for c in checks:
        if c.name == "hash lists (before)" and c.data is not None:
            ts = rt.utc_now()
            hash_before = {n: rt._hash_summary(r, ts) for n, r in c.data.items()}
    _, _, estimate_usd = rt.lab_estimate_cost(jobs)
    git, code = rt.git_info(), x_code_hashes(builder, versions)
    inputs = x_input_hashes(args.project, models, xroot, builder.rtc_root)
    started = rt.utc_now()
    notes = (["resumed run"] if args.resume else []) + [
        f"outside product {args.project}: passages from a frozen file, no stored prompt, no fact sheet"]

    def manifest(state: str, stop: str | None, ended: str | None, recs: Sequence[Mapping[str, Any]],
                 summary: rt.ExecSummary | None, after: Mapping[str, Any]) -> dict[str, Any]:
        m = rt.build_lab_manifest(
            run_name=args.run_name, state=state, stop_reason=stop, started_utc=started, ended_utc=ended,
            argv=argv_list, args=args, sel=sel, jobs=jobs, estimate_usd=estimate_usd, hash_before=hash_before,
            hash_after=after, records=recs, summary=summary, notes=notes, git=git, code=code, inputs=inputs,
            builder=builder, versions=versions, stories_spec=spec, holdout_used=[], final_check=False)  # type: ignore[arg-type]
        m["configuration"]["source"] = (
            "prompt versions below scripts/tuning_lib/prompt_versions, with the frozen passages of an outside product")
        m["options"]["project"] = args.project
        return m

    def on_start() -> None:
        rt.write_manifest(run_dir, manifest("running", None, None, rt._read_cells(run_dir, jobs), None, {}), guard)

    def finish(summary: rt.ExecSummary) -> None:
        after_results = rt.check_all_hash_lists()
        ts = rt.utc_now()
        hash_after = {n: rt._hash_summary(r, ts) for n, r in after_results.items()}
        exit_code, stop = summary.exit_code, summary.stop_reason
        if any(not r.ok for r in after_results.values()):
            exit_code, stop = 3, "registered files changed"
            print("registered files changed or missing")
        state = "complete" if exit_code == 0 else "stopped"
        recs = rt._read_cells(run_dir, jobs)
        rt.write_manifest(run_dir, manifest(state, stop, rt.utc_now(), recs, summary, hash_after), guard)
        rt.safe_write(run_dir / "report.md", render_x_report(args.project, jobs, recs, args.max_attempts), guard=guard)
        print(f"{state}: {summary.final_ok} ok, {summary.final_failed} failed, {summary.not_final} not final, "
              f"cost {summary.cost_usd:.4f} USD")
        if stop:
            print(f"stop reason: {stop}")
        summary.exit_code, summary.stop_reason = exit_code, stop

    with outside_product_records():
        summary = rt.execute_detailed(
            jobs, run_dir, workers=args.workers, max_cost_usd=args.max_cost_usd, max_minutes=args.max_minutes,
            max_attempts=args.max_attempts, client_factory=factory, guard=guard, sleep=sleep, clock=clock,
            on_start=on_start, on_finish=finish)
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
