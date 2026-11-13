"""What we learned - T21 replaces this placeholder with insight cards."""
import streamlit as st

from viewer.views import common

path = common.ROOT / "insights.md"
if path.exists():
    st.markdown(path.read_text(encoding="utf-8"))
else:
    st.info("No insights generated yet.")
