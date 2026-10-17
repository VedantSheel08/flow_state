"""Test support for the intelligence layer: the Gemini REST client and the grounding check live in ``flowstate.experiments`` on another
branch. Set ``FLOWSTATE_INTEGRATIONS_PATH`` to that branch's ``backend/flowstate`` directory to make them importable; tests that need
them are skipped otherwise (the guard itself is tested without them)."""

import importlib
import os
from pathlib import Path

import flowstate


def use_integrations() -> bool:
    p = os.environ.get("FLOWSTATE_INTEGRATIONS_PATH")
    if p and Path(p).is_dir() and p not in flowstate.__path__:
        flowstate.__path__.append(p)
    try:
        importlib.import_module("flowstate.experiments.assistant")
        return True
    except ImportError:
        return False
