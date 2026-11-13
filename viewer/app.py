"""PLATO-40 demo viewer: evidence -> risk -> decision -> autonomy -> verification.

    streamlit run viewer/app.py -- --decisions out/qs.jsonl --data data/quickstart

Read-only view of a decisions file. Approve/Reject only writes a local JSON file (mock executor).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plato40 import io  # noqa: E402
from viewer import core  # noqa: E402

PAGES = ["Overview", "Timeline", "Incident card", "Approval inbox", "Data quality & look-alikes", "Hero scenarios"]


def _args():
    p = argparse.ArgumentParser()
    p.add_argument("--decisions", default=os.environ.get("PLATO40_DECISIONS", "out/qs.jsonl"))
    p.add_argument("--data", default=os.environ.get("PLATO40_DATA", "data/quickstart"))
    p.add_argument("--approvals", default=os.environ.get("PLATO40_APPROVALS", "out/approvals.json"))
    a, _ = p.parse_known_args(sys.argv[1:])
    return a


def _abs(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (ROOT / q)


# ---------------------------------------------------------------------------- cached loading
@st.cache_resource(show_spinner="Loading dataset …")
def load_data(path: str):
    data = io.load(path)
    return data, core.SeriesStore(data)


@st.cache_data(show_spinner="Loading decisions …")
def load_decisions(path: str, mtime: float) -> pd.DataFrame:
    return core.load_decisions(path)


@st.cache_resource(show_spinner=False)
def checks(decisions: str, data_dir: str, mtime: float) -> core.Checks:
    return core.Checks(decisions, data_dir)


# ---------------------------------------------------------------------------- small UI helpers
def badge_html(autonomy: str) -> str:
    label, colour = core.badge(autonomy)
    return (f"<span style='background:{colour};color:white;padding:2px 8px;border-radius:10px;"
            f"font-size:0.8em'>{label}</span>")


def pill(text: str, colour: str = "#455a64") -> str:
    return (f"<span style='border:1px solid {colour};color:{colour};padding:1px 7px;border-radius:10px;"
            f"font-size:0.8em'>{text}</span>")


def goto(page: str, episode: str | None = None):
    st.session_state["_goto"] = page
    if episode is not None:
        st.session_state["_episode"] = episode
    st.rerun()


def plot(fig, key: str):
    st.plotly_chart(fig, width="stretch", key=key, config={"displayModeBar": False})


# ---------------------------------------------------------------------------- screens
def page_overview(ctx):
    df, data = ctx["df"], ctx["data"]
    s = core.summary(df, ctx["approvals"])
    st.subheader(f"PLATO-40 · {data.manifest.get('profile')} · {data.start:%Y-%m-%d} → {data.end:%Y-%m-%d} (UTC)")
    st.caption(f"Decisions file `{ctx['decisions']}` · data `{ctx['data_dir']}` · synthetic dataset "
               f"{data.manifest.get('dataset_version', '')}")
    c = st.columns(5)
    c[0].metric("Decisions", s["decisions"])
    c[1].metric("Episodes (incidents)", s["episodes"])
    c[2].metric("Need human approval", s["needs_approval"])
    c[3].metric("Approvals pending", s["approvals_pending"])
    c[4].metric("Guest consent needed", s["consent_needed"], help="needed ≠ granted")

    with st.spinner("Running validate.py + eval/evaluate.py …"):
        ok, out, text = ctx["checks"].result()
    if ok:
        st.success("validate.py: **PASS** — 0 safety/contract violations")
    else:
        st.error("validate.py: **FAIL** — see violations below")
    with st.expander("validate.py output", expanded=not ok):
        st.code(out, language="text")

    left, right = st.columns(2)
    ep = core.episodes(df)
    with left:
        st.markdown("**Episodes by issue type**")
        if len(ep):
            t = ep.groupby("issue_type").agg(episodes=("episode", "count"), decisions=("decisions", "sum"))
            st.dataframe(t.sort_values("episodes", ascending=False), width="stretch")
        else:
            st.info("No decisions in this file.")
    with right:
        st.markdown("**Decisions by autonomy level / agent**")
        a = pd.DataFrame({"decisions": s["by_autonomy"]}).rename(index=lambda x: core.badge(x)[0])
        st.dataframe(a, width="stretch")
        st.dataframe(pd.DataFrame({"decisions": s["by_agent"]}), width="stretch")

    st.markdown("**Detection vs labels** (`eval/evaluate.py` — dev aid, labels are not ground truth)")
    table = core.parse_eval_table(text)
    head = next((x for x in text.splitlines() if x.startswith("Incidents detected")), "")
    if head:
        st.markdown(f"`{head}`")
    if len(table):
        st.dataframe(table, width="stretch", hide_index=True)
    with st.expander("evaluate.py output"):
        st.code(text, language="text")


def page_timeline(ctx):
    import plotly.express as px

    df, data = ctx["df"], ctx["data"]
    span = data.end - data.start
    ep = core.episodes(df, min_bar=max(pd.Timedelta(hours=2), span / 250))
    if ep.empty:
        st.info("No episodes.")
        return
    types = sorted(ep.issue_type.unique())
    sel = st.multiselect("Issue types", types, default=types)
    ep = ep[ep.issue_type.isin(sel)]
    if ep.empty:
        return
    fig = px.timeline(ep, x_start="start", x_end="bar_end", y="issue_type", color="issue_type",
                      custom_data=["episode"], hover_data={"episode": True, "decisions": True, "priority": True,
                                                           "bar_end": False, "issue_type": False},
                      hover_name="label")
    fig.update_layout(height=120 + 45 * len(sel), showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
    st.caption("Click a bar to open its incident card. Bars span first → last decision of the episode.")
    ev = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="timeline")
    pts = (ev or {}).get("selection", {}).get("points", []) if isinstance(ev, dict) else \
        getattr(getattr(ev, "selection", None), "points", [])
    if pts:
        cd = pts[0].get("customdata") or []
        if cd:
            goto("Incident card", cd[0])
    st.dataframe(ep[["episode", "issue_type", "start", "end", "decisions", "priority", "autonomy", "assets"]],
                 width="stretch", hide_index=True)


def page_incident(ctx, episode: str | None = None, key: str = "inc"):
    df, store, data = ctx["df"], ctx["store"], ctx["data"]
    ep = core.episodes(df)
    if ep.empty:
        st.info("No episodes.")
        return
    if episode is None:
        types = ["(all)"] + sorted(ep.issue_type.unique())
        c1, c2 = st.columns([1, 3])
        it = c1.selectbox("Issue type", types, key=f"{key}_type")
        sub = ep if it == "(all)" else ep[ep.issue_type == it]
        ids = list(sub.episode)
        want = st.session_state.get("_episode")
        idx = ids.index(want) if want in ids else 0
        labels = dict(zip(sub.episode, sub.label))
        episode = c2.selectbox("Episode", ids, index=idx, format_func=labels.get, key=f"{key}_ep_{it}")
        st.session_state["_episode"] = episode
    rows = df[df.episode == episode].sort_values(["t", "event_id"])
    info = ep[ep.episode == episode].iloc[0]
    st.markdown(f"### {episode} · `{info.issue_type}` · {', '.join(info.assets)}")
    st.markdown(f"{badge_html(info.autonomy)} &nbsp; {pill('priority ' + info.priority)} &nbsp; "
                f"{pill(str(info.decisions) + ' decision(s)')} &nbsp; {pill('agent ' + (info.agent or '?'))}",
                unsafe_allow_html=True)
    st.caption(f"{core.fmt_times(info.start)}  →  {core.fmt_times(info.end)}")

    charts = store.charts_for_decisions(rows)
    markers = [(t, a) for t, a in zip(rows.t, rows.action)]
    if charts:
        st.caption("Evidence series around the episode. Red dotted lines = decisions (data to the right of a "
                   "line was NOT known at that decision). Grey = data gap (unknown, never zero).")
        cols = st.columns(min(2, len(charts)))
        for i, c in enumerate(charts):
            with cols[i % len(cols)]:
                plot(core.figure(c, markers), key=f"{key}_{episode}_chart_{i}")
    for i, (_, r) in enumerate(rows.iterrows()):
        with st.container(border=True):
            h1, h2 = st.columns([3, 2])
            h1.markdown(f"**{r.event_id} · `{r.action}`** &nbsp; {badge_html(r.autonomy)} &nbsp; "
                        f"{pill(r.priority)}", unsafe_allow_html=True)
            h2.caption(core.fmt_times(r.t))
            st.markdown(f"**Reason:** {r.reason}")
            if r.requires_guest_consent:
                st.warning("Guest consent **needed — not granted** (no entry/shutoff until consent + approval).")
            elif r.requires_human_approval:
                st.info("Requires human approval before execution (see Approval inbox).")
            a, b = st.columns(2)
            with a:
                if r.evidence:
                    st.markdown("**Evidence**")
                    st.markdown("\n".join(f"- `{e}`" for e in r.evidence))
                if isinstance(r.expected_outcome, str):
                    st.markdown(f"**Expected outcome:** {r.expected_outcome}")
                if isinstance(r.verification_plan, str):
                    st.markdown(f"**Verification plan:** {r.verification_plan}")
            with b:
                if isinstance(r.resource_id, str):
                    when = f" for {core.fmt_times(r.scheduled_for_utc)}" if isinstance(r.scheduled_for_utc, str) else ""
                    st.markdown(f"**Resource:** `{r.resource_id}`{when}")
                if core.nan_to_none(r.confidence) is not None:
                    st.markdown(f"**Confidence:** {float(r.confidence):.2f}")
                if r.dependencies:
                    st.markdown(f"**Depends on:** {', '.join(r.dependencies)}")
                msg = core.message_label(core.nan_to_none(r.message))
                if msg:
                    st.markdown("**Guest message** — proposed, NOT delivered")
                    st.info(msg)
                sim = core.simulated_label(core.nan_to_none(r.simulated_effect))
                if sim:
                    st.markdown(f"{pill('SIMULATED', '#6a1b9a')} {sim}", unsafe_allow_html=True)
                if r.guest_request_ids:
                    gr = core.guest_requests(data, r.guest_request_ids)
                    if len(gr):
                        st.markdown("**Guest request(s)**")
                        st.dataframe(gr, hide_index=True, width="stretch")


def page_approvals(ctx):
    df = ctx["df"]
    st.warning(f"**{core.MOCK}.** Approve/Reject only writes `{ctx['approvals_path']}`.")
    appr = df[df.requires_human_approval].copy()
    if appr.empty:
        st.info("No decisions need human approval.")
        return
    approvals = core.load_approvals(ctx["approvals_path"])
    appr["status"] = appr.event_id.map(lambda e: approvals.get(e, {}).get("status", "pending"))
    c1, c2, c3 = st.columns(3)
    show = c1.selectbox("Show", ["pending", "approved", "rejected", "all"])
    types = ["(all)"] + sorted(appr.issue_type.unique())
    it = c2.selectbox("Issue type", types, key="appr_type")
    lvl = c3.selectbox("Level", ["(all)", "approval", "approval + consent"])
    if show != "all":
        appr = appr[appr.status == show]
    if it != "(all)":
        appr = appr[appr.issue_type == it]
    if lvl != "(all)":
        appr = appr[appr.requires_guest_consent == (lvl == "approval + consent")]
    appr = appr.assign(r=appr.priority.map(core.PRIO_RANK)).sort_values(["r", "t"])
    st.caption(f"{len(appr)} decision(s); showing the first 50 by priority.")
    for _, r in appr.head(50).iterrows():
        with st.container(border=True):
            a, b = st.columns([4, 1])
            a.markdown(f"**{r.event_id} · `{r.action}` · {', '.join(r.affected_assets)}** &nbsp; "
                       f"{badge_html(r.autonomy)} &nbsp; {pill(r.priority)} &nbsp; {pill(r.status)}",
                       unsafe_allow_html=True)
            a.caption(f"{core.fmt_times(r.t)} · episode {r.episode} · {r.issue_type}")
            a.markdown(core.approval_note(r))
            if r.requires_guest_consent:
                a.markdown("Guest consent: **needed — not granted**")
            if b.button("Approve", key=f"ok_{r.event_id}", type="primary"):
                core.record_approval(ctx["approvals_path"], r.event_id, "approved", ctx["decisions"], core.MOCK)
                st.rerun()
            if b.button("Reject", key=f"no_{r.event_id}"):
                core.record_approval(ctx["approvals_path"], r.event_id, "rejected", ctx["decisions"], core.MOCK)
                st.rerun()
            if b.button("Open", key=f"open_{r.event_id}"):
                goto("Incident card", r.episode)


def page_dq(ctx):
    df, data, store = ctx["df"], ctx["data"], ctx["store"]
    files = core.side_files(ctx["decisions"])
    st.markdown("**Data-quality log** (cleaning applied while loading — `<out>_dq.json`)")
    dq = core.load_json(files["dq"][0]) if files["dq"] else None
    if dq is None:
        dq = [vars(x) for x in data.dq]
        st.caption("No `_dq.json` next to the decisions file — showing the loader's findings for this dataset.")
    dq = pd.DataFrame(dq)
    if len(dq):
        st.dataframe(dq.groupby(["kind", "source"]).agg(findings=("count", "size"), rows=("count", "sum")),
                     width="stretch")
        with st.expander(f"All {len(dq)} findings"):
            st.dataframe(dq, width="stretch", hide_index=True)

    st.markdown("**Gateway outages / data-quality episodes** (from decisions)")
    dqd = df[df.issue_type == "data_quality"]
    gw = dqd[dqd.affected_assets.map(lambda a: any(x.startswith("GW_") for x in a)).astype(bool)]
    st.caption(f"{dqd.episode.nunique()} data-quality episodes, {gw.episode.nunique()} gateway outages. "
               "Rooms behind a gateway outage are treated as unknown, not normal.")
    if len(dqd):
        st.dataframe(dqd[["event_id", "timestamp_utc", "affected_assets", "priority", "reason"]],
                     width="stretch", hide_index=True)
    if len(gw):
        g = gw.iloc[0]
        asset = next(x for x in g.affected_assets if x.startswith("GW_"))
        for i, c in enumerate(store.charts_for(None, asset, "data_quality", None, g.t - pd.Timedelta(hours=24),
                                               g.t + pd.Timedelta(hours=24))):
            plot(core.figure(c, [(g.t, g.action)]), key=f"gw_{i}")

    st.markdown("**Ruled-out look-alikes** (exported by agents as `*_ruled_out.json`)")
    if files["ruled_out"]:
        for p in files["ruled_out"]:
            items = core.load_json(p) or []
            st.markdown(f"`{p.name}` — {len(items)} look-alike(s) ruled out (no decision emitted)")
            st.dataframe(pd.DataFrame(items), width="stretch", hide_index=True)
    else:
        st.caption("None exported next to this decisions file yet.")

    st.markdown("**BMS alarms vs our episodes** (BMS = a clue, not ground truth)")
    if files["bms"]:
        for p in files["bms"]:
            st.markdown(f"`{p.name}`")
            if p.suffix == ".md":
                st.markdown(p.read_text())
            elif p.suffix == ".csv":
                st.dataframe(pd.read_csv(p), width="stretch", hide_index=True)
            else:
                st.json(core.load_json(p))
    bms = core.bms_alarms(data)
    c1, c2 = st.columns(2)
    c1.dataframe(bms.groupby("alarm_code").size().rename("alarms").sort_values(ascending=False),
                 width="stretch")
    ours = df[df.issue_type != "data_quality"].groupby("issue_type").episode.nunique().rename("our episodes")
    c2.dataframe(ours, width="stretch")


def _auto_heroes(ctx, profile):
    st.info(f"No hero presets for `{profile}` — showing the four most severe episodes instead.")
    ids = core.auto_heroes(ctx["df"])
    if not ids:
        st.warning("No episodes.")
        return
    for t, e in zip(st.tabs(ids), ids):
        with t:
            page_incident(ctx, e, key=f"hero_{e}")


def page_heroes(ctx):
    df, store, data = ctx["df"], ctx["store"], ctx["data"]
    profile = data.manifest.get("profile", "")
    heroes = core.hero_presets(core.load_labels(profile))
    if not heroes:
        return _auto_heroes(ctx, profile)
    tabs = st.tabs([h["title"] for h in heroes])
    for tab, h in zip(tabs, heroes):
        with tab:
            s, e, lab, eps = core.hero_window(h, df)
            st.markdown(f"**Talking points:** {h['say']}")
            file, asset = h["chart"]
            pad = pd.Timedelta(hours=12)
            charts = store.charts_for(file, asset, h["issue_type"], None,
                                      (s - pad) if s is not None else None, (e + pad) if e is not None else None)
            rows = df[df.episode.isin(eps)]
            markers = [(t, a) for t, a in zip(rows.t, rows.action)]
            if lab:
                st.caption(f"Yellow = labelled incident window `{lab}` (eval/ hand label, dev aid only).")
            cols = st.columns(min(2, max(1, len(charts))))
            for i, c in enumerate(charts):
                with cols[i % len(cols)]:
                    plot(core.figure(c, markers, shade=(s, e) if lab else None), key=f"hero_{h['key']}_{i}")
            if not eps:
                st.warning("No episode in this decisions file matches this incident yet "
                           f"(`{h['issue_type']}` on {', '.join(h['assets'])}).")
                continue
            pick = st.selectbox("Episode", eps, key=f"hero_pick_{h['key']}") if len(eps) > 1 else eps[0]
            page_incident(ctx, pick, key=f"hero_{h['key']}")


# ---------------------------------------------------------------------------- main
def main():
    st.set_page_config(page_title="PLATO-40 viewer", layout="wide")
    args = _args()
    if "_goto" in st.session_state:
        st.session_state["page"] = st.session_state.pop("_goto")
    with st.sidebar:
        st.markdown("## PLATO-40 · autonomous ops")
        st.caption("Fictional building, synthetic data. Simulated effects are labelled SIMULATED; guest messages "
                   "are proposed, never delivered.")
        page = st.radio("Screen", PAGES, key="page")
        dec = st.text_input("Decisions file", args.decisions)
        data_dir = st.text_input("Data directory", args.data)
        approvals_path = st.text_input("Approvals file (mock)", args.approvals)
    dpath, ddir = _abs(dec), _abs(data_dir)
    if not dpath.exists():
        st.error(f"Decisions file `{dpath}` not found. Run `python run.py {data_dir} --out {dec}` first.")
        st.stop()
    mtime = dpath.stat().st_mtime
    bg = checks(str(dpath), str(ddir), mtime)
    data, store = load_data(str(ddir))
    try:
        df = load_decisions(str(dpath), mtime)
    except ValueError as e:
        st.error(f"Cannot read decisions file `{dpath}`: {e}")
        st.stop()
    ctx = {"df": df, "data": data, "store": store, "decisions": dpath,
           "data_dir": ddir, "mtime": mtime, "checks": bg, "approvals_path": _abs(approvals_path)}
    ctx["approvals"] = core.load_approvals(ctx["approvals_path"])
    st.title(page)
    {"Overview": page_overview, "Timeline": page_timeline, "Incident card": page_incident,
     "Approval inbox": page_approvals, "Data quality & look-alikes": page_dq, "Hero scenarios": page_heroes}[page](ctx)


main()
