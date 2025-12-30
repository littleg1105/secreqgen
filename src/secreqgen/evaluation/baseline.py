"""Baseline runner for comparative evaluation.

Orchestrates A/B comparison between RAG-enhanced and zero-shot (baseline)
security requirements generation for thesis evaluation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

from secreqgen.generation.generator import GenerationMode, SecurityRequirementsGenerator
from secreqgen.generation.schemas import GenerationResult, UserStory

if TYPE_CHECKING:
    from secreqgen.generation.llm_client import OllamaClient
    from secreqgen.retrieval.retriever import SimpleRetriever

logger = logging.getLogger(__name__)


@dataclass
class ComparativeResults:
    """Results from a comparative evaluation run.

    Holds both baseline and RAG generation results for the same set
    of user stories, enabling side-by-side analysis.

    Attributes:
        user_stories: The input user stories that were processed.
        baseline_results: Results from zero-shot (baseline) generation.
        rag_results: Results from RAG-enhanced generation.
        baseline_time_ms: Total time for baseline generation in milliseconds.
        rag_time_ms: Total time for RAG generation in milliseconds.
        run_timestamp: When the comparative run was executed.
        baseline_errors: List of (story_id, error_message) for failed baseline generations.
        rag_errors: List of (story_id, error_message) for failed RAG generations.
    """

    user_stories: list[UserStory]
    baseline_results: list[GenerationResult]
    rag_results: list[GenerationResult]
    baseline_time_ms: float
    rag_time_ms: float
    run_timestamp: datetime = field(default_factory=datetime.now)
    baseline_errors: list[tuple[str, str]] = field(default_factory=list)
    rag_errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_stories(self) -> int:
        """Total number of user stories processed."""
        return len(self.user_stories)

    @property
    def baseline_success_count(self) -> int:
        """Number of successfully generated baseline results."""
        return len(self.baseline_results)

    @property
    def rag_success_count(self) -> int:
        """Number of successfully generated RAG results."""
        return len(self.rag_results)

    @property
    def baseline_requirements_count(self) -> int:
        """Total number of requirements generated in baseline mode."""
        return sum(len(r.requirements) for r in self.baseline_results)

    @property
    def rag_requirements_count(self) -> int:
        """Total number of requirements generated in RAG mode."""
        return sum(len(r.requirements) for r in self.rag_results)

    def summary(self) -> str:
        """Generate a human-readable summary of the comparative run."""
        return (
            f"Comparative Evaluation Summary\n"
            f"{'=' * 40}\n"
            f"User Stories: {self.total_stories}\n"
            f"Run Time: {self.run_timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"\n"
            f"Baseline (Zero-Shot):\n"
            f"  Successful: {self.baseline_success_count}/{self.total_stories}\n"
            f"  Requirements: {self.baseline_requirements_count}\n"
            f"  Time: {self.baseline_time_ms:.0f}ms\n"
            f"  Errors: {len(self.baseline_errors)}\n"
            f"\n"
            f"RAG-Enhanced:\n"
            f"  Successful: {self.rag_success_count}/{self.total_stories}\n"
            f"  Requirements: {self.rag_requirements_count}\n"
            f"  Time: {self.rag_time_ms:.0f}ms\n"
            f"  Errors: {len(self.rag_errors)}\n"
        )


# Type alias for progress callback
ProgressCallback = Callable[[str, int, int], None]


class BaselineRunner:
    """Runner for comparative evaluation of RAG vs baseline generation.

    Orchestrates running both zero-shot (baseline) and RAG-enhanced
    generation on the same set of user stories for A/B comparison.

    The runner can be used in three ways:
    1. `run_baseline()` - Run only zero-shot generation
    2. `run_rag()` - Run only RAG-enhanced generation
    3. `run_comparative()` - Run both and return combined results

    Example:
        >>> runner = BaselineRunner(llm_client, retriever)
        >>> results = runner.run_comparative(user_stories)
        >>> print(results.summary())
        >>> # results.baseline_results and results.rag_results ready for evaluation
    """

    def __init__(
        self,
        llm_client: OllamaClient,
        retriever: Optional[SimpleRetriever] = None,
        system_context: Optional[str] = None,
        max_retries: int = 3,
    ) -> None:
        """Initialize the baseline runner.

        Args:
            llm_client: The Ollama client for LLM generation.
            retriever: The retriever for RAG mode. If None, only baseline
                mode will be available.
            system_context: Optional system context to inject into prompts.
                If None and retriever is available, will fetch from vector store.
            max_retries: Maximum retries for failed generations (default: 3).
        """
        self.llm_client = llm_client
        self.retriever = retriever
        self.system_context = system_context
        self.max_retries = max_retries

        # Create generators for each mode
        self._baseline_generator = SecurityRequirementsGenerator(
            llm_client=llm_client,
            retriever=None,  # No retriever for baseline
            max_retries=max_retries,
        )

        self._rag_generator: Optional[SecurityRequirementsGenerator] = None
        if retriever is not None:
            self._rag_generator = SecurityRequirementsGenerator(
                llm_client=llm_client,
                retriever=retriever,
                max_retries=max_retries,
            )

    def run_baseline(
        self,
        stories: list[UserStory],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[list[GenerationResult], list[tuple[str, str]], float]:
        """Run baseline (zero-shot) generation on user stories.

        Args:
            stories: List of user stories to generate requirements for.
            progress_callback: Optional callback(mode, current, total) for progress updates.

        Returns:
            Tuple of (results, errors, time_ms):
            - results: List of successful GenerationResult objects
            - errors: List of (story_id, error_message) for failures
            - time_ms: Total generation time in milliseconds
        """
        return self._run_generation(
            stories=stories,
            mode=GenerationMode.ZERO_SHOT,
            generator=self._baseline_generator,
            mode_name="baseline",
            progress_callback=progress_callback,
        )

    def run_rag(
        self,
        stories: list[UserStory],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[list[GenerationResult], list[tuple[str, str]], float]:
        """Run RAG-enhanced generation on user stories.

        Args:
            stories: List of user stories to generate requirements for.
            progress_callback: Optional callback(mode, current, total) for progress updates.

        Returns:
            Tuple of (results, errors, time_ms):
            - results: List of successful GenerationResult objects
            - errors: List of (story_id, error_message) for failures
            - time_ms: Total generation time in milliseconds

        Raises:
            ValueError: If no retriever was provided during initialization.
        """
        if self._rag_generator is None:
            raise ValueError(
                "Cannot run RAG mode: no retriever provided during initialization. "
                "Provide a retriever to enable RAG generation."
            )

        return self._run_generation(
            stories=stories,
            mode=GenerationMode.RAG,
            generator=self._rag_generator,
            mode_name="rag",
            progress_callback=progress_callback,
        )

    def run_comparative(
        self,
        stories: list[UserStory],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> ComparativeResults:
        """Run both baseline and RAG generation for comparison.

        Executes zero-shot generation first, then RAG-enhanced generation
        on the same set of user stories. Results are packaged together
        for side-by-side evaluation.

        Args:
            stories: List of user stories to generate requirements for.
            progress_callback: Optional callback(mode, current, total) for progress updates.

        Returns:
            ComparativeResults containing both result sets and metadata.

        Raises:
            ValueError: If no retriever was provided during initialization.
        """
        if self._rag_generator is None:
            raise ValueError(
                "Cannot run comparative evaluation: no retriever provided. "
                "Provide a retriever during initialization to enable RAG comparison."
            )

        logger.info(f"Starting comparative evaluation on {len(stories)} user stories")

        # Run baseline first
        logger.info("Running baseline (zero-shot) generation...")
        baseline_results, baseline_errors, baseline_time = self.run_baseline(
            stories=stories,
            progress_callback=progress_callback,
        )

        # Run RAG
        logger.info("Running RAG-enhanced generation...")
        rag_results, rag_errors, rag_time = self.run_rag(
            stories=stories,
            progress_callback=progress_callback,
        )

        results = ComparativeResults(
            user_stories=stories,
            baseline_results=baseline_results,
            rag_results=rag_results,
            baseline_time_ms=baseline_time,
            rag_time_ms=rag_time,
            baseline_errors=baseline_errors,
            rag_errors=rag_errors,
        )

        logger.info(
            f"Comparative evaluation complete. "
            f"Baseline: {results.baseline_requirements_count} reqs, "
            f"RAG: {results.rag_requirements_count} reqs"
        )

        return results

    def _run_generation(
        self,
        stories: list[UserStory],
        mode: GenerationMode,
        generator: SecurityRequirementsGenerator,
        mode_name: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> tuple[list[GenerationResult], list[tuple[str, str]], float]:
        """Run generation with a specific mode.

        Args:
            stories: User stories to process.
            mode: Generation mode (RAG or ZERO_SHOT).
            generator: The generator instance to use.
            mode_name: Human-readable mode name for logging/callbacks.
            progress_callback: Optional progress callback.

        Returns:
            Tuple of (results, errors, time_ms).
        """
        results: list[GenerationResult] = []
        errors: list[tuple[str, str]] = []
        total = len(stories)

        start_time = time.perf_counter()

        for i, story in enumerate(stories):
            if progress_callback:
                progress_callback(mode_name, i, total)

            try:
                result = generator.generate(
                    user_story=story,
                    mode=mode,
                    system_context=self.system_context,
                )
                results.append(result)
                logger.debug(
                    f"[{mode_name}] Generated {len(result.requirements)} "
                    f"requirements for {story.id}"
                )
            except Exception as e:
                error_msg = str(e)
                errors.append((story.id, error_msg))
                logger.warning(
                    f"[{mode_name}] Failed to generate for {story.id}: {error_msg}"
                )

        if progress_callback:
            progress_callback(mode_name, total, total)

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            f"[{mode_name}] Completed: {len(results)}/{total} successful, "
            f"{len(errors)} errors, {elapsed_ms:.0f}ms"
        )

        return results, errors, elapsed_ms


def create_baseline_runner(
    llm_client: OllamaClient,
    vectorstore_path: Optional[str] = None,
    embedding_model: str = "all-mpnet-base-v2",
    system_context: Optional[str] = None,
) -> BaselineRunner:
    """Factory function to create a BaselineRunner with standard configuration.

    This is a convenience function that sets up the runner with a retriever
    configured from the vector store. For more control, instantiate
    BaselineRunner directly.

    Args:
        llm_client: The Ollama client for LLM generation.
        vectorstore_path: Path to the ChromaDB vector store. If None,
            only baseline mode will be available.
        embedding_model: Embedding model name for the retriever.
        system_context: Optional system context to inject.

    Returns:
        Configured BaselineRunner instance.
    """
    retriever = None

    if vectorstore_path is not None:
        # Lazy imports to avoid slow startup
        from secreqgen.ingestion.embedder import DocumentEmbedder
        from secreqgen.retrieval.retriever import SimpleRetriever
        from secreqgen.retrieval.vectorstore import SecReqGenVectorStore

        embedder = DocumentEmbedder(model_name=embedding_model)
        vectorstore = SecReqGenVectorStore(persist_directory=vectorstore_path)
        retriever = SimpleRetriever(vectorstore=vectorstore, embedder=embedder)

    return BaselineRunner(
        llm_client=llm_client,
        retriever=retriever,
        system_context=system_context,
    )
