"""Load and clean a PLATO-40 data directory.

Rules applied here (documented in README):
- All timestamps parsed as UTC. Local time (Europe/Vienna) only via plato40.clock.
- Empty field = missing (NaN). Never filled with zero.
- Exact duplicate keys are dropped (first kept) and logged.
- Physically implausible sensor values are set to NaN and logged.
- Cumulative water meters are turned into consumption between consecutive valid
  readings; large negative steps are meter resets (consumption unknown at that
  step), small negative steps are meter jitter (clipped to 0).

Every cleaning action is recorded in Data.dq so detectors and the report can
explain what was changed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

INTERVAL = pd.Timedelta(minutes=15)

# Plausibility ranges. Values outside are treated as sensor faults -> NaN.
PLAUSIBLE = {
    "temperature_c": (-5.0, 45.0),
    "setpoint_c": (5.0, 30.0),
    "humidity_pct": (0.0, 100.0),
    "co2_ppm": (250.0, 10000.0),
}
METER_RESET_L = 50.0  # negative step larger than this = meter exchange / restart

TS_COLUMNS = {
    "bookings.csv": ["created_utc", "arrival_utc", "departure_utc"],
    "access_log.csv": ["timestamp_utc"],
    "room_telemetry.csv": ["timestamp_utc"],
    "room_water.csv": ["timestamp_utc"],
    "water_main.csv": ["timestamp_utc"],
    "energy.csv": ["timestamp_utc"],
    "weather.csv": ["timestamp_utc"],
    "weather_forecast.csv": ["issued_utc", "target_utc"],
    "tariff.csv": ["timestamp_utc", "published_utc"],
    "equipment.csv": ["timestamp_utc"],
    "logistics.csv": ["timestamp_utc"],
    "staff_roster.csv": ["start_utc", "end_utc", "status_known_utc"],
    "work_orders.csv": ["created_utc", "scheduled_utc", "completed_utc"],
    "guest_requests.csv": ["timestamp_utc", "response_due_utc"],
}


@dataclass
class DQIssue:
    """One data-quality finding made while loading (not a decision)."""

    kind: str          # duplicate | implausible | meter_reset | meter_jitter | missing
    source: str        # file name
    asset: str         # room / asset id or "*"
    count: int
    first_utc: str | None = None
    detail: str = ""


@dataclass
class Data:
    path: Path
    manifest: dict
    building: dict
    bookings: pd.DataFrame
    access: pd.DataFrame
    telemetry: pd.DataFrame
    room_water: pd.DataFrame       # valid readings only, with use_l / span_intervals / reset
    water_main: pd.DataFrame       # same columns as room_water (room_id = WM_MAIN)
    energy: pd.DataFrame
    weather: pd.DataFrame
    forecast: pd.DataFrame
    tariff: pd.DataFrame
    equipment: pd.DataFrame        # long format, value_num numeric / value raw
    logistics: pd.DataFrame        # long format
    roster: pd.DataFrame
    work_orders: pd.DataFrame
    guest_requests: pd.DataFrame
    events: pd.DataFrame
    dq: list[DQIssue] = field(default_factory=list)

    # ---- convenience -------------------------------------------------------
    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.manifest["start_utc"])

    @property
    def end(self) -> pd.Timestamp:
        return pd.Timestamp(self.manifest["end_utc"])

    @property
    def rooms(self) -> list[dict]:
        return self.building["building"]["rooms"]

    @property
    def room_ids(self) -> list[str]:
        return [r["id"] for r in self.rooms]

    @property
    def resources(self) -> dict[str, str]:
        """resource_id -> role"""
        return {r["resource_id"]: r["role"] for r in self.building["staff"]["resources"]}

    @property
    def valid_asset_ids(self) -> set[str]:
        b = self.building["building"]
        ids = set(self.room_ids)
        ids |= {a["id"] for a in b["assets"]}
        ids |= {g["id"] for g in b.get("gateways", [])}
        ids |= {r["water_meter_id"] for r in self.rooms}
        ids |= set(self.resources)
        ids |= {"BUILDING", "MAIN_ENTRANCE"}
        return ids

    def equipment_wide(self, asset_id: str) -> pd.DataFrame:
        """Wide table for one asset (HP1 / AHU1): index timestamp, one column per metric."""
        e = self.equipment[self.equipment.asset_id == asset_id]
        num = e.pivot_table(index="timestamp_utc", columns="metric", values="value_num", aggfunc="first")
        if "status" in set(e.metric):
            st = e[e.metric == "status"].drop_duplicates("timestamp_utc").set_index("timestamp_utc")["value"]
            num["status"] = st
        return num.sort_index()

    def logistics_wide(self) -> pd.DataFrame:
        """Index timestamp, columns '<asset>.<metric>' (e.g. WASTE_MAIN.fill_pct)."""
        lg = self.logistics.assign(col=self.logistics.asset_id + "." + self.logistics.metric)
        return lg.pivot_table(index="timestamp_utc", columns="col", values="value", aggfunc="first").sort_index()


# ---------------------------------------------------------------------------
def _read_csv(path: Path, name: str) -> pd.DataFrame:
    df = pd.read_csv(path / name)
    for col in TS_COLUMNS.get(name, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    return df


def _dedupe(df: pd.DataFrame, keys: list[str], source: str, dq: list[DQIssue]) -> pd.DataFrame:
    dup = df.duplicated(keys, keep="first")
    if dup.any():
        first = df.loc[dup, keys[0]].min()
        dq.append(DQIssue("duplicate", source, "*", int(dup.sum()), str(first), f"keys={keys}"))
    return df[~dup].sort_values(keys).reset_index(drop=True)


def _clean_telemetry(df: pd.DataFrame, dq: list[DQIssue]) -> pd.DataFrame:
    df = _dedupe(df, ["timestamp_utc", "room_id"], "room_telemetry.csv", dq)
    df["implausible"] = ""  # comma-separated metrics that were removed in this row
    for col, (lo, hi) in PLAUSIBLE.items():
        bad = df[col].notna() & ((df[col] < lo) | (df[col] > hi))
        if bad.any():
            for room, g in df[bad].groupby("room_id"):
                dq.append(DQIssue("implausible", "room_telemetry.csv", room, len(g),
                                  str(g.timestamp_utc.min()), f"{col} outside [{lo},{hi}]"))
            df.loc[bad, "implausible"] = df.loc[bad, "implausible"] + col + "=" + df.loc[bad, col].astype(str) + ";"
            df.loc[bad, col] = np.nan
    return df


def _meter_to_use(df: pd.DataFrame, id_col: str, source: str, dq: list[DQIssue]) -> pd.DataFrame:
    """Cumulative readings -> consumption between consecutive *valid* readings."""
    df = _dedupe(df, ["timestamp_utc", id_col], source, dq)
    missing = df.meter_total_l.isna()
    if missing.any():
        for asset, g in df[missing].groupby(id_col):
            dq.append(DQIssue("missing", source, asset, len(g), str(g.timestamp_utc.min())))
    df = df[~missing].sort_values([id_col, "timestamp_utc"]).copy()
    g = df.groupby(id_col)
    df["use_l"] = g.meter_total_l.diff()
    df["span_intervals"] = (g.timestamp_utc.diff() / INTERVAL).round()
    df["reset"] = df.use_l < -METER_RESET_L
    jitter = (df.use_l < 0) & ~df.reset
    for asset, gg in df[df.reset].groupby(id_col):
        dq.append(DQIssue("meter_reset", source, asset, len(gg), str(gg.timestamp_utc.min()),
                          "counter restarted (exchange?) - consumption unknown for that step"))
    if jitter.any():
        dq.append(DQIssue("meter_jitter", source, "*", int(jitter.sum()),
                          str(df.loc[jitter, "timestamp_utc"].min()), "small negative steps clipped to 0"))
    df.loc[df.reset, "use_l"] = np.nan
    df.loc[jitter, "use_l"] = 0.0
    # litres per 15-min interval (spreads consumption over gaps)
    df["rate_l_per_interval"] = df.use_l / df.span_intervals.clip(lower=1)
    return df.reset_index(drop=True)


def _read_events(path: Path) -> pd.DataFrame:
    rows = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines() if line.strip()]
    ev = pd.DataFrame(rows)
    ev["timestamp_utc"] = pd.to_datetime(ev["timestamp_utc"], utc=True, format="ISO8601")
    return ev.sort_values(["timestamp_utc", "event_id"]).reset_index(drop=True)


def load(data_dir: str | Path) -> Data:
    path = Path(data_dir)
    if not (path / "manifest.json").exists():
        # allow pointing at a parent folder that contains exactly one dataset dir
        subs = [p for p in path.iterdir() if (p / "manifest.json").exists()] if path.is_dir() else []
        if len(subs) != 1:
            raise FileNotFoundError(f"{path} is not a PLATO-40 data directory (no manifest.json)")
        path = subs[0]

    dq: list[DQIssue] = []
    manifest = json.loads((path / "manifest.json").read_text())
    building = json.loads((path / "building.json").read_text())

    telemetry = _clean_telemetry(_read_csv(path, "room_telemetry.csv"), dq)
    room_water = _meter_to_use(_read_csv(path, "room_water.csv"), "room_id", "room_water.csv", dq)
    wm = _read_csv(path, "water_main.csv").assign(room_id="WM_MAIN")
    water_main = _meter_to_use(wm, "room_id", "water_main.csv", dq)

    equipment = _dedupe(_read_csv(path, "equipment.csv"), ["timestamp_utc", "asset_id", "metric"], "equipment.csv", dq)
    equipment["value_num"] = pd.to_numeric(equipment["value"], errors="coerce")

    data = Data(
        path=path,
        manifest=manifest,
        building=building,
        bookings=_read_csv(path, "bookings.csv"),
        access=_read_csv(path, "access_log.csv").sort_values("timestamp_utc").reset_index(drop=True),
        telemetry=telemetry,
        room_water=room_water,
        water_main=water_main,
        energy=_dedupe(_read_csv(path, "energy.csv"), ["timestamp_utc"], "energy.csv", dq),
        weather=_dedupe(_read_csv(path, "weather.csv"), ["timestamp_utc"], "weather.csv", dq),
        forecast=_read_csv(path, "weather_forecast.csv").sort_values(["issued_utc", "target_utc"]).reset_index(drop=True),
        tariff=_dedupe(_read_csv(path, "tariff.csv"), ["timestamp_utc"], "tariff.csv", dq),
        equipment=equipment,
        logistics=_dedupe(_read_csv(path, "logistics.csv"), ["timestamp_utc", "asset_id", "metric"], "logistics.csv", dq),
        roster=_read_csv(path, "staff_roster.csv").sort_values("start_utc").reset_index(drop=True),
        work_orders=_read_csv(path, "work_orders.csv").sort_values("created_utc").reset_index(drop=True),
        guest_requests=_read_csv(path, "guest_requests.csv").sort_values("timestamp_utc").reset_index(drop=True),
        events=_read_events(path),
        dq=dq,
    )
    return data
