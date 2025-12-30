"""Unit tests for the evaluation pipeline and report generator."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from secreqgen.evaluation.baseline import ComparativeResults
from secreqgen.evaluation.metrics import ComparisonResult, MetricsBreakdown
from secreqgen.evaluation.pipeline import (
    EvaluationPipeline,
    EvaluationPipelineResult,
    StoryEvaluationResult,
    create_evaluation_pipeline,
)
from secreqgen.evaluation.report import EvaluationReportGenerator, write_evaluation_report
from secreqgen.evaluation.semantic_matcher import GroundTruthRequirement, MatchResult, MatchType
from secreqgen.generation.schemas import (
    ASVSCategory,
    GenerationResult,
    Priority,
    SecurityRequirement,
    UserStory,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_stories() -> list[UserStory]:
    """Create sample user stories for testing."""
    return [
        UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in securely",
            acceptance_criteria=["Enter credentials", "Get feedback"],
        ),
        UserStory(
            id="US-002",
            title="Two-Factor Auth",
            story="As a user, I want to enable 2FA",
            acceptance_criteria=["Enable TOTP", "Use hardware key"],
        ),
    ]


@pytest.fixture
def sample_ground_truth() -> dict[str, list[GroundTruthRequirement]]:
    """Create sample ground truth for testing."""
    return {
        "US-001": [
            GroundTruthRequirement(
                id="GT-001",
                title="Secure Password Storage",
                description="Passwords must be hashed with bcrypt or Argon2",
                asvs_category="V2 - Authentication",
            ),
            GroundTruthRequirement(
                id="GT-002",
                title="Account Lockout",
                description="Lock account after 5 failed login attempts",
                asvs_category="V2 - Authentication",
            ),
        ],
        "US-002": [
            GroundTruthRequirement(
                id="GT-003",
                title="TOTP Implementation",
                description="Support time-based one-time passwords",
                asvs_category="V2 - Authentication",
            ),
        ],
    }


@pytest.fixture
def sample_requirements() -> list[SecurityRequirement]:
    """Create sample generated requirements."""
    return [
        SecurityRequirement(
            id="SR-001",
            user_story_id="US-001",
            title="Password Hashing",
            description="Hash passwords using bcrypt with cost factor 12",
            rationale="Protect against password database breaches",
            asvs_category=ASVSCategory.V2_AUTHENTICATION,
            priority=Priority.CRITICAL,
        ),
        SecurityRequirement(
            id="SR-002",
            user_story_id="US-001",
            title="Login Rate Limiting",
            description="Limit login attempts to prevent brute force",
            rationale="Mitigate credential stuffing attacks",
            asvs_category=ASVSCategory.V2_AUTHENTICATION,
            priority=Priority.HIGH,
        ),
    ]


@pytest.fixture
def sample_generation_result(sample_stories, sample_requirements) -> GenerationResult:
    """Create a sample generation result."""
    return GenerationResult(
        user_story=sample_stories[0],
        requirements=sample_requirements,
        threat_mappings=[],
        model_used="llama3:8b",
        rag_enabled=True,
    )


@pytest.fixture
def sample_metrics_breakdown() -> MetricsBreakdown:
    """Create a sample metrics breakdown."""
    return MetricsBreakdown(
        true_positives=3,
        false_positives=1,
        false_negatives=2,
        partial_matches=1,
        total_generated=5,
        total_ground_truth=6,
        precision=0.75,
        recall=0.60,
        f1_score=0.667,
        hallucination_rate=0.20,
        matched_pairs=[("SR-001", "GT-001", 0.92)],
    )


@pytest.fixture
def sample_comparison_result(sample_metrics_breakdown) -> ComparisonResult:
    """Create a sample comparison result."""
    baseline = MetricsBreakdown(
        true_positives=2,
        false_positives=2,
        false_negatives=3,
        partial_matches=0,
        total_generated=4,
        total_ground_truth=5,
        precision=0.50,
        recall=0.40,
        f1_score=0.444,
        hallucination_rate=0.50,
    )
    return ComparisonResult(
        baseline_metrics=baseline,
        rag_metrics=sample_metrics_breakdown,
        precision_delta=0.25,
        recall_delta=0.20,
        f1_delta=0.223,
        hallucination_delta=-0.30,
        precision_improvement_pct=50.0,
        recall_improvement_pct=50.0,
        f1_improvement_pct=50.2,
        meets_threshold=True,
        improvement_threshold=1.20,
    )


@pytest.fixture
def sample_story_result(
    sample_stories, sample_ground_truth, sample_generation_result, sample_metrics_breakdown
) -> StoryEvaluationResult:
    """Create a sample story evaluation result."""
    return StoryEvaluationResult(
        story_id="US-001",
        story_title="User Login",
        ground_truth=sample_ground_truth["US-001"],
        baseline_result=sample_generation_result,
        rag_result=sample_generation_result,
        baseline_matches=[
            MatchResult(
                generated_id="SR-001",
                generated_text="Password Hashing. Hash passwords using bcrypt",
                ground_truth_id="GT-001",
                ground_truth_text="Secure Password Storage. Passwords must be hashed",
                similarity_score=0.88,
                match_type=MatchType.MATCH,
            )
        ],
        rag_matches=[
            MatchResult(
                generated_id="SR-001",
                generated_text="Password Hashing. Hash passwords using bcrypt",
                ground_truth_id="GT-001",
                ground_truth_text="Secure Password Storage. Passwords must be hashed",
                similarity_score=0.92,
                match_type=MatchType.MATCH,
            ),
            MatchResult(
                generated_id="SR-002",
                generated_text="Login Rate Limiting. Limit login attempts",
                ground_truth_id="GT-002",
                ground_truth_text="Account Lockout. Lock account after 5 failed attempts",
                similarity_score=0.78,
                match_type=MatchType.PARTIAL,
            ),
        ],
        baseline_metrics=sample_metrics_breakdown,
        rag_metrics=sample_metrics_breakdown,
    )


@pytest.fixture
def sample_pipeline_result(
    sample_story_result, sample_metrics_breakdown, sample_comparison_result
) -> EvaluationPipelineResult:
    """Create a sample pipeline result."""
    # Create mock ComparativeResults
    comparative = MagicMock(spec=ComparativeResults)

    return EvaluationPipelineResult(
        per_story_results=[sample_story_result],
        aggregate_baseline_metrics=sample_metrics_breakdown,
        aggregate_rag_metrics=sample_metrics_breakdown,
        comparison=sample_comparison_result,
        comparative_results=comparative,
        config={
            "match_threshold": 0.85,
            "partial_threshold": 0.70,
            "improvement_threshold": 1.20,
        },
    )


@pytest.fixture
def stories_json_file(tmp_path: Path, sample_stories) -> Path:
    """Create a JSON file with user stories."""
    data = {
        "user_stories": [
            {
                "id": s.id,
                "title": s.title,
                "story": s.story,
                "acceptance_criteria": s.acceptance_criteria,
            }
            for s in sample_stories
        ]
    }
    path = tmp_path / "stories.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def ground_truth_json_file(tmp_path: Path, sample_ground_truth) -> Path:
    """Create a JSON file with ground truth."""
    data = {
        "ground_truth": [
            {
                "story_id": story_id,
                "requirements": [
                    {
                        "id": gt.id,
                        "title": gt.title,
                        "description": gt.description,
                        "asvs_category": gt.asvs_category,
                    }
                    for gt in requirements
                ],
            }
            for story_id, requirements in sample_ground_truth.items()
        ]
    }
    path = tmp_path / "ground_truth.json"
    path.write_text(json.dumps(data))
    return path


# ============================================================================
# StoryEvaluationResult Tests
# ============================================================================


class TestStoryEvaluationResult:
    """Tests for StoryEvaluationResult dataclass."""

    def test_requirement_counts(self, sample_story_result):
        """Test requirement count properties."""
        assert sample_story_result.baseline_requirement_count == 2
        assert sample_story_result.rag_requirement_count == 2
        assert sample_story_result.ground_truth_count == 2

    def test_requirement_counts_with_none_results(self, sample_ground_truth):
        """Test counts when generation results are None."""
        result = StoryEvaluationResult(
            story_id="US-001",
            story_title="Test",
            ground_truth=sample_ground_truth["US-001"],
            baseline_result=None,
            rag_result=None,
            baseline_matches=[],
            rag_matches=[],
            baseline_metrics=MetricsBreakdown(
                true_positives=0,
                false_positives=0,
                false_negatives=2,
                partial_matches=0,
                total_generated=0,
                total_ground_truth=2,
                precision=0,
                recall=0,
                f1_score=0,
                hallucination_rate=0,
            ),
            rag_metrics=MetricsBreakdown(
                true_positives=0,
                false_positives=0,
                false_negatives=2,
                partial_matches=0,
                total_generated=0,
                total_ground_truth=2,
                precision=0,
                recall=0,
                f1_score=0,
                hallucination_rate=0,
            ),
        )

        assert result.baseline_requirement_count == 0
        assert result.rag_requirement_count == 0


# ============================================================================
# EvaluationPipelineResult Tests
# ============================================================================


class TestEvaluationPipelineResult:
    """Tests for EvaluationPipelineResult dataclass."""

    def test_total_stories(self, sample_pipeline_result):
        """Test total stories count."""
        assert sample_pipeline_result.total_stories == 1

    def test_meets_thesis_target(self, sample_pipeline_result):
        """Test thesis target property."""
        assert sample_pipeline_result.meets_thesis_target is True

    def test_get_partial_matches(self, sample_pipeline_result):
        """Test getting partial matches for review."""
        partials = sample_pipeline_result.get_partial_matches()
        assert len(partials) == 1
        story_id, match = partials[0]
        assert story_id == "US-001"
        assert match.match_type == MatchType.PARTIAL
        assert match.similarity_score == 0.78

    def test_summary_generation(self, sample_pipeline_result):
        """Test summary string generation."""
        summary = sample_pipeline_result.summary()

        assert "EVALUATION PIPELINE RESULTS" in summary
        assert "Stories Evaluated: 1" in summary
        assert "THESIS TARGET MET" in summary
        assert "Precision" in summary
        assert "Recall" in summary
        assert "F1 Score" in summary


# ============================================================================
# EvaluationPipeline Tests
# ============================================================================


class TestEvaluationPipeline:
    """Tests for EvaluationPipeline class."""

    def test_init_with_retriever(self):
        """Test initialization with retriever."""
        mock_client = MagicMock()
        mock_retriever = MagicMock()

        pipeline = EvaluationPipeline(
            llm_client=mock_client,
            retriever=mock_retriever,
            match_threshold=0.85,
            partial_threshold=0.70,
        )

        assert pipeline.llm_client == mock_client
        assert pipeline.retriever == mock_retriever
        assert pipeline.match_threshold == 0.85
        assert pipeline.partial_threshold == 0.70

    def test_init_without_retriever(self):
        """Test initialization without retriever (baseline only)."""
        mock_client = MagicMock()

        pipeline = EvaluationPipeline(llm_client=mock_client, retriever=None)

        assert pipeline.retriever is None

    def test_load_stories(self, tmp_path: Path):
        """Test loading user stories from JSON."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        # Create stories file
        stories_data = {
            "user_stories": [
                {
                    "id": "US-001",
                    "title": "Test Story",
                    "story": "As a user, I want to test",
                    "acceptance_criteria": ["Criterion 1"],
                }
            ]
        }
        path = tmp_path / "stories.json"
        path.write_text(json.dumps(stories_data))

        stories = pipeline._load_stories(path)

        assert len(stories) == 1
        assert stories[0].id == "US-001"
        assert stories[0].title == "Test Story"

    def test_load_stories_file_not_found(self):
        """Test error when stories file doesn't exist."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        with pytest.raises(FileNotFoundError):
            pipeline._load_stories("/nonexistent/path.json")

    def test_load_stories_invalid_format(self, tmp_path: Path):
        """Test error when stories file has invalid format."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        path = tmp_path / "invalid.json"
        path.write_text('{"wrong_key": []}')

        with pytest.raises(ValueError, match="missing 'user_stories' key"):
            pipeline._load_stories(path)

    def test_load_ground_truth(self, tmp_path: Path):
        """Test loading ground truth from JSON."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        gt_data = {
            "ground_truth": [
                {
                    "story_id": "US-001",
                    "requirements": [
                        {
                            "id": "GT-001",
                            "title": "Test Requirement",
                            "description": "This is a test",
                        }
                    ],
                }
            ]
        }
        path = tmp_path / "ground_truth.json"
        path.write_text(json.dumps(gt_data))

        gt_map = pipeline._load_ground_truth(path)

        assert "US-001" in gt_map
        assert len(gt_map["US-001"]) == 1
        assert gt_map["US-001"][0].title == "Test Requirement"

    def test_load_ground_truth_file_not_found(self):
        """Test error when ground truth file doesn't exist."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        with pytest.raises(FileNotFoundError):
            pipeline._load_ground_truth("/nonexistent/path.json")

    def test_load_ground_truth_invalid_format(self, tmp_path: Path):
        """Test error when ground truth file has invalid format."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        path = tmp_path / "invalid.json"
        path.write_text('{"wrong_key": []}')

        with pytest.raises(ValueError, match="missing 'ground_truth' key"):
            pipeline._load_ground_truth(path)

    def test_evaluate_per_story_empty_results(
        self, sample_stories, sample_ground_truth
    ):
        """Test evaluation when generation produces no results."""
        mock_client = MagicMock()
        pipeline = EvaluationPipeline(llm_client=mock_client)

        results = pipeline._evaluate_per_story(
            stories=sample_stories,
            ground_truth_map=sample_ground_truth,
            baseline_results=[],
            rag_results=[],
        )

        assert len(results) == 2
        # All metrics should show failures (no matches, all GT are FN)
        for result in results:
            assert result.baseline_result is None
            assert result.rag_result is None


class TestCreateEvaluationPipeline:
    """Tests for create_evaluation_pipeline factory function."""

    def test_create_without_vectorstore(self):
        """Test creating pipeline without vectorstore."""
        mock_client = MagicMock()

        pipeline = create_evaluation_pipeline(
            llm_client=mock_client,
            vectorstore_path=None,
        )

        assert pipeline.retriever is None

    def test_create_with_vectorstore(self, tmp_path):
        """Test creating pipeline with vectorstore path.

        The factory function uses local imports, so we patch at the source
        module level where they're actually imported from.
        """
        mock_client = MagicMock()
        mock_embedder = MagicMock()
        mock_vectorstore = MagicMock()
        mock_retriever = MagicMock()

        # Patch where the classes are imported FROM (their source modules)
        with patch("secreqgen.ingestion.embedder.DocumentEmbedder", return_value=mock_embedder), \
             patch("secreqgen.retrieval.vectorstore.SecReqGenVectorStore", return_value=mock_vectorstore), \
             patch("secreqgen.retrieval.retriever.SimpleRetriever", return_value=mock_retriever):

            pipeline = create_evaluation_pipeline(
                llm_client=mock_client,
                vectorstore_path=str(tmp_path),
                embedding_model="all-MiniLM-L6-v2",
            )

            # Verify the pipeline was created with a retriever
            assert pipeline.retriever is not None


# ============================================================================
# EvaluationReportGenerator Tests
# ============================================================================


class TestEvaluationReportGenerator:
    """Tests for EvaluationReportGenerator class."""

    def test_init(self, tmp_path: Path):
        """Test initialization."""
        generator = EvaluationReportGenerator(tmp_path)
        assert generator.output_dir == tmp_path
        assert generator.create_dirs is True

    def test_generate_full_report(self, tmp_path: Path, sample_pipeline_result):
        """Test generating full report in multiple formats."""
        generator = EvaluationReportGenerator(tmp_path)

        paths = generator.generate_full_report(
            result=sample_pipeline_result,
            base_filename="test_eval",
            include_samples=True,
        )

        assert len(paths) == 2

        # Check Markdown file
        md_paths = [p for p in paths if p.suffix == ".md"]
        assert len(md_paths) == 1
        md_content = md_paths[0].read_text()
        assert "Evaluation Report" in md_content
        assert "THESIS TARGET MET" in md_content

        # Check JSON file
        json_paths = [p for p in paths if p.suffix == ".json"]
        assert len(json_paths) == 1
        json_content = json.loads(json_paths[0].read_text())
        assert "aggregate_metrics" in json_content
        assert "per_story_results" in json_content

    def test_generate_markdown_report(self, tmp_path: Path, sample_pipeline_result):
        """Test Markdown report content."""
        generator = EvaluationReportGenerator(tmp_path)

        path = generator._write_markdown_report(
            result=sample_pipeline_result,
            filename_base="test",
            include_samples=True,
            max_samples=3,
        )

        content = path.read_text()

        # Check required sections
        assert "# Evaluation Report" in content
        assert "## Aggregate Metrics" in content
        assert "## Per-Story Results" in content
        assert "## Partial Matches" in content
        assert "## Configuration" in content

        # Check metrics table
        assert "| Metric | Baseline | RAG | Delta |" in content
        assert "Precision" in content
        assert "Recall" in content
        assert "F1 Score" in content

    def test_generate_json_report(self, tmp_path: Path, sample_pipeline_result):
        """Test JSON report structure."""
        generator = EvaluationReportGenerator(tmp_path)

        path = generator._write_json_report(
            result=sample_pipeline_result,
            filename_base="test",
        )

        data = json.loads(path.read_text())

        assert "run_timestamp" in data
        assert "total_stories" in data
        assert "meets_thesis_target" in data
        assert "aggregate_metrics" in data
        assert "baseline" in data["aggregate_metrics"]
        assert "rag" in data["aggregate_metrics"]
        assert "comparison" in data
        assert "per_story_results" in data
        assert "partial_matches" in data

    def test_generate_summary(self, sample_pipeline_result):
        """Test summary text generation."""
        generator = EvaluationReportGenerator(Path("."))

        summary = generator.generate_summary(sample_pipeline_result)

        assert "EVALUATION PIPELINE RESULTS" in summary
        assert "THESIS TARGET MET" in summary

    def test_creates_output_directory(self, tmp_path: Path, sample_pipeline_result):
        """Test that output directory is created if it doesn't exist."""
        output_dir = tmp_path / "new_dir" / "reports"
        generator = EvaluationReportGenerator(output_dir, create_dirs=True)

        paths = generator.generate_full_report(
            result=sample_pipeline_result,
            base_filename="test",
        )

        assert output_dir.exists()
        assert len(paths) == 2


