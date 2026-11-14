"""AI wording of the Level 3 cards: number guard, stored once, regenerated only when the insight changes."""
import json

import pytest

from viewer import insights_ai as A

ROW = {"insight_id": "PLATO-P1", "topic": "Floor above the car park runs cold",
       "finding": "PLATO-40: floor-1 rooms are below 20 °C in 17.3 % of winter intervals vs 9.8 %–11.3 % "
                  "(n=177,083).",
       "recommendation": "Insulate the slab; CLARA: +0.5 °C setpoint on floor 1.",
       "numbers": {"stratified_excess_pp": 6.79}}
GOOD = {"headline": "The floor above the car park runs cold",
        "finding": "In PLATO-40, floor-1 rooms were below 20 °C in 17.3 % of winter intervals (n=177083).",
        "recommendation": "Insulate the slab and raise the floor-1 setpoint by 0.5 °C."}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STORE", tmp_path / "out" / "insights_ai.json")
    monkeypatch.setattr(A, "SHIPPED", tmp_path / "shipped.json")
    return A.STORE


def test_check_accepts_copied_numbers_and_rejects_new_ones():
    assert A.check(GOOD, ROW)
    assert not A.check({**GOOD, "finding": "Below 20 °C in 18 % of intervals."}, ROW)        # rounded
    assert not A.check({**GOOD, "recommendation": "Saves 1200 EUR a year."}, ROW)             # invented
    assert not A.check({**GOOD, "headline": ""}, ROW)


def test_generate_stores_once_and_rejects_bad_text(store):
    calls = []

    def fake(system, prompt, max_tokens):
        calls.append(prompt)
        return json.dumps(GOOD)

    assert A.generate([ROW], complete=fake, model="m") == (1, 0)
    assert A.generate([ROW], complete=fake, model="m") == (0, 0)          # stored: no second call
    assert len(calls) == 1
    e = A.entry(ROW, A.load())
    assert e and e["headline"] == GOOD["headline"]
    assert A.key_for(ROW) in json.loads(store.read_text())

    changed = {**ROW, "finding": ROW["finding"].replace("17.3", "18.1")}
    assert A.entry(changed, A.load()) is None                              # insight changed -> regenerate

    bad = {**ROW, "insight_id": "PLATO-P2"}
    assert A.generate([bad], complete=lambda *a: json.dumps({**GOOD, "finding": "Up 99 %."}), model="m") == (0, 1)
    assert A.entry(bad, A.load()) is None


def test_no_key_means_no_calls(monkeypatch):
    monkeypatch.setattr(A.llm, "config", lambda: None)
    assert A.generate([ROW]) == (0, 0)
    assert not A.available()


def test_one_retry_names_the_extra_numbers():
    prompts = []

    def fake(system, prompt, max_tokens):
        prompts.append(prompt)
        return json.dumps({**GOOD, "finding": "Below 20 °C in 156 rooms."} if len(prompts) == 1 else GOOD)

    assert A.generate([ROW], complete=fake, model="m") == (1, 0)
    assert len(prompts) == 2 and "156" in prompts[1]


def test_plato40_is_not_a_free_40_and_headlines_have_no_numbers():
    assert not A.check({**GOOD, "finding": GOOD["finding"] + " A gateway outage blinds 40 rooms."}, ROW)
    assert not A.check({**GOOD, "headline": "Floor 1 runs 6.8 points colder"}, ROW)
    assert A.check({**GOOD, "finding": "PLATO-40: " + GOOD["finding"]}, ROW)


def test_two_datasets_keep_their_own_cards():
    other = {**ROW, "finding": ROW["finding"].replace("17.3", "9.8"), "status": "rejected"}
    assert A.key_for(other) != A.key_for(ROW)
    assert A.generate([ROW], complete=lambda *a: json.dumps(GOOD), model="m") == (1, 0)
    good2 = {**GOOD, "finding": "PLATO-40: this dataset does not support it (9.8 %)."}
    assert A.generate([other], complete=lambda *a: json.dumps(good2), model="m") == (1, 0)
    s = A.load()
    assert A.entry(ROW, s)["finding"] == GOOD["finding"] and A.entry(other, s)["finding"] == good2["finding"]


def test_rejected_insights_keep_the_analysis_text():
    rej = {**ROW, "status": "rejected"}
    assert A.missing([rej], {}) == []
    assert A.generate([rej], complete=lambda *a: json.dumps(GOOD), model="m") == (0, 0)
    assert A.entry(rej, A.load()) is None
