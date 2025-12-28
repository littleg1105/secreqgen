"""Metadata extraction for security documents.

Extracts document type, security domains, technologies, and compliance
frameworks from document content using keyword pattern matching.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class DocumentType(str, Enum):
    """Type of security document."""

    POLICY = "policy"
    ARCHITECTURE = "architecture"
    STANDARD = "standard"
    SYSTEM_CONTEXT = "system_context"
    UNKNOWN = "unknown"


class SecurityDomain(str, Enum):
    """Security domain/category for requirements."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    CRYPTOGRAPHY = "cryptography"
    INPUT_VALIDATION = "input_validation"
    SESSION_MANAGEMENT = "session_management"
    ERROR_HANDLING = "error_handling"
    DATA_PROTECTION = "data_protection"
    API_SECURITY = "api_security"
    FILE_HANDLING = "file_handling"
    CONFIGURATION = "configuration"
    LOGGING_MONITORING = "logging_monitoring"
    NETWORK_SECURITY = "network_security"
    SECURE_DEVELOPMENT = "secure_development"


class ComplianceFramework(str, Enum):
    """Compliance and security frameworks."""

    OWASP_ASVS = "owasp_asvs"
    GDPR = "gdpr"
    PCI_DSS = "pci_dss"
    HIPAA = "hipaa"
    SOC2 = "soc2"
    ISO27001 = "iso27001"
    NIST = "nist"
    CIS = "cis"


