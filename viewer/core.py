"""Viewer logic without Streamlit: load decisions, episodes, evidence series, approvals, checks.

Pure functions so they can be unit-tested (tests/test_viewer.py). The viewer is a read-only
presentation of a decisions file: nothing here feeds back into the pipeline.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from plato40.clock import LOCAL_TZ

ROOT = Path(__file__).resolve().parents[1]
INTERVAL = pd.Timedelta(minutes=15)
PRIO_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

SIMULATED = "SIMULATED"
PROPOSED = "PROPOSED — not delivered"
MOCK = "SIMULATION — mock executor, nothing is executed"

BADGES = {  # autonomy -> (label, colour)
    "autonomous": ("autonomous", "#2e7d32"),
    "human_approval": ("approval", "#ef6c00"),
    "approval_and_consent": ("approval + consent", "#c62828"),
}


# ---------------------------------------------------------------------------- decisions
def load_decisions(path: str | Path) -> pd.DataFrame:
    """decisions.jsonl -> DataFrame with parsed `t` and filled `episode`/`autonomy`."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["event_id", "timestamp_utc", "affected_assets", "issue_type", "action",
                                   "priority", "reason", "requires_human_approval"])
    for col in ("episode_id", "autonomy", "agent", "requires_guest_consent", "evidence", "message",
                "simulated_effect", "verification_plan", "expected_outcome", "resource_id",
                "scheduled_for_utc", "confidence", "guest_request_ids", "dependencies", "approval_note"):
        if col not in df.columns:
            df[col] = None
    df["t"] = pd.to_datetime(df["timestamp_utc"], utc=True, format="ISO8601")
    df["episode"] = df["episode_id"].where(df["episode_id"].notna(), df["event_id"])
    df["requires_guest_consent"] = df["requires_guest_consent"].fillna(False).astype(bool)
    df["requires_human_approval"] = df["requires_human_approval"].fillna(False).astype(bool)
    df["autonomy"] = [a if isinstance(a, str) and a else _level(r, c)
                      for a, r, c in zip(df["autonomy"], df["requires_human_approval"], df["requires_guest_consent"])]
    df["agent"] = df["agent"].fillna("")
    for col in ("evidence", "affected_assets", "guest_request_ids", "dependencies"):
        df[col] = [x if isinstance(x, list) else [] for x in df[col]]
    return df.sort_values(["t", "event_id"]).reset_index(drop=True)


def _level(approval: bool, consent: bool) -> str:
    return "approval_and_consent" if consent else ("human_approval" if approval else "autonomous")


def badge(autonomy: str) -> tuple[str, str]:
    return BADGES.get(autonomy, (str(autonomy), "#616161"))


