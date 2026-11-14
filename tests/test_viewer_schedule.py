"""T28 Housekeeping timeline: pure schedule functions + AppTest of the page and the incident embed."""
import time

import pandas as pd
import pytest

from plato40 import io
from viewer import core
from viewer import schedule as S

import viewer_apptest as V

HK = "views/housekeeping.py"


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_hk"))


@pytest.fixture(scope="module")
def qs(sets):
    dec, data = sets["quickstart"]
    return io.load(data), core.load_decisions(dec)


def _day(qs, day):
    return S.Day(qs[0], qs[1], pd.Timestamp(day))


def test_plan_window_regex_cleaner_robot_oncall():
    day = pd.Timestamp("2026-01-08").date()
    r = "plan: ROBOT_2 floor pre-clean 10:45-11:15, then CLEANER_3 11:45-12:30 local -> room clean ~12:30 local."
    assert S.plan_window(r, "CLEANER_3", day) == (pd.Timestamp("2026-01-08T10:45Z"), pd.Timestamp("2026-01-08T11:30Z"))
    assert S.plan_window(r, "ROBOT_2", day) == (pd.Timestamp("2026-01-08T09:45Z"), pd.Timestamp("2026-01-08T10:15Z"))
    o = "no slot left); ONCALL_1 on site ~16:45, room clean ~17:30 local (one room at a time, costly)"
    assert S.plan_window(o, "ONCALL_1", day) == (pd.Timestamp("2026-01-08T15:45Z"), pd.Timestamp("2026-01-08T16:30Z"))
    assert S.plan_window("no times here", "CLEANER_1", day) is None
    summer = pd.Timestamp("2026-07-01").date()
    assert S.plan_window("CLEANER_1 11:00-12:00 local", "CLEANER_1", summer)[0] == pd.Timestamp("2026-07-01T09:00Z")


def test_short_reason_is_plain_words():
    assert S.short_reason("CLEANER_1 sick became known - capacity re-planned") == \
        "Cleaner 1 sick became known — re-planned"
    assert S.short_reason("daily re-plan 06:30 local") == "Daily re-plan 06:30"
    assert "R308" not in S.short_reason("checkout of R308 at 10:45 local")


def test_actual_blocks_pair_start_and_completion(qs):
    d = _day(qs, "2026-01-08")
    a = d.actual_blocks()
    assert len(a) and (a.end > a.start).all()
    ev = qs[0].events
    day_ev = ev[(ev.timestamp_utc >= d.start) & (ev.timestamp_utc < d.end)]
    assert len(a[a.kind == "cleaning"]) == (day_ev.type == "cleaning_completed").sum()
    assert len(a[a.kind == "robot"]) == (day_ev.type == "robot_floor_cleaning_completed").sum()
    r308 = a[a.room == "R308"].sort_values("start")
    assert list(r308.kind) == ["robot", "cleaning"]           # floor pre-clean before the human turnover


def test_absence_with_known_at_and_replan_not_before(qs):
    d = _day(qs, "2026-01-08")
    ab = d.absences()
    assert list(ab.resource_id) == ["CLEANER_1"] and ab.status.iloc[0] == "sick"
    known = ab.known_at.iloc[0]
    assert known == pd.Timestamp("2026-01-08T05:15Z")
    assert d.absences(known - pd.Timedelta(minutes=15)).empty          # invisible before it is known
    rp = d.replans()
    assert len(rp) and rp.t.min() >= known
    assert "Cleaner 1 sick" in rp.label.iloc[0]


def test_as_of_hides_future_actuals_and_plans(qs):
    d = _day(qs, "2026-01-08")
    t = S.at_local(d.day, "06:00")
    assert d.actual_blocks(t).empty or (d.actual_blocks(t).start <= t).all()
    assert (d.actual_blocks(t).end <= t).all()
    assert (d.planned_blocks(t).known_at <= t).all()
    full = d.actual_blocks()
    assert (full.start > t).any()


