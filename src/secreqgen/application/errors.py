"""Typed errors of the application layer.

The text of each error is plain. It holds no Rich markup.
"""

from __future__ import annotations

from secreqgen.exceptions import SecReqGenError


class ApplicationError(SecReqGenError):
    """Base class of the errors that a use case raises."""


class InputError(ApplicationError):
    """A file, a folder or an option is wrong."""


class NothingToDoError(InputError):
    """The input holds no user stories."""


class ModelServerError(ApplicationError):
    """Ollama does not answer, or it does not have the model."""

    def __init__(
        self, message: str, model: str | None = None, details: str | None = None
    ) -> None:
        super().__init__(message, details)
        self.model = model
