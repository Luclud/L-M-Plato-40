"""Guest conversations: one thread per guest request with Steward's linked decisions (pure, read-only).

The only file written is the consent mock (`consents.json`, next to the approvals file): it simulates a guest's
answer and never changes decisions or approvals.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from viewer.labels import CONSENT_MOCK as CONSENT_NOTE
COLS = ["request_id", "t", "room_id", "channel", "language", "request_type", "text", "priority", "due",
        "consent_flag", "event_ids", "n_decisions", "first_reply", "response_minutes", "on_time", "needs_consent",
        "episodes"]


def _utc(s) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def links(df: pd.DataFrame) -> pd.DataFrame:
    """(request_id, decision row) pairs from `guest_request_ids`, in decision time order."""
    if df is None or df.empty or "guest_request_ids" not in df:
        return pd.DataFrame(columns=["request_id", "event_id", "t", "has_message", "consent", "episode"])
    x = df[df.guest_request_ids.map(lambda v: isinstance(v, list) and len(v) > 0)]
    x = x.explode("guest_request_ids")
    return pd.DataFrame({"request_id": x.guest_request_ids.astype(str).values, "event_id": x.event_id.values,
                         "t": _utc(x.timestamp_utc).values,
                         "has_message": x.message.map(lambda m: isinstance(m, dict) and bool(m.get("text"))).values,
                         "consent": x.requires_guest_consent.astype(bool).values,
                         "episode": x.episode.values}).assign(t=lambda d: _utc(d.t)).sort_values(
        ["t", "event_id"], kind="stable")


def threads(data, df: pd.DataFrame) -> pd.DataFrame:
    """One row per guest request (newest first); requests Steward never answered stay in with no reply."""
    gr = getattr(data, "guest_requests", None)
    if gr is None or gr.empty:
        return pd.DataFrame(columns=COLS)
    lk = links(df)
    g = lk.groupby("request_id", sort=False)
    ev = g.event_id.agg(list)
    rep = lk[lk.has_message.astype(bool)].groupby("request_id").t.min()
    out = pd.DataFrame({
        "request_id": gr.request_id.astype(str), "t": _utc(gr.timestamp_utc), "room_id": gr.room_id,
        "channel": gr.channel, "language": gr.language.astype(str).str.lower(), "request_type": gr.request_type,
        "text": gr.text, "priority": gr.priority, "due": _utc(gr.response_due_utc),
        "consent_flag": gr.requires_guest_consent.astype(str).str.lower().eq("required"),
    })
    out["event_ids"] = out.request_id.map(ev).map(lambda v: v if isinstance(v, list) else [])
    out["n_decisions"] = out.event_ids.map(len)
    out["first_reply"] = _utc(out.request_id.map(rep))
    out["response_minutes"] = (out.first_reply - out.t).dt.total_seconds() / 60
    out["on_time"] = [None if pd.isna(r) or pd.isna(d) else bool(r <= d) for r, d in zip(out.first_reply, out.due)]
    out["needs_consent"] = out.request_id.map(g.consent.any()).eq(True)
    out["episodes"] = out.request_id.map(g.episode.agg(lambda s: list(dict.fromkeys(s.dropna()))))
    out["episodes"] = out.episodes.map(lambda v: v if isinstance(v, list) else [])
    return out.sort_values("t", ascending=False, kind="stable").reset_index(drop=True)[COLS]


def thread_decisions(df: pd.DataFrame, event_ids: list[str]) -> pd.DataFrame:
    return df[df.event_id.isin(event_ids)].sort_values(["timestamp_utc", "event_id"], kind="stable")


def summary(th: pd.DataFrame, consents: dict, df: pd.DataFrame) -> dict:
    rep = th[th.on_time.notna()] if len(th) else th
    open_ = [e for e in consent_requests(df).event_id if e not in consents]
    return {
        "requests": len(th),
        "answered": int(th.first_reply.notna().sum()) if len(th) else 0,
        "no_reply": int(th.first_reply.isna().sum()) if len(th) else 0,
        "on_time_pct": (100 * rep.on_time.astype(bool).mean()) if len(rep) else None,
        "median_minutes": float(th.response_minutes.median()) if th.response_minutes.notna().any() else None,
        "languages": int(th.language.nunique()) if len(th) else 0,
        "consent_open": len(open_),
    }


def consent_requests(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["event_id"])
    return df[df.requires_guest_consent.astype(bool)].sort_values("timestamp_utc", kind="stable")


# ---------------------------------------------------------------------------- consent mock
def consents_path(approvals_path) -> Path:
    env = os.environ.get("PLATO40_CONSENTS")
    return Path(env) if env else Path(approvals_path).with_name("consents.json")


def load_consents(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("decisions", {})
    except (json.JSONDecodeError, AttributeError, OSError):
        return {}


def record_consent(path, event_id: str, status: str) -> dict:
    """Store a simulated guest answer (yes/no). Simulation only - nothing is sent or granted."""
    if status not in ("yes", "no"):
        raise ValueError("status must be yes|no")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = load_consents(p)
    cur[event_id] = {"status": status, "recorded_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")}
    p.write_text(json.dumps({"_note": CONSENT_NOTE, "decisions": cur}, indent=1, ensure_ascii=False), encoding="utf-8")
    return cur
