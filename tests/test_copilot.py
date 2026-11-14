"""T25: "Ask the building" copilot - read-only tools, mock-client tool use, citations, offline/cached answers, page."""
import json
import re

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from viewer import copilot as CP  # noqa: E402
from viewer import core  # noqa: E402
from viewer import labels as L  # noqa: E402

import viewer_apptest as V  # noqa: E402

RAW = [re.compile(p) for p in (r"\bD\d{6}\b", r"\b[A-Z]{2}-\d{5}\b", r"\bSH\d{6}\b", r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
                               r"\.(?:csv|jsonl)\b", r"\bR\d{3}\b", r":material/")]


@pytest.fixture(scope="module")
def qs(tmp_path_factory):
    return V.quickstart_decisions(tmp_path_factory.mktemp("cp"))


@pytest.fixture(scope="module")
def df(qs):
    return core.load_decisions(qs)


def _no_raw(md):
    for rx in RAW:
        assert not rx.search(md), (rx.pattern, md)


def script(*steps):
    """Mock Claude: each step is a list of tool_use dicts or a callable(messages) -> final text."""
    seen = []

    def client(system, messages, tools, timeout):
        assert "PLATO-40" in system and CP.NOT_IN_LOG in system and timeout <= CP.TIMEOUT_S
        assert {t["name"] for t in tools} == {s["name"] for s in CP.TOOL_SPECS}
        seen.append(messages)
        step = steps[min(len(seen) - 1, len(steps) - 1)]
        if callable(step):
            return {"content": [{"type": "text", "text": step(messages)}], "stop_reason": "end_turn"}
        return {"content": [{"type": "tool_use", "id": f"tu{i}", "name": n, "input": a}
                            for i, (n, a) in enumerate(step)], "stop_reason": "tool_use"}
    client.seen = seen
    return client


def _last_result(messages):
    return json.loads(messages[-1]["content"][0]["content"])


def test_mock_client_dispatches_tools_and_cites(df):
    def final(messages):
        inc = _last_result(messages)["incidents"][0]
        return f"Room 104 had a water leak [{inc['first_decision_id']}]."

    client = script([("list_incidents", {"asset": "Room 104", "type": "water_leak"})], final)
    tools = CP.Tools(df)
    a = CP.answer("What happened in Room 104?", tools, client)
    assert a.source == "llm" and not a.error and a.tool_calls == [("list_incidents", {"asset": "Room 104",
                                                                                      "type": "water_leak"})]
    res = _last_result(client.seen[1])
    assert res["count"] == 1 and res["incidents"][0]["type"] == "Water leak"
    md, src = CP.render(a.text, df, tools.cited)
    assert md == "Room 104 had a water leak [1]." and len(src) == 1
    assert src[0]["episode"] == df[df.event_id == res["incidents"][0]["first_decision_id"]].episode.iloc[0]
    assert src[0]["title"].startswith("Water leak · Room 104")


def test_render_drops_unknown_and_uncited_ids(df):
    eid = df.event_id.iloc[0]
    md, src = CP.render(f"A [{eid}], B [D999999], C [{eid}, D999998].", df)
    assert md == "A [1], B, C [1]." and len(src) == 1
    md, src = CP.render(f"A [{eid}].", df, allowed=set())
    assert md == "A." and src == []
    md, _ = CP.render("- at 2026-01-12T17:45:00Z R104 had shift SH000054\n- second", df)
    assert md.startswith("- at Mon 12 Jan, 18:45 Room 104") and md.endswith("\n- second")
    _no_raw(md)


def test_unknown_question_is_not_in_the_log(df):
    a = CP.answer("What is the weather tomorrow?", CP.Tools(df), script(lambda m: "That is not in the decision log."))
    assert CP.NOT_IN_LOG in a.text and a.source == "llm"
    off = CP.answer("What is the weather tomorrow?", CP.Tools(df))
    assert CP.NOT_IN_LOG in off.text and off.source == "offline"


