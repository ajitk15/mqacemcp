"""Stdlib logging factory shared across the unified MCP server.

Two handlers attached to the root logger:
- StreamHandler on stderr (always)
- TimedRotatingFileHandler writing to logs/app-YYYY-MM-DD.log (always)

The query log (per-tool-call JSONL) lives in `server.query_log` — this module
only handles the human-readable application log.
"""
from __future__ import annotations

import glob
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime

from server.config import LOG_DIR, LOG_LEVEL, LOG_RETENTION_DAYS

_CONFIGURED = False


class _DateStampedFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Rotates at midnight; writes to `app-YYYY-MM-DD.log` instead of `app.log.YYYY-MM-DD`.

    Power BI's "From Folder" connector matches files by glob, so date-stamped
    filenames are easier to ingest than the suffix scheme TimedRotatingFileHandler
    produces by default.
    """

    def __init__(self, log_dir, base_name: str, when: str = "midnight",
                 backup_count: int = 30) -> None:
        self._log_dir = log_dir
        self._base_name = base_name
        path = os.path.join(log_dir, f"{base_name}-{datetime.now():%Y-%m-%d}.log")
        super().__init__(
            path,
            when=when,
            backupCount=backup_count,
            encoding="utf-8",
            utc=False,
        )

    def doRollover(self) -> None:
        # On rollover, switch to a freshly named file for the new day.
        if self.stream:
            self.stream.close()
            self.stream = None
        new_path = os.path.join(
            self._log_dir, f"{self._base_name}-{datetime.now():%Y-%m-%d}.log"
        )
        self.baseFilename = os.path.abspath(new_path)
        self.stream = self._open()

        # Recompute next rollover time.
        current_time = int(time.time())
        new_rollover_at = self.computeRollover(current_time)
        while new_rollover_at <= current_time:
            new_rollover_at += self.interval
        self.rolloverAt = new_rollover_at


def _sweep_old_logs(pattern: str, retention_days: int) -> None:
    """Remove rotated log files older than retention_days. Best-effort, never raises."""
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _configure_root_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid stacking handlers on re-import (e.g. when run with reloaders).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(fmt)
    stderr_handler.setLevel(level)
    root.addHandler(stderr_handler)

    try:
        file_handler = _DateStampedFileHandler(
            LOG_DIR, base_name="app", backup_count=LOG_RETENTION_DAYS
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except Exception as e:
        # File logging is best-effort — never crash startup over disk issues.
        logging.getLogger(__name__).warning(
            "Could not attach file log handler in %s: %s", LOG_DIR, e
        )

    _sweep_old_logs(os.path.join(str(LOG_DIR), "app-*.log"), LOG_RETENTION_DAYS)
    _sweep_old_logs(
        os.path.join(str(LOG_DIR), "queries-*.jsonl"), LOG_RETENTION_DAYS
    )

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger. First call sets up handlers."""
    _configure_root_once()
    return logging.getLogger(name)
