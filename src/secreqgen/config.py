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

    # How the command line calls Ollama. None = the default of the mode:
    #   word search (--docs, the tested setup): context window 16384 tokens,
    #   time limit 900 s and no JSON-mode option, as the runs of Study 2
    #   (scripts/run_study2.py);
    #   vector store: the default window of the server, 120 s and the
    #   JSON-mode option (the behaviour before the word search).
    # Some Ollama runners refuse the JSON-mode option with error 501
    # ("structured output is unavailable"). Then set structured_output to
    # false (SECREQGEN_LLM_STRUCTURED_OUTPUT=false).
    num_ctx: Optional[int] = None
    timeout: Optional[float] = None
    structured_output: Optional[bool] = None

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
    # Cosine similarity threshold for filtering retrieved chunks.
    # Tuned for sentence-transformers/all-mpnet-base-v2 (the embedding
    # model used by DocumentEmbedder). On sentence-to-paragraph RAG
    # queries with that model + this corpus shape, genuinely relevant
    # content typically scores 0.45-0.65 cosine; closely-paraphrased
    # content scores 0.65-0.85. A threshold of 0.4 filters off-topic
    # noise while admitting the relevant top-K.
    #
    # 2026-05-19 audit: previous default of 0.7 caused ALL retrieval
    # to fall back to "No relevant security documentation found." See
    # docs/RAG_RETRIEVAL_AUDIT_2026-05-19.md for the postmortem. The
    # earlier baseline results (qwen3 N=20, qwen3.6 N=10, cross-family
    # N=10) ran under the 0.7 default and therefore had no real
    # retrieval; their F1 deltas measure prompt-structure variation,
    # not RAG benefit, and must be re-run.
    similarity_threshold: float = 0.4


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
