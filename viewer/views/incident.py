"""Incident story page (one episode): What we saw -> Why we think so -> What we did -> How we'll know.

Rendered by the Incidents page when the URL has ``?episode=<id>``; linkable from Building, Approvals and the tour."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from viewer import components as C
from viewer import core
from viewer import labels as L
from viewer import summaries

_STRICT = {"autonomous": 0, "human_approval": 1, "approval_and_consent": 2}
_CODE = re.compile(r"\b(?:WM_R\d{3}|Z_R\d{3}|R\d{3}|WM_MAIN|HP1|AHU1|PV1|BAT1|ACS1|VEND1|WASTE_F\d|WASTE_MAIN|"
                   r"GW_F\d|CLEANER_\d|TECH_\d|ROBOT_\d|ONCALL_\d)\b")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_FILE = re.compile(r"\b[\w\-]+\.(?:csv|jsonl|json)(?::[A-Za-z0-9_\-]+)?:?")
_ACTION = re.compile(r"\b(" + "|".join(sorted(L.ACTIONS, key=len, reverse=True)) + r")\b")
_ISSUE = re.compile(r"\b(" + "|".join(sorted(L.ISSUE_TYPES, key=len, reverse=True)) + r")\b")
_ID = re.compile(r"\b(?:D\d{6}|[A-Z]{2}-\d{5}|GR\d{6}|WO\d{5}|SH\d{6})\b")


def humanize(text) -> str:
    """Pipeline free text -> plain language: no file names, codes or UTC (local time instead)."""
    if not isinstance(text, str) or not text:
        return ""
    s = _FILE.sub("", text)
    s = _TS.sub(lambda m: C.local_time(m.group(0)), s).replace("..", " – ")
    s = _DATE.sub(lambda m: pd.Timestamp(m.group(0)).strftime("%a %-d %b"), s)
    s = _CODE.sub(lambda m: L.label("asset", m.group(0)), s)
    s = _ACTION.sub(lambda m: L.label("action", m.group(1)).lower(), s)
    s = _ISSUE.sub(lambda m: L.label("issue", m.group(1)).lower(), s)
    s = _ID.sub("", s)
    s = re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\(\s*\)", "", s))
    return " ".join(s.replace("->", "→").replace("~", "≈").split()).strip(" :")


def strictest(rows: pd.DataFrame) -> str:
    return max(rows.autonomy, key=lambda a: _STRICT.get(a, 0)) if len(rows) else "autonomous"


def confidence_word(c) -> str:
    c = core.nan_to_none(c)
    if c is None:
        return ""
    c = float(c)
    return f"confidence {'high' if c >= 0.75 else 'medium' if c >= 0.5 else 'low'} ({c:.1f})"


def matching_label(rows: pd.DataFrame, labels: list[dict]) -> dict | None:
    """Hand label (dev aid, quickstart only) that this episode belongs to."""
    if rows.empty:
        return None
    it, assets, t0 = rows.issue_type.iloc[0], set(a for x in rows.affected_assets for a in x), rows.t.min()
    for lab in labels:
        if lab.get("issue_type") != it:
            continue
        s, e = pd.Timestamp(lab["start_utc"]), pd.Timestamp(lab["end_utc"])
        if not (s - pd.Timedelta(days=3) <= t0 <= e):
            continue
        if "BUILDING" in lab.get("assets", []) or assets & set(lab.get("assets", [])):
            return lab
    return None


def _duration(td: pd.Timedelta) -> str:
    m = int(abs(td).total_seconds() // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min" if m < 48 * 60 else f"{m // 1440} days"


def title(rows: pd.DataFrame) -> str:
    first = rows.iloc[0]
    assets = list(dict.fromkeys(a for x in rows.affected_assets for a in x))
    more = f" +{len(assets) - 3} more" if len(assets) > 3 else ""
    return f"{L.icon('issue', first.issue_type)} {L.label('issue', first.issue_type)} · {L.assets_text(assets[:3])}{more}"


def _approve_buttons(ctx, r, key):
    decided = (ctx.get("approvals") or {}).get(r.event_id)
    if decided:
        st.caption(f"{L.MOCK} · recorded: {decided['status']}")
        return
    a, b, c = st.columns([1, 1, 4])
    for col, status, lab, icon in ((a, "approved", "Approve", ":material/check:"),
                                   (b, "rejected", "Reject", ":material/close:")):
        if col.button(lab, key=f"{key}_{status}_{r.event_id}", icon=icon):
            ctx["approvals"] = core.record_approval(ctx["approvals_path"], r.event_id, status, str(ctx["decisions"]))
            st.rerun()
    c.caption(L.MOCK)


def render(ctx: dict, episode: str, key: str = "story"):
    df, store = ctx["df"], ctx["store"]
    rows = df[df.episode == episode].sort_values(["t", "event_id"])
    if rows.empty:
        st.warning("This incident is not in the loaded decisions.")
        return
    first = rows.iloc[0]
    t0, t1 = rows.t.min(), rows.t.max()
    st.markdown(f"## {title(rows)}")
    C.chips(min(rows.priority, key=lambda p: core.PRIO_RANK.get(p, 9)), strictest(rows))
    bits = [C.time_range(t0, t1)]
    lab = matching_label(rows, core.load_labels(core.profile_of(ctx["data_dir"])))
    if lab:
        d = t0 - pd.Timestamp(lab["start_utc"])
        bits.append(f"detected {_duration(d)} after it started" if d >= pd.Timedelta(0) else
                    f"flagged {_duration(d)} before it started")
    conf = confidence_word(rows.confidence.max())
    if conf:
        bits.append(conf)
    st.caption(" · ".join(bits))
    summaries.show(summaries.load(ctx["decisions"]).get(episode))

    st.markdown("#### 1 · What we saw")
    charts = store.charts_for_decisions(rows)
    if charts:
        markers = [(t, L.label("action", a)) for t, a in zip(rows.t, rows.action)]
        cols = st.columns(min(2, len(charts)))
        for i, ch in enumerate(charts):
            with cols[i % len(cols)]:
                st.plotly_chart(core.figure(ch, markers, shade=(t0, t1)), width="stretch", theme=None,
                                key=f"{key}_{episode}_chart_{i}", config={"displayModeBar": False})
        st.caption("Red numbers = what we did and when (data to the right of a number was not yet known then). "
                   "Amber band = incident period. Grey = data gap (unknown, never treated as zero).")
    else:
        st.caption("No measured series for this incident (it is based on bookings and staff plans).")

    st.markdown("#### 2 · Why we think so")
    why = list(dict.fromkeys(humanize(core.parse_evidence(e).detail or e) for x in rows.evidence for e in x))
    why = [w for w in why if w]
    excl = []
    for reason in rows.reason:
        m = re.search(r"Excluded:\s*(.+?)(?:\.|$)", str(reason))
        if m:
            excl.append(humanize(m.group(1)))
    st.markdown("\n".join(f"- {w}" for w in why[:8]) or "- " + humanize(first.reason))
    if excl:
        st.markdown("**Look-alikes ruled out:** " + "; ".join(dict.fromkeys(excl)))

    st.markdown("#### 3 · What we did")
    for n, (_, r) in enumerate(rows.iterrows(), 1):
        with st.container(border=True, key=f"card_{key}_{episode}_step_{n}"):
            a, b = st.columns([5, 2])
            res = f" · {L.label('resource', r.resource_id)}" if isinstance(r.resource_id, str) else ""
            a.markdown(f"**{n}** · {C.local_time(r.t)} · {L.icon('action', r.action)} "
                       f"**{L.label('action', r.action)}** — {L.assets_text(r.affected_assets)}{res}")
            with b:
                C.status_chip(r.autonomy)
            st.markdown(humanize(r.reason))
            if isinstance(r.scheduled_for_utc, str) and r.scheduled_for_utc:
                st.caption(f"Planned for {C.local_time(r.scheduled_for_utc)}")
            if r.requires_guest_consent:
                st.warning(L.CONSENT_NEEDED, icon=":material/gpp_maybe:")
            C.message_bubble(core.nan_to_none(r.message))
            if r.requires_human_approval:
                _approve_buttons(ctx, r, key)

    st.markdown("#### 4 · How we'll know it worked")
    plans = [humanize(v) for v in dict.fromkeys(rows.verification_plan) if isinstance(v, str) and v]
    outs = [humanize(v) for v in dict.fromkeys(rows.expected_outcome) if isinstance(v, str) and v]
    for p in plans[:4]:
        st.markdown(f"- **Check:** {p}")
    for o in outs[:4]:
        st.markdown(f"- **Expected:** {o}")
    for v in rows.simulated_effect:
        sim = C.simulated_badge(core.nan_to_none(v))
        if sim:
            st.markdown(f"- {humanize(sim)}")
    if not (plans or outs):
        st.caption("No verification plan recorded for this incident.")
    tech = rows.drop(columns=["t", "episode"]).to_dict("records")
    C.tech_details(tech)
