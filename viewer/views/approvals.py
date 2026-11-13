"""Approvals: one card per incident with decisions that need a human; mock Approve/Reject (nothing is executed)."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from viewer import components as C
from viewer import core
from viewer import labels as L
from viewer.views import common
from viewer.views.incident import humanize

PAGE = 20
_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_COST = ("cost", "EUR", "€", "contractor", "budget")
_SHIFT = re.compile(r"\s*(?:\(|for\s+)?(?:shift\s+)?\bSH\d+\)?")


def cards(df: pd.DataFrame, approvals: dict) -> pd.DataFrame:
    """One row per episode with >= 1 approval decision: status pending|decided, urgency, decisions."""
    cols = ["episode", "issue_type", "priority", "start", "pending", "decided", "rows"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    a = df[df.requires_human_approval]
    out = []
    for ep, g in a.sort_values(["t", "event_id"]).groupby("episode", sort=False):
        done = g.event_id.isin(set(approvals or {}))
        out.append({"episode": ep, "issue_type": g.issue_type.iloc[0],
                    "priority": min(g.priority, key=lambda p: _SEV.get(p, 9)), "start": g.t.min(),
                    "pending": int((~done).sum()), "decided": int(done.sum()), "rows": g})
    c = pd.DataFrame(out, columns=cols)
    if c.empty:
        return c
    return c.assign(_s=c.priority.map(_SEV).fillna(9)).sort_values(["_s", "start"]).drop(columns="_s")


def question(r) -> str:
    when = r.scheduled_for_utc if isinstance(r.scheduled_for_utc, str) and r.scheduled_for_utc else r.t
    assets = L.assets_text(list(r.affected_assets)[:3]) + (
        f" +{len(r.affected_assets) - 3} more" if len(r.affected_assets) > 3 else "")
    res = f" ({L.label('resource', r.resource_id)})" if isinstance(r.resource_id, str) and r.resource_id else ""
    if r.action == "request_human_approval":
        return f"Your decision is needed for {assets} — {C.local_time(when)}"
    return f"Approve “{L.label('action', r.action)}”{res} for {assets} — {C.local_time(when)}?"


def why(r) -> str:
    text = C.headline(_SHIFT.sub("", humanize(r.reason)))
    return text[:1].upper() + text[1:]


def cost_hint(r) -> str | None:
    txt = " ".join(str(x) for x in (r.reason, r.approval_note) if isinstance(x, str))
    return "Needs approval because it may cost money." if any(k in txt for k in _COST) else None


def _record(c, event_id, status):
    c["approvals"] = core.record_approval(c["approvals_path"], event_id, status, str(c["decisions"]), L.MOCK)


def card(c: dict, row, key: str):
    rows = row.rows
    first = rows.iloc[0]
    with st.container(border=True, key=f"card_{key}_{row.episode}"):
        a, d = st.columns([6, 1.3])
        with a:
            st.markdown(f"{L.icon('issue', row.issue_type)} **{L.label('issue', row.issue_type)}** · "
                        f"{L.assets_text(list(first.affected_assets)[:3])} · since {C.local_time(row.start)}")
            C.chips(row.priority)
        d.page_link("views/incidents.py", label="Open incident", icon=":material/open_in_new:",
                    query_params={"episode": row.episode})
        for r in rows.itertuples():
            st.markdown(f"**{question(r)}**")
            st.caption(why(r))
            hint = cost_hint(r)
            if hint:
                st.caption(f":material/euro: {hint}")
            if r.requires_guest_consent:
                st.error(L.CONSENT_NEEDED, icon=":material/gpp_maybe:")
            rec = (c.get("approvals") or {}).get(r.event_id)
            if rec:
                st.caption(f"{'Approved' if rec['status'] == 'approved' else 'Rejected'} (mock) — nothing was executed")
                continue
            x, y, _ = st.columns([1, 1, 5])
            x.button("Approve", key=f"{key}_ok_{r.event_id}", type="primary", icon=":material/check:",
                     on_click=_record, args=(c, r.event_id, "approved"))
            y.button("Reject", key=f"{key}_no_{r.event_id}", icon=":material/close:",
                     on_click=_record, args=(c, r.event_id, "rejected"))


c = common.ctx()
st.subheader("Approvals")
st.warning(f"{L.MOCK}. Approve/Reject is only written to a local approvals file (see Technical details).",
           icon=":material/science:")
allc = cards(c["df"], c.get("approvals") or {})
if allc.empty:
    st.info("Nothing needs a human decision in this run.")
else:
    n_p, n_d = int((allc.pending > 0).sum()), int((allc.pending == 0).sum())
    show = st.segmented_control("Show", ["Pending", "Decided"], default="Pending", key="ap_show",
                                format_func=lambda s: f"{s} {n_p if s == 'Pending' else n_d}",
                                label_visibility="collapsed") or "Pending"
    sub = allc[allc.pending > 0] if show == "Pending" else allc[allc.pending == 0]
    pages = max(1, -(-len(sub) // PAGE))
    page = st.number_input("Page", 1, pages, 1, key="ap_page") if pages > 1 else 1
    st.caption(f"{len(sub)} incident(s)" + (f" · page {page} of {pages}" if pages > 1 else ""))
    if sub.empty:
        st.info("No pending approvals." if show == "Pending" else "Nothing decided yet.")
    for row in sub.iloc[(page - 1) * PAGE: page * PAGE].itertuples():
        card(c, row, "ap")
C.tech_details({"approvals_file": str(c["approvals_path"]), "decisions_file": str(c["decisions"])})
