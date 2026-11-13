"""T19 Building view: twin state at one moment + AppTest of the page."""
import pandas as pd
import pytest

from plato40 import io
from viewer import core, twin

import viewer_apptest as V


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_bld"))


def test_room_state_missing_is_unknown_never_vacant_clean():
    assert twin.room_state(None, 0.0) == "unknown"
    assert twin.room_state(float("nan"), 0.0) == "unknown"
    assert twin.room_state("clean", float("nan")) == "unknown"
    assert twin.room_state("clean", 0.0) == "vacant_clean"
    assert twin.room_state("clean", 1.0) == "occupied"
    assert twin.room_state("dirty", 1.0) == "needs_cleaning"
    assert twin.room_state("in_progress", 0.0) == "cleaning"


def test_missing_telemetry_rows_and_values_are_no_data():
    d = io.load("data/quickstart")
    tw = twin.Twin(d)
    tel = d.telemetry
    gap = tel[tel.housekeeping_state.isna()].iloc[0]
    rooms = tw.rooms_at(gap.timestamp_utc).set_index("room_id")
    assert rooms.loc[gap.room_id, "state"] == "unknown"
    before = tw.rooms_at(tw.start - pd.Timedelta(days=1))
    assert set(before.state) == {"unknown"}


def test_quickstart_r104_burst_and_cleaner_sick_as_known(sets):
    dec, data = sets["quickstart"]
    df = core.load_decisions(dec)
    d = io.load(data)
    t = twin.default_moment(df, d.start)
    lab = next(x for x in core.load_labels("quickstart") if x["id"] == "QS-S02-R104-burst")
    assert pd.Timestamp(lab["start_utc"]) <= t <= pd.Timestamp(lab["good_by_utc"])
    o = twin.open_at(twin.episode_spans(df), t)
    assert any("R104" in a for a in o.assets) and "water_leak" in set(o.issue_type)
    sick = d.roster[(d.roster.status == "sick") & (d.roster.resource_id == "CLEANER_1")
                    & (d.roster.start_utc.dt.strftime("%m-%d") == "01-24")].iloc[0]
    for dt, expect in ((-pd.Timedelta(minutes=15), "scheduled"), (pd.Timedelta(0), "sick")):
        at = sick.status_known_utc + dt
        s = twin.staff_at(d.roster, at, *twin.local_day(at)).set_index("resource_id")
        assert s.loc["CLEANER_1", "status"] == expect


def test_planned_cleanings_only_known_and_later_today(sets):
    dec, _ = sets["dev"]
    df = core.load_decisions(dec)
    c = df[(df.action == "schedule_cleaning") & df.scheduled_for_utc.notna()].iloc[0]
    when = pd.Timestamp(c.scheduled_for_utc)
    _, end = twin.local_day(c.t)
    room = next(a for a in c.affected_assets if a.startswith("R"))
    if when < end and when > c.t:
        assert room in twin.planned_cleanings(df, c.t, end)
    assert room not in twin.planned_cleanings(df, c.t - pd.Timedelta(minutes=15), end) or \
        (df[(df.action == "schedule_cleaning") & (df.t < c.t)].affected_assets.map(lambda x: room in x).any())


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_building_page_renders(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "a.json")
    at.switch_page("views/building.py")
    at.run()
    assert not at.exception, V.errors(at)
    md = " ".join(m.value for m in at.markdown)
    assert "Needs attention now" in md and "Floor 1" in md and "Staff today" in md
    assert len([b for b in at.button if b.key and b.key.startswith("tile_")]) == 40
    if name == "quickstart":
        assert any(b.key == "tile_R104" and "●" in b.label for b in at.button)
        assert "Water leak" in md
        at.button(key="tile_R104").click().run()
        assert not at.exception and "Room 104" in " ".join(m.value for m in at.markdown)
        at.query_params["t"] = "2026-01-24T05:00Z"   # 06:00 local, before the sick call (known 05:30Z)
        at.run()
        assert "Cleaner 1 ✓" in " ".join(m.value for m in at.markdown)
        at.query_params["t"] = "2026-01-24T05:30Z"
        at.run()
        assert "Cleaner 1 sick" in " ".join(m.value for m in at.markdown)
