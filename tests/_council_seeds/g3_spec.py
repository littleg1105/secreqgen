from pathlib import Path
from unittest.mock import MagicMock

import pytest

from secreqgen.evaluation.multi_run import run_multi_evaluation


def test_g3_run_multi_evaluation_calls_pipeline_n_times(tmp_path: Path) -> None:
    pipeline = MagicMock()
    pipeline.run.return_value = MagicMock()
    stories = tmp_path / "stories.json"
    ground_truth = tmp_path / "ground_truth.json"
    stories.write_text("{}")
    ground_truth.write_text("{}")

    results = run_multi_evaluation(
        pipeline=pipeline,
        stories_path=stories,
        ground_truth_path=ground_truth,
        n_runs=5,
    )

    assert pipeline.run.call_count == 5
    assert len(results) == 5


def test_g3_run_multi_evaluation_returns_per_run_results_in_order(tmp_path: Path) -> None:
    pipeline = MagicMock()
    sentinels = [MagicMock(name="run_" + str(i)) for i in range(4)]
    pipeline.run.side_effect = sentinels
    stories = tmp_path / "stories.json"
    ground_truth = tmp_path / "ground_truth.json"
    stories.write_text("{}")
    ground_truth.write_text("{}")

    results = run_multi_evaluation(
        pipeline=pipeline,
        stories_path=stories,
        ground_truth_path=ground_truth,
        n_runs=4,
    )

    assert results == sentinels


def test_g3_run_multi_evaluation_invokes_progress_callback(tmp_path: Path) -> None:
    pipeline = MagicMock()
    pipeline.run.return_value = MagicMock()
    stories = tmp_path / "stories.json"
    ground_truth = tmp_path / "ground_truth.json"
    stories.write_text("{}")
    ground_truth.write_text("{}")

    callback_args: list[tuple[int, int, str]] = []

    def cb(current: int, total: int, phase: str) -> None:
        callback_args.append((current, total, phase))

    run_multi_evaluation(
        pipeline=pipeline,
        stories_path=stories,
        ground_truth_path=ground_truth,
        n_runs=3,
        progress_callback=cb,
    )

    assert len(callback_args) >= 1
    assert all(total == 3 for _, total, _ in callback_args)
