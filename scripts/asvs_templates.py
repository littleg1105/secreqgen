"""ASVS-aligned ground-truth requirement templates.

Rule-based mapping from user-story content (keywords) to OWASP ASVS 4.0
controls. Used by `bootstrap_dataset.py` to generate ground_truth.json
without involving any LLM (so the ground-truth signal is INDEPENDENT
from the LLM under test).

This is intentionally conservative: each keyword cluster maps to a small,
high-confidence set of ASVS-derived requirements that an expert reviewer
would also produce. Coverage is not exhaustive — it's calibrated to
roughly match the depth of the hand-curated 10-project gold set
(~3-6 requirements per user story).

Defensible thesis claim: "ground truth was derived from a public,
versioned, expert-authored security standard (OWASP ASVS 4.0) via a
deterministic keyword-mapping rule. The mapping rules are open in
scripts/asvs_templates.py and reproducible." That's far stronger than
"we used an LLM to generate ground truth."
"""

from dataclasses import dataclass


@dataclass
class GTRequirement:
    title: str
    description: str
    asvs_category: str
    asvs_control: str  # e.g. "ASVS V2.1.1"


# Keyword → list of ASVS-aligned ground-truth requirements.
# Order matters: keywords listed first are matched first. Lowercase comparison.
KEYWORD_REQUIREMENTS: list[tuple[list[str], list[GTRequirement]]] = [
    # ─────────────────────────────────────────────────────────────
    # V2: Authentication
    # ─────────────────────────────────────────────────────────────
    ([
        "register", "registration", "sign up", "signup", "create account",
        "new account", "account creation",
    ], [
        GTRequirement(
            title="Password Strength Enforcement",
            description="Registration must enforce minimum password strength (12 characters minimum, reject commonly breached passwords) with clear feedback to the user.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.1.1",
        ),
        GTRequirement(
            title="Email Verification Required",
            description="New accounts must require email verification before sensitive functionality is unlocked. Unverified accounts have restricted capabilities.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.1.4",
        ),
        GTRequirement(
            title="Secure Password Storage",
            description="Passwords must be stored only after one-way hashing with a memory-hard or computationally-expensive function (Argon2id, bcrypt, PBKDF2 with sufficient iterations). Plaintext passwords must never be persisted.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.4.1",
        ),
    ]),
    ([
        "login", "log in", "sign in", "signin", "authenticate", "authentication",
        "credentials",
    ], [
        GTRequirement(
            title="Login Rate Limiting and Lockout",
            description="Login attempts must be rate-limited and progressive lockout/CAPTCHA must trigger after repeated failures from the same IP, account, or device.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.2.1",
        ),
        GTRequirement(
            title="Generic Error Messages on Login Failure",
            description="Login failure messages must not disclose whether the username, password, or both were incorrect — to prevent username enumeration.",
            asvs_category="V7: Error Handling and Logging",
            asvs_control="ASVS V7.4.1",
        ),
        GTRequirement(
            title="Authentication Event Logging",
            description="Authentication successes and failures must be logged with timestamp, source IP, and outcome for security monitoring.",
            asvs_category="V7: Error Handling and Logging",
            asvs_control="ASVS V7.1.1",
        ),
    ]),
    ([
        "two-factor", "2fa", "mfa", "multi-factor", "multifactor", "totp",
        "webauthn", "fido2", "authenticator app", "second factor",
    ], [
        GTRequirement(
            title="Strong Multi-Factor Authentication Support",
            description="Multi-factor authentication must be supported using industry-standard mechanisms (TOTP RFC 6238, WebAuthn/FIDO2). SMS/email-only MFA is acceptable as fallback only.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.7.1",
        ),
        GTRequirement(
            title="Backup Recovery Codes",
            description="When MFA is enabled, the system must provide single-use backup codes that allow recovery if the primary MFA factor is lost. Backup codes must be hashed at rest.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.7.6",
        ),
        GTRequirement(
            title="MFA Re-prompt on Sensitive Actions",
            description="Sensitive actions (password change, MFA reset, payment, key export) must require re-authentication or a fresh MFA challenge.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.8.4",
        ),
    ]),
    ([
        "password reset", "forgot password", "password recovery", "reset password",
        "account recovery",
    ], [
        GTRequirement(
            title="Single-Use Time-Limited Reset Tokens",
            description="Password-reset tokens must be cryptographically random, single-use, and expire within 60 minutes. Used or expired tokens must be unconditionally rejected.",
            asvs_category="V2: Authentication",
            asvs_control="ASVS V2.5.1",
        ),
        GTRequirement(
            title="Reset Confirmation Notification",
            description="The user must be notified through an out-of-band channel (e.g., email) when a password reset is initiated and again when the password actually changes.",
            asvs_category="V7: Error Handling and Logging",
            asvs_control="ASVS V7.2.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V3: Session Management
    # ─────────────────────────────────────────────────────────────
    ([
        "session", "logout", "log out", "sign out", "token expir",
    ], [
        GTRequirement(
            title="Session Expiry and Idle Timeout",
            description="Sessions must expire after a fixed maximum lifetime (e.g., 24 hours) and after a shorter idle timeout (e.g., 30 minutes for sensitive applications).",
            asvs_category="V3: Session Management",
            asvs_control="ASVS V3.3.1",
        ),
        GTRequirement(
            title="Session Invalidation on Logout",
            description="Logout must immediately invalidate the server-side session and revoke any associated refresh tokens. Subsequent requests with the old token must be rejected.",
            asvs_category="V3: Session Management",
            asvs_control="ASVS V3.3.2",
        ),
        GTRequirement(
            title="Secure Cookie Attributes",
            description="Session cookies must be marked Secure, HttpOnly, and SameSite=Strict (or Lax with CSRF defense) to prevent transport-layer leakage and CSRF attacks.",
            asvs_category="V3: Session Management",
            asvs_control="ASVS V3.4.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V4: Access Control
    # ─────────────────────────────────────────────────────────────
    ([
        "access control", "authorization", "authorise", "authorize", "permission",
        "role", "rbac", "abac", "share", "sharing", "collaborator", "team member",
    ], [
        GTRequirement(
            title="Server-Side Authorization Checks",
            description="Authorization decisions must be enforced server-side on every request that accesses or modifies a protected resource. Client-side checks alone are insufficient.",
            asvs_category="V4: Access Control",
            asvs_control="ASVS V4.1.1",
        ),
        GTRequirement(
            title="Default Deny",
            description="The default authorization decision must be deny; access is granted only when an explicit, role- or attribute-based rule allows it.",
            asvs_category="V4: Access Control",
            asvs_control="ASVS V4.1.3",
        ),
        GTRequirement(
            title="Insecure Direct Object Reference Defense",
            description="Resource identifiers exposed to clients must not allow access to resources owned by other principals; the server must verify the requester's authorization for each referenced object.",
            asvs_category="V4: Access Control",
            asvs_control="ASVS V4.2.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V5: Validation, Sanitization
    # ─────────────────────────────────────────────────────────────
    ([
        "input", "form", "search", "query", "user input", "comment", "post",
        "message", "submit",
    ], [
        GTRequirement(
            title="Input Validation Before Processing",
            description="All untrusted input must be validated server-side against an explicit schema or allowlist before use; reject (do not sanitize) anything that does not conform.",
            asvs_category="V5: Validation, Sanitization and Encoding",
            asvs_control="ASVS V5.1.1",
        ),
        GTRequirement(
            title="Output Encoding for Context",
            description="Untrusted data rendered into HTML, JS, attribute, URL, or CSS contexts must be encoded with a context-appropriate encoder to prevent injection (XSS).",
            asvs_category="V5: Validation, Sanitization and Encoding",
            asvs_control="ASVS V5.3.3",
        ),
    ]),
    ([
        "upload", "file upload", "attachment", "image upload", "document upload",
    ], [
        GTRequirement(
            title="File Upload Type and Size Restrictions",
            description="File uploads must be validated against an allowlist of MIME types (verified by content sniffing, not extension), and rejected if they exceed a maximum size.",
            asvs_category="V12: Files and Resources",
            asvs_control="ASVS V12.1.1",
        ),
        GTRequirement(
            title="Uploaded File Storage Outside Web Root",
            description="Uploaded files must be stored outside the web root or served through a controlled handler that re-validates authorization on each access.",
            asvs_category="V12: Files and Resources",
            asvs_control="ASVS V12.4.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V6: Cryptography
    # ─────────────────────────────────────────────────────────────
    ([
        "encrypt", "encryption", "decrypt", "vault", "secret", "key derivation",
        "cryptographic",
    ], [
        GTRequirement(
            title="Modern Cryptographic Algorithms Only",
            description="The system must use only modern, vetted cryptographic algorithms (AES-256-GCM, ChaCha20-Poly1305 for symmetric; X25519/secp256r1 for ECDH; SHA-2/SHA-3 for hashing). Deprecated algorithms (DES, RC4, MD5, SHA-1) must be rejected.",
            asvs_category="V6: Stored Cryptography",
            asvs_control="ASVS V6.2.1",
        ),
        GTRequirement(
            title="Secrets Never Stored in Plaintext",
            description="Secrets, API keys, and master credentials must be stored encrypted at rest (KMS-managed key, hardware-backed secure enclave, or per-tenant master key).",
            asvs_category="V6: Stored Cryptography",
            asvs_control="ASVS V6.4.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V8: Data Protection
    # ─────────────────────────────────────────────────────────────
    ([
        "personal data", "pii", "user data", "profile", "privacy", "gdpr",
        "delete account", "export data", "data export",
    ], [
        GTRequirement(
            title="Personal Data Inventory and Minimization",
            description="The system must collect only personal data needed for declared purposes and must document an inventory of categories collected (lawful-basis tracking).",
            asvs_category="V8: Data Protection",
            asvs_control="ASVS V8.1.1",
        ),
        GTRequirement(
            title="User Data Export and Deletion",
            description="Users must be able to export their data in a structured machine-readable format and request deletion. Deletion must propagate to backups within a documented retention window.",
            asvs_category="V8: Data Protection",
            asvs_control="ASVS V8.3.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V9: Communications
    # ─────────────────────────────────────────────────────────────
    ([
        "api", "rest", "graphql", "webhook", "endpoint", "http",
    ], [
        GTRequirement(
            title="TLS 1.2+ Required for All Connections",
            description="All client-server and server-server communication must use TLS 1.2 or higher with strong cipher suites; plaintext HTTP must redirect to HTTPS.",
            asvs_category="V9: Communication",
            asvs_control="ASVS V9.1.1",
        ),
        GTRequirement(
            title="HSTS and Certificate Validation",
            description="HTTP Strict Transport Security (HSTS) must be enabled with a long max-age. Server certificates must be validated by clients without override prompts.",
            asvs_category="V9: Communication",
            asvs_control="ASVS V9.1.2",
        ),
        GTRequirement(
            title="API Rate Limiting",
            description="API endpoints must enforce per-principal rate limits to prevent abuse, scraping, and brute-force attacks. Limits must be applied at the application gateway and re-checked in the application.",
            asvs_category="V13: API and Web Service",
            asvs_control="ASVS V13.1.1",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V11: Business Logic
    # ─────────────────────────────────────────────────────────────
    ([
        "checkout", "payment", "order", "purchase", "transaction", "transfer",
    ], [
        GTRequirement(
            title="Transaction Idempotency",
            description="Payment and other side-effecting operations must be idempotent (idempotency-key header or transaction-id deduplication) to prevent double-charge under retry.",
            asvs_category="V11: Business Logic",
            asvs_control="ASVS V11.1.1",
        ),
        GTRequirement(
            title="Authorization on Every Step of Multi-Step Flow",
            description="Multi-step business flows (cart → checkout → payment → confirmation) must re-authorize the user and re-verify cart state at every step; partial state must not be trusted.",
            asvs_category="V11: Business Logic",
            asvs_control="ASVS V11.1.4",
        ),
    ]),

    # ─────────────────────────────────────────────────────────────
    # V14: Configuration
    # ─────────────────────────────────────────────────────────────
    ([
        "config", "setup", "deployment", "production", "environment",
    ], [
        GTRequirement(
            title="Production Configuration Hardening",
            description="Debug endpoints, default credentials, verbose error responses, and development-only middleware must be disabled in production builds; configuration must be validated on startup.",
            asvs_category="V14: Configuration",
            asvs_control="ASVS V14.1.1",
        ),
    ]),
]


def derive_ground_truth_for_story(story_text: str) -> list[GTRequirement]:
    """Return the deduplicated list of ASVS-derived ground-truth
    requirements for a single user story.

    Matching is case-insensitive substring containment over the union of
    `title + story + acceptance_criteria` text. A requirement is added
    once even if multiple keywords from the same cluster match.
    """
    haystack = story_text.lower()
    out: list[GTRequirement] = []
    seen_titles: set[str] = set()
    for keywords, reqs in KEYWORD_REQUIREMENTS:
        if any(kw.lower() in haystack for kw in keywords):
            for r in reqs:
                if r.title not in seen_titles:
                    out.append(r)
                    seen_titles.add(r.title)
    return out
