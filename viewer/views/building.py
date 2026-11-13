"""Building (start page) - T19 replaces this placeholder."""
import streamlit as st  # noqa: F401

from viewer.views import classic_screens, common  # noqa: F401

classic_screens.render("Overview")
