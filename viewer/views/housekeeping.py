"""Housekeeping: who cleans which room when, deadlines and re-plans for one local day (T28)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from viewer import labels as L
from viewer import schedule as S
from viewer import twin
from viewer.views import common

ACCENT = "#0E6B57"
ROBOT = "#5B7FB8"
CLEAN = "#0E6B57"
ONCALL = "#B8743A"
LANE_COLOUR = {"cleaning": CLEAN, "robot": ROBOT, "oncall": ONCALL}
X0, X1 = "06:00", "22:00"


@st.cache_resource(show_spinner=False, max_entries=64)
def get_day(data_path: str, decisions: str, mtime: float, day) -> S.Day:
    data, _ = common.load_data(data_path)
    return S.Day(data, common.load_decisions(decisions, mtime), day)


def _hm(t) -> str:
    return pd.Timestamp(t).tz_convert(L.TZ).strftime("%H:%M")


def _loc(t):
    return pd.Timestamp(t).tz_convert(L.TZ).tz_localize(None)


def lane_name(res: str) -> str:
    return L.label("resource", res)


def figure(day: S.Day, as_of=None, moment=None, highlight_rooms=(), highlight_res=(), show_changes=False,
           height=430) -> go.Figure:
    lanes = ["Deadlines"] + [lane_name(r) for r in S.LANES]
    fig = go.Figure()
    hl_rooms, hl_res = set(highlight_rooms), set(highlight_res)

    def bar(res_lane, a, b, colour, text, hover, line=None, opacity=1.0, pattern=None, width=0.62):
        a, b = _loc(a), _loc(b)
        fig.add_trace(go.Bar(
            y=[res_lane], x=[(b - a).total_seconds() * 1000], base=[a], orientation="h", width=width,
            marker=dict(color=colour, opacity=opacity, line=line or dict(width=0),
                        pattern=pattern or {}),
            text=[text], textposition="inside", insidetextanchor="middle", textfont=dict(size=11),
            hovertext=[hover], hoverinfo="text", showlegend=False))

    sh = day.shifts(as_of)
    for r in sh.itertuples():
        lane = lane_name(r.resource_id)
        if r.absent:
            lab = f"{r.status.replace('_', ' ')}" + (f", known {_hm(r.known_at)}" if r.known_at is not None else "")
            bar(lane, r.start, r.end, "rgba(200,60,60,.18)", lab, f"{lane}: {lab}",
                line=dict(color="rgba(200,60,60,.7)", width=1), pattern=dict(shape="/", fgcolor="rgba(200,60,60,.55)"),
                width=0.8)
            if r.known_at is not None:
                fig.add_trace(go.Scatter(x=[_loc(r.known_at)], y=[lane], mode="markers",
                                         marker=dict(symbol="triangle-down", size=11, color="#C83C3C"),
                                         hovertext=[f"{lane} {r.status.replace('_', ' ')}, known {_hm(r.known_at)}"],
                                         hoverinfo="text", showlegend=False))
        else:
            what = "on call" if r.status == "on_call" else "on shift"
            bar(lane, r.start, r.end, "rgba(14,107,87,.07)", "", f"{lane} {what} {_hm(r.start)}–{_hm(r.end)}",
                width=0.8)
    plans = day.planned_blocks(as_of)
    if not show_changes and len(plans):
        plans = plans[~plans.superseded]
    for r in plans.itertuples():
        lane = lane_name(r.resource_id)
        hl = (r.room in hl_rooms) or (r.resource_id in hl_res)
        state = "earlier plan (changed later)" if r.superseded else "planned"
        hover = f"{lane} · {r.label} · {_hm(r.start)}–{_hm(r.end)} · {state} (known {_hm(r.known_at)})"
        dash = dict(color=ACCENT if not r.superseded else "rgba(14,107,87,.35)", width=3 if hl else 1.5)
        bar(lane, r.start, r.end, "rgba(0,0,0,0)", r.label if not r.superseded else "", hover,
            line=dash, opacity=0.5 if r.superseded else 1.0, width=0.42 if r.kind != "bracket" else 0.3)
    act = day.actual_blocks(as_of)
    for r in act.itertuples():
        lane = lane_name(r.resource_id)
        hl = (r.room in hl_rooms) or (r.resource_id in hl_res)
        kind = {"robot": "robot floor clean", "oncall": "on-call cleaning", "cleaning": "cleaning"}[r.kind]
        hover = f"{lane} · {r.label} · {_hm(r.start)}–{_hm(r.end)} · actual {kind}"
        bar(lane, r.start, r.end, LANE_COLOUR[r.kind], r.label, hover, opacity=1.0 if hl or not hl_rooms else 0.45,
            line=dict(color="#1D2421", width=2) if hl else None, width=0.42)
    dl = day.deadlines(as_of)
    sym = {"checkin": ("line-ns", "#555"), "early_checkin": ("diamond", "#E0A030"), "waiting": ("x", "#C83C3C"),
           "released": ("star", "#2E8B57"), "checkout": ("triangle-right", "#8A8A8A")}
    for kind, g in (dl.groupby("kind") if len(dl) else []):
        s, c = sym.get(kind, ("circle", "#555"))
        fig.add_trace(go.Scatter(x=[_loc(t) for t in g.t], y=["Deadlines"] * len(g), mode="markers",
                                 marker=dict(symbol=s, size=12, color=c, line=dict(width=2, color=c)),
                                 hovertext=[f"{lab} · {_hm(t)}" for lab, t in zip(g.label, g.t)], hoverinfo="text",
                                 name=L.SCHEDULE_MARKS.get(kind, kind), showlegend=True))
    ci = dl[dl.kind == "checkin"] if len(dl) else dl
    for t in (ci.t if len(ci) else []):
        fig.add_vline(x=_loc(t), line=dict(color="#555", width=1, dash="dot"))
    for r in day.replans(as_of).itertuples():
        fig.add_vline(x=_loc(r.t), line=dict(color="#C83C3C", width=1.5, dash="dash"))
        fig.add_annotation(x=_loc(r.t), y=1.0, yref="paper", text=r.label, showarrow=False, xanchor="left",
                           yanchor="bottom", font=dict(size=11, color="#C83C3C"), bgcolor="rgba(255,255,255,.85)")
    if moment is not None:
        fig.add_vline(x=_loc(moment), line=dict(color=ACCENT, width=2.5))
        fig.add_annotation(x=_loc(moment), y=1.0, yref="paper", text=f"selected {_hm(moment)}", showarrow=False,
                           xanchor="right", yanchor="bottom", font=dict(size=11, color=ACCENT),
                           bgcolor="rgba(255,255,255,.85)")
    x0 = _loc(S.at_local(day.day, X0))
    x1 = _loc(S.at_local(day.day, X1))
    fig.update_layout(barmode="overlay", height=height, margin=dict(l=150, r=10, t=40, b=36), plot_bgcolor="white",
                      paper_bgcolor="white", legend=dict(orientation="h", y=-0.12, font=dict(size=11)),
                      hoverlabel=dict(bgcolor="white"))
    fig.update_xaxes(type="date", range=[x0, x1], tickformat="%H:%M", dtick=3600000, gridcolor="#EEE")
    fig.update_yaxes(automargin=True, categoryorder="array", categoryarray=list(reversed(lanes)), gridcolor="#F4F4F4")
    return fig


def summary_text(s: dict) -> str:
    parts = [f"{s['rooms']} rooms to turn over",
             f"{s['demand_min']:,} min of cleaning vs {s['staff_min']:,} min of staff"
             + (" (more work than staff)" if s["over"] else "")]
    if s["absent"]:
        parts.append(f"{L.n(s['absent'], 'absence')}")
    parts.append(f"{L.n(s['waited'], 'guest')} waited" if s["waited"] else "no guest waited")
    if s["oncall"]:
        parts.append(f"on-call called {'once' if s['oncall'] == 1 else str(s['oncall']) + ' times'}")
    return " · ".join(parts)


def render_day(c: dict, day, moment=None, key="hk", known_only=True, show_changes=False, highlight_rooms=(),
               highlight_res=()):
    """Shared chart (Housekeeping page and the incident story)."""
    d = get_day(str(c["data_dir"]), str(c["decisions"]), c["mtime"], day)
    as_of = moment if known_only and moment is not None else None
    sh = d.shifts(as_of)
    if sh.empty:
        st.info(L.SCHEDULE_EMPTY)
        return d
    s = d.day_summary(as_of)
    st.markdown(f"**{summary_text(s)}**" + (f" · as known at {_hm(as_of)}" if as_of is not None else ""))
    st.plotly_chart(figure(d, as_of, moment, highlight_rooms, highlight_res, show_changes), width="stretch",
                    theme=None, key=f"{key}_chart", config={"displayModeBar": False})
    return d


def turnovers(c: dict, d: S.Day, as_of=None):
    plans = d.planned_blocks(as_of)
    act = d.actual_blocks(as_of)
    rooms = sorted(set(plans.room.dropna()) | set(act[act.kind == "cleaning"].room) | set(act[act.kind == "oncall"].room))
    st.markdown(f"**{L.SCHEDULE_TURNOVERS}**")
    if not rooms:
        st.caption("No turnovers on this day.")
        return
    for room in rooms:
        p = plans[(plans.room == room) & ~plans.superseded]
        a = act[act.room == room]
        bits = []
        if len(a):
            bits.append("done " + ", ".join(f"{lane_name(x.resource_id)} {_hm(x.start)}–{_hm(x.end)}"
                                             for x in a.itertuples()))
        if len(p):
            bits.append("planned " + ", ".join(f"{lane_name(x.resource_id)} {_hm(x.start)}–{_hm(x.end)}"
                                                for x in p.itertuples()))
        eps = [e for e in p.episode.dropna().unique()] if len(p) else []
        x, y = st.columns([7, 1])
        x.markdown(f"{L.label('asset', room)} · " + " · ".join(bits))
        if eps:
            with y.container(key=f"right_hk_{room}"):
                st.page_link("views/incidents.py", label="Open", icon=":material/open_in_new:",
                             query_params={"episode": eps[-1]})


def _moment(c, tw) -> pd.Timestamp:
    raw = st.query_params.get("t")
    try:
        t = twin.floor_t(raw) if raw else twin.default_moment(c["df"], tw.start)
    except ValueError:
        t = twin.default_moment(c["df"], tw.start)
    return min(max(t, twin.floor_t(tw.start)), twin.floor_t(tw.end))


def _on_change():
    day, hm = st.session_state["hk_day"], st.session_state["hk_time"]
    steps = {_hm(s): s for s in twin.day_steps(day)}
    t = steps.get(hm) or next(iter(steps.values()))
    st.query_params["t"] = t.strftime("%Y-%m-%dT%H:%MZ")
    st.session_state["_hk_t"] = st.query_params["t"]


class _Bounds:
    def __init__(self, data):
        self.start, self.end = pd.Timestamp(data.start), pd.Timestamp(data.end)


def page():
    c = common.ctx()
    st.subheader("Housekeeping")
    data, _ = common.load_data(str(c["data_dir"]))
    start, end = pd.Timestamp(data.start), pd.Timestamp(data.end)
    t = _moment(c, _Bounds(data))
    key = t.strftime("%Y-%m-%dT%H:%MZ")
    if st.session_state.get("_hk_t") != key:
        st.session_state["_hk_t"] = key
        st.session_state["hk_day"] = t.tz_convert(L.TZ).date()
        st.session_state["hk_time"] = _hm(t)
    a1, a2, a3, a4 = st.columns([1.2, 3, 2, 1.6])
    lo, hi = start.tz_convert(L.TZ).date(), (end - pd.Timedelta(minutes=1)).tz_convert(L.TZ).date()
    a1.date_input("Day", min_value=lo, max_value=hi, key="hk_day", on_change=_on_change, format="DD.MM.YYYY")
    opts = [_hm(s) for s in twin.day_steps(st.session_state["hk_day"])]
    if st.session_state.get("hk_time") not in opts:
        st.session_state["hk_time"] = opts[0]
    a2.select_slider("Time (local)", opts, key="hk_time", on_change=_on_change)
    with a3.container(key="hk_switch_known"):   # style.css: pushed down to the slider's track
        known = st.toggle(L.SCHEDULE_KNOWN_ONLY, value=True, key="hk_known")
    with a4.container(key="hk_switch_changes"):
        changes = st.toggle(L.SCHEDULE_CHANGES, value=False, key="hk_changes")
    steps = {_hm(s): s for s in twin.day_steps(st.session_state["hk_day"])}
    t = steps.get(st.session_state["hk_time"], t)
    d = render_day(c, st.session_state["hk_day"], t, key="hk", known_only=known, show_changes=changes)
    if d is not None and not d.shifts(t if known else None).empty:
        turnovers(c, d, t if known else None)


if __name__ == "__main__":
    page()
