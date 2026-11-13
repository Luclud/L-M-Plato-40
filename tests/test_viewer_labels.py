"""T17 viewer foundation: every pipeline code has a friendly label; helpers; every page renders on 3 datasets."""
import json
from pathlib import Path

import pandas as pd
import pytest

from plato40.decision import ALL_ACTIONS, ISSUE_TYPES, PRIORITIES
from viewer import components as C
from viewer import labels as L

import viewer_apptest as V

ROOT = Path(__file__).resolve().parents[1]


def _building(profile):
    return json.loads((ROOT / "data" / profile / "building.json").read_text())


def test_every_issue_type_and_action_has_a_label_and_icon():
    for code in ISSUE_TYPES:
        assert L.label("issue", code) != code and L.icon("issue", code).startswith(":material/"), code
    for code in ALL_ACTIONS:
        assert L.label("action", code) != code and L.icon("action", code).startswith(":material/"), code
    for code in PRIORITIES:
        assert L.label("priority", code) != code
    for code in ("autonomous", "human_approval", "approval_and_consent"):
        assert L.label("autonomy", code) != code


@pytest.mark.parametrize("profile", ["quickstart", "dev"])
def test_every_resource_and_asset_in_building_json_has_a_label(profile):
    b = _building(profile)
    codes = [r["resource_id"] for r in b["staff"]["resources"]]
    codes += [a["id"] for a in b["building"]["assets"]]
    codes += [r["id"] for r in b["building"]["rooms"]] + [r["water_meter_id"] for r in b["building"]["rooms"]]
    codes += [g["id"] for g in b["building"]["gateways"]]
    for code in codes:
        assert L.label("asset", code) != code, code
    for r in b["staff"]["resources"]:
        assert L.label("resource", r["resource_id"]) != r["resource_id"]
        assert L.label("role", r["role"]) != r["role"], r["role"]


def test_label_falls_back_and_never_crashes():
    assert L.label("issue", "brand_new_type") == "brand_new_type"
    assert L.label("nonsense_kind", "X") == "X"
    assert L.label("asset", None) == ""
    assert L.label("asset", "R104") == "Room 104"
    assert L.icon("issue", "nope") == ":material/info:"
    assert L.assets_text(["BUILDING"]) == "Whole building"


def test_app_name_is_not_plato_or_clara_product_name():
    assert L.APP_NAME and L.APP_NAME not in ("PLATO", "CLARA", "PLATO-40", "CLARA-40")


def test_local_time_and_headline():
    assert C.local_time("2026-01-12T18:30:00Z") == "Mon 12 Jan, 19:30"
    assert C.local_time("2026-07-12T18:30:00Z", with_day=False) == "20:30"
    assert C.local_time(None) == ""
    assert C.time_range("2026-01-12T18:00:00Z", "2026-01-12T18:30:00Z") == "Mon 12 Jan, 19:00–19:30"
    r = "Continuous high flow ~27 L per 15 min since 18:00, no shower pattern. Likely burst hose -> inspect."
    assert C.headline(r) == "Continuous high flow ~27 L per 15 min since 18:00, no shower pattern"
    assert len(C.headline("word " * 100)) <= 120
    assert C.headline("v1.5 bar drop; next") == "v1.5 bar drop"


def test_honesty_labels_present():
    assert "not sent" in L.PROPOSED and "Simulated" == L.SIMULATED
    assert "Mock" in L.MOCK and "not granted" in L.CONSENT_NEEDED
    assert C.simulated_badge("SIMULATED: 12 EUR saved").startswith(f":gray-badge[{L.SIMULATED}]")
    assert C.simulated_badge(None) is None


def test_figure_uses_numbered_markers_and_local_time():
    from viewer.core import Chart, figure
    idx = pd.date_range("2026-01-12T17:00Z", periods=8, freq="15min")
    ch = Chart(title="t", unit="L", series={"room": pd.Series(range(8), index=idx, dtype=float)})
    fig = figure(ch, [(idx[3], "Flagged a problem"), (idx[1], "Asked for an inspection")], shade=(idx[1], idx[3]))
    mk = [d for d in fig.data if d.name == "what we did"][0]
    assert list(mk.text) == ["1", "2"] and "Asked for an inspection" in mk.hovertext[0]
    assert pd.Timestamp(mk.x[0]).hour == 18  # 17:15Z -> 18:15 local
    assert not [s for s in fig.layout.shapes if s.type == "line"]  # no dotted vertical lines any more


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer"))


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_every_page_renders(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "approvals.json")
    at.run()
    assert not at.exception, V.errors(at)
    for page in V.PAGES:
        at.switch_page(page)
        at.run()
        assert not at.exception, (name, page, V.errors(at))
