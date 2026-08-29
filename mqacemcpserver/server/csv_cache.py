"""mtime-based auto-reloading cache for the offline CSV manifests.

The CSV manifests (`qmgr_dump.csv`, `node_dump.csv`, `node_config.csv`,
`cert_dump.csv`) are replaced once a day by an external extract job. The
loaders used to cache load-once-forever, so the server had to be restarted to
see new data. `CsvCache` instead checks the file's `(mtime, size)` on each
access and reloads only when it changed — so the daily swap is picked up on the
next tool call with no restart, no background thread, and no new dependency
(one `os.stat` per access).

Each loader passed in returns a DataFrame on a successful parse (even 0 rows)
or `None` on a missing file / parse error. On `None` the cache keeps the
previously loaded DataFrame and does NOT advance the recorded mtime, so a read
that lands mid-swap simply retries on the next call instead of caching a
partial/empty result.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

# Every CsvCache registers here so /healthz can report freshness for all of them.
_REGISTRY: list["CsvCache"] = []


class CsvCache:
    """Caches one CSV's DataFrame, reloading when the file changes on disk."""

    def __init__(
        self,
        path: Path,
        loader: Callable[[], pd.DataFrame | None],
        logger: logging.Logger,
        name: str,
    ) -> None:
        self._path = path
        self._loader = loader
        self._logger = logger
        self._name = name
        self._df: pd.DataFrame | None = None
        self._sig: tuple[float, int] | None = None  # (st_mtime, st_size) in memory
        self._loaded_at: datetime | None = None
        _REGISTRY.append(self)

    def _disk_sig(self) -> tuple[float, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def get(self) -> pd.DataFrame:
        """Return the cached DataFrame, reloading first if the file changed."""
        sig = self._disk_sig()
        if self._df is None or sig != self._sig:
            df = self._loader()
            if df is not None:
                self._df = df
                self._sig = sig
                self._loaded_at = datetime.now()
            elif self._df is None:
                # Nothing good cached yet and the file is missing/unreadable —
                # serve empty for now; do not record a signature so the next
                # call retries.
                self._df = pd.DataFrame()
            else:
                # Reload failed mid-swap; keep last-good and retry next call
                # (signature deliberately left unchanged).
                self._logger.warning(
                    "%s reload failed; keeping previously loaded data", self._name
                )
        return self._df

    def status(self) -> dict:
        """Cheap (stat-only) freshness snapshot for /healthz — never raises."""
        disk_sig = self._disk_sig()
        file_mtime = (
            datetime.fromtimestamp(disk_sig[0]).isoformat(timespec="seconds")
            if disk_sig
            else None
        )
        return {
            "name": self._name,
            "file": self._path.name,
            "exists": disk_sig is not None,
            "rows": None if self._df is None else int(len(self._df)),
            "file_mtime": file_mtime,
            "loaded_at": (
                self._loaded_at.isoformat(timespec="seconds")
                if self._loaded_at
                else None
            ),
            "stale": disk_sig is not None and disk_sig != self._sig,
        }


def all_status() -> list[dict]:
    """Freshness snapshot for every registered manifest (for /healthz)."""
    return [c.status() for c in _REGISTRY]
