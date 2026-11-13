"""Building "digital twin" at one 15-minute moment, built only from pipeline inputs/outputs (read-only, no Streamlit).

Everything is evaluated *as known at t*: telemetry at t, roster statuses with ``status_known_utc <= t``, decisions with
``timestamp_utc <= t``. Missing telemetry is "unknown", never "vacant clean"."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from plato40.clock import LOCAL_TZ
from viewer import labels as L

STEP = pd.Timedelta(minutes=15)
STALE = pd.Timedelta(minutes=30)
STATES = ["occupied", "vacant_clean", "needs_cleaning", "cleaning", "unknown"]
_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_STRICT = {"autonomous": 0, "human_approval": 1, "approval_and_consent": 2}
PLANT = {"HP1": ["HP1"], "AHU1": ["AHU1"], "WM_MAIN": ["WM_MAIN"], "PV1": ["PV1", "BAT1"]}


def floor_t(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("15min")


def local_day(t) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC [start, end) of the local (Europe/Vienna) day containing t."""
    d = pd.Timestamp(t).tz_convert(LOCAL_TZ).normalize()
    return d.tz_convert("UTC"), (d + pd.Timedelta(days=1)).tz_localize(None).tz_localize(LOCAL_TZ).tz_convert("UTC")


def day_steps(day) -> list[pd.Timestamp]:
    """15-min UTC steps of a local calendar day (``datetime.date``), DST-aware."""
    s = pd.Timestamp(day).tz_localize(LOCAL_TZ)
    e = (pd.Timestamp(day) + pd.Timedelta(days=1)).tz_localize(LOCAL_TZ)
    return list(pd.date_range(s.tz_convert("UTC"), e.tz_convert("UTC"), freq="15min", inclusive="left"))


def room_state(hk, presence) -> str:
    if not isinstance(hk, str) or not hk:
        return "unknown"
    if hk == "in_progress":
        return "cleaning"
    if hk == "dirty":
        return "needs_cleaning"
    if presence is None or (isinstance(presence, float) and np.isnan(presence)):
        return "unknown"
    return "occupied" if presence >= 0.5 else "vacant_clean"


