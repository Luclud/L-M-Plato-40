"""Ask the building: questions about the loaded run, answered only from the decision log, with numbered sources."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from viewer import copilot
from viewer import labels as L
from viewer.views import common

LABELS = {"llm": L.AI_ANSWER, "cached:llm": f"{L.AI_ANSWER} · {L.CACHED_ANSWER}",
          "offline": L.OFFLINE_ANSWER, "cached:offline": f"{L.OFFLINE_ANSWER} · {L.CACHED_ANSWER}"}


@st.cache_data(show_spinner=False)
def suggestions(decisions: str, mtime: float, _df: pd.DataFrame) -> list[str]:
    return copilot.suggestions(_df)


def show(turn: dict):
    with st.chat_message("user"):
        st.markdown(turn["q"])
    with st.chat_message("assistant"):
        if turn.get("error"):
            st.warning(f"{turn['error']} Showing the answer looked up without the AI model instead.",
                       icon=":material/cloud_off:")
        st.markdown(turn["md"])
        for s in turn["sources"]:
            st.page_link("views/incidents.py", label=f"[{s['n']}] {s['title']}", icon=":material/open_in_new:",
                         query_params={"episode": s["episode"]})
        st.caption(LABELS.get(turn["source"], L.OFFLINE_ANSWER))


c = common.ctx()
df = c["df"]
st.subheader("Ask the building")
st.caption(L.ASK_INTRO)
client = copilot.default_client()
if client is None:
    st.info(L.ASK_NO_KEY, icon=":material/key_off:")
    with st.expander("Technical details — turn on the AI copilot", icon=":material/code:"):
        st.code("# .env (git-ignored), then restart the viewer\nLLM_PROVIDER=anthropic\nLLM_API_KEY=...\n"
                "LLM_MODEL=<Claude model id>", language="bash")
if df.empty:
    st.info("No decisions are loaded, so there is nothing to ask about yet.", icon=":material/inbox:")
    st.stop()

history = st.session_state.setdefault(f"ask:{c['decisions']}", [])
question = None
sugg = suggestions(str(c["decisions"]), c["mtime"], df)
for i, (col, s) in enumerate(zip(st.columns(len(sugg)), sugg)):
    if col.button(s, key=f"ask_suggest_{i}", use_container_width=True):
        question = s
question = st.chat_input("Ask about a room, a date, approvals or an incident type …") or question
if history and st.button("Clear conversation", icon=":material/delete_sweep:", type="tertiary"):
    history.clear()

for turn in history:
    show(turn)
if question:
    tools = copilot.Tools(df, c.get("approvals"))
    past = [m for t in history for m in ({"role": "user", "content": t["q"]},
                                         {"role": "assistant", "content": t["raw"] or "-"})]
    with st.spinner("Looking it up in the decision log …"):
        a = copilot.answer(question, tools, client, copilot.load_cache(), history=past)
    md, sources = copilot.render(a.text, df, tools.cited if a.source == "llm" else None)
    turn = {"q": question, "raw": a.text, "md": md, "sources": sources, "source": a.source, "error": a.error}
    history.append(turn)
    show(turn)
