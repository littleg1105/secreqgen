"""Import weight of the command line.

Each case imports modules in a fresh interpreter. It then lists the heavy
modules that were loaded. Later steps of the cleanup add one case each.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def forbidden_loaded(imports: list[str], forbidden: list[str]) -> list[str]:
    """Import the modules in a subprocess. Return the forbidden ones that loaded."""
    code = (
        "import json, sys\n"
        f"for name in {imports!r}:\n"
        "    __import__(name)\n"
        f"found = [m for m in {forbidden!r}\n"
        "         if m in sys.modules]\n"
        "print(json.dumps(found))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_cli_import_is_light():
    found = forbidden_loaded(
        ["secreqgen.cli"],
        [
            "torch",
            "sentence_transformers",
            "chromadb",
            "numpy",
            "scipy",
            "secreqgen.ingestion",
            "secreqgen.retrieval",
            "secreqgen.evaluation",
            "secreqgen.threats",
        ],
    )
    assert found == []


HEAVY = [
    "chromadb",
    "torch",
    "sentence_transformers",
    "secreqgen.retrieval.vectorstore",
    "secreqgen.retrieval.retriever",
]


def test_threats_import_is_light():
    assert forbidden_loaded(["secreqgen.threats"], HEAVY) == []


def test_word_search_import_is_light():
    assert forbidden_loaded(["secreqgen.retrieval.word_search"], HEAVY) == []


LAZY_NAMES = {
    "QueryResult": "secreqgen.retrieval.vectorstore",
    "SecReqGenVectorStore": "secreqgen.retrieval.vectorstore",
    "SimpleRetriever": "secreqgen.retrieval.retriever",
    "RetrievalResult": "secreqgen.retrieval.retriever",
    "WordSearchRetriever": "secreqgen.retrieval.word_search",
    "WordRetrievalResult": "secreqgen.retrieval.word_search",
}


@pytest.mark.parametrize("name", sorted(LAZY_NAMES))
def test_lazy_name_is_the_defining_object(name):
    import secreqgen.retrieval as retrieval

    module = importlib.import_module(LAZY_NAMES[name])
    assert getattr(retrieval, name) is getattr(module, name)
    assert name in dir(retrieval)


def test_submodules_still_import():
    import secreqgen.retrieval.retriever
    import secreqgen.retrieval.vectorstore

    assert secreqgen.retrieval.retriever.SimpleRetriever is not None
    assert secreqgen.retrieval.vectorstore.SecReqGenVectorStore is not None


def test_unknown_name_raises_attribute_error():
    import secreqgen.retrieval as retrieval

    with pytest.raises(AttributeError):
        retrieval.NoSuchName


def test_application_builders_import_is_light():
    found = forbidden_loaded(
        ["secreqgen.application.clients", "secreqgen.application.retrievers"],
        HEAVY + ["secreqgen.ingestion", "secreqgen.retrieval"],
    )
    assert found == []


def test_application_use_case_import_is_light():
    found = forbidden_loaded(
        [
            "secreqgen.application.errors",
            "secreqgen.application.events",
            "secreqgen.application.inputs",
            "secreqgen.application.requirements",
        ],
        HEAVY
        + ["secreqgen.ingestion", "secreqgen.retrieval", "typer", "rich"],
    )
    assert found == []
