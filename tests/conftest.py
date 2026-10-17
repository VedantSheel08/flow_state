import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import tempfile  # noqa: E402

os.environ["FLOWSTATE_RUNS_DIR"] = tempfile.mkdtemp(prefix="flowstate-test-runs-")

from flowstate.api.service import NETWORKS_DIR  # noqa: E402
from flowstate.network.city import build_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def networks():
    if not (NETWORKS_DIR / "district.net.xml").exists() or not (NETWORKS_DIR / "district_bike.net.xml").exists():
        build_all(NETWORKS_DIR)
    return NETWORKS_DIR
