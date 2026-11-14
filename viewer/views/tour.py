"""Guided demo: 8 steps with Back / Next over the real pages (deep link ``?tour=N``).

Heroes: labelled datasets (quickstart) via ``eval/labels_<profile>.json``; otherwise the highest-priority episode
of each scenario class. Talking points live in ``viewer/tour.toml``."""
from __future__ import annotations

import ast
import runpy
from pathlib import Path

import pandas as pd
import streamlit as st

from viewer import core, twin
from viewer.views import common, incident

try:
    import tomllib as _toml
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as _toml
    except ModuleNotFoundError:
        _toml = None

VIEWS = Path(__file__).resolve().parent
TOML = VIEWS.parent / "tour.toml"
# step key -> (scenario class, issue type) for incident steps
SCENARIOS = {"water": ("S02", "water_leak"), "heating": ("S03", "heating_comfort"),
             "turnaround": ("S01", "room_turnaround"), "surge": ("S05", "occupancy_surge"),
             "filter": ("S04", "ventilation_maintenance")}
ORDER = ["building", "water", "heating", "turnaround", "surge", "filter", "learned", "proof"]


def steps(path: Path = TOML) -> list[dict]:
    """Talking points per step (missing file / entries fall back to the key as title)."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if _toml is not None:
        data = _toml.loads(text)
    else:
        import toml
        data = toml.loads(text)
    by_key = {s.get("key"): s for s in data.get("step", [])}
    return [{"key": k, "title": by_key.get(k, {}).get("title", k.title()), "say": by_key.get(k, {}).get("say", "")}
            for k in ORDER]


def heroes(df: pd.DataFrame, labels: list[dict]) -> dict[str, str | None]:
    """Episode per incident step: first label of the scenario with a matching episode, else most severe episode."""
    out = {}
    for key, (scen, it) in SCENARIOS.items():
        pick = None
        for lab in labels:
            if lab.get("scenario") == scen and lab.get("issue_type") == it:
                m = core.match_episodes(df, it, list(lab.get("assets", [])), lab.get("start_utc"), lab.get("end_utc"))
                if m:
                    pick = m[0]
                    break
        e = df[df.issue_type == it] if pick is None and len(df) else df.iloc[0:0]
        if len(e):
            g = e.assign(r=e.priority.map(core.PRIO_RANK).fillna(9)).groupby("episode").agg(
                r=("r", "min"), s=("t", "min"))
            pick = g.sort_values(["r", "s"]).index[0]
        out[key] = pick
    return out


@st.cache_data(show_spinner=False)
def cached_heroes(decisions: str, mtime: float, profile: str, _df: pd.DataFrame) -> dict[str, str | None]:
    return heroes(_df, core.load_labels(profile))


def page_helpers(name: str) -> dict:
    """Imports, functions and UPPER/_private constants of a page script, without running its page body."""
    path = VIEWS / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef))
            or (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and (t.id.isupper() or t.id.startswith("_"))
                                                  for t in n.targets))]
    ns = {"__name__": f"tour_{path.stem}", "__file__": str(path)}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), ns)
    return ns


def _go(n: int):
    st.query_params["tour"] = str(n)


def current(total: int) -> int:
    try:
        n = int(st.query_params.get("tour", 1))
    except ValueError:
        n = 1
    return min(max(n, 1), total)


def nav(n: int, total: int, where: str):
    b, mid, f = st.columns([1, 6, 1])
    b.button("Back", icon=":material/arrow_back:", disabled=n <= 1, on_click=_go, args=(n - 1,),
             key=f"tour_back_{where}", width="stretch")
    f.button("Next", icon=":material/arrow_forward:", disabled=n >= total, on_click=_go, args=(n + 1,),
             key=f"tour_next_{where}", type="primary", width="stretch")
    if where == "top":
        mid.markdown(" ".join(":blue[●]" if i == n else ":gray[○]" for i in range(1, total + 1)),
                     help="Step dots: you are at the blue one.")


def show_building(c: dict, water_ep: str | None, fresh: bool):
    if fresh:
        rows = c["df"][c["df"].episode == water_ep] if water_ep else c["df"].iloc[0:0]
        if len(rows):
            st.query_params["t"] = twin.floor_t(rows.t.min()).strftime("%Y-%m-%dT%H:%MZ")
    runpy.run_path(str(VIEWS / "building.py"), run_name="tour_building")


def show_learned():
    h = page_helpers("learned.py")
    rows = h["load_insights"](h["insights_dir"]())
    if not rows:
        st.info("No insights generated yet. This step is skipped.", icon=":material/info:")
        return
    cols = st.columns(2)
    for col, (target, title, _icon) in zip(cols, h["COLUMNS"]):
        mine = sorted((r for r in rows if str(r.get("target", "")).upper() == target),
                      key=lambda r: -float(r.get("confidence") or 0))
        with col:
            st.markdown(f"#### {title}")
            if mine:
                h["card"](mine[0])
    st.page_link("views/learned.py", label="All insights", icon=":material/lightbulb:")


c = common.ctx()
steps_ = steps()
total = len(steps_)
n = current(total)
fresh = st.session_state.get("_tour_last") != n
st.session_state["_tour_last"] = n
step = steps_[n - 1]
st.subheader("Guided demo")
nav(n, total, "top")
st.info(f"**{n} / {total} · {step['title']}**  \n{step['say']}", icon=":material/record_voice_over:")
hero = cached_heroes(str(c["decisions"]), c["mtime"], core.profile_of(c["data_dir"]), c["df"])
key = step["key"]
if key == "building":
    show_building(c, hero.get("water"), fresh)
elif key in SCENARIOS:
    ep = hero.get(key)
    if ep is None:
        st.warning("No matching incident in this dataset. Press Next to skip this step.",
                   icon=":material/skip_next:")
    else:
        incident.render(c, ep, key=f"tour{n}")
elif key == "learned":
    show_learned()
else:
    runpy.run_path(str(VIEWS / "proof.py"), run_name="tour_proof")
st.divider()
nav(n, total, "bottom")
