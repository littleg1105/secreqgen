"""Fixed paths and constants of the tuning tools. Importing this module reads no file."""

from __future__ import annotations

from pathlib import Path

WORKSPACE: Path = Path(__file__).resolve().parents[4]  # the thesis root
CODED: Path = WORKSPACE / "coded_project"
SECREQGEN: Path = CODED / "secreqgen"
SCRIPTS: Path = SECREQGEN / "scripts"
RTC: Path = CODED / "real_test_cases"
STUDY2: Path = RTC / "study2"
REPOS_ROOT: Path = RTC / "repos"  # cloned source trees of the products (read only)
REPO_DIR_OVERRIDE: dict[str, str] = {"bitwarden": "bitwarden-server"}  # product -> folder name
STUDY2B: Path = RTC / "study2b"
TUNING_ROOT: Path = RTC / "tuning"
FINAL_TEST_ROOT: Path = RTC / "final_test"
DATA: Path = SECREQGEN / "data"
EVIDENCE: Path = WORKSPACE / "thesis" / "evidence"
HASH_LISTS: dict[str, Path] = {
    "study2": EVIDENCE / "study2_registered_hashes_2026-11-14.sha256",
    "study2b": EVIDENCE / "study2b_hashes_2026-11-16.sha256",
    "vectordb": EVIDENCE / "vectordb_hashes_2026-11-14.sha256",
}
PROJECTS: tuple[str, ...] = ("mastodon", "keycloak", "bitwarden")
MODELS: tuple[str, ...] = ("qwen3.8:27b", "gemma4:26b")  # local names; the "model" key of every record
# Models of the model test of 16 November 2026 (hosted only, with the word search). They have no stored
# passages of their own: for them the code of xproject.py takes the stored block of BLOCK_SOURCE_MODEL.
EXTRA_MODELS: tuple[str, ...] = ("mistral-small3.2:24b", "nemotron3-nano:30b")
BLOCK_SOURCE_MODEL: str = "qwen3.8:27b"
FALLBACK_CONTEXT: str = "No relevant security documentation found."  # 41 characters; retriever.py:343
ANALYSIS_S2B: Path = STUDY2B / "analysis_s2b.json"


def model_slug(model: str) -> str:
    """Folder name of a model. Same rule as _slug in run_cross_family_winning_config.py."""
    return model.replace(":", "_").replace(".", "_").replace("/", "_")
