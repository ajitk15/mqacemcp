"""Certificate inventory helpers: cached OFFLINE CSV loader + substring search.

All functions here are pure utilities — they do not register MCP tools. The
composite tool wrapper lives in `server.composite_tools`.

The inventory (`resources/cert_dump.csv`, shared with the granular server) is an
OFFLINE extract produced by an external job. There is no live system to query —
freshness depends on whenever the extract last ran. Columns are kept verbatim
(the date columns are display strings, not parsed as datetimes).
"""
from __future__ import annotations

import re

import pandas as pd

from server.config import CERT_DUMP_PATH
from server.logger import get_logger

logger = get_logger("mqacemcpserver-single.cert")

# Column order as produced by the extract job.
CERT_COLUMNS = ["alias", "cnname", "validfrom", "validuntil", "hostname"]

# ---------------------------------------------------------------------------
# cert_dump.csv (offline inventory) — cached at module level
# ---------------------------------------------------------------------------
_CERT_DUMP_CACHE: pd.DataFrame | None = None


def _load_cert_dump_from_disk() -> pd.DataFrame:
    if not CERT_DUMP_PATH.exists():
        logger.warning("Certificate inventory not found at %s", CERT_DUMP_PATH)
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            CERT_DUMP_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=0,
        )
        df.columns = [c.strip() for c in df.columns]
        # Strip string cells; leave date strings verbatim (no datetime coercion).
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        logger.info(
            "Certificate inventory loaded: %d rows, %d columns",
            len(df),
            len(df.columns),
        )
        return df
    except Exception:
        logger.exception("ERROR loading certificate inventory")
        return pd.DataFrame()


def load_cert_dump() -> pd.DataFrame:
    """Return the cached certificate inventory, loading from disk on first call."""
    global _CERT_DUMP_CACHE
    if _CERT_DUMP_CACHE is None:
        _CERT_DUMP_CACHE = _load_cert_dump_from_disk()
    return _CERT_DUMP_CACHE


# ---------------------------------------------------------------------------
# Substring search across all columns
# ---------------------------------------------------------------------------
def search_certs(search_string: str) -> list[dict]:
    """Search cert_dump.csv across all columns and return matching rows as dicts."""
    df = load_cert_dump()
    if df.empty:
        return []

    mask = df.astype(str).apply(
        lambda row: row.str.contains(
            re.escape(search_string), case=False, na=False
        ).any(),
        axis=1,
    )
    matches = df[mask]
    if matches.empty:
        return []

    results = []
    for _, r in matches.iterrows():
        results.append({col: str(r[col]).strip() for col in df.columns})
    return results
