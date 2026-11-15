"""Tests for CLI functionality."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from secreqgen import __version__
from secreqgen.cli import app

runner = CliRunner()


def test_version() -> None:
    """Test version command."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help() -> None:
    """Test --help flag.

    The test fails when --help crashes. Typer 0.15 crashed with Click 8.2 and
    later (fixed on 2026-11-14 with typer 0.16).
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "secreqgen" in result.stdout.lower()


@pytest.mark.parametrize(
    "command",
    [
        ["ingest"],
        ["generate"],
        ["evaluate"],
        ["evaluate", "run"],
        ["evaluate", "compare-runs"],
        ["evaluate", "compare"],
        ["evaluate", "report"],
    ],
    ids=" ".join,
)
def test_command_help(command: list[str]) -> None:
    """The help of each command and sub-command exits with code 0."""
    result = runner.invoke(app, [*command, "--help"])
    assert result.exit_code == 0, result.output


def test_config_command() -> None:
    """Test config command displays configuration."""
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "Configuration" in result.stdout
    assert "Embedding Model" in result.stdout
    assert "LLM Model" in result.stdout


class TestIngestDocuments:
    """Tests for ingest documents command."""

    def test_ingest_documents_nonexistent_directory(self) -> None:
        """Test ingest documents with non-existent directory."""
        result = runner.invoke(app, ["ingest", "documents", "/nonexistent/path"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "error" in result.stdout.lower()

    def test_ingest_documents_invalid_doc_type(self, tmp_path: Path) -> None:
        """Test ingest documents with invalid document type."""
        # Create an empty directory
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()

        result = runner.invoke(
            app, ["ingest", "documents", str(docs_dir), "--type", "invalid"]
        )
        assert result.exit_code == 1
        assert "invalid" in result.stdout.lower()

    def test_ingest_documents_empty_directory(self, tmp_path: Path) -> None:
        """Test ingest documents with empty directory."""
        docs_dir = tmp_path / "empty"
        docs_dir.mkdir()

        # Use temp vector store to avoid pollution
        with tempfile.TemporaryDirectory() as vectordb_dir:
            env = os.environ.copy()
            env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = vectordb_dir

            result = runner.invoke(
                app,
                ["ingest", "documents", str(docs_dir)],
                env=env,
            )
            assert result.exit_code == 0
            assert "no supported documents" in result.stdout.lower()

    def test_ingest_documents_success(self, sample_markdown_file: Path) -> None:
        """Test successful document ingestion."""
        docs_dir = sample_markdown_file.parent

        with tempfile.TemporaryDirectory() as vectordb_dir:
            env = os.environ.copy()
            env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = vectordb_dir

            result = runner.invoke(
                app,
                ["ingest", "documents", str(docs_dir), "--type", "policies"],
                env=env,
            )

            assert result.exit_code == 0
            assert "loaded" in result.stdout.lower()
            assert "chunks" in result.stdout.lower()
            assert "complete" in result.stdout.lower()

    def test_ingest_documents_architecture_type(self, sample_markdown_file: Path) -> None:
        """Test ingesting documents as architecture type."""
        docs_dir = sample_markdown_file.parent

        with tempfile.TemporaryDirectory() as vectordb_dir:
            env = os.environ.copy()
            env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = vectordb_dir

            result = runner.invoke(
                app,
                ["ingest", "documents", str(docs_dir), "--type", "architecture"],
                env=env,
            )

            assert result.exit_code == 0
            assert "architecture" in result.stdout.lower()


class TestIngestSystemContext:
    """Tests for ingest system-context command."""

    def test_ingest_system_context_nonexistent_file(self) -> None:
        """Test ingest system-context with non-existent file."""
        result = runner.invoke(
            app, ["ingest", "system-context", "/nonexistent/context.yaml"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_ingest_system_context_not_yaml(self, tmp_path: Path) -> None:
        """Test ingest system-context with non-YAML file."""
        txt_file = tmp_path / "context.txt"
        txt_file.write_text("not yaml")

        result = runner.invoke(app, ["ingest", "system-context", str(txt_file)])
        assert result.exit_code == 1
        assert "yaml" in result.stdout.lower()

    def test_ingest_system_context_success(
        self, sample_yaml_file: Path
    ) -> None:
        """Test successful system context ingestion."""
        with tempfile.TemporaryDirectory() as vectordb_dir:
            env = os.environ.copy()
            env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = vectordb_dir

            result = runner.invoke(
                app,
                ["ingest", "system-context", str(sample_yaml_file)],
                env=env,
            )

            assert result.exit_code == 0
            assert "loaded" in result.stdout.lower()
            assert "system context" in result.stdout.lower()


class TestIngestStats:
    """Tests for ingest stats command."""

    def test_ingest_stats_no_store(self, tmp_path: Path) -> None:
        """Test stats when vector store doesn't exist."""
        # Point to non-existent directory
        nonexistent = tmp_path / "nonexistent_vectordb"

        env = os.environ.copy()
        env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = str(nonexistent)

        result = runner.invoke(app, ["ingest", "stats"], env=env)
        assert result.exit_code == 0
        assert "not initialized" in result.stdout.lower()

    def test_ingest_stats_with_data(self, sample_markdown_file: Path) -> None:
        """Test stats after ingesting documents."""
        docs_dir = sample_markdown_file.parent

        with tempfile.TemporaryDirectory() as vectordb_dir:
            env = os.environ.copy()
            env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = vectordb_dir

            # First ingest some documents
            runner.invoke(
                app,
                ["ingest", "documents", str(docs_dir), "--type", "policies"],
                env=env,
            )

            # Then check stats
            result = runner.invoke(app, ["ingest", "stats"], env=env)

            assert result.exit_code == 0
            assert "policy" in result.stdout.lower()
            assert "total" in result.stdout.lower()


