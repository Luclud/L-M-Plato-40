"""T22 Guided demo: 8 steps, Back / Next, heroes from labels (quickstart) or most severe episode (dev / holdout)."""
import re

import pandas as pd
import pytest

from viewer import core
from viewer import labels as L
from viewer.views import incident

import viewer_apptest as V


def _helpers():
    """tour.py's functions and constants without running the page body (it is a Streamlit page script)."""
    import ast
    import types
    path = V.ROOT / "viewer" / "views" / "tour.py"
    tree = ast.parse(path.read_text())
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Try))
            or (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in n.targets))]
    mod = types.ModuleType("tour_helpers")
    mod.__file__ = str(path)
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), mod.__dict__)
    return mod


pytest.importorskip("streamlit")
tour = _helpers()

EXPECTED_QS = {"water": "QS-S02-R104-burst", "heating": "QS-S03-HP1-coldsnap", "turnaround": "QS-S01-R308-0108",
               "surge": "QS-S05-event-weekend", "filter": "QS-S04-AHU1-filter"}


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_tour"))


def test_talking_points_file():
    s = tour.steps()
    assert [x["key"] for x in s] == tour.ORDER and len(s) == 8
    for x in s:
        assert x["title"] and x["say"]
        assert len(re.findall(r"[.!?](?:\s|$)", x["say"])) <= 2, x["say"]
        assert not re.search(r"\b[a-z]+_[a-z_]+\b|\bEP-\d+|\bR\d{3}\b|\bD\d{6}\b", x["say"]), x["say"]


def test_missing_talking_points_fall_back(tmp_path):
    s = tour.steps(tmp_path / "none.toml")
    assert len(s) == 8 and s[0]["title"] == "Building"


def test_quickstart_heroes_match_labels(sets):
    dec, data = sets["quickstart"]
    df = core.load_decisions(dec)
    labels = core.load_labels("quickstart")
    h = tour.heroes(df, labels)
    for key, lab_id in EXPECTED_QS.items():
        assert h[key] is not None, key
        assert incident.matching_label(df[df.episode == h[key]], labels)["id"] == lab_id


def test_heroes_without_labels_pick_most_severe(sets):
    df = core.load_decisions(sets["dev"][0])
    h = tour.heroes(df, [])
    ep = core.episodes(df)
    for key, (_s, it) in tour.SCENARIOS.items():
        e = ep[ep.issue_type == it]
        best = e.priority.map(core.PRIO_RANK).min()
        assert core.PRIO_RANK[ep.set_index("episode").priority[h[key]]] == best
    assert tour.heroes(pd.DataFrame(columns=df.columns), []) == {k: None for k in tour.SCENARIOS}


def _walk(at):
    seen = []
    for i in range(8):
        assert not V.errors(at), (i, V.errors(at))
        info = next(x.value for x in at.info if "/ 8 ·" in x.value)
        seen.append(info)
        if i < 7:
            at.button(key="tour_next_top").click().run()
    assert at.button(key="tour_next_top").disabled
    return seen


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_walk_all_steps(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    at = V.app(monkeypatch, dec, data, tmp_path / "a.json")
    at.switch_page("views/tour.py")
    at.run()
    seen = _walk(at)
    assert [s.split("**")[1] for s in seen] == [f"{i} / 8 · {x['title']}" for i, x in enumerate(tour.steps(), 1)]
    at.button(key="tour_back_bottom").click().run()
    assert any("7 / 8" in x.value for x in at.info)
    if name == "empty":
        at.query_params["tour"] = "2"
        at.run()
        assert any("No matching incident" in w.value for w in at.warning)


def test_quickstart_steps_show_the_right_incident(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    appr = tmp_path / "a.json"
    at = V.app(monkeypatch, dec, data, appr)
    at.switch_page("views/tour.py")
    at.run()
    assert at.query_params.get("t") in ([], None) or at.query_params["t"]
    t = at.query_params["t"]
    t = t[0] if isinstance(t, list) else t
    df = core.load_decisions(dec)
    h = tour.heroes(df, core.load_labels("quickstart"))
    assert pd.Timestamp(t) == df[df.episode == h["water"]].t.min().floor("15min")
    titles = {"water": "Room 104", "heating": L.label("asset", "HP1"), "turnaround": "Room 308",
              "filter": L.label("asset", "AHU1")}
    for n, key in enumerate(tour.ORDER, 1):
        at.query_params["tour"] = str(n)
        at.run()
        assert not V.errors(at), (key, V.errors(at))
        if key in titles:
            assert any(m.value.startswith("## ") and titles[key] in m.value for m in at.markdown), key
        if key == "surge":
            ok = [b for b in at.button if b.key and b.key.startswith(f"tour{n}_approved_")]
            assert ok, "staffing request can be approved in the tour"
            ok[0].click().run()
            assert core.load_approvals(appr)[ok[0].key.split("_approved_")[1]]["status"] == "approved"
        if key == "learned":
            assert sum(m.value.startswith("#### For") for m in at.markdown) == 2
        if key == "proof":
            at.button(key="proof_run").click().run()
            assert any("PASS" in s.value for s in at.success)


def test_deep_link(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    at = V.app(monkeypatch, dec, data, tmp_path / "a.json")
    at.query_params["tour"] = "3"
    at.switch_page("views/tour.py")
    at.run()
    assert any("3 / 8" in x.value for x in at.info)
