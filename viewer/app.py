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

from viewer import labels as L  # noqa: E402
from viewer.views import common  # noqa: E402

st.set_page_config(page_title=L.APP_NAME, page_icon=":material/apartment:", layout="wide")
ctx = common.build_ctx()
n = common.pending_approvals(ctx)
pages = {
    "": [
        st.Page("views/building.py", title="Building", icon=":material/apartment:", default=True),
        st.Page("views/incidents.py", title="Incidents", icon=":material/report:"),
        st.Page("views/approvals.py", title=f"Approvals ({n})", icon=":material/how_to_reg:", url_path="approvals"),
        st.Page("views/learned.py", title="What we learned", icon=":material/lightbulb:"),
        st.Page("views/tour.py", title="Guided demo", icon=":material/play_circle:"),
        st.Page("views/ask.py", title="Ask the building", icon=":material/forum:"),
    ],
    "Proof": [
        st.Page("views/proof.py", title="Proof", icon=":material/verified:"),
    ],
}
st.navigation(pages, position="top").run()
