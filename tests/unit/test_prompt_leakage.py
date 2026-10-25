"""Prompt templates must not contain evaluation-corpus identifiers.

Regression test for the 2026-10-25 council audit finding: the idea-21
prompts and the Stage-A extractor prompt used ``CAS_UID_KEY``,
``S3_ALIAS_HOST``, ``ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH``,
``followers_synchronization`` and ``/api/v1/follows/synchronize`` as
*examples* — the exact answer vocabulary of the five Mastodon adversarial
probe stories — plus ``PasswordProtectedKeyEnvelope`` (Bitwarden) and
``realm-management`` (Keycloak). The analysis script only subtracted terms
present in the story text, so prompt-supplied identifiers were scored as
"docs-only" citations in every arm.

Two checks:

1. A fixed denylist of known probe / planned-probe identifiers must appear
   in no prompt source. This is the direct regression.
2. Every identifier-like token in every prompt source (UPPER_SNAKE keys,
   ``/api/...`` paths, ``lower_snake`` feature names) must be absent from
   the evaluation corpora, ground truths and user stories under
   ``real_test_cases/``. This generalises the regression to projects and
   stories that do not exist yet. Skipped when the datasets are not on
   disk (CI without the data checkout).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from secreqgen.generation import grounded_citation

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_TEST_CASES = REPO_ROOT.parent / "real_test_cases"

PROMPT_FILES = sorted(REPO_ROOT.glob("data/winning_prompts*/*.txt"))
PROMPT_SOURCES: dict[str, str] = {
    str(p.relative_to(REPO_ROOT)): p.read_text() for p in PROMPT_FILES
}
PROMPT_SOURCES["grounded_citation.STAGE_A_SYSTEM"] = grounded_citation.STAGE_A_SYSTEM
PROMPT_SOURCES["grounded_citation.STAGE_A_USER_TEMPLATE"] = (
    grounded_citation.STAGE_A_USER_TEMPLATE
)

# Identifiers that are, or are planned to be, probe answers. Any of these in
# a prompt is leakage regardless of what the corpora contain.
KNOWN_PROBE_IDENTIFIERS = [
    "CAS_UID_KEY",
    "CAS_EMAIL_KEY",
    "CAS_LOGIN_URL",
    "CAS_SECURITY_ASSUME_EMAIL_IS_VERIFIED",
    "LIMITED_FEDERATION_MODE",
    "AUTHORIZED_FETCH",
    "DISALLOW_UNAUTHENTICATED_API_ACCESS",
    "S3_ALIAS_HOST",
    "S3_HOSTNAME",
    "ALLOW_UNSAFE_AUTH_PROVIDER_REATTACH",
    "ONE_CLICK_SSO_LOGIN",
    "followers_synchronization",
    "/api/v1/follows/synchronize",
    "PasswordProtectedKeyEnvelope",
    "realm-management",
]

# JSON schema field names and template vocabulary that legitimately appear
# in both prompts and datasets.
ALLOWED_TOKENS = {
    "user_story_id",
    "acceptance_criteria",
    "asvs_category",
    "implementation_notes",
    "source_references",
    "security_requirements",
    "threat_mappings",
    "story_id",
    "story_title",
    "story_text",
    "system_context",
    "retrieved_context",
    "json_schema",
    "must_not",
}

IDENTIFIER_RE = re.compile(
    r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"  # UPPER_SNAKE configuration keys
    r"|/api/v\d[\w/]*"  # API endpoint paths
    r"|\b[a-z]+(?:_[a-z]+)+\b"  # lower_snake feature / endpoint names
)

CORPORA = {
    "mastodon": REAL_TEST_CASES / "mastodon" / "docs",
    "keycloak": REAL_TEST_CASES / "extracted_docs_md" / "keycloak",
    "bitwarden": REAL_TEST_CASES / "bitwarden-test-data",
    "gitlab": REAL_TEST_CASES / "gitlab" / "docs",
    "mattermost": REAL_TEST_CASES / "mattermost" / "docs",
}


def _identifiers(text: str) -> set[str]:
    return {
        t
        for t in IDENTIFIER_RE.findall(text)
        if len(t) > 5 and t.lower() not in ALLOWED_TOKENS
    }


def _load_evaluation_text() -> dict[str, str]:
    """Docs + ground truth + user stories per project, as one string each."""
    texts: dict[str, str] = {}
    for project, docs_dir in CORPORA.items():
        parts = [p.read_text(errors="ignore") for p in docs_dir.rglob("*.md")]
        for name in ("ground_truth.json", "user_stories.json"):
            p = REAL_TEST_CASES / project / name
            if p.exists():
                parts.append(p.read_text(errors="ignore"))
        texts[project] = "\n".join(parts)
    adversarial = REAL_TEST_CASES / "mastodon_adversarial"
    texts["mastodon_adversarial"] = "\n".join(
        p.read_text(errors="ignore") for p in adversarial.glob("*.json")
    )
    return texts


@pytest.mark.parametrize("source", sorted(PROMPT_SOURCES))
def test_no_known_probe_identifier_in_prompt(source: str) -> None:
    """Direct regression: none of the probe answer terms appears in a prompt."""
    text = PROMPT_SOURCES[source]
    leaked = [t for t in KNOWN_PROBE_IDENTIFIERS if t in text]
    assert not leaked, f"{source} names probe identifiers: {leaked}"


@pytest.mark.skipif(
    not all(d.is_dir() for d in CORPORA.values()),
    reason="evaluation datasets (real_test_cases/) not on disk",
)
def test_no_prompt_identifier_appears_in_evaluation_corpora() -> None:
    """Generalisation: no identifier used as a prompt example exists in any
    project's documentation, ground truth or stories."""
    evaluation_text = _load_evaluation_text()
    leaks: list[str] = []
    for source, text in PROMPT_SOURCES.items():
        for token in sorted(_identifiers(text)):
            found_in = [p for p, corpus in evaluation_text.items() if token in corpus]
            if found_in:
                leaks.append(f"{source}: {token} -> {', '.join(found_in)}")
    msg = "prompt identifiers found in evaluation data:\n" + "\n".join(leaks)
    assert not leaks, msg


def test_zero_shot_and_rag_templates_share_exemplars() -> None:
    """Arm A (no documents) and arm B/C must see the same few-shot exemplars,
    otherwise a difference between arms is partly a difference in instructions
    (the May idea-21 baseline had lost them: 24 lines vs 124)."""
    idea21 = REPO_ROOT / "data" / "winning_prompts_idea21"
    rag = (idea21 / "rag_user_prompt.txt").read_text()
    zero = (idea21 / "zero_shot_user_prompt.txt").read_text()
    for marker in ("### Example 1", "### Example 2", "EXPECTED OUTPUT:"):
        assert marker in rag and marker in zero, marker
    # The exemplar blocks are byte-identical between the two templates.
    rag_block = rag[rag.index("### Example 1") : rag.index("---")]
    zero_block = zero[zero.index("### Example 1") : zero.index("---")]
    assert rag_block == zero_block
    # And the same requirement-count budget is requested from every arm.
    assert "Generate 4-6 requirements per user story" in rag
    assert "Generate 4-6 requirements per user story" in zero


def _json_safe_load(path: Path) -> object:
    return json.loads(path.read_text())
