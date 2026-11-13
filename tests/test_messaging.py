"""T09 - multilingual guest messages, requested-time parsing, approval notes, optional LLM."""
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from plato40 import llm, messaging
from plato40.messaging import (DELIVERY_PHRASES, KINDS, REQUIRED_FACTS, SUPPORTED_LANGUAGES,
                               approval_note, compose_guest_message, parse_requested_time)

ROOT = Path(__file__).resolve().parents[1]

FACTS = dict(room_id="R307", eta_local="13:45", earliest_local="15:00", until_local="12:30",
             proposed_window_local="10:00-11:30", reason="water_leak", request_type="extra_towels",
             slot_local="09:15", arrival_local="23:30", reply_by_local="08:45")
FACT_SLOTS = {"room_id": "R307", "eta_local": "13:45", "earliest_local": "15:00", "until_local": "12:30",
              "proposed_window_local": "10:00–11:30", "slot_local": "09:15"}
STUB_KINDS = ("room_ready_eta", "early_checkin_possible", "early_checkin_not_possible", "late_checkout_ok",
              "late_checkout_not_possible", "waiting_update", "maintenance_consent_request", "service_ack",
              "heating_update")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """Tests never touch the network, whatever the developer's .env says."""
    monkeypatch.setenv("PLATO40_NO_LLM", "1")


def test_stub_kinds_kept():
    assert set(STUB_KINDS) <= set(KINDS)
    assert set(KINDS) == set(REQUIRED_FACTS) == set(messaging.TEMPLATES)


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_language_renders_with_facts(kind, lang):
    m = compose_guest_message(kind, lang, **FACTS)
    assert m["language"] == lang and m["source"] == "template" and m["kind"] == kind
    assert "missing_facts" not in m
    text = m["text"]
    assert not re.search(r"[{}]|\bNone\b|\bnan\b|\bNaT\b", text), text
    for fact in REQUIRED_FACTS[kind]:
        assert FACT_SLOTS[fact] in text, (fact, text)
    if kind in ("early_checkin_not_possible",):
        assert "15:00" in text
    if kind == "maintenance_consent_request":
        assert "08:45" in text          # deadline for the guest's answer
    low = text.lower()
    assert not any(p in low for p in DELIVERY_PHRASES), text


@pytest.mark.parametrize("lang,marker", [("de", "Sie|Ihr|Ihre|Ihnen"), ("fr", "vous|votre"),
                                         ("it", "Sua|La|Lei"), ("es", "su|usted"), ("nl", "uw|u")])
def test_polite_register(lang, marker):
    for kind in ("room_ready_eta", "waiting_update", "early_checkin_not_possible"):
        text = compose_guest_message(kind, lang, **FACTS)["text"]
        assert re.search(rf"\b(?:{marker})\b", text, re.I), text
    if lang == "de":
        assert not re.search(r"\b(du|dein|dich|dir)\b", compose_guest_message("waiting_update", "de", **FACTS)["text"])


def test_languages_differ_and_fallback_language():
    texts = {lang: compose_guest_message("waiting_update", lang, **FACTS)["text"] for lang in SUPPORTED_LANGUAGES}
    assert len(set(texts.values())) == 6
    m = compose_guest_message("waiting_update", "pl", **FACTS)
    assert m["language"] == "en" and m["requested_language"] == "pl"
    assert compose_guest_message("waiting_update", None, **FACTS)["language"] == "en"
    assert compose_guest_message("waiting_update", "DE", **FACTS)["language"] == "de"


def test_missing_required_fact_gives_neutral_text_not_invention():
    m = compose_guest_message("waiting_update", "fr", room_id="R101")
    assert m["missing_facts"] == ["eta_local"]
    assert not re.search(r"\d\d:\d\d", m["text"])
    m = compose_guest_message("late_checkout_ok", "de", until_local="spät")
    assert m["missing_facts"] == ["until_local"]


def test_defaults_and_unknown_kind():
    assert "11:00" in compose_guest_message("late_checkout_not_possible", "en")["text"]
    assert "15:00" in compose_guest_message("early_checkin_not_possible", "nl")["text"]
    m = compose_guest_message("does_not_exist", "it", request_type="parking")
    assert m["kind"] == "service_ack" and "parcheggio" in m["text"]


def test_local_time_with_dst():
    # 12:15Z is 13:15 in winter (CET) and 14:15 in summer (CEST)
    w = compose_guest_message("room_ready_eta", "de", room_id="R101", eta_local=pd.Timestamp("2026-01-15T12:15Z"))
    s = compose_guest_message("room_ready_eta", "de", room_id="R101", eta_local="2026-07-15T12:15:00Z")
    assert "13:15" in w["text"] and "14:15" in s["text"]
    # DST switch day 29 Mar 2026: 00:30Z = 01:30 CET, 01:30Z = 03:30 CEST
    assert messaging.to_local_hm("2026-03-29T00:30:00Z") == "01:30"
    assert messaging.to_local_hm("2026-03-29T01:30:00Z") == "03:30"
    assert messaging.to_local_hm("9:05") == "09:05"
    m = compose_guest_message("maintenance_consent_request", "en", room_id="R2",
                              proposed_window_local=(pd.Timestamp("2026-07-01T08:00Z"), pd.Timestamp("2026-07-01T09:00Z")))
    assert "10:00–11:00" in m["text"]


