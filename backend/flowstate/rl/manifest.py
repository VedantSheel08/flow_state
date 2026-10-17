"""Artifact manifest: every file under ``artifacts/rl`` with size and SHA-256, and whether git carries it.

Intermediate checkpoints are not committed (``.gitignore``); the manifest still lists them so a copy restored from
backup storage can be verified byte for byte::

    PYTHONPATH=backend python -m flowstate.rl.manifest            # (re)write artifacts/rl/MANIFEST.json
    PYTHONPATH=backend python -m flowstate.rl.manifest --verify   # check files on disk against the committed manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .controllers import artifacts_dir

SKIP_SUFFIXES = {".pyc"}
SKIP_NAMES = {"MANIFEST.json", "train_stdout.log"}  # the manifest itself; stdout duplicates progress.csv and carries absolute paths


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ignored(root: Path, rel: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=root, capture_output=True)
    return r.returncode == 0


def build() -> dict:
    art = artifacts_dir()
    root = art.parents[1]
    files = []
    for p in sorted(art.rglob("*")):
        if not p.is_file() or p.suffix in SKIP_SUFFIXES or p.name in SKIP_NAMES or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        files.append({"path": rel, "bytes": p.stat().st_size, "sha256": _sha(p), "in_git": not _ignored(root, rel)})
    return {"note": "Files with in_git=false are excluded from the repository (intermediate checkpoints). Keep them in external storage "
                    "(for example a release asset or a shared drive) and restore them to the same relative path; verify with --verify.",
            "files": files}


def verify(manifest: dict) -> list[str]:
    root = artifacts_dir().parents[1]
    bad = []
    for f in manifest["files"]:
        p = root / f["path"]
        if not p.exists():
            if f["in_git"]:
                bad.append(f"missing: {f['path']}")
        elif _sha(p) != f["sha256"]:
            bad.append(f"changed: {f['path']}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flowstate.rl.manifest")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args(argv)
    out = artifacts_dir() / "MANIFEST.json"
    if a.verify:
        problems = verify(json.loads(out.read_text()))
        print("\n".join(problems) or f"all {len(json.loads(out.read_text())['files'])} manifest entries verified")
        return 1 if problems else 0
    m = build()
    out.write_text(json.dumps(m, indent=1))
    kept = sum(f["in_git"] for f in m["files"])
    print(f"wrote {out} ({len(m['files'])} files, {kept} committed, {len(m['files']) - kept} external)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
