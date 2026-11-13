"""Shared UI building blocks. All wording comes from :mod:`viewer.labels`."""
from __future__ import annotations

import json
import re

import pandas as pd
import streamlit as st

from viewer import labels as L


def local_time(ts, with_day: bool = True) -> str:
    """UTC timestamp -> local wall-clock text, e.g. "Mon 12 Jan, 19:30"."""
    if ts is None or (not isinstance(ts, str) and pd.isna(ts)):
        return ""
    t = pd.Timestamp(ts)
    t = (t.tz_localize("UTC") if t.tzinfo is None else t).tz_convert(L.TZ)
    return t.strftime("%a %-d %b, %H:%M") if with_day else t.strftime("%H:%M")


def time_range(t0, t1) -> str:
    a, b = local_time(t0), local_time(t1)
    if a == b:
        return a
    return f"{a}–{local_time(t1, with_day=False)}" if a[:10] == b[:10] else f"{a} – {b}"


_CLAUSE = re.compile(r"(?<=[a-z0-9)%])[.;](?:\s|$)|\s->\s|\s—\s")


def headline(reason, limit: int = 120) -> str:
    """First clause of a decision reason, at most ``limit`` characters."""
    if not reason:
        return ""
    text = " ".join(str(reason).split())
    first = _CLAUSE.split(text, maxsplit=1)[0].strip()
    if len(first) > limit:
        first = first[: limit - 1].rsplit(" ", 1)[0] + "…"
    return first


def status_chip(autonomy: str):
    st.badge(L.label("autonomy", autonomy), icon=L.icon("autonomy", autonomy), color=L.colour("autonomy", autonomy))


def priority_chip(priority: str):
    st.badge(L.label("priority", priority), color=L.colour("priority", priority))


def chips(priority: str | None = None, autonomy: str | None = None):
    """Priority and status side by side on one line (markdown badges, same colours as the chips)."""
    parts = []
    if priority:
        parts.append(f":{L.colour('priority', priority)}-badge[{L.label('priority', priority)}]")
    if autonomy:
        icon = L.icon("autonomy", autonomy)
        parts.append(f":{L.colour('autonomy', autonomy)}-badge[{icon + ' ' if icon else ''}"
                     f"{L.label('autonomy', autonomy)}]")
    if parts:
        st.markdown("&nbsp;".join(parts))


def simulated_badge(value) -> str | None:
    """Markdown for a simulated effect, or None. Always carries the honesty label."""
    if value in (None, "", [], {}):
        return None
    if isinstance(value, dict):
        text = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in value.items())
    else:
        text = str(value)
    text = re.sub(r"^\s*SIMULATED[:\s]*", "", text, flags=re.I)
    return f":gray-badge[{L.SIMULATED}] {text}"


def message_bubble(msg):
    """Proposed guest message - never shown as delivered."""
    if not msg:
        return
    if isinstance(msg, str):
        msg = {"text": msg}
    lang = str(msg.get("language") or "").lower()
    with st.chat_message("assistant", avatar=":material/mail:"):
        st.caption(f"{L.LANG_FLAGS.get(lang, '')} {lang.upper()} · {L.PROPOSED}".strip())
        st.markdown(msg.get("text", ""))


def tech_details(obj, title: str = "Technical details"):
    """Collapsed box with ids, UTC, raw evidence and JSON - the only place raw codes appear on cards."""
    with st.expander(title, icon=":material/code:", expanded=False):
        if isinstance(obj, pd.DataFrame):
            obj = obj.to_dict("records")
        st.code(json.dumps(obj, default=str, ensure_ascii=False, indent=1), language="json")


def decision_step(row, n: int):
    """One numbered step of "What we did"."""
    c1, c2 = st.columns([5, 2])
    with c1:
        st.markdown(f"**{n}** · {local_time(row['timestamp_utc'], with_day=False)} · "
                    f"{L.icon('action', row['action'])} {L.label('action', row['action'])} — "
                    f"{L.assets_text(row.get('affected_assets'))}")
        st.caption(headline(row.get("reason")))
    with c2:
        status_chip(row["autonomy"])
    if row.get("requires_guest_consent"):
        st.warning(L.CONSENT_NEEDED, icon=":material/gpp_maybe:")
    message_bubble(row.get("guest_message"))
    sim = simulated_badge(row.get("simulated_effect"))
    if sim:
        st.markdown(sim)


def incident_card(ep: pd.DataFrame, key: str | None = None) -> bool:
    """Card for one episode (rows = its decisions). Returns True when "Open" was clicked."""
    ep = ep.sort_values("timestamp_utc")
    first = ep.iloc[0]
    top = ep.sort_values("priority", key=lambda s: s.map({"critical": 0, "high": 1, "medium": 2, "low": 3})).iloc[0]
    worst = "approval_and_consent" if (ep.autonomy == "approval_and_consent").any() else (
        "human_approval" if (ep.autonomy == "human_approval").any() else "autonomous")
    with st.container(border=True):
        c1, c2, c3 = st.columns([6, 1.3, 2.2])
        with c1:
            st.markdown(f"#### {L.icon('issue', first.issue_type)} {L.label('issue', first.issue_type)} · "
                        f"{L.assets_text(first.affected_assets)}")
            st.caption(time_range(ep.timestamp_utc.min(), ep.timestamp_utc.max()))
        with c2:
            priority_chip(top.priority)
        with c3:
            status_chip(worst)
        st.markdown(f"“{headline(first.reason)}”")
        steps = list(dict.fromkeys(L.label("action", a).lower() for a in ep.action))
        st.caption("We: " + " → ".join(steps))
        return st.button("Open", key=key or f"open_{first.episode_id}", icon=":material/open_in_new:")
