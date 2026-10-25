"""Idea 21 — grounded-citation generation via extract-then-cite two-stage prompting.

Built on top of the existing SecurityRequirementsGenerator. The change is
localised to RAG mode: zero-shot is delegated to the parent so the baseline
arm of the comparison stays identical to other experiments.

Pipeline:
    Stage A  — retrieve docs (parent retriever) → extract project-specific
               tokens via a helper LLM call ("tokens" JSON list)
    Stage B  — prepend an explicit "PROJECT-SPECIFIC TOKENS" section to the
               retrieved context, then call the generator with a prompt
               template that hard-requires citation by name.

Output shape is unchanged (GenerationResult), so the matcher, metrics,
and multi-run report continue to work without modification. The extracted
tokens are recorded in `retrieval_context` (a header line) so they can be
audited in the dumped raw_runs JSON.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional

from secreqgen.generation.generator import (
    GenerationMode,
    SecurityRequirementsGenerator,
)
from secreqgen.generation.llm_client import OllamaClient
from secreqgen.generation.prompts import SecurityRequirementsPromptBuilder
from secreqgen.generation.schemas import GenerationResult, UserStory

logger = logging.getLogger(__name__)


# Every example identifier below is FICTITIOUS. The 2026-10-25 council
# audit found the previous examples (CAS_UID_KEY, S3_ALIAS_HOST, ...) were
# the Mastodon probe's own answer vocabulary, and two others were real
# Bitwarden / Keycloak identifiers. tests/unit/test_prompt_leakage.py
# enforces that no prompt names an evaluation-corpus identifier.
STAGE_A_SYSTEM = """You are a code-and-config tokenizer for a security architect.
Your job is to extract project-specific identifiers from the supplied security documentation that are RELEVANT to the supplied user story.

Output a JSON object with a single key "tokens". The value is a list of strings.

What counts as a project-specific token:
- Configuration keys (e.g., AUTH_PROVIDER_ATTRIBUTE_KEY, AUTH_PROVIDER_ASSUME_VERIFIED)
- Environment variables (e.g., WORKER_UPLOAD_BACKEND, WORKER_UPLOAD_ALIAS_HOST)
- Named API endpoint paths or feature names (e.g., /api/v2/session/revoke, session_revocation)
- Class, module, or table names that the documentation calls out by name (e.g., SessionRevocationService)
- Realm/role/group names defined by the project (e.g., upload-operator)

What is NOT a project-specific token:
- Generic security terms: AES, RSA, TLS, JWT, OWASP, ASVS, OAuth, SAML, HMAC, PBKDF2, Argon2id, CSRF, XSS, CORS, etc.
- Generic feature words ("session", "login", "password") unless they appear as a specific named identifier in the docs.
- Tokens that do NOT actually appear in the supplied documentation.

Rules:
- Only return tokens that ACTUALLY appear in the documentation below.
- Only return tokens RELEVANT to the supplied user story.
- Preserve exact spelling and casing (AUTH_PROVIDER_ATTRIBUTE_KEY not auth_provider_attribute_key).
- Cap at 30 items. Empty list ({"tokens": []}) is fine when nothing applies.
- Output JSON only, no commentary."""


STAGE_A_USER_TEMPLATE = """## USER STORY
ID: {story_id}
Title: {story_title}
Story: {story_text}
Acceptance criteria:
{acceptance_criteria}

## SECURITY DOCUMENTATION
{retrieved_context}

