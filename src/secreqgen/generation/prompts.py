"""Prompt templates for security requirements generation.

Provides structured prompt management for RAG and zero-shot generation modes,
with support for external template files and variable substitution.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Optional

from secreqgen.generation.schemas import ASVSCategory, Priority, UserStory


@dataclass
class PromptTemplate:
    """A prompt template with variable substitution.

    Attributes:
        name: Unique identifier for the template.
        template: The template string with $variable placeholders.
        required_vars: List of required variable names.
        description: Human-readable description of the template's purpose.
    """

    name: str
    template: str
    required_vars: list[str] = field(default_factory=list)
    description: str = ""

    def format(self, **kwargs) -> str:
        """Format the template with provided variables.

        Args:
            **kwargs: Variable name-value pairs for substitution.

        Returns:
            Formatted template string.

        Raises:
            KeyError: If a required variable is missing.
        """
        # Check for required variables
        missing = [var for var in self.required_vars if var not in kwargs]
        if missing:
            raise KeyError(f"Missing required variables: {missing}")

        # Use safe_substitute to leave unknown variables unchanged
        return Template(self.template).safe_substitute(**kwargs)


# Default system prompt for security requirements generation
SYSTEM_PROMPT_TEMPLATE = """You are an expert Security Requirements Engineer with deep knowledge of:
- OWASP Application Security Verification Standard (ASVS) v4.0
- Secure Software Development Lifecycle (SSDLC) practices
- Cloud security (AWS, Azure, GCP)
- Common web application vulnerabilities and mitigations

Your task is to generate specific, actionable, and testable security requirements based on user stories.

Guidelines:
1. Be SPECIFIC - avoid generic requirements; tailor to the system context
2. Be ACTIONABLE - each requirement should have clear implementation guidance
3. Be TESTABLE - include acceptance criteria that can be verified
4. PRIORITIZE correctly - critical items affect authentication, authorization, or data protection
5. CITE sources - reference ASVS requirements, security standards, or provided documentation
6. Consider THREATS - think about who might attack this functionality and how

Always respond with valid JSON matching the specified schema."""

# RAG-enhanced prompt template
RAG_USER_PROMPT_TEMPLATE = """## System Context
$system_context

## User Story
**ID:** $story_id
**Title:** $story_title

**Story:** $story_text

**Acceptance Criteria:**
$acceptance_criteria

## Retrieved Security Documentation
The following security documentation is relevant to this user story:

$retrieved_context

## Task
Based on the user story, system context, and retrieved security documentation, generate security requirements that:
1. Address potential security risks in implementing this user story
2. Are grounded in the provided security documentation and standards
3. Include specific implementation guidance for the system's technology stack
4. Map to OWASP ASVS categories where applicable

## Output Format
Respond with a JSON object containing a "requirements" array. Each requirement must have:
$json_schema

IMPORTANT: Respond ONLY with valid JSON. Do not include any text before or after the JSON."""

# Zero-shot (baseline) prompt template - no retrieved context
ZERO_SHOT_USER_PROMPT_TEMPLATE = """## System Context
$system_context

## User Story
**ID:** $story_id
**Title:** $story_title

**Story:** $story_text

**Acceptance Criteria:**
$acceptance_criteria

## Task
Based on the user story and system context, generate security requirements that:
1. Address potential security risks in implementing this user story
2. Apply OWASP ASVS and security best practices
3. Include specific implementation guidance for the system's technology stack
4. Map to OWASP ASVS categories where applicable

## Output Format
Respond with a JSON object containing a "requirements" array. Each requirement must have:
$json_schema

IMPORTANT: Respond ONLY with valid JSON. Do not include any text before or after the JSON."""


def get_json_schema() -> str:
    """Get the JSON schema description for the expected output format.

    Returns:
        Human-readable description of the JSON schema.
    """
    return """
- id: string (format: "SR-XXX" where XXX is a 3-digit number)
- user_story_id: string (must match the input user story ID)
- title: string (concise title, max 100 characters)
- description: string (detailed requirement description)
- rationale: string (why this requirement is necessary)
- acceptance_criteria: array of strings (testable verification criteria)
- asvs_category: string (one of the OWASP ASVS v4.0 categories)
- priority: string (one of: "critical", "high", "medium", "low")
- implementation_notes: string or null (specific implementation guidance)
- source_references: array of strings (references to standards or documentation)

Valid ASVS categories:
- "V1: Architecture, Design and Threat Modeling"
- "V2: Authentication"
- "V3: Session Management"
- "V4: Access Control"
- "V5: Validation, Sanitization and Encoding"
- "V6: Stored Cryptography"
- "V7: Error Handling and Logging"
- "V8: Data Protection"
- "V9: Communication"
- "V10: Malicious Code"
- "V11: Business Logic"
- "V12: Files and Resources"
- "V13: API and Web Service"
- "V14: Configuration"

