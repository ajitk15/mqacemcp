"""Coverage for the mtime-based auto-reloading CSV cache (`server.csv_cache`).

These tests prove the production behaviour the user asked for: after the daily
extract replaces a CSV on disk, the next access reflects the new data **without
restarting the process / re-importing the module**. They also cover the
keep-last-good-on-failed-reload path.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd

from server.csv_cache import CsvCache

_LOG = logging.getLogger("test.csv_cache")


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _loader_for(path):
    """A minimal loader: returns a DataFrame, or None if the file is missing."""

    def _load():
        if not path.exists():
            return None
        return pd.read_csv(path, delimiter="|", header=0)

    return _load


def _bump_mtime(path):
    """Ensure a later mtime even on coarse-resolution filesystems."""
    future = time.time() + 10
    os.utime(path, (future, future))


def test_reloads_when_file_changes(tmp_path):
    csv = tmp_path / "data.csv"
    _write(csv, "a|b\n1|2\n")
    cache = CsvCache(csv, _loader_for(csv), _LOG, "test")

    assert len(cache.get()) == 1  # first load

    # Daily-swap simulation: rewrite with more rows + a newer mtime.
    _write(csv, "a|b\n1|2\n3|4\n5|6\n")
    _bump_mtime(csv)

    assert len(cache.get()) == 3, "cache must pick up the new file without restart"


def test_no_reparse_when_unchanged(tmp_path):
    csv = tmp_path / "data.csv"
    _write(csv, "a|b\n1|2\n")
    calls = {"n": 0}

    def _counting_loader():
        calls["n"] += 1
        return pd.read_csv(csv, delimiter="|", header=0)

    cache = CsvCache(csv, _counting_loader, _LOG, "test")
    cache.get()
    cache.get()
    cache.get()
    assert calls["n"] == 1, "unchanged file must be parsed only once"


def test_keeps_last_good_on_failed_reload(tmp_path):
    csv = tmp_path / "data.csv"
    _write(csv, "a|b\n1|2\n")
    cache = CsvCache(csv, _loader_for(csv), _LOG, "test")
    assert len(cache.get()) == 1

    # Simulate the file briefly disappearing mid-swap (loader returns None).
    csv.unlink()
    # Must keep serving the previously loaded data, not flap to empty.
    assert len(cache.get()) == 1

    # When a good file reappears, the cache recovers.
    _write(csv, "a|b\n7|8\n9|0\n")
    _bump_mtime(csv)
    assert len(cache.get()) == 2


def test_status_reports_freshness(tmp_path):
    csv = tmp_path / "data.csv"
    _write(csv, "a|b\n1|2\n")
    cache = CsvCache(csv, _loader_for(csv), _LOG, "test")

    before = cache.status()
    assert before["exists"] is True
    assert before["rows"] is None  # not loaded yet
    assert before["stale"] is True

    cache.get()
    after = cache.status()
    assert after["rows"] == 1
    assert after["loaded_at"] is not None
    assert after["stale"] is False