def test_tools_are_read_only(df, tmp_path):
    before = df.copy(deep=True)
    approvals = {df.event_id.iloc[0]: {"status": "approved"}}
    snap = json.dumps(approvals)
    t = CP.Tools(df, approvals)
    ep, eid = df.episode.iloc[0], df.event_id.iloc[0]
    for name, args in [("list_incidents", {}), ("list_incidents", {"needs_approval": True, "include_routine": True,
                                                                    "date_from": "2026-01-10", "date_to": "2026-01-20"}),
                       ("get_incident", {"episode_id": ep}), ("get_decision", {"event_id": eid}),
                       ("get_evidence", {"episode_id": ep}), ("count_by_type", {}), ("pending_approvals", {}),
                       ("get_incident", {"episode_id": "nope"}), ("get_decision", {"event_id": "D999999"})]:
        res = t.call(name, args)
        assert isinstance(res, dict) and json.dumps(res, default=str)
    for bad in ("record_approval", "approve", "write_file", "__init__", "_dec"):
        assert "unknown tool" in t.call(bad, {"event_id": eid})["error"]
    assert "bad arguments" in t.call("get_incident", {"wrong": 1})["error"]
    assert df.equals(before) and json.dumps(approvals) == snap
    assert not any(n.startswith(("record", "approve", "set_", "write", "save")) for n in dir(t))
    assert CP.NOT_IN_LOG in t.call("get_decision", {"event_id": "D999999"})["error"]


def test_tool_call_limit_timeout_and_errors_fall_back(df):
    loop = script([("count_by_type", {})])
    a = CP.ask_llm("How many?", CP.Tools(df), loop)
    assert a.error and "6" in a.error and len(a.tool_calls) == CP.MAX_TOOL_CALLS

    def boom(*a, **k):
        raise OSError("network down")
    b = CP.answer("How many incidents of each type are there?", CP.Tools(df), boom)
    assert b.error and b.source == "offline" and "Water leak" in b.text
    c = CP.ask_llm("x", CP.Tools(df), script([("count_by_type", {})]), timeout=0)
    assert c.error and "30 s" in c.error


def test_offline_answers_to_suggestions_are_cited_and_plain(df):
    qs = CP.suggestions(df)
    assert len(qs) == 4 and qs[0] == "What happened in Room 104?" and "24 Jan" in qs[2]
    for q in qs:
        t = CP.Tools(df)
        a = CP.answer(q, t)
        md, src = CP.render(a.text, df)
        assert a.source == "offline" and CP.NOT_IN_LOG not in a.text, (q, a.text)
        _no_raw(md)
        if "How many" not in q:
            assert src and "[1]" in md, q
    assert "Called the on-call housekeeper" in CP.answer(qs[2], CP.Tools(df)).text


def test_committed_cache_matches_quickstart_and_rebuilds(df, qs, tmp_path):
    cache = CP.load_cache()
    assert CP.fingerprint(df) in cache, "rebuild: python viewer/copilot.py --build-cache --decisions out/qs.jsonl"
    for q in CP.suggestions(df):
        a = CP.cached(df, q, cache)
        assert a and a.source.startswith("cached:")
        assert CP.answer(q, CP.Tools(df), None, cache).text == a.text
    out = tmp_path / "c.json"
    entry = CP.build_cache(qs, out)
    assert set(entry["answers"]) == set(CP.suggestions(df))
    assert CP.load_cache(out)[CP.fingerprint(df)]["answers"] == cache[CP.fingerprint(df)]["answers"]
    assert CP.load_cache(tmp_path / "missing.json") == {}


def test_page_without_key(qs, monkeypatch, tmp_path):
    monkeypatch.setenv("PLATO40_NO_LLM", "1")
    at = V.app(monkeypatch, qs, V.ROOT / "data" / "quickstart", tmp_path / "a.json")
    at.switch_page("views/ask.py")
    at.run()
    assert not at.exception, V.errors(at)
    assert any(L.ASK_NO_KEY in i.value for i in at.info)
    at.button(key="ask_suggest_0").click().run()
    assert not at.exception, V.errors(at)
    md = " ".join(m.value for m in at.markdown)
    assert "Water leak · Room 104" in md and "[1]" in md
    assert any(L.CACHED_ANSWER in c.value for c in at.caption)
    at.chat_input[0].set_value("Why was a technician sent on 15 Jan?").run()
    assert not at.exception, V.errors(at)
    md = " ".join(m.value for m in at.markdown)
    assert "Sent a technician" in md
    assert any(c.value == L.OFFLINE_ANSWER for c in at.caption)
    _no_raw(" ".join(m.value for m in at.markdown))


