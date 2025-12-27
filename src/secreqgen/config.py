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
    """Configuration for LLM (Ollama)."""

    model_config = SettingsConfigDict(env_prefix="SECREQGEN_LLM_")

    model_name: str = "llama3:8b"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.1
    max_tokens: int = 4096


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
