"""T8: the Study 2 analyzer gives the registered means and the known p-values.

The test imports scripts/analyze_study2.py as a module (its main() does not
run) and recomputes the report from the committed records.jsonl files. Then it
compares the report with the registered analysis file and with the s2.* keys
of thesis/numbers/numbers.json (tolerance 1e-9).

Blinding: the author must not see a Study 2 result before his review labels
are locked. So the test prints nothing when it passes, and a failure names
only the keys that differ, never a value.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

TOL = 1e-9
HERE = Path(__file__).resolve()
ANALYZER = HERE.parents[2] / "scripts" / "analyze_study2.py"
ANALYSIS_NAME = "analysis_2026-11-02.json"

# Key names of thesis/numbers/sections/study2.py.
MODELS = {"qwen": "qwen3.8:27b", "gemma": "gemma4:26b"}
ARMS = ["A", "B", "C", "D"]
CONTRASTS = [  # key in numbers.json, analyzer name, x arm, y arm (x minus y)
    ("c_minus_b", "C-B (primary)", "C", "B"),
    ("b_minus_a", "B-A", "B", "A"),
    ("d_minus_b", "D-B", "D", "B"),
]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)  # a script's main() runs only as __main__
    return module


def _diff(a, b, path: str, bad: list[str]) -> None:
    """Add to `bad` the path of each value in a that differs from b."""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            bad.append(f"{path} (keys)")
        for k in sorted(set(a) & set(b)):
            _diff(a[k], b[k], f"{path}.{k}", bad)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            bad.append(f"{path} (length)")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", bad)
    elif (
        isinstance(a, (int, float)) and isinstance(b, (int, float))
        and not isinstance(a, bool) and not isinstance(b, bool)
    ):
        if math.isnan(a) or math.isnan(b):
            if not (math.isnan(a) and math.isnan(b)):
                bad.append(path)
        elif abs(a - b) > TOL:
            bad.append(path)
    elif a != b:
        bad.append(path)


@pytest.fixture(scope="module")
def root() -> Path:
    for parent in HERE.parents:
        if (parent / "thesis" / "numbers" / "numbers.json").is_file():
            return parent
    pytest.skip("no folder above this test holds thesis/numbers/numbers.json")


@pytest.fixture(scope="module")
def stats(root: Path):
    path = root / "thesis" / "numbers" / "stats.py"
    if not path.is_file():
        pytest.skip(f"statistics module not found: {path}")
    return _load_module("thesis_numbers_stats_t8", path)


@pytest.fixture(scope="module")
def study2(root: Path) -> dict:
    data = root / "coded_project" / "real_test_cases" / "study2"
    if not (data / ANALYSIS_NAME).is_file():
        pytest.skip(f"Study 2 data or {ANALYSIS_NAME} not found under {data}")
    analyzer = _load_module("analyze_study2_t8", ANALYZER)
    # Point the analyzer at the data folder found above. This changes the
    # module in memory only; the registered script file stays as it is.
    analyzer.STUDY2 = data
    stored = json.loads((data / ANALYSIS_NAME).read_text())
    recomputed = [analyzer.analyze(rep["project"]) for rep in stored]
    # The registered file was written with json.dumps(default=str): use the same form.
    recomputed = json.loads(json.dumps(recomputed, default=str))
    numbers = json.loads((root / "thesis" / "numbers" / "numbers.json").read_text())
    return {"stored": stored, "recomputed": recomputed, "numbers": numbers}


def test_analyzer_reproduces_registered_analysis(study2: dict) -> None:
    stored, recomputed = study2["stored"], study2["recomputed"]
    assert [r["project"] for r in recomputed] == [r["project"] for r in stored]
    bad: list[str] = []
    for new, old in zip(recomputed, stored):
        _diff(new, old, new["project"], bad)
    assert not bad, f"{len(bad)} values differ from {ANALYSIS_NAME}: {bad[:20]}"


def _check_key(numbers: dict, key: str, expected, bad: list[str]) -> None:
    if key not in numbers:
        bad.append(f"{key} (missing)")
    else:
        _diff(numbers[key]["value"], expected, key, bad)


def test_numbers_json_agrees_with_recomputed(study2: dict, stats) -> None:
    numbers = study2["numbers"]
    bad: list[str] = []
    n_checked = 0
    for rep in study2["recomputed"]:
        proj = rep["project"]
        for mk, model in MODELS.items():
            m = rep["models"][model]
            pre = f"s2.{mk}.{proj}"
            # Per-cell arm means (the registered outcome).
            for arm in ARMS:
                key = f"{pre}.rate.{arm.lower()}"
                if arm in m["arms"]:
                    _check_key(numbers, key, m["arms"][arm]["frac_reqs_citing"], bad)
                    n_checked += 1
                elif isinstance(numbers.get(key, {}).get("value"), (int, float)):
                    bad.append(f"{key} (a number for an arm that did not run)")
            # Per-cell contrasts.
            for ckey, name, x, y in CONTRASTS:
                key = f"{pre}.{ckey}"
                if name not in m["contrasts"]:
                    if f"{key}.mean_pp" in numbers:
                        bad.append(f"{key}.mean_pp (no such contrast in the analyzer)")
                    continue
                c = m["contrasts"][name]
                diffs = [
                    m["per_story"][x][s]["frac_reqs_citing"]
                    - m["per_story"][y][s]["frac_reqs_citing"]
                    for s in c["stories"]
                ]
                _check_key(numbers, f"{key}.mean_pp", 100 * c["mean_diff"], bad)
                _check_key(numbers, f"{key}.per_story",
                           [[s, 100 * d] for s, d in zip(c["stories"], diffs)], bad)
                _check_key(numbers, f"{key}.p", stats.wilcoxon_exact(diffs)["p"], bad)
                _check_key(numbers, f"{key}.dz", stats.cohen_dz(diffs), bad)
                n_checked += 4
    assert n_checked > 0, "no s2 key was checked"
    assert not bad, f"{len(bad)} numbers.json keys differ: {bad}"


def test_wilcoxon_exact_known_values(stats) -> None:
    five = stats.wilcoxon_exact([0.1, 0.2, 0.3, 0.4, 0.5])
    assert five["n_used"] == 5
    assert five["p"] == pytest.approx(0.0625, abs=1e-15)
    fifteen = stats.wilcoxon_exact([i / 100 for i in range(1, 16)])
    assert fifteen["n_used"] == 15
    assert fifteen["p"] == pytest.approx(2 / 2**15, abs=1e-15)
