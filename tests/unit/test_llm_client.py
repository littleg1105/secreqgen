"""Unit tests for OllamaClient."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from secreqgen.exceptions import GenerationError, LLMConnectionError
from secreqgen.generation.llm_client import LLMResponse, OllamaClient


class TestLLMResponse:
    """Tests for LLMResponse dataclass."""

    def test_llm_response_creation(self):
        """Test creating an LLMResponse."""
        response = LLMResponse(
            content="Hello, world!",
            model="llama3:8b",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=100.5,
            raw_response={"response": "Hello, world!"},
        )

        assert response.content == "Hello, world!"
        assert response.model == "llama3:8b"
        assert response.prompt_tokens == 10
        assert response.completion_tokens == 5
        assert response.total_tokens == 15
        assert response.latency_ms == 100.5


class TestOllamaClientInit:
    """Tests for OllamaClient initialization."""

    def test_default_initialization(self):
        """Test default client initialization."""
        client = OllamaClient()

        assert client.base_url == "http://localhost:11434"
        assert client.model_name == "llama3:8b"
        assert client.temperature == 0.1
        assert client.max_tokens == 4096
        assert client.timeout == 120.0

    def test_custom_initialization(self):
        """Test custom client initialization."""
        client = OllamaClient(
            base_url="http://custom:8080",
            model_name="mistral:7b",
            temperature=0.5,
            max_tokens=2048,
            timeout=60.0,
        )

        assert client.base_url == "http://custom:8080"
        assert client.model_name == "mistral:7b"
        assert client.temperature == 0.5
        assert client.max_tokens == 2048
        assert client.timeout == 60.0

    def test_trailing_slash_removed(self):
        """Test that trailing slash is removed from base_url."""
        client = OllamaClient(base_url="http://localhost:11434/")
        assert client.base_url == "http://localhost:11434"


class TestOllamaClientHealthCheck:
    """Tests for OllamaClient.check_health()."""

    def test_health_check_success(self):
        """Test successful health check when model is available."""
        client = OllamaClient(model_name="llama3:8b")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:8b"},
                {"name": "mistral:7b"},
            ]
        }

        with patch.object(client._client, "get", return_value=mock_response):
            assert client.check_health() is True

    def test_health_check_model_not_available(self):
        """Test health check when model is not available."""
        client = OllamaClient(model_name="llama3:8b")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "mistral:7b"},
            ]
        }

        with patch.object(client._client, "get", return_value=mock_response):
            assert client.check_health() is False

    def test_health_check_partial_model_match(self):
        """Test health check with partial model name match."""
        client = OllamaClient(model_name="llama3:8b")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:8b-instruct-q4_0"},
            ]
        }

        with patch.object(client._client, "get", return_value=mock_response):
            assert client.check_health() is True

    def test_health_check_connection_error(self):
        """Test health check when Ollama is not running."""
        client = OllamaClient()

        with patch.object(
            client._client, "get", side_effect=httpx.ConnectError("Connection refused")
        ):
            assert client.check_health() is False

    def test_health_check_http_error(self):
        """Test health check on HTTP error."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(client._client, "get", return_value=mock_response):
            assert client.check_health() is False


class TestOllamaClientListModels:
    """Tests for OllamaClient.list_models()."""

    def test_list_models_success(self):
        """Test successful model listing."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:8b"},
                {"name": "mistral:7b"},
                {"name": "codellama:13b"},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "get", return_value=mock_response):
            models = client.list_models()
            assert models == ["llama3:8b", "mistral:7b", "codellama:13b"]

    def test_list_models_empty(self):
        """Test listing when no models available."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "get", return_value=mock_response):
            models = client.list_models()
            assert models == []

    def test_list_models_connection_error(self):
        """Test list_models raises LLMConnectionError on connection failure."""
        client = OllamaClient()

        with patch.object(
            client._client, "get", side_effect=httpx.ConnectError("Connection refused")
        ):
            with pytest.raises(LLMConnectionError) as exc_info:
                client.list_models()

            assert "Unable to connect" in str(exc_info.value.message)
            assert "Is Ollama running?" in str(exc_info.value.details)


