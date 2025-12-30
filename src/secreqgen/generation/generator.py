"""Security requirements generator for RAG pipeline.

Orchestrates LLM generation with optional retrieval-augmented context
to generate security requirements from user stories.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from pydantic import ValidationError

from secreqgen.exceptions import GenerationError
from secreqgen.generation.llm_client import LLMResponse, OllamaClient
from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder
from secreqgen.generation.schemas import (
    ASVSCategory,
    GenerationResult,
    Priority,
    SecurityRequirement,
    ThreatAgentMapping,
    UserStory,
)
from secreqgen.retrieval.retriever import RetrievalResult, SimpleRetriever

logger = logging.getLogger(__name__)


class GenerationMode(str, Enum):
    """Generation mode for requirements."""

    RAG = "rag"  # With retrieval-augmented generation
    ZERO_SHOT = "zero_shot"  # Baseline without retrieval


@dataclass
class GenerationMetrics:
    """Metrics from a generation operation.

    Tracks token usage, latency, and retrieval statistics
    for thesis analysis.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_latency_ms: float
    retrieval_latency_ms: Optional[float]  # None for zero-shot
    total_latency_ms: float
    chunks_retrieved: int
    chunks_used: int  # After threshold filtering
    retry_count: int


class SecurityRequirementsGenerator:
    """Generator for security requirements from user stories.

    Combines LLM generation with optional RAG context retrieval to
    produce security requirements. Supports both RAG-enhanced and
    zero-shot (baseline) generation modes for thesis comparison.

    Example:
        >>> generator = SecurityRequirementsGenerator(llm_client, retriever)
        >>> result = generator.generate(user_story, mode=GenerationMode.RAG)
        >>> for req in result.requirements:
        ...     print(f"{req.id}: {req.title}")
    """

    # Regex patterns for JSON extraction
    _JSON_CODE_BLOCK_PATTERN = re.compile(
        r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE
    )
    _JSON_OBJECT_PATTERN = re.compile(r"\{[\s\S]*\}")

    def __init__(
        self,
        llm_client: OllamaClient,
        retriever: Optional[SimpleRetriever] = None,
        prompt_builder: Optional[SecurityRequirementsPromptBuilder] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        """Initialize the generator.

        Args:
            llm_client: The Ollama client for LLM generation.
            retriever: Optional retriever for RAG mode. If None, only
                zero-shot mode is available.
            prompt_builder: Optional custom prompt builder. If None,
                uses default SecurityRequirementsPromptBuilder.
            max_retries: Maximum retries on JSON parsing failure (default: 3).
            retry_delay: Initial delay between retries in seconds (default: 1.0).
                Uses exponential backoff.
        """
        self.llm_client = llm_client
        self.retriever = retriever
        self.prompt_builder = prompt_builder or SecurityRequirementsPromptBuilder()
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def generate(
        self,
        user_story: UserStory,
        mode: GenerationMode = GenerationMode.RAG,
        system_context: Optional[str] = None,
    ) -> GenerationResult:
        """Generate security requirements for a user story.

        Args:
            user_story: The user story to generate requirements for.
            mode: Generation mode (RAG or ZERO_SHOT). Default is RAG.
            system_context: Optional system context YAML. If None and
                retriever is available, will fetch from vector store.

        Returns:
            GenerationResult with requirements and metadata.

        Raises:
            GenerationError: If generation fails after all retries.
            ValueError: If RAG mode requested but no retriever available.
        """
        start_time = time.perf_counter()

        # Validate mode
        if mode == GenerationMode.RAG and self.retriever is None:
            raise ValueError(
                "RAG mode requested but no retriever provided. "
                "Either provide a retriever or use ZERO_SHOT mode."
            )

        # Get system context
        if system_context is None and self.retriever is not None:
            system_context = self.retriever.get_system_context()

        # Retrieve context if RAG mode
        retrieved_context = ""
        retrieval_result: Optional[RetrievalResult] = None
        retrieval_latency_ms: Optional[float] = None

        if mode == GenerationMode.RAG and self.retriever is not None:
            retrieval_result = self.retriever.retrieve_for_user_story(user_story)
            retrieval_latency_ms = retrieval_result.retrieval_time_ms
            retrieved_context = self.retriever.format_result_for_prompt(retrieval_result)

        # Build prompt
        if mode == GenerationMode.RAG:
            user_prompt = self.prompt_builder.build_rag_prompt(
                user_story=user_story,
                retrieved_context=retrieved_context,
                system_context=system_context,
            )
        else:
            user_prompt = self.prompt_builder.build_zero_shot_prompt(
                user_story=user_story,
                system_context=system_context,
            )

        system_prompt = self.prompt_builder.build_system_prompt()

        # Generate with retry logic
        response, retry_count = self._generate_with_retry(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )

        # Parse response
        requirements, threat_mappings = self._parse_response(
            response=response,
            user_story=user_story,
        )

        total_latency_ms = (time.perf_counter() - start_time) * 1000

        # Build result
        return GenerationResult(
            user_story=user_story,
            requirements=requirements,
            threat_mappings=threat_mappings,
            generated_at=datetime.now(),
            model_used=self.llm_client.model_name,
            rag_enabled=(mode == GenerationMode.RAG),
            retrieval_context=retrieved_context if mode == GenerationMode.RAG else None,
        )

    def generate_batch(
        self,
        user_stories: list[UserStory],
        mode: GenerationMode = GenerationMode.RAG,
        system_context: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> list[GenerationResult]:
        """Generate requirements for multiple user stories.

        Args:
            user_stories: List of user stories to process.
            mode: Generation mode for all stories.
            system_context: Optional system context (shared across all).
            progress_callback: Optional callback(current, total) for progress updates.

        Returns:
            List of GenerationResult objects.
        """
        results = []
        total = len(user_stories)

        for i, story in enumerate(user_stories):
            if progress_callback:
                progress_callback(i, total)

            result = self.generate(
                user_story=story,
                mode=mode,
                system_context=system_context,
            )
            results.append(result)

        if progress_callback:
            progress_callback(total, total)

        return results

    def _generate_with_retry(
        self,
        user_prompt: str,
        system_prompt: str,
    ) -> tuple[LLMResponse, int]:
        """Generate with retry on JSON parsing failure.

        Args:
            user_prompt: The user prompt to send.
            system_prompt: The system prompt to send.

        Returns:
            Tuple of (LLMResponse, retry_count).

        Raises:
            GenerationError: If all retries exhausted.
        """
        last_error: Optional[Exception] = None
        retry_count = 0

        for attempt in range(self.max_retries):
            try:
                response = self.llm_client.generate(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    format="json",  # Enable Ollama JSON mode
                )

                # Try to extract JSON to validate response
                self._extract_json(response.content)

                return response, retry_count

            except json.JSONDecodeError as e:
                last_error = e
                retry_count = attempt + 1
                logger.warning(
                    f"JSON parsing failed (attempt {retry_count}/{self.max_retries}): {e}"
                )

                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    time.sleep(delay)

        raise GenerationError(
            f"Failed to generate valid JSON after {self.max_retries} attempts",
            details=f"Last error: {last_error}\nLast response: {response.content[:500] if 'response' in dir() else 'N/A'}",
        )

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from LLM response.

        Handles responses wrapped in markdown code blocks (```json ... ```)
        or raw JSON objects.

        Args:
            text: The raw LLM response text.

        Returns:
            Parsed JSON as a dictionary.

        Raises:
            json.JSONDecodeError: If no valid JSON found.
        """
        # First, try to find JSON in markdown code block
        code_block_match = self._JSON_CODE_BLOCK_PATTERN.search(text)
        if code_block_match:
            json_str = code_block_match.group(1).strip()
            return json.loads(json_str)

        # Try to find raw JSON object
        json_match = self._JSON_OBJECT_PATTERN.search(text)
        if json_match:
            json_str = json_match.group(0)
            return json.loads(json_str)

        # Try parsing the entire text as JSON
        return json.loads(text.strip())

    def _parse_response(
        self,
        response: LLMResponse,
        user_story: UserStory,
    ) -> tuple[list[SecurityRequirement], list[ThreatAgentMapping]]:
        """Parse LLM response into structured requirements.

        Args:
            response: The LLM response to parse.
            user_story: The user story (for linking requirements).

        Returns:
            Tuple of (requirements list, threat mappings list).
        """
        data = self._extract_json(response.content)

        requirements = []
        threat_mappings = []

        # Parse requirements
        raw_requirements = data.get("requirements", [])
        for i, raw_req in enumerate(raw_requirements):
            try:
                req = self._parse_requirement(raw_req, user_story, i)
                requirements.append(req)
            except (ValidationError, KeyError, ValueError) as e:
                logger.warning(f"Failed to parse requirement {i}: {e}")
                continue

        # Parse threat mappings if present
        raw_threats = data.get("threat_mappings", [])
        for i, raw_threat in enumerate(raw_threats):
            try:
                threat = self._parse_threat_mapping(raw_threat)
                threat_mappings.append(threat)
            except (ValidationError, KeyError, ValueError) as e:
                logger.warning(f"Failed to parse threat mapping {i}: {e}")
                continue

        return requirements, threat_mappings

    def _parse_requirement(
        self,
        raw: dict,
        user_story: UserStory,
        index: int,
    ) -> SecurityRequirement:
        """Parse a single requirement from raw JSON.

        Args:
            raw: Raw requirement dictionary from LLM.
            user_story: Parent user story.
            index: Index for generating fallback ID.

        Returns:
            Validated SecurityRequirement.
        """
        # Generate ID if not provided or invalid
        req_id = raw.get("id", f"SR-{index + 1:03d}")

        # Normalize ASVS category
        asvs_category = self._normalize_asvs_category(
            raw.get("asvs_category", "V1: Architecture, Design and Threat Modeling")
        )

        # Normalize priority
        priority = self._normalize_priority(raw.get("priority", "medium"))

        # Build requirement
        return SecurityRequirement(
            id=req_id,
            user_story_id=raw.get("user_story_id", user_story.id),
            title=raw.get("title", "Untitled Requirement"),
            description=raw.get("description", ""),
            rationale=raw.get("rationale", ""),
            acceptance_criteria=raw.get("acceptance_criteria", []),
            asvs_category=asvs_category,
            priority=priority,
            implementation_notes=raw.get("implementation_notes"),
            source_references=raw.get("source_references", []),
        )

    def _parse_threat_mapping(self, raw: dict) -> ThreatAgentMapping:
        """Parse a single threat mapping from raw JSON.

        Args:
            raw: Raw threat mapping dictionary from LLM.

        Returns:
            Validated ThreatAgentMapping.
        """
        return ThreatAgentMapping(
            category=raw.get("category", "unknown"),
            attack_vector=raw.get("attack_vector", ""),
            complexity_capability=raw.get("complexity_capability", "intermediate"),
            relevance_ranking=min(5, max(1, int(raw.get("relevance_ranking", 3)))),
        )

    def _normalize_asvs_category(self, value: str) -> ASVSCategory:
        """Normalize ASVS category string to enum value.

        Handles various formats LLMs might produce:
        - "V2: Authentication"
        - "V2"
        - "Authentication"
        - "V2: Authentication Verification Requirements"

        Args:
            value: Raw ASVS category string.

        Returns:
            Matching ASVSCategory enum value.
        """
        value_lower = value.lower().strip()

        # Try exact match first
        for cat in ASVSCategory:
            if value_lower == cat.value.lower():
                return cat

        # Try matching by V-number
        v_match = re.match(r"v?(\d+)", value_lower)
        if v_match:
            v_num = int(v_match.group(1))
            for cat in ASVSCategory:
                if cat.value.startswith(f"V{v_num}:"):
                    return cat

        # Try matching by keyword
        keyword_map = {
            "architecture": ASVSCategory.V1_ARCHITECTURE,
            "design": ASVSCategory.V1_ARCHITECTURE,
            "threat": ASVSCategory.V1_ARCHITECTURE,
            "authentication": ASVSCategory.V2_AUTHENTICATION,
            "auth": ASVSCategory.V2_AUTHENTICATION,
            "session": ASVSCategory.V3_SESSION,
            "access": ASVSCategory.V4_ACCESS_CONTROL,
            "authorization": ASVSCategory.V4_ACCESS_CONTROL,
            "validation": ASVSCategory.V5_VALIDATION,
            "sanitization": ASVSCategory.V5_VALIDATION,
            "encoding": ASVSCategory.V5_VALIDATION,
            "input": ASVSCategory.V5_VALIDATION,
            "cryptograph": ASVSCategory.V6_CRYPTOGRAPHY,
            "encrypt": ASVSCategory.V6_CRYPTOGRAPHY,
            "error": ASVSCategory.V7_ERROR_LOGGING,
            "logging": ASVSCategory.V7_ERROR_LOGGING,
            "data protection": ASVSCategory.V8_DATA_PROTECTION,
            "privacy": ASVSCategory.V8_DATA_PROTECTION,
            "communication": ASVSCategory.V9_COMMUNICATION,
            "transport": ASVSCategory.V9_COMMUNICATION,
            "tls": ASVSCategory.V9_COMMUNICATION,
            "malicious": ASVSCategory.V10_MALICIOUS_CODE,
            "business": ASVSCategory.V11_BUSINESS_LOGIC,
            "file": ASVSCategory.V12_FILES_RESOURCES,
            "resource": ASVSCategory.V12_FILES_RESOURCES,
            "api": ASVSCategory.V13_API_WEB_SERVICE,
            "web service": ASVSCategory.V13_API_WEB_SERVICE,
            "config": ASVSCategory.V14_CONFIGURATION,
        }

        for keyword, category in keyword_map.items():
            if keyword in value_lower:
                return category

        # Default to V1 Architecture
        logger.warning(f"Unknown ASVS category '{value}', defaulting to V1")
        return ASVSCategory.V1_ARCHITECTURE

    def _normalize_priority(self, value: str) -> Priority:
        """Normalize priority string to enum value.

        Args:
            value: Raw priority string.

        Returns:
            Matching Priority enum value.
        """
        value_lower = value.lower().strip()

        priority_map = {
            "critical": Priority.CRITICAL,
            "crit": Priority.CRITICAL,
            "highest": Priority.CRITICAL,
            "high": Priority.HIGH,
            "important": Priority.HIGH,
            "medium": Priority.MEDIUM,
            "med": Priority.MEDIUM,
            "moderate": Priority.MEDIUM,
            "low": Priority.LOW,
            "minor": Priority.LOW,
        }

        return priority_map.get(value_lower, Priority.MEDIUM)
