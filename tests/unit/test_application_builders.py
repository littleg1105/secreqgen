"""Tests of the shared builders in secreqgen.application."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from secreqgen.application import clients, retrievers
from secreqgen.config import LLMConfig


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NUM_CTX", "TIMEOUT", "STRUCTURED_OUTPUT"):
        monkeypatch.delenv(f"SECREQGEN_LLM_{name}", raising=False)


def test_tested_setup_uses_the_values_of_the_tested_runs() -> None:
    cfg = SimpleNamespace(llm=LLMConfig())
    client = clients.build_ollama_client(cfg, tested_setup=True)
    assert client.num_ctx == 16384
    assert client.timeout == 900.0
    assert client.structured_output is False


def test_a_setting_wins_over_the_tested_setup() -> None:
    chosen = LLMConfig(structured_output=True, num_ctx=8192, timeout=60.0)
    client = clients.build_ollama_client(SimpleNamespace(llm=chosen), True)
    assert (client.structured_output, client.num_ctx, client.timeout) == (
        True,
        8192,
        60.0,
    )


def test_other_setup_keeps_the_old_values() -> None:
    cfg = SimpleNamespace(llm=LLMConfig())
    client = clients.build_ollama_client(cfg, tested_setup=False)
    assert client.num_ctx is None
    assert client.timeout == 120.0
    assert client.structured_output is True


def test_model_name_argument_wins() -> None:
    cfg = SimpleNamespace(llm=LLMConfig())
    client = clients.build_ollama_client(cfg, False, model_name="other-model")
    assert client.model_name == "other-model"


class RecordingClient:
    """Stand-in for OllamaClient. It keeps the arguments."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_health_and_evaluation_clients_pass_only_their_arguments(monkeypatch) -> None:
    monkeypatch.setattr("secreqgen.generation.OllamaClient", RecordingClient)
    cfg = SimpleNamespace(
        llm=SimpleNamespace(
            base_url="http://host:1",
            model_name="m0",
            temperature=0.3,
            max_tokens=77,
        )
    )
    assert clients.build_health_client(cfg).kwargs == {
        "base_url": "http://host:1",
        "model_name": "m0",
    }
    assert clients.build_evaluation_client(cfg, "m1").kwargs == {
        "base_url": "http://host:1",
        "model_name": "m1",
        "temperature": 0.3,
        "max_tokens": 77,
    }


def make_fakes(monkeypatch):
    log: list = []

    class FakeEmbedder:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            log.append(("embedder", kwargs))

    class FakeStore:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            log.append(("store", kwargs))

    class FakeRetriever:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            log.append(("retriever", kwargs))

    monkeypatch.setattr("secreqgen.ingestion.DocumentEmbedder", FakeEmbedder)
    monkeypatch.setattr("secreqgen.retrieval.SecReqGenVectorStore", FakeStore)
    monkeypatch.setattr("secreqgen.retrieval.SimpleRetriever", FakeRetriever)
    return log


def make_cfg():
    return SimpleNamespace(
        embedding=SimpleNamespace(model_name="emb", device="cpu", batch_size=5),
        vectorstore=SimpleNamespace(persist_directory="/db", collection_prefix="p"),
        retrieval=SimpleNamespace(top_k=4, similarity_threshold=0.4),
    )


def test_build_vector_retriever_passes_the_settings(monkeypatch) -> None:
    log = make_fakes(monkeypatch)
    retriever = retrievers.build_vector_retriever(make_cfg())
    assert [name for name, _ in log] == ["embedder", "store", "retriever"]
    assert log[0][1] == {"model_name": "emb", "device": "cpu"}
    assert log[1][1] == {"persist_directory": "/db", "collection_prefix": "p"}
    assert retriever.kwargs["top_k"] == 4
    assert retriever.kwargs["similarity_threshold"] == 0.4
    assert retriever.kwargs["embedder"].kwargs == log[0][1]
    assert retriever.kwargs["vectorstore"].kwargs == log[1][1]


def test_build_embedder_passes_batch_size_only_when_given(monkeypatch) -> None:
    log = make_fakes(monkeypatch)
    retrievers.build_embedder(make_cfg())
    retrievers.build_embedder(make_cfg(), batch_size=5)
    assert log[0][1] == {"model_name": "emb", "device": "cpu"}
    assert log[1][1] == {"model_name": "emb", "device": "cpu", "batch_size": 5}


def test_build_vector_store_passes_the_settings(monkeypatch) -> None:
    log = make_fakes(monkeypatch)
    retrievers.build_vector_store(make_cfg())
    assert log == [("store", {"persist_directory": "/db", "collection_prefix": "p"})]
