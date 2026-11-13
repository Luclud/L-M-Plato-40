"""T12: BMS alarms vs our episodes (analysis/bms_compare.py)."""
import json

import pandas as pd
import pytest

from conftest import QS, ROOT


def _ev(rows):
    df = pd.DataFrame([{"event_id": f"E{i}", "type": "bms_alarm", "value": None, **r} for i, r in enumerate(rows)])
    df["timestamp_utc"] = pd.to_datetime(df.timestamp_utc, utc=True)
    return df


def test_alarm_pairing_open_and_duplicate_raise():
    from analysis.bms_compare import alarm_episodes
    end = pd.Timestamp("2026-01-03T00:00Z")
    ev = _ev([
        {"timestamp_utc": "2026-01-01T01:00Z", "alarm_code": "X", "state": "raised", "asset_id": "A"},
        {"timestamp_utc": "2026-01-01T02:00Z", "alarm_code": "X", "state": "raised", "asset_id": "A"},   # still open
        {"timestamp_utc": "2026-01-01T03:30Z", "alarm_code": "X", "state": "cleared", "asset_id": "A"},
        {"timestamp_utc": "2026-01-01T04:00Z", "alarm_code": "X", "state": "cleared", "asset_id": "A"},  # orphan
        {"timestamp_utc": "2026-01-02T00:00Z", "alarm_code": "X", "state": "raised", "asset_id": "B"},   # never cleared
    ])
    a = alarm_episodes(ev, end)
    assert len(a) == 2
    first = a.iloc[0]
    assert (first.asset_id, first.duration_h, bool(first.open_at_end)) == ("A", 2.5, False)
    assert a.iloc[1].cleared_utc == end and bool(a.iloc[1].open_at_end)


def test_asset_relations_from_building(qs):
    from analysis.bms_compare import Assets
    a = Assets(qs)
    room = sorted(a.rooms)[0]
    meter = a.room_meter[room]
    assert a.related("WM_MAIN", [meter]) and a.related(meter, [room])
    other = sorted(a.rooms)[-1]
    assert not a.related(room, [other]) and not a.related("HP1", [room])
    gw = next(g for g, rs in a.gw_rooms.items() if room in rs)
    assert a.related(gw, [room]) and a.related("BUILDING", ["HP1"])


@pytest.fixture(scope="module")
def qs_result(tmp_path_factory):
    if not (QS / "manifest.json").exists():
        pytest.skip("quickstart data missing")
    import run
    from analysis import bms_compare
    d = tmp_path_factory.mktemp("bms")
    dec = d / "qs.jsonl"
    assert run.main([str(QS), "--out", str(dec)]) == 0
    out = d / "bms_compare.json"
    assert bms_compare.main([str(dec), str(QS), "--out", str(out),
                             "--labels", str(ROOT / "eval" / "labels_quickstart.json")]) == 0
    return json.loads(out.read_text()), (d / "bms_compare.md").read_text()


def test_quickstart_r104_ours_before_bms_before_work_order(qs_result):
    res, md = qs_result
    r104 = next(i for i in res["incidents"] if "R104" in i["assets"])
    assert r104["bms_first_code"] == "WATER_NIGHT_FLOW"
    ours, bms, wo = (pd.Timestamp(r104[k]) for k in ("ours_first_utc", "bms_first_utc", "work_order_utc"))
    assert ours < bms < wo
    assert r104["lead_vs_bms_h"] > 0 and r104["lead_vs_work_order_h"] > r104["lead_vs_bms_h"]
    assert "| QS-S02-R104-burst |" in md and "WATER_NIGHT_FLOW" in md


def test_quickstart_summary_consistent(qs_result):
    res, md = qs_result
    v = res["volume"]
    assert v["bms_events"] >= v["bms_alarm_episodes"] == len(res["alarms"]) > 0
    assert v["bms_matched"] + v["bms_unmatched"] == v["bms_alarm_episodes"]
    assert sum(c["raised"] for c in res["per_code"]) == v["bms_alarm_episodes"]
    assert sum(r["episodes"] for r in res["per_issue"]) == v["our_episodes"]
    wnf = next(c for c in res["per_code"] if c["alarm_code"] == "WATER_NIGHT_FLOW")
    assert wnf["matched"] == wnf["raised"]
    # the subtle toilet leak never reaches the BMS but is caught by us
    assert res["incident_summary"]["bms_missed_ours_detected"] >= 1
    for c in res["per_code"]:
        assert sum(c["unmatched_reasons"].values()) == c["unmatched"]
    assert md.startswith("## BMS alarms vs our episodes") and "| alarm code |" in md


def test_pipeline_does_not_import_bms_compare():
    for p in (ROOT / "plato40").rglob("*.py"):
        assert "bms_compare" not in p.read_text(), p


def test_weak_labels_from_work_orders(qs):
    from analysis.bms_compare import work_order_incidents
    inc = work_order_incidents(qs)
    types = {i["issue_type"] for i in inc}
    assert {"water_leak", "heating_comfort", "ventilation_maintenance"} <= types
    assert not any("Guest-reported room defect" in i["notes"] for i in inc)
    for i in inc:
        assert pd.Timestamp(i["start_utc"]) < pd.Timestamp(i["end_utc"])
    # consecutive work orders on the same asset do not overlap
    by = {}
    for i in inc:
        by.setdefault(i["assets"][0], []).append(i)
    for xs in by.values():
        for a, b in zip(xs, xs[1:]):
            assert pd.Timestamp(b["start_utc"]) > pd.Timestamp(a["end_utc"])
