"""T31 Guest messages: what guests wrote next to Steward's proposed replies, deadlines and the consent mock."""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from viewer import components as C
from viewer import conversations as K
from viewer import labels as L
from viewer.views import common

PAGE = 25
ROUTINE = "guest_request"
_ROOM_ACTIONS = ("request_inspection", "schedule_cleaning", "dispatch_technician")


@st.cache_data(show_spinner="Linking guest requests and replies …")
def load_threads(decisions: str, mtime: float, data_dir: str) -> pd.DataFrame:
    data, _ = common.load_data(data_dir)
    return K.threads(data, common.load_decisions(decisions, mtime))


def lang_text(code) -> str:
    code = str(code or "").lower()
    return f"{L.LANG_FLAGS.get(code, '')} {L.LANGUAGES.get(code, code.upper())}".strip()


def guest_bubble(r):
    """The guest's original message (also used on the incident story)."""
    due = f" · reply due {C.local_time(r['due'], with_day=False)}" if pd.notna(r["due"]) else ""
    with st.chat_message("user", avatar=":material/person:"):
        st.caption(f"Guest · {L.label('asset', r['room_id'])} · {lang_text(r['language'])} · "
                   f"{L.CHANNELS.get(r['channel'], r['channel'])} · {C.local_time(r['t'])}{due}")
        st.markdown(str(r["text"]))


def story_guest_text(ctx, d):
    """Incident story: the guest's own words above Steward's reply, and the mock consent answer if recorded."""
    gr = ctx["data"].guest_requests
    for g in gr[gr.request_id.isin(list(d.guest_request_ids))].itertuples(index=False):
        guest_bubble({"t": g.timestamp_utc, "due": g.response_due_utc, "room_id": g.room_id, "language": g.language,
                      "channel": g.channel, "text": g.text})
    if d.requires_guest_consent:
        txt = consent_text(K.load_consents(K.consents_path(ctx["approvals_path"])), d.event_id)
        if txt:
            st.caption(f":material/gpp_maybe: {txt}")


def consent_question(d) -> str:
    when = d.scheduled_for_utc if isinstance(d.scheduled_for_utc, str) and d.scheduled_for_utc else d.timestamp_utc
    rooms = [a for a in d.affected_assets if str(a).startswith("R")]
    if d.action in _ROOM_ACTIONS and rooms:
        return f"May we enter {L.assets_text(rooms)} at about {C.local_time(when)}?"
    return f"May we go ahead with “{L.label('action', d.action)}” for {L.assets_text(d.affected_assets)} " \
           f"at about {C.local_time(when)}?"


def consent_text(consents: dict, event_id: str) -> str | None:
    v = (consents or {}).get(event_id)
    return f"Consent: {v['status']} (mock)" if v else None


def consent_card(c, d, consents: dict, path):
    with st.container(border=True, key=f"consent_{d.event_id}"):
        st.markdown(f":material/gpp_maybe: **{consent_question(d)}**")
        st.caption(L.CONSENT_MOCK)
        a, b, s = st.columns([2, 2, 3])
        if a.button(L.CONSENT_YES, key=f"cy_{d.event_id}", icon=":material/thumb_up:"):
            K.record_consent(path, d.event_id, "yes")
            st.rerun()
        if b.button(L.CONSENT_NO, key=f"cn_{d.event_id}", icon=":material/thumb_down:"):
            K.record_consent(path, d.event_id, "no")
            st.rerun()
        s.markdown(consent_text(consents, d.event_id) or L.CONSENT_NEEDED)


def system_line(d) -> str:
    when = f" {C.local_time(d.scheduled_for_utc, with_day=False)}" if isinstance(d.scheduled_for_utc, str) \
        and d.scheduled_for_utc else ""
    res = f" · {L.label('resource', d.resource_id)}" if isinstance(d.resource_id, str) and d.resource_id else ""
    rooms = [a for a in d.affected_assets if a != d.resource_id]
    return (f":material/settings: {C.local_time(d.timestamp_utc, with_day=False)} · "
            f"{L.label('action', d.action)}{when} · {L.assets_text(rooms)}{res}")


def thread(c, r, consents: dict, path):
    df = c["df"]
    st.markdown(f"#### {L.label('asset', r['room_id'])} · {L.REQUEST_TYPES.get(r['request_type'], r['request_type'])}")
    guest_bubble(r)
    rows = K.thread_decisions(df, r["event_ids"])
    if rows.empty:
        st.warning(L.GUESTS_NO_REPLY, icon=":material/error:")
    first = True
    for d in rows.itertuples(index=False):
        if d.action != "notify_guest":
            st.caption(system_line(d))
        msg = d.message if isinstance(d.message, dict) else None
        if msg and msg.get("text"):
            with st.chat_message("assistant", avatar=":material/support_agent:"):
                src = L.MESSAGE_SOURCES.get(str(msg.get("source")), str(msg.get("source") or ""))
                st.caption(f"Steward · {C.local_time(d.timestamp_utc)} · {lang_text(msg.get('language'))} · "
                           f"{L.PROPOSED} · {src}")
                st.markdown(msg["text"])
                if first and pd.notna(r["response_minutes"]):
                    ok = ":green-badge[on time]" if r["on_time"] else ":red-badge[late]"
                    st.markdown(f"Proposed after {r['response_minutes']:.0f} min {ok}")
            first = False
        if d.requires_guest_consent:
            consent_card(c, d, consents, path)
    eps = df[df.episode.isin(r["episodes"]) & (df.issue_type != ROUTINE)].drop_duplicates("episode")
    for e in eps.itertuples(index=False):
        st.page_link("views/incidents.py", query_params={"episode": e.episode}, icon=L.icon("issue", e.issue_type),
                     label=f"Related incident: {L.label('issue', e.issue_type)} · {L.assets_text(e.affected_assets)}")
    C.tech_details({"request": {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in r.items()},
                    "decisions": rows[["event_id", "timestamp_utc", "action", "episode", "requires_guest_consent"]]
                    .to_dict("records")})


