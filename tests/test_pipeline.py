"""End-to-end: pipeline runs on quickstart and the validator passes."""
import json
import subprocess
import sys

from conftest import QS, ROOT


def test_pipeline_and_validator(tmp_path, qs):
    out = tmp_path / "decisions.jsonl"
    r = subprocess.run([sys.executable, "run.py", str(QS), "--out", str(out)], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert rows, "no decisions"
    v = subprocess.run([sys.executable, "validate.py", str(out), str(QS)], cwd=ROOT, capture_output=True, text=True)
    assert v.returncode == 0, v.stdout[-3000:]


def test_validator_catches_unsafe(tmp_path, qs):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(json.dumps(x) for x in [
        {"event_id": "X1", "timestamp_utc": "2026-01-08T07:00:00Z", "affected_assets": ["R105"],
         "issue_type": "room_turnaround", "action": "schedule_cleaning", "priority": "high", "reason": "t",
         "requires_human_approval": False, "resource_id": "CLEANER_1"},
        {"event_id": "X2", "timestamp_utc": "2026-01-06T08:45:00Z", "affected_assets": ["R102"],
         "issue_type": "room_turnaround", "action": "release_room", "priority": "high", "reason": "t",
         "requires_human_approval": False},
    ]))
    v = subprocess.run([sys.executable, "validate.py", str(bad), str(QS)], cwd=ROOT, capture_output=True, text=True)
    assert v.returncode == 1
    assert "CLEANER_1 not available" in v.stdout and "release_room R102" in v.stdout


def test_no_eval_imports_in_pipeline():
    """The pipeline must never read evaluation labels."""
    import re
    forbidden = re.compile(r"""(['"][./]*eval[/\\]|^\s*(from|import)\s+eval\b|labels_quickstart)""", re.M)
    for p in (ROOT / "plato40").rglob("*.py"):
        assert not forbidden.search(p.read_text()), f"{p} references eval/ labels"
