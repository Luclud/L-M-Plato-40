""""Ask the building": read-only Q&A over one loaded run (decision log), grounded with citations.

The copilot only reads: every tool is a query over the decisions DataFrame; nothing can approve, decide or write.
With a key (``.env``: ``LLM_PROVIDER=anthropic``, ``LLM_API_KEY``, ``LLM_MODEL``) Claude answers via tool use
(HTTP, like ``plato40/llm.py``). Without one, a deterministic offline answerer handles simple questions (a room or
asset, a date + action, approvals, counts, incident types) and ``copilot_cache.json`` holds answers to the suggested
questions per run (``python viewer/copilot.py --build-cache --decisions out/qs.jsonl``).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plato40 import llm  # noqa: E402
from viewer import components as C  # noqa: E402
from viewer import core  # noqa: E402
from viewer import labels as L  # noqa: E402
from viewer.views.incident import humanize, title  # noqa: E402

_SHIFT = re.compile(r"\s*\(?(?:shift\s+)?\bSH\d{6}\b\)?", re.I)

CACHE = Path(__file__).with_name("copilot_cache.json")
MAX_TOOL_CALLS = 6
TIMEOUT_S = 30
LIST_LIMIT = 15
NOT_IN_LOG = "not in the decision log"
CITE = re.compile(r"\[((?:D\d{6}|[A-Z]{2}-\d{5})(?:\s*,\s*(?:D\d{6}|[A-Z]{2}-\d{5}))*)\]")
_SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}

SYSTEM = (
    "You answer questions about one replay of PLATO-40, a fictional 40-room hotel, using ONLY the results of the "
    "tools (the decision log of this run). Rules: never guess; if the tools do not contain the answer, say it is "
    f"'{NOT_IN_LOG}'. Cite every fact with the decision id in square brackets, e.g. [D000123]. Use the friendly "
    "names and local times the tools return; do not print other ids, UTC times or file names. Simulated effects "
    "are simulated (not measured); guest messages are proposed, never sent; approvals in this viewer are mock and "
    "execute nothing; guest consent needed means not granted. You cannot change, approve or decide anything. "
    "Answer in English, at most 6 short sentences or bullets."
)

TOOL_SPECS = [
    {"name": "list_incidents", "description": "Incidents (one per episode), most severe first. All filters optional.",
     "input_schema": {"type": "object", "properties": {
         "type": {"type": "string", "description": "incident type, e.g. water_leak or 'Water leak'"},
         "asset": {"type": "string", "description": "room or asset, e.g. 'Room 104', 'R104', 'heat pump'"},
         "date_from": {"type": "string", "description": "local date YYYY-MM-DD (inclusive)"},
         "date_to": {"type": "string", "description": "local date YYYY-MM-DD (inclusive)"},
         "needs_approval": {"type": "boolean"},
         "include_routine": {"type": "boolean", "description": "also guest requests, energy, supplies, data issues"}}}},
    {"name": "get_incident", "description": "One incident with all its decisions in order.",
     "input_schema": {"type": "object", "properties": {"episode_id": {"type": "string"}}, "required": ["episode_id"]}},
    {"name": "get_decision", "description": "One decision in full (reason, evidence, plan, message, approval).",
     "input_schema": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}},
    {"name": "get_evidence", "description": "Evidence details and verification plans of one incident.",
     "input_schema": {"type": "object", "properties": {"episode_id": {"type": "string"}}, "required": ["episode_id"]}},
    {"name": "count_by_type", "description": "Number of incidents/episodes and decisions per type.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "pending_approvals", "description": "Incidents with decisions still waiting for a (mock) approval.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _plain(text) -> str:
    """humanize() + no shift ids."""
    return _SHIFT.sub("", humanize(text)).strip()


def plain_title(g: pd.DataFrame) -> str:
    """Incident title without the icon, e.g. "Water leak · Room 104"."""
    return re.sub(r":material/[a-z_]+:\s*", "", title(g)).strip()


def _cut(s, n=300) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _asset_code(text: str) -> str | None:
    """'Room 104' / 'R104' / 'heat pump' / 'Cleaner 1' -> code (None = no match)."""
    t = (text or "").strip()
    m = re.search(r"\b(?:room\s*|R)(\d{3})\b", t, re.I)
    if m:
        return f"R{m.group(1)}"
    low = t.lower()
    for table in (L.ASSETS, L.RESOURCES):
        for code, name in table.items():
            if low in (code.lower(), name.lower()) or (len(low) > 3 and name.lower() in low):
                return code
    return t.upper() if re.fullmatch(r"[A-Z]+_?[A-Z0-9]*\d", t) else None


def _type_code(text: str) -> str | None:
    low = (text or "").strip().lower()
    for code, (name, _) in L.ISSUE_TYPES.items():
        if low in (code, name.lower()):
            return code
    return None


class Tools:
    """Read-only queries over one run. ``call`` is the only entry point used by the model."""

    def __init__(self, df: pd.DataFrame, approvals: dict | None = None):
        self._df = df
        self._approvals = dict(approvals or {})
        self.cited: set[str] = set()   # decision/episode ids returned so far (allowed citations)

    # ---- helpers
    def _dec(self, r) -> dict:
        self.cited.add(r.event_id)
        return {"decision_id": r.event_id, "time": C.local_time(r.t), "did": L.label("action", r.action),
                "assets": [L.label("asset", a) for a in r.affected_assets],
                "status": L.label("autonomy", r.autonomy), "priority": L.label("priority", r.priority),
                "reason": _cut(_plain(r.reason), 240),
                **({"guest_message": L.PROPOSED} if isinstance(r.message, dict) and r.message else {}),
                **({"effect": L.SIMULATED + ": " + _cut(_plain(str(r.simulated_effect)), 160)}
                   if core.simulated_label(r.simulated_effect) else {}),
                **({"guest_consent": L.CONSENT_NEEDED} if r.requires_guest_consent else {}),
                **({"approval": ("mock decision recorded: " + str(self._approvals[r.event_id].get("status"))
                                 if isinstance(self._approvals.get(r.event_id), dict) else "waiting for a mock approval")}
                   if r.requires_human_approval else {})}

    def _inc(self, g: pd.DataFrame) -> dict:
        first = g.iloc[0]
        self.cited.update([first.episode, *g.event_id])
        return {"episode_id": first.episode, "title": plain_title(g), "type": L.label("issue", first.issue_type),
                "when": C.time_range(g.t.min(), g.t.max()),
                "priority": L.label("priority", min(g.priority, key=lambda p: _SEV.get(p, 9))),
                "needs_approval": bool(g.requires_human_approval.any()),
                "first_decision_id": first.event_id, "decisions": len(g),
                "headline": _cut(_plain(first.reason), 200)}

    def _groups(self):
        return self._df.sort_values(["t", "event_id"]).groupby("episode", sort=False)

    # ---- tools
    def list_incidents(self, type=None, asset=None, date_from=None, date_to=None, needs_approval=None,
                       include_routine=False):
        df = self._df
        if df.empty:
            return {"count": 0, "incidents": []}
        keep = df.issue_type.isin(L.INCIDENT_TYPES if not include_routine else list(L.ISSUE_TYPES))
        if type:
            code = _type_code(type)
            keep &= df.issue_type == (code or type)
        eps = set(df[keep].episode)
        if asset:
            code = _asset_code(asset) or asset
            eps &= set(df[[code in a or any(x.startswith(f"WM_{code}") for x in a) for a in df.affected_assets]].episode)
        local = df.t.dt.tz_convert(L.TZ).dt.date
        if date_from:
            eps &= set(df[local >= pd.Timestamp(date_from).date()].episode)
        if date_to:
            eps &= set(df[local <= pd.Timestamp(date_to).date()].episode)
        out = [self._inc(g) for ep, g in self._groups() if ep in eps]
        if needs_approval is not None:
            out = [o for o in out if o["needs_approval"] == bool(needs_approval)]
        out.sort(key=lambda o: (_SEV.get(next((k for k, (n, _) in L.PRIORITIES.items() if n == o["priority"]), ""), 9)))
        return {"count": len(out), "incidents": out[:LIST_LIMIT], **({"more": len(out) - LIST_LIMIT}
                                                                       if len(out) > LIST_LIMIT else {})}

    def get_incident(self, episode_id):
        g = self._df[self._df.episode == episode_id].sort_values(["t", "event_id"])
        if g.empty:
            return {"error": f"incident {NOT_IN_LOG}"}
        return {**self._inc(g), "steps": [self._dec(r) for r in g.head(12).itertuples()]}

    def get_decision(self, event_id):
        g = self._df[self._df.event_id == event_id]
        if g.empty:
            return {"error": f"decision {NOT_IN_LOG}"}
        r = next(g.itertuples())
        self.cited.add(r.episode)
        return {**self._dec(r), "episode_id": r.episode,
                "expected_outcome": _cut(_plain(r.expected_outcome), 200),
                "how_we_will_know": _cut(_plain(r.verification_plan), 200),
                "evidence": [_cut(_plain(core.parse_evidence(e).detail or e), 160) for e in r.evidence[:5]]}

    def get_evidence(self, episode_id):
        g = self._df[self._df.episode == episode_id]
        if g.empty:
            return {"error": f"incident {NOT_IN_LOG}"}
        self.cited.update([episode_id, *g.event_id])
        ev = list(dict.fromkeys(_plain(core.parse_evidence(e).detail or e) for x in g.evidence for e in x))
        plans = list(dict.fromkeys(_plain(v) for v in g.verification_plan if isinstance(v, str) and v))
        return {"episode_id": episode_id, "first_decision_id": g.event_id.iloc[0],
                "evidence": [_cut(e, 160) for e in ev[:8] if e], "how_we_will_know": [_cut(p, 200) for p in plans[:3]]}

    def count_by_type(self):
        df = self._df
        if df.empty:
            return {"types": []}
        g = df.groupby("issue_type").agg(episodes=("episode", "nunique"), decisions=("event_id", "size"))
        return {"types": [{"type": L.label("issue", k), "incident": k in L.INCIDENT_TYPES,
                           "episodes": int(v.episodes), "decisions": int(v.decisions)} for k, v in g.iterrows()]}

    def pending_approvals(self):
        df = self._df
        need = df[df.requires_human_approval & ~df.event_id.isin(set(self._approvals))] if len(df) else df
        out = []
        for ep, g in need.sort_values(["t", "event_id"]).groupby("episode", sort=False):
            inc = self._inc(self._df[self._df.episode == ep].sort_values(["t", "event_id"]))
            inc["waiting"] = [{"decision_id": r.event_id, "did": L.label("action", r.action),
                               "time": C.local_time(r.t)} for r in g.head(4).itertuples()]
            out.append(inc)
        out.sort(key=lambda o: _SEV.get(next((k for k, (n, _) in L.PRIORITIES.items() if n == o["priority"]), ""), 9))
        return {"note": L.MOCK, "count": len(out), "incidents": out[:LIST_LIMIT]}

    def call(self, name: str, args: dict | None) -> dict:
        fn = {s["name"]: getattr(self, s["name"]) for s in TOOL_SPECS}.get(name)
        if fn is None:
            return {"error": f"unknown tool {name!r}; tools are read-only: {[s['name'] for s in TOOL_SPECS]}"}
        try:
            return fn(**{k: v for k, v in (args or {}).items() if v not in (None, "")})
        except (TypeError, ValueError) as e:
            return {"error": f"bad arguments: {e}"}


# ----------------------------------------------------------------------------- answers
@dataclass
class Answer:
    text: str                        # may contain [D000123] citations (render() turns them into links)
    source: str                      # llm | offline | cached:llm | cached:offline
    tool_calls: list = field(default_factory=list)
    error: str | None = None


def _anthropic(cfg: dict) -> Callable:
    def client(system, messages, tools, timeout):
        url = (cfg["base_url"] or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        body = {"model": cfg["model"], "max_tokens": 700, "temperature": 0, "system": system,
                "messages": messages, "tools": tools}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "content-type": "application/json", "x-api-key": cfg["key"], "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (configured endpoint)
            return json.loads(r.read().decode())
    return client


def default_client() -> Callable | None:
    """Claude client from .env, or None (no key / other provider / disabled)."""
    cfg = llm.config()
    return _anthropic(cfg) if cfg and cfg["provider"] == "anthropic" else None


def ask_llm(question: str, tools: Tools, client: Callable, history: list | None = None,
            max_calls: int = MAX_TOOL_CALLS, timeout: float = TIMEOUT_S) -> Answer:
    """Tool-use loop: at most ``max_calls`` tool calls and ``timeout`` seconds; never raises."""
    messages = [*(history or [])[-6:], {"role": "user", "content": question}]
    calls, t0 = [], time.monotonic()
    try:
        while True:
            left = timeout - (time.monotonic() - t0)
            if left <= 0:
                return Answer("", "llm", calls, "The AI copilot took too long (over 30 s). Try a shorter question.")
            out = client(SYSTEM, messages, TOOL_SPECS, left)
            blocks = out.get("content") or []
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not uses:
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                return Answer(text or f"That is {NOT_IN_LOG}.", "llm", calls)
            if len(calls) + len(uses) > max_calls:
                return Answer("", "llm", calls, f"The question needed more than {max_calls} look-ups. "
                                                "Try asking about one room, date or incident.")
            results = []
            for u in uses:
                res = tools.call(u.get("name", ""), u.get("input") or {})
                calls.append((u.get("name"), u.get("input") or {}))
                results.append({"type": "tool_result", "tool_use_id": u.get("id"),
                                "content": json.dumps(res, ensure_ascii=False, default=str)[:6000]})
            messages += [{"role": "assistant", "content": blocks}, {"role": "user", "content": results}]
    except Exception:  # network, auth, quota, malformed response
        return Answer("", "llm", calls, "The AI copilot is not reachable right now.")


def _date_in(q: str, df: pd.DataFrame):
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", q, re.I)
    m2 = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q)
    if m2:
        return pd.Timestamp(m2.group(0)).date()
    if not m or df.empty:
        return None
    years = sorted(df.t.dt.tz_convert(L.TZ).dt.year.unique())
    for y in years:
        try:
            d = pd.Timestamp(year=int(y), month=_MONTHS[m.group(2).lower()[:3]], day=int(m.group(1))).date()
        except ValueError:
            continue
        if (df.t.dt.tz_convert(L.TZ).dt.date == d).any():
            return d
    return None


def _action_in(q: str) -> str | None:
    low = q.lower()
    keys = {"on-call": "call_oncall_housekeeper", "oncall": "call_oncall_housekeeper", "technician": "dispatch_technician",
            "robot": "dispatch_robot", "shut": "water_shutoff", "inspection": "request_inspection",
            "service": "order_service", "moved": "reassign_room", "released": "release_room",
            "blocked": "block_room", "setpoint": "adjust_room_setpoint", "temperature": "adjust_room_setpoint"}
    return next((a for k, a in keys.items() if k in low), None)


_TYPE_WORDS = {"water_leak": ("water leak", "leak"), "heating_comfort": ("heating", "heat pump", "cold", "comfort"),
               "ventilation_maintenance": ("filter", "ventilation"), "occupancy_surge": ("busy weekend", "surge", "event"),
               "room_turnaround": ("turnaround", "early check-in", "early checkin", "cleaning conflict")}


def _bullet(o: dict) -> str:
    return f"- **{o['title']}**, {o['when']} ({o['priority']}): {o['headline']} [{o['first_decision_id']}]"


def ask_offline(question: str, tools: Tools) -> Answer:
    """Deterministic answers for simple questions, from the same read-only tools (no AI model)."""
    q, df = question.strip(), tools._df
    calls = []

    def use(name, **kw):
        calls.append((name, kw))
        return tools.call(name, kw)

    low = q.lower()
    day, action = _date_in(q, df), _action_in(q)
    if action and day is not None:
        local = df.t.dt.tz_convert(L.TZ).dt.date
        rows = df[(df.action == action) & (local == day)]
        if rows.empty:
            return Answer(f"No “{L.label('action', action).lower()}” on {pd.Timestamp(day):%a %-d %b} — that is "
                          f"{NOT_IN_LOG}.", "offline", calls)
        lines = []
        for r in rows.head(5).itertuples():
            d = use("get_decision", event_id=r.event_id)
            lines.append(f"- {d['time']}: {d['did']} for {', '.join(d['assets']) or 'the building'} — {d['reason']}"
                         f"{'; ' + d['approval'] if 'approval' in d else ''} [{r.event_id}]")
        return Answer(f"On {pd.Timestamp(day):%a %-d %b} the log has {len(rows)} such decision(s):\n" + "\n".join(lines),
                      "offline", calls)
    if "approv" in low:
        res = use("pending_approvals")
        if not res["count"]:
            return Answer("No incident is waiting for a (mock) approval.", "offline", calls)
        lines = [f"- **{o['title']}** ({o['priority']}): "
                 + ", ".join(f"{w['did'].lower()} [{w['decision_id']}]" for w in o["waiting"]) for o in res["incidents"]]
        more = res["count"] - len(res["incidents"])
        return Answer(f"{res['count']} incident(s) still wait for a manager's decision. {L.MOCK}.\n"
                      + "\n".join(lines) + (f"\n- … and {more} more" if more > 0 else ""), "offline", calls)
    if re.search(r"\bhow many\b|\bcount\b|\beach type\b|\bper type\b", low):
        res = use("count_by_type")
        inc = [t for t in res["types"] if t["incident"]]
        rest = [t for t in res["types"] if not t["incident"]]
        if not res["types"]:
            return Answer(f"This run has no decisions, so the answer is {NOT_IN_LOG}.", "offline", calls)
        return Answer("Incidents in this run:\n" + "\n".join(f"- {t['type']}: {t['episodes']}" for t in inc)
                      + ("\n\nRoutine work: " + ", ".join(f"{t['type'].lower()} {t['episodes']}" for t in rest)
                         if rest else ""), "offline", calls)
    asset = _asset_code(q) if re.search(r"\broom\s*\d{3}\b|\bR\d{3}\b", q, re.I) or any(
        n.lower() in low for n in [*L.ASSETS.values(), *L.RESOURCES.values()]) else None
    typ = None if asset else next((c for c, words in _TYPE_WORDS.items() if any(w in low for w in words)), None)
    if asset or typ:
        kw = {k: v for k, v in (("asset", asset), ("type", typ)) if v}
        if day is not None:
            kw.update(date_from=str(day), date_to=str(day))
        res = use("list_incidents", **kw)
        what = " and ".join(x for x in (L.label("asset", asset) if asset else "",
                                        L.label("issue", typ).lower() if typ else "") if x)
        if not res["count"]:
            return Answer(f"No incident for {what} — that is {NOT_IN_LOG}.", "offline", calls)
        more = res.get("more", 0)
        return Answer(f"{res['count']} incident(s) for {what}:\n" + "\n".join(_bullet(o) for o in res["incidents"][:6])
                      + (f"\n- … and {res['count'] - 6} more" if res["count"] > 6 or more else ""), "offline", calls)
    return Answer(f"I couldn't find that — it is {NOT_IN_LOG} as far as the offline helper can tell. Without the "
                  "AI copilot I understand questions about a room or asset, a date with an action, approvals, "
                  "counts and incident types.", "offline", calls)


# ----------------------------------------------------------------------------- suggestions + cache
def suggestions(df: pd.DataFrame) -> list[str]:
    """Four questions that have an answer in this run (room of the most severe incident, approvals, a date, counts)."""
    out = []
    inc = df[df.issue_type.isin(L.INCIDENT_TYPES)] if len(df) else df
    rooms = inc.assign(s=inc.priority.map(_SEV).fillna(9)).sort_values(["s", "t"]) if len(inc) else inc
    room = next((a for x in rooms.affected_assets for a in x if re.fullmatch(r"R\d{3}", a)), None) if len(rooms) else None
    out.append(f"What happened in {L.label('asset', room)}?" if room else "What happened in Room 101?")
    out.append("Which incidents still need approval?")
    oc = df[df.action == "call_oncall_housekeeper"] if len(df) else df
    if len(oc):
        out.append(f"Why was the on-call housekeeper called on {oc.t.iloc[0].tz_convert(L.TZ):%-d %b}?")
    else:
        out.append("What did we do about the heat pump?")
    out.append("How many incidents of each type are there?")
    return out


def fingerprint(df: pd.DataFrame) -> str:
    cols = ["event_id", "timestamp_utc", "issue_type", "action", "episode"]
    rows = df[cols].astype(str).values.tolist() if len(df) else []
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def load_cache(path: Path = CACHE) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc.get("runs", {}) if isinstance(doc, dict) else {}


def cached(df: pd.DataFrame, question: str, cache: dict) -> Answer | None:
    e = (cache.get(fingerprint(df)) or {}).get("answers", {}).get(question.strip())
    return Answer(e["text"], "cached:" + str(e.get("source") or "offline")) if isinstance(e, dict) and isinstance(e.get("text"), str) else None


def answer(question: str, tools: Tools, client: Callable | None = None, cache: dict | None = None,
           history: list | None = None) -> Answer:
    """LLM (if a client) -> cached example -> offline answer. A failed LLM call falls back with its error kept."""
    err = None
    if client is not None:
        a = ask_llm(question, tools, client, history)
        if not a.error:
            return a
        err = a.error
    c = cached(tools._df, question, cache or {})
    a = c or ask_offline(question, tools)
    a.error = err
    return a


def render(text: str, df: pd.DataFrame, allowed: set[str] | None = None) -> tuple[str, list[dict]]:
    """[D000123] -> [1]; returns (markdown, sources [{n, episode, title}]). Ids not in the log (or not returned by a
    tool, if ``allowed``) are dropped. Remaining codes/UTC are turned into plain language line by line."""
    by_event = dict(zip(df.event_id, df.episode)) if len(df) else {}
    eps = set(df.episode) if len(df) else set()
    sources: list[dict] = []
    index: dict[str, int] = {}

    def num(cid: str) -> str | None:
        if allowed is not None and cid not in allowed:
            return None
        ep = by_event.get(cid) or (cid if cid in eps else None)
        if ep is None:
            return None
        if ep not in index:
            index[ep] = len(sources) + 1
            g = df[df.episode == ep]
            sources.append({"n": index[ep], "episode": ep, "title": f"{plain_title(g)} · {C.local_time(g.t.min())}"})
        return f"⟦{index[ep]}⟧"

    def sub(m):
        ns = list(dict.fromkeys(x for x in (num(c.strip()) for c in m.group(1).split(",")) if x))
        return "".join(ns)

    lines = []
    for line in CITE.sub(sub, text or "").splitlines():
        lead = re.match(r"^\s*(?:[-*•]|\d+\.)\s+", line)
        body = _plain(line[lead.end():] if lead else line)
        lines.append(("- " if lead else "") + body + (":" if line.rstrip().endswith(":") and body else ""))
    md = "\n".join(lines)
    md = re.sub(r"⟦(\d+)⟧", r" [\1]", md).replace("  [", " [")
    return md, sources


def build_cache(decisions: Path, out: Path = CACHE, client: Callable | None = None) -> dict:
    df = core.load_decisions(decisions)
    runs = load_cache(out)
    entry = {"decisions": Path(decisions).name, "answers": {}}
    for q in suggestions(df):
        a = answer(q, Tools(df), client)
        entry["answers"][q] = {"text": a.text, "source": a.source.removeprefix("cached:")}
    runs[fingerprint(df)] = entry
    out.write_text(json.dumps({"note": "Example answers for the suggested questions, keyed by run fingerprint. "
                                       "Built by `python viewer/copilot.py --build-cache`.", "runs": runs},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return entry


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-cache", action="store_true", help="answer the suggested questions and store them")
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=CACHE)
    ap.add_argument("--ask", help="answer one question and print it")
    a = ap.parse_args(argv)
    client = default_client()
    if a.ask:
        df = core.load_decisions(a.decisions)
        ans = answer(a.ask, Tools(df), client)
        print(f"[{ans.source}] {ans.error or ''}\n{ans.text}")
        return 0
    entry = build_cache(a.decisions, a.out, client)
    for q, e in entry["answers"].items():
        print(f"Q: {q}\n[{e['source']}] {e['text']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
