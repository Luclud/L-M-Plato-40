"""Dataset picker discovery: a holdout extracted one folder deeper (data/holdout/<folder>/) is still found."""
import json

import pytest

pytest.importorskip("streamlit")
from viewer.views import common  # noqa: E402


def _dataset(d, profile):
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"profile": profile}))


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "ROOT", tmp_path)
    common.datasets.clear()
    yield tmp_path
    common.datasets.clear()


def test_flat_and_nested_holdout(root):
    _dataset(root / "data" / "dev", "dev")
    (root / "decisions.jsonl").write_text("")
    _dataset(root / "data" / "holdout" / "PLATO40_v2_holdout", "holdout")
    (root / "decisions_holdout.jsonl").write_text("")
    ds = common.datasets()
    assert list(ds) == ["Dev year", "Holdout"]
    assert ds["Holdout"] == (str(root / "decisions_holdout.jsonl"),
                             str(root / "data" / "holdout" / "PLATO40_v2_holdout"))


def test_two_nested_datasets_are_ambiguous(root):
    _dataset(root / "data" / "holdout" / "a", "holdout")
    _dataset(root / "data" / "holdout" / "b", "holdout")
    (root / "decisions_holdout.jsonl").write_text("")
    assert common.datasets() == {}


def test_out_file_named_after_outer_folder(root):
    _dataset(root / "data" / "holdout" / "inner", "v2")
    (root / "out").mkdir()
    (root / "out" / "holdout.jsonl").write_text("")
    assert common.datasets() == {"V2": (str(root / "out" / "holdout.jsonl"), str(root / "data" / "holdout" / "inner"))}
