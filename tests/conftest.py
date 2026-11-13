import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QS = ROOT / "data" / "quickstart"


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
