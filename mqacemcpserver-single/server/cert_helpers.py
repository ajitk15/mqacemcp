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
from datetime import date, datetime

import pandas as pd

from server.config import CERT_DUMP_PATH
from server.csv_cache import CsvCache
from server.logger import get_logger

logger = get_logger("mqacemcpserver-single.cert")

# Column order as produced by the extract job.
CERT_COLUMNS = [
    "hostname",
    "alias",
    "cn_name",
    "valid_from",
    "valid_until",
    "expirydays",
]

# ---------------------------------------------------------------------------
# cert_dump.csv (offline inventory) — auto-reloads when the file changes
# ---------------------------------------------------------------------------
def _load_cert_dump_from_disk() -> pd.DataFrame | None:
    if not CERT_DUMP_PATH.exists():
        logger.warning("Certificate inventory not found at %s", CERT_DUMP_PATH)
        return None

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
        return None


_cert_dump_cache = CsvCache(
    CERT_DUMP_PATH, _load_cert_dump_from_disk, logger, "Certificate inventory"
)


def load_cert_dump() -> pd.DataFrame:
    """Return the certificate inventory, reloading if the CSV changed on disk."""
    return _cert_dump_cache.get()


# ---------------------------------------------------------------------------
# Live expiry-days computation
# ---------------------------------------------------------------------------
def compute_expiry_days(valid_until: str, today: date | None = None) -> int | None:
    """Whole days from `today` until the `valid_until` date string.

    `valid_until` is the Java `Date.toString()` form the extract emits, e.g.
    ``"Thu Jun 25 12:00:00 EDT 2026"``. The timezone abbreviation can't be
    parsed portably via ``%Z``, so it is dropped before parsing (date-only
    granularity is all we need). Negative means already expired. Returns
    ``None`` if the string can't be parsed.
    """
    if not valid_until:
        return None
    today = today or date.today()
    parts = valid_until.strip().split()
    # Drop the timezone token ("EDT"/"EST"/…) from the 6-token Java form.
    if len(parts) == 6:
        parts = parts[:4] + parts[5:]
    cleaned = " ".join(parts)
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b %d %Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return (parsed.date() - today).days
        except ValueError:
            continue
    logger.warning("Could not parse valid_until for expiry days: %r", valid_until)
    return None


# ---------------------------------------------------------------------------
# Substring search across all columns
# ---------------------------------------------------------------------------
def search_certs(search_string: str) -> list[dict]:
    """Search cert_dump.csv across all columns and return matching rows as dicts.

    `expirydays` is (re)computed live from `valid_until` against today's date —
    the CSV's own `expirydays` is a frozen extract-time value, so it is
    overridden here whenever `valid_until` parses.
    """
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
        row = {col: str(r[col]).strip() for col in df.columns}
        if "valid_until" in row:
            days = compute_expiry_days(row.get("valid_until", ""))
            if days is not None:
                row["expirydays"] = str(days)
        results.append(row)
    return results