def episodes(df: pd.DataFrame, min_bar: pd.Timedelta | None = None) -> pd.DataFrame:
    """One row per episode: span, issue type, assets, size, strongest autonomy level, best priority."""
    cols = ["episode", "issue_type", "start", "end", "bar_end", "decisions", "assets", "priority",
            "autonomy", "agent", "first_action", "label"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = []
    rank = {"autonomous": 0, "human_approval": 1, "approval_and_consent": 2}
    for ep, g in df.groupby("episode", sort=False):
        assets = list(dict.fromkeys(a for lst in g.affected_assets for a in lst))
        prio = min(g.priority, key=lambda p: PRIO_RANK.get(p, 9))
        out.append({
            "episode": ep, "issue_type": g.issue_type.iloc[0], "start": g.t.min(), "end": g.t.max(),
            "decisions": len(g), "assets": assets, "priority": prio,
            "autonomy": max(g.autonomy, key=lambda a: rank.get(a, 0)), "agent": g.agent.iloc[0],
            "first_action": g.action.iloc[0],
        })
    ep = pd.DataFrame(out).sort_values(["start", "episode"]).reset_index(drop=True)
    min_bar = min_bar or pd.Timedelta(hours=2)
    ep["bar_end"] = ep[["end"]].assign(m=ep.start + min_bar).max(axis=1)
    ep["label"] = [f"{e} · {i} · {', '.join(a[:3])}{'…' if len(a) > 3 else ''} · {s:%Y-%m-%d %H:%M}Z"
                   for e, i, a, s in zip(ep.episode, ep.issue_type, ep.assets, ep.start)]
    return ep[cols]


def summary(df: pd.DataFrame, approvals: dict | None = None) -> dict:
    approvals = approvals or {}
    appr = df[df.requires_human_approval]
    return {
        "decisions": len(df),
        "episodes": int(df.episode.nunique()) if len(df) else 0,
        "needs_approval": len(appr),
        "approvals_pending": int(sum(e not in approvals for e in appr.event_id)),
        "consent_needed": int(df.requires_guest_consent.sum()),
        "by_issue": df.issue_type.value_counts().to_dict(),
        "by_autonomy": df.autonomy.value_counts().to_dict(),
        "by_agent": df.agent.replace("", "(unknown)").value_counts().to_dict(),
    }


def fmt_times(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return f"{t.tz_convert('UTC'):%Y-%m-%d %H:%M}Z · {t.tz_convert(LOCAL_TZ):%a %d %b %H:%M} local"


def message_label(msg) -> str | None:
    """Guest message as shown in the UI - always marked as proposed, never as delivered."""
    if not msg:
        return None
    if isinstance(msg, str):
        msg = {"text": msg}
    lang = msg.get("language") or "?"
    src = msg.get("source") or "template"
    return f"[{PROPOSED}] ({lang}, {src}) {msg.get('text', '')}"


def simulated_label(value) -> str | None:
    if value in (None, "", [], {}):
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if text.upper().startswith(SIMULATED) else f"{SIMULATED}: {text}"


def approval_note(row) -> str:
    note = row.get("approval_note") if hasattr(row, "get") else None
    if isinstance(note, str) and note:
        return note
    assets = ", ".join(row["affected_assets"])
    extra = " Guest consent needed (not granted)." if row["requires_guest_consent"] else ""
    return f"Approve {row['action']} for {assets}? {row['reason']}{extra}"


# ---------------------------------------------------------------------------- evidence
_EV = re.compile(r"^(?P<file>[\w.\-]+\.(?:csv|jsonl|json))(?::(?P<asset>[A-Za-z0-9_\-]*))?(?::(?P<rest>.*))?$")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z?")
_METRIC = re.compile(r"^([a-z][a-z0-9_]*)\b")


@dataclass
class Evidence:
    raw: str
    file: str | None = None
    asset: str | None = None
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None
    metric: str | None = None
    detail: str = ""


def parse_evidence(s: str) -> Evidence:
    """`<file>:<asset>:<iso_start>..<iso_end>[:detail]` (tolerant to free text after the asset)."""
    m = _EV.match(str(s).strip())
    if not m:
        return Evidence(raw=str(s), detail=str(s))
    rest = m.group("rest") or ""
    ts = [pd.Timestamp(x if x.endswith("Z") else x + "Z") for x in _TS.findall(rest)]
    mm = _METRIC.match(rest)
    metric = mm.group(1) if mm and not _TS.match(rest) else None
    return Evidence(raw=str(s), file=m.group("file"), asset=m.group("asset") or None,
                    start=min(ts) if ts else None, end=max(ts) if ts else None, metric=metric, detail=rest)


@dataclass
class Chart:
    title: str
    series: dict[str, pd.Series]
    unit: str = ""
    gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)
    kind: str = "line"   # line | bar


def gap_intervals(s: pd.Series, start=None, end=None, min_len: pd.Timedelta = pd.Timedelta(minutes=30)):
    """Stretches without valid readings on the 15-min grid (missing = unknown, shown shaded)."""
    if s is None or len(s) == 0:
        return []
    start = start if start is not None else s.index.min()
    end = end if end is not None else s.index.max()
    grid = pd.date_range(start.floor("15min"), end.ceil("15min"), freq=INTERVAL)
    valid = s.dropna()
    valid = valid[~valid.index.duplicated()]
    ok = pd.Series(grid.isin(valid.index), index=grid)
    out, run = [], None
    for t, v in ok.items():
        if not v and run is None:
            run = t
        elif v and run is not None:
            if t - run >= min_len:
                out.append((run, t))
            run = None
    if run is not None and grid[-1] + INTERVAL - run >= min_len:
        out.append((run, grid[-1] + INTERVAL))
    return out


class SeriesStore:
    """Full-period series per asset, computed once per dataset and sliced per chart."""

    def __init__(self, data):
        self.d = data
        self.rooms = set(data.room_ids)
        eq = data.equipment
        self.eq_metrics = {a: set(g.metric) for a, g in eq.groupby("asset_id")}

    # cached full series ------------------------------------------------------
    @lru_cache(maxsize=256)
    def room_water(self, room: str) -> pd.Series:
        w = self.d.room_water[self.d.room_water.room_id == room]
        return w.set_index("timestamp_utc")["rate_l_per_interval"].sort_index()

    @lru_cache(maxsize=4)
    def main_water(self) -> pd.Series:
        return self.d.water_main.set_index("timestamp_utc")["rate_l_per_interval"].sort_index()

    @lru_cache(maxsize=4)
    def main_minus_rooms(self) -> pd.Series:
        rooms = self.d.room_water.groupby("timestamp_utc").rate_l_per_interval.sum(min_count=1)
        return (self.main_water() - rooms).dropna()

    @lru_cache(maxsize=256)
    def telemetry(self, room: str) -> pd.DataFrame:
        return self.d.telemetry[self.d.telemetry.room_id == room].set_index("timestamp_utc").sort_index()

    @lru_cache(maxsize=8)
    def equipment(self, asset: str) -> pd.DataFrame:
        w = self.d.equipment_wide(asset)
        if {"heat_output_kw", "electric_kw"} <= set(w.columns):
            w["cop"] = w.heat_output_kw / w.electric_kw.where(w.electric_kw > 0)
        return w

    @lru_cache(maxsize=2)
    def logistics(self) -> pd.DataFrame:
        return self.d.logistics_wide()

    @lru_cache(maxsize=2)
    def energy(self) -> pd.DataFrame:
        return self.d.energy.set_index("timestamp_utc").sort_index()

    @lru_cache(maxsize=2)
    def weather(self) -> pd.Series:
        return self.d.weather.set_index("timestamp_utc")["outdoor_c"].sort_index()

    @lru_cache(maxsize=2)
    def arrivals_per_day(self) -> pd.Series:
        b = self.d.bookings[self.d.bookings.status == "confirmed"]
        day = b.arrival_utc.dt.tz_convert(LOCAL_TZ).dt.normalize()
        return b.groupby(day).size().rename("arrivals")

    @lru_cache(maxsize=8)
    def floor_reporting(self, floor: str) -> pd.Series:
        tel = self.d.telemetry
        f = tel[tel.room_id.str[1] == floor]
        return f.dropna(subset=["temperature_c"]).groupby("timestamp_utc").room_id.nunique()

    # charts ------------------------------------------------------------------
    def charts_for(self, file: str | None, asset: str | None, issue_type: str = "", metric: str | None = None,
                   start=None, end=None) -> list[Chart]:
        start, end = self._window(start, end)
        sl = lambda s: s[(s.index >= start) & (s.index <= end)]  # noqa: E731
        a = asset or ""
        room = a[3:] if a.startswith("WM_R") else a
        if file == "water_main.csv" or a == "WM_MAIN" or (a == "BUILDING" and issue_type == "water_leak"):
            main, gap = sl(self.main_water()), sl(self.main_minus_rooms())
            return [Chart("WM_MAIN rate and main − Σ rooms (gap)", {"WM_MAIN": main, "main − Σ rooms": gap},
                          "L/15 min", gap_intervals(main, start, end))]
        if room in self.rooms and (file == "room_water.csv" or (file is None and issue_type == "water_leak")
                                   or a.startswith("WM_")):
            s = sl(self.room_water(room))
            return [Chart(f"{room} room water rate", {f"{room} L/15 min": s}, "L/15 min",
                          gap_intervals(s, start, end))]
        if room in self.rooms:
            tel = sl(self.telemetry(room))
            cols = [metric] if metric in tel.columns and metric not in ("temperature_c", "setpoint_c") else \
                ["temperature_c", "setpoint_c"]
            cols = [c for c in cols if c in tel.columns and pd.api.types.is_numeric_dtype(tel[c])] or \
                ["temperature_c", "setpoint_c"]
            ser = {c: tel[c] for c in cols}
            return [Chart(f"{room} {' / '.join(cols)}", ser, "", gap_intervals(tel[cols[0]], start, end))]
        if a in self.eq_metrics or file == "equipment.csv":
            want = "filter_dp_pa" if issue_type == "ventilation_maintenance" else "heat_output_kw"
            if a not in self.eq_metrics:
                a = next((k for k, m in self.eq_metrics.items() if want in m), next(iter(self.eq_metrics), ""))
            eq = sl(self.equipment(a))
            if "filter_dp_pa" in eq.columns:
                return [Chart(f"{a} filter Δp", {"filter_dp_pa": eq.get("filter_dp_pa")}, "Pa",
                              gap_intervals(eq.get("filter_dp_pa"), start, end)),
                        Chart(f"{a} airflow", {"airflow_m3h": eq.get("airflow_m3h")}, "m³/h",
                              gap_intervals(eq.get("airflow_m3h"), start, end))]
            ser = {"COP (heat/electric)": eq.get("cop"), "outdoor °C": sl(self.weather())}
            return [Chart(f"{a} COP vs outdoor temperature", ser, "", gap_intervals(eq.get("cop"), start, end)),
                    Chart(f"{a} supply temperature", {"supply_temp_c": eq.get("supply_temp_c")}, "°C")]
        if a.startswith(("WASTE_", "VEND")) or file == "logistics.csv":
            lg = sl(self.logistics())
            cols = [c for c in lg.columns if c.startswith(a + ".")] if a else list(lg.columns)
            return [Chart(f"{a or 'logistics'}", {c: lg[c] for c in cols})]
        if a.startswith("GW_F"):
            s = sl(self.floor_reporting(a[-1]))
            return [Chart(f"Rooms reporting on floor {a[-1]}", {"rooms reporting": s}, "rooms",
                          gap_intervals(s, start, end))]
        if file == "energy.csv" or a in ("BAT1", "PV1") or issue_type == "energy_optimization":
            en = sl(self.energy())
            return [Chart("Energy", {c: en[c] for c in ("building_load_kw", "pv_kw", "battery_soc_kwh")
                                     if c in en.columns}, "kW / kWh")]
        if file == "weather.csv":
            return [Chart("Outdoor temperature", {"outdoor_c": sl(self.weather())}, "°C")]
        if a == "BUILDING" or issue_type in ("occupancy_surge", "room_turnaround") or file == "bookings.csv":
            arr = self.arrivals_per_day()
            arr = arr[(arr.index >= start.tz_convert(LOCAL_TZ).normalize()) & (arr.index <= end)]
            return [Chart("Confirmed arrivals per local day", {"arrivals": arr}, "rooms", kind="bar")]
        return []

    def _window(self, start, end):
        lo, hi = self.d.start, self.d.end
        start = max(lo, pd.Timestamp(start)) if start is not None else lo
        end = min(hi, pd.Timestamp(end)) if end is not None else hi
        return start, end

    def charts_for_decisions(self, rows: pd.DataFrame, max_charts: int = 3,
                             pad_before=pd.Timedelta(hours=24), pad_after=pd.Timedelta(hours=24)) -> list[Chart]:
        """Charts of the series referenced by the decisions' evidence (fallback: their assets)."""
        if rows.empty:
            return []
        t0, t1 = rows.t.min(), rows.t.max()
        refs = []
        for _, r in rows.iterrows():
            for ev in map(parse_evidence, r.evidence):
                if ev.file:
                    refs.append((ev.file, ev.asset, ev.metric, ev.start, r.issue_type))
        if not refs:
            for _, r in rows.iterrows():
                for a in r.affected_assets:
                    refs.append((None, a, None, None, r.issue_type))
        seen, charts = set(), []
        for file, asset, metric, ev_start, issue in refs:
            key = (file, asset if asset != "BUILDING" else issue, metric)
            if key in seen:
                continue
            seen.add(key)
            s = min(t0, ev_start) if ev_start is not None else t0
            for c in self.charts_for(file, asset, issue, metric, s - pad_before, t1 + pad_after):
                if any(v is not None and len(v) for v in c.series.values()) and c.title not in {x.title for x in charts}:
                    charts.append(c)
            if len(charts) >= max_charts:
                break
        return charts[:max_charts]


def figure(chart: Chart, markers: list[tuple[pd.Timestamp, str]] | None = None, height: int = 260,
           shade: tuple[pd.Timestamp, pd.Timestamp] | None = None):
    """Plotly figure: series, shaded data gaps (unknown, not zero), vertical decision markers."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for name, s in chart.series.items():
        if s is None or not len(s):
            continue
        if chart.kind == "bar":
            fig.add_bar(x=s.index, y=s.values, name=name)
        else:
            fig.add_scatter(x=s.index, y=s.values, name=name, mode="lines", connectgaps=False)
    for a, b in chart.gaps:
        fig.add_vrect(x0=a, x1=b, fillcolor="#9e9e9e", opacity=0.25, line_width=0)
    if chart.gaps:
        fig.add_scatter(x=[None], y=[None], mode="markers", name="data gap (unknown)",
                        marker=dict(symbol="square", size=12, color="rgba(158,158,158,0.5)"))
    if shade is not None:
        fig.add_vrect(x0=shade[0], x1=shade[1], fillcolor="#fff59d", opacity=0.25, line_width=0)
    for t, label in markers or []:
        fig.add_shape(type="line", x0=t, x1=t, y0=0, y1=1, yref="paper", line=dict(color="#c62828", dash="dot"))
        fig.add_annotation(x=t, y=1, yref="paper", text=label, showarrow=False, textangle=-90,
                           xanchor="right", yanchor="top", font=dict(size=9, color="#c62828"))
    fig.update_layout(title=dict(text=chart.title, font=dict(size=13)), height=height,
                      margin=dict(l=10, r=10, t=35, b=10), yaxis_title=chart.unit,
                      legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    return fig


# ---------------------------------------------------------------------------- hero scenarios
CHART_FOR_ISSUE = {"water_leak": "room_water.csv", "ventilation_maintenance": "equipment.csv",
                   "heating_comfort": "equipment.csv", "occupancy_surge": "bookings.csv",
                   "room_turnaround": "bookings.csv"}

# Quickstart demo presets. Assets, issue type and window come from the referenced eval/ hand label
# (viewer-side display aid only; the pipeline never reads eval/).
HEROES = [
    {"key": "burst", "label": "QS-S02-R104-burst", "title": "Burst hose (S02 loud)",
     "say": "Room meter jumps to ~27 L/15 min while the BMS night-flow alarm comes hours later. Show detection "
            "time vs BMS, the inspection/shutoff escalation and its approval + consent flags."},
    {"key": "ahu", "label": "QS-S04-AHU1-filter", "title": "AHU filter cascade (S04)",
     "say": "filter Δp trend before the gap; the grey band is a DATA GAP (unknown, not repaired); technician on "
            "vacation -> contractor via approval."},
    {"key": "surge", "label": "QS-S05-event-weekend", "title": "Event weekend (S05)",
     "say": "Arrival peak visible days ahead from bookings; re-plan when the cleaner calls in sick; robots for "
            "floors, on-call only via approval; guest messages are proposed, not delivered."},
    {"key": "subtle", "label": "QS-S02-R305-toilet", "title": "Subtle toilet leak (S02 subtle)",
     "say": "~1 L/15 min, mostly at night when nobody uses water. Beat the guest complaint; main − Σ rooms is "
            "NOT conclusive here (open question in DATA.md)."},
]


def hero_presets(labels: list[dict]) -> list[dict]:
    """HEROES joined with their label: adds issue_type, assets, chart=(file, asset), label dict."""
    by_id = {x["id"]: x for x in labels}
    out = []
    for h in HEROES:
        lab = by_id.get(h["label"])
        if lab is None:
            continue
        assets = list(lab.get("assets", []))
        room = next((x for x in assets if not x.startswith("WM_")), assets[0] if assets else "BUILDING")
        out.append({**h, "issue_type": lab["issue_type"], "assets": assets, "lab": lab,
                    "chart": (CHART_FOR_ISSUE.get(lab["issue_type"]), room)})
    return out


def match_episodes(df: pd.DataFrame, issue_type: str, assets: list[str], start=None, end=None) -> list[str]:
    """Episodes of `issue_type` touching `assets` (BUILDING = any asset), optionally in [start, end]."""
    if df.empty:
        return []
    m = df.issue_type == issue_type
    if "BUILDING" not in assets:
        m &= df.affected_assets.map(lambda a: bool(set(a) & set(assets)))
    if start is not None:
        m &= df.t >= pd.Timestamp(start)
    if end is not None:
        m &= df.t <= pd.Timestamp(end)
    g = df[m].groupby("episode").t.min().sort_values()
    return list(g.index)


def load_labels(profile: str) -> list[dict]:
    """Hand labels exist only for quickstart (viewer-side dev aid; the pipeline never reads eval/)."""
    p = ROOT / "eval" / f"labels_{profile}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("incidents", [])


def hero_window(hero: dict, df: pd.DataFrame):
    """(start, end, label_id, episodes) for a preset from `hero_presets`."""
    lab = hero.get("lab")
    if lab:
        s, e = pd.Timestamp(lab["start_utc"]), pd.Timestamp(lab["end_utc"])
        return s, e, lab["id"], match_episodes(df, hero["issue_type"], hero["assets"], s, e)
    eps = match_episodes(df, hero["issue_type"], hero["assets"])
    if eps:
        g = df[df.episode == eps[0]]
        return g.t.min(), g.t.max(), None, eps
    return None, None, None, []


def auto_heroes(df: pd.DataFrame, n: int = 4) -> list[str]:
    """For datasets without presets: the n most severe non-routine episodes (one per issue type first)."""
    routine = {"data_quality", "energy_optimization", "logistics", "guest_request"}
    ep = episodes(df)
    if ep.empty:
        return []
    ep = ep.assign(r=ep.priority.map(PRIO_RANK).fillna(9), routine=ep.issue_type.isin(routine))
    ep = ep.sort_values(["routine", "r", "decisions"], ascending=[True, True, False])
    first = ep.drop_duplicates("issue_type")
    rest = ep[~ep.episode.isin(first.episode)]
    return list(pd.concat([first, rest]).episode[:n])


# ---------------------------------------------------------------------------- approvals (mock executor)
def load_approvals(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("decisions", {})
    except (json.JSONDecodeError, AttributeError):
        return {}


def record_approval(path: str | Path, event_id: str, status: str, decisions_file: str, note: str = "") -> dict:
    """Write an Approve/Reject click to a local JSON file. Simulation only - nothing is executed."""
    if status not in ("approved", "rejected"):
        raise ValueError("status must be approved|rejected")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = load_approvals(p)
    cur[event_id] = {"status": status, "recorded_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "decisions_file": str(decisions_file), "note": note}
    p.write_text(json.dumps({"_note": MOCK, "decisions": cur}, indent=1, ensure_ascii=False))
    return cur


# ---------------------------------------------------------------------------- side files + checks
def side_files(decisions: str | Path) -> dict[str, list[Path]]:
    p = Path(decisions)
    d = p.parent
    return {
        "dq": [x for x in [p.with_name(p.stem + "_dq.json")] if x.exists()],
        "ruled_out": sorted(d.glob("*_ruled_out.json")),
        "bms": sorted(x for x in d.glob("*bms*") if x.suffix in (".json", ".csv", ".md")),
    }


def load_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _validate_cmd(decisions, data_dir) -> list[str]:
    return [sys.executable, str(ROOT / "validate.py"), str(decisions), str(data_dir)]


def _evaluate_cmd(decisions, data_dir, profile: str) -> list[str]:
    labels = ROOT / "eval" / f"labels_{profile}.json"
    args = ["--labels", str(labels)] if profile and labels.exists() else ["--data", str(data_dir)]
    return [sys.executable, str(ROOT / "eval" / "evaluate.py"), str(decisions), *args]


def profile_of(data_dir: str | Path) -> str:
    m = Path(data_dir) / "manifest.json"
    try:
        return str(json.loads(m.read_text(encoding="utf-8")).get("profile", ""))
    except (OSError, ValueError):
        return ""


def run_validate(decisions: str | Path, data_dir: str | Path) -> tuple[bool, str]:
    r = subprocess.run(_validate_cmd(decisions, data_dir), cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def run_evaluate(decisions: str | Path, data_dir: str | Path, profile: str) -> str:
    r = subprocess.run(_evaluate_cmd(decisions, data_dir, profile), cwd=ROOT, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


class Checks:
    """validate.py + evaluate.py started in the background (in parallel with data loading)."""

    def __init__(self, decisions: str | Path, data_dir: str | Path):
        kw = dict(cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self._v = subprocess.Popen(_validate_cmd(decisions, data_dir), **kw)
        self._e = subprocess.Popen(_evaluate_cmd(decisions, data_dir, profile_of(data_dir)), **kw)
        self._res = None

    def result(self) -> tuple[bool, str, str]:
        """(validate ok, validate output, evaluate output); blocks until both finished."""
        if self._res is None:
            v = self._v.communicate()[0].strip()
            e = self._e.communicate()[0].strip()
            self._res = (self._v.returncode == 0, v, e)
        return self._res


def parse_eval_table(text: str) -> pd.DataFrame:
    """evaluate.py stdout (fixed-width table) -> DataFrame."""
    rows, on = [], False
    for line in text.splitlines():
        if line.startswith("incident "):
            on = True
            continue
        if on:
            if not line.strip():
                break
            rows.append({"incident": line[0:28].strip(), "scenario": line[29:39].strip(),
                         "detected": line[40:44].strip() == "Y", "latency_h": line[45:51].strip(),
                         "in_time": line[52:60].strip(), "decisions": line[61:65].strip(),
                         "first decision": line[67:].strip()})
    return pd.DataFrame(rows)


def bms_alarms(data) -> pd.DataFrame:
    ev = data.events
    b = ev[ev.type == "bms_alarm"]
    return b[["timestamp_utc", "asset_id", "alarm_code", "state"]].reset_index(drop=True)


def guest_requests(data, ids: list[str]) -> pd.DataFrame:
    gr = data.guest_requests
    return gr[gr.request_id.isin(ids)][["request_id", "timestamp_utc", "room_id", "language", "request_type", "text"]]


def nan_to_none(x):
    if isinstance(x, float) and np.isnan(x):
        return None
    return x
