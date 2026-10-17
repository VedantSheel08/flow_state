"""Cross-check the recorder's emission totals against SUMO's own ``--emission-output`` XML for the same run.

    python -m flowstate.experiments.verify_emissions --scenario rush_hour --seed 7 --out data/verification/emissions.json

Runs one experiment on the TraCI backend with SUMO's per-vehicle emission output switched on, then integrates that file
(post-warm-up, step by step) and compares CO2 and fuel with what the recorder stored. Needs a few minutes of SUMO time.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="rush_hour")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--controller", default="fixed")
    ap.add_argument("--warmup", type=float, default=60.0)
    ap.add_argument("--duration", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    os.environ["FLOWSTATE_BACKEND"] = "traci"  # libsumo cannot take extra command-line options through this harness
    import traci

    xml_path = str(Path(tempfile.mkdtemp(prefix="fs-emis-")) / "emission.xml")
    orig = traci.start
    traci.start = lambda cmd, *p, **k: orig(list(cmd) + ["--emission-output", xml_path, "--emission-output.precision", "6"], *p, **k)
    from .recorder import ExperimentSpec, run_experiment

    spec = ExperimentSpec(scenario=a.scenario, seed=a.seed, controller=a.controller, params={"duration": a.duration}, warmup=a.warmup)
    res = run_experiment(spec, None)
    dt, tot = spec.step_length, {"co2": 0.0, "fuel": 0.0}
    for _, el in ET.iterparse(xml_path, events=("end",)):
        if el.tag == "timestep":
            if float(el.get("time")) >= spec.warmup:
                for v in el.iter("vehicle"):
                    tot["co2"] += float(v.get("CO2")) * dt
                    tot["fuel"] += float(v.get("fuel")) * dt
            el.clear()
    sumo_co2, sumo_fuel = tot["co2"] / 1000, tot["fuel"] / 1000  # mg -> g
    s = res.summary
    out = {"scenario": a.scenario, "seed": a.seed, "controller": a.controller, "warmup_s": a.warmup, "engine": res.engine_info,
           "recorder": {"co2_total_g": s["co2_total_g"], "fuel_total_g": s["fuel_total_g"]},
           "sumo_emission_output": {"co2_total_g": sumo_co2, "fuel_total_g": sumo_fuel},
           "ratio_recorder_to_sumo": {"co2": s["co2_total_g"] / sumo_co2, "fuel": s["fuel_total_g"] / sumo_fuel},
           "co2_g_per_fuel_g": s["co2_total_g"] / s["fuel_total_g"],
           "reading": "Ratios near 1 mean the stored numbers are SUMO's own output, integrated per step; ~3.1 g CO2 per g fuel is the carbon content of petrol/diesel."}
    print(json.dumps(out, indent=1))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
