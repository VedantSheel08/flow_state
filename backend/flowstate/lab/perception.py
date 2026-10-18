"""What the AI components actually see, computed from the live simulation (read-only: nothing here can change a signal).

* forecaster : the 16 x 19 TrafficState feature matrix it is given, and what it predicts from it.
* PPO agent  : the 33-number observation each junction's policy receives and the action probabilities it would output. The checkpoint failed
               its validation gate, so it is shown as a *shadow*: it never controls anything, and its choice is compared with the active controller's.
* camera     : a **synthetic** overhead camera. The SUMO state is rendered to a noisy image and classical OpenCV detection (background subtraction,
               contours, frame-to-frame overlap to tell stopped from moving) finds vehicles and estimates queues. Detections are scored against SUMO's
               own vehicle list, so the error shown is real, but it is an upper bound on what a real camera would achieve (no weather, glare or occlusion).

Every optional dependency (torch/stable-baselines3, OpenCV) degrades to ``{"available": false, "reason": ...}``.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..rl import config as C
from ..rl.controllers import artifacts_dir

OBS_NAMES = ([f"{d}:{n}" for d in C.DIRECTIONS for n in ("queue/cap", "wait/60s", "exit occupancy", "ambulance")]
             + ["stage 0 green", "stage 1 green", "in transition", "green elapsed/max", "transition left", "can switch"]
             + ["demand: current stage", "demand: next stage", "demand: next - current", "exit full: current", "exit full: next", "ambulance: current", "ambulance: next"]
             + ["neighbour 1 queue", "neighbour 2 queue", "network queue", "network wait"])
OBS_GROUPS = [("Approaches (N, E, S, W)", 0, 16), ("Signal", 16, 22), ("Stage demand", 22, 29), ("Context", 29, 33)]
PPO_CHECKPOINT = "run4/best_validation.zip"  # the per-junction (v2 observation) candidate that was evaluated and rejected by the gate

# Same palette as the lab's map (BGR order for OpenCV): light land, curb bed, kerb line, grey asphalt, yellow centre line.
LAND, CURB_BED, KERB, ASPHALT, YELLOW = (230, 234, 232), (225, 222, 220), (195, 188, 183), (107, 99, 94), (59, 185, 226)
CAR_BGR = [(235, 232, 229), (191, 182, 170), (179, 160, 141), (163, 185, 199), (144, 149, 169), (154, 168, 147)]  # the lab's fixed car colours
SCALE = 4.5  # camera pixels per metre: 3 px/m merged queued cars (79% precision), 4.5 px/m separates them (99% / 99%), 6 px/m only costs more time
VIEW_M = 160.0  # camera footprint, metres per side
STOP_OVERLAP = float(os.environ.get("FLOWSTATE_STOP_OVERLAP", 0.88))  # share of a car's box that must sit on last second's foreground to call it stopped
REGION_M = 80.0  # queue estimation window upstream of each stop line (what the camera covers)


class Perception:
    def __init__(self, ppm: float | None = None) -> None:
        self.scale = float(ppm or os.environ.get("FLOWSTATE_CAMERA_PPM", SCALE))  # camera pixels per metre
        self.size = int(VIEW_M * self.scale)
        self._ppo: Any = None
        self._ppo_err: str | None = None
        self._cv: Any = None
        self._cv_err: str | None = None
        self._geom: dict[str, Any] | None = None
        self._bg: dict[str, np.ndarray] = {}
        self._ref: dict[str, np.ndarray] = {}  # the empty scene after the same blur the camera frames get (thin lane markings would otherwise read as vehicles)
        self.timing: dict[str, list[float]] = {"ppo": [], "camera": []}

    # ------------------------------------------------------------------------------------------------ PPO (shadow)
    def _load_ppo(self) -> Any:
        if self._ppo is None and self._ppo_err is None:
            try:
                from stable_baselines3 import PPO

                self._ppo = PPO.load(str(artifacts_dir() / PPO_CHECKPOINT), device="cpu")
            except Exception as exc:  # noqa: BLE001 - torch / SB3 not installed, or the checkpoint is missing
                self._ppo_err = f"{type(exc).__name__}: {exc}"[:200]
        return self._ppo

    def ppo_status(self) -> dict[str, Any]:
        f = artifacts_dir() / "validation_records" / "run4_best_validation.json"
        v = json.loads(f.read_text()) if f.exists() else {}
        return {"checkpoint": PPO_CHECKPOINT, "validationPassed": bool(v.get("passed")), "validationReason": v.get("reason"),
                "role": "shadow: shown for inspection, never controls a signal", "observationNames": OBS_NAMES, "groups": [{"name": n, "from": a, "to": b} for n, a, b in OBS_GROUPS]}

    def ppo_view(self, sim: Any, active_actions: list[int]) -> dict[str, Any]:
        out: dict[str, Any] = {**self.ppo_status(), "available": False}
        try:
            from ..rl.env import build_agent_observations

            obs = build_agent_observations(sim)
        except Exception as exc:  # noqa: BLE001
            return out | {"reason": f"observation unavailable: {type(exc).__name__}: {exc}"[:160]}
        out["observation"] = np.round(obs.astype(np.float64), 3).tolist()  # 4 junctions x 33
        model = self._load_ppo()
        if model is None:
            return out | {"reason": f"PPO could not be loaded ({self._ppo_err})"}
        t0 = time.perf_counter()
        try:
            import torch

            with torch.no_grad():
                t, _ = model.policy.obs_to_tensor(obs)
                probs = model.policy.get_distribution(t).distribution.probs.cpu().numpy()
                value = model.policy.predict_values(t).cpu().numpy().reshape(-1)
        except Exception as exc:  # noqa: BLE001
            return out | {"reason": f"PPO inference failed: {type(exc).__name__}: {exc}"[:160]}
        self.timing["ppo"].append((time.perf_counter() - t0) * 1000)
        ppo_act = probs.argmax(axis=1).astype(int).tolist()
        out |= {"available": True, "junctions": list(C.CONTROLLED), "actionNames": ["hold the green", "switch to the next stage"],
                "probabilities": np.round(probs.astype(np.float64), 3).tolist(), "value": np.round(value.astype(np.float64), 2).tolist(), "ppoAction": ppo_act, "activeAction": [int(a) for a in active_actions],
                "agrees": [int(a == b) for a, b in zip(ppo_act, active_actions)], "latencyMs": round(self.timing["ppo"][-1], 2)}
        return out

    # ------------------------------------------------------------------------------------------------ synthetic camera + CV
    def _cv2(self) -> Any:
        if self._cv is None and self._cv_err is None:
            try:
                import cv2

                self._cv = cv2
            except Exception as exc:  # noqa: BLE001
                self._cv_err = f"{type(exc).__name__}: {exc}"[:160]
        return self._cv

    def camera_status(self) -> dict[str, Any]:
        cv = self._cv2()
        return {"available": cv is not None, "reason": self._cv_err, "synthetic": True, "footprintM": VIEW_M, "pixelsPerMetre": self.scale,
                "note": "rendered from the simulation, not real footage: an upper bound on real camera accuracy"}

    def _geometry(self, sim: Any) -> dict[str, Any]:
        if self._geom is None:
            from .sim import geometry

            self._geom = geometry(sim)
        return self._geom

    def _px(self, x: float, y: float, c: tuple[float, float]) -> tuple[int, int]:
        return int(round(self.size / 2 + (x - c[0]) * self.scale)), int(round(self.size / 2 - (y - c[1]) * self.scale))

    def _background(self, sim: Any, jid: str) -> tuple[np.ndarray, tuple[float, float]]:
        """The empty scene in the same palette as the lab's map (light land, curb bed, kerb line, grey asphalt, dashed lane lines, yellow centre line, stop bars)."""
        cv = self._cv2()
        g = self._geometry(sim)
        c = tuple(next(j["c"] for j in g["junctions"] if j["id"] == jid))
        if jid not in self._bg:
            img = np.full((self.size, self.size, 3), LAND, np.uint8)
            px = lambda pts: np.array([self._px(x, y, c) for x, y in pts], np.int32)  # noqa: E731
            for color, extra in ((CURB_BED, 5.2), (KERB, 0.7), (ASPHALT, 0.05)):  # curb bed, then kerb line, then asphalt on top
                for ln in g["lanes"]:
                    cv.polylines(img, [px(ln["s"])], False, color, max(1, int((ln["w"] + extra) * self.scale)))
                for j in g["junctions"]:
                    cv.fillPoly(img, [px(j["s"])], color)
            edges: dict[str, list[dict[str, Any]]] = {}
            for ln in g["lanes"]:
                if not ln["i"]:
                    edges.setdefault(ln["id"].rsplit("_", 1)[0], []).append(ln)
            for lanes in edges.values():
                lanes = sorted(lanes, key=lambda l: l["id"])
                for a_, b_ in zip(lanes, lanes[1:]):  # dashed divider halfway between neighbouring lanes
                    if len(a_["s"]) == len(b_["s"]):
                        self._dashed(img, px([((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in zip(a_["s"], b_["s"])]), (246, 250, 250), 2.5 * self.scale, 3.5 * self.scale)
                inner = lanes[-1]  # the left edge of the last lane is the centre line
                (x0, y0), (x1, y1) = inner["s"][0], inner["s"][-1]
                L = float(np.hypot(x1 - x0, y1 - y0)) or 1.0
                dx, dy = (x1 - x0) / L, (y1 - y0) / L
                cv.polylines(img, [px([(x - dy * inner["w"] / 2, y + dx * inner["w"] / 2) for x, y in inner["s"]])], False, YELLOW, 1)
            for ap in g["approaches"]:  # white stop bar across each incoming lane
                for ln in g["lanes"]:
                    if ln["id"].startswith(ap["edge"] + "_"):
                        (px0, py0), (px1, py1) = ln["s"][-2], ln["s"][-1]
                        L = float(np.hypot(px1 - px0, py1 - py0)) or 1.0
                        ux, uy = (px1 - px0) / L, (py1 - py0) / L
                        hw = ln["w"] / 2
                        cv.line(img, self._px(px1 + uy * hw - ux, py1 - ux * hw - uy, c), self._px(px1 - uy * hw - ux, py1 + ux * hw - uy, c), (239, 244, 244), 2)
            rng = np.random.default_rng(7)
            self._bg[jid] = np.clip(img.astype(np.int16) + rng.normal(0, 1.5, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
        return self._bg[jid], c

    def _dashed(self, img: np.ndarray, pts: np.ndarray, color: tuple[int, int, int], dash: float, gap: float) -> None:
        cv = self._cv2()
        on, left = True, dash
        for p, q in zip(pts[:-1], pts[1:]):
            seg = float(np.hypot(*(q - p)))
            pos = 0.0
            while pos < seg:
                step = min(left, seg - pos)
                if on:
                    cv.line(img, tuple(int(v) for v in p + (q - p) * pos / seg), tuple(int(v) for v in p + (q - p) * (pos + step) / seg), color, 1)
                pos += step
                left -= step
                if left <= 1e-6:
                    on = not on
                    left = dash if on else gap

    def _render(self, vehicles: list[list[float]], bg: np.ndarray, c: tuple[float, float], t: float) -> np.ndarray:
        cv = self._cv2()
        img = bg.copy()
        for v in vehicles:
            vid, x, y, ang, typ = int(v[0]), v[1] / 10.0, v[2] / 10.0, v[3], int(v[4])
            if abs(x - c[0]) > VIEW_M / 2 + 8 or abs(y - c[1]) > VIEW_M / 2 + 8:
                continue
            length = {0: 4.5, 1: 8.0, 2: 6.0}.get(typ, 4.5)
            col = (250, 250, 250) if typ == 2 else ((163, 153, 141) if typ == 1 else CAR_BGR[vid % len(CAR_BGR)])
            rad = np.radians(ang)  # SUMO angle: degrees clockwise from north; (x, y) is the front bumper
            fx, fy = np.sin(rad), np.cos(rad)
            cx_, cy_ = x - fx * length / 2, y - fy * length / 2
            rect = ((self.size / 2 + (cx_ - c[0]) * self.scale, self.size / 2 - (cy_ - c[1]) * self.scale), (1.8 * self.scale, length * self.scale), ang)
            box = cv.boxPoints(rect).astype(np.int32)
            cv.fillPoly(img, [box + np.array([2, 2])], (60, 62, 66))  # shadow
            cv.fillPoly(img, [box], col)
        img = cv.GaussianBlur(img, (3, 3), 0)
        rng = np.random.default_rng(int(t) & 0xFFFF)
        light = 1.0 + 0.03 * np.sin(t / 40.0)  # slow illumination drift
        noisy = img.astype(np.float32) * light + rng.normal(0, 3.0, img.shape)
        return np.clip(noisy, 0, 255).astype(np.uint8)

    def _detect(self, frame: np.ndarray, bg: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Foreground blobs of |frame - empty road|. No morphological close: it would bridge the small gap between two queued cars."""
        cv = self._cv2()
        diff = cv.absdiff(cv.cvtColor(frame, cv.COLOR_BGR2GRAY), cv.cvtColor(bg, cv.COLOR_BGR2GRAY))
        diff = cv.GaussianBlur(diff, (3, 3), 0)
        _, mask = cv.threshold(diff, 22, 255, cv.THRESH_BINARY)
        mask = cv.morphologyEx(mask, cv.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, labels, stats, cents = cv.connectedComponentsWithStats(mask, connectivity=8)
        blobs = []
        floor = 0.35 * (getattr(self, "_car_px", None) or 4.5 * 1.8 * self.scale * self.scale)
        for k in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[k])
            if area < floor:
                continue
            ys, xs = np.nonzero(labels[y:y + h, x:x + w] == k)
            rect = cv.minAreaRect(np.stack([xs + x, ys + y], axis=1).astype(np.float32))
            blobs.append({"box": [x, y, w, h], "area": area, "centroid": [round(float(cents[k][0]), 1), round(float(cents[k][1]), 1)], "label": k, "rect": rect})
        return mask, blobs

    def _cars_in(self, b: dict[str, Any]) -> list[np.ndarray]:
        """One oriented box (4 pixel corners) per car in a blob. A lone car is the blob's own minimum-area rectangle. Touching cars have merged into one blob,
        so it is cut into the number of cars its area implies (area / one calibrated car), split across the lanes first (its short side / one car width)
        and then along them, giving equal cells aligned with the blob."""
        cv = self._cv2()
        (cx, cy), (w, h), ang = b["rect"]
        n = max(1, int(round(b["area"] / self._car_area())))
        if n == 1 or n > 60:
            return [cv.boxPoints(b["rect"])]
        th = np.radians(ang)
        u, v = np.array([np.cos(th), np.sin(th)]), np.array([-np.sin(th), np.cos(th)])  # the rectangle's width and height directions
        along, across, la, lb = (u, v, w, h) if w >= h else (v, u, h, w)
        ny = max(1, min(4, int(round(lb / (1.9 * self.scale)))))
        nx = max(1, int(round(n / ny)))
        cars = []
        for i in range(nx):
            for j in range(ny):
                c = np.array([cx, cy]) + along * ((i + 0.5) / nx - 0.5) * la + across * ((j + 0.5) / ny - 0.5) * lb
                hl, hw = la / nx / 2, lb / ny / 2
                cars.append(np.array([c - along * hl - across * hw, c + along * hl - across * hw, c + along * hl + across * hw, c - along * hl + across * hw]))
        return cars

    @property
    def _car_fallback(self) -> float:
        return 4.5 * 1.8 * self.scale * self.scale

    def _car_area(self) -> float:
        """Blob area (px) of one isolated passenger car as THIS pipeline sees it (shadow, blur and threshold included), measured on a plain road
        patch with every palette colour."""
        if getattr(self, "_car_px", None) is None:
            plain = np.full((self.size, self.size, 3), ASPHALT, np.uint8)
            areas = []
            for i in range(7):
                veh = [[i, 0, 0, 0.0, 0, 0, 0]]  # centre of the view, heading north, vtype car; id selects the colour
                _, blobs = self._detect(self._render(veh, plain, (0.0, 0.0), 10.0 + i), plain)
                if len(blobs) == 1:
                    areas.append(blobs[0]["area"])
            self._car_px = float(np.median(areas)) if areas else self._car_fallback
        return self._car_px

    def camera_view(self, sim: Any, jid: str, poses_now: list[list[float]], poses_prev: list[list[float]] | None, t: float) -> dict[str, Any]:
        cv = self._cv2()
        if cv is None:
            return {"available": False, "reason": self._cv_err} | self.camera_status()
        t0 = time.perf_counter()
        bg, c = self._background(sim, jid)
        ref = self._ref.get(jid)
        if ref is None:
            ref = self._ref[jid] = cv.GaussianBlur(bg, (3, 3), 0)
        frame = self._render(poses_now, bg, c, t)
        mask_now, blobs = self._detect(frame, ref)
        prev_mask = self._detect(self._render(poses_prev, bg, c, t - 1.0), ref)[0] if poses_prev is not None else None
        S, Z = self.scale, self.size
        to_world = lambda px, py: (c[0] + (px - Z / 2) / S, c[1] - (py - Z / 2) / S)  # noqa: E731
        cars: list[dict[str, Any]] = []
        for b in blobs:
            polys = self._cars_in(b)
            for poly in polys:
                x0, y0 = np.floor(poly.min(axis=0)).astype(int).clip(0, Z - 1)
                x1, y1 = np.ceil(poly.max(axis=0)).astype(int).clip(0, Z - 1)
                stopped, overlap = None, 0.0
                if prev_mask is not None:  # a car that sits on the same pixels one second earlier is stopped
                    cell = np.zeros((y1 - y0 + 1, x1 - x0 + 1), np.uint8)
                    cv.fillPoly(cell, [np.round(poly - [x0, y0]).astype(np.int32)], 255)
                    inside = cell > 0
                    overlap = float((prev_mask[y0:y1 + 1, x0:x1 + 1][inside] > 0).mean()) if inside.any() else 0.0
                    stopped = overlap >= STOP_OVERLAP
                ctr = poly.mean(axis=0)
                cars.append({"poly": np.round(poly, 1).tolist(), "box": [int(x0), int(y0), int(x1 - x0), int(y1 - y0)], "centroid": [round(float(ctr[0]), 1), round(float(ctr[1]), 1)],
                             "stopped": stopped, "stationaryOverlap": round(overlap, 2), "split": len(polys) > 1})
        # ground truth: SUMO's own vehicles in the footprint, at their centres (SUMO reports the front bumper)
        truth = []
        for v in poses_now:
            x, y = v[1] / 10.0, v[2] / 10.0
            if abs(x - c[0]) > VIEW_M / 2 or abs(y - c[1]) > VIEW_M / 2:
                continue
            length = {0: 4.5, 1: 8.0, 2: 6.0}.get(int(v[4]), 4.5)
            r = np.radians(v[3])
            truth.append((int(v[0]), x - np.sin(r) * length / 2, y - np.cos(r) * length / 2, v[5] / 10.0))
        pairs = sorted(((float(np.hypot(*(np.array(to_world(*d["centroid"])) - [tx, ty]))), i, k) for i, d in enumerate(cars) for k, (_, tx, ty, _) in enumerate(truth)))
        used_d, used_t, matched = set(), set(), 0
        for dist, i, k in pairs:
            if dist > 2.5:
                break
            if i in used_d or k in used_t:
                continue
            used_d.add(i)
            used_t.add(k)
            matched += 1
        for i, d in enumerate(cars):
            d["matchedTruth"] = i in used_d
        approaches = []
        for a in self._geometry(sim)["approaches"]:
            if not a["key"].startswith(jid + ":"):
                continue
            b_, a_ = np.array(a["b"]), np.array(a["a"])
            u = (a_ - b_) / (np.linalg.norm(a_ - b_) or 1.0)
            n_ = np.array([-u[1], u[0]])

            def inside(px: float, py: float) -> bool:
                d = np.array([px, py]) - b_
                return -2.0 <= float(d @ u) <= REGION_M and abs(float(d @ n_)) <= 7.0

            truth_q = sum(1 for _, tx, ty, sp in truth if sp < 0.1 and inside(tx, ty))
            det_q = sum(1 for d in cars if d["stopped"] and inside(*to_world(*d["centroid"])))
            approaches.append({"approach": a["key"], "cameraQueue": det_q, "sumoQueue": truth_q, "error": det_q - truth_q})
        ok, jpg = cv.imencode(".jpg", frame, [cv.IMWRITE_JPEG_QUALITY, 80])
        ok2, mjpg = cv.imencode(".png", mask_now)
        self.timing["camera"].append((time.perf_counter() - t0) * 1000)
        return {"available": True, "synthetic": True, "junction": jid, "t": t, "size": Z, "pixelsPerMetre": S, "footprintM": VIEW_M,
                "frame": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode(), "mask": "data:image/png;base64," + base64.b64encode(mjpg.tobytes()).decode(),
                "detections": cars, "blobs": len(blobs), "mergedBlobs": sum(1 for b in blobs if len(self._cars_in(b)) > 1), "splitCars": sum(1 for d in cars if d["split"]),
                "truthVehiclesInView": len(truth), "matched": matched, "countEstimate": len(cars),
                "precision": round(matched / len(cars), 3) if cars else None, "recall": round(matched / len(truth), 3) if truth else None,
                "approaches": approaches, "latencyMs": round(self.timing["camera"][-1], 2),
                "method": "background subtraction (|frame - empty road| > 22) -> connected components -> one oriented box per car: a lone car is its blob's rectangle; touching cars "
                          "(merged blobs) are cut along the lane into the number of cars the blob area implies; a car whose box overlaps >= 88% with the previous second's foreground is 'stopped'",
                "limits": "cars that touch are separated by an area-based cut, not by seeing the gap, so dense jams can be over- or under-split; real footage adds weather, glare, shadows and occlusion that this render does not"}

    # ------------------------------------------------------------------------------------------------ forecaster
    def forecaster_view(self, sim: Any) -> dict[str, Any]:
        from .contract import approach_table
        from .sim import FEATURES

        X = sim.state.X
        return {"features": list(FEATURES), "approaches": [a["key"] for a in approach_table()], "matrix": np.round(X.astype(np.float64), 2).tolist(),
                "sampleS": C.DECISION_S, "historyS": 35}


def poses(sim: Any) -> list[list[float]]:
    """Vehicle rows in the replay-frame layout [id, x*10, y*10, angle, type, speed*10, waiting] from the current subscription results."""
    from ..rl.sim import T, tc

    out = []
    for vid, r in T.vehicle.getAllSubscriptionResults().items():
        if tc.VAR_POSITION not in r:
            continue
        x, y = r[tc.VAR_POSITION]
        if x < -1e6:
            continue
        out.append([int(vid[1:]), round(x * 10), round(y * 10), r[tc.VAR_ANGLE], sim.vtype.get(vid, 0), round(r[tc.VAR_SPEED] * 10), 1 if r[tc.VAR_WAITING_TIME] > 0 else 0])
    return out
