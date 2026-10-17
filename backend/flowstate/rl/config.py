"""Constants shared by the RL environment, the baseline controllers and the evaluation harness.

Signal timing limits are derived with the *same* formulas the FlowState signal designer uses
(:mod:`flowstate.sim.signals`: ITE yellow, kinematic all-red, MUTCD pedestrian clearance), so an RL
controller can never be given a timing the pre-timed designer would consider unsafe.
"""

from __future__ import annotations

import math

from ..sim.signals import WALK_MIN, all_red_time, flashing_dont_walk, yellow_time

# --- network -------------------------------------------------------------------------------------
BLOCK_LENGTH_M = 200.0  # street between two junctions
ATTACH_LENGTH_M = 200.0  # entry / exit stubs
LANES = 2
SPEED_MS = 13.89  # 50 km/h
LANE_WIDTH_M = 3.2
SLOT_M = 7.5  # road space one queued vehicle needs (length 4.5 m + gap)

# --- simulation ----------------------------------------------------------------------------------
STEP_S = 1.0  # SUMO step length
DECISION_S = 5.0  # the agent decides every 5 s of simulated time
TELEPORT_S = 240  # as in the main FlowState engine

# --- safe signal timing (never negotiable by the agent) -------------------------------------------
CROSSING_M = 2 * LANES * LANE_WIDTH_M  # curb to curb, the width a pedestrian crosses
YELLOW_S = yellow_time(SPEED_MS)
ALL_RED_S = all_red_time(CROSSING_M, SPEED_MS)
# a green must cover the pedestrian WALK interval plus the clearance left after amber and all-red
PED_CLEARANCE_S = flashing_dont_walk(CROSSING_M, YELLOW_S, ALL_RED_S)
MIN_GREEN_S = float(math.ceil(WALK_MIN + PED_CLEARANCE_S))
MAX_GREEN_S = 60.0  # forced change: no approach can be starved forever

# --- reward normalisation ---------------------------------------------------------------------------
WAIT_CAP_S = 120.0
SAT_FLOW_VPS = 2.0  # network-wide discharge used to normalise throughput (veh/s)

CONTROLLED = ("A0", "A1", "B0", "B1")
DIRECTIONS = ("N", "E", "S", "W")


def describe() -> dict[str, float]:
    return {"yellow_s": YELLOW_S, "all_red_s": ALL_RED_S, "ped_clearance_s": PED_CLEARANCE_S,
            "min_green_s": MIN_GREEN_S, "max_green_s": MAX_GREEN_S, "decision_s": DECISION_S}
