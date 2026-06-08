"""IBM ACE helpers: REST client, CSV manifests, node→endpoint resolution."""
from __future__ import annotations

import json
import re

import httpx
import pandas as pd

from server.config import (
    ACE_ALLOWED_HOSTNAME_PREFIXES,
    ACE_NODE_CONFIG_PATH,
    ACE_NODE_DUMP_PATH,
    ACE_PASSWORD,
    ACE_USER_NAME,
)
from server.csv_cache import CsvCache
from server.errors import safe_error_message
from server.logger import get_logger
from server.query_log import record_endpoint
from server.safety import is_hostname_allowed

logger = get_logger("mqacemcpserver.ace")

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------
_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return a shared async HTTP client with optional ACE basic auth."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        auth = None
        if ACE_USER_NAME and ACE_PASSWORD:
            auth = httpx.BasicAuth(username=ACE_USER_NAME, password=ACE_PASSWORD)
        _HTTP_CLIENT = httpx.AsyncClient(verify=False, auth=auth, timeout=30.0)
    return _HTTP_CLIENT


async def aclose_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()


# ---------------------------------------------------------------------------
# node_dump.csv (offline inventory) — auto-reloads when the file changes
# ---------------------------------------------------------------------------
def _load_node_dump_from_disk() -> pd.DataFrame | None:
    if not ACE_NODE_DUMP_PATH.exists():
        logger.warning("ACE node dump not found at %s", ACE_NODE_DUMP_PATH)
        return None

    try:
        df = pd.read_csv(
            ACE_NODE_DUMP_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=0,
        )
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(
            columns={
                "extractedat": "timestamp",
                "hostname": "host",
                "resource": "status",
            }
        )
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].str.strip()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        logger.info(
            "ACE node dump loaded: %d rows, %d columns", len(df), len(df.columns)
        )
        return df
    except Exception:
        logger.exception("ERROR loading ACE node dump")
        return None


_node_dump_cache = CsvCache(
    ACE_NODE_DUMP_PATH, _load_node_dump_from_disk, logger, "ACE node dump"
)


def load_node_dump() -> pd.DataFrame:
    return _node_dump_cache.get()


# ---------------------------------------------------------------------------
# node_config.csv (node → host:port mapping) — auto-reloads when file changes
# ---------------------------------------------------------------------------
def _load_node_config_from_disk() -> pd.DataFrame | None:
    if not ACE_NODE_CONFIG_PATH.exists():
        logger.warning("ACE node config not found at %s", ACE_NODE_CONFIG_PATH)
        return None

    try:
        df = pd.read_csv(
            ACE_NODE_CONFIG_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=0,
        )
        df.columns = [c.strip() for c in df.columns]
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        if "nodeport" in df.columns:
            df["nodeport"] = (
                pd.to_numeric(df["nodeport"], errors="coerce")
                .fillna(7600)
                .astype(int)
            )
        return df
    except Exception:
        logger.exception("ERROR loading ACE node config")
        return None


_node_config_cache = CsvCache(
    ACE_NODE_CONFIG_PATH, _load_node_config_from_disk, logger, "ACE node config"
)


def load_node_config() -> pd.DataFrame:
    return _node_config_cache.get()


def get_node_endpoint(node: str) -> tuple[str, int]:
    """Return (host, port) for a given integration node from node_config.csv."""
    df = load_node_config()
    if df.empty:
        raise ValueError(
            "ACE node configuration is empty or missing (resources/node_config.csv)."
        )

    matches = df[df["node"].str.upper() == node.upper()]
    if matches.empty:
        raise ValueError(f"Integration Node '{node}' is not defined in node_config.csv.")

    row = matches.iloc[0]
    return str(row["host"]).strip(), int(row["nodeport"])


def hostname_allowed(hostname: str) -> tuple[bool, str]:
    """Apply the ACE-specific allow-list to a hostname."""
    return is_hostname_allowed(hostname, ACE_ALLOWED_HOSTNAME_PREFIXES)


# ---------------------------------------------------------------------------
# REST helper for the ACE Admin API
# ---------------------------------------------------------------------------
def _err_envelope(message: str, **details) -> str:
    return json.dumps(
        {"status": "error", "message": message, "details": details}, indent=2
    )


