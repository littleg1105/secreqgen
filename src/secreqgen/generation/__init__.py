"""Generation module for SecReqGen."""

from secreqgen.generation.generator import (
    GenerationMetrics,
    GenerationMode,
    SecurityRequirementsGenerator,
)
from secreqgen.generation.llm_client import LLMResponse, OllamaClient
from secreqgen.generation.prompts import (
    PromptTemplate,
    SecurityRequirementsPromptBuilder,
    format_retrieved_context,
)
from secreqgen.generation.schemas import (
    ASVSCategory,
    EvaluationResult,
    GenerationResult,
    Priority,
    SecurityRequirement,
    ThreatAgentMapping,
    UserStory,
)

__all__ = [
    # Generator
    "SecurityRequirementsGenerator",
    "GenerationMode",
    "GenerationMetrics",
    # LLM Client
    "OllamaClient",
    "LLMResponse",
    # Prompts
    "PromptTemplate",
    "SecurityRequirementsPromptBuilder",
    "format_retrieved_context",
    # Schemas
    "ASVSCategory",
    "Priority",
    "UserStory",
    "SecurityRequirement",
    "ThreatAgentMapping",
    "GenerationResult",
    "EvaluationResult",
]
