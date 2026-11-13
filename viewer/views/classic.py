"""Classic developer screens, reachable from Proof until T23 removes them."""
import streamlit as st  # noqa: F401

from viewer.views import classic_screens, common  # noqa: F401

classic_screens.render()
