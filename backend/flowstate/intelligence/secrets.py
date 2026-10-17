"""Secret handling for the intelligence pipeline: where the Gemini key comes from, and making sure it cannot leak.

* The key is read from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) in the process environment. For local development
  ``FLOWSTATE_ENV_FILE`` may name a ``.env`` file; only the requested variable is parsed, the value is returned to the
  caller and **never written into** ``os.environ``, logs, exceptions or API responses. No path is hard-coded.
* In production the variable comes from the service's protected environment file (``chmod 600``); nothing in this repository
  reads or contains a credential, and ``.env`` files are git-ignored.
* ``redact`` / ``RedactingFilter`` mask the known key and anything shaped like a Google API key, so a provider error message
  or a stray log line cannot reveal one.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")  # Google API keys; masked even when the exact value is unknown
_known: set[str] = set()


def _parse_env_file(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m or m.group(1) not in names:
            continue
        v = m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:  # inline comment on an unquoted value
            v = v.split(" #", 1)[0].rstrip()
        if v:
            out[m.group(1)] = v
    return out


def gemini_api_key() -> str | None:
    """The configured key, or ``None``. Environment first, then the optional ``FLOWSTATE_ENV_FILE``. The key is remembered for redaction."""
    key = next((os.environ[n] for n in KEY_VARS if os.environ.get(n)), None)
    if key is None and os.environ.get("FLOWSTATE_ENV_FILE"):
        found = _parse_env_file(Path(os.environ["FLOWSTATE_ENV_FILE"]), KEY_VARS)
        key = next((found[n] for n in KEY_VARS if n in found), None)
    if key:
        _known.add(key)
    return key


def redact(text: object) -> str:
    s = str(text)
    for k in sorted(_known, key=len, reverse=True):
        s = s.replace(k, "<redacted>")
    return KEY_SHAPE.sub("<redacted>", s)


class RedactingFilter(logging.Filter):
    """Masks keys in every record that passes through the handlers it is attached to (message, arguments and exception text)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args) if isinstance(record.args, tuple) else record.args
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact("".join(logging.Formatter().formatException(record.exc_info)))
            record.exc_info = None
        return True


def install_log_redaction(logger_names: tuple[str, ...] = ("", "httpx", "httpcore", "uvicorn", "uvicorn.error", "uvicorn.access")) -> None:
    """Attach the filter to the root and HTTP-library loggers' handlers (and the loggers themselves). Idempotent."""
    for name in logger_names:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in lg.filters):
            lg.addFilter(RedactingFilter())
        for h in lg.handlers:
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(RedactingFilter())