class TestOllamaClientGenerate:
    """Tests for OllamaClient.generate()."""

    def test_generate_success(self):
        """Test successful generation."""
        client = OllamaClient(model_name="llama3:8b")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "llama3:8b",
            "response": "The answer is 4.",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", return_value=mock_response):
            response = client.generate("What is 2+2?")

            assert response.content == "The answer is 4."
            assert response.model == "llama3:8b"
            assert response.prompt_tokens == 10
            assert response.completion_tokens == 5
            assert response.total_tokens == 15
            assert response.latency_ms > 0

    def test_generate_with_system_prompt(self):
        """Test generation with system prompt."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "llama3:8b",
            "response": "Hello!",
            "prompt_eval_count": 20,
            "eval_count": 3,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            client.generate(
                prompt="Say hello",
                system_prompt="You are a friendly assistant.",
            )

            # Verify system prompt was included in payload
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["system"] == "You are a friendly assistant."

    def test_generate_with_json_format(self):
        """Test generation with JSON format mode."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "llama3:8b",
            "response": '{"name": "test"}',
            "prompt_eval_count": 15,
            "eval_count": 8,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            response = client.generate(
                prompt="Return a JSON object with a name field",
                format="json",
            )

            # Verify format was included in payload
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["format"] == "json"
            assert response.content == '{"name": "test"}'

    def test_generate_connection_error(self):
        """Test generate raises LLMConnectionError on connection failure."""
        client = OllamaClient()

        with patch.object(
            client._client, "post", side_effect=httpx.ConnectError("Connection refused")
        ):
            with pytest.raises(LLMConnectionError) as exc_info:
                client.generate("Hello")

            assert "Unable to connect" in str(exc_info.value.message)
            assert "ollama serve" in str(exc_info.value.details)

    def test_generate_timeout_error(self):
        """Test generate raises GenerationError on timeout."""
        client = OllamaClient(timeout=10.0)

        with patch.object(
            client._client, "post", side_effect=httpx.TimeoutException("Timeout")
        ):
            with pytest.raises(GenerationError) as exc_info:
                client.generate("Hello")

            assert "timed out" in str(exc_info.value.message)

    def test_generate_http_error(self):
        """Test generate raises GenerationError on HTTP error."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        with patch.object(client._client, "post", return_value=mock_response):
            with pytest.raises(GenerationError) as exc_info:
                client.generate("Hello")

            assert "API error" in str(exc_info.value.message)

    def test_generate_payload_structure(self):
        """Test that generate sends correct payload structure."""
        client = OllamaClient(
            model_name="llama3:8b",
            temperature=0.2,
            max_tokens=1000,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "llama3:8b",
            "response": "Test",
            "prompt_eval_count": 5,
            "eval_count": 1,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            client.generate("Test prompt")

            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")

            assert payload["model"] == "llama3:8b"
            assert payload["prompt"] == "Test prompt"
            assert payload["stream"] is False
            assert payload["options"]["temperature"] == 0.2
            assert payload["options"]["num_predict"] == 1000


class TestOllamaClientGenerateWithMessages:
    """Tests for OllamaClient.generate_with_messages()."""

    def test_generate_with_messages_success(self):
        """Test successful chat generation."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "llama3:8b",
            "message": {"role": "assistant", "content": "Hello! How can I help?"},
            "prompt_eval_count": 20,
            "eval_count": 8,
        }
        mock_response.raise_for_status = MagicMock()

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        with patch.object(client._client, "post", return_value=mock_response):
            response = client.generate_with_messages(messages)

            assert response.content == "Hello! How can I help?"
            assert response.model == "llama3:8b"

    def test_generate_with_messages_json_format(self):
        """Test chat generation with JSON format."""
        client = OllamaClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "model": "llama3:8b",
            "message": {"role": "assistant", "content": '{"result": true}'},
            "prompt_eval_count": 15,
            "eval_count": 6,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            messages = [{"role": "user", "content": "Return JSON"}]
            response = client.generate_with_messages(messages, format="json")

            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["format"] == "json"

    def test_generate_with_messages_connection_error(self):
        """Test chat generation handles connection error."""
        client = OllamaClient()

        with patch.object(
            client._client, "post", side_effect=httpx.ConnectError("Connection refused")
        ):
            with pytest.raises(LLMConnectionError):
                client.generate_with_messages([{"role": "user", "content": "Hi"}])


class TestOllamaClientCleanup:
    """Tests for OllamaClient cleanup."""

    def test_client_cleanup_on_delete(self):
        """Test that httpx client is closed on deletion."""
        client = OllamaClient()
        mock_client = MagicMock()
        client._client = mock_client

        del client

        mock_client.close.assert_called_once()
