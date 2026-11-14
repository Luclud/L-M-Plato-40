"""Shared helpers for Streamlit AppTest smoke tests of the viewer (quickstart, dev, empty decisions file)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "viewer" / "app.py"
PAGES = ["views/building.py", "views/incidents.py", "views/approvals.py", "views/learned.py", "views/proof.py",
         "views/tour.py", "views/ask.py"]


def quickstart_decisions(tmp_dir: Path) -> Path:
    out = tmp_dir / "qs.jsonl"
    subprocess.run([sys.executable, str(ROOT / "run.py"), str(ROOT / "data" / "quickstart"), "--out", str(out),
                    "--no-llm"], check=True, cwd=ROOT, capture_output=True)
    return out


def datasets(tmp_dir: Path) -> dict:
    """name -> (decisions, data dir). Dev uses the committed root decisions.jsonl (= fresh dev run)."""
    empty = tmp_dir / "empty.jsonl"
    empty.write_text("")
    return {"quickstart": (quickstart_decisions(tmp_dir), ROOT / "data" / "quickstart"),
            "dev": (ROOT / "decisions.jsonl", ROOT / "data" / "dev"),
            "empty": (empty, ROOT / "data" / "quickstart")}


def app(monkeypatch, decisions, data, approvals, timeout=180):
    testing = pytest.importorskip("streamlit.testing.v1")
    pytest.importorskip("plotly")
    monkeypatch.setenv("PLATO40_DECISIONS", str(decisions))
    monkeypatch.setenv("PLATO40_DATA", str(data))
    monkeypatch.setenv("PLATO40_APPROVALS", str(approvals))
    monkeypatch.setenv("PLATO40_DEFAULT_DATASET", "off")   # use the files above, not a local holdout
    return testing.AppTest.from_file(str(APP), default_timeout=timeout)


def errors(at):
    return [e.message for e in at.exception]
