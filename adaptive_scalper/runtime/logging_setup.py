"""Structured JSONL + human-readable rotating logs (directive section 109).

Both files rotate (10 MB x 5). Records carry UTC timestamps. Nothing here
logs credentials: the runtime never handles passwords (MT5 login happens
in the terminal itself), and `_redact` masks anything that looks like one
if it ever appears in a message.
"""

from __future__ import annotations

import json
import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+")


def _redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "severity": record.levelname, "subsystem": record.name, "event": _redact(record.getMessage()),
        }
        for key in ("symbol", "strategy", "decision_id", "proposal_id", "order_id", "reason"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


class _HumanFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record))


def configure_logging(log_dir: str | Path, *, level: int = logging.INFO) -> Path:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_adaptive_scalper", False):
            root.removeHandler(handler)
    json_handler = RotatingFileHandler(path / "adaptive_scalper.jsonl", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    json_handler.setFormatter(_JsonFormatter())
    human_handler = RotatingFileHandler(path / "adaptive_scalper.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    human_handler.setFormatter(_HumanFormatter("%(asctime)sZ %(levelname)s %(name)s: %(message)s"))
    for handler in (json_handler, human_handler):
        handler._adaptive_scalper = True
        root.addHandler(handler)
    return path
