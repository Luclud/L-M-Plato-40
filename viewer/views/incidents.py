"""Incidents: real incidents as cards (routine work as aggregates); ``?episode=<id>`` opens the story page."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from viewer import components as C
from viewer import labels as L
from viewer import summaries
from viewer.views import common, incident

PAGE = 20
_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@st.cache_data(show_spinner=False)
def episode_table(path: str, mtime: float) -> pd.DataFrame:
    """One row per episode with everything the list needs (cached per decisions file)."""
    df = common.load_decisions(path, mtime)
    if df.empty:
        return pd.DataFrame(columns=["episode", "issue_type", "start", "end", "assets", "priority", "autonomy",
                                     "needs_approval", "headline", "steps", "search"])
    out = []
    for ep, g in df.sort_values(["t", "event_id"]).groupby("episode", sort=False):
        assets = list(dict.fromkeys(a for x in g.affected_assets for a in x))
        names = [L.label("asset", a) for a in assets]
        out.append({
            "episode": ep, "issue_type": g.issue_type.iloc[0], "start": g.t.min(), "end": g.t.max(),
            "assets": assets, "priority": min(g.priority, key=lambda p: _SEV.get(p, 9)),
            "autonomy": incident.strictest(g), "needs_approval": bool(g.requires_human_approval.any()),
            "headline": C.headline(incident.humanize(g.reason.iloc[0])),
            "steps": " → ".join(dict.fromkeys(L.label("action", a).lower() for a in g.action)),
            "search": " ".join(assets + names).lower(),
        })
    return pd.DataFrame(out)


def _short(assets, n: int = 3) -> str:
    rest = len(assets) - n
    return L.assets_text(assets[:n]) + (f" +{rest} more" if rest > 0 else "")


def _open(ep: str):
    st.query_params["episode"] = ep


def card(r, key: str, summary: dict | None = None):
    with st.container(border=True, key=f"card_{key}_{r.episode}"):
        st.markdown(f"**{L.icon('issue', r.issue_type)} {L.label('issue', r.issue_type)} · "
                    f"{_short(r.assets)}** · {C.time_range(r.start, r.end)}")
        C.chips(r.priority, r.autonomy)
        if summary:
            summaries.show(summary)
        else:
            st.markdown(f"“{r.headline}”")
        x, y = st.columns([6, 1])
        x.caption(f"We: {r.steps}")
        y.container(key=f"right_{key}_{r.episode}").button("Open", key=f"{key}_{r.episode}",
                                                          icon=":material/open_in_new:", on_click=_open,
                                                          args=(r.episode,))


def page_list(c: dict):
    eps = episode_table(str(c["decisions"]), c["mtime"])
    inc = eps[eps.issue_type.isin(L.INCIDENT_TYPES)]
    counts = inc.issue_type.value_counts()
    opts = ["All"] + [t for t in L.INCIDENT_TYPES if counts.get(t, 0)]
    fmt = {"All": f"All {len(inc)}", **{t: f"{L.label('issue', t)} {counts.get(t, 0)}" for t in L.INCIDENT_TYPES}}
    pick = st.pills("Type", opts, default="All", format_func=fmt.get, key="inc_type", label_visibility="collapsed")
    f1, f2, f3 = st.columns([0.85, 3.45, 1.5], vertical_alignment="center")
    need = f1.toggle("Needs approval", key="inc_need")
    q = f2.text_input("Search room or asset", key="inc_q", placeholder="Search room or asset (e.g. 104, heat pump)",
                      label_visibility="collapsed")
    sort = f3.selectbox("Sort", ["Most severe", "Newest", "Oldest"], key="inc_sort", label_visibility="collapsed")
    sub = inc if pick in (None, "All") else inc[inc.issue_type == pick]
    if need:
        sub = sub[sub.needs_approval]
    if q.strip():
        sub = sub[sub.search.str.contains(q.strip().lower(), regex=False)]
    if sort == "Most severe":
        sub = sub.assign(_s=sub.priority.map(_SEV)).sort_values(["_s", "start"])
    else:
        sub = sub.sort_values("start", ascending=(sort == "Oldest"))
    if sub.empty:
        st.info("No incidents match." if len(inc) else "No incidents in this run.")
    else:
        pages = max(1, -(-len(sub) // PAGE))
        page = 1
        if pages > 1:
            page = st.segmented_control("Page", list(range(1, pages + 1)), default=1, key="inc_page") or 1
        st.caption(f"{L.n(len(sub), 'incident')}" + (f" · page {page} of {pages}" if pages > 1 else ""))
        summ = summaries.load(c["decisions"])
        for r in sub.iloc[(page - 1) * PAGE: page * PAGE].itertuples():
            card(r, "open", summ.get(r.episode))
    routine(c, eps)


def routine(c: dict, eps: pd.DataFrame):
    df = c["df"]
    rt = df[df.issue_type.isin(L.ROUTINE_TYPES)] if not df.empty else df
    n = {t: int((rt.issue_type == t).sum()) if len(rt) else 0 for t in L.ROUTINE_TYPES}
    if not len(rt):
        return
    langs = rt[rt.issue_type == "guest_request"].message.map(
        lambda m: (m or {}).get("language") if isinstance(m, dict) else None).dropna().nunique()
    parts = [f"{n['guest_request']:,} guest replies" + (f" in {langs} languages" if langs else ""),
             f"{n['energy_optimization']:,} energy plans ({L.SIMULATED.lower()})",
             f"{n['logistics']:,} supplies & waste", f"{n['data_quality']:,} data issues"]
    with st.expander("Routine work handled automatically: " + " · ".join(parts), icon=":material/autorenew:"):
        t = st.pills("Routine type", L.ROUTINE_TYPES, default="guest_request", key="rt_type",
                     format_func=lambda x: f"{L.label('issue', x)} {n[x]}", label_visibility="collapsed")
        sub = eps[eps.issue_type == (t or "guest_request")].sort_values("start", ascending=False)
        pages = max(1, -(-len(sub) // PAGE))
        page = st.number_input("Page", 1, pages, 1, key=f"rt_page_{t}") if pages > 1 else 1
        for r in sub.iloc[(page - 1) * PAGE: page * PAGE].itertuples():
            x, y = st.columns([8, 1])
            x.markdown(f"{C.local_time(r.start)} · {L.assets_text(r.assets)} · {r.headline}")
            y.container(key=f"right_rt_{r.episode}").button("Open", key=f"rt_{r.episode}", on_click=_open,
                                                            args=(r.episode,), type="tertiary")


c = common.ctx()
ep = st.query_params.get("episode")
if ep:
    def _back():
        st.query_params.pop("episode", None)
    st.button("All incidents", icon=":material/arrow_back:", on_click=_back, type="tertiary")
    incident.render(c, ep)
else:
    st.subheader("Incidents")
    page_list(c)
