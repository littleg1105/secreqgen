"""Custom exceptions for SecReqGen."""


class SecReqGenError(Exception):
    """Base exception for SecReqGen."""

    def __init__(self, message: str, details: str | None = None) -> None:
        self.message = message
        self.details = details
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class ConfigurationError(SecReqGenError):
    """Raised when there is a configuration error."""

    pass


class IngestionError(SecReqGenError):
    """Raised when document ingestion fails."""

    pass


class RetrievalError(SecReqGenError):
    """Raised when document retrieval fails."""

    pass


class GenerationError(SecReqGenError):
    """Raised when requirements generation fails."""

    pass


class ValidationError(SecReqGenError):
    """Raised when validation fails."""

    pass


class LLMConnectionError(SecReqGenError):
    """Raised when connection to LLM (Ollama) fails."""

    pass


class VectorStoreError(SecReqGenError):
    """Raised when vector store operations fail."""

    pass
