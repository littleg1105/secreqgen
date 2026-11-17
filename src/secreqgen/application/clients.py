"""Builders for the model clients of SecReqGen.

The functions read only the llm part of the settings (cfg.llm). Each function
imports the client class inside its body, so this module loads no heavy
library.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# The tested setup (word search) calls the model as the runs of Study 2 did
# (scripts/run_study2.py): a context window that holds the documentation, the
# prompt and the answer, a long time limit, and no JSON-mode option.
WORD_SEARCH_NUM_CTX = 16384
WORD_SEARCH_TIMEOUT_S = 900.0
DEFAULT_TIMEOUT_S = 120.0

ClientFactory = Callable[..., Any]


def build_ollama_client(cfg, tested_setup: bool, model_name: str | None = None):
    """Build the Ollama client of a generate command.

    A value in the settings (SECREQGEN_LLM_NUM_CTX, SECREQGEN_LLM_TIMEOUT,
    SECREQGEN_LLM_STRUCTURED_OUTPUT) wins. Without one, the word search uses
    the values of the tested runs, and the vector store path keeps the
    behaviour that it had before.
    """
    from secreqgen.generation import OllamaClient

    num_ctx = cfg.llm.num_ctx
    timeout = cfg.llm.timeout
    structured_output = cfg.llm.structured_output
    if tested_setup:
        num_ctx = num_ctx or WORD_SEARCH_NUM_CTX
        timeout = timeout or WORD_SEARCH_TIMEOUT_S
        if structured_output is None:
            structured_output = False
    return OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=model_name or cfg.llm.model_name,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        timeout=timeout or DEFAULT_TIMEOUT_S,
        num_ctx=num_ctx,
        structured_output=True if structured_output is None else structured_output,
    )


def build_evaluation_client(cfg, model_name: str):
    """Build the Ollama client of the evaluate commands."""
    from secreqgen.generation import OllamaClient

    return OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=model_name,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
    )


def build_health_client(cfg):
    """Build the Ollama client of the check-ollama command."""
    from secreqgen.generation import OllamaClient

    return OllamaClient(
        base_url=cfg.llm.base_url,
        model_name=cfg.llm.model_name,
    )
