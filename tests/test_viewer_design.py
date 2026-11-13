"""T30 Next building: lessons pinned to the PLATO-40 drawing (pure board + AppTest)."""
import json

import pytest

from viewer import design_board as D

import viewer_apptest as V

PAGE = "views/design.py"
ROWS = [json.loads(x) for x in (V.ROOT / "insights.jsonl").read_text().splitlines() if x.strip()]
BUILDING = json.loads((V.ROOT / "data" / "dev" / "building.json").read_text())


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_design"))


def test_every_plato_insight_has_a_pin_in_the_toml():
    board = D.load_board()
    plato = [r["insight_id"] for r in ROWS if r["target"] == "PLATO"]
    assert len(plato) >= 5
    for iid in plato:
        assert board["pins"][iid]["element"] in board["elements"], iid
    pins, unpinned, unknown = D.pins(ROWS, board)
    assert [p["id"] for p in pins] == plato and not unpinned and not unknown
    assert {p["element"] for p in pins} == {"floor1_slab", "south_upper", "roof_access", "risers", "gateways"}


def test_unknown_and_unpinned_ids_are_listed_not_crashing():
    board = D.load_board()
    rows = ROWS + [{"insight_id": "PLATO-P9", "target": "PLATO", "topic": "New lesson", "confidence": 0.3}]
    board = {**board, "pins": {**board["pins"], "PLATO-P8": {"element": "risers"}}}
    pins, unpinned, unknown = D.pins(rows, board)
    assert "PLATO-P9" in unpinned and "PLATO-P8" in unknown
    assert D.svg(BUILDING, pins, board).startswith("<svg")


def test_svg_has_building_parts_and_pins():
    pins, _, _ = D.pins(ROWS, D.load_board(), ("PLATO", "CLARA"))
    s = D.svg(BUILDING, pins, selected="PLATO-P3")
    assert s.count("<title>Room ") == 40
    assert all(f"Floor {f}" in s for f in range(1, 5))
    assert "Ventilation unit" in s and "Technical room" in s and "Car park" in s
    assert s.count('href="?pin=') == len(pins) == len(ROWS)
    assert 'stroke-width="4"' in s                                       # selected pin ring


def test_confidence_levels_and_labels():
    assert D.conf_level(0.8) == "high" and D.conf_level(0.6) == "medium" and D.conf_level(None) == "low"
    assert D.pin_label("PLATO-P3") == "3" and D.pin_label("CLARA-C4") == "C4"


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_design_page_apptest(sets, monkeypatch, tmp_path, name):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page(PAGE)
    at.run()
    assert not V.errors(at)
    text = " ".join(m.value for m in at.markdown)
    assert "<svg" in text and "Recommendation" in text
    assert not [m for m in at.markdown if m.value.strip() in ("None", "`None`")]   # no stray magic output
    assert len(at.radio[0].options) == 5
    at.toggle(key="design_clara").set_value(True).run()
    assert len(at.radio[0].options) == len(ROWS)
    at.radio[0].set_value("PLATO-P3").run()
    text = " ".join(m.value for m in at.markdown)
    assert "Rooftop plant" in text and not V.errors(at)


def test_design_page_follows_the_insights_file(sets, monkeypatch, tmp_path):
    rows = [dict(r) for r in ROWS]
    rows[0]["finding"] = "PLATO-40: changed finding with 42.0 % for the test. More text."
    (tmp_path / "insights.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setenv("PLATO40_INSIGHTS_DIR", str(tmp_path))
    dec, data = sets["quickstart"]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page(PAGE)
    at.run()
    assert any("changed finding with 42.0 %" in m.value for m in at.markdown)


def test_design_page_without_insights(sets, monkeypatch, tmp_path):
    monkeypatch.setenv("PLATO40_INSIGHTS_DIR", str(tmp_path))
    dec, data = sets["quickstart"]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page(PAGE)
    at.run()
    assert not V.errors(at)
    assert any("not loaded in this installation yet" in i.value for i in at.info)