@dataclass
class DocumentMetadata:
    """Extracted metadata from a document or chunk."""

    document_type: DocumentType = DocumentType.UNKNOWN
    security_domains: list[SecurityDomain] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    compliance_frameworks: list[ComplianceFramework] = field(default_factory=list)
    domain_scores: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage in vector DB metadata."""
        return {
            "document_type": self.document_type.value,
            "security_domains": [d.value for d in self.security_domains],
            "technologies": self.technologies,
            "compliance_frameworks": [f.value for f in self.compliance_frameworks],
        }


class MetadataExtractor:
    """Extracts security-relevant metadata from document content.

    Uses keyword pattern matching to detect:
    - Document type (policy, architecture, standard, system_context)
    - Security domains (authentication, cryptography, etc.)
    - Technologies mentioned (AWS, Spring, PostgreSQL, etc.)
    - Compliance frameworks (GDPR, PCI-DSS, OWASP ASVS, etc.)
    """

    # Document type detection patterns
    # Format: (pattern, DocumentType, weight)
    DOCUMENT_TYPE_PATTERNS: list[tuple[str, DocumentType, int]] = [
        # Path-based patterns (higher weight)
        (r"/policies?/", DocumentType.POLICY, 10),
        (r"/arch(itecture)?/", DocumentType.ARCHITECTURE, 10),
        (r"/standards?/", DocumentType.STANDARD, 10),
        (r"system[_-]?context", DocumentType.SYSTEM_CONTEXT, 10),
        (r"context\.ya?ml$", DocumentType.SYSTEM_CONTEXT, 10),
        # Content-based patterns (lower weight)
        (r"\bsecurity\s+policy\b", DocumentType.POLICY, 5),
        (r"\bpolicy\s+document\b", DocumentType.POLICY, 5),
        (r"\bshall\b.*\bsecurity\b", DocumentType.POLICY, 3),
        (r"\bmust\b.*\bsecurity\b", DocumentType.POLICY, 3),
        (r"\barchitecture\s+diagram\b", DocumentType.ARCHITECTURE, 5),
        (r"\bsystem\s+architecture\b", DocumentType.ARCHITECTURE, 5),
        (r"\bcomponent\s+design\b", DocumentType.ARCHITECTURE, 4),
        (r"\bservice\s+layer\b", DocumentType.ARCHITECTURE, 3),
        (r"\bowasp\s+asvs\b", DocumentType.STANDARD, 5),
        (r"\bcwe-\d+\b", DocumentType.STANDARD, 4),
        (r"\bstandard\s+requirement\b", DocumentType.STANDARD, 4),
        (r"\bguideline\b", DocumentType.STANDARD, 2),
    ]

    # Security domain detection patterns
    # Format: (SecurityDomain, list of keyword patterns)
    SECURITY_DOMAIN_PATTERNS: dict[SecurityDomain, list[str]] = {
        SecurityDomain.AUTHENTICATION: [
            r"\bauth(entication|enticate)?\b",
            r"\blogin\b",
            r"\blogout\b",
            r"\bpassword\b",
            r"\bcredential\b",
            r"\bmfa\b",
            r"\b2fa\b",
            r"\bmulti[- ]?factor\b",
            r"\boauth\b",
            r"\bsaml\b",
            r"\bjwt\b",
            r"\boidc\b",
            r"\bsso\b",
            r"\bsingle\s+sign[- ]?on\b",
        ],
        SecurityDomain.AUTHORIZATION: [
            r"\bauthoriz(ation|e)\b",
            r"\bpermission\b",
            r"\brole\b",
            r"\brbac\b",
            r"\babac\b",
            r"\baccess\s+control\b",
            r"\bprivilege\b",
            r"\bacl\b",
            r"\bgrant\b",
            r"\bdeny\b",
        ],
        SecurityDomain.CRYPTOGRAPHY: [
            r"\bencrypt(ion)?\b",
            r"\bdecrypt(ion)?\b",
            r"\bhash(ing)?\b",
            r"\bcipher\b",
            r"\bssl\b",
            r"\btls\b",
            r"\bcertificate\b",
            r"\bprivate\s+key\b",
            r"\bpublic\s+key\b",
            r"\baes\b",
            r"\brsa\b",
            r"\bhmac\b",
            r"\bsalt(ing)?\b",
            r"\bkdf\b",
            r"\bargon2\b",
            r"\bbcrypt\b",
            r"\bscrypt\b",
        ],
        SecurityDomain.INPUT_VALIDATION: [
            r"\bvalidat(e|ion)\b",
            r"\bsanitiz(e|ation)\b",
            r"\binput\s+(validation|filtering)\b",
            r"\bxss\b",
            r"\bcross[- ]?site\s+scripting\b",
            r"\binjection\b",
            r"\bsql\s+injection\b",
            r"\bescape\b",
            r"\bwhitelist\b",
            r"\bblacklist\b",
            r"\ballowlist\b",
            r"\bdenylist\b",
        ],
        SecurityDomain.SESSION_MANAGEMENT: [
            r"\bsession\b",
            r"\bcookie\b",
            r"\btoken\b",
            r"\bexpir(e|ation|y)\b",
            r"\btimeout\b",
            r"\bidle\s+timeout\b",
            r"\brefresh\s+token\b",
        ],
        SecurityDomain.ERROR_HANDLING: [
            r"\berror\s+handl(e|ing)\b",
            r"\bexception\b",
            r"\bstack\s+trace\b",
            r"\berror\s+message\b",
            r"\bfail\s+secure\b",
            r"\bfail\s+safe\b",
        ],
        SecurityDomain.DATA_PROTECTION: [
            r"\bpii\b",
            r"\bpersonal(ly)?\s+(identifiable\s+)?information\b",
            r"\bsensitive\s+data\b",
            r"\bdata\s+protection\b",
            r"\bdata\s+privacy\b",
            r"\bdata\s+classification\b",
            r"\bmask(ing)?\b",
            r"\banonymiz(e|ation)\b",
            r"\bpseudonymiz(e|ation)\b",
            r"\bdata\s+retention\b",
            r"\bright\s+to\s+be\s+forgotten\b",
        ],
        SecurityDomain.API_SECURITY: [
            r"\bapi\s+security\b",
            r"\brest\s+api\b",
            r"\bgraphql\b",
            r"\bendpoint\b",
            r"\brate\s+limit(ing)?\b",
            r"\bthrottl(e|ing)\b",
            r"\bcors\b",
            r"\bapi\s+key\b",
            r"\bapi\s+gateway\b",
        ],
        SecurityDomain.FILE_HANDLING: [
            r"\bfile\s+upload\b",
            r"\bfile\s+download\b",
            r"\bpath\s+traversal\b",
            r"\bdirectory\s+traversal\b",
            r"\bfile\s+type\b",
            r"\bfile\s+extension\b",
            r"\bmime\s+type\b",
            r"\bmalware\b",
            r"\bantivirus\b",
        ],
        SecurityDomain.CONFIGURATION: [
            r"\bconfiguration\s+management\b",
            r"\bsecret\s+management\b",
            r"\benvironment\s+variable\b",
            r"\bconfig(uration)?\s+file\b",
            r"\bhardcoded\b",
            r"\bdefault\s+(password|credential)\b",
            r"\bsecurity\s+header\b",
        ],
        SecurityDomain.LOGGING_MONITORING: [
            r"\blog(ging)?\b",
            r"\baudit\s+(log|trail)\b",
            r"\bmonitor(ing)?\b",
            r"\balert(ing)?\b",
            r"\bsiem\b",
            r"\bintrusion\s+detection\b",
            r"\bids\b",
            r"\bips\b",
        ],
        SecurityDomain.NETWORK_SECURITY: [
            r"\bfirewall\b",
            r"\bnetwork\s+segmentation\b",
            r"\bvpn\b",
            r"\bdmz\b",
            r"\bwaf\b",
            r"\bweb\s+application\s+firewall\b",
            r"\bddos\b",
            r"\bload\s+balancer\b",
        ],
        SecurityDomain.SECURE_DEVELOPMENT: [
            r"\bsecure\s+(coding|development)\b",
            r"\bsast\b",
            r"\bdast\b",
            r"\bstatic\s+analysis\b",
            r"\bdynamic\s+analysis\b",
            r"\bpenetration\s+test(ing)?\b",
            r"\bvulnerability\s+scan\b",
            r"\bcode\s+review\b",
            r"\bthreat\s+model(ing)?\b",
        ],
    }

    # Technology detection patterns
    # Format: list of (pattern, normalized_name)
    TECHNOLOGY_PATTERNS: list[tuple[str, str]] = [
        # Cloud providers
        (r"\baws\b", "AWS"),
        (r"\bamazon\s+web\s+services\b", "AWS"),
        (r"\bazure\b", "Azure"),
        (r"\bgcp\b", "GCP"),
        (r"\bgoogle\s+cloud\b", "GCP"),
        # AWS services
        (r"\bs3\b", "AWS S3"),
        (r"\bec2\b", "AWS EC2"),
        (r"\blambda\b", "AWS Lambda"),
        (r"\brds\b", "AWS RDS"),
        (r"\biam\b", "AWS IAM"),
        (r"\bcognito\b", "AWS Cognito"),
        (r"\bkms\b", "AWS KMS"),
        # Auth providers
        (r"\bkeycloak\b", "Keycloak"),
        (r"\bauth0\b", "Auth0"),
        (r"\bokta\b", "Okta"),
        (r"\bldap\b", "LDAP"),
        (r"\bactive\s+directory\b", "Active Directory"),
        # Frameworks
        (r"\bspring\s*(boot|security|framework)?\b", "Spring"),
        (r"\bdjango\b", "Django"),
        (r"\bfastapi\b", "FastAPI"),
        (r"\bflask\b", "Flask"),
        (r"\bexpress(\.?js)?\b", "Express.js"),
        (r"\bnext(\.?js)?\b", "Next.js"),
        (r"\breact\b", "React"),
        (r"\bangular\b", "Angular"),
        (r"\bvue(\.?js)?\b", "Vue.js"),
        # Databases
        (r"\bpostgres(ql)?\b", "PostgreSQL"),
        (r"\bmysql\b", "MySQL"),
        (r"\bmongodb\b", "MongoDB"),
        (r"\bredis\b", "Redis"),
        (r"\belasticsearch\b", "Elasticsearch"),
        (r"\bdynamodb\b", "DynamoDB"),
        # Container/orchestration
        (r"\bdocker\b", "Docker"),
        (r"\bkubernetes\b", "Kubernetes"),
        (r"\bk8s\b", "Kubernetes"),
        (r"\bhelm\b", "Helm"),
        # Security tools
        (r"\bsonarqube\b", "SonarQube"),
        (r"\bsnyk\b", "Snyk"),
        (r"\bveracode\b", "Veracode"),
        (r"\bcheckmarx\b", "Checkmarx"),
        (r"\bburp\s*suite\b", "Burp Suite"),
        (r"\bzap\b", "OWASP ZAP"),
    ]

    # Compliance framework detection patterns
    COMPLIANCE_PATTERNS: dict[ComplianceFramework, list[str]] = {
        ComplianceFramework.OWASP_ASVS: [
            r"\basvs\b",
            r"\bowasp\s+asvs\b",
            r"\bapplication\s+security\s+verification\s+standard\b",
            r"\bv\d+\.\d+\.\d+",  # Version pattern like V4.0.3
        ],
        ComplianceFramework.GDPR: [
            r"\bgdpr\b",
            r"\bgeneral\s+data\s+protection\s+regulation\b",
            r"\bdata\s+subject\s+rights?\b",
            r"\bright\s+to\s+erasure\b",
            r"\bdata\s+portability\b",
            r"\blegitimate\s+interest\b",
        ],
        ComplianceFramework.PCI_DSS: [
            r"\bpci[- ]?dss\b",
            r"\bpayment\s+card\s+industry\b",
            r"\bcardholder\s+data\b",
            r"\bpan\b",
            r"\bcvv\b",
        ],
        ComplianceFramework.HIPAA: [
            r"\bhipaa\b",
            r"\bphi\b",
            r"\bprotected\s+health\s+information\b",
            r"\bhitech\b",
            r"\bhealth\s+insurance\s+portability\b",
        ],
        ComplianceFramework.SOC2: [
            r"\bsoc\s*2\b",
            r"\bsoc2\b",
            r"\btrust\s+services?\s+criteria\b",
            r"\bservice\s+organization\s+control\b",
        ],
        ComplianceFramework.ISO27001: [
            r"\biso\s*27001\b",
            r"\biso27001\b",
            r"\bisms\b",
            r"\binformation\s+security\s+management\s+system\b",
        ],
        ComplianceFramework.NIST: [
            r"\bnist\b",
            r"\bnist\s+800[- ]\d+\b",
            r"\bnist\s+cybersecurity\s+framework\b",
            r"\bcsf\b",
        ],
        ComplianceFramework.CIS: [
            r"\bcis\s+benchmark\b",
            r"\bcis\s+controls?\b",
            r"\bcenter\s+for\s+internet\s+security\b",
        ],
    }

    def __init__(self) -> None:
        """Initialize the metadata extractor with compiled regex patterns."""
        # Pre-compile all patterns for performance
        self._doc_type_patterns = [
            (re.compile(pattern, re.IGNORECASE), doc_type, weight)
            for pattern, doc_type, weight in self.DOCUMENT_TYPE_PATTERNS
        ]

        self._domain_patterns = {
            domain: [re.compile(p, re.IGNORECASE) for p in patterns]
            for domain, patterns in self.SECURITY_DOMAIN_PATTERNS.items()
        }

        self._tech_patterns = [
            (re.compile(pattern, re.IGNORECASE), name)
            for pattern, name in self.TECHNOLOGY_PATTERNS
        ]

        self._compliance_patterns = {
            framework: [re.compile(p, re.IGNORECASE) for p in patterns]
            for framework, patterns in self.COMPLIANCE_PATTERNS.items()
        }

    def extract(self, content: str, source_path: str) -> DocumentMetadata:
        """Extract metadata from document content.

        Args:
            content: The document or chunk content.
            source_path: Path to the source document.

        Returns:
            DocumentMetadata with extracted information.
        """
        # Combine path and content for analysis
        full_text = f"{source_path}\n{content}"

        return DocumentMetadata(
            document_type=self._detect_document_type(source_path, content),
            security_domains=self._detect_security_domains(content),
            technologies=self._detect_technologies(content),
            compliance_frameworks=self._detect_compliance_frameworks(content),
            domain_scores=self._calculate_domain_scores(content),
        )

    def _detect_document_type(self, source_path: str, content: str) -> DocumentType:
        """Detect the document type from path and content.

        Args:
            source_path: Path to the source document.
            content: Document content.

        Returns:
            The detected DocumentType.
        """
        # Check for YAML system context first (based on file extension)
        path = Path(source_path)
        if path.suffix.lower() in [".yaml", ".yml"]:
            # Check if it looks like system context
            if self._is_system_context(content):
                return DocumentType.SYSTEM_CONTEXT

        # Score each document type
        type_scores: dict[DocumentType, int] = {t: 0 for t in DocumentType}

        # Check path patterns
        for pattern, doc_type, weight in self._doc_type_patterns:
            if pattern.search(source_path):
                type_scores[doc_type] += weight

        # Check content patterns
        for pattern, doc_type, weight in self._doc_type_patterns:
            matches = len(pattern.findall(content))
            if matches > 0:
                type_scores[doc_type] += weight * min(matches, 3)  # Cap at 3x

        # Return highest scoring type (excluding UNKNOWN)
        best_type = DocumentType.UNKNOWN
        best_score = 0

        for doc_type, score in type_scores.items():
            if doc_type != DocumentType.UNKNOWN and score > best_score:
                best_type = doc_type
                best_score = score

        return best_type

    def _is_system_context(self, content: str) -> bool:
        """Check if YAML content appears to be system context.

        Args:
            content: YAML content.

        Returns:
            True if it looks like system context.
        """
        context_indicators = [
            r"^system\s*:",
            r"^application\s*:",
            r"^project\s*:",
            r"^context\s*:",
            r"^architecture\s*:",
            r"^security\s*:",
            r"^compliance\s*:",
        ]
        for pattern in context_indicators:
            if re.search(pattern, content, re.MULTILINE | re.IGNORECASE):
                return True
        return False

    def _detect_security_domains(self, content: str) -> list[SecurityDomain]:
        """Detect security domains mentioned in content.

        Args:
            content: Document content.

        Returns:
            List of detected SecurityDomain values.
        """
        detected: list[SecurityDomain] = []

        for domain, patterns in self._domain_patterns.items():
            for pattern in patterns:
                if pattern.search(content):
                    detected.append(domain)
                    break  # Found this domain, move to next

        return detected

    def _calculate_domain_scores(self, content: str) -> dict[str, int]:
        """Calculate relevance scores for each security domain.

        Higher score = more mentions of related keywords.

        Args:
            content: Document content.

        Returns:
            Dict mapping domain names to scores.
        """
        scores: dict[str, int] = {}

        for domain, patterns in self._domain_patterns.items():
            score = 0
            for pattern in patterns:
                matches = len(pattern.findall(content))
                score += matches
            if score > 0:
                scores[domain.value] = score

        return scores

    def _detect_technologies(self, content: str) -> list[str]:
        """Detect technologies mentioned in content.

        Args:
            content: Document content.

        Returns:
            List of detected technology names (normalized).
        """
        detected: set[str] = set()

        for pattern, name in self._tech_patterns:
            if pattern.search(content):
                detected.add(name)

        return sorted(detected)

    def _detect_compliance_frameworks(self, content: str) -> list[ComplianceFramework]:
        """Detect compliance frameworks mentioned in content.

        Args:
            content: Document content.

        Returns:
            List of detected ComplianceFramework values.
        """
        detected: list[ComplianceFramework] = []

        for framework, patterns in self._compliance_patterns.items():
            for pattern in patterns:
                if pattern.search(content):
                    detected.append(framework)
                    break  # Found this framework, move to next

        return detected
