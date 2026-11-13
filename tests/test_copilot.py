"""T25: "Ask the building" copilot - read-only tools, mock-client tool use, citations, offline/cached answers, page."""
import json
import re

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
