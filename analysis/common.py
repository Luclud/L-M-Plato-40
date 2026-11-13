"""Shared helpers for the insight analyses: loading, occupancy from stays, formatting."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from plato40 import io
from plato40.clock import LOCAL_TZ
from plato40.state import State

WINTER = (12, 1, 2)       # local months
SUMMER = (6, 7, 8)
COMFORT_LO, COMFORT_HI = 20.0, 23.0
HOT_C = 26.0
MIN_N = 500               # minimum telemetry intervals for a share to be reported


@dataclass
class Insight:
    insight_id: str
    target: str                    # PLATO | CLARA
    topic: str
    hypothesis: str                # e.g. "P1 Floor 1 runs colder in winter"
    status: str                    # confirmed | qualified | rejected | insufficient data
    finding: str
    evidence: list[str]
    method: str
    recommendation: str
    expected_benefit: str
    uncertainty: str
    confidence: float
    numbers: dict = field(default_factory=dict)

    def record(self) -> dict:
        """insights.jsonl contract fields first, then our extras."""
        return {
            "insight_id": self.insight_id, "target": self.target, "topic": self.topic,
            "finding": self.finding, "evidence": self.evidence, "recommendation": self.recommendation,
            "expected_benefit": self.expected_benefit, "confidence": round(float(self.confidence), 2),
            "status": self.status, "hypothesis": self.hypothesis, "method": self.method,
            "uncertainty": self.uncertainty, "numbers": self.numbers,
        }


@dataclass
class Ctx:
    data: io.Data
    state: State
    rooms: pd.DataFrame            # index room id: floor, area_m2, orientation, has_bathtub, gateway
    tel: pd.DataFrame              # telemetry + occ, floor, area_m2, orientation, month, date (local)

    @property
    def period(self) -> str:
        return f"{self.data.start:%Y-%m-%d}..{self.data.end:%Y-%m-%d}"


def room_gateways(data: io.Data) -> dict[str, str]:
    gw = {}
    for s in data.building["building"].get("sensors", []):
        if s.get("metric") == "temperature_c" and s.get("gateway"):
            gw[s["asset_id"]] = s["gateway"]
    return gw


def occupancy_mask(state: State, room_ids: pd.Series, ts: pd.Series, end: pd.Timestamp) -> np.ndarray:
    """True where a stay (from events.jsonl via State) covers (room, ts)."""
    out = np.zeros(len(ts), bool)
    rid = room_ids.to_numpy()
    tsv = ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    for room, stays in state.stays.items():
        idx = np.flatnonzero(rid == room)
        if not len(idx) or not stays:
            continue
        t = tsv[idx]
        starts = np.array([s.start.tz_convert("UTC").tz_localize(None).to_datetime64() for s in stays],
                          dtype="datetime64[ns]")
        ends = np.array([(s.end if s.end is not None else end).tz_convert("UTC").tz_localize(None).to_datetime64()
                         for s in stays], dtype="datetime64[ns]")
        order = np.argsort(starts)
        starts, ends = starts[order], ends[order]
        k = np.searchsorted(starts, t, side="right") - 1
        ok = k >= 0
        occ = np.zeros(len(t), bool)
        occ[ok] = t[ok] < ends[k[ok]]
        out[idx] = occ
    return out


def build(data_dir) -> Ctx:
    data = io.load(data_dir)
    state = State(data)
    rooms = pd.DataFrame(data.rooms).set_index("id")
    rooms["gateway"] = pd.Series(room_gateways(data))
    tel = data.telemetry[["timestamp_utc", "room_id", "temperature_c", "setpoint_c", "window_open"]].copy()
    tel["occ"] = occupancy_mask(state, tel.room_id, tel.timestamp_utc, data.end)
    tel = tel.join(rooms[["floor", "area_m2", "orientation"]], on="room_id")
    lt = tel.timestamp_utc.dt.tz_convert(LOCAL_TZ)
    tel["month"] = lt.dt.month
    tel["date"] = lt.dt.date
    return Ctx(data, state, rooms, tel)


def share(s: pd.Series, cond) -> float:
    s = s.dropna()
    return float(cond(s).mean()) if len(s) else float("nan")


def pct(x: float, nd: int = 1) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{100 * x:.{nd}f} %"


def months_label(months) -> str:
    return "local months " + ",".join(str(m) for m in months)


def insufficient(iid, target, topic, hypothesis, why, evidence) -> Insight:
    return Insight(iid, target, topic, hypothesis, "insufficient data",
                   f"PLATO-40: hypothesis could not be tested on this dataset ({why}).",
                   evidence, "n/a", "Re-run on a dataset that covers the relevant season/events.",
                   "none claimed", "untested", 0.1)
