"""One path through the whole lab: run (fake client) -> report -> blind batches -> judgments -> scores.

Uses the real prompt builder, the real reference of lab_measures and the real judging module. No
network, no model, no key. Everything is written below tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests" / "unit"))

from test_tuning_run_versions import (  # noqa: E402,F401
    SMALL,
    FakeClient,
    builder,
    guard,
    lab,
    pv_root,
    safe_env,
)
from tuning_lib import judging as J  # noqa: E402


def test_run_report_batches_scores(tmp_path, guard, builder, safe_env, capsys):
    assert lab(tmp_path, guard, builder, *SMALL, factory=lambda pin: FakeClient()) == 0
    capsys.readouterr()
    run_dir = tmp_path / "runs" / "L1"
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["baseline"] == "v0_old"

    design = J.build_batches(run_dir, ["v0_old", "v1"], ["BW-S2-001"], per_cell=2, seed=7,
                             round_name="r1", guard=guard)
    assert design.n_items == 2 * 2 * 2  # 2 versions x 2 models x 2 items
    batch = json.loads(design.batch_paths[0].read_text(encoding="utf-8"))
    assert [i["qid"] for i in batch["items"]][0] == "BW-S2-001-q01"
    text = design.batch_paths[0].read_text(encoding="utf-8").lower()
    assert "v0_old" not in text and '"v1"' not in text

    key = json.loads(design.key_path.read_text(encoding="utf-8"))
    qids = sorted(key)
    labels = ["S", "G", "U", "S"]
    judgments = {"BW-S2-001": {
        name: [{"qid": q, "label": labels[(n + i) % 4 - 0] if labels[(n + i) % 4] != "U" else "U",
                "kind": "wrong_fact" if labels[(n + i) % 4] == "U" else "none",
                "claim": "c", "why": "w", "doubt": False} for i, q in enumerate(qids)]
        for n, name in enumerate(("judge_a", "judge_b"))}}
    jp = tmp_path / "judgments.json"
    jp.write_text(json.dumps(judgments), encoding="utf-8")
    sc = J.score(jp, design.key_path, guard=guard)
    assert sc["judges"] == ["judge_a", "judge_b"]
    assert set(sc["by_version"]) == {"v0_old", "v1"}
    assert (design.round_dir / "scores.json").is_file() and (design.round_dir / "scores.md").is_file()
