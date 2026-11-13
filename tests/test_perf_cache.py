"""Caches added for runtime must not change any result (perf-planner): cached as-of views and planner
helpers are compared with the plain (uncached) computations at many times t."""
import pandas as pd

from plato40.asof import AsOf
from plato40.planning import housekeeping as hk


def _times(data, n=40):
    return pd.date_range(data.start, data.end, periods=n)


def test_roster_cache_matches_uncached(qs):
    for t in _times(qs):
        r = qs.roster.copy()
        unknown = r.status_known_utc.notna() & (r.status_known_utc > t)
        r.loc[unknown, "status"] = "scheduled"
        got = AsOf(qs, t).roster()
        pd.testing.assert_frame_equal(got, r)
        assert AsOf(qs, t).roster() is got   # same view object while nothing new is known


def test_bookings_cache_matches_uncached(qs):
    for t in _times(qs):
        b = qs.bookings
        pd.testing.assert_frame_equal(AsOf(qs, t).bookings(), b[b.created_utc <= t])


def test_resource_windows_same_with_and_without_shared_rows(qs):
    resources = sorted(qs.resources)
    for t in _times(qs, 20):
        r = AsOf(qs, t).roster()
        plain = r.copy()
        plain.attrs = {}   # forces the plain pandas path
        lo, hi = t - pd.Timedelta(days=1), t + pd.Timedelta(days=2)
        assert hk.resource_windows(r, resources, lo, hi) == hk.resource_windows(plain, resources, lo, hi)


def test_learn_durations_matches_pandas_reference(qs):
    rec = hk.cleaning_records(qs.events)
    rob = hk.robot_records(qs.events)
    area_of = {r["id"]: int(r.get("area_m2") or 0) for r in qs.rooms}
    for t in _times(qs):
        for min_samples in (1, 3, hk.MIN_SAMPLES):
            got = hk.learn_durations(rec, qs.rooms, t, robot=rob, min_samples=min_samples)
            sub = rec[rec.end <= t].assign(area=lambda d: d.room_id.map(area_of))
            pooled, by_area = dict(hk.PRIOR_MINUTES), {}
            for flag in (False, True):
                s = sub[sub.robot_pre_clean == flag]
                if len(s) >= min_samples:
                    pooled[flag] = hk._ceil_minutes(s.minutes.median())
                for a, g in s.groupby("area"):
                    if len(g) >= min_samples:
                        by_area[(int(a), flag)] = hk._ceil_minutes(g.minutes.median())
            assert got.pooled == pooled and dict(got.by_area) == by_area and got.samples == len(sub)


def test_arrivals_on_cache_returns_same_rows(qs):
    for t in _times(qs, 10):
        bk = AsOf(qs, t).bookings()
        day = hk.local_day(t)
        a1 = hk.arrivals_on(bk, day)
        b = bk[bk.status != "cancelled"]
        lo, hi = hk.day_bounds(day)
        b = b[(b.arrival_utc >= lo) & (b.arrival_utc < hi)]
        pd.testing.assert_frame_equal(a1, b.sort_values("arrival_utc").drop_duplicates("room_id"))
        assert hk.arrivals_on(bk, day) is a1


def test_bookings_prefix_view_and_unsorted_fallback(qs):
    from plato40.asof import prefix_of
    for t in _times(qs):
        bk = AsOf(qs, t).bookings()
        pre = prefix_of(bk)
        if qs.bookings.created_utc.is_monotonic_increasing:
            assert pre is not None and pre[0] is qs.bookings and len(bk) == pre[1]
    # an unsorted table must still give the filtered rows (no prefix shortcut)
    import copy
    d2 = copy.copy(qs)
    d2.__dict__.pop("_asof_memo", None)
    d2.bookings = qs.bookings.iloc[::-1]
    for t in _times(qs, 10):
        got = AsOf(d2, t).bookings()
        assert prefix_of(got) is None
        pd.testing.assert_frame_equal(got, d2.bookings[d2.bookings.created_utc <= t])


def test_ceil_step_matches_pandas():
    import random
    rnd = random.Random(0)
    for tz in ("UTC", "Europe/Vienna", None):
        for unit in ("s", "us", "ns"):
            for _ in range(500):
                t = (pd.Timestamp("2026-01-01", tz=tz) + pd.Timedelta(seconds=rnd.randrange(0, 400 * 86400))).as_unit(unit)
                try:
                    want = t.ceil(hk.STEP)
                except ValueError:   # pandas cannot localise wall times at the DST switch
                    continue
                got = hk.ceil_step(t)
                assert got == want and got.unit == want.unit and str(got) == str(want)