def test_superseded_plans_kept_and_flagged(qs):
    d = _day(qs, "2026-01-08")
    p = d.planned_blocks()
    r = p[(p.room == "R308") & ~p.resource_id.str.startswith("ROBOT")]
    assert r.superseded.sum() >= 1 and (~r.superseded).sum() == 1
    assert r[~r.superseded].resource_id.iloc[0] == "CLEANER_3"
    early = d.planned_blocks(pd.Timestamp("2026-01-08T05:00Z"))
    assert list(early[early.room == "R308"].resource_id) == ["CLEANER_1"]
    assert not early.superseded.any()


def test_room_released_before_checkin_and_deadlines(qs):
    d = _day(qs, "2026-01-08")
    dl = d.deadlines()
    rel = dl[(dl.kind == "released") & (dl.room == "R308")].t.iloc[0]
    assert rel < dl[dl.kind == "checkin"].t.iloc[0]
    assert (dl.kind == "early_checkin").any() and (dl.kind == "waiting").any()


def test_surge_day_oncall_and_demand_over_capacity(qs):
    d = _day(qs, "2026-01-24")
    s = d.day_summary()
    assert s["over"] and s["oncall"] == 2 and s["absent"] == 1
    a = d.actual_blocks()
    assert len(a[a.kind == "oncall"]) == 2 and len(a[a.kind == "robot"]) > 0
    p = d.planned_blocks()
    assert set(p[p.resource_id == "ONCALL_1"].room) == {"R101", "R306"}
    assert len(d.replans())


def test_no_work_assigned_during_known_absence(qs):
    for day in ("2026-01-08", "2026-01-17", "2026-01-24"):
        d = _day(qs, day)
        ab = d.absences()
        for blocks in (d.actual_blocks(), d.planned_blocks()):
            live = blocks.loc[~blocks.superseded.astype(bool)]
            for x in ab.itertuples():
                clash = live[(live.resource_id == x.resource_id) & (live.start < x.end) & (live.end > x.start)
                             & (live.known_at >= x.known_at)]
                assert clash.empty, (day, clash)


def test_empty_decisions_and_empty_day(qs):
    d = S.Day(qs[0], qs[1].iloc[0:0], pd.Timestamp("2026-01-08"))
    assert d.planned_blocks().empty and d.replans().empty
    assert len(d.actual_blocks())                      # events still show what happened
    far = S.Day(qs[0], qs[1], pd.Timestamp("2030-01-01"))
    assert far.shifts().empty and far.actual_blocks().empty and far.day_summary()["rooms"] == 0


def test_dev_day_is_fast_after_cache(sets):
    dec, data = sets["dev"]
    dd, df = io.load(data), core.load_decisions(dec)
    d = S.Day(dd, df, pd.Timestamp("2026-05-09"))
    d.planned_blocks()
    t = time.perf_counter()
    for f in (d.shifts, d.absences, d.actual_blocks, d.planned_blocks, d.deadlines, d.replans, d.day_summary):
        f()
    assert time.perf_counter() - t < 1.0


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_housekeeping_page_apptest(sets, monkeypatch, tmp_path, name):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page(HK)
    at.run()
    assert not V.errors(at)
    text = " ".join(m.value for m in at.markdown) + " ".join(i.value for i in at.info)
    assert "Housekeeping" in " ".join(s.value for s in at.subheader)
    if name != "empty":
        assert "rooms to turn over" in text
    for tg in at.toggle:
        tg.set_value(not tg.value)
    at.run()
    assert not V.errors(at)
    assert not any(code in text for code in ("CLEANER_", "ROBOT_", "ONCALL_", "schedule_cleaning"))


def test_housekeeping_page_selected_moment_and_empty_day(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.query_params["t"] = "2026-01-08T05:00Z"
    at.run()
    at.switch_page(HK)
    at.run()
    assert not V.errors(at)
    assert any("as known at 06:00" in m.value for m in at.markdown)
    assert at.get("plotly_chart")


def test_incident_story_embeds_days_plan(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    df = core.load_decisions(dec)
    ep = df[df.issue_type == "room_turnaround"].episode.iloc[0]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.query_params["episode"] = ep
    at.run()
    at.switch_page("views/incidents.py")
    at.run()
    assert not V.errors(at)
    heads = [m.value for m in at.markdown]
    i = next(k for k, v in enumerate(heads) if "The day's plan" in v)
    j = next(k for k, v in enumerate(heads) if "How we'll know" in v)
    assert i < j and at.get("plotly_chart")
