"""Locate the SUMO installation and expose binary paths.

Resolution order:
1. ``SUMO_HOME`` environment variable (if it contains ``bin/sumo``).
2. The ``eclipse-sumo`` pip package (``import sumo; sumo.SUMO_HOME``).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name


def find_sumo_home() -> Path:
    env = os.environ.get("SUMO_HOME")
    if env and (Path(env) / "bin" / _exe("sumo")).exists():
        return Path(env)
    try:
        import sumo  # type: ignore

        home = Path(sumo.SUMO_HOME)
        if (home / "bin" / _exe("sumo")).exists():
            return home
    except Exception:  # pragma: no cover - depends on environment
        pass
    found = shutil.which("sumo")
    if found:
        return Path(found).resolve().parent.parent
    raise RuntimeError(
        "SUMO not found. Install with `pip install eclipse-sumo` or set SUMO_HOME."
    )


SUMO_HOME: Path = find_sumo_home()
os.environ.setdefault("SUMO_HOME", str(SUMO_HOME))
# Make the bundled TraCI / sumolib importable even when installed from SUMO_HOME only.
_tools = SUMO_HOME / "tools"
if _tools.exists() and str(_tools) not in sys.path:
    sys.path.append(str(_tools))


def binary(name: str) -> str:
    """Absolute path to a SUMO binary (sumo, netgenerate, netconvert, duarouter ...)."""
    path = SUMO_HOME / "bin" / _exe(name)
    if not path.exists():
        raise FileNotFoundError(f"SUMO binary missing: {path}")
    return str(path)


def sumo_version() -> str:
    import subprocess

    out = subprocess.run([binary("sumo"), "--version"], capture_output=True, text=True)
    first = (out.stdout or out.stderr).strip().splitlines()[0]
    return first.replace("Eclipse SUMO sumo ", "")
