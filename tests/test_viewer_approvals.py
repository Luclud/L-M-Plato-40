"""T20 Approvals grouped per incident (mock - nothing is executed)."""
import re

import pytest

from viewer import core
from viewer import labels as L

import viewer_apptest as V

RAW = [re.compile(p) for p in (r"\bEP-\d+", r"\bD\d{6}\b", r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", r"\.(?:csv|jsonl?)\b",
                               r"\bSH\d+", r"\bWO\d+")]
RAW_CODES = set(L.ISSUE_TYPES) | set(L.ACTIONS) | {"human_approval", "approval_and_consent"}


@pytest.fixture(scope="module")
def sets(tmp_path_factory):
    return V.datasets(tmp_path_factory.mktemp("viewer_appr"))


def _text(at):
    return " ".join(str(x.value) for x in [*at.markdown, *at.caption, *at.subheader, *at.info, *at.warning, *at.error])


def _open(monkeypatch, dec, data, appr):
    at = V.app(monkeypatch, dec, data, appr)
    at.switch_page("views/approvals.py")
    at.run()
    assert not V.errors(at), V.errors(at)
    return at


def _approve_buttons(at):
    return [b for b in at.button if b.key and b.key.startswith("ap_ok_")]


@pytest.mark.parametrize("name", ["quickstart", "dev", "empty"])
def test_page_renders_plain_language(sets, name, monkeypatch, tmp_path):
    dec, data = sets[name]
    at = _open(monkeypatch, dec, data, tmp_path / "a.json")
    assert any(L.MOCK in w.value for w in at.warning)
    text = _text(at)
    for rx in RAW:
        assert not rx.search(text), (name, rx.pattern, rx.search(text).group(0))
    assert not (set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text)) & RAW_CODES)
    df = core.load_decisions(dec)
    need = df[df.requires_human_approval] if len(df) else df
    if need.empty:
        assert any("Nothing needs a human decision" in i.value for i in at.info)
        return
    # one card per incident, all approval decisions of the shown incidents listed in it, 20 per page
    eps = need.episode.unique()
    shown = min(20, len(eps))
    assert sum(1 for b in at.button if b.key and b.key.startswith("ap_ok_")) >= shown
    asks = [m for m in at.markdown if m.value.startswith(("**Approve “", "**Your decision is needed"))]
    assert len(asks) == len(_approve_buttons(at))
    show = at.button_group(key="ap_show")
    assert show.format_func("Pending") == f"Pending {len(eps)}"
    consent = need[need.requires_guest_consent].event_id
    shown_ids = {b.key.removeprefix("ap_ok_") for b in _approve_buttons(at)}
    assert sum(e.value == L.CONSENT_NEEDED for e in at.error) == len(shown_ids & set(consent))


def test_cards_sorted_most_urgent_first(sets, monkeypatch, tmp_path):
    dec, data = sets["dev"]
    at = _open(monkeypatch, dec, data, tmp_path / "a.json")
    rank = {L.label("priority", p): i for i, p in enumerate(["critical", "high", "medium", "low"])}
    badge = re.compile(r"^:\w+-badge\[(.+)\]$")
    seen = [rank[m.group(1)] for m in (badge.match(x.value) for x in at.markdown) if m and m.group(1) in rank]
    assert seen and seen == sorted(seen)


def test_approve_persists_and_counts_update(sets, monkeypatch, tmp_path):
    dec, data = sets["quickstart"]
    appr = tmp_path / "state" / "approvals.json"
    at = _open(monkeypatch, dec, data, appr)
    df = core.load_decisions(dec)
    need = df[df.requires_human_approval]
    n_eps = need.episode.nunique()
    before = len(_approve_buttons(at))
    first = _approve_buttons(at)[0]
    eid = first.key.removeprefix("ap_ok_")
    first.click().run()
    assert not V.errors(at)
    rec = core.load_approvals(appr)
    assert rec[eid]["status"] == "approved" and L.MOCK in rec[eid]["note"]
    assert len(_approve_buttons(at)) == before - 1
    # nothing outside the approvals file is written
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["approvals.json"]

    # reject one more, then reload in a fresh app: both answers persist
    no = [b for b in at.button if b.key and b.key.startswith("ap_no_")][0]
    eid2 = no.key.removeprefix("ap_no_")
    no.click().run()
    at2 = _open(monkeypatch, dec, data, appr)
    keys = {b.key for b in at2.button}
    assert f"ap_ok_{eid}" not in keys and f"ap_ok_{eid2}" not in keys
    assert core.load_approvals(appr)[eid2]["status"] == "rejected"
    # decide every decision of one incident -> it moves to Decided, pending incident count drops by one
    ep = need[need.event_id == eid].episode.iloc[0]
    for e in need[need.episode == ep].event_id:
        core.record_approval(appr, e, "approved", str(dec), L.MOCK)
    at3 = _open(monkeypatch, dec, data, appr)
    g = at3.button_group(key="ap_show")
    opts = [g.format_func(o) for o in ("Pending", "Decided")]
    pend = int(opts[0].split()[-1])
    assert pend < n_eps and pend + int(opts[1].split()[-1]) == n_eps
    at3.button_group(key="ap_show").set_value("Decided").run()
    assert not V.errors(at3)
    assert any("Approved (mock)" in c.value for c in at3.caption)
