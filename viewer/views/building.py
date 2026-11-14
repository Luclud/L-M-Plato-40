"""Building (start page): the hotel at one 15-minute moment of the replay - rooms, plant, staff, what needs attention."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from viewer import components as C
from viewer import core, twin
from viewer import labels as L
from viewer.views import common

COLOURS = {"occupied": "#D7E4F2", "vacant_clean": "#DDEEE3", "needs_cleaning": "#F6DDBC", "cleaning": "#F3E9B0",
           "unknown": "repeating-linear-gradient(135deg,#ECEAE3 0 6px,#E3E0D8 6px 12px)"}
EDGES = {"occupied": "#A9C2DE", "vacant_clean": "#AFD2BC", "needs_cleaning": "#E2B57C", "cleaning": "#D9C66A",
         "unknown": "#D3CFC5"}
ACCENT = "#0E6B57"
STATE_NAMES = {"occupied": "occupied", "vacant_clean": "vacant clean", "needs_cleaning": "needs cleaning",
               "cleaning": "cleaning", "unknown": "no data (unknown)"}
PLANT_NAMES = {"HP1": "Heat pump", "AHU1": "Ventilation", "WM_MAIN": "Water main", "PV1": "PV / battery"}
PLANT_STATUS = {"ok": "Running normally", "incident": "Open incident", "no_data": "No data"}


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
    with st.container(border=True, key="card_attention"):
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
            with y.container(key=f"right_att_{r.episode}"):
                st.page_link("views/incidents.py", label="Open", icon=":material/open_in_new:",
                             query_params={"episode": r.episode})
        if len(open_eps) > 6:
            st.caption(f"+{len(open_eps) - 6} more open incidents on the Incidents page.")


def floors(c: dict, rooms: pd.DataFrame, open_eps: pd.DataFrame, planned: dict):
    hot = set(a for x in (open_eps.assets if len(open_eps) else []) for a in x)
    css = []
    by_floor: dict[str, list] = {}
    for r in rooms.itertuples():
        by_floor.setdefault(r.room_id[1], []).append(r)
    for fl in sorted(by_floor, reverse=True):
        cols = st.columns([0.9] + [1] * len(by_floor[fl]))
        cols[0].markdown(f"<div class='floor-l'>Floor {fl}</div>", unsafe_allow_html=True)
        for col, r in zip(cols[1:], by_floor[fl]):
            num = r.room_id[1:]
            lab = f"{num} :red[●]" if r.room_id in hot or f"WM_{r.room_id}" in hot else num
            tip = f"{L.label('asset', r.room_id)} · {STATE_NAMES[r.state]}"
            border = f"1px solid {EDGES[r.state]}"
            if r.room_id in planned:
                when, res = planned[r.room_id]
                lab += f" · {_hm(when)}"
                tip += f" · cleaning {_hm(when)}" + (f" · {L.label('resource', res)}" if res else "")
                border = f"2px dashed {ACCENT}"
            css.append(f".st-key-tile_{r.room_id} button{{background:{COLOURS[r.state]};border:{border};"
                       f"min-height:2.6rem;padding:0 .2rem;font-weight:500;font-variant-numeric:tabular-nums}}"
                       f".st-key-tile_{r.room_id} button:hover{{border-color:{ACCENT};"
                       f"box-shadow:0 2px 8px rgba(29,36,33,.10)}}")
            if col.button(lab, key=f"tile_{r.room_id}", help=tip, width="stretch"):
                st.session_state["b_room"] = r.room_id
    st.html("<style>" + "".join(css) + "</style>")
    leg = "".join(f"<span><i class='sw' style='background:{COLOURS[s]};border-color:{EDGES[s]}'></i>"
                  f"{STATE_NAMES[s]}</span>" for s in twin.STATES)
    st.markdown(f"<div class='legend'>{leg}<span><i class='sw planned'></i>cleaning planned (time)</span>"
                f"<span><span class='dot'>●</span>open incident</span><span>· click a room for details</span></div>",
                unsafe_allow_html=True)
    if planned:
        st.caption("Planned cleanings (proposal, not yet done): " + " · ".join(
            f"{L.label('asset', r)} {_hm(w)}" + (f" ({L.label('resource', res)})" if res else "")
            for r, (w, res) in sorted(planned.items(), key=lambda kv: kv[1][0])))


def plant(status: dict):
    st.markdown("<div class='side-h'>Plant</div><div class='plant-list'>" + "".join(
        f"<div class='plant'><span class='name'>{PLANT_NAMES[tile]}</span>"
        f"<span class='state {s}'>{PLANT_STATUS[s]}</span></div>" for tile, s in status.items()) + "</div>",
        unsafe_allow_html=True)


def staff(sh: pd.DataFrame, t):
    head = f"<div class='side-h gap'>Staff today <span>as known at {_hm(t)}</span></div>"
    if sh.empty:
        st.markdown(head + "<div class='side-row muted'>No shifts published.</div>", unsafe_allow_html=True)
        return
    rows = []
    for r in sh.itertuples():
        name = L.label("resource", r.resource_id)
        if r.status == "scheduled":
            rows.append(f"<div class='side-row'><span>{name} ✓</span>"
                        f"<span class='num'>{_hm(r.start)}–{_hm(r.end)}"
                        f"</span></div>")
        else:
            st_ = r.status.replace("_", " ")
            known = f"known {_hm(r.known)}" if r.known is not None else ""
            rows.append(f"<div class='side-row warn'><span>{name} {st_}</span><span class='num'>{known}</span></div>")
    st.markdown(head + "".join(rows), unsafe_allow_html=True)


def today(n: dict):
    items = [("Guest replies", n["guest_request"]), (f"Energy plans ({L.SIMULATED.lower()})", n["energy_optimization"]),
             ("Supplies & waste", n["logistics"]), ("Cleanings scheduled", n["cleanings"])]
    st.markdown("<div class='side-h gap'>Today so far</div>" + "".join(
        f"<div class='side-row'><span>{k}</span><span class='num'>{v}</span></div>" for k, v in items),
        unsafe_allow_html=True)


def kpis(d: dict, t, n_rooms: int):
    """Headline numbers at the selected moment (the same series the charts below draw)."""
    def now(key, col):
        return _now_value(_upto(d[key][col], d[key].index, t), t)

    def f(v, fmt, unit=""):
        return "–" if v is None else f"{v:{fmt}}{unit}"

    load, pv, grid = now("energy", "Building load"), now("energy", "Solar (PV)"), now("energy", "From the grid")
    booked, dirty = now("rooms", "Booked"), now("rooms", "Needs cleaning")
    inside, outside = now("temps", "Rooms (average)"), now("temps", "Outdoors")
    water = now("water", "Whole building")
    tiles = [("Power use", f(load, ".0f", " kW"), f"{f(pv, '.0f', ' kW')} solar · {f(grid, '.0f', ' kW')} from the grid"),
             ("Rooms booked", f"{f(booked, '.0f')}<small> / {n_rooms}</small>", f"{f(dirty, '.0f')} need cleaning"),
             ("Room temperature", f(inside, ".1f", " °C"), f"average · {f(outside, '.1f', ' °C')} outdoors"),
             ("Water use", f(water, ".0f", " L"), "per 15 min · whole building")]
    st.markdown("<div class='kpis'>" + "".join(
        f"<div class='kpi'><div class='k'>{k}</div><div class='v'>{v}</div><div class='s'>{sub}</div></div>"
        for k, v, sub in tiles) + "</div>", unsafe_allow_html=True)


def room_panel(c: dict, room: str, t, day_start, day_end):
    store, df = c["store"], c["df"]
    with st.container(border=True, key="card_room"):
        a, b = st.columns([6, 1])
        a.markdown(f"**{L.label('asset', room)}** · {C.local_time(day_start)}")
        if b.container(key="right_b_close").button("Close", key="b_close", type="tertiary", icon=":material/close:"):
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
                st.plotly_chart(core.figure(ch, markers, height=220), width="stretch", theme=None,
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
            with y.container(key=f"right_room_{r.episode}"):
                st.page_link("views/incidents.py", label="Open", query_params={"episode": r.episode})


# ---------------------------------------------------------------------------- today in charts
# Fixed series order, validated with the dataviz palette checker on the paper ground (#F6F5F1): all six checks
# pass. The brand green #0E6B57 itself is too grey for a data line, so charts use a slightly richer step of it.
SERIES = ["#087A5A", "#C26A12", "#2F6BA8"]
INK, MUTED, LINE = "#1D2421", "#5F6B66", "#DEDAD0"


def _grid(day_start, day_end) -> pd.DatetimeIndex:
    return pd.date_range(day_start, day_end, freq="15min", inclusive="left")


def _upto(s: pd.Series, grid: pd.DatetimeIndex, t) -> pd.Series:
    """The series on the day's 15-min grid, known up to t only (later steps empty; missing stays missing)."""
    s = s[~s.index.duplicated()].reindex(grid)
    return s.where(s.index <= t)