Output JSON only:
{{"tokens": ["..."]}}"""


# Generic security terms we filter out as defensive guard (the system prompt
# already tells the LLM to skip these, but some models leak them anyway).
_GENERIC_TERMS = {
    "TLS", "SSL", "JWT", "AES", "RSA", "HMAC", "OAUTH", "SAML",
    "CSRF", "XSS", "CORS", "OWASP", "ASVS", "PBKDF2", "ARGON2", "ARGON2ID",
    "HTTPS", "HTTP", "JSON", "YAML", "XML", "API", "URL", "URI",
    "TOTP", "HOTP", "OTP", "MFA", "2FA", "SSO",
}


class Idea21Generator(SecurityRequirementsGenerator):
    """RAG generator with Stage-A token extraction + Stage-B grounded citation.

    Pass-through for zero-shot mode (delegates to the parent). For RAG mode:

    1. Retrieve as usual via self.retriever.
    2. Run Stage A: ask `extractor_llm` for project-specific tokens given
       the retrieved context + user story.
    3. Format an explicit "PROJECT-SPECIFIC TOKENS" section and prepend it
       to the retrieved context.
    4. Build the RAG prompt with this augmented context (using the
       idea-21 prompt template installed on `prompt_builder`).
    5. Generate Stage B with the parent's retry + JSON parsing helpers.

    The `extractor_llm` defaults to the generator LLM but can be swapped
    for a different-family model (matching the agent-role hygiene we use
    for the idea-19 code-grep agent).
    """

    def __init__(
        self,
        llm_client: OllamaClient,
        extractor_llm: Optional[OllamaClient] = None,
        retriever=None,
        prompt_builder: Optional[SecurityRequirementsPromptBuilder] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        max_tokens_in_list: int = 30,
    ) -> None:
        super().__init__(
            llm_client=llm_client,
            retriever=retriever,
            prompt_builder=prompt_builder,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self.extractor_llm = extractor_llm or llm_client
        self.max_tokens_in_list = max_tokens_in_list
        # Per-story log of what was extracted (useful for the analysis script).
        self.last_extraction: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        user_story: UserStory,
        mode: GenerationMode = GenerationMode.RAG,
        system_context: Optional[str] = None,
    ) -> GenerationResult:
        if mode == GenerationMode.ZERO_SHOT:
            return super().generate(
                user_story, mode=mode, system_context=system_context
            )
        if self.retriever is None:
            raise ValueError("Idea21Generator requires a retriever for RAG mode.")

        # Resolve system_context the same way the parent does.
        if system_context is None:
            system_context = self.retriever.get_system_context()

        # Stage 0: retrieve.
        retrieval_result = self.retriever.retrieve_for_user_story(user_story)
        retrieved_context = self.retriever.format_result_for_prompt(retrieval_result)

        # Stage A: extract tokens.
        t0 = time.perf_counter()
        tokens = self._extract_tokens(user_story, retrieved_context)
        stage_a_ms = (time.perf_counter() - t0) * 1000
        self.last_extraction[user_story.id] = tokens
        logger.info(
            f"[idea21] story={user_story.id} extracted {len(tokens)} tokens "
            f"in {stage_a_ms:.0f}ms"
        )

        # Stage B: build prompt with the token-list section prepended.
        token_section = self._format_token_section(tokens)
        augmented_context = f"{token_section}\n\n{retrieved_context}"

        user_prompt = self.prompt_builder.build_rag_prompt(
            user_story=user_story,
            retrieved_context=augmented_context,
            system_context=system_context,
        )
        system_prompt = self.prompt_builder.build_system_prompt()

        response, _retry_count = self._generate_with_retry(
            user_prompt=user_prompt, system_prompt=system_prompt,
        )
        requirements, threat_mappings = self._parse_response(
            response=response, user_story=user_story,
        )

        # Record extracted tokens at the head of retrieval_context for audit.
        annotated_context = (
            f"## IDEA-21 EXTRACTED TOKENS ({len(tokens)} items, "
            f"stage_a_ms={stage_a_ms:.0f})\n"
            f"{json.dumps(tokens, ensure_ascii=False)}\n\n"
            f"{augmented_context}"
        )

        return GenerationResult(
            user_story=user_story,
            requirements=requirements,
            threat_mappings=threat_mappings,
            generated_at=datetime.now(),
            model_used=self.llm_client.model_name,
            rag_enabled=True,
            retrieval_context=annotated_context,
        )

    # ------------------------------------------------------------------
    # Stage A internals
    # ------------------------------------------------------------------

    def _extract_tokens(self, user_story: UserStory, retrieved_context: str) -> list[str]:
        """Stage A: ask the extractor LLM for project-specific tokens."""
        ac_text = (
            "\n".join(f"- {a}" for a in (user_story.acceptance_criteria or []))
            or "(none)"
        )
        user_prompt = STAGE_A_USER_TEMPLATE.format(
            story_id=user_story.id,
            story_title=user_story.title,
            story_text=user_story.story,
            acceptance_criteria=ac_text,
            retrieved_context=retrieved_context,
        )

        try:
            resp = self.extractor_llm.generate(
                prompt=user_prompt,
                system_prompt=STAGE_A_SYSTEM,
                format="json",
            )
        except Exception as e:
            logger.warning(f"[idea21] Stage A LLM call failed for {user_story.id}: {e}")
            return []

        raw_text = (resp.content or "").strip()
        try:
            obj = self._extract_json(raw_text)
        except Exception as e:
            logger.warning(
                f"[idea21] Stage A JSON parse failed for {user_story.id}: {e}; "
                f"raw start: {raw_text[:200]!r}"
            )
            return []

        if not isinstance(obj, dict):
            return []
        raw_tokens = obj.get("tokens", [])
        if not isinstance(raw_tokens, list):
            return []

        out: list[str] = []
        seen: set[str] = set()
        for tok in raw_tokens:
            if not isinstance(tok, str):
                continue
            t = tok.strip()
            if not t:
                continue
            key = t.lower()
            if key in seen:
                continue
            if t.upper() in _GENERIC_TERMS:
                continue
            seen.add(key)
            out.append(t)
            if len(out) >= self.max_tokens_in_list:
                break

        # 2026-10-25: a token the extractor "found" that is not verbatim in the
        # retrieved context is a paraphrase or a hallucination. Previously this
        # was only logged and the token was still handed to Stage B as if it
        # were grounded; now it is dropped. The May runs showed 0 such tokens in
        # 50 extractions, so this changes provenance guarantees, not results.
        absent = [t for t in out if t not in retrieved_context]
        if absent:
            logger.info(
                f"[idea21] dropping {len(absent)}/{len(out)} extracted tokens not "
                f"verbatim in retrieved_context: {absent[:5]}"
            )
            out = [t for t in out if t in retrieved_context]

        return out

    @staticmethod
    def _format_token_section(tokens: list[str]) -> str:
        if not tokens:
            return (
                "## PROJECT-SPECIFIC TOKENS (CITE BY NAME)\n"
                "(none extracted — fall back to ASVS-grounded requirements without forced citation)"
            )
        lines = [
            "## PROJECT-SPECIFIC TOKENS (CITE BY NAME)",
            (
                f"The following {len(tokens)} tokens appear in the project's "
                "security documentation and are relevant to this user story. "
                "EVERY requirement MUST cite AT LEAST ONE of these tokens by "
                "its exact name (in the title, description, acceptance "
                "criteria, or implementation notes)."
            ),
        ]
        for t in tokens:
            lines.append(f"- {t}")
        return "\n".join(lines)
