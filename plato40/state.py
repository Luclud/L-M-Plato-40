"""Building state derived from the data: stays (occupancy), housekeeping, staff availability.

All lookups take a time t and only use information up to t.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .asof import AsOf
from .clock import at_local, local
from .io import Data

STAY_START = {"checkin", "checkin_early", "checkin_after_wait", "stay_in_progress"}
STAY_END = {"checkout", "checkout_early"}
ONCALL_ID = "ONCALL_1"
ONCALL_LOCAL = ("15:30", "22:00")  # building.json staff.notes.on_call_housekeeping


@dataclass
class Stay:
    room_id: str
    booking_id: str
    start: pd.Timestamp
    end: pd.Timestamp | None  # None = still in house at end of data


class State:
    def __init__(self, data: Data):
        self.d = data
        self.stays: dict[str, list[Stay]] = {r: [] for r in data.room_ids}
        self._build_stays()
        # housekeeping state per room: sorted timestamps + values (non-null only)
        tel = data.telemetry[["timestamp_utc", "room_id", "housekeeping_state", "presence"]]
        self._hk = {}
        for room, g in tel.dropna(subset=["housekeeping_state"]).groupby("room_id"):
            self._hk[room] = (g.timestamp_utc.tolist(), g.housekeeping_state.tolist())  # tolist: no per-item iteration

    # ---- occupancy ---------------------------------------------------------
    def _build_stays(self) -> None:
        ev = self.d.events
        ev = ev[ev.type.isin(STAY_START | STAY_END) & ev.room_id.notna()]
        open_: dict[str, Stay] = {}
        for row in ev.itertuples(index=False):
            if row.type in STAY_START:
                if row.room_id in open_:  # implicit end (e.g. reassignment without checkout)
                    s = open_.pop(row.room_id)
                    s.end = row.timestamp_utc
                s = Stay(row.room_id, row.booking_id, row.timestamp_utc, None)
                open_[row.room_id] = s
                self.stays.setdefault(row.room_id, []).append(s)
            elif row.room_id in open_:
                open_.pop(row.room_id).end = row.timestamp_utc

    def stay_at(self, room_id: str, t: pd.Timestamp) -> Stay | None:
        """Stay in progress at t, as known at t (a checkout after t is not known yet)."""
        for s in reversed(self.stays.get(room_id, [])):
            if s.start <= t:
                return s if (s.end is None or s.end > t) else None
        return None

    def occupied(self, room_id: str, t: pd.Timestamp) -> bool:
        return self.stay_at(room_id, t) is not None

    # ---- housekeeping --------------------------------------------------------
    def housekeeping(self, room_id: str, t: pd.Timestamp) -> str | None:
        ts, vals = self._hk.get(room_id, ([], []))
        i = bisect_right(ts, t)
        return vals[i - 1] if i else None

    def can_release(self, room_id: str, t: pd.Timestamp, blocked: set[str] | None = None) -> tuple[bool, str]:
        """building.json constraints.room_release_requires: vacant, clean, not_blocked, inspection passed."""
        if self.occupied(room_id, t):
            return False, "room occupied"
        hk = self.housekeeping(room_id, t)
        if hk != "clean":
            return False, f"housekeeping_state={hk}"
        if blocked and room_id in blocked:
            return False, "room blocked"
        return True, "vacant and clean"

    # ---- staff / robots -------------------------------------------------------
    def shift_at(self, resource_id: str, t: pd.Timestamp, known_at: pd.Timestamp | None = None):
        """Roster row covering t for resource (status as known at known_at, default t)."""
        r = AsOf(self.d, known_at if known_at is not None else t).roster()
        # as-of rosters have the same rows in the same order as data.roster (only 'status' differs):
        # find the first covering row via a per-resource index instead of filtering the whole frame
        idx = self._shift_index().get(resource_id)
        if idx is None:
            return None
        pos, st, en = idx
        tt = np.datetime64(pd.Timestamp(t).value, "ns")   # UTC instant
        hit = ((st <= tt) & (en > tt)).nonzero()[0]
        return None if len(hit) == 0 else r.iloc[pos[hit[0]]]

    def _shift_index(self) -> dict:
        """resource_id -> (row positions ascending, start, end) of data.roster."""
        src = self.d.roster
        cache = self.__dict__.get("_shift_idx")
        if cache is None or cache[0] is not src or cache[1] != len(src):
            rid = src.resource_id.to_numpy(dtype=object)
            st = src.start_utc.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
            en = src.end_utc.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
            idx = {}
            for res in pd.unique(rid):
                if isinstance(res, str):
                    pos = (rid == res).nonzero()[0]
                    idx[res] = (pos, st[pos], en[pos])
            cache = self._shift_idx = (src, len(src), idx)
        return cache[2]

    def available(self, resource_id: str, t: pd.Timestamp, known_at: pd.Timestamp | None = None) -> tuple[bool, str]:
        """Can this resource be scheduled at time t (judged with knowledge at known_at)?"""
        if resource_id == ONCALL_ID:
            lo, hi = at_local(t, ONCALL_LOCAL[0]), at_local(t, ONCALL_LOCAL[1])
            ok = lo <= t < hi
            return ok, ("on-call window" if ok else f"outside on-call {ONCALL_LOCAL[0]}-{ONCALL_LOCAL[1]} local")
        shift = self.shift_at(resource_id, t, known_at)
        if shift is None:
            return False, "no shift at that time"
        if shift.status != "scheduled":
            return False, f"shift {shift.shift_id} status={shift.status}"
        return True, f"shift {shift.shift_id}"

    def available_resources(self, role: str, t: pd.Timestamp, known_at: pd.Timestamp | None = None) -> list[str]:
        return [rid for rid, rl in self.d.resources.items() if rl == role and self.available(rid, t, known_at)[0]]


def is_local_between(t: pd.Timestamp, start_hhmm: str, end_hhmm: str) -> bool:
    lt = local(t)
    hm = lt.hour * 60 + lt.minute
    a = int(start_hhmm[:2]) * 60 + int(start_hhmm[3:])
    b = int(end_hhmm[:2]) * 60 + int(end_hhmm[3:])
    return a <= hm < b
