"""Unit tests for metadata extraction."""

import pytest

from secreqgen.ingestion.metadata import (
    ComplianceFramework,
    DocumentMetadata,
    DocumentType,
    MetadataExtractor,
    SecurityDomain,
)


class TestDocumentMetadata:
    """Tests for DocumentMetadata dataclass."""

    def test_default_metadata(self) -> None:
        """Test creating metadata with defaults."""
        metadata = DocumentMetadata()
        assert metadata.document_type == DocumentType.UNKNOWN
        assert metadata.security_domains == []
        assert metadata.technologies == []
        assert metadata.compliance_frameworks == []

    def test_metadata_with_values(self) -> None:
        """Test creating metadata with values."""
        metadata = DocumentMetadata(
            document_type=DocumentType.POLICY,
            security_domains=[SecurityDomain.AUTHENTICATION],
            technologies=["AWS", "Spring"],
            compliance_frameworks=[ComplianceFramework.GDPR],
        )
        assert metadata.document_type == DocumentType.POLICY
        assert SecurityDomain.AUTHENTICATION in metadata.security_domains
        assert "AWS" in metadata.technologies
        assert ComplianceFramework.GDPR in metadata.compliance_frameworks

    def test_to_dict(self) -> None:
        """Test converting metadata to dictionary."""
        metadata = DocumentMetadata(
            document_type=DocumentType.STANDARD,
            security_domains=[SecurityDomain.CRYPTOGRAPHY, SecurityDomain.AUTHENTICATION],
            technologies=["PostgreSQL"],
            compliance_frameworks=[ComplianceFramework.PCI_DSS],
        )
        result = metadata.to_dict()

        assert result["document_type"] == "standard"
        assert "cryptography" in result["security_domains"]
        assert "authentication" in result["security_domains"]
        assert "PostgreSQL" in result["technologies"]
        assert "pci_dss" in result["compliance_frameworks"]


class TestDocumentTypeDetection:
    """Tests for document type detection."""

    @pytest.fixture
    def extractor(self) -> MetadataExtractor:
        """Create a metadata extractor."""
        return MetadataExtractor()

    def test_detect_policy_from_path(self, extractor: MetadataExtractor) -> None:
        """Test detecting policy type from file path."""
        metadata = extractor.extract(
            content="Some generic content here.",
            source_path="/docs/policies/security-policy.md",
        )
        assert metadata.document_type == DocumentType.POLICY

    def test_detect_policy_from_content(self, extractor: MetadataExtractor) -> None:
        """Test detecting policy type from content keywords."""
        content = """
        Security Policy Document

        All users shall implement security measures.
        Systems must comply with security requirements.
        """
        metadata = extractor.extract(content=content, source_path="/docs/misc.md")
        assert metadata.document_type == DocumentType.POLICY

    def test_detect_architecture_from_path(self, extractor: MetadataExtractor) -> None:
        """Test detecting architecture type from file path."""
        metadata = extractor.extract(
            content="Component diagram here.",
            source_path="/docs/architecture/system-design.md",
        )
        assert metadata.document_type == DocumentType.ARCHITECTURE

    def test_detect_architecture_from_content(
        self, extractor: MetadataExtractor
    ) -> None:
        """Test detecting architecture type from content."""
        content = """
        System Architecture

        The system architecture includes the following components.
        The service layer handles business logic.
        """
        metadata = extractor.extract(content=content, source_path="/docs/design.md")
        assert metadata.document_type == DocumentType.ARCHITECTURE

    def test_detect_standard_from_path(self, extractor: MetadataExtractor) -> None:
        """Test detecting standard type from file path."""
        metadata = extractor.extract(
            content="Security guidelines.",
            source_path="/docs/standards/owasp-requirements.md",
        )
        assert metadata.document_type == DocumentType.STANDARD

    def test_detect_standard_from_content(self, extractor: MetadataExtractor) -> None:
        """Test detecting standard type from content."""
        content = """
        OWASP ASVS Requirements

        CWE-79: Improper Neutralization of Input
        Standard requirement for input validation.
        """
        metadata = extractor.extract(content=content, source_path="/docs/reqs.md")
        assert metadata.document_type == DocumentType.STANDARD

    def test_detect_system_context_yaml(self, extractor: MetadataExtractor) -> None:
        """Test detecting system context from YAML file."""
        content = """
        system:
          name: MyApp
          description: A web application
        security:
          authentication: oauth2
        """
        metadata = extractor.extract(
            content=content,
            source_path="/configs/system_context.yaml",
        )
        assert metadata.document_type == DocumentType.SYSTEM_CONTEXT

    def test_unknown_type(self, extractor: MetadataExtractor) -> None:
        """Test that unclear content returns UNKNOWN."""
        metadata = extractor.extract(
            content="Hello world, this is some random text.",
            source_path="/random/file.txt",
        )
        assert metadata.document_type == DocumentType.UNKNOWN


