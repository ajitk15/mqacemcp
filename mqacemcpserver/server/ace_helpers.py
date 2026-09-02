"""IBM ACE helpers: REST client, CSV manifests, node→endpoint resolution."""
from __future__ import annotations

import difflib
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
from server.errors import safe_error_message
from server.csv_cache import CsvCache
from server.logger import get_logger
from server.query_log import record_endpoint
from server.safety import is_hostname_allowed

logger = get_logger("mqacemcpserver.ace")

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


# --- BIP message parsing --------------------------------------------------
#
# node_dump.csv carries one free-text BIP message per row, so the execution
# group, application and flow are only implicit inside that text. Splitting
# them into real columns is what lets a caller ask "which applications run on
# EG X" and get an EXACT answer, instead of a substring match that also drags
# in ACE_DEMO_CACHE because both rows happen to contain "Cache".
#
# Grammar present in the extract:
#   BIP1286I: Integration server 'EG' on integration node 'NODE' is running.
#   BIP1275I: Application 'APP' on integration server 'EG' is running.
#   BIP1277I/BIP1278I: Message flow 'FLOW' on integration server 'EG' is
#       running/stopped. (Application 'APP', Library '')
#   BIP1299I: File 'F' is deployed to integration server 'EG'. (Application 'APP', ...)
#   BIP1390I: Policy project 'P' is deployed to integration server 'EG'.
#   BIP1391I: Policy 'X' type 'T' is deployed as 'P/X.policyxml' to integration server 'EG'.

_RE_BIP_CODE = re.compile(r"(BIP[0-9]+[A-Z]):")
# Every code except BIP1286I qualifies the server with on/to; in BIP1286I the
# integration server IS the subject of the sentence.
_RE_EG_QUALIFIED = re.compile(r"(?:on|to) integration server '([^']*)'", re.IGNORECASE)
_RE_EG_SUBJECT = re.compile(r"Integration server '([^']*)'", re.IGNORECASE)
_RE_APPLICATION = re.compile(r"Application '([^']*)'", re.IGNORECASE)
_RE_FLOW = re.compile(r"Message flow '([^']*)'", re.IGNORECASE)
_RE_FILE = re.compile(r"File '([^']*)'", re.IGNORECASE)
_RE_POLICY = re.compile(r"Policy(?: project)? '([^']*)'", re.IGNORECASE)
_RE_STATE = re.compile(r"[ ]is (running|stopped)[.]", re.IGNORECASE)

_BIP_KINDS = {
    "BIP1286I": "server",
    "BIP1275I": "application",
    "BIP1277I": "flow",
    "BIP1278I": "flow",
    "BIP1299I": "file",
    "BIP1390I": "policy",
    "BIP1391I": "policy",
}

_STRUCTURED_COLUMNS = (
    "bip_code",
    "resource_kind",
    "eg",
    "application",
    "flow",
    "file",
    "policy",
    "state",
)


