"""T24: offline incident summaries (template fallback, guard, cache) and their display in the viewer."""
import importlib.util
import json
import re

import pytest

pytest.importorskip("streamlit")
from viewer import core  # noqa: E402
from viewer import labels as L  # noqa: E402
from viewer import summaries  # noqa: E402

import viewer_apptest as V  # noqa: E402

_spec = importlib.util.spec_from_file_location("summarise_incidents", V.ROOT / "scripts" / "summarise_incidents.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

RAW = [re.compile(p) for p in (r"\b[A-Z]{2}-\d{5}\b", r"\bD\d{6}\b", r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
                               r"\.(?:csv|jsonl)\b", r"\b[a-z]+(?:_[a-z]+)+\b", r"\b(?:[A-Z]+_)*[A-Z]+_?\d+\b")]


@pytest.fixture(scope="module")
def qs(tmp_path_factory):
    return V.quickstart_decisions(tmp_path_factory.mktemp("summ"))


@pytest.fixture(scope="module")
def facts(qs):
    df = core.load_decisions(qs)
    rows = df[(df.issue_type == "water_leak")]
    ep = rows.sort_values("t").episode.iloc[0]
    return S.fact_sheet(df[df.episode == ep])


def _incident_eps(path):
    df = core.load_decisions(path)
    return set(df[df.issue_type.isin(L.INCIDENT_TYPES)].episode)


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_template_for_every_incident_without_key(name, qs, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATO40_NO_LLM", "1")
    dec = {"quickstart": qs, "dev": V.ROOT / "decisions.jsonl", "empty": tmp_path / "empty.jsonl"}[name]
    if name == "empty":
        dec.write_text("")
    out = tmp_path / "s.json"
    doc = S.summarise(dec, out=out, cache_path=tmp_path / "cache.json")
    assert json.loads(out.read_text())["summaries"] == doc["summaries"]
    assert set(doc["summaries"]) == _incident_eps(dec) and doc["llm_calls"] == 0
    for e in doc["summaries"].values():
        assert e["source"] == "template" and e["rejected"] is None
        assert 2 <= len(re.findall(r"[.!?](?:\s|$)", e["text"])) <= 4, e["text"]
        for rx in RAW:
            assert not rx.search(e["text"]), (rx.pattern, e["text"])
    assert not (tmp_path / "cache.json").exists()


def test_template_passes_its_own_guard(qs):
    df = core.load_decisions(qs)
    for _, rows in df[df.issue_type.isin(L.INCIDENT_TYPES)].groupby("episode"):
        f = S.fact_sheet(rows)
        assert S.guard(S.template(f), f) is None, S.template(f)


def test_guard_rejects_new_numbers_ids_resources_and_claims(facts):
    ok = f"{facts['type']} at {facts['assets'][0]}, {facts['when']}. A manager still has to approve the next step."
    assert S.guard(ok, facts) is None
    room = re.search(r"\d{3}", facts["assets"][0]).group(0)
    bad = {
        "altered number": ok.replace(room, str(int(room) + 1)),
        "new number": ok + " About 999 litres were lost.",
        "raw id": ok.replace(facts["assets"][0], "R" + room),
        "decision id": ok + " See D000123.",
        "new resource": ok + " Cleaner 3 will help.",
        "delivery claim": ok + " We sent the guest a message.",
        "repair claim": ok + " The leak was repaired.",
        "approval claim": ok + " The shut-off was approved.",
        "savings claim": ok + " This saved money.",
        "empty": " ",
    }
    for what, text in bad.items():
        assert S.guard(text, facts), what


def test_mock_llm_summaries_are_guarded_and_cached(qs, tmp_path):
    calls = []

    def good(system, prompt, max_tokens):
        calls.append(prompt)
        f = json.loads(prompt)
        assert "PLATO-40" in system and "never say a message was sent" in system
        return f"{f['type']} at {f['assets'][0]}, {f['when']}. We {f['steps'][0]['did'].lower()}."

    cache = tmp_path / "cache.json"
    d1 = S.summarise(qs, out=tmp_path / "a.json", complete=good, model="mock", cache_path=cache)
    n = len(d1["summaries"])
    assert n and len(calls) == n == d1["llm_calls"]
    assert all(e["source"] == "llm" and e["rejected"] is None for e in d1["summaries"].values())
    d2 = S.summarise(qs, out=tmp_path / "b.json", complete=good, model="mock", cache_path=cache)
    assert len(calls) == n and d2["llm_calls"] == 0, "second run must be served from the cache"
    assert d2["summaries"] == d1["summaries"]

    def liar(system, prompt, max_tokens):
        f = json.loads(prompt)
        return f"{f['type']} at {f['assets'][0]}. We sent the guest a message and it was repaired by Cleaner 3."

    d3 = S.summarise(qs, out=tmp_path / "c.json", complete=liar, model="liar", cache_path=tmp_path / "c2.json")
    assert all(e["source"] == "template" and e["rejected"] for e in d3["summaries"].values())
    assert d3["summaries"].keys() == d1["summaries"].keys()


def test_default_path_and_viewer_load(qs, tmp_path):
    dec = tmp_path / "run.jsonl"
    dec.write_text(qs.read_text())
    assert summaries.path_for(dec) == tmp_path / "run_summaries.json"
    assert summaries.load(dec) == {}
    S.summarise(dec, complete=None, cache_path=tmp_path / "x.json")
    assert set(summaries.load(dec)) == _incident_eps(dec)
    summaries.path_for(dec).write_text("{broken")
    assert summaries.load(dec) == {}


def _text(at):
    return " ".join(str(x.value) for x in [*at.markdown, *at.caption])


def test_viewer_shows_ai_label_only_for_llm_summaries(qs, tmp_path, monkeypatch):
    dec = tmp_path / "run.jsonl"
    dec.write_text(qs.read_text())
    df = core.load_decisions(dec)
    water = df[df.issue_type == "water_leak"].sort_values("t").episode.iloc[0]
    other = df[df.issue_type == "ventilation_maintenance"].episode.iloc[0]
    summaries.path_for(dec).write_text(json.dumps({"summaries": {
        water: {"text": "AI-WRITTEN water summary.", "source": "llm"},
        other: {"text": "Template filter summary.", "source": "template"}}}))
    at = V.app(monkeypatch, dec, V.ROOT / "data" / "quickstart", tmp_path / "a.json")
    at.switch_page("views/incidents.py")
    at.run()
    assert not at.exception, V.errors(at)
    txt = _text(at)
    assert "AI-WRITTEN water summary." in txt and "Template filter summary." in txt and L.AI_SUMMARY in txt
    for ep, label in ((water, True), (other, False)):
        at.query_params["episode"] = ep
        at.run()
        assert not at.exception, V.errors(at)
        txt = _text(at)
        assert ("AI-WRITTEN" in txt if label else "Template filter summary." in txt)
        assert (L.AI_SUMMARY in txt) is label, ep