class TestSecurityDomainDetection:
    """Tests for security domain detection."""

    @pytest.fixture
    def extractor(self) -> MetadataExtractor:
        """Create a metadata extractor."""
        return MetadataExtractor()

    def test_detect_authentication(self, extractor: MetadataExtractor) -> None:
        """Test detecting authentication domain."""
        content = """
        Users must authenticate with multi-factor authentication.
        Password requirements include minimum 12 characters.
        OAuth2 is used for SSO integration.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.AUTHENTICATION in metadata.security_domains

    def test_detect_authorization(self, extractor: MetadataExtractor) -> None:
        """Test detecting authorization domain."""
        content = """
        Role-based access control (RBAC) is implemented.
        Users are granted permissions based on their role.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.AUTHORIZATION in metadata.security_domains

    def test_detect_cryptography(self, extractor: MetadataExtractor) -> None:
        """Test detecting cryptography domain."""
        content = """
        All data is encrypted using AES-256.
        TLS 1.3 is required for data in transit.
        Passwords are hashed with bcrypt.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.CRYPTOGRAPHY in metadata.security_domains

    def test_detect_input_validation(self, extractor: MetadataExtractor) -> None:
        """Test detecting input validation domain."""
        content = """
        All user input must be validated and sanitized.
        XSS prevention through proper output encoding.
        SQL injection prevention through parameterized queries.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.INPUT_VALIDATION in metadata.security_domains

    def test_detect_data_protection(self, extractor: MetadataExtractor) -> None:
        """Test detecting data protection domain."""
        content = """
        PII must be classified and handled according to GDPR.
        Data masking is required for sensitive information.
        Right to be forgotten must be supported.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.DATA_PROTECTION in metadata.security_domains

    def test_detect_multiple_domains(self, extractor: MetadataExtractor) -> None:
        """Test detecting multiple security domains."""
        content = """
        Users authenticate with OAuth2 and JWT tokens.
        Access is controlled through RBAC permissions.
        All data is encrypted with TLS in transit.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.AUTHENTICATION in metadata.security_domains
        assert SecurityDomain.AUTHORIZATION in metadata.security_domains
        assert SecurityDomain.CRYPTOGRAPHY in metadata.security_domains

    def test_domain_scores(self, extractor: MetadataExtractor) -> None:
        """Test that domain scores reflect keyword frequency."""
        content = """
        Password must be strong. Password policy requires complexity.
        Password reset uses secure tokens. Password hashing with bcrypt.
        Also uses encryption for data at rest.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")

        # Authentication should have higher score (more password mentions)
        assert "authentication" in metadata.domain_scores
        assert metadata.domain_scores["authentication"] >= 4  # "password" appears 4x


class TestTechnologyDetection:
    """Tests for technology detection."""

    @pytest.fixture
    def extractor(self) -> MetadataExtractor:
        """Create a metadata extractor."""
        return MetadataExtractor()

    def test_detect_cloud_providers(self, extractor: MetadataExtractor) -> None:
        """Test detecting cloud provider technologies."""
        content = "Deployed on AWS with EC2 instances and S3 storage."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        assert "AWS" in metadata.technologies
        assert "AWS EC2" in metadata.technologies
        assert "AWS S3" in metadata.technologies

    def test_detect_auth_providers(self, extractor: MetadataExtractor) -> None:
        """Test detecting authentication provider technologies."""
        content = "Using Keycloak for identity management with LDAP backend."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        assert "Keycloak" in metadata.technologies
        assert "LDAP" in metadata.technologies

    def test_detect_frameworks(self, extractor: MetadataExtractor) -> None:
        """Test detecting framework technologies."""
        content = "Backend built with Spring Boot, frontend with React."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        assert "Spring" in metadata.technologies
        assert "React" in metadata.technologies

    def test_detect_databases(self, extractor: MetadataExtractor) -> None:
        """Test detecting database technologies."""
        content = "Using PostgreSQL for data storage and Redis for caching."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        assert "PostgreSQL" in metadata.technologies
        assert "Redis" in metadata.technologies

    def test_detect_container_tech(self, extractor: MetadataExtractor) -> None:
        """Test detecting container technologies."""
        content = "Application runs in Docker containers on Kubernetes (k8s)."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        assert "Docker" in metadata.technologies
        assert "Kubernetes" in metadata.technologies

    def test_technologies_sorted(self, extractor: MetadataExtractor) -> None:
        """Test that technologies are returned sorted."""
        content = "Using Redis, PostgreSQL, AWS, and Docker."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        assert metadata.technologies == sorted(metadata.technologies)


class TestComplianceFrameworkDetection:
    """Tests for compliance framework detection."""

    @pytest.fixture
    def extractor(self) -> MetadataExtractor:
        """Create a metadata extractor."""
        return MetadataExtractor()

    def test_detect_owasp_asvs(self, extractor: MetadataExtractor) -> None:
        """Test detecting OWASP ASVS framework."""
        content = "Requirements aligned with OWASP ASVS level 2."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.OWASP_ASVS in metadata.compliance_frameworks

    def test_detect_gdpr(self, extractor: MetadataExtractor) -> None:
        """Test detecting GDPR compliance."""
        content = """
        GDPR compliance is required for EU users.
        Data subject rights must be respected.
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.GDPR in metadata.compliance_frameworks

    def test_detect_pci_dss(self, extractor: MetadataExtractor) -> None:
        """Test detecting PCI-DSS compliance."""
        content = "PCI-DSS compliance required for cardholder data."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.PCI_DSS in metadata.compliance_frameworks

    def test_detect_hipaa(self, extractor: MetadataExtractor) -> None:
        """Test detecting HIPAA compliance."""
        content = "HIPAA compliance for protected health information (PHI)."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.HIPAA in metadata.compliance_frameworks

    def test_detect_soc2(self, extractor: MetadataExtractor) -> None:
        """Test detecting SOC2 compliance."""
        content = "SOC 2 Type II certification with trust services criteria."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.SOC2 in metadata.compliance_frameworks

    def test_detect_iso27001(self, extractor: MetadataExtractor) -> None:
        """Test detecting ISO 27001 compliance."""
        content = "ISO 27001 certified ISMS implementation."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.ISO27001 in metadata.compliance_frameworks

    def test_detect_nist(self, extractor: MetadataExtractor) -> None:
        """Test detecting NIST compliance."""
        content = "Following NIST 800-53 security controls and CSF."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.NIST in metadata.compliance_frameworks

    def test_detect_multiple_frameworks(self, extractor: MetadataExtractor) -> None:
        """Test detecting multiple compliance frameworks."""
        content = """
        Compliance requirements:
        - GDPR for data protection
        - PCI-DSS for payment processing
        - SOC 2 for service organization controls
        """
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert ComplianceFramework.GDPR in metadata.compliance_frameworks
        assert ComplianceFramework.PCI_DSS in metadata.compliance_frameworks
        assert ComplianceFramework.SOC2 in metadata.compliance_frameworks


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.fixture
    def extractor(self) -> MetadataExtractor:
        """Create a metadata extractor."""
        return MetadataExtractor()

    def test_empty_content(self, extractor: MetadataExtractor) -> None:
        """Test extraction from empty content."""
        metadata = extractor.extract(content="", source_path="/doc.md")
        assert metadata.document_type == DocumentType.UNKNOWN
        assert metadata.security_domains == []
        assert metadata.technologies == []
        assert metadata.compliance_frameworks == []

    def test_whitespace_only(self, extractor: MetadataExtractor) -> None:
        """Test extraction from whitespace-only content."""
        metadata = extractor.extract(content="   \n\n\t  ", source_path="/doc.md")
        assert metadata.document_type == DocumentType.UNKNOWN

    def test_case_insensitive_matching(self, extractor: MetadataExtractor) -> None:
        """Test that matching is case-insensitive."""
        content = "AWS, aws, Aws - all should match. GDPR, gdpr, Gdpr too."
        metadata = extractor.extract(content=content, source_path="/doc.md")

        # Should only have one AWS entry (normalized)
        assert metadata.technologies.count("AWS") == 1
        assert ComplianceFramework.GDPR in metadata.compliance_frameworks

    def test_word_boundary_matching(self, extractor: MetadataExtractor) -> None:
        """Test that keywords match on word boundaries."""
        # "authenticate" should not match just "auth"
        content = "Users must authenticate to access the system."
        metadata = extractor.extract(content=content, source_path="/doc.md")
        assert SecurityDomain.AUTHENTICATION in metadata.security_domains

    def test_special_characters_in_path(self, extractor: MetadataExtractor) -> None:
        """Test extraction with special characters in path."""
        metadata = extractor.extract(
            content="Security policy content.",
            source_path="/docs/My Policies (2024)/security-policy.md",
        )
        # Should still work despite spaces and parentheses
        assert metadata.document_type == DocumentType.POLICY


class TestIntegration:
    """Integration tests for metadata extraction with other ingestion components."""

    def test_extract_from_loaded_document(self) -> None:
        """Test extracting metadata from a LoadedDocument."""
        from secreqgen.ingestion.loader import LoadedDocument
        from pathlib import Path

        doc = LoadedDocument(
            source_path=Path("/policies/auth-policy.md"),
            content="""
            # Authentication Policy

            All users must authenticate using OAuth2.
            Passwords must be hashed with bcrypt.
            MFA is required for admin accounts.

            Compliant with OWASP ASVS and GDPR requirements.
            """,
            format="markdown",
        )

        extractor = MetadataExtractor()
        metadata = extractor.extract(
            content=doc.content,
            source_path=str(doc.source_path),
        )

        assert metadata.document_type == DocumentType.POLICY
        assert SecurityDomain.AUTHENTICATION in metadata.security_domains
        assert SecurityDomain.CRYPTOGRAPHY in metadata.security_domains
        assert ComplianceFramework.OWASP_ASVS in metadata.compliance_frameworks
        assert ComplianceFramework.GDPR in metadata.compliance_frameworks
