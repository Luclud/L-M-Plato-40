"""Run context shared by every page: dataset picker, cached loading, CLI args."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plato40 import io  # noqa: E402
from viewer import core  # noqa: E402
from viewer import labels as L  # noqa: E402


@st.cache_resource
def logo_uri() -> str:
    """The Steward butler logo as a data URI for the brand row."""
    png = (Path(__file__).resolve().parents[1] / "assets" / "logo.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(png).decode()


GENERATED_INSIGHTS = ROOT / "out" / "insights"   # written by the "Generate insights" button (out/ is writable)
DEV_DATA = ROOT / "data" / "dev"


def _current_data_dir() -> Path | None:
    try:
        return Path(st.session_state["ctx"]["data_dir"])
    except (KeyError, TypeError):
        return None


def dataset_insights_dir(data_dir: Path) -> Path:
    """Where the Level 3 insights of one dataset live: the committed repo-root files for the dev year, else
    out/insights/<name>-<hash of path + manifest>/ (written once by generate_insights)."""
    d = Path(data_dir).resolve()
    if d == DEV_DATA.resolve() and (ROOT / "insights.jsonl").exists():
        return ROOT
    m = d / "manifest.json"
    stamp = f"{d}|{m.stat().st_mtime if m.exists() else 0}"
    return GENERATED_INSIGHTS / f"{d.name}-{hashlib.sha1(stamp.encode()).hexdigest()[:8]}"


def insights_dir(data_dir: Path | None = None) -> Path:
    """Insights for the selected dataset ($PLATO40_INSIGHTS_DIR wins). Falls back to the dev-year files while a
    dataset's own insights have not been generated yet."""
    env = os.environ.get("PLATO40_INSIGHTS_DIR")
    if env:
        return Path(env)
    data_dir = data_dir or _current_data_dir()
    if data_dir is not None:
        d = dataset_insights_dir(data_dir)
        if (d / "insights.jsonl").exists():
            return d
    return ROOT if (ROOT / "insights.jsonl").exists() else GENERATED_INSIGHTS


def insights_ready(data_dir: Path) -> bool:
    return (dataset_insights_dir(data_dir) / "insights.jsonl").exists()


def generate_insights(data_dir: Path = DEV_DATA, out_dir: Path | None = None) -> tuple[bool, str]:
    """Run analysis/insights.py (unchanged, deterministic, ~1-10 s) on one dataset into its own folder.
    Returns (ok, log tail)."""
    out_dir = out_dir or dataset_insights_dir(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / "analysis" / "insights.py"), str(data_dir),
           "--out", str(out_dir / "insights.md"), "--jsonl", str(out_dir / "insights.jsonl")]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr)[-3000:]


PROFILE_NAMES = {"quickstart": "Quickstart", "dev": "Dev year", "holdout": "Holdout"}
CUSTOM = "Custom…"


def args():
    p = argparse.ArgumentParser()
    p.add_argument("--decisions", default=os.environ.get("PLATO40_DECISIONS"))
    p.add_argument("--data", default=os.environ.get("PLATO40_DATA"))
    p.add_argument("--approvals", default=os.environ.get("PLATO40_APPROVALS", "out/approvals.json"))
    a, _ = p.parse_known_args(sys.argv[1:])
    return a


def abs_path(p: str | Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (ROOT / q)


def _candidates(d: Path, profile: str, outer: str | None = None) -> list[Path]:
    short = {"quickstart": "qs"}.get(profile, profile)
    names = [f"out/{short}.jsonl", f"out/{d.name}.jsonl", f"out/{profile}.jsonl", f"out/decisions_{profile}.jsonl",
             f"decisions_{profile}.jsonl"]
    if outer and outer != d.name:   # archive extracted into data/<outer>/<inner>/
        names += [f"out/{outer}.jsonl", f"decisions_{outer}.jsonl"]
    if profile == "dev":
        names.append("decisions.jsonl")
    return [ROOT / n for n in dict.fromkeys(names)]


@st.cache_data(ttl=30, show_spinner=False)
def datasets() -> dict[str, tuple[str, str]]:
    """Friendly name -> (decisions file, data dir), discovered from data/*/manifest.json and out/*.jsonl."""
    found = {}
    for d in sorted((ROOT / "data").glob("*/")):
        outer = d.name
        m = d / "manifest.json"
        if not m.exists():
            # 7z may extract the holdout as data/holdout/<folder>/ - accept exactly one nested dataset (like io.load)
            subs = [s for s in sorted(d.glob("*/")) if (s / "manifest.json").exists()]
            if len(subs) != 1:
                continue
            d, m = subs[0], subs[0] / "manifest.json"
        try:
            profile = json.loads(m.read_text()).get("profile") or d.name
        except (OSError, ValueError):
            profile = d.name
        dec = next((c for c in _candidates(d, profile, outer) if c.exists()), None)
        if dec is not None:
            found.setdefault(PROFILE_NAMES.get(profile, profile.replace("_", " ").title()), (str(dec), str(d)))
    order = list(PROFILE_NAMES.values())
    return dict(sorted(found.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0])))


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


