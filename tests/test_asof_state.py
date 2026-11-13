"""Time-of-availability and state helpers on quickstart."""
import pandas as pd

from plato40.asof import AsOf

T = pd.Timestamp


def test_sick_shift_unknown_before_report(qs, qs_state):
    # CLEANER_1 sick on 8 Jan 06:00Z, reported 05:15Z
    shift_time = T("2026-01-08T07:00:00Z")
    ok_before, _ = qs_state.available("CLEANER_1", shift_time, known_at=T("2026-01-08T05:00:00Z"))
    ok_after, why = qs_state.available("CLEANER_1", shift_time, known_at=T("2026-01-08T05:30:00Z"))
    assert ok_before is True
    assert ok_after is False and "sick" in why


def test_tariff_only_after_publication(qs):
    t = T("2026-01-10T11:00:00Z")  # before 13:00 local publication
    known = AsOf(qs, t).tariff()
    assert known.timestamp_utc.max() < T("2026-01-11T00:00:00Z")
    later = AsOf(qs, T("2026-01-10T12:30:00Z")).tariff()
    assert later.timestamp_utc.max() >= T("2026-01-11T00:00:00Z")


def test_forecast_latest_issue_only(qs):
    fc = AsOf(qs, T("2026-01-10T12:00:00Z")).forecast()
    assert fc.issued_utc.nunique() == 1
    assert fc.issued_utc.iloc[0] <= T("2026-01-10T12:00:00Z")


def test_oncall_window(qs_state):
    assert qs_state.available("ONCALL_1", T("2026-01-10T16:00:00Z"))[0]   # 17:00 local
    assert not qs_state.available("ONCALL_1", T("2026-01-10T08:00:00Z"))[0]


def test_release_requires_clean(qs_state):
    ok, why = qs_state.can_release("R102", T("2026-01-06T08:45:00Z"))  # just checked out, dirty
    assert not ok


def test_missing_is_not_zero(qs):
    tel = qs.telemetry
    assert tel.temperature_c.isna().any()          # gaps preserved
    assert (tel.temperature_c.dropna() <= 45).all()  # implausible removed
    assert not (qs.room_water.use_l < 0).any()     # jitter clipped, resets NaN
