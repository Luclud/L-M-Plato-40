"""T29 Alarms vs Steward: the alarm flood vs the incidents Steward raised (every number computed from the run)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from viewer import alarm_stats as A
from viewer import components as C
from viewer import labels as L
from viewer.views import common

MUTED = {"ROOM_TEMP_LOW": "#b9c7d6", "ROOM_TEMP_HIGH": "#e4c1a6", "SENSOR_COMM_LOSS": "#cfcfcf",
         "FILTER_DP_HIGH": "#c9d8b6", "AIRFLOW_LOW": "#d9cbe6", "WATER_NIGHT_FLOW": "#a9d3dd"}
ACCENT = "#1f6f5c"


@st.cache_data(show_spinner="Comparing alarms and incidents …", max_entries=8)
def stats(data_dir: str, decisions: str, mtime: float) -> dict:
    data, _ = common.load_data(data_dir)
    df = common.load_decisions(decisions, mtime)
    labels, source = A.labels_for(data_dir)
    return {"events": A.events_by_code(data), "episodes": A.alarm_episodes(data), "weeks": A.alarms_by_week(data),
            "inc": A.incident_episodes(df), "inc_weeks": A.incidents_by_week(df, data.start, data.end),
            "labels_source": source,
            "detection": A.incident_detection(data, df, labels), "coverage": A.coverage(data, df, labels),
            "noise": A.noise(data), "outages": A.gateway_outages(data)}


def flood_chart(s: dict) -> go.Figure:
    fig = go.Figure()
    w = s["weeks"]
    for code in sorted(w.alarm_code.unique()) if len(w) else []:
        g = w[w.alarm_code == code]
        fig.add_bar(x=g.week, y=g.n, name=L.ALARM_CODES.get(code, code),
                    marker_color=MUTED.get(code, "#d0d0d0"),
                    hovertemplate="%{x|%d %b}: %{y} alarm(s)<extra>" + L.ALARM_CODES.get(code, code) + "</extra>")
    iw = s["inc_weeks"]
    if len(iw):
        fig.add_scatter(x=iw.week, y=iw.n, name="Steward incidents", mode="lines+markers",
                        line=dict(color=ACCENT, width=3), marker=dict(size=5),
                        hovertemplate="%{x|%d %b}: %{y} incident(s)<extra>Steward</extra>")
    fig.update_layout(barmode="stack", height=360, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=-0.15), plot_bgcolor="white",
                      xaxis_title="Week (local)", yaxis_title="per week")
    return fig


def coverage_chart(cov: pd.DataFrame) -> go.Figure:
    names = [L.label("issue", x) for x in cov.issue_type]
    lead = ["—" if pd.isna(x) else f"{x:+.1f} h" for x in cov.median_lead_h]
    fig = go.Figure()
    for col, name, colour in (("labelled", "Labelled incidents", "#9aa5ad"), ("by_alarms", "Caught by alarms",
                                                                               "#c9a27e"),
                              ("by_steward", "Caught by Steward", ACCENT)):
        fig.add_bar(x=names, y=cov[col], name=name, marker_color=colour, text=cov[col], textposition="outside",
                    customdata=lead, hovertemplate="%{x}: %{y}<br>Steward ahead of the first alarm (median): "
                                                   "%{customdata}<extra>" + name + "</extra>")
    fig.update_layout(barmode="group", height=330, margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="white",
                      legend=dict(orientation="h", y=-0.2))
    return fig


def _missed(det: pd.DataFrame) -> list[str]:
    out = []
    for r in det[det.we_missed].itertuples(index=False):
        day = pd.Timestamp(r.start_utc).tz_convert(L.TZ)
        out.append(f"{L.label('issue', r.issue_type)} · {L.assets_text(r.assets)} · from {day:%a %d %b}")
    return out


def page():
    c = common.ctx()
    st.subheader(L.ALARMS_TITLE)
    st.caption(L.ALARMS_INTRO)
    s = stats(str(c["data_dir"]), str(c["decisions"]), c["mtime"])
    n_alarm, n_inc = len(s["episodes"]), len(s["inc"])
    if n_alarm == 0 and n_inc == 0:
        st.info(L.ALARMS_EMPTY, icon=":material/info:")
        return
    cov, det, nz = s["coverage"], s["detection"], s["noise"]
    water = cov[cov.issue_type == "water_leak"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Building alarms", f"{n_alarm:,}", help=f"{sum(s['events'].values()):,} alarm events "
              "(raised + cleared), paired into alarms")
    m2.metric("Steward incidents", f"{n_inc:,}")
    if len(water):
        w = water.iloc[0]
        m3.metric("Water leaks caught: alarms vs Steward", f"{w.by_alarms} vs {w.by_steward}",
                  help=f"of {w.labelled} labelled water leaks")

    st.markdown("#### The flood vs the signal")
    st.plotly_chart(flood_chart(s), width="stretch", key="alarms_flood")
    st.caption(" · ".join(f"{L.ALARM_CODES.get(k, k)}: {v:,}" for k, v in s["events"].items())
               + " alarm events in this run")

    st.markdown("#### Which real problems were caught")
    if len(cov):
        st.plotly_chart(coverage_chart(cov), width="stretch", key="alarms_cov")
        st.caption(L.ALARMS_LABELS_NOTE[s["labels_source"]])
    else:
        st.caption("No labels for this dataset, so detection is not compared.")

    st.markdown("#### Why the alarms are noisy")
    with st.container(border=True):
        lines = []
        if nz["comm_loss"]:
            lines.append(f"**{nz['comm_loss_in_outage']} of {nz['comm_loss']}** “sensor offline” alarms fired while a "
                         f"whole floor's gateway was down ({nz['gateway_outages']} outages): one cause, many alarms. "
                         "Steward raises one data issue per outage and treats those rooms as unknown.")
        if nz["temp_low"]:
            lines.append(f"**{nz['temp_low_vacant']} of {nz['temp_low']}** “room too cold” alarms were in a room where "
                         "the presence sensor saw nobody — an empty room in eco mode, not a comfort problem.")
        if nz["short_alarms"]:
            lines.append(f"**{nz['short_alarms']}** alarms cleared themselves within an hour.")
        st.markdown("\n".join(f"- {x}" for x in lines) or "No alarm noise found in this run.")
    st.caption(L.ALARMS_HONEST)
    missed = _missed(det) if len(det) else []
    if len(det):
        st.markdown("**Steward missed:** " + ("; ".join(missed) if missed else "none of the labelled incidents"))

    C.tech_details({
        "alarm episodes": "events.jsonl type=bms_alarm, raise/clear paired per (asset, alarm code) with "
                          "analysis/bms_compare.alarm_episodes; a raise without a clear stays open to the end",
        "caught": "analysis/bms_compare.compare_incidents: an alarm of a compatible code on a related asset raised "
                  "inside the label window; Steward = a decision of the label's issue type on its assets in the window",
        "labels": "eval/labels_<profile>.json if present, else eval/evaluate.weak_labels (work orders + waits)",
        "gateway outage": "15-min steps where every room behind one gateway (building.json sensors) sent no valid "
                          "temperature, >= 30 min",
        "vacant": "room_telemetry presence == 0 at the alarm's raise time",
        "events_by_code": s["events"], "noise": nz,
        "coverage": cov.to_dict("records"),
    })


if __name__ == "__main__":
    page()
