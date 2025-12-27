"""Tests for configuration management."""

from pathlib import Path

from secreqgen.config import (
    EmbeddingConfig,
    EvaluationConfig,
    LLMConfig,
    RetrievalConfig,
    SecReqGenConfig,
    VectorStoreConfig,
    get_config,
)


class TestEmbeddingConfig:
    """Tests for EmbeddingConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        config = EmbeddingConfig()
        assert config.model_name == "all-mpnet-base-v2"
        assert config.device == "cpu"
        assert config.batch_size == 32


class TestVectorStoreConfig:
    """Tests for VectorStoreConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        config = VectorStoreConfig()
        assert config.persist_directory == Path("./data/vectordb")
        assert config.collection_prefix == "secreqgen"


class TestLLMConfig:
    """Tests for LLMConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        config = LLMConfig()
        assert config.model_name == "llama3:8b"
        assert config.base_url == "http://localhost:11434"
        assert config.temperature == 0.1
        assert config.max_tokens == 4096


class TestRetrievalConfig:
    """Tests for RetrievalConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        config = RetrievalConfig()
        assert config.top_k == 5
        assert config.similarity_threshold == 0.7


class TestEvaluationConfig:
    """Tests for EvaluationConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        config = EvaluationConfig()
        assert config.match_threshold == 0.85
        assert config.review_threshold == 0.70


class TestSecReqGenConfig:
    """Tests for main SecReqGenConfig."""

    def test_defaults(self) -> None:
        """Test that all nested configs have correct defaults."""
        config = SecReqGenConfig()
        assert config.embedding.model_name == "all-mpnet-base-v2"
        assert config.llm.model_name == "llama3:8b"
        assert config.retrieval.top_k == 5

    def test_get_config(self) -> None:
        """Test get_config helper function."""
        config = get_config()
        assert isinstance(config, SecReqGenConfig)

    def test_paths(self) -> None:
        """Test path configurations."""
        config = SecReqGenConfig()
        assert config.prompts_dir == Path("./prompts")
        assert config.configs_dir == Path("./configs")
        assert config.output_dir == Path("./output")