def _filter(th: pd.DataFrame) -> pd.DataFrame:
    with st.container(border=True):
        a, b, c3, d = st.columns(4)
        lang = a.multiselect("Language", sorted(th.language.dropna().unique()), format_func=lang_text, key="g_lang")
        typ = b.multiselect("Request", sorted(th.request_type.dropna().unique()),
                            format_func=lambda x: L.REQUEST_TYPES.get(x, x), key="g_type")
        ch = c3.multiselect("Channel", sorted(th.channel.dropna().unique()),
                            format_func=lambda x: L.CHANNELS.get(x, x), key="g_channel")
        room = d.multiselect("Room", sorted(th.room_id.dropna().unique()), format_func=lambda x: L.label("asset", x),
                             key="g_room")
        e, f, g = st.columns([4, 1, 1])
        q = e.text_input("Search", placeholder="Search the guest's words, e.g. Heizung", key="g_q",
                         label_visibility="collapsed")
        consent = f.checkbox("Needs consent", key="g_consent")
        late = g.checkbox("Late / no reply", key="g_late")
    m = pd.Series(True, index=th.index)
    for col, v in (("language", lang), ("request_type", typ), ("channel", ch), ("room_id", room)):
        if v:
            m &= th[col].isin(v)
    if q:
        m &= th.text.astype(str).str.contains(q, case=False, regex=False) | th.room_id.astype(str).str.contains(
            q, case=False, regex=False)
    if consent:
        m &= th.needs_consent | th.consent_flag
    if late:
        m &= th.on_time.map(lambda v: v is not True)
    return th[m]


def page():
    c = common.ctx()
    st.subheader(L.GUESTS_TITLE)
    st.caption(L.GUESTS_INTRO)
    th = load_threads(str(c["decisions"]), c["mtime"], str(c["data_dir"]))
    if th.empty:
        st.info(L.GUESTS_EMPTY, icon=":material/info:")
        return
    path = K.consents_path(c["approvals_path"])
    consents = K.load_consents(path)
    s = K.summary(th, consents, c["df"])
    m = st.columns(5)
    m[0].metric("Guest requests", f"{s['requests']:,}", help=f"{s['no_reply']} without any reply from Steward")
    m[1].metric("Proposed reply on time", "—" if s["on_time_pct"] is None else f"{s['on_time_pct']:.0f} %",
                help="First proposed reply before the reply deadline, of the requests that got one")
    m[2].metric("Median time to a proposed reply", "—" if s["median_minutes"] is None else
                f"{s['median_minutes']:.0f} min")
    m[3].metric("Languages", s["languages"])
    m[4].metric("Consent requests open", s["consent_open"], help=L.CONSENT_MOCK)
    f = _filter(th)
    if f.empty:
        st.info(L.GUESTS_NONE_MATCH, icon=":material/filter_alt_off:")
        return
    want = st.query_params.get("request")
    if want in set(th.request_id) and st.session_state.get("_g_q") != want:
        st.session_state["_g_q"] = want
        st.session_state["g_sel"] = want
    left, right = st.columns([2, 3])
    with left:
        pages = max(1, math.ceil(len(f) / PAGE))
        pg = st.number_input(f"Page (of {pages}) · {len(f):,} requests, newest first", 1, pages, 1, key="g_page")
        for r in f.iloc[(pg - 1) * PAGE:pg * PAGE].itertuples(index=False):
            tag = "" if r.on_time is True else " · no reply" if r.on_time is None else " · late"
            tag += " · consent" if r.needs_consent else ""
            if st.button(f"{L.LANG_FLAGS.get(r.language, '')} {L.label('asset', r.room_id)} · "
                         f"{L.REQUEST_TYPES.get(r.request_type, r.request_type)} · {C.local_time(r.t)}{tag}",
                         key=f"g_{r.request_id}", width="stretch",
                         type="primary" if st.session_state.get("g_sel") == r.request_id else "secondary"):
                st.session_state["g_sel"] = r.request_id
                st.query_params["request"] = r.request_id
                st.session_state["_g_q"] = r.request_id
                st.rerun()
    sel = st.session_state.get("g_sel")
    if sel not in set(th.request_id):
        sel = f.request_id.iloc[0]
    with right:
        thread(c, th[th.request_id == sel].iloc[0], consents, path)


if __name__ == "__main__":
    page()