def test_shift_at_matches_pandas_filter(qs_state, qs):
    for t in _times(qs, 30):
        r = AsOf(qs, t).roster()
        for rid in qs.resources:
            m = r[(r.resource_id == rid) & (r.start_utc <= t) & (r.end_utc > t)]
            got = qs_state.shift_at(rid, t)
            if m.empty:
                assert got is None
            else:
                pd.testing.assert_series_equal(got, m.iloc[0])


def test_departure_lookup_and_arrivals_match_pandas(qs):
    for t in _times(qs, 15):
        bk = AsOf(qs, t).bookings()
        dep = bk.set_index("booking_id").departure_utc
        dep = dep[~dep.index.duplicated()]
        get = hk._departure_lookup(bk)
        for bid in list(qs.bookings.booking_id.unique()[:200]) + ["nope"]:
            assert get(bid) == dep.get(bid) or (get(bid) is None and dep.get(bid) is None)
        for k in range(-1, 3):
            day = hk.local_day(t) + pd.Timedelta(days=k).to_pytimedelta()
            b = bk[bk.status != "cancelled"]
            lo, hi = hk.day_bounds(day)
            b = b[(b.arrival_utc >= lo) & (b.arrival_utc < hi)]
            pd.testing.assert_frame_equal(hk.arrivals_on(bk, day),
                                          b.sort_values("arrival_utc").drop_duplicates("room_id"))


def test_resource_windows_matches_bruteforce(qs):
    resources = sorted(qs.resources)
    for t in _times(qs, 25):
        r = AsOf(qs, t).roster()
        for lo, hi in ((t, t + pd.Timedelta(hours=8)), (t - pd.Timedelta(days=1), t + pd.Timedelta(days=2))):
            want = {x: [] for x in resources}
            s = r[r.status == "scheduled"].sort_values("start_utc", kind="stable")
            for rid, a, b in zip(s.resource_id, s.start_utc, s.end_utc):
                if rid in want and a < hi and b > lo:
                    want[rid].append((max(a, lo), min(b, hi)))
            assert hk.resource_windows(r, resources, lo, hi) == want


def _windows_bruteforce(r, resources, lo, hi):
    want = {x: [] for x in resources}
    s = r[r.status == "scheduled"].sort_values("start_utc", kind="stable")
    for rid, a, b in zip(s.resource_id, s.start_utc, s.end_utc):
        if rid in want and a < hi and b > lo:
            want[rid].append((max(a, lo), min(b, hi)))
    return want


def test_frames_derived_from_cached_roster_are_not_treated_as_views(qs):
    """Copies/filters of AsOf.roster() inherit pandas attrs; caches must not mistake them for the view."""
    resources = sorted(qs.resources)
    t = qs.start + pd.Timedelta(days=3)
    r = AsOf(qs, t).roster()
    hk.resource_windows(r, resources, t, t + pd.Timedelta(days=2))   # warm the caches for the real view
    # copy with one shift made much longer than any other
    c = r.copy()
    i = c.index[len(c) // 2]
    c.loc[i, "end_utc"] = c.loc[i, "start_utc"] + pd.Timedelta(days=3)
    lo = c.loc[i, "start_utc"] + pd.Timedelta(days=2)
    hi = lo + pd.Timedelta(hours=1)
    assert hk.resource_windows(c, resources, lo, hi) == _windows_bruteforce(c, resources, lo, hi)
    # subset keeping the first and last row (same first/last start as the view)
    sub = r[(r.index == r.index[0]) | (r.index == r.index[-1]) | (r.index % 3 == 0)]
    lo, hi = t, t + pd.Timedelta(days=2)
    assert hk.resource_windows(sub, resources, lo, hi) == _windows_bruteforce(sub, resources, lo, hi)


def test_learn_durations_ignores_records_without_end(qs):
    rec = hk.cleaning_records(qs.events)
    t = qs.end
    bad = rec.copy()
    bad.loc[bad.index[:3], "end"] = pd.NaT
    bad.loc[bad.index[:3], "minutes"] = float("nan")
    got = hk.learn_durations(bad, qs.rooms, t, min_samples=1)
    ref = hk.learn_durations(rec.iloc[3:].reset_index(drop=True), qs.rooms, t, min_samples=1)
    assert got.samples == int((bad.end <= t).sum()) and got.pooled == ref.pooled and got.by_area == ref.by_area
