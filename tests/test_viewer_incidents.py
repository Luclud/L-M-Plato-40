"""T18 Incidents list + incident story page."""
import re

import pandas as pd
import pytest

from viewer import core
from viewer import labels as L
from viewer.views import incident

import viewer_apptest as V

RAW = [re.compile(p) for p in (r"\bEP-\d+", r"\b[A-Z]{2}-\d{5}\b", r"\bD\d{6}\b", r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",
                               r"\.(?:csv|jsonl)\b")]
RAW_CODES = set(L.ISSUE_TYPES) | set(L.ACTIONS) | {"human_approval", "approval_and_consent"}


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_inc"))


def _visible_text(at):
    return " ".join(str(x.value) for x in [*at.markdown, *at.caption, *at.subheader, *at.info, *at.warning])


def _assert_no_raw(text, where):
    for rx in RAW:
        assert not rx.search(text), (where, rx.pattern, rx.search(text).group(0))
    words = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text))
    assert not (words & RAW_CODES), (where, words & RAW_CODES)


def test_humanize_removes_codes_files_and_utc():
    s = incident.humanize("room_water.csv:R104:2026-01-12T17:00:00Z..2026-01-12T17:45:00Z:~26 L/15min on WM_R104, "
                          "flag_anomaly by CLEANER_2 on 2026-01-23")
    _assert_no_raw(s, "humanize")
    assert "Room 104" in s and "Cleaner 2" in s and "Mon 12 Jan, 18:00" in s and "Fri 23 Jan" in s


def test_matching_label_and_confidence():
    rows = pd.DataFrame({"issue_type": ["water_leak"], "affected_assets": [["R104"]],
                         "t": [pd.Timestamp("2026-01-12T18:00Z")]})
    lab = {"issue_type": "water_leak", "assets": ["R104"], "start_utc": "2026-01-12T17:00:00Z",
           "end_utc": "2026-01-13T22:45:00Z"}
    assert incident.matching_label(rows, [lab]) is lab
    assert incident.confidence_word(0.8) == "confidence high (0.8)" and incident.confidence_word(None) == ""


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_list_and_one_story_per_incident_type(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    df = core.load_decisions(dec)
    at = V.app(monkeypatch, dec, data, tmp_path / "a.json")
    at.switch_page("views/incidents.py")
    at.run()
    assert not at.exception, V.errors(at)
    _assert_no_raw(_visible_text(at), "list")
    if df.empty:
        return
    exp = [e for e in at.expander if e.label.startswith("Routine work")][0].label
    n = df.issue_type.value_counts()
    assert f"{n.get('guest_request', 0):,} guest replies" in exp and f"{n.get('data_quality', 0):,} data issues" in exp
    for it in L.INCIDENT_TYPES:
        eps = df[df.issue_type == it].episode.unique()
        if not len(eps):
            continue
        at.query_params["episode"] = eps[0]
        at.run()
        assert not at.exception, (it, V.errors(at))
        txt = _visible_text(at)
        for step in ("1 · What we saw", "2 · Why we think so", "3 · What we did", "4 · How we'll know it worked"):
            assert step in txt, (it, step)
        _assert_no_raw(txt, it)


def test_quickstart_labels_reachable_in_two_clicks(sets, monkeypatch, tmp_path):
    """Every quickstart hand label has an incident card on the list (filter chip = 1 click, Open = 2nd click)."""
    dec, data = sets["quickstart"]
    df = core.load_decisions(dec)
    at = V.app(monkeypatch, dec, data, tmp_path / "a.json")
    at.switch_page("views/incidents.py")
    at.run()
    for lab in core.load_labels("quickstart"):
        eps = core.match_episodes(df, lab["issue_type"], lab["assets"], lab["start_utc"], lab["end_utc"])
        assert eps, lab["id"]
        at.pills(key="inc_type").set_value(lab["issue_type"])
        at.run()
        keys = {b.key for b in at.button}
        pages = at.get("button_group")
        shown = [e for e in eps if f"open_{e}" in keys]
        if not shown and pages:  # paginated type: open the page containing it
            for p in range(2, 10):
                at.segmented_control(key="inc_page").set_value(p).run()
                keys = {b.key for b in at.button}
                shown = [e for e in eps if f"open_{e}" in keys]
                if shown:
                    break
        assert shown, lab["id"]
        at.button(key=f"open_{shown[0]}").click().run()
        assert at.query_params.get("episode") == [shown[0]] or at.query_params.get("episode") == shown[0]
        assert "3 · What we did" in _visible_text(at)
        at.query_params.pop("episode", None)
        at.run()