@st.cache_data(show_spinner=False)
def day_series(data_dir: str, day_start: pd.Timestamp, day_end: pd.Timestamp, _store, _tw) -> dict[str, pd.DataFrame]:
    """One local day of building-wide measurements on the 15-min grid (whole day; cut at t when drawn)."""
    store, tw = _store, _tw
    grid = _grid(day_start, day_end)
    en = store.energy()
    en = en[(en.index >= day_start) & (en.index < day_end)]
    energy = pd.DataFrame({k: en[col] if col in en else pd.Series(dtype=float) for k, col in
                           (("Building load", "building_load_kw"), ("Solar (PV)", "pv_kw"),
                            ("From the grid", "grid_import_kw"))}).reindex(grid)
    tel = tw.day_telemetry(day_start, day_end)
    state = pd.Series([twin.room_state(h, p) for h, p in zip(tel.housekeeping_state, tel.presence)], index=tel.index)
    counts = pd.crosstab(tel.timestamp_utc, state).reindex(grid)
    b = store.d.bookings
    b = b[b.status == "confirmed"]
    booked = pd.Series([int(((b.arrival_utc <= g) & (b.departure_utc > g) & (b.created_utc <= g)).sum())
                        for g in grid], index=grid, dtype=float)
    dirty = counts.get("needs_cleaning", pd.Series(0.0, index=grid)).fillna(0)
    dirty[counts.drop(columns="unknown", errors="ignore").sum(axis=1, min_count=1).isna()] = float("nan")
    rooms = pd.DataFrame({"Booked": booked, "Needs cleaning": dirty}, index=grid)
    w = store.weather()
    temps = pd.DataFrame({"Rooms (average)": tel.groupby("timestamp_utc").temperature_c.mean(),
                          "Outdoors": w[(w.index >= day_start) & (w.index < day_end)]}).reindex(grid)
    water = store.main_water()
    water = pd.DataFrame({"Whole building": water[(water.index >= day_start) & (water.index < day_end)]})
    return {"energy": energy, "rooms": rooms, "temps": temps,
            "water": water[~water.index.duplicated()].reindex(grid)}