class TestWriteEvaluationReport:
    """Tests for write_evaluation_report convenience function."""

    def test_write_evaluation_report(self, tmp_path: Path, sample_pipeline_result):
        """Test the convenience function."""
        paths = write_evaluation_report(
            result=sample_pipeline_result,
            output_dir=tmp_path,
            base_filename="convenience_test",
        )

        assert len(paths) == 2
        assert all(p.exists() for p in paths)


# ============================================================================
# Integration-style Tests (with mocked components)
# ============================================================================


class TestEvaluationPipelineIntegration:
    """Integration-style tests using mocked components."""

    def test_run_from_data(self, sample_stories, sample_ground_truth):
        """Test running pipeline with pre-loaded data."""
        mock_client = MagicMock()
        mock_retriever = MagicMock()

        # Mock the BaselineRunner.run_comparative
        with patch.object(
            EvaluationPipeline, "_runner", create=True
        ) as mock_runner_attr:
            mock_runner = MagicMock()

            # Create mock generation results
            baseline_result = GenerationResult(
                user_story=sample_stories[0],
                requirements=[
                    SecurityRequirement(
                        id="SR-001",
                        user_story_id="US-001",
                        title="Password Hashing",
                        description="Use bcrypt for password hashing",
                        rationale="Security",
                        asvs_category=ASVSCategory.V2_AUTHENTICATION,
                    )
                ],
                threat_mappings=[],
                model_used="test",
                rag_enabled=False,
            )

            rag_result = GenerationResult(
                user_story=sample_stories[0],
                requirements=[
                    SecurityRequirement(
                        id="SR-001",
                        user_story_id="US-001",
                        title="Secure Password Storage",
                        description="Hash passwords with bcrypt cost 12",
                        rationale="Security",
                        asvs_category=ASVSCategory.V2_AUTHENTICATION,
                    )
                ],
                threat_mappings=[],
                model_used="test",
                rag_enabled=True,
            )

            mock_runner.run_comparative.return_value = ComparativeResults(
                user_stories=sample_stories,
                baseline_results=[baseline_result],
                rag_results=[rag_result],
                baseline_time_ms=1000,
                rag_time_ms=1500,
            )

            pipeline = EvaluationPipeline(
                llm_client=mock_client,
                retriever=mock_retriever,
            )
            pipeline._runner = mock_runner

            # This would normally embed and match, so we need to mock that too
            with patch.object(pipeline._matcher, "match_requirements") as mock_match:
                mock_match.return_value = [
                    MatchResult(
                        generated_id="SR-001",
                        generated_text="Test",
                        ground_truth_id="GT-001",
                        ground_truth_text="Test GT",
                        similarity_score=0.90,
                        match_type=MatchType.MATCH,
                    )
                ]

                result = pipeline.run_from_data(
                    stories=sample_stories[:1],  # Just first story
                    ground_truth_map=sample_ground_truth,
                )

                assert result.total_stories == 1
                assert len(result.per_story_results) == 1