def episode_spans(df: pd.DataFrame) -> pd.DataFrame:
    """One row per incident episode: first/last decision time, assets, best priority, strictest autonomy."""
    cols = ["episode", "issue_type", "start", "end", "assets", "priority", "autonomy"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    inc = df[df.issue_type.isin(L.INCIDENT_TYPES)]
    out = []
    for ep, g in inc.groupby("episode", sort=False):
        out.append({"episode": ep, "issue_type": g.issue_type.iloc[0], "start": g.t.min(), "end": g.t.max(),
                    "assets": list(dict.fromkeys(a for x in g.affected_assets for a in x)),
                    "priority": min(g.priority, key=lambda p: _SEV.get(p, 9)),
                    "autonomy": max(g.autonomy, key=lambda a: _STRICT.get(a, 0))})
    return pd.DataFrame(out, columns=cols)


def default_moment(df: pd.DataFrame, fallback) -> pd.Timestamp:
    """Start of the highest-priority incident (earliest among equals); dataset start if there are none."""
    sp = episode_spans(df)
    if sp.empty:
        return floor_t(fallback)
    sp = sp.assign(_s=sp.priority.map(_SEV).fillna(9)).sort_values(["_s", "start"])
    return floor_t(sp.start.iloc[0])


def open_at(spans: pd.DataFrame, t) -> pd.DataFrame:
    """Incident episodes open at t (first <= t <= last decision, 15-min resolution)."""
    if spans.empty:
        return spans
    t = floor_t(t)
    m = (spans.start.dt.floor("15min") <= t) & (spans.end >= t)
    return spans[m].assign(_s=lambda x: x.priority.map(_SEV)).sort_values(["_s", "start"]).drop(columns="_s")


def pending_at(df: pd.DataFrame, episodes: list[str], t, approvals: dict | None) -> dict[str, int]:
    """Approval decisions already made by t in the given episodes without a recorded (mock) answer."""
    if df.empty or not episodes:
        return {}
    a = df[df.episode.isin(episodes) & df.requires_human_approval & (df.t <= t)]
    a = a[~a.event_id.isin(set(approvals or {}))]
    return a.groupby("episode").size().to_dict()


def planned_cleanings(df: pd.DataFrame, t, day_end) -> dict[str, tuple[pd.Timestamp, str | None]]:
    """room -> (planned UTC time, resource) for schedule_cleaning decisions known at t, planned later that day."""
    if df.empty:
        return {}
    c = df[(df.action == "schedule_cleaning") & (df.t <= t)]
    if c.empty:
        return {}
    when = pd.to_datetime(c.scheduled_for_utc, utc=True, errors="coerce")
    c = c.assign(planned=when)[(when > t) & (when < day_end)].sort_values("t")
    out = {}
    for r in c.itertuples():
        for a in r.affected_assets:
            if a.startswith("R") and a[1:].isdigit():
                out[a] = (r.planned, r.resource_id if isinstance(r.resource_id, str) else None)
    return out


def staff_at(roster: pd.DataFrame, t, day_start, day_end) -> pd.DataFrame:
    """Shifts of the local day as known at t. A later sick/out-of-service notice is invisible before it is known."""
    cols = ["resource_id", "role", "start", "end", "status", "known"]
    if roster is None or roster.empty:
        return pd.DataFrame(columns=cols)
    r = roster[(roster.start_utc < day_end) & (roster.end_utc > day_start)]
    rows = []
    for x in r.sort_values(["resource_id", "start_utc"]).itertuples():
        known = x.status_known_utc <= t
        if not known and x.status == "scheduled":
            continue                    # shift not yet published at t
        status = x.status if known else "scheduled"
        rows.append({"resource_id": x.resource_id, "role": x.role, "start": x.start_utc, "end": x.end_utc,
                     "status": status, "known": x.status_known_utc if known and status != "scheduled" else None})
    return pd.DataFrame(rows, columns=cols)


def today_counts(df: pd.DataFrame, day_start, t) -> dict[str, int]:
    if df.empty:
        return {"guest_request": 0, "energy_optimization": 0, "logistics": 0, "cleanings": 0}
    d = df[(df.t >= day_start) & (df.t <= t)]
    return {"guest_request": int((d.issue_type == "guest_request").sum()),
            "energy_optimization": int((d.issue_type == "energy_optimization").sum()),
            "logistics": int((d.issue_type == "logistics").sum()),
            "cleanings": int((d.action == "schedule_cleaning").sum())}


class Twin:
    """Time-indexed views of one dataset; build once per dataset (cheap per-step lookups)."""

    def __init__(self, data):
        tel = data.telemetry.sort_values(["timestamp_utc", "room_id"])
        self.rooms = list(data.room_ids)
        self._tel = tel[["timestamp_utc", "room_id", "presence", "housekeeping_state", "temperature_c"]]
        self._tel_ts = tel.timestamp_utc.values
        self.roster = data.roster
        self.start, self.end = pd.Timestamp(data.start), pd.Timestamp(data.end)
        seen = {}
        eq = data.equipment.dropna(subset=["value"])
        for a in ("HP1", "AHU1"):
            seen[a] = np.sort(eq[eq.asset_id == a].timestamp_utc.unique())
        wm = data.water_main.dropna(subset=[c for c in data.water_main.columns if c != "timestamp_utc"], how="all")
        seen["WM_MAIN"] = np.sort(wm.timestamp_utc.unique())
        en = data.energy.dropna(subset=["pv_kw"]) if "pv_kw" in data.energy else data.energy
        seen["PV1"] = np.sort(en.timestamp_utc.unique())
        self._seen = {k: pd.DatetimeIndex(v).tz_localize("UTC") if pd.DatetimeIndex(v).tz is None
                      else pd.DatetimeIndex(v) for k, v in seen.items()}

    def rooms_at(self, t) -> pd.DataFrame:
        """room_id, state, temperature at t (rooms without a row at t are unknown)."""
        t = floor_t(t)
        tv = np.datetime64(t.tz_convert("UTC").tz_localize(None), "ns")
        ts = self._tel_ts.astype("datetime64[ns]") if self._tel_ts.dtype != "datetime64[ns]" else self._tel_ts
        i, j = np.searchsorted(ts, tv, "left"), np.searchsorted(ts, tv, "right")
        now = self._tel.iloc[i:j].set_index("room_id")
        out = []
        for r in self.rooms:
            if r in now.index:
                x = now.loc[r]
                out.append((r, room_state(x.housekeeping_state, x.presence), x.temperature_c))
            else:
                out.append((r, "unknown", np.nan))
        return pd.DataFrame(out, columns=["room_id", "state", "temperature_c"])

    def plant_at(self, t, open_eps: pd.DataFrame) -> dict[str, str]:
        """Plant tile -> ok | incident | no_data (no measurement in the last 30 min)."""
        t = floor_t(t)
        busy = set(a for x in (open_eps.assets if len(open_eps) else []) for a in x)
        out = {}
        for tile, assets in PLANT.items():
            if busy & set(assets):
                out[tile] = "incident"
                continue
            idx = self._seen.get(tile)
            k = idx.searchsorted(t, "right") if idx is not None else 0
            out[tile] = "ok" if k and t - idx[k - 1] <= STALE else "no_data"
        return out

    @lru_cache(maxsize=16)
    def day_telemetry(self, day_start: pd.Timestamp, day_end: pd.Timestamp) -> pd.DataFrame:
        m = (self._tel.timestamp_utc >= day_start) & (self._tel.timestamp_utc < day_end)
        return self._tel[m]
