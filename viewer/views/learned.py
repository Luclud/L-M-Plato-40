"""What we learned: Level 3 insight cards for PLATO (design) and CLARA (operations), target architecture, limits."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import streamlit as st

from viewer import components as C
from viewer.views import common
from viewer.views.incident import confidence_word

GENERATE = "python analysis/insights.py data/dev --out insights.md --jsonl insights.jsonl"
COLUMNS = [("PLATO", "For the next PLATO building", ":material/domain_add:"),
           ("CLARA", "For CLARA next year", ":material/tune:")]
_PATH = re.compile(r"\s*`[^`]*\.(?:md|jsonl?|csv|py)`(?:\s*§\s*\d+)?,?")


def insights_dir() -> Path:
    return Path(os.environ.get("PLATO40_INSIGHTS_DIR") or common.ROOT)


def load_insights(d: Path) -> list[dict]:
    p = d / "insights.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    m = re.search(r"(?<=[a-z0-9)%°])\.(?:\s|$)", text)
    return text[: m.start() + 1] if m else text


def section(md: str, title: str) -> str:
    """Body of the `## <title>...` section of insights.md, without file references."""
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", md, flags=re.M | re.S)
    if not m:
        return ""
    body = "\n".join(x for x in m.group(1).splitlines() if not x.startswith("Starting point:"))
    return _PATH.sub("", body).strip()


def card(r: dict):
    with st.container(border=True):
        conf = confidence_word(r.get("confidence"))
        st.markdown(f"**{r.get('topic', '')}**" + (f" &nbsp; :blue-badge[{conf}]" if conf else ""))
        st.markdown(first_sentence(r.get("finding")))
        if r.get("recommendation"):
            st.markdown(f":material/lightbulb: **Recommendation:** {r['recommendation']}")
        if r.get("expected_benefit"):
            st.caption(f"Expected benefit (estimate, not measured): {r['expected_benefit']}")
        with st.expander("Show full evidence", icon=":material/fact_check:"):
            st.markdown(r.get("finding", ""))
            for k, title in (("method", "How we checked"), ("uncertainty", "How sure we are")):
                if r.get(k):
                    st.markdown(f"**{title}:** {r[k]}")
        C.tech_details({k: r.get(k) for k in ("insight_id", "status", "hypothesis", "evidence", "numbers")})


st.subheader("What we learned")
st.caption("Lessons from one synthetic year of the fictional PLATO-40 hotel — for the next building and for running "
           "this one better. Not facts about real PLATO buildings or CLARA installations.")
d = insights_dir()
rows = load_insights(d)
if not rows:
    st.info("No insights generated yet. Create them with:", icon=":material/info:")
    st.code(GENERATE, language="bash")
else:
    cols = st.columns(2)
    for col, (target, title, icon) in zip(cols, COLUMNS):
        mine = [r for r in rows if str(r.get("target", "")).upper() == target]
        with col:
            st.markdown(f"#### {title}")
            st.caption(f"{len(mine)} insight(s)")
            for r in mine:
                card(r)
md = (d / "insights.md").read_text(encoding="utf-8") if (d / "insights.md").exists() else ""
arch, lim = section(md, "Target architecture"), section(md, "Limitations")
if arch:
    st.markdown("#### What the next building's operations system looks like")
    st.markdown(arch)
if lim:
    st.markdown("#### Limitations")
    st.markdown(lim)
