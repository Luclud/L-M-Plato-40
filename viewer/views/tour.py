"""Guided demo - T22 replaces this placeholder with the 8-step tour."""
import streamlit as st

from viewer.views import common

st.subheader("Guided demo")
st.info("The step-by-step guided demo is being built. Until then the hero scenarios are under Classic views.",
        icon=":material/play_circle:")
st.page_link(common.CLASSIC_PAGE, label="Open Classic views → Hero scenarios", icon=":material/history:")
