"""Proof - T21 replaces this placeholder."""
import streamlit as st  # noqa: F401

from viewer.views import classic_screens, common  # noqa: F401

st.page_link(common.CLASSIC_PAGE, label="Classic views (developer screens)", icon=":material/history:")
classic_screens.render("Data quality & look-alikes")