def _now_value(s: pd.Series, t) -> float | None:
    s = s[s.index <= t].dropna()
    return None if s.empty else float(s.iloc[-1])


def _fig(frame: pd.DataFrame, t, day_start, day_end, unit: str, fmt: str, ymin=None, step=False):
    """Compact day chart: thin lines up to t, a 'now' rule, end-of-line labels, one legend, crosshair hover."""
    import plotly.graph_objects as go

    loc = lambda x: pd.DatetimeIndex(x).tz_convert(L.TZ).tz_localize(None)   # noqa: E731
    fig = go.Figure()
    for i, (name, s) in enumerate(frame.items()):
        s = _upto(s, _grid(day_start, day_end), t)
        col = SERIES[i % len(SERIES)]
        fig.add_scatter(x=loc(s.index), y=s.values, name=name, mode="lines", connectgaps=False,
                        line=dict(color=col, width=2, shape="hv" if step else "linear"),
                        hovertemplate=f"{name}: %{{y:{fmt}}} {unit}<extra></extra>")
        v = _now_value(s, t)
        if v is not None and len(frame.columns) > 1:
            fig.add_scatter(x=[loc([t])[0]], y=[v], mode="markers", showlegend=False, hoverinfo="skip",
                            marker=dict(size=8, color=col, line=dict(color="#F6F5F1", width=2)))
    now = loc([t])[0]
    fig.add_vline(x=now, line=dict(color=MUTED, width=1, dash="dot"))
    ax = dict(gridcolor="#E7E4DC", zeroline=False, linecolor=LINE, tickfont=dict(color=MUTED, size=11),
              automargin=True, showticklabels=True)
    fig.update_layout(
        height=230, margin=dict(l=4, r=8, t=30, b=4), hovermode="x unified",
        showlegend=len(frame.columns) > 1,
        legend=dict(orientation="h", y=1.04, yanchor="bottom", x=0, xanchor="left", font=dict(size=11, color=INK)),
        font=dict(family="IBM Plex Sans, sans-serif", color=INK, size=12),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=LINE, font=dict(color=INK)),
        xaxis=dict(range=[loc([day_start])[0], loc([day_end])[0]], tickformat="%H:%M", dtick=3 * 3600 * 1000,
                   hoverformat="%H:%M", fixedrange=True, **ax),
        yaxis=dict(ticksuffix=f" {unit}" if len(unit) <= 3 else "", rangemode="tozero" if ymin == 0 else "normal",
                   fixedrange=True, **ax))
    return fig


