"""AI wording for the "What we learned" cards, generated once and stored.

The Level 3 insights themselves (``insights.md`` / ``insights.jsonl``) are computed by ``analysis/insights.py`` and stay
the deliverable. This module only rewrites each card's headline, finding and recommendation in plain language for the
viewer. Guard: the AI text may not contain any number that is not already in the insight; otherwise it is rejected and
the original wording is shown. Results are stored per insight (keyed by a hash of its source text and the model), so
each insight costs one model call, ever: ``out/insights_ai.json`` (writable, mounted in Docker), with a committed copy
in ``viewer/insights_ai.json`` as fallback for installations without an API key.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from plato40 import llm

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "out" / "insights_ai.json"
SHIPPED = Path(__file__).resolve().parent / "insights_ai.json"
FIELDS = ("topic", "finding", "recommendation", "expected_benefit")

SYSTEM = (
    "You rewrite one finding from a year of operating data of PLATO-40, a fictional 40-room self-check-in hotel, for "
    "the people who run the building and plan the next one. Write plain, concrete English, no jargon, no hype. "
    "Use ONLY facts and claims stated in the given insight; never draw a new conclusion. Use at most three key "
    "numbers in total, each copied exactly with its unit and meaning; never add, round, combine or compute numbers, "
    "and never write a count or 'X of Y' that the insight does not state. Range edges such as '-40..-5 °C' or "
    "'10..40 °C' are bin labels: describe them in words ('the coldest days', 'mild days'), never quote the edges. "
    "The headline contains no numbers. Do not claim savings or real-world results; the data is synthetic. "
    "Say 'PLATO-40' when you name the building. Reply with JSON only: "
    '{"headline": "<at most 9 words, no numbers>", "finding": "<1-2 sentences: what the data shows>", '
    '"recommendation": "<1-2 sentences: what to do>"}'
)

_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    """Number tokens, thousands separators removed ('177,083' == '177083'), trailing zeros of decimals ignored."""
    out = set()
    for m in _NUM.findall(text or ""):
        n = m.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", m) else m.replace(",", ".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


def source_text(r: dict) -> str:
    return "\n".join(f"{k}: {r.get(k)}" for k in FIELDS if r.get(k))


def key_for(r: dict, model: str) -> str:
    return hashlib.sha256(json.dumps([source_text(r), model, SYSTEM], ensure_ascii=False).encode()).hexdigest()[:16]


def extra_numbers(text: dict, r: dict) -> set[str] | None:
    """Numbers in the AI text that are not in the insight (incl. its id and numbers); None if the text is incomplete."""
    if not isinstance(text, dict) or not all(isinstance(text.get(k), str) and text[k].strip()
                                             for k in ("headline", "finding", "recommendation")):
        return None
    src = (source_text(r) + " " + json.dumps(r.get("numbers") or {})).replace("PLATO-40", "PLATO")
    allowed = _numbers(src)
    body = re.sub(r"PLATO-40", "PLATO", " ".join(text[k] for k in ("headline", "finding", "recommendation")))
    if _numbers(text["headline"].replace("PLATO-40", "PLATO")):
        return {"(number in headline)"}
    return _numbers(body) - allowed


def check(text: dict, r: dict) -> bool:
    """True if the AI text is complete and adds no number that is not in the insight."""
    return extra_numbers(text, r) == set()


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, flags=re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def load() -> dict:
    """Stored AI wordings: the writable store wins over the shipped copy."""
    return {**_read(SHIPPED), **_read(STORE)}


def entry(r: dict, store: dict) -> dict | None:
    """The stored AI wording for this insight if it still matches the insight's current text."""
    e = store.get(str(r.get("insight_id")))
    if not isinstance(e, dict) or e.get("source_hash") != key_for(r, e.get("model", "")):
        return None
    return e if check(e, r) else None


def missing(rows: list[dict], store: dict) -> list[dict]:
    return [r for r in rows if entry(r, store) is None]


def generate(rows: list[dict], complete: Callable[[str, str, int], str | None] | None = None,
             model: str | None = None) -> tuple[int, int]:
    """Write AI wordings for insights that have none yet; store each as soon as it passes the check.
    Returns (written, rejected). No API key -> (0, 0)."""
    cfg = llm.config()
    if complete is None:
        if cfg is None:
            return 0, 0
        complete = llm.complete
    model = model or (cfg or {}).get("model", "test")
    store, written, rejected = _read(STORE), 0, 0
    for r in missing(rows, load()):
        prompt = source_text(r)
        text = _parse(complete(SYSTEM, prompt, 400))
        extra = extra_numbers(text, r)
        if extra:                                       # one retry, told exactly which numbers are not allowed
            prompt += ("\n\nYour previous draft used numbers that are not in the insight: " + ", ".join(sorted(extra))
                       + ". Rewrite it using only numbers that appear above, copied exactly; leave a figure out "
                         "rather than compute it.")
            text = _parse(complete(SYSTEM, prompt, 400))
        if text is None or not check(text, r):
            rejected += 1
            continue
        store[str(r["insight_id"])] = {"headline": text["headline"].strip(), "finding": text["finding"].strip(),
                                        "recommendation": text["recommendation"].strip(), "model": model,
                                        "source_hash": key_for(r, model)}
        written += 1
        try:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    return written, rejected


def available() -> bool:
    return llm.config() is not None
