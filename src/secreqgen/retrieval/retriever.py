"""Simple semantic retriever for RAG pipeline.

Combines document embedder and vector store for semantic search,
with similarity threshold filtering and context formatting for LLM injection.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from secreqgen.generation.schemas import UserStory


# Security keyword mapping: maps feature concepts to security terminology
# This bridges the vocabulary gap between user story language and security documentation
SECURITY_KEYWORD_MAP: dict[str, list[str]] = {
    # Authentication & Registration
    "registration": ["password hashing", "key derivation", "PBKDF2", "Argon2", "bcrypt", "account creation", "email verification"],
    "login": ["authentication", "session", "credential", "brute force", "lockout", "failed attempts"],
    "password": ["master password", "key derivation", "PBKDF2", "Argon2", "password policy", "password strength"],
    "authentication": ["session management", "token", "credential storage", "MFA", "multi-factor"],
    "master password": ["key derivation", "PBKDF2", "Argon2", "HKDF", "stretched key", "vault unlock"],

    # Biometric & Device Security
    "biometric": ["secure storage", "keychain", "DPAPI", "keystore", "device key", "fingerprint", "face recognition"],
    "device": ["secure enclave", "hardware security", "TPM", "device binding"],
    "unlock": ["vault decryption", "key derivation", "memory protection", "session"],

    # Two-Factor Authentication
    "2fa": ["TOTP", "WebAuthn", "FIDO2", "authenticator", "backup codes", "recovery codes"],
    "two-factor": ["TOTP", "WebAuthn", "FIDO2", "authenticator", "second factor"],
    "mfa": ["multi-factor", "TOTP", "WebAuthn", "FIDO2", "authenticator"],
    "totp": ["time-based one-time password", "authenticator", "seed", "secret key"],
    "webauthn": ["FIDO2", "passkey", "authenticator", "challenge-response"],

    # Cryptography & Encryption
    "encrypt": ["AES-256", "encryption key", "cipher", "ciphertext", "authenticated encryption", "AEAD"],
    "decrypt": ["decryption", "plaintext", "client-side decryption", "vault unlock"],
    "vault": ["encryption", "zero-knowledge", "client-side", "E2EE", "end-to-end encryption"],
    "credential": ["encrypted storage", "vault item", "secure storage"],

    # File Handling
    "file": ["file validation", "file encryption", "file type", "MIME type", "size limit", "upload"],
    "upload": ["file encryption", "antivirus", "malware scan", "file size", "file type validation"],
    "attachment": ["file encryption", "per-file key", "attachment storage"],

    # Access Control & Sharing
    "share": ["key exchange", "asymmetric encryption", "RSA", "access control", "sharing permissions"],
    "organization": ["RBAC", "role-based access", "collection", "team management"],
    "access": ["authorization", "RBAC", "permissions", "access control", "privilege"],
    "revoke": ["access revocation", "key rotation", "permission removal"],

    # API & Tokens
    "api": ["API token", "rate limiting", "authentication token", "scope", "API key"],
    "token": ["token generation", "cryptographic random", "entropy", "token expiration", "token revocation"],
    "rate": ["rate limiting", "throttling", "brute force protection", "DOS protection"],

    # Session & Memory
    "session": ["session token", "session timeout", "session invalidation", "idle timeout"],
    "memory": ["memory protection", "secure memory", "memory clearing", "sensitive data"],

    # General Security
    "security": ["vulnerability", "threat", "mitigation", "security requirement", "OWASP", "ASVS"],
}
from secreqgen.ingestion.embedder import DocumentEmbedder
from secreqgen.ingestion.metadata import DocumentType
from secreqgen.retrieval.vectorstore import QueryResult, SecReqGenVectorStore


@dataclass
class RetrievalResult:
    """Result from a retrieval operation.

    Attributes:
        chunks: Retrieved chunks that passed the similarity threshold.
        query_text: The original query text.
        total_retrieved: Number of chunks retrieved before filtering.
        above_threshold: Number of chunks above similarity threshold.
        retrieval_time_ms: Time taken for retrieval in milliseconds.
    """

    chunks: list[QueryResult]
    query_text: str
    total_retrieved: int
    above_threshold: int
    retrieval_time_ms: float


class SimpleRetriever:
    """Simple semantic retriever for RAG pipeline.

    Combines the document embedder and vector store to provide semantic
    search with similarity threshold filtering. Designed for the minimum
    viable RAG pipeline - no re-ranking or query expansion.

    Example:
        >>> retriever = SimpleRetriever(vectorstore, embedder)
        >>> result = retriever.retrieve("authentication requirements")
        >>> context = retriever.format_context(result.chunks)
    """

    def __init__(
        self,
        vectorstore: SecReqGenVectorStore,
        embedder: DocumentEmbedder,
        top_k: int = 5,
        similarity_threshold: float = 0.7,
    ) -> None:
        """Initialize the retriever.

        Args:
            vectorstore: The vector store containing embedded documents.
            embedder: The embedder for generating query embeddings.
            top_k: Maximum number of results to retrieve (default: 5).
            similarity_threshold: Minimum similarity score [0-1] to include
                a result (default: 0.7). Lower = more results but less relevant.
        """
        self.vectorstore = vectorstore
        self.embedder = embedder
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold

    def retrieve(
        self,
        query: str,
        doc_types: Optional[list[DocumentType]] = None,
        top_k: Optional[int] = None,
    ) -> RetrievalResult:
        """Retrieve relevant chunks for a query.

        Args:
            query: The search query text.
            doc_types: Optional list of document types to search.
                If None, searches all collections except SYSTEM_CONTEXT.
            top_k: Override the default top_k value.

        Returns:
            RetrievalResult with matching chunks and metadata.
        """
        start_time = time.perf_counter()

        # Default to all non-system-context document types
        if doc_types is None:
            doc_types = [
                DocumentType.POLICY,
                DocumentType.ARCHITECTURE,
                DocumentType.STANDARD,
            ]

        k = top_k or self.top_k

        # Generate query embedding
        query_embedding = self.embedder.embed_text(query)

        # Query vector store
        results = self.vectorstore.query(
            query_embedding=query_embedding,
            doc_types=doc_types,
            n_results=k,
        )

        # Filter by similarity threshold
        filtered = [r for r in results if r.similarity >= self.similarity_threshold]

        retrieval_time_ms = (time.perf_counter() - start_time) * 1000

        return RetrievalResult(
            chunks=filtered,
            query_text=query,
            total_retrieved=len(results),
            above_threshold=len(filtered),
            retrieval_time_ms=retrieval_time_ms,
        )

    def retrieve_for_user_story(
        self,
        user_story: UserStory,
        top_k: Optional[int] = None,
    ) -> RetrievalResult:
        """Retrieve context relevant to a user story.

        Builds a query from the user story title, story text, and
        acceptance criteria, then retrieves relevant security documentation.

        Args:
            user_story: The user story to find relevant context for.
            top_k: Override the default top_k value.

        Returns:
            RetrievalResult with matching chunks and metadata.
        """
        query = self._build_query_from_story(user_story)
        return self.retrieve(query, top_k=top_k)

    def _build_query_from_story(self, user_story: UserStory) -> str:
        """Build a security-focused retrieval query from a user story.

        Transforms the user story narrative into a query that uses security
        terminology, bridging the vocabulary gap between functional requirements
        and security documentation.

        Args:
            user_story: The user story to build a query from.

        Returns:
            Query string optimized for security document retrieval.
        """
        # Extract security keywords from the user story
        security_keywords = self._extract_security_keywords(user_story)

        # Build a security-focused query
        parts = []

        # Start with extracted security keywords for better semantic matching
        if security_keywords:
            parts.append("Security requirements: " + " ".join(security_keywords))

        # Add the story title and context
        parts.append(f"Feature: {user_story.title}")

        # Include acceptance criteria as they often contain security-relevant terms
        if user_story.acceptance_criteria:
            parts.append("Requirements: " + "; ".join(user_story.acceptance_criteria))

        return " ".join(parts)

    def _extract_security_keywords(self, user_story: UserStory) -> list[str]:
        """Extract security-related keywords from a user story.

        Analyzes the user story text and maps feature concepts to security
        terminology using the SECURITY_KEYWORD_MAP. This bridges the vocabulary
        gap between user-facing language and security documentation.

        Args:
            user_story: The user story to extract keywords from.

        Returns:
            List of security keywords relevant to the user story.
        """
        # Combine all text for analysis
        text = " ".join([
            user_story.title.lower(),
            user_story.story.lower(),
            " ".join(user_story.acceptance_criteria).lower() if user_story.acceptance_criteria else "",
        ])

        # Track keywords to avoid duplicates
        found_keywords: set[str] = set()

        # Check each security concept in our mapping
        for concept, keywords in SECURITY_KEYWORD_MAP.items():
            # Use word boundary matching to avoid false positives
            # e.g., "password" should match but not "passwords" substring issues
            if re.search(rf'\b{re.escape(concept)}\b', text, re.IGNORECASE):
                # Add the security keywords for this concept
                found_keywords.update(keywords[:3])  # Limit to top 3 per concept to avoid query bloat

        return list(found_keywords)

    def get_system_context(self) -> Optional[str]:
        """Retrieve the system context from the vector store.

        System context is stored in the SYSTEM_CONTEXT collection as a
        single chunk. This is injected into every generation prompt.

        Returns:
            The system context content, or None if not found.
        """
        # For system context, we don't need a real query embedding
        # since we're retrieving all content from a single-item collection.
        # We use a dummy embedding to satisfy the API.
        dummy_embedding = self.embedder.embed_text("system context")

        results = self.vectorstore.query(
            query_embedding=dummy_embedding,
            doc_types=[DocumentType.SYSTEM_CONTEXT],
            n_results=1,
        )

        if results:
            return results[0].content

        return None

    def format_context(
        self,
        chunks: list[QueryResult],
        include_scores: bool = True,
    ) -> str:
        """Format retrieved chunks for injection into prompts.

        Args:
            chunks: List of QueryResult objects to format.
            include_scores: Whether to include similarity scores.

        Returns:
            Formatted context string for prompt injection.
        """
        if not chunks:
            return "No relevant security documentation found."

        # Lazy import to avoid circular dependency
        from secreqgen.generation.prompts import format_retrieved_context

        return format_retrieved_context(
            chunks=[c.content for c in chunks],
            sources=[c.source_document for c in chunks],
            sections=[" > ".join(c.section_hierarchy) for c in chunks],
            scores=[c.similarity for c in chunks] if include_scores else [0.0] * len(chunks),
        )

    def format_result_for_prompt(self, result: RetrievalResult) -> str:
        """Format a retrieval result for injection into prompts.

        Convenience method that extracts chunks from a RetrievalResult
        and formats them.

        Args:
            result: The RetrievalResult to format.

        Returns:
            Formatted context string for prompt injection.
        """
        return self.format_context(result.chunks)
