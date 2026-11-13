"""Incident summaries written offline by ``scripts/summarise_incidents.py`` (T24); shown if the file exists."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from viewer import labels as L


def path_for(decisions) -> Path:
    p = Path(decisions)
    return p.with_name(f"{p.stem}_summaries.json")


@st.cache_data(show_spinner=False)
def _load(path: str, mtime: float) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    s = doc.get("summaries") if isinstance(doc, dict) else None
    return {k: v for k, v in (s or {}).items() if isinstance(v, dict) and isinstance(v.get("text"), str)}


def load(decisions) -> dict:
    p = path_for(decisions)
    return _load(str(p), p.stat().st_mtime) if p.is_file() else {}


def show(entry: dict | None):
    """Summary text; LLM-written ones carry the AI label, template ones none."""
    if not entry:
        return
    st.markdown(entry["text"])
    if entry.get("source") == "llm":
        st.caption(L.AI_SUMMARY, help="Written by an AI model from this incident's facts only; checked so it adds no "
                                      "number, time, room or claim that is not in the decision log.")
