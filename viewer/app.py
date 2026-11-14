"""Viewer entry point: a thin router with top navigation.

    streamlit run viewer/app.py -- --decisions out/qs.jsonl --data data/quickstart

Pages live in viewer/views/*.py; wording in viewer/labels.py. The viewer only reads pipeline outputs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from plato40 import llm  # noqa: E402  (reads .env; nothing is sent)
from viewer import labels as L  # noqa: E402
from viewer.views import common  # noqa: E402

ASSETS = Path(__file__).parent / "assets"

# Show the "Guided demo" tab. Set to False (or STEWARD_GUIDED_DEMO=0 in the environment / .env) to hide it,
# e.g. while recording the video.
SHOW_GUIDED_DEMO = True


def guided_demo_enabled() -> bool:
    raw = llm._cfg("STEWARD_GUIDED_DEMO").lower()
    return SHOW_GUIDED_DEMO if not raw else raw not in ("0", "false", "no", "off")


st.set_page_config(page_title=L.APP_NAME, page_icon=str(ASSETS / "bot_avatar.png"), layout="wide")
st.html("<style>" + (Path(__file__).parent / "style.css").read_text(encoding="utf-8") + "</style>")
ctx = common.build_ctx()
n = common.pending_approvals(ctx)
pages = {
    "": [
        st.Page("views/building.py", title="Building", icon=":material/apartment:", default=True),
        st.Page("views/housekeeping.py", title="Housekeeping", icon=":material/cleaning_services:"),
        st.Page("views/incidents.py", title="Incidents", icon=":material/report:"),
        st.Page("views/approvals.py", title=f"Approvals ({n})", icon=":material/how_to_reg:", url_path="approvals"),
        st.Page("views/learned.py", title="What we learned", icon=":material/lightbulb:"),
        *([st.Page("views/tour.py", title="Guided demo", icon=":material/play_circle:")]
          if guided_demo_enabled() else []),
        st.Page("views/ask.py", title="Ask the building", icon=":material/forum:"),
        st.Page("views/proof.py", title="Proof", icon=":material/verified:"),
    ],
}
st.navigation(pages, position="top").run()
