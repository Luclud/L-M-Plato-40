import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QS = ROOT / "data" / "quickstart"

# Viewer UI tests need streamlit/plotly (viewer/requirements.txt). The pipeline setup
# (requirements-dev.txt, used by scripts/release_check.*) does not install them, so skip
# those test files cleanly instead of failing at collection.
_VIEWER_DEPS = all(importlib.util.find_spec(m) is not None for m in ("streamlit", "plotly"))
collect_ignore_glob = [] if _VIEWER_DEPS else ["test_viewer_*.py"]


@pytest.fixture(scope="session")
def qs():
    from plato40 import io
    if not (QS / "manifest.json").exists():
        pytest.skip("quickstart data missing (data/quickstart)")
    return io.load(QS)


@pytest.fixture(scope="session")
def qs_state(qs):
    from plato40.state import State
    return State(qs)
