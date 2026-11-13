"""T29 Alarms vs Steward: building-alarm flood vs Steward incidents, computed from the loaded run (pure, no Streamlit).

Alarm episodes come from `analysis.bms_compare` (raise/clear pairing); detection per labelled incident reuses
`bms_compare.compare_incidents`. Labels are a dev aid: quickstart hand labels, else work-order weak labels.
"""
from __future__ import annotations

import pandas as pd

from analysis import bms_compare as B
from viewer import core

TZ = "Europe/Vienna"
INCIDENT_TYPES = ("room_turnaround", "water_leak", "heating_comfort", "ventilation_maintenance", "occupancy_surge")
COMM_LOSS, TEMP_LOW = "SENSOR_COMM_LOSS", "ROOM_TEMP_LOW"


def alarm_events(data) -> pd.DataFrame:
    ev = data.events
    if "alarm_code" not in ev:
        return pd.DataFrame(columns=["timestamp_utc", "asset_id", "alarm_code", "state"])
    return core.bms_alarms(data)


def events_by_code(data) -> dict[str, int]:
    """Alarm events (raised + cleared) per alarm code, as in events.jsonl."""
    return {k: int(v) for k, v in alarm_events(data).alarm_code.value_counts().sort_index().items()}


def alarm_episodes(data) -> pd.DataFrame:
    return B.alarm_episodes(data.events, data.end)


def _week(t: pd.Series) -> pd.Series:
    loc = t.dt.tz_convert(TZ).dt.tz_localize(None)
    return (loc - pd.to_timedelta(loc.dt.weekday, unit="D")).dt.normalize()


def alarms_by_week(data) -> pd.DataFrame:
    """Alarm episodes raised per local week (Monday) and alarm code: columns week, alarm_code, n."""
    a = alarm_episodes(data)
    if a.empty:
        return pd.DataFrame(columns=["week", "alarm_code", "n"])
    return a.assign(week=_week(a.raised_utc)).groupby(["week", "alarm_code"]).size().rename("n").reset_index()


def incident_episodes(df: pd.DataFrame) -> pd.DataFrame:
    ep = core.episodes(df)
    return ep[ep.issue_type.isin(INCIDENT_TYPES)].reset_index(drop=True)


def incidents_by_week(df: pd.DataFrame) -> pd.DataFrame:
    ep = incident_episodes(df)
    if ep.empty:
        return pd.DataFrame(columns=["week", "n"])
    return ep.assign(week=_week(ep.start)).groupby("week").size().rename("n").reset_index()


def labels_for(data_dir) -> tuple[list[dict], str]:
    """(incidents, source): hand labels for the dataset profile if they exist, else work-order weak labels."""
    lab = core.load_labels(core.profile_of(data_dir))
    if lab:
        return lab, "hand"
    from eval.evaluate import weak_labels  # dev aid (same labels as eval/evaluate.py --data)
    return weak_labels(str(data_dir)), "weak"


def _rows(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return [{"timestamp_utc": t.isoformat(), "issue_type": it, "affected_assets": list(a), "action": ac}
            for t, it, a, ac in zip(df.t, df.issue_type, df.affected_assets, df.action)]


def incident_detection(data, df: pd.DataFrame, labels: list[dict]) -> pd.DataFrame:
    """One row per labelled incident: caught by an alarm / by Steward, lead time (h, > 0 = Steward first)."""
    cols = ["id", "scenario", "issue_type", "assets", "start_utc", "ours_first_utc", "bms_first_utc",
            "bms_first_code", "lead_vs_bms_h", "bms_missed", "we_missed"]
    if not labels:
        return pd.DataFrame(columns=cols)
    res = B.compare_incidents(labels, _rows(df), alarm_episodes(data), data, B.Assets(data))
    return pd.DataFrame(res)[cols]


def coverage(data, df: pd.DataFrame, labels: list[dict]) -> pd.DataFrame:
    """Per incident type: labelled, caught by alarms, caught by Steward, median lead time vs the first alarm."""
    det = incident_detection(data, df, labels)
    cols = ["issue_type", "labelled", "by_alarms", "by_steward", "median_lead_h"]
    if det.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for it, g in det.groupby("issue_type", sort=False):
        lead = g.lead_vs_bms_h.dropna()
        out.append({"issue_type": it, "labelled": len(g), "by_alarms": int((~g.bms_missed).sum()),
                    "by_steward": int((~g.we_missed).sum()),
                    "median_lead_h": round(float(lead.median()), 1) if len(lead) else None})
    order = {k: i for i, k in enumerate(INCIDENT_TYPES)}
    return pd.DataFrame(out, columns=cols).sort_values("issue_type", key=lambda s: s.map(order)).reset_index(drop=True)


def _missing_grid(data) -> pd.DataFrame:
    """15-min grid x room: True where the room sent no valid temperature."""
    t = data.telemetry
    rooms = [r["id"] for r in data.rooms]
    grid = pd.date_range(data.start, data.end, freq="15min", inclusive="left")
    if t.empty:
        return pd.DataFrame(True, index=grid, columns=rooms)
    v = t.dropna(subset=["temperature_c"]).drop_duplicates(["timestamp_utc", "room_id"])
    have = v.pivot(index="timestamp_utc", columns="room_id", values="temperature_c").reindex(index=grid, columns=rooms)
    return have.isna()


def gateway_outages(data) -> pd.DataFrame:
    """Intervals where every room behind one gateway sent nothing (from the telemetry; method in Technical details)."""
    miss = _missing_grid(data)
    out = []
    for gw, rooms in sorted(B.Assets(data).gw_rooms.items()):
        rooms = [r for r in rooms if r in miss.columns]
        if len(rooms) < 2:
            continue
        down = miss[rooms].all(axis=1)
        for s, e in core.gap_intervals(down.where(~down), data.start, data.end - pd.Timedelta(minutes=15),
                                       min_len=pd.Timedelta(minutes=30)):
            out.append({"gateway": gw, "start": s, "end": e, "rooms": sorted(rooms)})
    return pd.DataFrame(out, columns=["gateway", "start", "end", "rooms"])


def noise(data) -> dict:
    """Why the alarms are noisy: communication-loss alarms during gateway outages, cold-room alarms with nobody there."""
    a = alarm_episodes(data)
    comm = a[a.alarm_code == COMM_LOSS]
    gw = gateway_outages(data)
    during = 0
    for r in comm.itertuples(index=False):
        hit = gw[(gw.start <= r.raised_utc) & (r.raised_utc < gw.end)]
        during += bool(len(hit) and any(r.asset_id in x or r.asset_id in set(gw.gateway) for x in hit.rooms))
    low = a[a.alarm_code == TEMP_LOW]
    vacant = known = 0
    if len(low) and not data.telemetry.empty:
        p = data.telemetry.drop_duplicates(["timestamp_utc", "room_id"]).set_index(["room_id", "timestamp_utc"]).presence
        for r in low.itertuples(index=False):
            v = p.get((r.asset_id, r.raised_utc.floor("15min")))
            if v is not None and pd.notna(v):
                known += 1
                vacant += int(v == 0)
    return {"comm_loss": int(len(comm)), "comm_loss_in_outage": int(during), "gateway_outages": int(len(gw)),
            "temp_low": int(len(low)), "temp_low_presence_known": int(known), "temp_low_vacant": int(vacant),
            "short_alarms": int(((a.duration_h < B.TRANSIENT_H) & ~a.open_at_end.astype(bool)).sum()) if len(a) else 0,
            "alarm_episodes": int(len(a))}
