#!/usr/bin/env python3
"""T12: BMS alarms vs our episodes + look-alike report.

    python analysis/bms_compare.py out/qs.jsonl data/quickstart --labels <hand labels json>
    python analysis/bms_compare.py out/dev.jsonl data/dev          # incidents from corrective work orders

Writes `<out>.json` (default: bms_compare.json next to the decisions file) and a markdown summary
`<out>.md` that the viewer and insights.md can embed.

- BMS raise/clear events are paired into alarm episodes per (asset, alarm_code); a raise without a clear
  stays open until the end of the dataset.
- An alarm episode is *matched* when one of our episodes has a compatible issue type, a related asset
  (room <-> its water meter, WM_MAIN <-> every room meter, gateway <-> its rooms, BUILDING <-> all) and
  overlaps in time (our first..last decision, +/- slack). Unmatched alarms are likely false/benign
  look-alikes and are classified (vacant room, transient < 1 h, other).
- Labelled incidents (a labels json given with --labels, else weak labels: corrective work orders on plant /
  water assets, with a look-back window): first detection ours vs BMS vs the first related work order /
  guest report.

Post-hoc analysis (it may look at the whole period); it never feeds back into decisions and the pipeline never
imports it. No label file is read unless passed explicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plato40 import io  # noqa: E402

ROUTINE = {"data_quality", "energy_optimization", "logistics", "guest_request"}
CODE_ISSUES = {  # BMS alarm code -> our issue types that address the same problem
    "WATER_NIGHT_FLOW": {"water_leak"},
    "ROOM_TEMP_LOW": {"heating_comfort"},
    "ROOM_TEMP_HIGH": {"heating_comfort", "energy_optimization"},
    "FILTER_DP_HIGH": {"ventilation_maintenance"},
    "AIRFLOW_LOW": {"ventilation_maintenance"},
    "SENSOR_COMM_LOSS": {"data_quality"},
}
GUEST_TYPES = {  # guest request types that count as a human report of an incident type
    "water_leak": {"water_issue", "maintenance_issue"},
    "heating_comfort": {"temperature_cold", "temperature_hot"},
    "room_turnaround": {"waiting_for_room"},
}
TRANSIENT_H = 1.0
WO_LOOKBACK_D = 14
WATER_WORDS = r"leak|burst|hose|fill valve|continuous running|pipe"
ASSET_ISSUES = {"heat_pump": "heating_comfort", "air_handling_unit": "ventilation_maintenance", "water_meter": "water_leak"}


def compatible(code: str, issue_type: str) -> bool:
    return issue_type in CODE_ISSUES.get(code, {issue_type})  # unknown codes: any issue type


class Assets:
    """Asset relations from building.json (no hard-coded ids)."""

    def __init__(self, data: io.Data):
        b = data.building["building"]
        self.rooms = {r["id"] for r in b.get("rooms", [])}
        self.room_meter = {r["id"]: r.get("water_meter_id") or f"WM_{r['id']}" for r in b.get("rooms", [])}
        self.meter_room = {m: r for r, m in self.room_meter.items()}
        self.gw_rooms: dict[str, set] = defaultdict(set)
        for s in b.get("sensors", []):
            if s.get("gateway") and s.get("asset_id") in self.rooms:
                self.gw_rooms[s["gateway"]].add(s["asset_id"])
        self._cache: dict[str, frozenset] = {}

    def covers(self, a: str) -> frozenset:
        if a not in self._cache:
            if a == "BUILDING":
                s = {"*"}
            elif a == "WM_MAIN":
                s = {"WM_MAIN", *self.rooms}
            elif a in self.gw_rooms:
                s = {a, *self.gw_rooms[a]}
            elif a in self.meter_room:
                s = {self.meter_room[a]}
            else:
                s = {a}
            self._cache[a] = frozenset(s)
        return self._cache[a]

    def related(self, a: str, others) -> bool:
        ca = self.covers(a)
        for o in others:
            co = self.covers(o)
            if "*" in ca or "*" in co or ca & co:
                return True
        return False


def alarm_episodes(events: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    cols = ["asset_id", "alarm_code", "raised_utc", "cleared_utc", "open_at_end", "value", "duration_h"]
    if "alarm_code" not in events:
        return pd.DataFrame(columns=cols)
    a = events[events.type == "bms_alarm"].sort_values(["timestamp_utc", "event_id"])
    out = []
    for (asset, code), g in a.groupby(["asset_id", "alarm_code"], sort=True):
        cur = None
        for r in g.itertuples(index=False):
            if r.state == "raised" and cur is None:
                cur = {"asset_id": asset, "alarm_code": code, "raised_utc": r.timestamp_utc, "value": r.value}
            elif r.state == "cleared" and cur is not None:
                out.append({**cur, "cleared_utc": r.timestamp_utc, "open_at_end": False})
                cur = None
        if cur is not None:
            out.append({**cur, "cleared_utc": end, "open_at_end": True})
    df = pd.DataFrame(out, columns=[c for c in cols if c != "duration_h"])
    df["duration_h"] = (df.cleared_utc - df.raised_utc).dt.total_seconds() / 3600 if len(df) else []
    return df.sort_values(["raised_utc", "asset_id", "alarm_code"]).reset_index(drop=True)


def our_episodes(rows: list[dict]) -> pd.DataFrame:
    eps: dict[str, dict] = {}
    for r in rows:
        k = r.get("episode_id") or r["event_id"]
        t = pd.Timestamp(r["timestamp_utc"])
        e = eps.setdefault(k, {"episode_id": k, "issue_type": r.get("issue_type"), "agent": r.get("agent", ""),
                               "first_utc": t, "last_utc": t, "assets": set(), "decisions": 0,
                               "first_action": r.get("action")})
        if t < e["first_utc"]:
            e["first_utc"], e["first_action"] = t, r.get("action")
        e["last_utc"] = max(e["last_utc"], t)
        e["assets"].update(r.get("affected_assets", []))
        e["decisions"] += 1
    return pd.DataFrame(list(eps.values()),
                        columns=["episode_id", "issue_type", "agent", "first_utc", "last_utc", "assets", "decisions",
                                 "first_action"])


def _occupied(state, room: str, t: pd.Timestamp) -> bool | None:
    if state is None or room not in getattr(state, "stays", {}):
        return None
    return any(s.start <= t and (s.end is None or t < s.end) for s in state.stays[room])


def match(alarms: pd.DataFrame, eps: pd.DataFrame, assets: Assets, slack: pd.Timedelta, state=None) -> pd.DataFrame:
    alarms = alarms.copy()
    m_ep, lead, why = [], [], []
    ep_list = list(eps.itertuples(index=False))
    for a in alarms.itertuples(index=False):
        hits = [e for e in ep_list if compatible(a.alarm_code, e.issue_type)
                and e.first_utc - slack <= a.cleared_utc and a.raised_utc <= e.last_utc + slack
                and assets.related(a.asset_id, e.assets)]
        if hits:
            first = min(hits, key=lambda e: e.first_utc)
            m_ep.append(";".join(sorted(e.episode_id for e in hits)))
            lead.append((a.raised_utc - first.first_utc).total_seconds() / 3600)
            why.append("")
            continue
        m_ep.append("")
        lead.append(np.nan)
        comfort = "heating_comfort" in CODE_ISSUES.get(a.alarm_code, set())
        occ = _occupied(state, a.asset_id, a.raised_utc) if comfort and a.asset_id in assets.rooms else None
        if occ is False:
            why.append("vacant room")
        elif occ:
            why.append("occupied room, no episode")
        elif a.duration_h < TRANSIENT_H and not a.open_at_end:
            why.append(f"transient < {TRANSIENT_H:g} h")
        else:
            why.append("no matching episode")
    alarms["matched_episodes"] = m_ep
    alarms["lead_h"] = lead   # BMS raise - our first decision (> 0: we were earlier)
    alarms["unmatched_reason"] = why
    return alarms


def per_code(alarms: pd.DataFrame) -> list[dict]:
    out = []
    for code, g in alarms.groupby("alarm_code", sort=True):
        m = g[g.matched_episodes != ""]
        u = g[g.matched_episodes == ""]
        out.append({
            "alarm_code": code, "raised": int(len(g)), "matched": int(len(m)), "unmatched": int(len(u)),
            "unmatched_share": round(len(u) / len(g), 3) if len(g) else None,
            "unmatched_reasons": {k: int(v) for k, v in u.unmatched_reason.value_counts().sort_index().items()},
            "median_lead_h": round(float(m.lead_h.median()), 1) if len(m) else None,
            "ours_first": int((m.lead_h > 0).sum()),
            "median_duration_h": round(float(g.duration_h.median()), 2),
        })
    return out


def per_issue(eps: pd.DataFrame, alarms: pd.DataFrame) -> list[dict]:
    hit = set()
    for s in alarms.matched_episodes:
        hit.update(x for x in s.split(";") if x)
    out = []
    for it, g in eps.groupby("issue_type", sort=True):
        w = int(g.episode_id.isin(hit).sum())
        out.append({"issue_type": it, "episodes": int(len(g)), "with_bms_alarm": w, "bms_silent": int(len(g) - w),
                    "decisions": int(g.decisions.sum())})
    return out


def compare_incidents(incidents, rows, alarms, data: io.Data, assets: Assets) -> list[dict]:
    wo = data.work_orders
    wo = wo[wo.type.astype(str).str.lower() != "preventive"] if "type" in wo else wo
    gr = data.guest_requests
    out = []
    for inc in incidents:
        s, e = pd.Timestamp(inc["start_utc"]), pd.Timestamp(inc["end_utc"])
        ia = set(inc["assets"])
        any_asset = "BUILDING" in ia

        def direct(x) -> bool:  # human reports: same asset / room <-> its meter only (no WM_MAIN fan-out)
            return isinstance(x, str) and (x in ia or assets.room_meter.get(x) in ia or assets.meter_room.get(x) in ia)
        ours = sorted([(pd.Timestamp(r["timestamp_utc"]), r) for r in rows
                      if r.get("issue_type") == inc["issue_type"] and s <= pd.Timestamp(r["timestamp_utc"]) <= e
                      and (any_asset or ia & set(r.get("affected_assets", [])))], key=lambda x: x[0])
        ours_t = ours[0][0] if ours else None
        b = alarms[(alarms.raised_utc >= s) & (alarms.raised_utc <= e)]
        b = b[[compatible(c, inc["issue_type"]) and assets.related(x, ia) for c, x in zip(b.alarm_code, b.asset_id)]] \
            if len(b) else b
        bms_t = b.raised_utc.min() if len(b) else None
        w = wo[(wo.created_utc >= s) & (wo.created_utc <= e)]
        w = w[[direct(x) for x in w.asset_id]] if len(w) else w
        wo_t = w.created_utc.min() if len(w) else None
        g = gr[(gr.timestamp_utc >= s) & (gr.timestamp_utc <= e)
               & gr.request_type.isin(GUEST_TYPES.get(inc["issue_type"], set()))]
        g = g[[direct(x) for x in g.room_id]] if len(g) else g
        gr_t = g.timestamp_utc.min() if len(g) else None

        def lead(ref):
            return round((ref - ours_t).total_seconds() / 3600, 1) if (ours_t is not None and ref is not None) else None

        out.append({
            "id": inc["id"], "scenario": inc.get("scenario", ""), "issue_type": inc["issue_type"],
            "assets": sorted(ia), "start_utc": _iso(s), "ours_first_utc": _iso(ours_t),
            "ours_first_action": ours[0][1].get("action") if ours else None,
            "bms_first_utc": _iso(bms_t), "bms_first_code": b.sort_values("raised_utc").alarm_code.iloc[0] if len(b) else None,
            "work_order_utc": _iso(wo_t), "guest_report_utc": _iso(gr_t),
            "lead_vs_bms_h": lead(bms_t), "lead_vs_work_order_h": lead(wo_t), "lead_vs_guest_h": lead(gr_t),
            "bms_missed": bms_t is None, "we_missed": ours_t is None,
        })
    return out


def work_order_incidents(data: io.Data, lookback_d: int = WO_LOOKBACK_D) -> list[dict]:
    """Weak labels for any dataset: corrective work orders on plant (by asset type) or water problems (by text).
    Generic guest-reported defects carry no detectable signal and are skipped."""
    b = data.building["building"]
    types = {x["id"]: x.get("type", "") for x in b.get("assets", [])}
    rooms = {r["id"]: r.get("water_meter_id") or f"WM_{r['id']}" for r in b.get("rooms", [])}
    wo = data.work_orders
    if wo.empty:
        return []
    wo = wo[wo.type.astype(str).str.lower() == "corrective"] if "type" in wo else wo
    water = wo.summary.astype(str).str.contains(WATER_WORDS, case=False, regex=True)
    out, prev = [], {}
    for w, is_water in zip(wo.itertuples(index=False), water):
        a = str(w.asset_id)
        start = w.created_utc - pd.Timedelta(days=lookback_d)
        if a in prev:  # never reach back past the previous corrective work order on the same asset
            start = max(start, prev[a] + pd.Timedelta(minutes=15))
        prev[a] = w.created_utc
        issue = ASSET_ISSUES.get(types.get(a, "")) or ("water_leak" if is_water and (a in rooms or a == "BUILDING")
                                                       else None)
        if issue is None:
            continue
        assets = [a] + ([rooms[a]] if a in rooms else [])
        out.append({"id": f"WO-{w.work_order_id}", "scenario": f"WO {issue}", "issue_type": issue, "assets": assets,
                    "start_utc": str(start), "end_utc": str(w.created_utc),
                    "notes": str(w.summary)})
    return out


def lookalikes(decisions: Path) -> list[dict]:
    """Detector look-alike exports (`*_ruled_out.json` next to the decisions file), if any."""
    out = []
    for p in sorted(decisions.parent.glob("*_ruled_out.json")):
        try:
            items = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        kinds = defaultdict(int)
        for it in items if isinstance(items, list) else []:
            kinds[str(it.get("kind", "?")) if isinstance(it, dict) else "?"] += 1
        out.append({"file": p.name, "items": len(items) if isinstance(items, list) else 0, "by_kind": dict(kinds)})
    return out


def _iso(t) -> str | None:
    if t is None or (isinstance(t, float) and np.isnan(t)) or pd.isna(t):
        return None
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")


def _short(t: str | None) -> str:
    return "—" if not t else pd.Timestamp(t).strftime("%d %b %H:%MZ")


def _h(x) -> str:
    return "—" if x is None else f"{x:+.1f} h"


def to_markdown(res: dict) -> str:
    v = res["volume"]
    L = [f"## BMS alarms vs our episodes — PLATO-40 ({res['dataset']}, {res['period']})", "",
         "BMS = a clue, not ground truth. Matching: compatible issue type + related asset + time overlap "
         f"(±{res['params']['slack_h']:g} h). Lead = BMS (or work order / guest) time − our first decision; "
         "positive = we were earlier.", "",
         f"- Raw BMS events: **{v['bms_events']}** (raised + cleared) → **{v['bms_alarm_episodes']}** alarm episodes; "
         f"ours: **{v['our_episodes']}** episodes ({v['our_incident_episodes']} non-routine) from {v['our_decisions']} "
         f"decisions — {v['episode_reduction_pct']} % fewer episodes than BMS alarms.",
         f"- BMS alarms matched to one of our episodes: {v['bms_matched']}/{v['bms_alarm_episodes']}; "
         f"unmatched: {v['bms_unmatched']} (likely false/benign look-alikes, or a BMS-only finding — see reasons below).",
         f"- Our non-routine episodes with no BMS alarm at all (BMS silent): {v['our_incident_episodes_bms_silent']}"
         f"/{v['our_incident_episodes']}.", ""]
    if res["incidents"]:
        L += [f"### Labelled incidents ({res['labels_source']}) — first detection", "",
              "| incident | ours | BMS | work order | guest report | lead vs BMS | lead vs WO |",
              "|---|---|---|---|---|---|---|"]
        quiet = [i for i in res["incidents"] if i["we_missed"] and i["bms_missed"]]
        for i in res["incidents"]:
            if i in quiet:
                continue
            bms = _short(i["bms_first_utc"]) + (f" ({i['bms_first_code']})" if i["bms_first_code"] else "")
            L.append(f"| {i['id']} | {_short(i['ours_first_utc'])} | {bms if i['bms_first_utc'] else 'missed'} | "
                     f"{_short(i['work_order_utc'])} | {_short(i['guest_report_utc'])} | {_h(i['lead_vs_bms_h'])} | "
                     f"{_h(i['lead_vs_work_order_h'])} |")
        s = res["incident_summary"]
        if quiet:
            L += [f"| *{len(quiet)} more* | — | missed | | | | |"]
            L += ["", f"Neither we nor the BMS flagged {len(quiet)} labelled incident(s): "
                  + ", ".join(sorted({f"{i['scenario']} ×{sum(j['scenario'] == i['scenario'] for j in quiet)}" for i in quiet}))
                  + " (details in the json `incidents`)."]
        L += ["", f"Incidents: {s['n']} · we detected {s['ours_detected']} · BMS alarmed {s['bms_detected']} · "
              f"we were earlier than the BMS in {s['ours_earlier_than_bms']}/{s['both_detected']} where both fired · "
              f"BMS missed {s['bms_missed_ours_detected']} we caught.", ""]
    L += ["### Per BMS alarm code", "",
          "| alarm code | raised | matched | unmatched | unmatched reasons | median lead | ours first | median duration |",
          "|---|---:|---:|---:|---|---:|---:|---:|"]
    for c in res["per_code"]:
        reasons = ", ".join(f"{k} {n}" for k, n in c["unmatched_reasons"].items()) or "—"
        L.append(f"| {c['alarm_code']} | {c['raised']} | {c['matched']} | {c['unmatched']} | {reasons} | "
                 f"{_h(c['median_lead_h'])} | {c['ours_first']} | {c['median_duration_h']:.2f} h |")
    L += ["", "### Our episodes per issue type", "",
          "| issue type | episodes | with BMS alarm | BMS silent | decisions |", "|---|---:|---:|---:|---:|"]
    for r in res["per_issue"]:
        L.append(f"| {r['issue_type']} | {r['episodes']} | {r['with_bms_alarm']} | {r['bms_silent']} | {r['decisions']} |")
    if res["detector_lookalikes"]:
        L += ["", "### Look-alikes ruled out by our agents (no decision emitted)", ""]
        for x in res["detector_lookalikes"]:
            L.append(f"- `{x['file']}`: {x['items']} — " + ", ".join(f"{k} {n}" for k, n in x["by_kind"].items()))
    return "\n".join(L) + "\n"


def compare(decisions: Path, data_dir: Path, incidents: list[dict] | None = None, labels_source: str = "",
            slack_h: float = 6.0) -> dict:
    """incidents=None -> weak labels from work orders; [] -> no incident comparison."""
    data = io.load(data_dir)
    if incidents is None:
        incidents = work_order_incidents(data)
    try:
        from plato40.state import State
        state = State(data)
    except Exception:  # occupancy is only used to explain unmatched alarms
        state = None
    rows = [json.loads(x) for x in Path(decisions).read_text(encoding="utf-8").splitlines() if x.strip()]
    assets = Assets(data)
    alarms = match(alarm_episodes(data.events, data.end), our_episodes(rows), assets, pd.Timedelta(hours=slack_h), state)
    eps = our_episodes(rows)
    inc_eps = eps[~eps.issue_type.isin(ROUTINE)]
    matched_ids = {x for s in alarms.matched_episodes for x in s.split(";") if x}
    n_ev = int((data.events.type == "bms_alarm").sum())
    inc = compare_incidents(incidents, rows, alarms, data, assets)
    both = [i for i in inc if not i["bms_missed"] and not i["we_missed"]]
    res = {
        "dataset": str(data.manifest.get("profile", data.path.name)),
        "period": f"{data.start:%Y-%m-%d}..{data.end:%Y-%m-%d}",
        "decisions_file": str(decisions), "labels_source": labels_source,
        "params": {"slack_h": slack_h, "transient_h": TRANSIENT_H, "code_issue_types": {k: sorted(v) for k, v in CODE_ISSUES.items()}},
        "volume": {
            "bms_events": n_ev, "bms_alarm_episodes": int(len(alarms)),
            "bms_matched": int((alarms.matched_episodes != "").sum()), "bms_unmatched": int((alarms.matched_episodes == "").sum()),
            "our_decisions": len(rows), "our_episodes": int(len(eps)), "our_incident_episodes": int(len(inc_eps)),
            "our_incident_episodes_bms_silent": int((~inc_eps.episode_id.isin(matched_ids)).sum()),
            "episode_reduction_pct": round(100 * (1 - len(eps) / len(alarms)), 1) if len(alarms) else None,
        },
        "per_code": per_code(alarms) if len(alarms) else [],
        "per_issue": per_issue(eps, alarms) if len(eps) else [],
        "incidents": inc,
        "incident_summary": {
            "n": len(inc), "ours_detected": sum(not i["we_missed"] for i in inc),
            "bms_detected": sum(not i["bms_missed"] for i in inc), "both_detected": len(both),
            "ours_earlier_than_bms": sum(i["lead_vs_bms_h"] > 0 for i in both),
            "bms_missed_ours_detected": sum(i["bms_missed"] and not i["we_missed"] for i in inc),
            "median_lead_vs_bms_h": round(float(np.median([i["lead_vs_bms_h"] for i in both])), 1) if both else None,
        },
        "detector_lookalikes": lookalikes(Path(decisions)),
        "alarms": [{**{k: (_iso(v) if k.endswith("_utc") else v) for k, v in r.items()},
                    "value": None if pd.isna(r["value"]) else float(r["value"]),
                    "lead_h": None if pd.isna(r["lead_h"]) else round(float(r["lead_h"]), 2),
                    "duration_h": round(float(r["duration_h"]), 2), "open_at_end": bool(r["open_at_end"])}
                   for r in alarms.to_dict("records")],
    }
    return res


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("decisions")
    p.add_argument("data_dir")
    p.add_argument("--out", help="output json (default: bms_compare.json next to the decisions file); "
                                 "markdown is written next to it with suffix .md")
    p.add_argument("--labels", help="hand labels json with 'incidents' (default: weak labels from work orders)")
    p.add_argument("--no-labels", action="store_true", help="skip the incident comparison")
    p.add_argument("--slack-h", type=float, default=6.0, help="time slack for alarm/episode overlap (h)")
    a = p.parse_args(argv)
    decisions = Path(a.decisions)
    out = Path(a.out) if a.out else decisions.with_name("bms_compare.json")
    incidents, src = [], ""
    if a.labels and not a.no_labels:
        incidents, src = json.loads(Path(a.labels).read_text(encoding="utf-8"))["incidents"], Path(a.labels).name
    elif not a.no_labels:
        incidents, src = None, f"weak labels: corrective work orders, {WO_LOOKBACK_D}-day look-back"
    res = compare(decisions, Path(a.data_dir), incidents, src, a.slack_h)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = to_markdown(res)
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    print(f"[done] wrote {out} and {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