def day_charts(c: dict, t, day_start, day_end):
    """Today in four charts, each drawn up to the selected moment."""
    d = day_series(str(c["data_dir"]), day_start, day_end, c["store"], get_twin(str(c["data_dir"])))

    st.markdown("##### Today in numbers")
    st.caption(f"Measured so far today, up to {_hm(t)}. Gaps in a line are missing data (unknown, not zero).")
    specs = [("Energy", "energy", "kW", ".1f", 0), ("Rooms", "rooms", "", ".0f", 0),
             ("Temperature", "temps", "°C", ".1f", None), ("Water use", "water", "L", ".0f", 0)]
    for row in (specs[:2], specs[2:]):
        cols = st.columns(2, gap="medium")
        for col, (title, key, unit, fmt, ymin) in zip(cols, row):
            with col, st.container(border=True, key=f"card_chart_{key}"):
                st.markdown(f"**{title}**")
                frame = d[key]
                if frame.where(frame.index.to_series() <= t, axis=0).dropna(how="all").empty:
                    st.caption("No measurements yet today.")
                    continue
                st.plotly_chart(_fig(frame, t, day_start, day_end, unit, fmt, ymin, step=key == "rooms"), width="stretch", theme=None,
                                config={"displayModeBar": False}, key=f"b_day_{key}")
    with st.expander("Today's numbers as a table", icon=":material/table:"):
        tab = pd.concat([d[k].add_suffix(f" ({u})") for k, u in
                         (("energy", "kW"), ("rooms", "rooms"), ("temps", "°C"), ("water", "L/15 min"))], axis=1)
        tab = tab[tab.index <= t]
        tab.index = tab.index.tz_convert(L.TZ).strftime("%H:%M")
        st.dataframe(tab.round(1).iloc[::-1], width="stretch", height=260)


c = common.ctx()
tw = get_twin(str(c["data_dir"]))
t = moment(c, tw)
st.subheader("Building")
controls(tw)
t = twin.floor_t(st.query_params.get("t", t))
day_start, day_end = twin.local_day(t)
open_eps = twin.open_at(spans(str(c["decisions"]), c["mtime"]), t)
pending = twin.pending_at(c["df"], list(open_eps.episode), t, c.get("approvals"))
d = day_series(str(c["data_dir"]), day_start, day_end, c["store"], tw)
kpis(d, t, len(tw.rooms))
with st.container(key="b_split"):            # its column gap is --tile-gap, the stats row's gap (style.css)
    main, side = st.columns([3.1, 1])
with main:
    attention(c, t, open_eps, pending)
    floors(c, tw.rooms_at(t), open_eps, twin.planned_cleanings(c["df"], t, day_end))
    if st.session_state.get("b_room"):
        room_panel(c, st.session_state["b_room"], t, day_start, day_end)
with side, st.container(border=True, key="card_side"):
    plant(tw.plant_at(t, open_eps))
    staff(twin.staff_at(tw.roster, t, day_start, day_end), t)
    today(twin.today_counts(c["df"], day_start, t))
day_charts(c, t, day_start, day_end)