class TestIngestClear:
    """Tests for ingest clear command."""

    def test_ingest_clear_no_store(self, tmp_path: Path) -> None:
        """Test clear when vector store doesn't exist."""
        nonexistent = tmp_path / "nonexistent_vectordb"

        env = os.environ.copy()
        env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = str(nonexistent)

        result = runner.invoke(app, ["ingest", "clear", "--force"], env=env)
        assert result.exit_code == 0
        assert "not initialized" in result.stdout.lower()

    def test_ingest_clear_with_force(self, sample_markdown_file: Path) -> None:
        """Test clear with --force flag."""
        docs_dir = sample_markdown_file.parent

        with tempfile.TemporaryDirectory() as vectordb_dir:
            env = os.environ.copy()
            env["SECREQGEN_VECTORSTORE_PERSIST_DIRECTORY"] = vectordb_dir

            # First ingest some documents
            runner.invoke(
                app,
                ["ingest", "documents", str(docs_dir), "--type", "policies"],
                env=env,
            )

            # Clear with force
            result = runner.invoke(app, ["ingest", "clear", "--force"], env=env)
            assert result.exit_code == 0
            assert "cleared" in result.stdout.lower()


class TestGenerateRequirements:
    """Tests for generate requirements command."""

    def test_generate_requirements_file_not_found(self) -> None:
        """Test generate requirements errors on missing file."""
        result = runner.invoke(app, ["generate", "requirements", "/nonexistent/stories.yaml"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_generate_requirements_invalid_format(self) -> None:
        """Test generate requirements errors on invalid format."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"user_stories": []}')
            f.flush()
            result = runner.invoke(
                app, ["generate", "requirements", f.name, "--format", "invalid"]
            )
        assert result.exit_code == 1
        assert "invalid format" in result.stdout.lower()

    def test_generate_requirements_empty_stories(self) -> None:
        """Test generate requirements handles empty user stories."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"user_stories": []}')
            f.flush()
            result = runner.invoke(app, ["generate", "requirements", f.name])
        assert result.exit_code == 0
        assert "no user stories" in result.stdout.lower()


class TestEvaluateRun:
    """Tests for evaluate run command."""

    def test_evaluate_run_stories_not_found(self, tmp_path: Path) -> None:
        """Test evaluate run errors when stories file not found."""
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('{"ground_truth": []}')

        result = runner.invoke(
            app, ["evaluate", "run", "/nonexistent/stories.json", str(gt_file)]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_evaluate_run_ground_truth_not_found(self, tmp_path: Path) -> None:
        """Test evaluate run errors when ground truth file not found."""
        stories_file = tmp_path / "stories.json"
        stories_file.write_text('{"user_stories": []}')

        result = runner.invoke(
            app, ["evaluate", "run", str(stories_file), "/nonexistent/gt.json"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_evaluate_run_context_not_found(self, tmp_path: Path) -> None:
        """Test evaluate run errors when context file not found."""
        stories_file = tmp_path / "stories.json"
        stories_file.write_text('{"user_stories": []}')
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('{"ground_truth": []}')

        result = runner.invoke(
            app,
            [
                "evaluate", "run",
                str(stories_file),
                str(gt_file),
                "--context", "/nonexistent/context.yaml"
            ]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


class TestEvaluateCompare:
    """Tests for evaluate compare command."""

    def test_evaluate_compare_generated_not_found(self, tmp_path: Path) -> None:
        """Test evaluate compare errors when generated file not found."""
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('{"requirements": []}')

        result = runner.invoke(
            app, ["evaluate", "compare", "/nonexistent/generated.json", str(gt_file)]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_evaluate_compare_ground_truth_not_found(self, tmp_path: Path) -> None:
        """Test evaluate compare errors when ground truth file not found."""
        gen_file = tmp_path / "generated.json"
        gen_file.write_text('{"requirements": []}')

        result = runner.invoke(
            app, ["evaluate", "compare", str(gen_file), "/nonexistent/gt.json"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_evaluate_compare_empty_requirements(self, tmp_path: Path) -> None:
        """Test evaluate compare handles empty requirements list."""
        gen_file = tmp_path / "generated.json"
        gen_file.write_text('{"requirements": []}')
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('{"requirements": []}')

        result = runner.invoke(
            app, ["evaluate", "compare", str(gen_file), str(gt_file)]
        )
        assert result.exit_code == 0
        assert "no generated requirements" in result.stdout.lower()

    def test_evaluate_compare_invalid_json(self, tmp_path: Path) -> None:
        """Test evaluate compare errors on invalid JSON."""
        gen_file = tmp_path / "generated.json"
        gen_file.write_text('not valid json')
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('{"requirements": []}')

        result = runner.invoke(
            app, ["evaluate", "compare", str(gen_file), str(gt_file)]
        )
        assert result.exit_code == 1
        assert "json" in result.stdout.lower()

    def test_evaluate_compare_with_requirements(self, tmp_path: Path) -> None:
        """Test evaluate compare with actual requirements computes metrics."""
        gen_file = tmp_path / "generated.json"
        gen_file.write_text('''
        {
            "requirements": [
                {
                    "id": "GEN-001",
                    "title": "Password Validation",
                    "description": "Validate password length and complexity"
                },
                {
                    "id": "GEN-002",
                    "title": "Session Management",
                    "description": "Implement secure session handling with timeout"
                }
            ]
        }
        ''')
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('''
        {
            "requirements": [
                {
                    "id": "GT-001",
                    "title": "Password Requirements",
                    "description": "Enforce password complexity rules including length, character types"
                },
                {
                    "id": "GT-002",
                    "title": "Session Security",
                    "description": "Implement secure session management with proper timeout"
                }
            ]
        }
        ''')

        result = runner.invoke(
            app, ["evaluate", "compare", str(gen_file), str(gt_file)]
        )
        assert result.exit_code == 0
        assert "match" in result.stdout.lower()
        # Should show metrics table
        assert "precision" in result.stdout.lower()
        assert "recall" in result.stdout.lower()
        assert "f1" in result.stdout.lower()

    def test_evaluate_compare_with_output(self, tmp_path: Path) -> None:
        """Test evaluate compare saves results to output file."""
        gen_file = tmp_path / "generated.json"
        gen_file.write_text('''
        {
            "requirements": [
                {"id": "GEN-001", "title": "Test Requirement", "description": "A test requirement description"}
            ]
        }
        ''')
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('''
        {
            "requirements": [
                {"id": "GT-001", "title": "Test Requirement", "description": "A test requirement description"}
            ]
        }
        ''')
        output_file = tmp_path / "results.json"

        result = runner.invoke(
            app,
            ["evaluate", "compare", str(gen_file), str(gt_file), "--output", str(output_file)]
        )
        assert result.exit_code == 0
        assert output_file.exists()

        # Verify output content
        import json
        output_data = json.loads(output_file.read_text())
        assert "metrics" in output_data
        assert "matches" in output_data
        assert "config" in output_data

    def test_evaluate_compare_custom_thresholds(self, tmp_path: Path) -> None:
        """Test evaluate compare respects custom thresholds."""
        gen_file = tmp_path / "generated.json"
        gen_file.write_text('{"requirements": [{"id": "GEN-001", "title": "Test", "description": "Description"}]}')
        gt_file = tmp_path / "ground_truth.json"
        gt_file.write_text('{"requirements": [{"id": "GT-001", "title": "Test", "description": "Description"}]}')

        result = runner.invoke(
            app,
            [
                "evaluate", "compare",
                str(gen_file), str(gt_file),
                "--threshold", "0.90",
                "--partial", "0.75"
            ]
        )
        assert result.exit_code == 0
        assert "0.90" in result.stdout or "0.9" in result.stdout  # Threshold displayed


class TestEvaluateReport:
    """Tests for evaluate report command."""

    def test_evaluate_report_file_not_found(self) -> None:
        """Test evaluate report errors when results file not found."""
        result = runner.invoke(
            app, ["evaluate", "report", "/nonexistent/results.json"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    def test_evaluate_report_invalid_json(self, tmp_path: Path) -> None:
        """Test evaluate report errors on invalid JSON."""
        results_file = tmp_path / "results.json"
        results_file.write_text('not valid json')

        result = runner.invoke(app, ["evaluate", "report", str(results_file)])
        assert result.exit_code == 1
        assert "json" in result.stdout.lower()

    def test_evaluate_report_missing_key(self, tmp_path: Path) -> None:
        """Test evaluate report errors on invalid results structure."""
        results_file = tmp_path / "results.json"
        results_file.write_text('{"incomplete": "data"}')

        result = runner.invoke(app, ["evaluate", "report", str(results_file)])
        assert result.exit_code == 1
        assert "missing" in result.stdout.lower() or "key" in result.stdout.lower()

    def test_evaluate_report_generates_files(self, tmp_path: Path) -> None:
        """Test evaluate report generates report files from valid results."""
        results_file = tmp_path / "results.json"
        # Create a minimal valid results structure
        results_file.write_text('''
        {
            "run_timestamp": "2025-12-31T12:00:00",
            "total_stories": 1,
            "meets_thesis_target": true,
            "config": {"match_threshold": 0.85},
            "aggregate_metrics": {
                "baseline": {
                    "true_positives": 2,
                    "false_positives": 1,
                    "false_negatives": 1,
                    "partial_matches": 0,
                    "total_generated": 3,
                    "total_ground_truth": 3
                },
                "rag": {
                    "true_positives": 3,
                    "false_positives": 0,
                    "false_negatives": 0,
                    "partial_matches": 0,
                    "total_generated": 3,
                    "total_ground_truth": 3
                }
            },
            "comparison": {
                "precision_delta": 0.33,
                "recall_delta": 0.33,
                "f1_delta": 0.33,
                "hallucination_delta": -0.33,
                "precision_improvement_pct": 50.0,
                "recall_improvement_pct": 50.0,
                "f1_improvement_pct": 50.0,
                "improvement_threshold": 1.20
            },
            "per_story_results": [
                {
                    "story_id": "US-001",
                    "story_title": "Test Story",
                    "ground_truth_count": 3,
                    "baseline_requirement_count": 3,
                    "rag_requirement_count": 3,
                    "baseline_metrics": {
                        "true_positives": 2,
                        "false_positives": 1,
                        "false_negatives": 1,
                        "partial_matches": 0,
                        "total_generated": 3,
                        "total_ground_truth": 3
                    },
                    "rag_metrics": {
                        "true_positives": 3,
                        "false_positives": 0,
                        "false_negatives": 0,
                        "partial_matches": 0,
                        "total_generated": 3,
                        "total_ground_truth": 3
                    },
                    "baseline_matches": [],
                    "rag_matches": []
                }
            ],
            "partial_matches": []
        }
        ''')

        output_dir = tmp_path / "reports"

        result = runner.invoke(
            app,
            ["evaluate", "report", str(results_file), "--output", str(output_dir)]
        )
        assert result.exit_code == 0
        assert output_dir.exists()
        # Should have created at least one report file
        report_files = list(output_dir.glob("*"))
        assert len(report_files) >= 1

    def test_evaluate_report_custom_name(self, tmp_path: Path) -> None:
        """Test evaluate report uses custom report name."""
        results_file = tmp_path / "results.json"
        results_file.write_text('''
        {
            "run_timestamp": "2025-12-31T12:00:00",
            "total_stories": 0,
            "meets_thesis_target": false,
            "config": {},
            "aggregate_metrics": {
                "baseline": {"true_positives": 0, "false_positives": 0, "false_negatives": 0, "partial_matches": 0},
                "rag": {"true_positives": 0, "false_positives": 0, "false_negatives": 0, "partial_matches": 0}
            },
            "comparison": {
                "precision_delta": 0.0, "recall_delta": 0.0, "f1_delta": 0.0, "hallucination_delta": 0.0,
                "precision_improvement_pct": 0.0, "recall_improvement_pct": 0.0, "f1_improvement_pct": 0.0,
                "improvement_threshold": 1.20
            },
            "per_story_results": []
        }
        ''')

        output_dir = tmp_path / "reports"

        result = runner.invoke(
            app,
            ["evaluate", "report", str(results_file), "--output", str(output_dir), "--name", "custom_report"]
        )
        assert result.exit_code == 0
        # Check that files contain the custom name
        report_files = list(output_dir.glob("custom_report*"))
        assert len(report_files) >= 1
