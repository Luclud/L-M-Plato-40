"""T31 Guest messages: threads, on-time, consent mock (pure) + AppTest on quickstart, dev and empty decisions."""
import hashlib
import json

import pandas as pd
import pytest

from plato40 import io
from viewer import conversations as K
from viewer import core

import viewer_apptest as V

PAGE = "views/guests.py"


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_guests"))


@pytest.fixture(scope="module")
def dev():
    return io.load(V.ROOT / "data" / "dev"), core.load_decisions(V.ROOT / "decisions.jsonl")


def test_dev_threads_one_per_request(dev):
    data, df = dev
    th = K.threads(data, df)
    assert len(th) == len(data.guest_requests) == th.request_id.nunique()
    linked = {x for ids in df.guest_request_ids for x in ids}
    assert (th.n_decisions > 0).sum() == len(linked & set(th.request_id))
    no_reply = th[th.n_decisions == 0]
    assert len(no_reply) == len(th) - len(linked & set(th.request_id)) and no_reply.on_time.isna().all()
    assert th.t.is_monotonic_decreasing


def test_on_time_and_response_minutes_are_computed(dev):
    data, df = dev
    th = K.threads(data, df)
    r = th[th.first_reply.notna()]
    assert (r.response_minutes >= 0).all()
    assert (r.on_time == (r.first_reply <= r.due)).all()
    s = K.summary(th, {}, df)
    assert s["on_time_pct"] == pytest.approx(100 * r.on_time.astype(bool).mean())
    assert s["median_minutes"] == pytest.approx(r.response_minutes.median())
    assert s["consent_open"] == int(df.requires_guest_consent.sum())


def test_on_time_false_when_reply_after_deadline():
    data = type("D", (), {})()
    data.guest_requests = pd.DataFrame([{
        "request_id": "GRX", "timestamp_utc": pd.Timestamp("2026-01-01T10:00Z"), "room_id": "R101",
        "booking_id": "B", "channel": "email", "language": "it", "request_type": "invoice", "text": "Fattura?",
        "priority": "low", "response_due_utc": pd.Timestamp("2026-01-01T10:30Z"), "requires_guest_consent": "x"}])
    df = pd.DataFrame([{"event_id": "D1", "timestamp_utc": "2026-01-01T11:00:00Z", "guest_request_ids": ["GRX"],
                        "message": {"text": "Grazie"}, "requires_guest_consent": False, "episode": "E1"}])
    r = K.threads(data, df).iloc[0]
    assert r.response_minutes == 60 and not r.on_time


def test_quickstart_gr000034(sets):
    dec, d = sets["quickstart"]
    th = K.threads(io.load(d), core.load_decisions(dec))
    r = th[th.request_id == "GR000034"].iloc[0]
    assert r.text.startswith("Zu kalt zum Schlafen") and r.room_id == "R101" and r.on_time
    df = core.load_decisions(dec)
    rows = K.thread_decisions(df, r.event_ids)
    assert rows.message.iloc[0]["language"] == "de"
    assert "heating_comfort" in set(df[df.episode.isin(r.episodes)].issue_type)


def test_record_consent_writes_only_consents_file(tmp_path):
    p = tmp_path / "consents.json"
    K.record_consent(p, "D1", "yes")
    cur = K.record_consent(p, "D2", "no")
    assert cur == K.load_consents(p) and cur["D1"]["status"] == "yes" and cur["D2"]["status"] == "no"
    assert "not granted" in json.loads(p.read_text())["_note"]
    assert [x.name for x in tmp_path.iterdir()] == ["consents.json"]
    with pytest.raises(ValueError):
        K.record_consent(p, "D3", "maybe")
    assert K.consents_path(tmp_path / "approvals.json") == p


def _open(monkeypatch, tmp_path, sets, name, request=None):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page(PAGE)
    if request:
        at.query_params["request"] = request
    at.run()
    assert not V.errors(at)
    return at


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_guests_page_apptest(sets, monkeypatch, tmp_path, name):
    at = _open(monkeypatch, tmp_path, sets, name)
    text = " ".join(m.value for m in at.markdown) + " ".join(x.value for x in at.caption)
    assert "Guest requests" in [m.label for m in at.metric]
    assert len([b for b in at.button if b.key and b.key.startswith("g_GR")]) == 25
    assert "GR0" not in " ".join(x.value for x in at.caption)              # ids only in Technical details
    if name == "empty":
        assert any("No reply" in w.value for w in at.warning)
    else:
        assert "Proposed message — not sent" in text


def test_gr000034_thread_and_consent_mock(sets, monkeypatch, tmp_path):
    dec, _ = sets["quickstart"]
    before = hashlib.sha256(dec.read_bytes()).hexdigest()
    at = _open(monkeypatch, tmp_path, sets, "quickstart", "GR000034")
    text = " ".join(m.value for m in at.markdown)
    caps = " ".join(x.value for x in at.caption)
    assert "Zu kalt zum Schlafen" in text and "Wir wissen von dem Temperaturproblem" in text
    assert "reply due 18:56" in caps and "Room 101" in caps
    assert any("Heating" in p.label or "heat" in p.label.lower() for p in at.get("page_link"))
    df = core.load_decisions(dec)
    ev = df[df.requires_guest_consent & df.guest_request_ids.map(len).gt(0)].iloc[0]
    at.query_params["request"] = ev.guest_request_ids[0]
    at.run()
    assert any("consent is needed, not granted" in x.value for x in at.caption)
    at.button(key=f"cy_{ev.event_id}").click().run()
    assert not V.errors(at)
    assert K.load_consents(tmp_path / "consents.json")[ev.event_id]["status"] == "yes"
    at.run()
    assert any("Consent: yes (mock)" in m.value for m in at.markdown)
    assert not (tmp_path / "appr.json").exists()
    assert hashlib.sha256(dec.read_bytes()).hexdigest() == before


def test_filters_and_no_match(sets, monkeypatch, tmp_path):
    at = _open(monkeypatch, tmp_path, sets, "quickstart")
    at.multiselect(key="g_lang").set_value(["it"]).run()
    assert all("🇮🇹" in b.label for b in at.button if b.key and b.key.startswith("g_GR"))
    at.text_input(key="g_q").set_value("zzzz-no-such-text").run()
    assert any("No guest request matches" in i.value for i in at.info)


def test_filter_that_excludes_selection_switches_thread(sets, monkeypatch, tmp_path):
    at = _open(monkeypatch, tmp_path, sets, "quickstart", "GR000034")
    at.multiselect(key="g_lang").set_value(["en"]).run()
    assert not V.errors(at)
    assert not any("Zu kalt zum Schlafen" in m.value for m in at.markdown)
