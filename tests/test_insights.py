"""T11: Level 3 insights (analysis/insights.py)."""
import json
import re

import numpy as np
import pytest

from conftest import QS, ROOT

CONTRACT = ("insight_id", "target", "topic", "finding", "evidence", "recommendation", "expected_benefit", "confidence")
DEV = ROOT / "data" / "dev"


def _gen(data_dir, tmp_path, tag):
    from analysis import insights
    md, jl = tmp_path / f"{tag}.md", tmp_path / f"{tag}.jsonl"
    assert insights.main([str(data_dir), "--out", str(md), "--jsonl", str(jl)]) == 0
    return md.read_text(encoding="utf-8"), jl.read_text(encoding="utf-8")


def _check(records):
    by = {t: [r for r in records if r["target"] == t] for t in ("PLATO", "CLARA")}
    assert len(by["PLATO"]) >= 4 and len(by["CLARA"]) >= 4
    assert len({r["insight_id"] for r in records}) == len(records)
    for r in records:
        assert all(k in r for k in CONTRACT), r["insight_id"]
        assert "PLATO-40" in r["finding"]
        assert r["uncertainty"].strip() and r["evidence"]
        assert 0.0 <= r["confidence"] <= 1.0
        assert re.search(r"\d", r["finding"]), f"{r['insight_id']} has no numbers"
        assert "analysis failed" not in r["finding"], r["finding"]


def test_quickstart_contract_and_deterministic(tmp_path):
    if not (QS / "manifest.json").exists():
        pytest.skip("quickstart data missing")
    md1, jl1 = _gen(QS, tmp_path, "a")
    md2, jl2 = _gen(QS, tmp_path, "b")
    assert (md1, jl1) == (md2, jl2)
    _check([json.loads(x) for x in jl1.splitlines()])
    for section in ("## For PLATO", "## For CLARA", "## Target architecture", "## Limitations"):
        assert section in md1


def test_committed_outputs_meet_acceptance():
    recs = [json.loads(x) for x in (ROOT / "insights.jsonl").read_text(encoding="utf-8").splitlines()]
    _check(recs)
    assert all(r["status"] != "insufficient data" for r in recs)
    assert any(r["status"] in ("qualified", "rejected") for r in recs)
    md = (ROOT / "insights.md").read_text(encoding="utf-8")
    assert all(f"### {r['insight_id']}" in md for r in recs)


def test_committed_outputs_regenerate_from_dev(tmp_path):
    if not (DEV / "manifest.json").exists():
        pytest.skip("dev data missing")
    md, jl = _gen(DEV, tmp_path, "dev")
    assert jl == (ROOT / "insights.jsonl").read_text(encoding="utf-8")
    assert md == (ROOT / "insights.md").read_text(encoding="utf-8")


def test_occupancy_mask_matches_state(qs, qs_state):
    from analysis.common import occupancy_mask
    tel = qs.telemetry.iloc[::97]
    mask = occupancy_mask(qs_state, tel.room_id, tel.timestamp_utc, qs.end)
    ref = np.array([qs_state.occupied(r, t) for r, t in zip(tel.room_id, tel.timestamp_utc)])
    assert (mask == ref).mean() > 0.99


def test_analysis_never_reads_eval():
    forbidden = re.compile(r"""(['"][./]*eval[/\\]|^\s*(from|import)\s+eval\b|labels_quickstart)""", re.M)
    for p in (ROOT / "analysis").glob("*.py"):
        assert not forbidden.search(p.read_text()), p
