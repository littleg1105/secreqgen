"""Pydantic schemas for security requirements generation."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ASVSCategory(str, Enum):
    """OWASP ASVS v4.0 verification categories."""

    V1_ARCHITECTURE = "V1: Architecture, Design and Threat Modeling"
    V2_AUTHENTICATION = "V2: Authentication"
    V3_SESSION = "V3: Session Management"
    V4_ACCESS_CONTROL = "V4: Access Control"
    V5_VALIDATION = "V5: Validation, Sanitization and Encoding"
    V6_CRYPTOGRAPHY = "V6: Stored Cryptography"
    V7_ERROR_LOGGING = "V7: Error Handling and Logging"
    V8_DATA_PROTECTION = "V8: Data Protection"
    V9_COMMUNICATION = "V9: Communication"
    V10_MALICIOUS_CODE = "V10: Malicious Code"
    V11_BUSINESS_LOGIC = "V11: Business Logic"
    V12_FILES_RESOURCES = "V12: Files and Resources"
    V13_API_WEB_SERVICE = "V13: API and Web Service"
    V14_CONFIGURATION = "V14: Configuration"


class Priority(str, Enum):
    """Security requirement priority levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserStory(BaseModel):
    """Represents a user story input for security requirements generation."""

    id: str = Field(..., description="Unique identifier for the user story")
    title: str = Field(..., description="Brief title of the user story")
    story: str = Field(
        ...,
        description="The user story in format: As a [role], I want [feature] so that [benefit]",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="List of acceptance criteria for the user story",
    )


class SecurityRequirement(BaseModel):
    """Represents a generated security requirement."""

    id: str = Field(..., description="Unique identifier for the requirement")
    user_story_id: str = Field(
        ..., description="ID of the user story this requirement addresses"
    )
    title: str = Field(..., description="Brief title of the security requirement")
    description: str = Field(
        ..., description="Detailed description of the security requirement"
    )
    rationale: str = Field(
        ..., description="Explanation of why this requirement is needed"
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Testable criteria for verifying the requirement is met",
    )
    asvs_category: ASVSCategory = Field(
        ..., description="OWASP ASVS category this requirement belongs to"
    )
    priority: Priority = Field(
        default=Priority.MEDIUM, description="Priority level of the requirement"
    )
    implementation_notes: Optional[str] = Field(
        default=None, description="Optional notes on how to implement the requirement"
    )
    source_references: list[str] = Field(
        default_factory=list,
        description="References to source documents (for RAG-generated requirements)",
    )


class ThreatAgentMapping(BaseModel):
    """Maps threat agents to security requirements."""

    category: str = Field(
        ..., description="Threat agent category (e.g., external_attacker, insider)"
    )
    attack_vector: str = Field(
        ..., description="Potential attack vector for this threat agent"
    )
    complexity_capability: str = Field(
        ..., description="Required capability level (low, intermediate, advanced)"
    )
    relevance_ranking: int = Field(
        ..., ge=1, le=5, description="Relevance ranking from 1 (low) to 5 (high)"
    )


class GenerationResult(BaseModel):
    """Result of security requirements generation for a user story."""

    user_story: UserStory = Field(..., description="The input user story")
    requirements: list[SecurityRequirement] = Field(
        default_factory=list, description="Generated security requirements"
    )
    threat_mappings: list[ThreatAgentMapping] = Field(
        default_factory=list, description="Threat agent mappings for the requirements"
    )
    generated_at: datetime = Field(
        default_factory=datetime.now, description="Timestamp of generation"
    )
    model_used: str = Field(..., description="LLM model used for generation")
    rag_enabled: bool = Field(
        ..., description="Whether RAG was used for generation"
    )
    retrieval_context: Optional[str] = Field(
        default=None, description="Retrieved context used (if RAG enabled)"
    )


class EvaluationResult(BaseModel):
    """Result of evaluating generated requirements against ground truth."""

    precision: float = Field(
        ..., ge=0.0, le=1.0, description="Precision score (0-1)"
    )
    recall: float = Field(..., ge=0.0, le=1.0, description="Recall score (0-1)")
    f1_score: float = Field(..., ge=0.0, le=1.0, description="F1 score (0-1)")
    hallucination_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Rate of hallucinated/unsupported requirements (0-1)",
    )
    true_positives: int = Field(
        default=0, description="Number of correctly matched requirements"
    )
    false_positives: int = Field(
        default=0, description="Number of generated requirements not in ground truth"
    )
    false_negatives: int = Field(
        default=0, description="Number of ground truth requirements not generated"
    )
    matched_requirements: list[tuple[str, str, float]] = Field(
        default_factory=list,
        description="List of (generated_id, ground_truth_id, similarity_score) tuples",
    )