# ----------------------------------------------------------------------------- building-data tools
@pytest.fixture(scope="module")
def bd():
    from plato40 import io
    from viewer import twin as TW
    d = io.load(V.ROOT / "data" / "quickstart")
    return d, core.SeriesStore(d), TW.Twin(d)


def _tools(df, bd, now="2026-01-12T17:45Z"):
    d, s, tw = bd
    return CP.Tools(df, {}, data=d, store=s, twin=tw, now=pd.Timestamp(now))


def test_building_tools_answer_with_friendly_names_and_local_times(df, bd):
    t = _tools(df, bd)
    r = t.call("room_status", {"room": "Room 104"})
    assert r["room"] == "Room 104" and r["at"] == "Mon 12 Jan, 18:45" and "temperature_c" in r
    assert r["open_incidents"] and r["open_incidents"][0]["title"].startswith("Water leak")
    b = t.call("building_now", {})
    assert sum(b["rooms"].values()) == 40 and set(b["plant"]) == {"Heat pump", "Ventilation", "Water main",
                                                                   "PV / battery"}
    m = t.call("measurements", {"subject": "Room 305", "metric": "water", "date_from": "2026-01-10",
                                "date_to": "2026-01-12"})
    assert m["subject"] == "Room 305" and m["measurements"][0]["unit"] == "L/15 min"
    assert t.charts and t.charts[-1]["title"].startswith("Room 305")
    g = _tools(df, bd, "2026-01-24T12:00Z").call("guest_requests", {"room": "Room 104"})
    assert g["count"] >= 1 and all(r["room"] == "Room 104" for r in g["requests"])
    for bad in (("room_status", {"room": "Room 999"}), ("measurements", {"subject": "moon"}),
                ("measurements", {"subject": "heat pump", "metric": "colour"})):
        assert "error" in t.call(*bad)
    blob = json.dumps([r, b, m, g], ensure_ascii=False, default=str)
    for rx in (r"\bR\d{3}\b", r"\bSH\d{6}\b", r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", r"\b(?:CLEANER|ROBOT|TECH)_\d\b"):
        assert not re.search(rx, blob), rx


def test_building_tools_use_only_what_is_known_at_now(df, bd):
    early, late = _tools(df, bd, "2026-01-12T17:45Z"), _tools(df, bd, "2026-01-24T06:00Z")
    assert early.call("staff_schedule", {"date": "2026-01-24"})["absent"] == []        # sick notice known 06:30
    assert late.call("staff_schedule", {})["absent"] == ["Cleaner 1"]
    a1 = early.call("arrivals_departures", {"date": "2026-01-23"})["arrivals"]
    a2 = _tools(df, bd, "2026-01-23T06:00Z").call("arrivals_departures", {})["arrivals"]
    assert a1 <= a2                                                                    # later bookings appear later
    today = early.call("measurements", {"subject": "heat pump"})
    assert all(x["latest_at"] <= "Mon 12 Jan, 18:45" for x in today["measurements"])
    assert early.charts[-1]["end"] == pd.Timestamp("2026-01-12T18:00Z")


def test_building_tools_are_read_only_and_need_data(df, bd):
    d, s, _ = bd
    before = [x.copy(deep=True) for x in (d.telemetry, d.bookings, d.roster, d.guest_requests)]
    t = _tools(df, bd)
    for name in ("room_status", "building_now", "measurements", "staff_schedule", "arrivals_departures",
                 "guest_requests"):
        args = {"room": "Room 101"} if name == "room_status" else {"subject": "building"} if name == "measurements" else {}
        assert isinstance(t.call(name, args), dict)
        assert "not loaded" in CP.Tools(df).call(name, args)["error"]
    assert all(a.equals(b) for a, b in zip(before, (d.telemetry, d.bookings, d.roster, d.guest_requests)))


def test_offline_routes_building_questions(df, bd):
    def ask(q):
        t = _tools(df, bd)
        a = CP.answer(q, t)
        md, _ = CP.render(a.text, df)
        _no_raw(md)
        return a, t
    a, _ = ask("How is Room 104 right now?")
    assert a.tool_calls[0][0] == "room_status" and "Room 104 at Mon 12 Jan, 18:45" in a.text
    a, _ = ask("What happened in Room 104?")
    assert a.tool_calls[0][0] == "list_incidents"
    a, _ = ask("Who is working on 24 Jan?")
    assert a.tool_calls[0][0] == "staff_schedule" and "Cleaner" in a.text
    a, t = ask("Show the heat pump today")
    assert a.tool_calls[0][0] == "measurements" and t.charts
    a, _ = ask("Who arrives on 13 Jan?")
    assert a.tool_calls[0][0] == "arrivals_departures" and "arrival" in a.text
    a, _ = ask("Can guests get an early check-in in Room 104?")
    assert not a.tool_calls or a.tool_calls[0][0] != "arrivals_departures"
    a, _ = ask("What is going on right now?")
    assert a.tool_calls[0][0] == "building_now" and "Building at" in a.text


def test_llm_gets_the_moment_and_new_tools(df, bd):
    seen = {}

    def client(system, messages, tools, timeout):
        seen["system"] = system
        seen["tools"] = {x["name"] for x in tools}
        return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    CP.answer("How is Room 104?", _tools(df, bd), client)
    assert "Now (the moment selected in the viewer) is Mon 12 Jan, 18:45 2026 (local 2026-01-12 18:45)" in seen["system"]
    assert {"room_status", "building_now", "measurements", "staff_schedule", "arrivals_departures",
            "guest_requests"} <= seen["tools"]


def test_page_draws_a_chart_for_a_measurement(qs, monkeypatch, tmp_path):
    monkeypatch.setenv("PLATO40_NO_LLM", "1")
    at = V.app(monkeypatch, qs, V.ROOT / "data" / "quickstart", tmp_path / "a.json")
    at.switch_page("views/ask.py")
    at.run()
    at.chat_input[0].set_value("Show the heat pump on 12 Jan").run()
    assert not at.exception, V.errors(at)
    assert "Heat pump" in " ".join(m.value for m in at.markdown)
    assert len(at.get("plotly_chart")) == 1


def test_measurements_and_requests_stop_at_now(df, bd):
    t = _tools(df, bd)
    day = t.call("measurements", {"subject": "heat pump", "date_from": "2026-01-12"})
    assert all(x["latest_at"] <= "Mon 12 Jan, 18:45" for x in day["measurements"])
    assert "after now" in t.call("measurements", {"subject": "heat pump", "date_from": "2026-01-20"})["error"]
    assert all(r["time"] <= "Mon 12 Jan, 18:45" for r in t.call("guest_requests", {})["requests"]
               if r["time"].startswith("Mon 12 Jan"))
    assert _tools(df, bd).call("guest_requests", {"room": "Room 104"})["count"] == 0      # its requests come later


def test_dates_outside_the_data_are_a_clear_error(df, bd):
    t = _tools(df, bd)
    for name, args in (("measurements", {"subject": "heat pump", "date_from": "2025-01-11"}),
                       ("staff_schedule", {"date": "2025-01-12"}), ("arrivals_departures", {"date": "2027-01-01"}),
                       ("room_status", {"room": "Room 104", "time": "2025-01-12 10:00"})):
        err = t.call(name, args)["error"]
        assert "outside the data" in err and "2026-01-05" in err, (name, err)


def test_multi_day_measurements_have_per_day_values(df, bd):
    m = _tools(df, bd).call("measurements", {"subject": "heat pump", "metric": "cop", "date_from": "2026-01-11",
                                              "date_to": "2026-01-12"})["measurements"][0]
    assert [d["day"] for d in m["by_day"]] == ["Sun 11 Jan", "Mon 12 Jan"]
    assert min(d["min"] for d in m["by_day"]) == m["min"] and max(d["max"] for d in m["by_day"]) == m["max"]
    one = _tools(df, bd).call("measurements", {"subject": "heat pump", "date_from": "2026-01-11"})["measurements"][0]
    assert "by_day" not in one
