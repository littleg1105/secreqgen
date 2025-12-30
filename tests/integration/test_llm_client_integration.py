"""Integration tests for OllamaClient.

These tests require Ollama to be running with appropriate models.
They are marked as slow and can be skipped with: pytest -m "not slow"
"""

import pytest

from secreqgen.exceptions import LLMConnectionError
from secreqgen.generation.llm_client import OllamaClient


# Mark all tests in this module as slow and requiring Ollama
pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
]


@pytest.fixture
def ollama_client() -> OllamaClient:
    """Create an OllamaClient for integration tests."""
    return OllamaClient(
        model_name="llama3:8b",  # Use available model (was llama3.2:3b)
        temperature=0.1,
        max_tokens=256,
        timeout=60.0,
    )


@pytest.fixture
def skip_if_ollama_unavailable(ollama_client: OllamaClient):
    """Skip test if Ollama is not running."""
    if not ollama_client.check_health():
        pytest.skip("Ollama is not running or model not available")


class TestOllamaClientIntegration:
    """Integration tests for OllamaClient with real Ollama server."""

    def test_health_check_real(self, ollama_client: OllamaClient):
        """Test real health check against Ollama server."""
        # This test will fail if Ollama is not running
        # Use skip_if_ollama_unavailable to make it more graceful
        try:
            result = ollama_client.check_health()
            # Result could be True or False depending on setup
            assert isinstance(result, bool)
        except Exception as e:
            pytest.fail(f"Health check raised unexpected exception: {e}")

    def test_list_models_real(
        self,
        ollama_client: OllamaClient,
        skip_if_ollama_unavailable,
    ):
        """Test listing models from real Ollama server."""
        models = ollama_client.list_models()
        assert isinstance(models, list)
        # Should have at least one model if Ollama is running
        assert len(models) > 0
        # Each model should be a string
        assert all(isinstance(m, str) for m in models)

    def test_generate_simple_prompt(
        self,
        ollama_client: OllamaClient,
        skip_if_ollama_unavailable,
    ):
        """Test generation with a simple prompt."""
        response = ollama_client.generate(
            prompt="What is 2+2? Answer with just the number."
        )

        assert response.content is not None
        assert len(response.content) > 0
        assert "4" in response.content
        assert response.prompt_tokens > 0
        assert response.completion_tokens > 0
        assert response.latency_ms > 0

    def test_generate_with_system_prompt_real(
        self,
        ollama_client: OllamaClient,
        skip_if_ollama_unavailable,
    ):
        """Test generation with system prompt."""
        response = ollama_client.generate(
            prompt="Say hello",
            system_prompt="You are a pirate. Always respond in pirate speak.",
        )

        assert response.content is not None
        assert len(response.content) > 0
        # Pirate-themed response should contain some pirate-y words
        content_lower = response.content.lower()
        pirate_words = ["ahoy", "matey", "arr", "ye", "aye", "sailor", "sea"]
        has_pirate_word = any(word in content_lower for word in pirate_words)
        # This is a soft check - LLMs can be unpredictable
        if not has_pirate_word:
            pytest.xfail("LLM didn't respond in pirate speak (expected but not required)")

    def test_generate_json_format_real(
        self,
        ollama_client: OllamaClient,
        skip_if_ollama_unavailable,
    ):
        """Test generation with JSON format mode."""
        import json

        response = ollama_client.generate(
            prompt="Return a JSON object with keys 'name' and 'age'. Example: {'name': 'Alice', 'age': 30}",
            format="json",
        )

        assert response.content is not None

        # Try to parse as JSON
        try:
            data = json.loads(response.content)
            assert isinstance(data, dict)
            assert "name" in data or "age" in data  # At least one key
        except json.JSONDecodeError:
            pytest.fail(f"Expected JSON response, got: {response.content[:200]}")

    def test_generate_with_messages_real(
        self,
        ollama_client: OllamaClient,
        skip_if_ollama_unavailable,
    ):
        """Test chat-style generation with messages."""
        messages = [
            {"role": "system", "content": "You are a helpful math tutor."},
            {"role": "user", "content": "What is 10 divided by 2?"},
        ]

        response = ollama_client.generate_with_messages(messages)

        assert response.content is not None
        assert "5" in response.content
        assert response.latency_ms > 0

    def test_metrics_tracking(
        self,
        ollama_client: OllamaClient,
        skip_if_ollama_unavailable,
    ):
        """Test that metrics are properly tracked."""
        response = ollama_client.generate(
            prompt="Count from 1 to 5."
        )

        # Verify all metrics are populated
        assert response.model is not None
        assert response.prompt_tokens > 0
        assert response.completion_tokens > 0
        assert response.total_tokens == response.prompt_tokens + response.completion_tokens
        assert response.latency_ms > 0
        assert response.raw_response is not None
        assert isinstance(response.raw_response, dict)


class TestOllamaClientConnectionHandling:
    """Test connection error handling."""

    def test_connection_to_invalid_url(self):
        """Test that connecting to invalid URL raises proper error."""
        client = OllamaClient(
            base_url="http://localhost:99999",  # Invalid port
            model_name="test",
        )

        # Health check should return False, not raise
        assert client.check_health() is False

        # But generate should raise LLMConnectionError
        with pytest.raises(LLMConnectionError) as exc_info:
            client.generate("Hello")

        assert "Unable to connect" in str(exc_info.value.message)
