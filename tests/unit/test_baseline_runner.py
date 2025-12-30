"""Unit tests for baseline runner."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secreqgen.evaluation.baseline import (
    BaselineRunner,
    ComparativeResults,
    create_baseline_runner,
)
from secreqgen.generation.generator import GenerationMode
from secreqgen.generation.schemas import (
    ASVSCategory,
    GenerationResult,
    Priority,
    SecurityRequirement,
    UserStory,
)


# Fixtures
@pytest.fixture
def sample_user_stories() -> list[UserStory]:
    """Sample user stories for testing."""
    return [
        UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in securely",
            acceptance_criteria=["Email and password required", "Session created"],
        ),
        UserStory(
            id="US-002",
            title="Password Reset",
            story="As a user, I want to reset my password",
            acceptance_criteria=["Email verification", "Secure token"],
        ),
    ]


@pytest.fixture
def sample_requirement() -> SecurityRequirement:
    """Sample security requirement."""
    return SecurityRequirement(
        id="SR-001",
        user_story_id="US-001",
        title="Secure Authentication",
        description="Implement secure password-based authentication",
        rationale="To protect user accounts",
        asvs_category=ASVSCategory.V2_AUTHENTICATION,
        priority=Priority.HIGH,
    )


@pytest.fixture
def mock_llm_client():
    """Mock LLM client."""
    return MagicMock()


@pytest.fixture
def mock_retriever():
    """Mock retriever for RAG mode."""
    retriever = MagicMock()
    retriever.get_system_context.return_value = "System context"
    return retriever


def create_mock_result(story: UserStory, rag_enabled: bool) -> GenerationResult:
    """Helper to create a mock generation result."""
    return GenerationResult(
        user_story=story,
        requirements=[
            SecurityRequirement(
                id=f"SR-{story.id}-001",
                user_story_id=story.id,
                title=f"Requirement for {story.title}",
                description="Test description",
                rationale="Test rationale",
                asvs_category=ASVSCategory.V2_AUTHENTICATION,
                priority=Priority.HIGH,
            )
        ],
        threat_mappings=[],
        model_used="test-model",
        rag_enabled=rag_enabled,
    )


# ComparativeResults tests
class TestComparativeResults:
    def test_properties(self, sample_user_stories):
        """Test computed properties."""
        baseline_results = [
            create_mock_result(sample_user_stories[0], rag_enabled=False),
        ]
        rag_results = [
            create_mock_result(sample_user_stories[0], rag_enabled=True),
            create_mock_result(sample_user_stories[1], rag_enabled=True),
        ]

        results = ComparativeResults(
            user_stories=sample_user_stories,
            baseline_results=baseline_results,
            rag_results=rag_results,
            baseline_time_ms=1000.0,
            rag_time_ms=1500.0,
            baseline_errors=[("US-002", "Error")],
            rag_errors=[],
        )

        assert results.total_stories == 2
        assert results.baseline_success_count == 1
        assert results.rag_success_count == 2
        assert results.baseline_requirements_count == 1
        assert results.rag_requirements_count == 2

    def test_summary_contains_key_info(self, sample_user_stories):
        """Summary should contain all key information."""
        results = ComparativeResults(
            user_stories=sample_user_stories,
            baseline_results=[create_mock_result(sample_user_stories[0], False)],
            rag_results=[create_mock_result(sample_user_stories[0], True)],
            baseline_time_ms=500.0,
            rag_time_ms=750.0,
        )

        summary = results.summary()

        assert "Comparative Evaluation Summary" in summary
        assert "User Stories: 2" in summary
        assert "Baseline (Zero-Shot)" in summary
        assert "RAG-Enhanced" in summary
        assert "500ms" in summary
        assert "750ms" in summary

    def test_empty_results(self):
        """Should handle empty results gracefully."""
        results = ComparativeResults(
            user_stories=[],
            baseline_results=[],
            rag_results=[],
            baseline_time_ms=0.0,
            rag_time_ms=0.0,
        )

        assert results.total_stories == 0
        assert results.baseline_requirements_count == 0
        assert results.rag_requirements_count == 0


# BaselineRunner initialization tests
class TestBaselineRunnerInit:
    def test_init_with_retriever(self, mock_llm_client, mock_retriever):
        """Should initialize with both baseline and RAG generators."""
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            retriever=mock_retriever,
        )

        assert runner._baseline_generator is not None
        assert runner._rag_generator is not None

    def test_init_without_retriever(self, mock_llm_client):
        """Should initialize with only baseline generator when no retriever."""
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            retriever=None,
        )

        assert runner._baseline_generator is not None
        assert runner._rag_generator is None

    def test_init_with_system_context(self, mock_llm_client):
        """Should store system context."""
        context = "Test system context"
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            system_context=context,
        )

        assert runner.system_context == context


# BaselineRunner.run_baseline tests
class TestBaselineRunnerRunBaseline:
    def test_run_baseline_success(
        self, mock_llm_client, sample_user_stories
    ):
        """Should run baseline generation successfully."""
        runner = BaselineRunner(llm_client=mock_llm_client)

        # Mock the generator's generate method
        mock_results = [
            create_mock_result(story, rag_enabled=False)
            for story in sample_user_stories
        ]

        with patch.object(
            runner._baseline_generator,
            "generate",
            side_effect=mock_results,
        ):
            results, errors, time_ms = runner.run_baseline(sample_user_stories)

        assert len(results) == 2
        assert len(errors) == 0
        assert time_ms > 0
        assert all(not r.rag_enabled for r in results)

    def test_run_baseline_with_errors(
        self, mock_llm_client, sample_user_stories
    ):
        """Should handle generation errors gracefully."""
        runner = BaselineRunner(llm_client=mock_llm_client)

        # First succeeds, second fails
        def mock_generate(user_story, mode, system_context):
            if user_story.id == "US-001":
                return create_mock_result(user_story, rag_enabled=False)
            else:
                raise Exception("Generation failed")

        with patch.object(
            runner._baseline_generator,
            "generate",
            side_effect=mock_generate,
        ):
            results, errors, time_ms = runner.run_baseline(sample_user_stories)

        assert len(results) == 1
        assert len(errors) == 1
        assert errors[0][0] == "US-002"
        assert "Generation failed" in errors[0][1]

    def test_run_baseline_with_progress_callback(
        self, mock_llm_client, sample_user_stories
    ):
        """Should call progress callback correctly."""
        runner = BaselineRunner(llm_client=mock_llm_client)
        progress_calls = []

        def progress_callback(mode, current, total):
            progress_calls.append((mode, current, total))

        with patch.object(
            runner._baseline_generator,
            "generate",
            return_value=create_mock_result(sample_user_stories[0], False),
        ):
            runner.run_baseline(sample_user_stories, progress_callback=progress_callback)

        # Should be called for each story + final call
        assert len(progress_calls) == 3  # 0/2, 1/2, 2/2
        assert progress_calls[0] == ("baseline", 0, 2)
        assert progress_calls[1] == ("baseline", 1, 2)
        assert progress_calls[2] == ("baseline", 2, 2)


# BaselineRunner.run_rag tests
class TestBaselineRunnerRunRag:
    def test_run_rag_success(
        self, mock_llm_client, mock_retriever, sample_user_stories
    ):
        """Should run RAG generation successfully."""
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            retriever=mock_retriever,
        )

        mock_results = [
            create_mock_result(story, rag_enabled=True)
            for story in sample_user_stories
        ]

        with patch.object(
            runner._rag_generator,
            "generate",
            side_effect=mock_results,
        ):
            results, errors, time_ms = runner.run_rag(sample_user_stories)

        assert len(results) == 2
        assert len(errors) == 0
        assert all(r.rag_enabled for r in results)

    def test_run_rag_without_retriever_raises(
        self, mock_llm_client, sample_user_stories
    ):
        """Should raise ValueError when no retriever provided."""
        runner = BaselineRunner(llm_client=mock_llm_client)

        with pytest.raises(ValueError, match="no retriever provided"):
            runner.run_rag(sample_user_stories)


# BaselineRunner.run_comparative tests
class TestBaselineRunnerRunComparative:
    def test_run_comparative_success(
        self, mock_llm_client, mock_retriever, sample_user_stories
    ):
        """Should run both modes and return ComparativeResults."""
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            retriever=mock_retriever,
        )

        baseline_results = [
            create_mock_result(story, rag_enabled=False)
            for story in sample_user_stories
        ]
        rag_results = [
            create_mock_result(story, rag_enabled=True)
            for story in sample_user_stories
        ]

        with patch.object(
            runner._baseline_generator,
            "generate",
            side_effect=baseline_results,
        ), patch.object(
            runner._rag_generator,
            "generate",
            side_effect=rag_results,
        ):
            results = runner.run_comparative(sample_user_stories)

        assert isinstance(results, ComparativeResults)
        assert len(results.baseline_results) == 2
        assert len(results.rag_results) == 2
        assert results.baseline_time_ms > 0
        assert results.rag_time_ms > 0
        assert len(results.baseline_errors) == 0
        assert len(results.rag_errors) == 0

    def test_run_comparative_without_retriever_raises(
        self, mock_llm_client, sample_user_stories
    ):
        """Should raise ValueError when no retriever provided."""
        runner = BaselineRunner(llm_client=mock_llm_client)

        with pytest.raises(ValueError, match="no retriever provided"):
            runner.run_comparative(sample_user_stories)

    def test_run_comparative_with_partial_failures(
        self, mock_llm_client, mock_retriever, sample_user_stories
    ):
        """Should handle partial failures in both modes."""
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            retriever=mock_retriever,
        )

        # Baseline: first succeeds, second fails
        def baseline_generate(user_story, mode, system_context):
            if user_story.id == "US-001":
                return create_mock_result(user_story, rag_enabled=False)
            raise Exception("Baseline error")

        # RAG: second succeeds, first fails
        def rag_generate(user_story, mode, system_context):
            if user_story.id == "US-002":
                return create_mock_result(user_story, rag_enabled=True)
            raise Exception("RAG error")

        with patch.object(
            runner._baseline_generator,
            "generate",
            side_effect=baseline_generate,
        ), patch.object(
            runner._rag_generator,
            "generate",
            side_effect=rag_generate,
        ):
            results = runner.run_comparative(sample_user_stories)

        assert len(results.baseline_results) == 1
        assert len(results.rag_results) == 1
        assert len(results.baseline_errors) == 1
        assert len(results.rag_errors) == 1
        assert results.baseline_errors[0][0] == "US-002"
        assert results.rag_errors[0][0] == "US-001"

    def test_run_comparative_with_progress_callback(
        self, mock_llm_client, mock_retriever, sample_user_stories
    ):
        """Should call progress callback for both modes."""
        runner = BaselineRunner(
            llm_client=mock_llm_client,
            retriever=mock_retriever,
        )
        progress_calls = []

        def progress_callback(mode, current, total):
            progress_calls.append((mode, current, total))

        with patch.object(
            runner._baseline_generator,
            "generate",
            return_value=create_mock_result(sample_user_stories[0], False),
        ), patch.object(
            runner._rag_generator,
            "generate",
            return_value=create_mock_result(sample_user_stories[0], True),
        ):
            runner.run_comparative(sample_user_stories, progress_callback=progress_callback)

        # Should have calls for both baseline and rag
        baseline_calls = [c for c in progress_calls if c[0] == "baseline"]
        rag_calls = [c for c in progress_calls if c[0] == "rag"]

        assert len(baseline_calls) == 3  # 0/2, 1/2, 2/2
        assert len(rag_calls) == 3


# create_baseline_runner factory tests
class TestCreateBaselineRunner:
    def test_create_without_vectorstore(self, mock_llm_client):
        """Should create runner with only baseline capability."""
        runner = create_baseline_runner(llm_client=mock_llm_client)

        assert runner._baseline_generator is not None
        assert runner._rag_generator is None

    def test_create_with_vectorstore(self, mock_llm_client, tmp_path):
        """Should create runner with RAG capability."""
        # Patch at the original module locations (lazy imports happen inside function)
        with patch(
            "secreqgen.retrieval.vectorstore.SecReqGenVectorStore"
        ) as mock_vs, patch(
            "secreqgen.ingestion.embedder.DocumentEmbedder"
        ) as mock_embedder, patch(
            "secreqgen.retrieval.retriever.SimpleRetriever"
        ) as mock_retriever_cls:
            # Configure mocks
            mock_vs.return_value = MagicMock()
            mock_embedder.return_value = MagicMock()
            mock_retriever_cls.return_value = MagicMock()

            runner = create_baseline_runner(
                llm_client=mock_llm_client,
                vectorstore_path=str(tmp_path),
            )

            mock_embedder.assert_called_once()
            mock_vs.assert_called_once()
            mock_retriever_cls.assert_called_once()

        assert runner._baseline_generator is not None
        assert runner._rag_generator is not None

    def test_create_with_custom_embedding_model(self, mock_llm_client, tmp_path):
        """Should use custom embedding model."""
        with patch(
            "secreqgen.retrieval.vectorstore.SecReqGenVectorStore"
        ), patch(
            "secreqgen.ingestion.embedder.DocumentEmbedder"
        ) as mock_embedder, patch(
            "secreqgen.retrieval.retriever.SimpleRetriever"
        ):
            create_baseline_runner(
                llm_client=mock_llm_client,
                vectorstore_path=str(tmp_path),
                embedding_model="custom-model",
            )

            mock_embedder.assert_called_once_with(model_name="custom-model")

    def test_create_with_system_context(self, mock_llm_client):
        """Should pass system context to runner."""
        context = "Custom context"
        runner = create_baseline_runner(
            llm_client=mock_llm_client,
            system_context=context,
        )

        assert runner.system_context == context


# Edge cases
class TestBaselineRunnerEdgeCases:
    def test_empty_stories_list(self, mock_llm_client):
        """Should handle empty stories list."""
        runner = BaselineRunner(llm_client=mock_llm_client)

        results, errors, time_ms = runner.run_baseline([])

        assert results == []
        assert errors == []
        assert time_ms >= 0

    def test_single_story(self, mock_llm_client):
        """Should handle single story."""
        runner = BaselineRunner(llm_client=mock_llm_client)
        story = UserStory(
            id="US-001",
            title="Test",
            story="Test story",
            acceptance_criteria=[],
        )

        with patch.object(
            runner._baseline_generator,
            "generate",
            return_value=create_mock_result(story, False),
        ):
            results, errors, time_ms = runner.run_baseline([story])

        assert len(results) == 1
        assert len(errors) == 0
