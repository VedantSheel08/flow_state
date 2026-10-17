from flowstate.api.service import NETWORKS_DIR, WORK_DIR
from flowstate.scenarios.catalog import create
from flowstate.sim.engine import Engine, RunConfig


def make_engine(scenario: str, seed: int, params: dict | None = None, label: str = "test") -> Engine:
    sc = create(scenario, params or {})
    return Engine(sc, RunConfig(scenario, sc.params, seed, sc.params.get("signals", "fixed"), label=label), NETWORKS_DIR, WORK_DIR / f"test_{label}")


def run_until(eng: Engine, t: float, every=None) -> None:
    while eng.t < t and not eng.finished:
        eng.step()
        if every:
            every(eng)
