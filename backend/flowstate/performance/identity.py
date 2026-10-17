"""Content identities for prepared live engines. Never key a state by seed alone."""
from __future__ import annotations

import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import platform

from .. import sumo_env

SCHEMA = 1


def resource_digests(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in
            (".json", ".csv", ".xml", ".toml", ".yaml", ".yml", ".txt")}


def dependencies():
    out = {}
    for package in ("eclipse-sumo", "libsumo", "traci", "sumolib", "pydantic"):
        try:
            out[package] = version(package)
        except PackageNotFoundError:
            out[package] = None
    return out


def identity(scenario: str, params: dict, seed: int, networks: Path, *, warmup: float,
             backend: str = "libsumo", step_length: float = 0.25) -> dict:
    from ..scenarios.catalog import create
    resolved = create(scenario, params)
    network = resolved.network_id()
    paths = sorted(networks.glob(f"{network}.*"))
    if not any(p.name == f"{network}.net.xml" for p in paths):
        raise FileNotFoundError(f"network not built: {network}")
    code = Path(__file__).resolve().parents[1]
    manifest = {
        "schema": SCHEMA, "sumo": sumo_env.sumo_version(), "backend": backend,
        "platform": platform.platform(), "python": platform.python_version(),
        "dependencies": dependencies(),
        "scenario": scenario, "params": resolved.params, "seed": seed,
        "warmup": warmup, "stepLength": step_length,
        "networkFiles": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()},
        "codeFiles": {p.relative_to(code).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(code.rglob("*.py"))},
        "resourceFiles": resource_digests(code),
    }
    key = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {"key": key, "manifest": manifest}
