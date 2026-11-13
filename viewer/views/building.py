"""Building (start page): the hotel at one 15-minute moment of the replay - rooms, plant, staff, what needs attention."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from viewer import components as C
from viewer import core, twin
from viewer import labels as L
from viewer.views import common

COLOURS = {"occupied": "#90caf9", "vacant_clean": "#c8e6c9", "needs_cleaning": "#ffcc80", "cleaning": "#fff59d",
           "unknown": "#e0e0e0"}
STATE_NAMES = {"occupied": "occupied", "vacant_clean": "vacant clean", "needs_cleaning": "needs cleaning",
               "cleaning": "cleaning", "unknown": "no data (unknown)"}
PLANT_NAMES = {"HP1": "Heat pump", "AHU1": "Ventilation", "WM_MAIN": "Water main", "PV1": "PV / battery"}
PLANT_STATUS = {"ok": ("✓", "#2e7d32"), "incident": ("⚠ incident", "#c62828"), "no_data": ("no data", "#757575")}


@st.cache_resource(show_spinner=False)
def get_twin(data_path: str) -> twin.Twin:
    data, _ = common.load_data(data_path)
    return twin.Twin(data)


@st.cache_data(show_spinner=False)
def spans(path: str, mtime: float) -> pd.DataFrame:
    return twin.episode_spans(common.load_decisions(path, mtime))


def _hm(t) -> str:
    return pd.Timestamp(t).tz_convert(L.TZ).strftime("%H:%M")


def _set_t(t: pd.Timestamp):
    st.query_params["t"] = t.strftime("%Y-%m-%dT%H:%MZ")
    st.session_state["_b_t"] = st.query_params["t"]


def _on_time():
    day, hm = st.session_state["b_day"], st.session_state["b_time"]
    steps = {_hm(s): s for s in twin.day_steps(day)}
    _set_t(steps.get(hm) or next(iter(steps.values())))


def _on_day():
    steps = twin.day_steps(st.session_state["b_day"])
    cur = st.session_state.get("b_time")
    hit = [s for s in steps if _hm(s) == cur]
    _set_t(hit[0] if hit else steps[0])


def moment(c: dict, tw: twin.Twin) -> pd.Timestamp:
    """Selected moment from ``?t=`` (deep links from the tour); default = start of the most severe incident."""
    raw = st.query_params.get("t")
    try:
        t = twin.floor_t(raw) if raw else twin.default_moment(c["df"], tw.start)
    except ValueError:
        t = twin.default_moment(c["df"], tw.start)
    lo, hi = twin.floor_t(tw.start), twin.floor_t(tw.end)
    t = min(max(t, lo), hi)
    key = t.strftime("%Y-%m-%dT%H:%MZ")
    if st.session_state.get("_b_t") != key:          # first visit or deep link: sync widgets with the URL
        st.session_state["_b_t"] = key
        st.session_state["b_day"] = t.tz_convert(L.TZ).date()
        st.session_state["b_time"] = _hm(t)
        st.query_params["t"] = key
    return t


def controls(tw: twin.Twin):
    a, b = st.columns([1.2, 5])
    lo, hi = tw.start.tz_convert(L.TZ).date(), (tw.end - pd.Timedelta(minutes=1)).tz_convert(L.TZ).date()
    a.date_input("Day", min_value=lo, max_value=hi, key="b_day", on_change=_on_day, format="DD.MM.YYYY")
    opts = [_hm(s) for s in twin.day_steps(st.session_state["b_day"])]
    if st.session_state.get("b_time") not in opts:
        st.session_state["b_time"] = opts[0]
    b.select_slider("Time (local)", opts, key="b_time", on_change=_on_time)


def attention(c: dict, t, open_eps: pd.DataFrame, pending: dict):
    df = c["df"]
    with st.container(border=True):
        st.markdown("**Needs attention now**")
        if open_eps.empty:
            st.caption("Nothing open at this moment.")
            return
        for r in open_eps.head(6).itertuples():
            done = df[(df.episode == r.episode) & (df.t <= t)]
            status = (f"waiting for approval ({pending[r.episode]})" if pending.get(r.episode)
                      else f"we {L.label('action', done.action.iloc[-1]).lower()}" if len(done) else "")
            assets = L.assets_text(r.assets[:3]) + (f" +{len(r.assets) - 3} more" if len(r.assets) > 3 else "")
            x, y = st.columns([7, 1])
            x.markdown(f"{L.icon('issue', r.issue_type)} **{L.label('issue', r.issue_type)}** · {assets} · "
                       f"since {C.local_time(r.start)} · {status}")
            y.page_link("views/incidents.py", label="Open", icon=":material/open_in_new:",
                        query_params={"episode": r.episode})
        if len(open_eps) > 6:
            st.caption(f"+{len(open_eps) - 6} more open incidents — see Incidents.")


def floors(c: dict, rooms: pd.DataFrame, open_eps: pd.DataFrame, planned: dict):
    hot = set(a for x in (open_eps.assets if len(open_eps) else []) for a in x)
    css = []
    by_floor: dict[str, list] = {}
    for r in rooms.itertuples():
        by_floor.setdefault(r.room_id[1], []).append(r)
    for fl in sorted(by_floor, reverse=True):
        cols = st.columns([0.7] + [1] * len(by_floor[fl]))
        cols[0].markdown(f"**Floor {fl}**")
        for col, r in zip(cols[1:], by_floor[fl]):
            num = r.room_id[1:]
            lab = f"{num} :red[●]" if r.room_id in hot or f"WM_{r.room_id}" in hot else num
            tip = f"{L.label('asset', r.room_id)} · {STATE_NAMES[r.state]}"
            if r.state == "unknown":
                lab += " · no data"
            border = "1px solid #bbb"
            if r.room_id in planned:
                when, res = planned[r.room_id]
                lab += f" · {_hm(when)}"
                tip += f" · cleaning {_hm(when)}" + (f" · {L.label('resource', res)}" if res else "")
                border = "2px dashed #1565c0"
            css.append(f".st-key-tile_{r.room_id} button{{background:{COLOURS[r.state]};border:{border};"
                       f"min-height:2.4rem;padding:0 .2rem}}")
            if col.button(lab, key=f"tile_{r.room_id}", help=tip, width="stretch"):
                st.session_state["b_room"] = r.room_id
    st.html("<style>" + "".join(css) + "</style>")
    leg = " ".join(f"<span style='background:{COLOURS[s]};padding:1px 8px;border-radius:4px;margin-right:6px'>"
                   f"{STATE_NAMES[s]}</span>" for s in twin.STATES)
    st.markdown(f"<small>{leg}<span style='border:2px dashed #1565c0;padding:0 8px;border-radius:4px'>cleaning "
                f"planned (time)</span> <span style='color:#c62828'>●</span> open incident · click a room for "
                f"details</small>", unsafe_allow_html=True)
    if planned:
        st.caption("Planned cleanings (proposal, not yet done): " + " · ".join(
            f"{L.label('asset', r)} {_hm(w)}" + (f" ({L.label('resource', res)})" if res else "")
            for r, (w, res) in sorted(planned.items(), key=lambda kv: kv[1][0])))


def plant(status: dict):
    cols = st.columns(len(status))
    for col, (tile, s) in zip(cols, status.items()):
        mark, colour = PLANT_STATUS[s]
        col.markdown(f"<div style='border:1px solid #ddd;border-radius:8px;padding:6px 10px'>{PLANT_NAMES[tile]} "
                     f"<b style='color:{colour}'>{mark}</b></div>", unsafe_allow_html=True)


def staff(sh: pd.DataFrame, t):
    if sh.empty:
        st.caption(f"Staff today (as known at {_hm(t)}): no shifts published.")
        return
    parts = []
    for r in sh.itertuples():
        name = L.label("resource", r.resource_id)
        if r.status == "scheduled":
            parts.append(f"{name} ✓ {_hm(r.start)}–{_hm(r.end)}")
        else:
            st_ = r.status.replace("_", " ")
            parts.append(f"**{name} {st_}**" + (f" (known {_hm(r.known)})" if r.known is not None else ""))
    st.markdown(f"**Staff today** (as known at {_hm(t)}): " + " · ".join(parts))


def room_panel(c: dict, room: str, t, day_start, day_end):
    store, df = c["store"], c["df"]
    with st.container(border=True):
        a, b = st.columns([6, 1])
        a.markdown(f"**{L.label('asset', room)}** · {C.local_time(day_start)}")
        if b.button("Close", key="b_close", type="tertiary", icon=":material/close:"):
            st.session_state.pop("b_room", None)
            st.rerun()
        tel = store.telemetry(room)
        tel = tel[(tel.index >= day_start) & (tel.index < day_end)]
        water = store.room_water(room)
        water = water[(water.index >= day_start) & (water.index < day_end)]
        mine = df[df.affected_assets.map(lambda x: room in x or f"WM_{room}" in x) & (df.t >= day_start)
                  & (df.t < day_end)] if len(df) else df
        markers = [(x, L.label("action", y)) for x, y in zip(mine.t, mine.action)] if len(mine) else []
        charts = []
        if len(tel):
            charts.append(core.Chart("Temperature", {"temperature": tel.temperature_c, "setpoint": tel.setpoint_c},
                                     "°C", core.gap_intervals(tel.temperature_c, day_start, day_end)))
        if len(water):
            charts.append(core.Chart("Water use", {"water": water}, "L / 15 min",
                                     core.gap_intervals(water, day_start, day_end)))
        cols = st.columns(max(1, len(charts)))
        for col, ch in zip(cols, charts):
            with col:
                st.plotly_chart(core.figure(ch, markers, height=220), width="stretch",
                                config={"displayModeBar": False}, key=f"b_chart_{room}_{ch.title}")
        if not charts:
            st.caption("No measurements for this room on this day.")
        eps = spans(str(c["decisions"]), c["mtime"])
        eps = eps[eps.assets.map(lambda x: room in x or f"WM_{room}" in x)] if len(eps) else eps
        if eps.empty:
            st.caption("No incidents for this room in this run.")
        for r in eps.itertuples():
            x, y = st.columns([7, 1])
            x.markdown(f"{L.icon('issue', r.issue_type)} {L.label('issue', r.issue_type)} · "
                       f"{C.time_range(r.start, r.end)}")
            y.page_link("views/incidents.py", label="Open", query_params={"episode": r.episode})


c = common.ctx()
tw = get_twin(str(c["data_dir"]))
t = moment(c, tw)
st.subheader("Building")
controls(tw)
t = twin.floor_t(st.query_params.get("t", t))
day_start, day_end = twin.local_day(t)
open_eps = twin.open_at(spans(str(c["decisions"]), c["mtime"]), t)
pending = twin.pending_at(c["df"], list(open_eps.episode), t, c.get("approvals"))
attention(c, t, open_eps, pending)
floors(c, tw.rooms_at(t), open_eps, twin.planned_cleanings(c["df"], t, day_end))
plant(tw.plant_at(t, open_eps))
staff(twin.staff_at(tw.roster, t, day_start, day_end), t)
n = twin.today_counts(c["df"], day_start, t)
st.caption(f"Today so far: {n['guest_request']} guest replies · {n['energy_optimization']} energy plan(s) "
           f"({L.SIMULATED.lower()}) · {n['logistics']} supplies & waste · {n['cleanings']} cleanings scheduled")
if st.session_state.get("b_room"):
    room_panel(c, st.session_state["b_room"], t, day_start, day_end)
