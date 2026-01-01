"""LLM-as-Judge module for requirement quality verification.

This module provides a verification layer that uses an LLM to score
generated security requirements for relevance, specificity, and actionability.
Requirements that don't meet threshold scores are filtered out.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

from secreqgen.generation.llm_client import OllamaClient, LLMResponse
from secreqgen.generation.schemas import SecurityRequirement
from secreqgen.config import VerificationConfig


JUDGE_SYSTEM_PROMPT = """You are a security requirements expert evaluating the quality of generated security requirements.

For each requirement, you must assess:
1. RELEVANCE (1-10): How relevant is this requirement to the given user story's security concerns?
2. SPECIFICITY (1-10): How specific and detailed is this requirement? Does it describe concrete security controls?
3. ACTIONABILITY (1-10): Can a developer implement this requirement? Is it clear what needs to be done?

A good security requirement should:
- Address specific security threats or vulnerabilities
- Be concrete (not vague like "ensure security")
- Reference specific mechanisms (e.g., "validate input using allowlist" not just "validate input")
- Be implementable and testable

You MUST respond with ONLY valid JSON in this exact format:
{
    "relevance": <score 1-10>,
    "specificity": <score 1-10>,
    "actionability": <score 1-10>,
    "reasoning": "<brief explanation>",
    "keep": <true/false>
}

Do not include any other text before or after the JSON."""


JUDGE_USER_PROMPT_TEMPLATE = """Evaluate this security requirement for the given user story:

## User Story
**Title:** {story_title}
**Story:** {story_text}

## Security Requirement to Evaluate
**Title:** {req_title}
**Description:** {req_description}
**ASVS Category:** {req_category}