Valid priorities: "critical", "high", "medium", "low"
"""


class SecurityRequirementsPromptBuilder:
    """Builder for security requirements generation prompts.

    Supports both RAG-enhanced and zero-shot (baseline) modes,
    with optional loading of custom templates from external files.

    Example:
        >>> builder = SecurityRequirementsPromptBuilder()
        >>> user_story = UserStory(id="US-001", title="Login", story="As a user...")
        >>> prompt = builder.build_rag_prompt(user_story, retrieved_context="...")
    """

    def __init__(self, prompts_dir: Optional[Path] = None) -> None:
        """Initialize the prompt builder.

        Args:
            prompts_dir: Optional directory containing custom prompt templates.
                If provided, templates will be loaded from files:
                - system_prompt.txt
                - rag_user_prompt.txt
                - zero_shot_user_prompt.txt
        """
        self.prompts_dir = prompts_dir
        self._system_template = self._load_template("system_prompt.txt", SYSTEM_PROMPT_TEMPLATE)
        self._rag_template = self._load_template("rag_user_prompt.txt", RAG_USER_PROMPT_TEMPLATE)
        self._zero_shot_template = self._load_template(
            "zero_shot_user_prompt.txt", ZERO_SHOT_USER_PROMPT_TEMPLATE
        )

    def _load_template(self, filename: str, default: str) -> str:
        """Load a template from file or use default.

        Args:
            filename: Name of the template file.
            default: Default template to use if file not found.

        Returns:
            Template string.
        """
        if self.prompts_dir:
            template_path = self.prompts_dir / filename
            if template_path.exists():
                return template_path.read_text()
        return default

    def build_system_prompt(self) -> str:
        """Build the system prompt.

        Returns:
            Formatted system prompt string.
        """
        return self._system_template

    def build_rag_prompt(
        self,
        user_story: UserStory,
        retrieved_context: str,
        system_context: Optional[str] = None,
    ) -> str:
        """Build RAG-enhanced user prompt.

        Args:
            user_story: The user story to generate requirements for.
            retrieved_context: Retrieved security documentation context.
            system_context: Optional system context YAML (tech stack, etc.).

        Returns:
            Formatted user prompt string.
        """
        return self._format_user_prompt(
            template=self._rag_template,
            user_story=user_story,
            system_context=system_context,
            retrieved_context=retrieved_context,
        )

    def build_zero_shot_prompt(
        self,
        user_story: UserStory,
        system_context: Optional[str] = None,
    ) -> str:
        """Build zero-shot baseline prompt.

        Args:
            user_story: The user story to generate requirements for.
            system_context: Optional system context YAML (tech stack, etc.).

        Returns:
            Formatted user prompt string.
        """
        return self._format_user_prompt(
            template=self._zero_shot_template,
            user_story=user_story,
            system_context=system_context,
        )

    def _format_user_prompt(
        self,
        template: str,
        user_story: UserStory,
        system_context: Optional[str] = None,
        retrieved_context: Optional[str] = None,
    ) -> str:
        """Format a user prompt template.

        Args:
            template: The template string to format.
            user_story: The user story data.
            system_context: Optional system context.
            retrieved_context: Optional retrieved context (for RAG mode).

        Returns:
            Formatted prompt string.
        """
        # Format acceptance criteria as bullet list
        if user_story.acceptance_criteria:
            ac_formatted = "\n".join(f"- {ac}" for ac in user_story.acceptance_criteria)
        else:
            ac_formatted = "- No acceptance criteria provided"

        # Build substitution dict
        subs = {
            "story_id": user_story.id,
            "story_title": user_story.title,
            "story_text": user_story.story,
            "acceptance_criteria": ac_formatted,
            "system_context": system_context or "No system context provided.",
            "json_schema": get_json_schema(),
        }

        if retrieved_context is not None:
            subs["retrieved_context"] = retrieved_context

        return Template(template).safe_substitute(subs)

    def get_json_schema(self) -> str:
        """Get the JSON schema description.

        Returns:
            JSON schema description string.
        """
        return get_json_schema()

    def get_valid_asvs_categories(self) -> list[str]:
        """Get list of valid ASVS category strings.

        Returns:
            List of valid ASVS category values.
        """
        return [cat.value for cat in ASVSCategory]

    def get_valid_priorities(self) -> list[str]:
        """Get list of valid priority strings.

        Returns:
            List of valid priority values.
        """
        return [p.value for p in Priority]


def format_retrieved_context(
    chunks: list[str],
    sources: list[str],
    sections: list[str],
    scores: list[float],
) -> str:
    """Format retrieved chunks for injection into prompts.

    Args:
        chunks: List of chunk content strings.
        sources: List of source document paths.
        sections: List of section hierarchies.
        scores: List of similarity scores.

    Returns:
        Formatted context string for prompt injection.
    """
    if not chunks:
        return "No relevant security documentation found."

    formatted_parts = []
    for i, (chunk, source, section, score) in enumerate(
        zip(chunks, sources, sections, scores), 1
    ):
        # Extract filename from path
        source_name = Path(source).name if source else "Unknown"

        # Format section hierarchy
        section_str = section if section else "General"

        formatted_parts.append(
            f"### [{i}] {source_name} > {section_str} (Relevance: {score:.2f})\n{chunk}"
        )

    return "\n\n---\n\n".join(formatted_parts)
