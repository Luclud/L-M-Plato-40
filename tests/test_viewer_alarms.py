"""T29 Alarms vs Steward: alarm statistics computed from the run + AppTest of the page."""
import pytest

from plato40 import io
from viewer import alarm_stats as A
from viewer import core

import viewer_apptest as V

PAGE = "views/alarms.py"


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_alarms"))


@pytest.fixture(scope="module")
def dev():
    return io.load(V.ROOT / "data" / "dev"), core.load_decisions(V.ROOT / "decisions.jsonl")


def test_dev_alarm_code_counts(dev):
    data, _ = dev
    assert A.events_by_code(data) == {"ROOM_TEMP_LOW": 318, "SENSOR_COMM_LOSS": 318, "ROOM_TEMP_HIGH": 250,
                                      "FILTER_DP_HIGH": 20, "AIRFLOW_LOW": 18, "WATER_NIGHT_FLOW": 2}
    ep = A.alarm_episodes(data)
    assert len(ep) == 464
    assert A.alarms_by_week(data).n.sum() == len(ep)


def test_dev_water_alarms_vs_steward_matches_evaluator(dev):
    data, df = dev
    labels, src = A.labels_for(V.ROOT / "data" / "dev")
    assert src == "weak"
    cov = A.coverage(data, df, labels).set_index("issue_type")
    table = core.parse_eval_table(core.run_evaluate(V.ROOT / "decisions.jsonl", V.ROOT / "data" / "dev", "dev"))
    water = table[table.scenario.str.startswith("S02")]
    assert cov.loc["water_leak", "labelled"] == len(water) == 5
    assert cov.loc["water_leak", "by_steward"] == int(water.detected.sum())
    assert cov.loc["water_leak", "by_alarms"] == 1      # one night-flow alarm (raised + cleared = 2 events)
    assert cov.by_steward.sum() == int(table.detected.sum())


def test_incidents_by_week_counts_incident_episodes_only(dev):
    _, df = dev
    inc = A.incident_episodes(df)
    assert set(inc.issue_type) <= set(A.INCIDENT_TYPES)
    assert A.incidents_by_week(df).n.sum() == len(inc) > 0


def test_noise_is_computed(dev):
    data, _ = dev
    nz = A.noise(data)
    assert nz["alarm_episodes"] == 464
    assert 0 < nz["comm_loss_in_outage"] <= nz["comm_loss"]
    assert 0 < nz["temp_low_vacant"] <= nz["temp_low_presence_known"] <= nz["temp_low"]
    out = A.gateway_outages(data)
    assert len(out) and (out.end > out.start).all()


def test_empty_decisions(sets):
    dec, data_dir = sets["empty"]
    data, df = io.load(data_dir), core.load_decisions(dec)
    labels, _ = A.labels_for(data_dir)
    cov = A.coverage(data, df, labels)
    assert (cov.by_steward == 0).all() and cov.labelled.sum() == len(labels)
    assert A.incidents_by_week(df).empty


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_alarms_page_apptest(sets, monkeypatch, tmp_path, name):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page(PAGE)
    at.run()
    assert not V.errors(at)
    assert any(m.label == "Building alarms" for m in at.metric)
    text = " ".join(m.value for m in at.markdown)
    assert "Why the alarms are noisy" in text
    assert "SENSOR_COMM_LOSS" not in text and "ROOM_TEMP" not in text


def test_proof_links_to_alarms(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    at = V.app(monkeypatch, dec, data, tmp_path / "appr.json")
    at.run()
    at.switch_page("views/proof.py")
    at.run()
    assert not V.errors(at)
