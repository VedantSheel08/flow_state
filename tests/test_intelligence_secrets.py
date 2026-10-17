"""Secret handling: where the key may come from, that it is redacted, that the repository holds none, and that Gemini is not in the control loop."""

import ast
import io
import logging
import os
import re
import subprocess
from pathlib import Path

import pytest

from flowstate.intelligence import secrets as S

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE_AT_START = os.environ.get("FLOWSTATE_ENV_FILE")  # captured before the fixture clears it: lets the repository scan look for the *real* key
FAKE = "AI" + "za" + "SyFakeKeyForTests_0123456789abcdefg"  # run-time concatenation: no key-shaped literal in the repository


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for n in (*S.KEY_VARS, "FLOWSTATE_ENV_FILE"):
        monkeypatch.delenv(n, raising=False)
    S._known.clear()


def test_environment_variable_wins_and_env_file_is_a_development_fallback(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text(f'OTHER=1\nexport GEMINI_API_KEY="{FAKE}"  \nGOOGLE_API_KEY=ignored # comment\n', encoding="utf-8")
    assert S.gemini_api_key() is None  # nothing configured
    monkeypatch.setenv("FLOWSTATE_ENV_FILE", str(f))
    assert S.gemini_api_key() == FAKE
    import os

    assert "GEMINI_API_KEY" not in os.environ  # the file value is returned, never exported into the process environment
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert S.gemini_api_key() == "from-env"


def test_only_the_requested_variables_are_parsed(tmp_path):
    f = tmp_path / ".env"
    f.write_text("DATABASE_URL=postgres://user:pw@host/db\nGEMINI_API_KEY=abc\n", encoding="utf-8")
    assert S._parse_env_file(f, S.KEY_VARS) == {"GEMINI_API_KEY": "abc"}
    assert S._parse_env_file(tmp_path / "missing", S.KEY_VARS) == {}


def test_redaction_masks_the_known_key_and_anything_key_shaped():
    S._known.add("custom-secret-value")
    assert S.redact("x custom-secret-value y") == "x <redacted> y"
    assert FAKE not in S.redact(f"error for key {FAKE} at url")
    assert "<redacted>" in S.redact(FAKE)


def test_logging_filter_redacts_messages_arguments_and_exceptions():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    h.addFilter(S.RedactingFilter())
    lg = logging.getLogger("test.secrets.filter")
    lg.propagate, lg.level = False, logging.DEBUG
    lg.addHandler(h)
    try:
        lg.info("direct %s", FAKE)
        lg.info(f"fstring {FAKE}")
        try:
            raise RuntimeError(f"boom {FAKE}")
        except RuntimeError:
            lg.exception("failed")
    finally:
        lg.removeHandler(h)
    assert FAKE not in buf.getvalue() and buf.getvalue().count("<redacted>") >= 3


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    return [ROOT / p for p in out if (ROOT / p).is_file() and (ROOT / p).stat().st_size < 3_000_000]


def test_no_key_shaped_string_and_no_configured_key_in_any_file_git_would_commit(monkeypatch):
    shape = re.compile(S.KEY_SHAPE.pattern.encode())
    candidates = [os.environ.get(v) for v in S.KEY_VARS]  # the real key, if this machine has one: searched for, never printed
    if ENV_FILE_AT_START:
        candidates += list(S._parse_env_file(Path(ENV_FILE_AT_START), S.KEY_VARS).values())
    real = [c.encode() for c in candidates if c and len(c) >= 20]
    offenders = []
    for p in _tracked_files():
        data = p.read_bytes()
        if shape.search(data) or any(r in data for r in real):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"credential-like content in: {offenders}"


def test_env_files_are_git_ignored():
    for name in (".env", "backend/.env", "deploy/.env.production"):
        assert subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT).returncode == 0, name


def test_gemini_is_not_imported_anywhere_in_the_signal_control_path():
    """Controllers, the safety layer, the environment and the evaluation harness must never depend on a language model."""
    forbidden = ("gemini", "httpx", "flowstate.experiments", "flowstate.intelligence.explain")
    bad = []
    for p in sorted((ROOT / "backend" / "flowstate" / "rl").glob("*.py")) + sorted((ROOT / "backend" / "flowstate" / "sim").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            bad += [f"{p.name}: {m}" for m in mods if any(m.lower().startswith(f) or f in m.lower() for f in forbidden)]
    assert not bad, bad