async def fetch_ace(
    target_node: str, path: str, component: str, **kwargs
) -> str:
    """Call the ACE Admin REST API on a specific integration node and format the response.

    Applies the hostname allow-list before issuing the network request.
    Always returns a JSON-encoded string envelope (never raises).
    """
    try:
        host, port = get_node_endpoint(target_node)
    except ValueError as e:
        # Curated "node not configured" message — safe to surface as-is.
        logger.warning("Unknown ACE node %s: %s", target_node, e)
        return _err_envelope(str(e), node=target_node)

    allowed, message = hostname_allowed(host)
    if not allowed:
        return _err_envelope(message.strip(), node=target_node, host=host)

    url = f"https://{host}:{port}/apiv2{path}"
    record_endpoint(url)

    client = get_http_client()
    try:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text}

        state = data.get("state", "unknown") if isinstance(data, dict) else "unknown"

        success_res = {
            "status": "success",
            "component": component,
            **kwargs,
            "runtime_state": state,
            "raw_response": data,
        }
        return json.dumps(success_res, indent=2)

    except httpx.HTTPStatusError as err:
        msg = safe_error_message(
            err,
            hint="ACE Admin API call failed",
            extra={"node": target_node, "host": host, "port": port},
        )
        return _err_envelope(msg, node=target_node)
    except Exception as err:
        msg = safe_error_message(
            err,
            hint="ACE Admin API call failed",
            extra={"node": target_node, "host": host, "port": port},
        )
        return _err_envelope(msg, node=target_node)


# ---------------------------------------------------------------------------
# Local-dump search — for the offline node_dump.csv
# ---------------------------------------------------------------------------
def search_node_dump(search_string: str) -> list[dict]:
    """Search node_dump.csv across all string columns and return matching rows."""
    df = load_node_dump()
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
        ts = r["timestamp"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if pd.notnull(ts) else ""
        results.append(
            {
                "timestamp": ts_str,
                "host": r["host"],
                "node": r["node"],
                "status": r["status"],
            }
        )
    return results


def nodes_on_host(hostname: str) -> list[str]:
    """Return the distinct ACE integration node names seen on `hostname`.

    Exact (case-insensitive) match against the `host` column of the OFFLINE
    node_dump.csv. Used to pivot a certificate's hostname to the node(s)
    running there. Returns an empty list for a host with no ACE node (e.g. a
    pure MQ host) or when the dump is empty/missing.
    """
    if not hostname:
        return []
    df = load_node_dump()
    if df.empty:
        return []
    target = hostname.strip().lower()
    matches = df[df["host"].astype(str).str.strip().str.lower() == target]
    return sorted(
        {str(n).strip() for n in matches["node"] if str(n).strip()}
    )


# ---------------------------------------------------------------------------
# Startup connectivity check
# ---------------------------------------------------------------------------
async def verify_connectivity() -> None:
    """Ping every configured ACE node once at startup; log result. Never raises."""
    df = load_node_config()
    if df.empty:
        return

    for _, row in df.iterrows():
        node = str(row.get("node", "")).strip()
        host = str(row.get("host", "")).strip()
        port = int(row.get("nodeport", 7600))
        if not node or not host:
            continue
        allowed, _ = hostname_allowed(host)
        if not allowed:
            logger.info(
                "ACE node %s on %s skipped (not in allow-list).", node, host
            )
            continue
        try:
            auth = None
            if ACE_USER_NAME and ACE_PASSWORD:
                auth = httpx.BasicAuth(username=ACE_USER_NAME, password=ACE_PASSWORD)
            async with httpx.AsyncClient(verify=False, auth=auth) as client:
                resp = await client.get(
                    f"https://{host}:{port}/apiv2", timeout=5.0
                )
                if resp.status_code in (200, 401):
                    logger.info("ACE node %s reachable at %s:%d.", node, host, port)
                else:
                    logger.warning(
                        "ACE node %s returned HTTP %d at %s:%d",
                        node,
                        resp.status_code,
                        host,
                        port,
                    )
        except Exception as e:
            logger.warning(
                "Cannot reach ACE node %s at %s:%d. Error: %s",
                node, host, port, e,
            )
