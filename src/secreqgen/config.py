"""Configuration management using Pydantic Settings."""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingConfig(BaseSettings):
    """Configuration for embedding model."""

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_EMBEDDING_")

    model_name: str = "all-mpnet-base-v2"
    device: str = "cpu"
    batch_size: int = 32


class VectorStoreConfig(BaseSettings):
    """Configuration for vector store (ChromaDB)."""

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_VECTORSTORE_")

    persist_directory: Path = Path("./data/vectordb")
    collection_prefix: str = "secreqgen"


class LLMConfig(BaseSettings):
    """Configuration for LLM (Ollama).

    Supports separate models for generation and verification (judge).
    Available models:
        - llama3:8b (default, fast)
        - qwen3:30b (larger, more capable)
        - WhiteRabbitNeo-V3-7B (security-focused)
        - qwen2.5-coder:7b (code-focused)
    """

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_LLM_")

    # Primary model for requirement generation
    model_name: str = "llama3:8b"

    # Optional separate model for verification/judging
    # If not set, uses model_name
    judge_model_name: Optional[str] = None

    base_url: str = "http://localhost:11434"
    temperature: float = 0.1
    max_tokens: int = 4096

    @property
    def judge_model(self) -> str:
        """Get the model to use for verification/judging."""
        return self.judge_model_name or self.model_name


class VerificationConfig(BaseSettings):
    """Configuration for LLM-as-Judge verification layer."""

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_VERIFICATION_")

    # Enable/disable verification
    enabled: bool = False

    # Minimum score (1-10) to keep a requirement
    min_relevance_score: float = 7.0
    min_specificity_score: float = 6.0
    min_actionability_score: float = 6.0

    # Overall average threshold
    min_average_score: float = 6.5

    # If True, use stricter AND logic (all must pass)
    # If False, use average score
    strict_mode: bool = False


class RetrievalConfig(BaseSettings):
    """Configuration for RAG retrieval."""

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_RETRIEVAL_")

    top_k: int = 5
    similarity_threshold: float = 0.7


class EvaluationConfig(BaseSettings):
    """Configuration for evaluation metrics."""

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_EVAL_")

    match_threshold: float = 0.85
    review_threshold: float = 0.70


class SecReqGenConfig(BaseSettings):
    """Main configuration for SecReqGen."""

    model_config = SettingsConfigDict(
        env_prefix="SECREQGEN_",
        env_nested_delimiter="__",
    )

    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vectorstore: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)

    # Paths
    prompts_dir: Path = Path("./prompts")
    configs_dir: Path = Path("./configs")
    output_dir: Path = Path("./output")

    # Logging
    log_level: str = "INFO"
    log_file: Optional[Path] = None


def get_config() -> SecReqGenConfig:
    """Get the application configuration."""
    return SecReqGenConfig()
