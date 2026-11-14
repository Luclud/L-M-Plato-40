"""What we learned: Level 3 insight cards for PLATO (design) and CLARA (operations), limits."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import streamlit as st

from viewer import components as C
from viewer import insights_ai
from viewer import labels as L
from viewer.views import common
from viewer.views.incident import confidence_word

GENERATE = "python analysis/insights.py data/dev --out insights.md --jsonl insights.jsonl"
COLUMNS = [("PLATO", "For the next PLATO building", ":material/domain_add:"),
           ("CLARA", "For CLARA next year", ":material/tune:")]
_PATH = re.compile(r"\s*`[^`]*\.(?:md|jsonl?|csv|py)`(?:\s*§\s*\d+)?,?")


def insights_dir() -> Path:
    return common.insights_dir()


# How far a dataset supports an insight (analysis/insights.py "status"); confirmed needs no badge.
STATUS_BADGE = {"qualified": ":orange-badge[partly supported]", "rejected": ":gray-badge[not supported by this data]",
                "insufficient data": ":gray-badge[not testable on this data]"}
WEAK = {"rejected", "insufficient data"}


def dataset_line(data_dir: Path) -> str:
    """'Quickstart (21 days)' from the dataset's manifest."""
    try:
        m = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        m = {}
    name = common.PROFILE_NAMES.get(str(m.get("profile")), m.get("profile") or data_dir.name)
    days = m.get("days") or m.get("n_days")
    if not days:
        try:
            import pandas as pd
            days = (pd.Timestamp(m["end_utc"]) - pd.Timestamp(m["start_utc"])).days
        except Exception:  # noqa: BLE001 - manifest without a time range
            days = None
    return f"{name} ({days} days)" if days else str(name)


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


def card(r: dict, ai: dict | None = None):
    """One insight; with ``ai`` the plain-language AI wording leads and the analysis text stays under the evidence."""
    with st.container(border=True):
        conf = confidence_word(r.get("confidence"))
        head = ai["headline"] if ai else r.get("topic", "")
        status = str(r.get("status") or "").lower()
        badge = STATUS_BADGE.get(status, "")
        st.markdown(f"**{head}**" + (f" &nbsp; {badge}" if badge else "")
                    + (f" &nbsp; :blue-badge[{conf}]" if conf and status not in WEAK else ""))
        st.markdown(ai["finding"] if ai else first_sentence(r.get("finding")))
        rec = ai["recommendation"] if ai else r.get("recommendation")
        if rec:
            st.markdown(f":material/lightbulb: **Recommendation:** {rec}")
        if ai:
            st.caption(L.AI_INSIGHT, help="Written once by an AI model from this insight only and stored. Rejected "
                                          "automatically if it adds or changes any number. The analysis wording is "
                                          "under 'Show full evidence'.")
        if r.get("expected_benefit"):
            st.caption(f"Expected benefit (estimate, not measured): {r['expected_benefit']}")
        with st.expander("Show full evidence", icon=":material/fact_check:"):
            if ai:
                st.markdown(f"**{r.get('topic', '')}**")
            st.markdown(r.get("finding", ""))
            if ai and r.get("recommendation"):
                st.markdown(f"**Recommendation (analysis):** {r['recommendation']}")
            for k, title in (("method", "How we checked"), ("uncertainty", "How sure we are")):
                if r.get(k):
                    st.markdown(f"**{title}:** {r[k]}")
        C.tech_details({k: r.get(k) for k in ("insight_id", "status", "hypothesis", "evidence", "numbers")})


st.subheader("What we learned")
st.caption("Lessons from the synthetic operating data of the fictional PLATO-40 hotel — for the next building and for running "
           "this one better. Not facts about real PLATO buildings or CLARA installations.")
data_dir = Path(common.ctx()["data_dir"])
if not os.environ.get("PLATO40_INSIGHTS_DIR") and not common.insights_ready(data_dir) \
        and (data_dir / "manifest.json").exists():
    with st.spinner(f"Analysing {dataset_line(data_dir)} for lessons (once, then stored)…"):
        ok, log = common.generate_insights(data_dir)
    if not ok:
        st.warning("The lessons for this dataset could not be computed; showing the dev-year lessons instead.",
                   icon=":material/warning:")
        C.tech_details({"log": log})
d = insights_dir()
rows = load_insights(d)
same = d.resolve() == common.dataset_insights_dir(data_dir).resolve()
if not rows:
    st.info("The lessons from the PLATO-40 year are not loaded in this installation yet.", icon=":material/info:")
    if (common.DEV_DATA / "manifest.json").exists():
        if st.button("Create the lessons now (about 10 seconds)", icon=":material/auto_awesome:", type="primary",
                     key="gen_insights"):
            with st.spinner("Analysing the 365-day dev year…"):
                ok, log = common.generate_insights()
            if ok:
                st.rerun()
            st.error("The lessons could not be created.", icon=":material/error:")
            C.tech_details({"log": log})
    else:
        st.caption("They are computed from the dev year, which is not available here.")
    with st.expander("Technical details", icon=":material/code:"):
        st.code(GENERATE, language="bash")
else:
    src = dataset_line(data_dir) if same else dataset_line(common.DEV_DATA)
    st.caption(f":material/calendar_month: Computed from **{src}**. Pick another dataset above and the analysis "
               "and its plain-language wording are created for it (once, then stored)."
               + ("" if same else " This dataset's own lessons are not available, so the dev year is shown."))
    if insights_ai.available() and insights_ai.missing(rows, insights_ai.load()):
        with st.spinner("Writing the lessons in plain language (once, then stored)…"):
            insights_ai.generate(rows)
    ai_store = insights_ai.load()
    cols = st.columns(2)
    for col, (target, title, icon) in zip(cols, COLUMNS):
        mine = [r for r in rows if str(r.get("target", "")).upper() == target]
        with col:
            st.markdown(f"#### {title}")
            st.caption(f"{len(mine)} insight(s)")
            for r in mine:
                card(r, insights_ai.entry(r, ai_store))
md = (d / "insights.md").read_text(encoding="utf-8") if (d / "insights.md").exists() else ""
lim = section(md, "Limitations")
if lim:
    st.markdown("#### Limitations")
    st.markdown(lim)
