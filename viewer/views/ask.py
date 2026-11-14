"""Ask the building: questions about the loaded run, answered from the decision log and the building data
(read-only tools), with numbered sources and charts of every measurement looked up."""
from __future__ import annotations

import base64
from pathlib import Path

import pandas as pd
import streamlit as st

from viewer import copilot, core, twin
from viewer import labels as L
from viewer.views import common

BOT_AVATAR = str(Path(__file__).resolve().parents[1] / "assets" / "bot_avatar.png")


@st.cache_resource
def avatar_uri() -> str:
    return "data:image/png;base64," + base64.b64encode(Path(BOT_AVATAR).read_bytes()).decode()

LABELS = {"llm": L.AI_ANSWER, "cached:llm": f"{L.AI_ANSWER} · {L.CACHED_ANSWER}",
          "offline": L.OFFLINE_ANSWER, "cached:offline": f"{L.OFFLINE_ANSWER} · {L.CACHED_ANSWER}"}


@st.cache_data(show_spinner=False)
def suggestions(decisions: str, mtime: float, _df: pd.DataFrame) -> list[str]:
    return copilot.suggestions(_df)


@st.cache_resource(show_spinner=False)
def get_twin(data_dir: str, _data) -> twin.Twin:
    return twin.Twin(_data)


def moment(c: dict, tw: twin.Twin) -> pd.Timestamp:
    """'Now' = the moment selected on the Building page (same session), else its default moment."""
    raw = st.session_state.get("_b_t") or st.query_params.get("t")
    try:
        return twin.floor_t(raw) if raw else twin.default_moment(c["df"], tw.start)
    except ValueError:
        return twin.default_moment(c["df"], tw.start)


def chart(ch: dict, key: str):
    fig = core.figure(core.Chart(ch["title"], ch["series"], ch["unit"]), height=220)
    fig.update_xaxes(range=[core._local(ch["start"]), core._local(ch["end"])])
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False}, key=key)


def show(turn: dict):
    with st.chat_message("user"):
        st.markdown(turn["q"])
    with st.chat_message("assistant", avatar=BOT_AVATAR):
        if turn.get("error"):
            st.warning(f"{turn['error']} Showing the answer looked up without the AI model instead.",
                       icon=":material/cloud_off:")
        st.markdown(turn["md"])
        for i, ch in enumerate(turn.get("charts") or []):
            chart(ch, f"ask_chart_{turn['id']}_{i}")
        for s in turn["sources"]:
            st.page_link("views/incidents.py", label=f"[{s['n']}] {s['title']}", icon=":material/open_in_new:",
                         query_params={"episode": s["episode"]})
        st.caption(LABELS.get(turn["source"], L.OFFLINE_ANSWER))


c = common.ctx()
df = c["df"]
st.subheader("Ask the building")
client = copilot.default_client()
if client is None:
    st.info(L.ASK_NO_KEY, icon=":material/key_off:")
    with st.expander("Turn on the AI copilot", icon=":material/code:"):
        st.code("# .env (git-ignored), then restart the viewer\nLLM_PROVIDER=anthropic\nLLM_API_KEY=...\n"
                "LLM_MODEL=<Claude model id>", language="bash")
if df.empty:
    st.info("No decisions are loaded, so there is nothing to ask about yet.", icon=":material/inbox:")
    st.stop()

tw = get_twin(str(c["data_dir"]), c["data"])
now = moment(c, tw)
EXAMPLES = {
    "A room": ["How is Room 104 right now?", "What happened in Room 305?"],
    "The building": ["What is going on right now?", "Which rooms need cleaning?"],
    "Measurements": ["Show the heat pump today", "Show water use in Room 305 on 12 Jan"],
    "People and guests": ["Who is working on 24 Jan?", "Which guest requests came from Room 210?"],
    "Decisions": ["Which incidents still need approval?", "Why was a technician sent on 15 Jan?"],
}
SUGGEST_ICONS = [":material/meeting_room:", ":material/how_to_reg:", ":material/cleaning_services:",
                 ":material/bar_chart:"]

history = st.session_state.setdefault(f"ask:{c['decisions']}", [])
question = None
hero = st.empty()   # welcome + suggestions; cleared as soon as the first question is asked
if not history:
    with hero.container(key="ask_hero"):
        st.markdown(f"<div class='ask-hero'><img src='{avatar_uri()}' alt=''>"
                    "<div class='ask-hero-title'>What would you like to know?</div></div>", unsafe_allow_html=True)
        sugg = suggestions(str(c["decisions"]), c["mtime"], df)
        with st.container(key="ask_sugg"):
            for row in range(0, len(sugg), 2):
                cols = st.columns(2, gap="small")
                for i, (col, s) in enumerate(zip(cols, sugg[row:row + 2]), start=row):
                    if col.button(s, key=f"ask_suggest_{i}", icon=SUGGEST_ICONS[i % len(SUGGEST_ICONS)],
                                  width="stretch"):
                        question = s
        with st.container(key="right_ask_more"):
            with st.popover("More examples", icon=":material/help:"):
                for topic, qs in EXAMPLES.items():
                    st.markdown(f"**{topic}**  \n" + "  \n".join(f"“{q}”" for q in qs))
                st.caption("Read-only: the copilot cannot approve, decide or change anything.")
question = st.chat_input("Ask about a room, the building now, staff, arrivals, measurements or an incident …") \
    or question
if question:
    hero.empty()
if history:
    with st.container(key="right_ask_clear"):
        if st.button("Clear conversation", icon=":material/delete_sweep:", type="tertiary"):
            history.clear()
            st.rerun()

for turn in history:
    show(turn)
if question:
    tools = copilot.Tools(df, c.get("approvals"), data=c["data"], store=c["store"], twin=tw, now=now)
    past = [m for t in history for m in ({"role": "user", "content": t["q"]},
                                         {"role": "assistant", "content": t["raw"] or "-"})]
    with st.spinner("Looking it up …"):
        a = copilot.answer(question, tools, client, copilot.load_cache(), history=past)
    md, sources = copilot.render(a.text, df, tools.cited if a.source == "llm" else None)
    turn = {"q": question, "raw": a.text, "md": md, "sources": sources, "source": a.source, "error": a.error,
            "charts": list(tools.charts), "id": len(history)}
    history.append(turn)
    show(turn)
