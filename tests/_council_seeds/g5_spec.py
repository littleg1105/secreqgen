from pathlib import Path

import pytest
from typer.testing import CliRunner

from secreqgen.cli import app


def test_g5_compare_runs_subcommand_exists() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["evaluate", "compare-runs", "--help"])
    assert result.exit_code == 0


def test_g5_compare_runs_help_documents_runs_and_pilot_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["evaluate", "compare-runs", "--help"])
    output = (result.stdout or "") + (result.stderr or "")
    assert "--runs" in output
    assert "--pilot" in output


def test_g5_compare_runs_help_documents_threshold_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["evaluate", "compare-runs", "--help"])
    output = (result.stdout or "") + (result.stderr or "")
    assert "--match-threshold" in output
    assert "--partial-threshold" in output


def test_g5_aggregator_exported_from_evaluation_package() -> None:
    from secreqgen.evaluation import build_multi_run_report
    assert callable(build_multi_run_report)


def test_g5_compare_runs_rejects_missing_input_files(tmp_path: Path) -> None:
    runner = CliRunner()
    nonexistent = tmp_path / "missing.json"
    result = runner.invoke(
        app,
        [
            "evaluate",
            "compare-runs",
            str(nonexistent),
            str(nonexistent),
            "--runs",
            "2",
        ],
    )
    assert result.exit_code != 0