@pytest.mark.parametrize("text,expected", [
    ("Wir kommen gegen 14:30 an.", "14:30"), ("Late Check-out bis 12:00?", "12:00"),
    ("Arriving at 12:45, room ready?", "12:45"), ("Nous arrivons vers 14h15.", "14:15"),
    ("Arriviamo alle 13:00.", "13:00"), ("Kunnen we om 13.00 uitchecken?", "13:00"),
    ("Llegamos a las 11:30.", "11:30"), ("ca. 9:15 Uhr", "09:15"), ("14 Uhr", "14:00"),
    ("around 2:30 pm", "14:30"), ("at 3pm", "15:00"), ("at 12 am", "00:00"),
    ("Wir kommen erst nach Mitternacht an.", "00:00"), ("Running late, arriving after midnight.", "00:00"),
    ("check-out at noon?", "12:00"), ("können wir um 14 kommen", "14:00"),
    ("Am 12.03.2026 kommen wir um 15 Uhr", "15:00"), ("Check out at 13:00 instead of 11?", "13:00"),
    ("We stay 2 nights.", None), ("Danke!", None), ("", None), (None, None), (float("nan"), None),
])
def test_parse_requested_time(text, expected):
    assert parse_requested_time(text) == expected


def _requests(profile):
    p = ROOT / "data" / profile / "guest_requests.csv"
    if not p.exists():
        pytest.skip(f"{p} missing")
    return pd.read_csv(p)


@pytest.mark.parametrize("profile", ["quickstart", "dev"])
def test_parse_coverage_on_dataset(profile):
    g = _requests(profile)
    el = g[g.request_type.isin(["early_checkin", "late_checkout"])]
    cov = el.text.map(parse_requested_time).notna().mean()
    ad = g[g.request_type == "arrival_delay"]
    cov_ad = ad.text.map(parse_requested_time).notna().mean() if len(ad) else 1.0
    print(f"{profile}: early_checkin+late_checkout {cov:.1%} of {len(el)}, arrival_delay {cov_ad:.1%} of {len(ad)}")
    assert cov >= 0.95 and cov_ad >= 0.95


def test_parsed_times_match_text_digits():
    g = _requests("dev")
    s = g[g.request_type.isin(["early_checkin", "late_checkout", "arrival_delay"])]
    for t in s.text:
        hm = parse_requested_time(t)
        m = re.search(r"(\d{1,2})[:.h](\d{2})", t)
        if m:
            assert hm == f"{int(m.group(1)):02d}:{m.group(2)}", t


def test_approval_note():
    n = approval_note("Suspected leak  R104\n(~27 L/15 min)", [f"room_water.csv:R104:{i}" for i in range(5)],
                      "request_inspection by TECH_1", "~100 L/h lost, water damage")
    lines = n.splitlines()
    assert [ln.split(":")[0] for ln in lines] == ["Why", "Proposal", "Evidence", "If no action"]
    assert "(+2 more)" in n and "R104" in lines[0] and "\n(" not in n
    assert "none given" in approval_note("x", [], "y", "z")


# --- optional LLM ------------------------------------------------------------------
def test_llm_off_without_config(monkeypatch):
    monkeypatch.delenv("PLATO40_NO_LLM")
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.setenv(k, "")
    monkeypatch.setattr(llm, "_env_cache", {})
    assert not llm.enabled() and llm.complete("s", "p") is None


def test_llm_wording_accepted_only_if_facts_kept(monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda s, p, max_tokens=300:
                        "Wir bitten um Entschuldigung. Ihr Zimmer R307 ist gegen 13:45 Uhr fertig.")
    m = compose_guest_message("waiting_update", "de", room_id="R307", eta_local="13:45")
    assert m["source"] == "llm" and "13:45" in m["text"] and "R307" in m["text"]
    # changed time -> rejected
    monkeypatch.setattr(llm, "complete", lambda s, p, max_tokens=300: "Ihr Zimmer R307 ist gegen 14:00 Uhr fertig.")
    m = compose_guest_message("waiting_update", "de", room_id="R307", eta_local="13:45")
    assert m["source"] == "template" and "13:45" in m["text"]
    # delivery claim -> rejected
    monkeypatch.setattr(llm, "complete", lambda s, p, max_tokens=300:
                        "We sent the info: your room R307 is ready at about 13:45.")
    assert compose_guest_message("waiting_update", "en", room_id="R307", eta_local="13:45")["source"] == "template"
    # LLM failure -> template
    monkeypatch.setattr(llm, "complete", lambda s, p, max_tokens=300: None)
    assert compose_guest_message("waiting_update", "en", room_id="R307", eta_local="13:45")["source"] == "template"


def test_llm_cache_and_error_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("PLATO40_NO_LLM")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "_broken", False)
    calls = []

    def fake_call(cfg, system, prompt, max_tokens):
        calls.append(prompt)
        return "hello"

    monkeypatch.setattr(llm, "_call", fake_call)
    assert llm.complete("s", "p") == "hello" and llm.complete("s", "p") == "hello"
    assert len(calls) == 1 and json.loads(next(tmp_path.glob("*.json")).read_text())["text"] == "hello"

    def boom(*a):
        raise OSError("network down")

    monkeypatch.setattr(llm, "_call", boom)
    assert llm.complete("s", "other") is None
    assert not llm.enabled()            # circuit breaker: no more calls this run
    monkeypatch.setattr(llm, "_broken", False)
    llm.set_enabled(False)
    try:
        assert not llm.enabled()
    finally:
        llm.set_enabled(True)


def test_plato40_does_not_import_network_by_default():
    src = (ROOT / "plato40" / "messaging.py").read_text()
    assert "urllib" not in src and "socket" not in src   # network only via plato40/llm.py
