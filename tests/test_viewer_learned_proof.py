"""T21 What we learned + Proof pages."""
import json
from pathlib import Path
import re
import shutil

import pytest

from viewer import labels as L

import viewer_apptest as V

RAW = [re.compile(p) for p in (r"\bEP-\d+", r"\bD\d{6}\b", r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
                               r"\b[\w/]+\.(?:csv|jsonl|md|py)\b")]


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_lp"))


def _open(monkeypatch, page, dec, data, tmp_path, insights=None):
    if insights is not None:
        monkeypatch.setenv("PLATO40_INSIGHTS_DIR", str(insights))
    at = V.app(monkeypatch, dec, data, tmp_path / "a.json")
    at.switch_page(page)
    at.run()
    assert not V.errors(at), V.errors(at)
    return at


def _text(at):
    return " ".join(str(x.value) for x in [*at.markdown, *at.caption, *at.subheader, *at.info])


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_learned_has_11_cards_in_two_columns(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    at = _open(monkeypatch, "views/learned.py", dec, data, tmp_path)
    from viewer.views import common          # the page shows the selected dataset's own lessons (dev: repo root)
    d = common.insights_dir(Path(data))
    rows = [json.loads(x) for x in (d / "insights.jsonl").read_text().splitlines() if x.strip()]
    topics = {r["topic"] for r in rows}
    cards = [m.value for m in at.markdown if m.value.startswith("**") and m.value[2:].split("**")[0] in topics]
    assert len(cards) == len(rows) == 11
    assert sum(r["target"] == "PLATO" for r in rows) == 5 and sum(r["target"] == "CLARA" for r in rows) == 6
    md = [m.value for m in at.markdown]
    assert "#### For the next PLATO building" in md and "#### For CLARA next year" in md
    assert "#### Limitations" in md
    assert sum("estimate" in c.value for c in at.caption) == 11
    assert all("PLATO-40" in r["finding"] for r in rows)
    text = _text(at)
    for rx in RAW:
        assert not rx.search(text), (rx.pattern, rx.search(text).group(0))


def test_learned_and_proof_without_insights_files(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    empty = tmp_path / "noins"
    empty.mkdir()
    at = _open(monkeypatch, "views/learned.py", dec, data, tmp_path, insights=empty)
    assert any("not loaded in this installation yet" in i.value for i in at.info)
    assert any("analysis/insights.py" in c.value for c in at.code)
    at.switch_page("views/proof.py").run()
    assert not V.errors(at)
    assert any("insights.md: not generated yet" in c.value for c in at.caption)


def test_learned_skips_damaged_line(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    d = tmp_path / "ins"
    d.mkdir()
    lines = (V.ROOT / "insights.jsonl").read_text().splitlines()
    (d / "insights.jsonl").write_text("\n".join([lines[0], "{broken", lines[-1]]))
    shutil.copy(V.ROOT / "insights.md", d / "insights.md")
    at = _open(monkeypatch, "views/learned.py", dec, data, tmp_path, insights=d)
    assert any(c.value == "1 insight(s)" for c in at.caption)


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_proof_validator_on_click_only(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    at = _open(monkeypatch, "views/proof.py", dec, data, tmp_path)
    assert not at.success and not at.error
    m = next(x.value for x in at.markdown if x.value.startswith("The BMS raised"))
    assert re.match(r"The BMS raised \d+ water alarm\(s\); we opened \d+ water-leak incident", m)
    labels = [b.label for b in at.get("download_button")]
    assert labels[:1] == ["decisions.jsonl"]
    at.button(key="proof_run").click().run()
    assert not V.errors(at)
    assert any("PASS" in s.value for s in at.success), [e.value for e in at.error]
    if name == "quickstart":
        assert any("Incidents detected: 7/7" in x.value for x in at.markdown)
        assert any("labels are not ground truth" in c.value for c in at.caption)
        assert any(x.value == "- Not detected: none" for x in at.markdown)


def test_bms_sentence_uses_real_numbers(sets, monkeypatch, tmp_path):
    from plato40 import io
    from viewer import core
    dec, data = sets["quickstart"]
    d = io.load(str(data))
    b = core.bms_alarms(d)
    n = int(((b.alarm_code == "WATER_NIGHT_FLOW") & (b.state == "raised")).sum())
    leaks = core.load_decisions(dec).query("issue_type == 'water_leak'").episode.nunique()
    at = _open(monkeypatch, "views/proof.py", dec, data, tmp_path)
    assert any(x.value.startswith(f"The BMS raised {n} water alarm(s); we opened {leaks} water-leak")
               for x in at.markdown)
    assert L.ROUTINE_TYPES
