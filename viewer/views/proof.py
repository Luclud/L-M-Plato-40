"""Proof: validator, detection vs labels (dev aid), BMS vs us, data quality, known misses, downloads.

Raw codes and file names are allowed on this page (developer / jury evidence)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

from viewer import core
from viewer import labels as L
from viewer.views import common

WATER_CODES = ("WATER_NIGHT_FLOW",)


def insights_dir() -> Path:
    return common.insights_dir()


def bms_sentence(c: dict) -> tuple[str, pd.DataFrame | None]:
    """One sentence with real numbers: BMS water alarms vs our water-leak incidents (+ T12 table if present)."""
    df = c["df"]
    b = core.bms_alarms(c["data"])
    raised = b[b.alarm_code.isin(WATER_CODES) & (b.state == "raised")]
    leaks = df[df.issue_type == "water_leak"].episode.nunique() if len(df) else 0
    all_raised = int((b.state == "raised").sum())
    ours = df[~df.issue_type.isin(L.ROUTINE_TYPES)].episode.nunique() if len(df) else 0
    text = (f"The BMS raised {L.n(len(raised), 'water alarm')}; we opened {L.n(leaks, 'water-leak incident')}. "
            f"Across all codes the BMS raised {L.n(all_raised, 'alarm')}, we opened {L.n(ours, 'incident')} "
            "(BMS alarms are a clue, not ground truth).")
    table = None
    files = core.side_files(c["decisions"])["bms"]
    js = next((p for p in files if p.suffix == ".json"), None)
    data = core.load_json(js) if js else None
    if data and data.get("per_code"):
        table = pd.DataFrame(data["per_code"])
        v = data.get("volume", {})
        if v:
            text += (f" T12 comparison: {v.get('bms_alarm_episodes')} BMS alarm episodes vs "
                     f"{v.get('our_incident_episodes')} incident episodes of ours; "
                     f"{v.get('our_incident_episodes_bms_silent')} of ours had no BMS alarm.")
    return text, table


def friendly_eval(text: str) -> tuple[str, pd.DataFrame, str]:
    t = core.parse_eval_table(text)
    head = next((x for x in text.splitlines() if x.startswith("Incidents detected")), "")
    miss = next((x for x in text.splitlines() if x.startswith("Episodes not matching")), "")
    if len(t):
        t = t.assign(detected=t.detected.map({True: "yes", False: "no"}),
                     **{"first action": t["first decision"].map(_first_action)}).drop(columns="first decision")
    return head, t, miss


def _first_action(s: str) -> str:
    m = re.search(r"\b([a-z_]+) @", str(s))
    return L.label("action", m.group(1)) if m else ""


c = common.ctx()
df = c["df"]
st.subheader("Proof")

st.markdown("#### Safety & contract check")
key = ("proof_check", str(c["decisions"]), c["mtime"])
if st.button("Run check", icon=":material/verified:", type="primary", key="proof_run"):
    with st.spinner("Running validate.py and eval/evaluate.py …"):
        st.session_state[key] = c["checks"].result()
res = st.session_state.get(key)
if res is not None:
    ok, vout, eout = res
    (st.success if ok else st.error)(f"validate.py: **{'PASS' if ok else 'FAIL'}**: "
                                     + ("0 safety/contract violations" if ok else "see the output below"))
    with st.expander("validate.py output", expanded=not ok):
        st.code(vout, language="text")
    head, table, miss = friendly_eval(eout)
    if len(table):
        st.markdown("#### Detection vs labels")
        st.caption("Dev aid: labels are not ground truth (hand labels on quickstart, work orders on dev).")
        if head:
            st.markdown(f"**{head}**")
        st.dataframe(table, width="stretch", hide_index=True)
        st.markdown("#### Known misses")
        missed = table[table.detected == "no"]
        st.markdown(f"- Not detected: {', '.join(missed.incident) if len(missed) else 'none'}")
        late = table[(table.detected == "yes") & (table.in_time != "yes")]
        if len(late):
            st.markdown(f"- Detected late: {', '.join(late.incident)}")
        if miss:
            st.markdown(f"- {miss}")
        with st.expander("evaluate.py output"):
            st.code(eout, language="text")
    else:
        st.caption("No labels for this dataset, so detection is not scored.")

st.markdown("#### BMS vs us")
sentence, table = bms_sentence(c)
st.markdown(sentence)
if table is not None:
    st.dataframe(table, width="stretch", hide_index=True)

st.markdown("#### Data quality")
dq = pd.DataFrame([vars(x) for x in c["data"].dq])
if len(dq):
    st.markdown(f"{int(dq['count'].sum())} rows cleaned while loading ({L.n(len(dq), 'finding')}); "
                "missing values stay unknown.")
    st.dataframe(dq.groupby(["kind", "source"]).agg(findings=("count", "size"), rows=("count", "sum")), width="stretch")
dqd = df[df.issue_type == "data_quality"] if len(df) else df
gw = dqd[dqd.affected_assets.map(lambda a: any(x.startswith("GW_") for x in a)).astype(bool)] if len(dqd) else dqd
st.markdown(f"{L.n(dqd.episode.nunique() if len(dqd) else 0, 'data-quality episode')}, "
            f"{L.n(gw.episode.nunique() if len(gw) else 0, 'gateway outage')}. "
            "Rooms behind an outage are unknown, not normal.")
if len(gw):
    st.dataframe(gw[["event_id", "timestamp_utc", "affected_assets", "action", "reason"]], width="stretch",
                 hide_index=True)

st.markdown("#### Downloads")
row = st.container(key="btnrow_downloads")   # style.css: buttons side by side
dec = c["decisions"]
row.download_button("decisions.jsonl", dec.read_bytes() if dec.exists() else b"", file_name=dec.name,
                   icon=":material/download:", key="dl_dec")
for name in ("insights.md", "insights.jsonl"):
    p = insights_dir() / name
    if p.exists():
        row.download_button(name, p.read_bytes(), file_name=name, icon=":material/download:", key=f"dl_{name}")
    else:
        row.caption(f"{name}: not generated yet")

