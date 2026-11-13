"""Offline AI incident summaries (T24): 2–3 plain sentences per incident episode, for the viewer only.

    python scripts/summarise_incidents.py out/qs.jsonl --data data/quickstart [--out out/qs_summaries.json]

Explain-only (docs/DECISIONS.md D6): it reads an existing decisions file, never detects or decides, and never runs inside
``run.py`` / the holdout pipeline. Per incident it builds a compact fact sheet (friendly names, local times) and asks the
LLM configured in ``.env`` (``LLM_PROVIDER``/``LLM_API_KEY``/``LLM_MODEL`` via ``plato40/llm.py``, temperature 0) for a
summary. A guard rejects any summary that adds a number, time, id, room/resource or claims delivery/execution; then (and
without a key) a deterministic template is used. ``source: llm|template`` per incident; accepted LLM summaries are
cached by fact-sheet hash in ``.llm_cache/summaries.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plato40 import llm  # noqa: E402
from viewer import components as C  # noqa: E402
from viewer import core  # noqa: E402
from viewer import labels as L  # noqa: E402
from viewer.summaries import path_for  # noqa: E402
from viewer.views.incident import humanize, strictest  # noqa: E402

CACHE = ROOT / ".llm_cache" / "summaries.json"
SYSTEM = ("You explain incidents from the decision log of PLATO-40, a fictional 40-room hotel, to hotel staff. Write 2-3 "
          "short plain English sentences: what happened, what was done, what is still pending. Use only facts from the "
          "JSON. Do not add any number, time, room, staff member or equipment that is not in it. Proposed guest messages "
          "are not sent and approvals are mock: never say a message was sent, a repair was done, something was approved "
          "or money was saved. No ids, no markdown.")

_CODEWORD = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")
_RAW_ID = re.compile(r"\b(?:[A-Z]+_)*[A-Z]+_?\d+\b")
_CLAIM = re.compile(
    r"\b(?:we|it|they|he|she|has|have|had|was|were|is|are|been|got)\s+(?:been\s+|now\s+|already\s+)?"
    r"(?:delivered|texted|e-?mailed|messaged|informed|notified|told|repaired|fixed|replaced|executed|carried out|"
    r"completed|resolved|approved|granted|refunded|saved)\b"
    r"|\b(?:was|were|has been|have been|got)\s+sent\b"
    r"|\bsent\s+(?:the\s+|a\s+|an\s+)?(?:guests?|message|sms|text|e-?mail|notification|reply)\b"
    r"|\bsaved\b|\bsavings? of\b", re.I)
_HONEST = {"notify_guest": "Drafted a guest message (not sent)"}
_NAMES = sorted({v for v in [*L.RESOURCES.values(), *L.ASSETS.values(), *L.ROLES.values()]} | {"Room", "Floor"},
                key=len, reverse=True)


def _plain(text: str) -> str:
    """humanize() + no snake_case field names left (``eta_local`` -> ``eta local``)."""
    s = _CODEWORD.sub(lambda m: m.group(0).replace("_", " "), humanize(text))
    s = _RAW_ID.sub("", s)
    return re.sub(r"\s+([:,.;)])", r"\1", re.sub(r"\(\s*\)", "", s)).strip()


def fact_sheet(rows) -> dict:
    """Everything a summary may say about one episode, in viewer wording (no codes, no UTC)."""
    rows = rows.sort_values(["t", "event_id"])
    assets = list(dict.fromkeys(a for x in rows.affected_assets for a in x))
    res = [L.label("resource", r) for r in dict.fromkeys(rows.resource_id) if isinstance(r, str) and r]
    ev = [_plain(core.parse_evidence(e).detail or e) for x in rows.evidence for e in x]
    plans = [_plain(v) for v in [*dict.fromkeys(rows.verification_plan), *dict.fromkeys(rows.expected_outcome)]
             if isinstance(v, str) and v]
    return {
        "type": L.label("issue", rows.issue_type.iloc[0]),
        "assets": [L.label("asset", a) for a in assets[:6]] + ([f"{len(assets) - 6} more"] if len(assets) > 6 else []),
        "when": C.time_range(rows.t.min(), rows.t.max()),
        "priority": L.label("priority", min(rows.priority, key=lambda p: core.PRIO_RANK.get(p, 9))),
        "status": L.label("autonomy", strictest(rows)),
        "headline": C.headline(_plain(rows.reason.iloc[0]), limit=200),
        "steps": [{"time": C.local_time(r.t), "did": _HONEST.get(r.action) or L.label("action", r.action),
                   "needs_approval": bool(r.requires_human_approval), "status": L.label("autonomy", r.autonomy)}
                  for r in rows.itertuples()][:8],
        "n_steps": len(rows),
        "n_waiting_for_approval": int(rows.requires_human_approval.sum()),
        "guest_consent_needed": bool(rows.requires_guest_consent.any()),
        "staff_or_robots": res,
        "evidence": [e for e in dict.fromkeys(ev) if e][:5],
        "how_we_will_know": plans[:2],
        "effects_are_simulated": bool(rows.simulated_effect.notna().any()),
    }


def fact_hash(facts: dict, model: str = "") -> str:
    return hashlib.sha256(json.dumps([model, SYSTEM, facts], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def template(f: dict) -> str:
    done = list(dict.fromkeys(s["did"].lower() for s in f["steps"] if not s["needs_approval"]))
    wait = list(dict.fromkeys(s["did"].lower() for s in f["steps"] if s["needs_approval"]))
    out = [f"{f['type']} at {', '.join(f['assets']) or 'the building'}, {f['when']}: {f['headline'].rstrip('.')}."]
    if done:
        out.append("We " + ", then ".join(done[:3]) + ".")
    if wait:
        who = "a manager's approval" + (" and guest consent" if f["guest_consent_needed"] else "")
        out.append(f"Waiting for {who} (mock): " + ", ".join(wait[:2]) + ".")
    if f["how_we_will_know"] and len(out) < 3:
        out.append(f"We'll know it worked when: {f['how_we_will_know'][0].rstrip('.')}.")
    return " ".join(out)


def guard(text: str, f: dict) -> str | None:
    """Reason to reject ``text`` (None = accepted): anything not in the fact sheet, or a delivery/execution claim."""
    if not text or not text.strip():
        return "empty"
    sheet = json.dumps(f, ensure_ascii=False)
    low_sheet = sheet.lower()
    body = text.replace("PLATO-40", "")
    if len(text) > 700 or len(re.findall(r"[.!?](?:\s|$)", text)) > 4:
        return "too long"
    m = _RAW_ID.search(body)
    if m:
        return f"raw id {m.group(0)}"
    for n in _NUM.findall(body):
        if n not in sheet:
            return f"number {n} not in the fact sheet"
    for name in _NAMES:
        if re.search(rf"\b{re.escape(name)}\b", body, re.I) and name.lower() not in low_sheet:
            return f"{name} not in the fact sheet"
    m = _CLAIM.search(body)
    if m:
        return f"claims delivery/execution: {m.group(0)!r}"
    return None


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def summarise(decisions: Path, data: Path | None = None, out: Path | None = None,
              complete: Callable[[str, str, int], str | None] | None = None, model: str | None = None,
              cache_path: Path = CACHE) -> dict:
    """Write ``{episode: {text, source, fact_hash, rejected}}`` for every incident episode; returns the document."""
    df = core.load_decisions(decisions)
    cfg = llm.config()
    complete = complete or (llm.complete if cfg else None)
    model = model or (cfg or {}).get("model", "")
    cache = _load_cache(cache_path) if complete else {}
    res, calls = {}, 0
    inc = df[df.issue_type.isin(L.INCIDENT_TYPES)] if len(df) else df
    for ep, rows in (inc.groupby("episode", sort=False) if len(inc) else []):
        f = fact_sheet(rows)
        h = fact_hash(f, model)
        entry = {"text": template(f), "source": "template", "fact_hash": h, "rejected": None}
        if complete:
            text = cache.get(h)
            if text is None:
                calls += 1
                text = (complete(SYSTEM, json.dumps(f, ensure_ascii=False), 250) or "").strip()
            why = guard(text, f)
            if why is None:
                entry.update(text=text, source="llm")
                cache[h] = text
            else:
                entry["rejected"] = why
        res[ep] = entry
    if complete and cache:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    doc = {"decisions": Path(decisions).name, "data": Path(data).name if data else None,
           "model": model or None, "llm_calls": calls, "summaries": res}
    out = Path(out) if out else path_for(decisions)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("decisions", type=Path)
    ap.add_argument("--data", type=Path, help="dataset directory of the run (recorded in the output)")
    ap.add_argument("--out", type=Path, help="default: <decisions stem>_summaries.json next to the decisions file")
    ap.add_argument("--no-llm", action="store_true", help="template summaries only")
    a = ap.parse_args(argv)
    if a.no_llm:
        llm.set_enabled(False)
    t0 = time.time()
    doc = summarise(a.decisions, a.data, a.out)
    s = doc["summaries"].values()
    n_llm = sum(e["source"] == "llm" for e in s)
    print(f"{len(doc['summaries'])} incident(s): {n_llm} llm, {len(doc['summaries']) - n_llm} template, "
          f"{sum(bool(e['rejected']) for e in s)} rejected by the guard, {doc['llm_calls']} llm call(s), "
          f"{time.time() - t0:.1f}s -> {a.out or path_for(a.decisions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
