"""T30 "Next building": PLATO lessons pinned to a drawing of the fictional PLATO-40 building (CLARA layer optional)."""
from __future__ import annotations

import json

import streamlit as st

from viewer import components as C
from viewer import core
from viewer import design_board as D
from viewer import labels as L
from viewer.views import common
from viewer.views.incident import confidence_word

GENERATE = "python analysis/insights.py data/dev --out insights.md --jsonl insights.jsonl"
STATUS = {"confirmed": "Confirmed by the data", "qualified": "Holds with qualifications"}
PRIO = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load_insights(d) -> list[dict]:
    p = d / "insights.jsonl"
    out = []
    for line in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def empty_state():
    st.info("The lessons from the PLATO-40 year are not loaded in this installation yet.", icon=":material/info:")
    if (common.DEV_DATA / "manifest.json").exists():
        if st.button("Create the lessons now (about 10 seconds)", icon=":material/auto_awesome:", type="primary",
                     key="gen_insights_design"):
            with st.spinner("Analysing the 365-day dev year…"):
                ok, log = common.generate_insights()
            if ok:
                st.rerun()
            st.error("The lessons could not be created.", icon=":material/error:")
            C.tech_details({"log": log})
    with st.expander("Technical details", icon=":material/code:"):
        st.code(GENERATE, language="bash")


def related(df, issue_types: list[str], limit: int = 3):
    ep = core.episodes(df)
    ep = ep[ep.issue_type.isin(issue_types)] if len(ep) else ep
    if ep.empty:
        return ep
    return ep.assign(_p=ep.priority.map(PRIO).fillna(9)).sort_values(["_p", "start"]).head(limit)


def card(r: dict, pin: dict, df):
    with st.container(border=True):
        conf = confidence_word(r.get("confidence"))
        st.markdown(f"**{pin['label']}. {r.get('topic', '')}**"
                    + (f" &nbsp; :blue-badge[{conf}]" if conf else "")
                    + (f" &nbsp; :gray-badge[{STATUS.get(r.get('status'), r.get('status'))}]" if r.get("status") else ""))
        st.caption(f":material/location_on: {pin['element_name']}")
        st.markdown(D.first_sentence(r.get("finding")))
        if r.get("recommendation"):
            st.markdown(f":material/lightbulb: **Recommendation:** {r['recommendation']}")
        if r.get("expected_benefit"):
            st.caption(f"Expected benefit (estimate, not measured): {r['expected_benefit']}")
        with st.expander("Show full evidence", icon=":material/fact_check:"):
            st.markdown(r.get("finding", ""))
            for k, title in (("method", "How we checked"), ("uncertainty", "How sure we are")):
                if r.get(k):
                    st.markdown(f"**{title}:** {r[k]}")
        eps = related(df, pin["incidents"]) if pin["incidents"] else []
        if len(eps):
            st.markdown("**Related incidents in this run:**")
            for e in eps.itertuples(index=False):
                st.page_link("views/incidents.py", query_params={"episode": e.episode}, icon=L.icon("issue", e.issue_type),
                             label=f"{L.label('issue', e.issue_type)} · {L.assets_text(e.assets)} · "
                                   f"{C.local_time(e.start)}")
        C.tech_details({k: r.get(k) for k in ("insight_id", "status", "evidence", "numbers")} | {"pin": pin})


def page():
    c = common.ctx()
    st.subheader("Next building")
    board = D.load_board()
    st.caption(board.get("caption", "PLATO-40 (fictional)") + " Not facts about real PLATO buildings.")
    rows = load_insights(common.insights_dir())
    if not rows:
        empty_state()
        return
    want = st.query_params.get("pin")
    clara_ids = [p["id"] for p in D.pins(rows, board, ("CLARA",))[0]]
    if want in clara_ids and st.session_state.get("_design_q") != want:
        st.session_state["design_clara"] = True                      # a CLARA pin link opens with its layer shown
    clara = st.toggle("Show operations lessons (CLARA)", value=False, key="design_clara")
    layers = ("PLATO", "CLARA") if clara else ("PLATO",)
    pins, unpinned, unknown = D.pins(rows, board, layers)
    ids = [p["id"] for p in pins]
    if want in ids and st.session_state.get("_design_q") != want:
        st.session_state["_design_q"] = want
        st.session_state["design_pin"] = want
    if st.session_state.get("design_pin") not in ids:
        st.session_state["design_pin"] = ids[0] if ids else None
    sel = st.session_state.get("design_pin")
    data, _ = common.load_data(str(c["data_dir"]))
    st.markdown(D.svg(data.building, pins, board, selected=sel), unsafe_allow_html=True)
    st.caption("Circles = lessons for the next PLATO building · squares = operations lessons for CLARA · colour = "
               "confidence: green high, amber medium, red low · click a pin or a number below")
    by_id = {r.get("insight_id"): r for r in rows}
    left, right = st.columns([2, 3])
    with left:
        st.markdown("#### Lessons on the drawing")
        st.radio("Lesson", ids, key="design_pin", label_visibility="collapsed",
                 format_func=lambda i: f"{D.pin_label(i)}. {by_id[i].get('topic', '')}")
        if unpinned:
            st.caption("Not on the drawing (no pin in design_board.toml): " + ", ".join(
                by_id[i].get("topic", i) for i in unpinned))
        if unknown:
            st.caption("Pinned but not in the lessons file: " + ", ".join(unknown))
    with right:
        if sel:
            card(by_id[sel], next(p for p in pins if p["id"] == sel), c["df"])


if __name__ == "__main__":
    page()