def _first(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _parse_resource(text: str) -> dict:
    """Split one BIP status line into (eg, application, flow, file, state).

    Deliberately tolerant: an unrecognised BIP code still yields an `eg` from
    the generic "on/to integration server 'X'" form, and a line that parses
    into nothing comes back with empty fields rather than being dropped.
    """
    text = str(text or "")
    code = _first(_RE_BIP_CODE, text)
    kind = _BIP_KINDS.get(code.upper(), "")

    eg = _first(_RE_EG_QUALIFIED, text) or _first(_RE_EG_SUBJECT, text)

    return {
        "bip_code": code,
        "resource_kind": kind,
        "eg": eg,
        "application": _first(_RE_APPLICATION, text),
        "flow": _first(_RE_FLOW, text),
        "file": _first(_RE_FILE, text) if kind == "file" else "",
        "policy": _first(_RE_POLICY, text) if kind == "policy" else "",
        "state": _first(_RE_STATE, text).lower(),
    }


def _add_structured_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Derive the EG/application/flow columns from the free-text `status`."""
    if "status" not in df.columns:
        for col in _STRUCTURED_COLUMNS:
            df[col] = ""
        return df
    parsed = pd.DataFrame(
        [_parse_resource(t) for t in df["status"]],
        index=df.index,
        columns=list(_STRUCTURED_COLUMNS),
    ).fillna("")
    return pd.concat([df, parsed], axis=1)


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
        df = _add_structured_columns(df)
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


def _rows_to_results(matches: pd.DataFrame) -> list[dict]:
    """Shape dump rows into the public {extracted_at, host, node, status} dicts.

    `extracted_at` is the CSV's `extractedat` column — WHEN THE EXTRACT JOB RAN,
    never when the thing described in `status` happened. The BIP text carries no
    event time at all, so nothing here can date a start, restart or deployment.
    The public key is deliberately not called `timestamp`: a bare `timestamp`
    sitting beside "…is running." reads as an event time and gets reported as
    one. Live start/restart times come from the Admin REST API instead (see
    `ace_node_overview`).
    """
    results = []
    for _, r in matches.iterrows():
        ts = r["timestamp"]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if pd.notnull(ts) else ""
        results.append(
            {
                "extracted_at": ts_str,
                "host": r["host"],
                "node": r["node"],
                "status": r["status"],
            }
        )
    return results


# Columns search_node_dump matches against. Pinned deliberately: the frame also
# carries the derived eg/application/flow columns (see _parse_resource), and
# letting the loose substring search see those would widen the haystack for
# every existing caller. Use server_rows()/server_inventory() for EG-scoped
# lookups instead.
_DUMP_SEARCH_COLUMNS = ("timestamp", "host", "node", "status")


def search_node_dump(search_string: str) -> list[dict]:
    """Search node_dump.csv and return matching {extracted_at, host, node, status} rows.

    Unanchored substring match. For "everything belonging to execution group
    X" use server_inventory(), which matches the parsed `eg` column exactly.

    `extracted_at` is the extract job's run time, not an event time — see
    _rows_to_results.
    """
    df = load_node_dump()
    if df.empty:
        return []

    cols = [c for c in _DUMP_SEARCH_COLUMNS if c in df.columns]
    mask = df[cols].astype(str).apply(
        lambda row: row.str.contains(
            re.escape(search_string), case=False, na=False
        ).any(),
        axis=1,
    )
    matches = df[mask]
    if matches.empty:
        return []

    return _rows_to_results(matches)


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


def known_servers(node: str | None = None) -> list[str]:
    """Every integration server (execution group) name in the OFFLINE dump."""
    df = load_node_dump()
    if df.empty or "eg" not in df.columns:
        return []
    if node:
        df = df[
            df["node"].astype(str).str.strip().str.upper() == node.strip().upper()
        ]
    return sorted({str(v).strip() for v in df["eg"] if str(v).strip()})


def resolve_server_name(name: str) -> str | None:
    """Canonical EG name when `name` IS a known execution group, else None.

    Exact, case-insensitive. Lets a caller tell "the user named an EG" apart
    from "the user typed a free-text fragment" without guessing.
    """
    if not name:
        return None
    target = name.strip().lower()
    for server in known_servers():
        if server.lower() == target:
            return server
    return None


def suggest_servers(name: str, node: str | None = None) -> list[str]:
    """Closest known EG names, for a "did you mean" hint on an unknown server."""
    if not name:
        return []
    return difflib.get_close_matches(name.strip(), known_servers(node), n=3, cutoff=0.5)


def server_rows(server: str, node: str | None = None) -> pd.DataFrame:
    """Dump rows whose parsed integration server EXACTLY equals `server`.

    Exact and case-insensitive on the derived `eg` column — never a substring
    — so 'AmazonS3' cannot drag in Policy 'AmazonS31', and a row belonging to
    a different execution group can never be returned.
    """
    df = load_node_dump()
    if df.empty or "eg" not in df.columns or not server:
        return df.iloc[0:0]
    matches = df[df["eg"].astype(str).str.strip().str.lower() == server.strip().lower()]
    if node:
        matches = matches[
            matches["node"].astype(str).str.strip().str.upper()
            == node.strip().upper()
        ]
    return matches


def nodes_hosting_server(server: str) -> list[str]:
    """Integration nodes that actually HOST `server`, in dump order.

    Replaces the old "nodes whose dump mentions the name" discovery, which
    matched any row that merely contained the string.
    """
    matches = server_rows(server)
    if matches.empty:
        return []
    found: list[str] = []
    for n in matches["node"]:
        n = str(n).strip()
        if n and n not in found:
            found.append(n)
    return found


def nodes_hosting_application(application: str) -> list[str]:
    """Integration nodes hosting `application`, matched exactly on the app column."""
    df = load_node_dump()
    if df.empty or "application" not in df.columns or not application:
        return []
    matches = df[
        df["application"].astype(str).str.strip().str.lower()
        == application.strip().lower()
    ]
    found: list[str] = []
    for n in matches["node"]:
        n = str(n).strip()
        if n and n not in found:
            found.append(n)
    return found


def dump_rows(
    server: str | None = None,
    application: str | None = None,
    node: str | None = None,
) -> list[dict]:
    """Dump rows filtered by EXACT eg / application / node.

    Same {extracted_at, host, node, status} shape as search_node_dump, but
    selected by parsed field equality rather than a substring sweep, so the
    result can never contain a row belonging to another execution group.

    `extracted_at` is the extract job's run time, not an event time — see
    _rows_to_results.
    """
    df = load_node_dump()
    if df.empty:
        return []
    if server:
        if "eg" not in df.columns:
            return []
        df = df[df["eg"].astype(str).str.strip().str.lower() == server.strip().lower()]
    if application:
        if "application" not in df.columns:
            return []
        df = df[
            df["application"].astype(str).str.strip().str.lower()
            == application.strip().lower()
        ]
    if node:
        df = df[
            df["node"].astype(str).str.strip().str.upper() == node.strip().upper()
        ]
    return _rows_to_results(df)


def server_inventory(server: str, node: str | None = None) -> dict | None:
    """EG-scoped inventory from the OFFLINE dump; None when the EG is unknown.

    Returns the applications on `server` (each with its message flows), plus
    flows deployed directly on the server, files and policies. Every entry
    comes from a row whose `eg` equals `server` exactly, so nothing from
    another execution group can leak in. With `node` omitted the EG is
    summarised across every node hosting it (applications de-duplicated).
    """
    matches = server_rows(server, node)
    if matches.empty:
        return None

    canonical = str(matches.iloc[0]["eg"]).strip()
    apps: dict[str, dict] = {}
    server_flows: list[dict] = []
    files: list[str] = []
    policies: list[str] = []
    state = ""

    for _, r in matches.iterrows():
        kind = str(r.get("resource_kind", "")).strip()
        app = str(r.get("application", "")).strip()
        row_state = str(r.get("state", "")).strip()

        if kind == "server":
            state = state or row_state
        elif kind == "application":
            entry = apps.setdefault(app, {"name": app, "state": "", "flows": []})
            entry["state"] = entry["state"] or row_state
        elif kind == "flow":
            flow = {"name": str(r.get("flow", "")).strip(), "state": row_state}
            if app:
                entry = apps.setdefault(app, {"name": app, "state": "", "flows": []})
                if flow not in entry["flows"]:
                    entry["flows"].append(flow)
            elif flow not in server_flows:
                server_flows.append(flow)
        elif kind == "file":
            f = str(r.get("file", "")).strip()
            if f and f not in files:
                files.append(f)
        elif kind == "policy":
            p = str(r.get("policy", "")).strip()
            if p and p not in policies:
                policies.append(p)

    inventory = {
        "server": canonical,
        "nodes": [node.strip()] if node else nodes_hosting_server(server),
        "state": state,
        "application_count": len(apps),
        "applications": list(apps.values()),
        "server_level_flows": server_flows,
        "files": files,
        "policies": policies,
    }
    return inventory


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
