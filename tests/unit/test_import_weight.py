"""Import weight of the command line.

Each case imports modules in a fresh interpreter. It then lists the heavy
modules that were loaded. Later steps of the cleanup add one case each.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

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
