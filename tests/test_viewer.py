"""T10 viewer: pure logic in viewer/core.py (+ a Streamlit smoke test if streamlit is installed)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from plato40.decision import make_decision
from viewer import core

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "viewer" / "sample" / "quickstart_illustrative.jsonl"
T0 = pd.Timestamp("2026-01-12T18:00:00Z")


def _write(tmp_path, decisions):
    p = tmp_path / "d.jsonl"
    p.write_text("".join(json.dumps(d) + "\n" for d in decisions))
    return p


def _dec(eid, t, ep=None, **kw):
    d = make_decision(t=t, **{"affected_assets": ["R1"], "issue_type": "water_leak", "action": "flag_anomaly",
                              "priority": "high", "reason": "r", **kw}).to_json()
    d["event_id"] = eid
    if ep:
        d["episode_id"] = ep
    return d


def test_load_decisions_fills_episode_and_autonomy(tmp_path):
    d1 = _dec("D1", T0, ep="E1")
    d2 = _dec("D2", T0 + pd.Timedelta(hours=1), ep="E1", action="request_inspection", enters_occupied_room=True)
    d3 = _dec("D3", T0 - pd.Timedelta(hours=1))
    for d in (d1, d2, d3):
        d.pop("autonomy", None)
    df = core.load_decisions(_write(tmp_path, [d1, d2, d3]))
    assert list(df.event_id) == ["D3", "D1", "D2"]                      # sorted by time
    assert df.set_index("event_id").episode.to_dict() == {"D3": "D3", "D1": "E1", "D2": "E1"}
    assert df.set_index("event_id").autonomy["D2"] == "approval_and_consent"
    assert df.set_index("event_id").autonomy["D1"] == "autonomous"
    ep = core.episodes(df)
    assert len(ep) == 2 and ep.set_index("episode").decisions["E1"] == 2
    assert ep.set_index("episode").autonomy["E1"] == "approval_and_consent"
    s = core.summary(df, approvals={"D2": {"status": "approved"}})
    assert s["needs_approval"] == 1 and s["approvals_pending"] == 0 and s["consent_needed"] == 1


def test_empty_decisions_file(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("")
    df = core.load_decisions(p)
    assert df.empty and core.episodes(df).empty and core.summary(df)["episodes"] == 0


def test_malformed_decisions_line_reports_line_number(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"event_id": "D1"}\n{not json}\n')
    with pytest.raises(ValueError, match="line 2"):
        core.load_decisions(p)
    p.write_text('[1, 2]\n')
    with pytest.raises(ValueError, match="line 1"):
        core.load_decisions(p)


def test_labels_simulated_and_proposed():
    assert core.simulated_label("~45 min saved").startswith("SIMULATED")
    assert core.simulated_label("SIMULATED: x") == "SIMULATED: x"
    assert core.simulated_label({"kwh": 3}).startswith("SIMULATED")
    assert core.simulated_label(None) is None
    m = core.message_label({"language": "de", "text": "Hallo", "source": "template"})
    assert "PROPOSED" in m and "not delivered" in m and "(de, template)" in m
    assert "PROPOSED" in core.message_label("plain text")
    assert core.message_label(None) is None


def test_approval_note_mentions_consent_not_granted():
    row = {"approval_note": None, "affected_assets": ["R1"], "action": "water_shutoff", "reason": "leak",
           "requires_guest_consent": True}
    assert "not granted" in core.approval_note(row)


def test_parse_evidence():
    ev = core.parse_evidence("room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T18:00:00Z:~27 L")
    assert (ev.file, ev.asset) == ("room_water.csv", "R104")
    assert ev.start == pd.Timestamp("2026-01-12T17:00:00Z") and ev.end == pd.Timestamp("2026-01-12T18:00:00Z")
    free = core.parse_evidence("some free text")
    assert free.file is None and free.detail == "some free text"


def test_gap_intervals_missing_is_gap_not_zero():
    idx = pd.date_range("2026-01-01", periods=12, freq="15min", tz="UTC")
    s = pd.Series(1.0, index=idx)
    s.iloc[4:8] = float("nan")              # 1 h unknown
    s.iloc[1] = 0.0                         # a real zero is NOT a gap
    gaps = core.gap_intervals(s)
    assert gaps == [(idx[4], idx[8])]


def test_record_approval_is_local_mock(tmp_path):
    p = tmp_path / "out" / "approvals.json"
    core.record_approval(p, "D1", "approved", "d.jsonl")
    core.record_approval(p, "D2", "rejected", "d.jsonl", note="no")
    raw = json.loads(p.read_text())
    assert "SIMULATION" in raw["_note"]
    assert core.load_approvals(p)["D2"]["status"] == "rejected"
    with pytest.raises(ValueError):
        core.record_approval(p, "D3", "executed", "d.jsonl")
    assert core.load_approvals(tmp_path / "missing.json") == {}


def test_parse_eval_table():
    text = ("Incidents detected: 1/2\n"
            "incident                     scen       det   lat_h in_time  #dec  first decision\n"
            "QS-S02-R104-burst            S02        Y       1.0 yes         4  D000001 flag_anomaly @ x\n"
            "QS-S01-R308-0108             S01        -                       0\n\n")
    t = core.parse_eval_table(text)
    assert list(t.incident) == ["QS-S02-R104-burst", "QS-S01-R308-0108"]
    assert list(t.detected) == [True, False] and t.decisions.iloc[0] == "4"


def test_hero_presets_come_from_labels():
    labels = core.load_labels("quickstart")
    heroes = core.hero_presets(labels)
    assert len(heroes) == 4
    ids = {x["id"] for x in labels}
    assert all(h["label"] in ids and h["assets"] and h["chart"][0] for h in heroes)
    assert core.hero_presets([]) == []


@pytest.mark.skipif(not SAMPLE.exists(), reason="illustrative sample missing")
def test_sample_matches_heroes_and_passes_validate():
    df = core.load_decisions(SAMPLE)
    heroes = core.hero_presets(core.load_labels("quickstart"))
    for h in heroes:
        assert core.hero_window(h, df)[3], h["key"]
    assert (df.agent == "viewer_sample").all() and df.reason.str.startswith("ILLUSTRATIVE").all()
    ok, out = core.run_validate(SAMPLE, ROOT / "data" / "quickstart")
    assert ok, out


def test_series_store_charts(qs):
    store = core.SeriesStore(qs)
    room = sorted(qs.room_ids)[0]
    charts = store.charts_for("room_water.csv", room, "water_leak", start=qs.start, end=qs.start + pd.Timedelta(days=2))
    assert charts and len(next(iter(charts[0].series.values()))) > 0
    vent = store.charts_for("equipment.csv", None, "ventilation_maintenance")
    assert vent and "filter" in vent[0].title
    pytest.importorskip("plotly")
    assert core.figure(charts[0], [(qs.start, "flag_anomaly")]) is not None


@pytest.mark.skipif(not SAMPLE.exists(), reason="illustrative sample missing")
def test_streamlit_app_smoke(monkeypatch, tmp_path):
    pytest.importorskip("plotly")
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("PLATO40_DECISIONS", str(SAMPLE))
    monkeypatch.setenv("PLATO40_DATA", str(ROOT / "data" / "quickstart"))
    monkeypatch.setenv("PLATO40_APPROVALS", str(tmp_path / "approvals.json"))
    monkeypatch.setenv("PLATO40_DEFAULT_DATASET", "off")
    at = testing.AppTest.from_file(str(ROOT / "viewer" / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception
    for page in ["views/incidents.py", "views/approvals.py", "views/tour.py", "views/proof.py"]:
        at.switch_page(page)
        at.run()
        assert not at.exception, (page, [e.message for e in at.exception])