class TestPartialMatchFlagging:
    """Tests for partial match identification and flagging."""

    def test_get_partial_matches_filters_correctly(self):
        """Test that only partial matches are returned."""
        matches = [
            MatchResult(
                generated_id="SR-001",
                generated_text="Test 1",
                ground_truth_id="GT-001",
                ground_truth_text="GT 1",
                similarity_score=0.92,
                match_type=MatchType.MATCH,
            ),
            MatchResult(
                generated_id="SR-002",
                generated_text="Test 2",
                ground_truth_id="GT-002",
                ground_truth_text="GT 2",
                similarity_score=0.78,
                match_type=MatchType.PARTIAL,
            ),
            MatchResult(
                generated_id="SR-003",
                generated_text="Test 3",
                ground_truth_id=None,
                ground_truth_text=None,
                similarity_score=0.45,
                match_type=MatchType.NO_MATCH,
            ),
        ]

        story_result = StoryEvaluationResult(
            story_id="US-001",
            story_title="Test",
            ground_truth=[],
            baseline_result=None,
            rag_result=None,
            baseline_matches=[],
            rag_matches=matches,
            baseline_metrics=MetricsBreakdown(
                true_positives=0,
                false_positives=0,
                false_negatives=0,
                partial_matches=0,
                total_generated=0,
                total_ground_truth=0,
                precision=0,
                recall=0,
                f1_score=0,
                hallucination_rate=0,
            ),
            rag_metrics=MetricsBreakdown(
                true_positives=1,
                false_positives=1,
                false_negatives=0,
                partial_matches=1,
                total_generated=3,
                total_ground_truth=2,
                precision=0.5,
                recall=0.5,
                f1_score=0.5,
                hallucination_rate=0.33,
            ),
        )

        mock_comparison = MagicMock(spec=ComparisonResult)
        mock_comparison.meets_threshold = True

        pipeline_result = EvaluationPipelineResult(
            per_story_results=[story_result],
            aggregate_baseline_metrics=story_result.baseline_metrics,
            aggregate_rag_metrics=story_result.rag_metrics,
            comparison=mock_comparison,
            comparative_results=MagicMock(),
        )

        partials = pipeline_result.get_partial_matches()

        assert len(partials) == 1
        assert partials[0][0] == "US-001"
        assert partials[0][1].generated_id == "SR-002"
        assert partials[0][1].similarity_score == 0.78
