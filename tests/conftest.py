"""Pytest fixtures for SecReqGen tests."""

from pathlib import Path

import pytest

from secreqgen.config import SecReqGenConfig
from secreqgen.generation.schemas import (
    ASVSCategory,
    Priority,
    SecurityRequirement,
    UserStory,
)


@pytest.fixture
def sample_config() -> SecReqGenConfig:
    """Create a sample configuration for testing."""
    return SecReqGenConfig()


@pytest.fixture
def sample_user_story() -> UserStory:
    """Create a sample user story for testing."""
    return UserStory(
        id="US-001",
        title="User Login",
        story="As a user, I want to log in to the application so that I can access my account",
        acceptance_criteria=[
            "User can enter username and password",
            "User receives feedback on login success/failure",
            "User is redirected to dashboard after successful login",
        ],
    )


@pytest.fixture
def sample_user_stories() -> list[UserStory]:
    """Create a list of sample user stories for testing."""
    return [
        UserStory(
            id="US-001",
            title="User Login",
            story="As a user, I want to log in to the application so that I can access my account",
            acceptance_criteria=[
                "User can enter username and password",
                "User receives feedback on login success/failure",
            ],
        ),
        UserStory(
            id="US-002",
            title="Password Reset",
            story="As a user, I want to reset my password so that I can regain access if I forget it",
            acceptance_criteria=[
                "User can request password reset via email",
                "Reset link expires after 24 hours",
            ],
        ),
        UserStory(
            id="US-003",
            title="File Upload",
            story="As a user, I want to upload files so that I can share documents with my team",
            acceptance_criteria=[
                "User can select files from their device",
                "Upload progress is displayed",
                "User receives confirmation after successful upload",
            ],
        ),
    ]


@pytest.fixture
def sample_security_requirement() -> SecurityRequirement:
    """Create a sample security requirement for testing."""
    return SecurityRequirement(
        id="SR-001",
        user_story_id="US-001",
        title="Secure Password Storage",
        description="User passwords must be hashed using a strong, adaptive hashing algorithm",
        rationale="Plain text password storage is a critical vulnerability that can lead to mass credential compromise",
        acceptance_criteria=[
            "Passwords are hashed using bcrypt, scrypt, or Argon2",
            "Password hashes are salted with unique, random salts",
            "Hash parameters are configured for adequate security",
        ],
        asvs_category=ASVSCategory.V2_AUTHENTICATION,
        priority=Priority.CRITICAL,
        implementation_notes="Use bcrypt with cost factor >= 10 or Argon2id",
        source_references=["OWASP ASVS 4.0 V2.4.1"],
    )


@pytest.fixture
def temp_vectordb(tmp_path: Path) -> Path:
    """Create a temporary directory for vector database testing."""
    vectordb_path = tmp_path / "vectordb"
    vectordb_path.mkdir(parents=True, exist_ok=True)
    return vectordb_path


@pytest.fixture
def sample_system_context() -> dict:
    """Create a sample system context for testing."""
    return {
        "system": {
            "name": "Test Application",
            "description": "A test web application",
            "version": "1.0.0",
        },
        "architecture": {
            "type": "web_application",
            "deployment": "cloud",
            "cloud_provider": "aws",
            "components": [
                {"name": "frontend", "type": "spa", "technology": "React"},
                {"name": "backend", "type": "api", "technology": "Python/FastAPI"},
                {"name": "database", "type": "relational", "technology": "PostgreSQL"},
            ],
        },
        "security": {
            "authentication": {"method": "oauth2", "mfa_enabled": True},
            "authorization": {"model": "rbac"},
            "data_classification": [
                {"category": "pii", "sensitivity": "high"},
                {"category": "operational", "sensitivity": "medium"},
            ],
        },
        "compliance": {
            "frameworks": ["OWASP ASVS"],
            "regulations": ["GDPR"],
        },
    }


@pytest.fixture
def sample_documents_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with sample documents."""
    docs_dir = tmp_path / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # Create a sample policy document
    policy_file = docs_dir / "security_policy.txt"
    policy_file.write_text(
        """Security Policy Document

1. Authentication Requirements
- All users must authenticate before accessing the system
- Multi-factor authentication is required for admin accounts
- Passwords must be at least 12 characters

2. Data Protection
- All sensitive data must be encrypted at rest
- Data in transit must use TLS 1.2 or higher
- PII must be handled according to GDPR requirements
"""
    )

    return docs_dir
