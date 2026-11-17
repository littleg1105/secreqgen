"""Commands of the evaluate group."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

from secreqgen.cli._app import console, evaluate_app
from secreqgen.config import get_config


@evaluate_app.command("run")
def evaluate_run(
    stories: Annotated[
        Path, typer.Argument(help="User stories JSON file")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth JSON file")
    ],
    output_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output directory for reports",
        ),
    ] = None,
    match_threshold: Annotated[
        float,
        typer.Option(
            "--match-threshold",
            help="Similarity threshold for match (default 0.85)",
        ),
    ] = 0.85,
    partial_threshold: Annotated[
        float,
        typer.Option(
            "--partial-threshold",
            help="Similarity threshold for partial match (default 0.70)",
        ),
    ] = 0.70,
    improvement_threshold: Annotated[
        float,
        typer.Option(
            "--improvement-threshold",
            help="F1 improvement factor for thesis success (default 1.20 = 20%)",
        ),
    ] = 1.20,
    context: Annotated[
        Optional[Path],
        typer.Option(
            "--context",
            "-c",
            help="Optional system context YAML file",
        ),
    ] = None,
    report_name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Base name for report files",
        ),
    ] = "evaluation",
    model: Annotated[
        Optional[str],
        typer.Option(
            "--model",
            "-m",
            help="Ollama model for generation (default: llama3:8b). Try qwen3:30b for better results.",
        ),
    ] = None,
    with_verification: Annotated[
        bool,
        typer.Option(
            "--with-verification",
            help="Enable LLM-as-Judge verification layer to filter low-quality requirements",
        ),
    ] = False,
    verification_threshold: Annotated[
        float,
        typer.Option(
            "--verification-threshold",
            help="Minimum average score (1-10) to keep a requirement (default: 6.5)",
        ),
    ] = 6.5,
) -> None:
    """Run full evaluation pipeline: RAG vs baseline comparison.

    Runs both baseline (zero-shot) and RAG-enhanced generation on the input
    stories, matches results against ground truth, and generates comparison
    reports with Precision/Recall/F1 metrics.

    Example:
        secreqgen evaluate run stories.json ground_truth.json -o docs/evaluation_results
    """
    from secreqgen.application.clients import build_evaluation_client
    from secreqgen.application.retrievers import build_vector_retriever
    from secreqgen.evaluation import (
        EvaluationPipeline,
        EvaluationReportGenerator,
    )
    from secreqgen.exceptions import GenerationError, LLMConnectionError

    # Validate files
    if not stories.exists():
        console.print(f"[red]Error:[/red] Stories file not found: {stories}")
        raise typer.Exit(1)

    if not ground_truth.exists():
        console.print(f"[red]Error:[/red] Ground truth file not found: {ground_truth}")
        raise typer.Exit(1)

    cfg = get_config()

    # Use specified model or default from config
    model_name = model if model else cfg.llm.model_name

    # Default output directory
    if output_dir is None:
        output_dir = Path("docs/evaluation_results")

    # Build status panel content
    panel_content = (
        f"[cyan]Stories:[/cyan] {stories}\n"
        f"[cyan]Ground Truth:[/cyan] {ground_truth}\n"
        f"[cyan]Output:[/cyan] {output_dir}\n"
        f"[cyan]Match Threshold:[/cyan] {match_threshold}\n"
        f"[cyan]Improvement Target:[/cyan] {(improvement_threshold - 1) * 100:.0f}%\n"
        f"[cyan]Model:[/cyan] {model_name}"
    )
    if with_verification:
        panel_content += f"\n[cyan]Verification:[/cyan] Enabled (threshold: {verification_threshold})"

    console.print(Panel(panel_content, title="Evaluation Pipeline"))

    # Load optional system context
    system_context = None
    if context:
        if not context.exists():
            console.print(f"[red]Error:[/red] Context file not found: {context}")
            raise typer.Exit(1)
        system_context = context.read_text()
        console.print(f"[green]✓[/green] Loaded system context from {context.name}")

    try:
        # Initialize components
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Initializing components...", total=None)

            # Initialize LLM client with specified model
            llm_client = build_evaluation_client(cfg, model_name)

            if not llm_client.check_health():
                console.print(
                    "[red]Error:[/red] Cannot connect to Ollama.\n"
                    f"Make sure Ollama is running: [cyan]ollama serve[/cyan]"
                )
                raise typer.Exit(1)

            # Initialize retriever
            retriever = build_vector_retriever(cfg)

            # Initialize pipeline
            pipeline = EvaluationPipeline(
                llm_client=llm_client,
                retriever=retriever,
                match_threshold=match_threshold,
                partial_threshold=partial_threshold,
                improvement_threshold=improvement_threshold,
                system_context=system_context,
            )

        console.print("[green]✓[/green] Components initialized")

        # Run evaluation with progress tracking
        console.print("\n[bold]Running evaluation pipeline...[/bold]\n")

        def progress_callback(phase: str, current: int, total: int) -> None:
            """Display progress updates."""
            phase_names = {
                "loading": "Loading data",
                "baseline": "Baseline generation",
                "rag": "RAG generation",
                "matching": "Matching results",
            }
            phase_display = phase_names.get(phase, phase)
            if total > 0:
                console.print(
                    f"  {phase_display}: {current}/{total}",
                    end="\r",
                )

        result = pipeline.run(
            stories_path=stories,
            ground_truth_path=ground_truth,
            progress_callback=progress_callback,
        )

        console.print("\n")  # Clear progress line

        # Display summary
        console.print(result.summary())

        # Generate reports
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Generating reports...", total=None)

            report_generator = EvaluationReportGenerator(output_dir)
            report_paths = report_generator.generate_full_report(result, report_name)

        console.print("\n[bold]Reports generated:[/bold]")
        for path in report_paths:
            console.print(f"  • {path}")

        # Final verdict
        if result.meets_thesis_target:
            console.print(
                "\n[bold green]✓ THESIS SUCCESS:[/bold green] "
                f"RAG shows {result.comparison.f1_improvement_pct:+.1f}% F1 improvement!"
            )
        else:
            console.print(
                "\n[bold yellow]✗ THESIS TARGET NOT MET:[/bold yellow] "
                f"RAG shows {result.comparison.f1_improvement_pct:+.1f}% F1 change "
                f"(target: {(improvement_threshold - 1) * 100:.0f}%)"
            )

    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except GenerationError as e:
        console.print(f"[red]Generation Error:[/red] {e}")
        raise typer.Exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]File Error:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Validation Error:[/red] {e}")
        raise typer.Exit(1)


@evaluate_app.command("compare-runs")
def evaluate_compare_runs(
    stories: Annotated[
        Path, typer.Argument(help="User stories JSON file")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth JSON file")
    ],
    runs: Annotated[
        int,
        typer.Option(
            "--runs",
            help="Number of repeated runs (default 20). Use --pilot for N=10.",
        ),
    ] = 20,
    pilot: Annotated[
        bool,
        typer.Option(
            "--pilot",
            help="Pilot mode: run N=10 and mark output as pilot (not thesis-grade).",
        ),
    ] = False,
    output_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output directory for the multi-run report",
        ),
    ] = None,
    match_threshold: Annotated[
        float,
        typer.Option(
            "--match-threshold",
            help="Similarity threshold for match (default 0.85)",
        ),
    ] = 0.85,
    partial_threshold: Annotated[
        float,
        typer.Option(
            "--partial-threshold",
            help="Similarity threshold for partial match (default 0.70)",
        ),
    ] = 0.70,
    context: Annotated[
        Optional[Path],
        typer.Option(
            "--context",
            "-c",
            help="Optional system context YAML file",
        ),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option(
            "--model",
            "-m",
            help="Ollama model for generation (default from config)",
        ),
    ] = None,
) -> None:
    """Run the evaluation pipeline N times and report aggregate statistics.

    Default N=20 produces thesis-grade variance characterization. Use
    --pilot for N=10 during development; pilot reports are flagged as
    such and should not be cited as headline results.

    Reports two paired tests: per-story (PRIMARY) and per-run
    (SENSITIVITY), with Wilcoxon as the primary inferential test and
    paired t-test as a sensitivity check. Effect size is paired Cohen's
    d_z. A Shapiro-Wilk normality check on the paired differences is
    reported alongside so reviewers can interpret the sensitivity
    result.

    Example:
        secreqgen evaluate compare-runs stories.json ground_truth.json --runs 20
    """
    from secreqgen.application.clients import build_evaluation_client
    from secreqgen.application.retrievers import build_vector_retriever
    from secreqgen.evaluation import (
        EvaluationPipeline,
        build_multi_run_report,
        run_multi_evaluation,
        serialize_individual_result,
        serialize_report,
    )
    from secreqgen.exceptions import GenerationError, LLMConnectionError

    # Validate input files BEFORE any heavy initialization so the command
    # fails fast on bad paths without spinning up Ollama or the embedder.
    if not stories.exists():
        console.print(f"[red]Error:[/red] Stories file not found: {stories}")
        raise typer.Exit(1)
    if not ground_truth.exists():
        console.print(
            f"[red]Error:[/red] Ground truth file not found: {ground_truth}"
        )
        raise typer.Exit(1)

    if pilot:
        runs = 10

    cfg = get_config()
    model_name = model if model else cfg.llm.model_name

    if output_dir is None:
        output_dir = Path("docs/evaluation_results")

    pilot_label = " (PILOT)" if pilot else ""
    panel_content = (
        f"[cyan]Stories:[/cyan] {stories}\n"
        f"[cyan]Ground Truth:[/cyan] {ground_truth}\n"
        f"[cyan]Output:[/cyan] {output_dir}\n"
        f"[cyan]Runs:[/cyan] {runs}{pilot_label}\n"
        f"[cyan]Match Threshold:[/cyan] {match_threshold}\n"
        f"[cyan]Partial Threshold:[/cyan] {partial_threshold}\n"
        f"[cyan]Model:[/cyan] {model_name}"
    )
    console.print(Panel(panel_content, title="Multi-Run Evaluation Pipeline"))

    system_context = None
    if context:
        if not context.exists():
            console.print(f"[red]Error:[/red] Context file not found: {context}")
            raise typer.Exit(1)
        system_context = context.read_text()
        console.print(
            f"[green]✓[/green] Loaded system context from {context.name}"
        )

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Initializing components...", total=None)

            llm_client = build_evaluation_client(cfg, model_name)

            if not llm_client.check_health():
                console.print(
                    "[red]Error:[/red] Cannot connect to Ollama.\n"
                    f"Make sure Ollama is running: [cyan]ollama serve[/cyan]"
                )
                raise typer.Exit(1)

            retriever = build_vector_retriever(cfg)

            pipeline = EvaluationPipeline(
                llm_client=llm_client,
                retriever=retriever,
                match_threshold=match_threshold,
                partial_threshold=partial_threshold,
                system_context=system_context,
            )

        console.print("[green]✓[/green] Components initialized")

        console.print(f"\n[bold]Running {runs} evaluations...[/bold]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Multi-run evaluation", total=runs)

            def progress_callback(current: int, total: int, phase: str) -> None:
                if phase.startswith("completed"):
                    progress.update(
                        task,
                        completed=current,
                        description=f"Run {current}/{total} complete",
                    )
                else:
                    progress.update(
                        task, description=f"Run {current}/{total}: {phase}"
                    )

            individual_results = run_multi_evaluation(
                pipeline=pipeline,
                stories_path=stories,
                ground_truth_path=ground_truth,
                n_runs=runs,
                progress_callback=progress_callback,
            )

        console.print()

        report_config = {
            "stories": str(stories),
            "ground_truth": str(ground_truth),
            "model": model_name,
            "match_threshold": match_threshold,
            "partial_threshold": partial_threshold,
            "n_runs": runs,
            "pilot": pilot,
        }
        report = build_multi_run_report(individual_results, report_config)

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = report.run_timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        run_dir_name = (
            f"run_comparison_{timestamp}_pilot"
            if pilot
            else f"run_comparison_{timestamp}"
        )
        run_dir = output_dir / run_dir_name
        run_dir.mkdir(parents=True, exist_ok=True)

        report_json = serialize_report(report)
        json_path = run_dir / "multi_run_report.json"
        json_path.write_text(report_json, encoding="utf-8")

        # Persist the raw per-run payloads (generated text, retrieval
        # context, match records) for qualitative audit. serialize_report
        # intentionally drops these to keep the aggregate file small; this
        # is the side-channel for spot-checking the F1 numbers and
        # examining what the model actually produced.
        raw_runs_dir = run_dir / "raw_runs"
        raw_runs_dir.mkdir(parents=True, exist_ok=True)
        for idx, result in enumerate(individual_results, start=1):
            raw_path = raw_runs_dir / f"run_{idx:03d}.json"
            raw_path.write_text(
                serialize_individual_result(result), encoding="utf-8"
            )

        console.print(
            f"\n[bold]Multi-Run Summary{' (PILOT)' if pilot else ''}[/bold]\n"
        )

        primary = report.paired_test_per_story_f1
        console.print(
            "[bold cyan]Primary inferential result (per-story F1)[/bold cyan]"
        )
        console.print(
            f"  N pairs (stories): {primary.n_pairs}\n"
            f"  Wilcoxon: W = {primary.primary_statistic:.3f}, "
            f"p = {primary.primary_p_value:.4f}\n"
            f"  Paired t-test: t = {primary.sensitivity_statistic:.3f}, "
            f"p = {primary.sensitivity_p_value:.4f}\n"
            f"  Shapiro-Wilk on diffs: p = {primary.shapiro_wilk_p:.4f}\n"
            f"  Cohen's d_z: {primary.d_z:.3f} ({primary.d_z_interpretation})"
        )

        sensitivity = report.paired_test_per_run_f1
        console.print(
            "\n[bold cyan]Sensitivity result (per-run aggregate F1)[/bold cyan]"
        )
        console.print(
            f"  N pairs (runs): {sensitivity.n_pairs}\n"
            f"  Wilcoxon: W = {sensitivity.primary_statistic:.3f}, "
            f"p = {sensitivity.primary_p_value:.4f}\n"
            f"  Paired t-test: t = {sensitivity.sensitivity_statistic:.3f}, "
            f"p = {sensitivity.sensitivity_p_value:.4f}\n"
            f"  Cohen's d_z: {sensitivity.d_z:.3f} "
            f"({sensitivity.d_z_interpretation})"
        )

        console.print(f"\n[green]✓[/green] Report written to {json_path}")

    except LLMConnectionError as e:
        console.print(f"[red]LLM Connection Error:[/red] {e}")
        raise typer.Exit(1)
    except GenerationError as e:
        console.print(f"[red]Generation Error:[/red] {e}")
        raise typer.Exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]File Error:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Validation Error:[/red] {e}")
        raise typer.Exit(1)


@evaluate_app.command("compare")
def evaluate_compare(
    generated: Annotated[
        Path, typer.Argument(help="Generated requirements file (JSON)")
    ],
    ground_truth: Annotated[
        Path, typer.Argument(help="Ground truth requirements file (JSON)")
    ],
    match_threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            "-t",
            help="Similarity threshold for match (default 0.85)",
        ),
    ] = 0.85,
    partial_threshold: Annotated[
        float,
        typer.Option(
            "--partial",
            "-p",
            help="Similarity threshold for partial match (default 0.70)",
        ),
    ] = 0.70,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output file for match results (JSON)",
        ),
    ] = None,
) -> None:
    """Compare generated requirements against ground truth.

    Quick evaluation of already-generated requirements without running the
    full pipeline. Useful for comparing different generation runs.

    Expected generated format:
    {"requirements": [{"id": "...", "title": "...", "description": "..."}]}

    Expected ground truth format:
    {"requirements": [{"id": "...", "title": "...", "description": "..."}]}
    """
    import json

    from secreqgen.evaluation import (
        MatchType,
        MetricsCalculator,
        SemanticMatcher,
        load_ground_truth,
    )
    from secreqgen.generation.schemas import SecurityRequirement

    # Validate files
    if not generated.exists():
        console.print(f"[red]Error:[/red] Generated file not found: {generated}")
        raise typer.Exit(1)

    if not ground_truth.exists():
        console.print(f"[red]Error:[/red] Ground truth file not found: {ground_truth}")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[cyan]Generated:[/cyan] {generated}\n"
            f"[cyan]Ground Truth:[/cyan] {ground_truth}\n"
            f"[cyan]Match Threshold:[/cyan] {match_threshold}\n"
            f"[cyan]Partial Threshold:[/cyan] {partial_threshold}",
            title="Comparing Requirements",
        )
    )

    try:
        # Load generated requirements
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Loading files...", total=None)

            with open(generated, encoding="utf-8") as f:
                gen_data = json.load(f)

            with open(ground_truth, encoding="utf-8") as f:
                gt_data = json.load(f)

        # Parse generated requirements
        # For compare command, we accept simplified requirement format without all fields
        gen_reqs = []
        requirements_data = gen_data.get("requirements", [])
        for i, req_data in enumerate(requirements_data):
            try:
                # Use default values for optional fields to allow simpler input format
                req = SecurityRequirement(
                    id=req_data.get("id", f"GEN-{i+1:03d}"),
                    user_story_id=req_data.get("user_story_id", "unknown"),  # Default for compare
                    title=req_data.get("title", ""),
                    description=req_data.get("description", ""),
                    rationale=req_data.get("rationale", ""),  # Default for compare
                    asvs_category=req_data.get("asvs_category", "V1: Architecture, Design and Threat Modeling"),
                    priority=req_data.get("priority", "medium"),
                    implementation_notes=req_data.get("implementation_notes", ""),
                    acceptance_criteria=req_data.get("acceptance_criteria", []),
                )
                gen_reqs.append(req)
            except Exception as e:
                console.print(f"[yellow]Warning: Skipping invalid requirement {i+1}: {e}[/yellow]")

        console.print(f"[green]✓[/green] Loaded {len(gen_reqs)} generated requirements")

        # Parse ground truth
        gt_reqs = load_ground_truth(gt_data.get("requirements", []))
        console.print(f"[green]✓[/green] Loaded {len(gt_reqs)} ground truth requirements")

        if not gen_reqs:
            console.print("[yellow]No generated requirements to compare.[/yellow]")
            raise typer.Exit(0)

        # Match requirements
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Computing semantic similarity...", total=None)

            matcher = SemanticMatcher(
                match_threshold=match_threshold,
                partial_threshold=partial_threshold,
            )
            matches = matcher.match_requirements(gen_reqs, gt_reqs)

        # Calculate metrics
        calculator = MetricsCalculator()
        metrics = calculator.calculate(matches, len(gt_reqs))

        # Display results
        console.print("\n[bold]Match Results:[/bold]\n")

        match_table = Table()
        match_table.add_column("Generated", style="cyan")
        match_table.add_column("Ground Truth", style="green")
        match_table.add_column("Similarity", justify="right")
        match_table.add_column("Status", justify="center")

        for m in matches:
            status_style = {
                MatchType.MATCH: "green",
                MatchType.PARTIAL: "yellow",
                MatchType.NO_MATCH: "red",
            }[m.match_type]
            status_text = {
                MatchType.MATCH: "✓ Match",
                MatchType.PARTIAL: "~ Partial",
                MatchType.NO_MATCH: "✗ No Match",
            }[m.match_type]

            gt_display = m.ground_truth_id if m.ground_truth_id else "-"
            match_table.add_row(
                m.generated_id,
                gt_display,
                f"{m.similarity_score:.3f}",
                f"[{status_style}]{status_text}[/{status_style}]",
            )

        console.print(match_table)

        # Display metrics
        console.print("\n[bold]Metrics:[/bold]\n")
        metrics_table = Table()
        metrics_table.add_column("Metric", style="cyan")
        metrics_table.add_column("Value", justify="right")

        metrics_table.add_row("True Positives", str(metrics.true_positives))
        metrics_table.add_row("False Positives", str(metrics.false_positives))
        metrics_table.add_row("False Negatives", str(metrics.false_negatives))
        metrics_table.add_row("Partial Matches", str(metrics.partial_matches))
        metrics_table.add_row("─" * 15, "─" * 10)
        metrics_table.add_row("Precision", f"{metrics.precision:.2%}")
        metrics_table.add_row("Recall", f"{metrics.recall:.2%}")
        metrics_table.add_row("[bold]F1 Score[/bold]", f"[bold]{metrics.f1_score:.2%}[/bold]")
        metrics_table.add_row("Hallucination Rate", f"{metrics.hallucination_rate:.2%}")

        console.print(metrics_table)

        # Save results if output specified
        if output:
            output_data = {
                "config": {
                    "match_threshold": match_threshold,
                    "partial_threshold": partial_threshold,
                    "generated_file": str(generated),
                    "ground_truth_file": str(ground_truth),
                },
                "metrics": {
                    "true_positives": metrics.true_positives,
                    "false_positives": metrics.false_positives,
                    "false_negatives": metrics.false_negatives,
                    "partial_matches": metrics.partial_matches,
                    "precision": metrics.precision,
                    "recall": metrics.recall,
                    "f1_score": metrics.f1_score,
                    "hallucination_rate": metrics.hallucination_rate,
                },
                "matches": [
                    {
                        "generated_id": m.generated_id,
                        "ground_truth_id": m.ground_truth_id,
                        "similarity_score": m.similarity_score,
                        "match_type": m.match_type.value,
                    }
                    for m in matches
                ],
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)
            console.print(f"\n[green]✓[/green] Results saved to {output}")

    except json.JSONDecodeError as e:
        console.print(f"[red]JSON Parse Error:[/red] {e}")
        raise typer.Exit(1)


@evaluate_app.command("report")
def evaluate_report(
    results_file: Annotated[
        Path, typer.Argument(help="Evaluation results JSON file")
    ],
    output_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Output directory for reports",
        ),
    ] = None,
    report_name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Base name for report files",
        ),
    ] = "evaluation",
) -> None:
    """Generate reports from saved evaluation results.

    Takes a JSON file from a previous evaluation run and generates
    Markdown and JSON reports for analysis.

    The input file should be the JSON output from 'evaluate run' command.
    """
    import json
    from datetime import datetime

    from secreqgen.evaluation import (
        ComparisonResult,
        EvaluationPipelineResult,
        EvaluationReportGenerator,
        GroundTruthRequirement,
        MatchResult,
        MatchType,
        MetricsBreakdown,
        StoryEvaluationResult,
    )
    from secreqgen.evaluation.baseline import ComparativeResults

    # Validate file
    if not results_file.exists():
        console.print(f"[red]Error:[/red] Results file not found: {results_file}")
        raise typer.Exit(1)

    # Default output directory
    if output_dir is None:
        output_dir = results_file.parent

    console.print(
        Panel(
            f"[cyan]Results:[/cyan] {results_file}\n"
            f"[cyan]Output:[/cyan] {output_dir}",
            title="Generate Reports",
        )
    )

    try:
        # Load results
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Loading results...", total=None)

            with open(results_file, encoding="utf-8") as f:
                data = json.load(f)

        # Reconstruct EvaluationPipelineResult from JSON
        # This requires parsing the nested structures
        def parse_metrics(d: dict) -> MetricsBreakdown:
            tp = d["true_positives"]
            fp = d["false_positives"]
            fn = d["false_negatives"]
            partial = d["partial_matches"]
            total_gen = d.get("total_generated", tp + fp)
            total_gt = d.get("total_ground_truth", tp + fn)

            # Compute metrics if not provided (for backwards compatibility)
            precision = d.get("precision", tp / (tp + fp) if (tp + fp) > 0 else 0.0)
            recall = d.get("recall", tp / (tp + fn) if (tp + fn) > 0 else 0.0)
            f1 = d.get("f1_score", 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0)
            hallucination = d.get("hallucination_rate", fp / total_gen if total_gen > 0 else 0.0)

            return MetricsBreakdown(
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                partial_matches=partial,
                total_generated=total_gen,
                total_ground_truth=total_gt,
                precision=precision,
                recall=recall,
                f1_score=f1,
                hallucination_rate=hallucination,
            )

        def parse_match(d: dict) -> MatchResult:
            return MatchResult(
                generated_id=d["generated_id"],
                generated_text=d.get("generated_text", ""),
                ground_truth_id=d.get("ground_truth_id"),
                ground_truth_text=d.get("ground_truth_text"),
                similarity_score=d["similarity_score"],
                match_type=MatchType(d["match_type"]),
            )

        def parse_story_result(d: dict) -> StoryEvaluationResult:
            return StoryEvaluationResult(
                story_id=d["story_id"],
                story_title=d["story_title"],
                ground_truth=[
                    GroundTruthRequirement(id="", title="", description="")
                ] * d["ground_truth_count"],  # Placeholder
                baseline_result=None,
                rag_result=None,
                baseline_matches=[parse_match(m) for m in d.get("baseline_matches", [])],
                rag_matches=[parse_match(m) for m in d.get("rag_matches", [])],
                baseline_metrics=parse_metrics(d["baseline_metrics"]),
                rag_metrics=parse_metrics(d["rag_metrics"]),
            )

        # Parse the data
        aggregate_baseline = parse_metrics(data["aggregate_metrics"]["baseline"])
        aggregate_rag = parse_metrics(data["aggregate_metrics"]["rag"])

        # Compute meets_threshold from improvement_pct and threshold
        f1_improvement = data["comparison"]["f1_improvement_pct"]
        threshold = data["comparison"]["improvement_threshold"]
        meets_threshold = (1 + f1_improvement / 100.0) >= threshold

        comparison = ComparisonResult(
            baseline_metrics=aggregate_baseline,
            rag_metrics=aggregate_rag,
            precision_delta=data["comparison"]["precision_delta"],
            recall_delta=data["comparison"]["recall_delta"],
            f1_delta=data["comparison"]["f1_delta"],
            hallucination_delta=data["comparison"]["hallucination_delta"],
            precision_improvement_pct=data["comparison"]["precision_improvement_pct"],
            recall_improvement_pct=data["comparison"]["recall_improvement_pct"],
            f1_improvement_pct=f1_improvement,
            meets_threshold=meets_threshold,
            improvement_threshold=threshold,
        )

        per_story = [parse_story_result(sr) for sr in data.get("per_story_results", [])]

        # Create a minimal ComparativeResults (for compatibility)
        comparative = ComparativeResults(
            user_stories=[],  # Not available from saved results
            baseline_results=[],
            rag_results=[],
            baseline_time_ms=0,
            rag_time_ms=0,
        )

        result = EvaluationPipelineResult(
            per_story_results=per_story,
            aggregate_baseline_metrics=aggregate_baseline,
            aggregate_rag_metrics=aggregate_rag,
            comparison=comparison,
            comparative_results=comparative,
            run_timestamp=datetime.fromisoformat(data.get("run_timestamp", datetime.now().isoformat())),
            config=data.get("config", {}),
        )

        console.print(f"[green]✓[/green] Loaded results with {len(per_story)} stories")

        # Generate reports
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Generating reports...", total=None)

            report_generator = EvaluationReportGenerator(output_dir)
            report_paths = report_generator.generate_full_report(result, report_name)

        console.print("\n[bold]Reports generated:[/bold]")
        for path in report_paths:
            console.print(f"  • {path}")

    except json.JSONDecodeError as e:
        console.print(f"[red]JSON Parse Error:[/red] {e}")
        raise typer.Exit(1)
    except KeyError as e:
        console.print(f"[red]Invalid results format:[/red] Missing key {e}")
        raise typer.Exit(1)