def _pick() -> tuple[str, str]:
    a = args()
    ds = datasets()
    options = list(ds) + [CUSTOM]
    if "dataset" not in st.session_state:
        if a.decisions or a.data:
            want = (str(abs_path(a.decisions)) if a.decisions else None, str(abs_path(a.data)) if a.data else None)
            hit = [k for k, v in ds.items() if (want[0] in (None, v[0])) and (want[1] in (None, str(Path(v[1]))))]
            st.session_state["dataset"] = hit[0] if hit and a.decisions else CUSTOM
        else:
            st.session_state["dataset"] = options[0]
        st.session_state.setdefault("custom_decisions", a.decisions or "out/qs.jsonl")
        st.session_state.setdefault("custom_data", a.data or "data/quickstart")
    if st.session_state["dataset"] not in options:
        st.session_state["dataset"] = options[0]
    c1, c2 = st.columns([5, 2])
    c1.markdown(
        f"<div class='brand'><img class='brand-mark' src='{logo_uri()}' alt='{L.APP_NAME} logo'><div><div class='brand-name'>{L.APP_NAME}</div></div></div>",
        unsafe_allow_html=True)
    c2.selectbox("Dataset", options, key="dataset", label_visibility="collapsed")
    if st.session_state["dataset"] == CUSTOM:
        with c2.popover("Custom files", icon=":material/folder_open:", width="stretch"):
            st.text_input("Decisions file", key="custom_decisions")
            st.text_input("Data directory", key="custom_data")
        return st.session_state["custom_decisions"], st.session_state["custom_data"]
    return ds[st.session_state["dataset"]]


def build_ctx() -> dict:
    """Pick the dataset, load everything (cached) and store the run context in the session."""
    dec, data_dir = _pick()
    a = args()
    dpath, ddir = abs_path(dec), abs_path(data_dir)
    custom = st.session_state.get("dataset") == CUSTOM
    if not dpath.exists():
        st.error(f"Decisions file `{dpath}` not found. Run `python run.py {data_dir} --out {dec}` first."
                 if custom else "The decisions for this dataset are missing. Run the pipeline first.")
        st.stop()
    if not (ddir / "manifest.json").exists():
        st.error(f"Data directory `{ddir}` has no manifest.json." if custom else "The dataset files are missing.")
        st.stop()
    mtime = dpath.stat().st_mtime
    data, store = load_data(str(ddir))
    try:
        df = load_decisions(str(dpath), mtime)
    except ValueError as e:
        st.error(f"Cannot read decisions file `{dpath}`: {e}" if custom else f"The decisions file is damaged: {e}")
        st.stop()
    bg = checks(str(dpath), str(ddir), mtime)
    ctx = {"df": df, "data": data, "store": store, "decisions": dpath, "data_dir": ddir, "mtime": mtime,
           "checks": bg, "approvals_path": abs_path(a.approvals), "dataset": st.session_state.get("dataset")}
    ctx["approvals"] = core.load_approvals(ctx["approvals_path"])
    st.session_state["ctx"] = ctx
    return ctx


def ctx() -> dict:
    return st.session_state["ctx"]


def pending_approvals(c: dict) -> int:
    """Incidents with at least one approval decision that has no mock decision recorded yet."""
    df = c["df"]
    if df.empty:
        return 0
    need = df[df.autonomy != "autonomous"]
    decided = set(c.get("approvals") or {})
    return int(need[~need.event_id.isin(decided)].episode_id.nunique())