Rate the requirement's relevance, specificity, and actionability (1-10 each).
Set "keep" to true if this is a valid, useful security requirement.
Respond with ONLY the JSON object."""


@dataclass
class JudgmentResult:
    """Result of judging a single requirement.

    Attributes:
        requirement_id: ID of the evaluated requirement.
        relevance_score: Relevance score (1-10).
        specificity_score: Specificity score (1-10).
        actionability_score: Actionability score (1-10).
        average_score: Average of the three scores.
        reasoning: LLM's explanation for the scores.
        keep: Whether the requirement passed verification.
        raw_response: Full LLM response for debugging.
    """
    requirement_id: str
    relevance_score: float
    specificity_score: float
    actionability_score: float
    average_score: float
    reasoning: str
    keep: bool
    raw_response: Optional[str] = None


class RequirementJudge:
    """LLM-based judge for evaluating security requirement quality.

    Uses a secondary LLM pass to score each generated requirement and
    filter out low-quality outputs. This reduces hallucinations and
    improves precision.

    Example:
        >>> judge = RequirementJudge(model_name="qwen3:30b")
        >>> filtered = judge.filter_requirements(
        ...     requirements=generated_reqs,
        ...     user_story=story,
        ...     min_average_score=7.0,
        ... )
    """

    def __init__(
        self,
        model_name: str = "qwen3:30b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.1,
        timeout: float = 60.0,
    ) -> None:
        """Initialize the requirement judge.

        Args:
            model_name: Ollama model to use for judging.
            base_url: Ollama server URL.
            temperature: Sampling temperature (low for consistent scoring).
            timeout: Request timeout in seconds.
        """
        self.client = OllamaClient(
            model_name=model_name,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
        )
        self.model_name = model_name

    def judge_requirement(
        self,
        requirement: SecurityRequirement,
        story_title: str,
        story_text: str,
    ) -> JudgmentResult:
        """Judge a single requirement's quality.

        Args:
            requirement: The security requirement to evaluate.
            story_title: Title of the associated user story.
            story_text: Full text of the user story.

        Returns:
            JudgmentResult with scores and keep decision.
        """
        # Build the prompt
        user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            story_title=story_title,
            story_text=story_text,
            req_title=requirement.title,
            req_description=requirement.description,
            req_category=requirement.asvs_category or "Not specified",
        )

        # Call the LLM
        try:
            response = self.client.generate(
                prompt=user_prompt,
                system_prompt=JUDGE_SYSTEM_PROMPT,
                format="json",
            )
            raw_content = response.content
        except Exception as e:
            # On error, be conservative and keep the requirement
            return JudgmentResult(
                requirement_id=requirement.id,
                relevance_score=5.0,
                specificity_score=5.0,
                actionability_score=5.0,
                average_score=5.0,
                reasoning=f"Error during judgment: {e}",
                keep=True,
                raw_response=str(e),
            )

        # Parse the JSON response
        try:
            # Try to extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'\{[^{}]*\}', raw_content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(raw_content)

            relevance = float(data.get("relevance", 5))
            specificity = float(data.get("specificity", 5))
            actionability = float(data.get("actionability", 5))
            reasoning = str(data.get("reasoning", ""))
            keep = bool(data.get("keep", True))

            # Clamp scores to 1-10
            relevance = max(1.0, min(10.0, relevance))
            specificity = max(1.0, min(10.0, specificity))
            actionability = max(1.0, min(10.0, actionability))

            average_score = (relevance + specificity + actionability) / 3.0

            return JudgmentResult(
                requirement_id=requirement.id,
                relevance_score=relevance,
                specificity_score=specificity,
                actionability_score=actionability,
                average_score=average_score,
                reasoning=reasoning,
                keep=keep,
                raw_response=raw_content,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # Parse error - be conservative and keep
            return JudgmentResult(
                requirement_id=requirement.id,
                relevance_score=5.0,
                specificity_score=5.0,
                actionability_score=5.0,
                average_score=5.0,
                reasoning=f"Failed to parse judgment: {e}",
                keep=True,
                raw_response=raw_content,
            )

    def filter_requirements(
        self,
        requirements: list[SecurityRequirement],
        story_title: str,
        story_text: str,
        config: Optional[VerificationConfig] = None,
    ) -> tuple[list[SecurityRequirement], list[JudgmentResult]]:
        """Filter requirements by judging each one.

        Args:
            requirements: List of requirements to evaluate.
            story_title: Title of the associated user story.
            story_text: Full text of the user story.
            config: Verification config with thresholds. If None, uses defaults.

        Returns:
            Tuple of (filtered_requirements, all_judgments).
            filtered_requirements contains only those that passed.
            all_judgments contains results for all inputs.
        """
        if config is None:
            config = VerificationConfig()

        judgments: list[JudgmentResult] = []
        filtered: list[SecurityRequirement] = []

        for req in requirements:
            judgment = self.judge_requirement(req, story_title, story_text)
            judgments.append(judgment)

            # Determine if requirement passes
            passes = self._check_passes(judgment, config)

            if passes:
                filtered.append(req)

        return filtered, judgments

    def _check_passes(
        self,
        judgment: JudgmentResult,
        config: VerificationConfig,
    ) -> bool:
        """Check if a judgment passes the verification thresholds.

        Args:
            judgment: The judgment result to check.
            config: Verification config with thresholds.

        Returns:
            True if the requirement passes verification.
        """
        # If LLM said not to keep, respect that
        if not judgment.keep:
            return False

        if config.strict_mode:
            # All scores must meet their individual thresholds
            return (
                judgment.relevance_score >= config.min_relevance_score
                and judgment.specificity_score >= config.min_specificity_score
                and judgment.actionability_score >= config.min_actionability_score
            )
        else:
            # Average score must meet threshold
            return judgment.average_score >= config.min_average_score

    def judge_batch(
        self,
        requirements_by_story: dict[str, tuple[str, str, list[SecurityRequirement]]],
        config: Optional[VerificationConfig] = None,
    ) -> dict[str, tuple[list[SecurityRequirement], list[JudgmentResult]]]:
        """Judge requirements for multiple stories.

        Args:
            requirements_by_story: Dict mapping story_id to
                (story_title, story_text, requirements).
            config: Verification config with thresholds.

        Returns:
            Dict mapping story_id to (filtered_requirements, judgments).
        """
        results = {}

        for story_id, (title, text, reqs) in requirements_by_story.items():
            filtered, judgments = self.filter_requirements(
                requirements=reqs,
                story_title=title,
                story_text=text,
                config=config,
            )
            results[story_id] = (filtered, judgments)

        return results
