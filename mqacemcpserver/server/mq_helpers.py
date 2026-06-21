"""IBM MQ helpers: REST client, CSV manifest, formatters, and friendly errors.

All functions here are pure utilities — they do not register MCP tools.
The tool wrappers live in `server.mq_tools`.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx
import pandas as pd

from server.config import (
    MQ_ALLOWED_HOSTNAME_PREFIXES,
    MQ_PASSWORD,
    MQ_QMGR_DUMP_PATH,
    MQ_URL_BASE,
    MQ_USER_NAME,
)
from server.csv_cache import CsvCache
from server.errors import safe_error_message
from server.logger import get_logger
from server.query_log import record_endpoint
from server.safety import is_hostname_allowed

logger = get_logger("mqacemcpserver.mq")

# Standard CSRF token value accepted by IBM MQ REST API (any non-empty value works)
CSRF_TOKEN = "token"

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------
_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return a shared async HTTP client to reuse TLS handshakes across calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        auth = httpx.BasicAuth(username=MQ_USER_NAME, password=MQ_PASSWORD)
        _HTTP_CLIENT = httpx.AsyncClient(verify=False, auth=auth, timeout=30.0)
    return _HTTP_CLIENT


async def aclose_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()


async def mq_get(url: str, **kwargs):
    """GET helper that records the endpoint URL onto the in-flight query log record."""
    record_endpoint(url)
    return await get_http_client().get(url, **kwargs)


async def mq_post(url: str, **kwargs):
    """POST helper that records the endpoint URL onto the in-flight query log record."""
    record_endpoint(url)
    return await get_http_client().post(url, **kwargs)


# ---------------------------------------------------------------------------
# CSV manifest (qmgr_dump.csv) — auto-reloads when the file changes
# ---------------------------------------------------------------------------
def _load_csv_from_disk() -> pd.DataFrame | None:
    if not MQ_QMGR_DUMP_PATH.exists():
        logger.warning("MQ manifest not found at %s", MQ_QMGR_DUMP_PATH)
        return None

    try:
        df = pd.read_csv(
            MQ_QMGR_DUMP_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=0,
        )
        df.columns = [c.strip() for c in df.columns]
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        df = df.rename(
            columns={
                "qmname": "qmgr",
                "objecttype": "object_type",
                "objectdef": "mqsc_command",
            }
        )
        if "extractedat" in df.columns:
            df["extractedat"] = pd.to_datetime(df["extractedat"], errors="coerce")
        logger.info(
            "MQ manifest loaded: %d rows, %d columns", len(df), len(df.columns)
        )
        return df
    except Exception:
        logger.exception("ERROR loading MQ manifest")
        return None


_csv_cache = CsvCache(MQ_QMGR_DUMP_PATH, _load_csv_from_disk, logger, "MQ manifest")


def load_csv() -> pd.DataFrame:
    """Return the MQ manifest dataframe, reloading if the CSV changed on disk."""
    return _csv_cache.get()


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------
def build_url(target_hostname: str, path: str) -> str:
    """Replace the hostname in MQ_URL_BASE with target_hostname and append path."""
    parsed = urlparse(MQ_URL_BASE)
    new_netloc = (
        f"{target_hostname}:{parsed.port}" if parsed.port else target_hostname
    )
    return parsed._replace(netloc=new_netloc).geturl() + path


def hostname_allowed(hostname: str) -> tuple[bool, str]:
    """Apply the MQ-specific allow-list to a hostname."""
    return is_hostname_allowed(hostname, MQ_ALLOWED_HOSTNAME_PREFIXES)


# ---------------------------------------------------------------------------
# Friendly error formatting
# ---------------------------------------------------------------------------
def friendly_error(err: Exception, hostname: str = "") -> str:
    """Return a user-safe message and log the raw error.

    User-facing strings never contain the raw exception, response body,
    or full URL. Mapped HTTP-status / network categories get a curated
    sentence; everything else falls through to ``safe_error_message`` which
    tags the response with a request_id for correlation in the app log.
    """
    extra = {"hostname": hostname} if hostname else {}
    return safe_error_message(err, hint="MQ REST API call failed", extra=extra)


# ---------------------------------------------------------------------------
# Response prettifiers
# ---------------------------------------------------------------------------
def prettify_dspmq(payload: bytes) -> str:
    json_output = json.loads(payload.decode("utf-8"))
    lines = []
    for x in json_output.get("qmgr", []):
        lines.append(f"name={x.get('name', 'N/A')}, state={x.get('state', 'N/A')}")
    return "\n".join(lines) if lines else "No queue managers returned."


def prettify_dspmqver(payload: bytes) -> str:
    json_output = json.loads(payload.decode("utf-8"))
    lines = ["\n---"]
    for x in json_output.get("installation", []):
        lines.append(
            f"Name: {x.get('name', 'N/A')}\n"
            f"Version: {x.get('version', 'N/A')}\n"
            f"Architecture: {x.get('architecture', 'N/A')}\n"
            f"Installation Path: {x.get('installationPath', 'N/A')}\n---"
        )
    return "\n".join(lines)


# Headers stripped from MQSC responses for a cleaner output
_STRIP_HEADERS = [
    "AMQ8409I: Display Queue details.",
    "AMQ8450I: Display Channel details.",
    "AMQ8420I: Display Queue Manager details.",
]


def prettify_runmqsc(payload: bytes) -> str:
    """Format MQSC command response for both z/OS and distributed queue managers."""
    json_output = json.loads(payload.decode("utf-8"))
    lines: list[str] = []

    for x in json_output.get("commandResponse", []):
        text_list = x.get("text", [])
        # z/OS responses start with CSQN205I
        if text_list and text_list[0].startswith("CSQN205I"):
            text_list.pop(0)
            if text_list:
                text_list.pop()
            for y in text_list:
                lines.append(y[15:].strip())
        else:
            for line in text_list:
                line_s = line.strip()
                if not line_s:
                    continue

                # 1. Skip echoes (e.g. "1 : DISPLAY ...")
                if line_s[0].isdigit() and " : " in line_s:
                    continue

                # 2. Strip known headers
                for h in _STRIP_HEADERS:
                    if line_s.startswith(h):
                        line_s = line_s[len(h):].strip()
                        break

                if not line_s:
                    continue

                # 3. Split data-rich lines on 2+ spaces
                parts = [p.strip() for p in re.split(r"\s{2,}", line_s) if p.strip()]
                lines.extend(parts)

    if not lines:
        return (
            "✅ Command executed successfully, but no objects matched or no "
            "diagnostic output was returned."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Manifest search & raw runner — used by composite tools
# ---------------------------------------------------------------------------
def search_objects_structured(
    search_string: str, object_type: str | None = None
) -> list[dict]:
    """Search the MQ manifest and return structured (qmgr, hostname, type, restricted) rows."""
    df = load_csv()
    if df.empty:
        return []

    search_cols = [
        c
        for c in ["qmgr", "hostname", "mqsc_command", "object_type"]
        if c in df.columns
    ]

    mask = pd.Series(False, index=df.index)
    for col in search_cols:
        mask |= df[col].astype(str).str.contains(
            re.escape(search_string), case=False, na=False
        )

    matches = df[mask]
    if matches.empty:
        return []

    inf_type = object_type
    if not inf_type:
        s_upper = search_string.upper()
        if s_upper.startswith("QL."):
            inf_type = "QLOCAL"
        elif s_upper.startswith("QA."):
            inf_type = "QALIAS"
        elif s_upper.startswith("QR."):
            inf_type = "QREMOTE"

    if inf_type:
        inf_upper = inf_type.upper()
        if inf_upper == "QUEUES":
            queue_types = ["QLOCAL", "QREMOTE", "QMODEL", "QALIAS"]
            matches = matches[matches["object_type"].str.upper().isin(queue_types)]
        else:
            matches = matches[matches["object_type"].str.upper() == inf_upper]

    if matches.empty:
        return []

    display = matches[["hostname", "qmgr", "object_type"]].drop_duplicates()
    results = []
    for _, r in display.iterrows():
        hostname = str(r["hostname"]).strip()
        allowed, _ = hostname_allowed(hostname)
        results.append(
            {
                "qmgr": str(r["qmgr"]).strip(),
                "hostname": hostname,
                "object_type": str(r["object_type"]).strip(),
                "restricted": not allowed,
            }
        )
    return results


async def run_mqsc_raw(
    qmgr_name: str, mqsc_command: str, target_hostname: str
) -> str:
    """Execute an MQSC command and return formatted output.

    Caller is responsible for hostname resolution and allow-list checks.
    """
    headers = {
        "Content-Type": "application/json",
        "ibm-mq-rest-csrf-token": CSRF_TOKEN,
    }
    data = json.dumps({"type": "runCommand", "parameters": {"command": mqsc_command}})
    url = build_url(target_hostname, f"action/qmgr/{qmgr_name}/mqsc")

    try:
        response = await mq_post(url, data=data, headers=headers, timeout=30.0)
        response.raise_for_status()
        return prettify_runmqsc(response.content)
    except Exception as err:
        return friendly_error(err, hostname=target_hostname)


# ---------------------------------------------------------------------------
# Startup connectivity check
# ---------------------------------------------------------------------------
async def verify_connectivity() -> None:
    """Ping the MQ REST API once at startup; log result. Never raises."""
    if not (MQ_URL_BASE and MQ_USER_NAME):
        return

    logger.debug("Verifying MQ REST connectivity to %s ...", MQ_URL_BASE)
    auth = httpx.BasicAuth(username=MQ_USER_NAME, password=MQ_PASSWORD)
    try:
        async with httpx.AsyncClient(verify=False, auth=auth) as client:
            response = await client.get(MQ_URL_BASE + "installation", timeout=5.0)
            if response.status_code == 200:
                logger.info("MQ REST API is responsive.")
            else:
                logger.warning(
                    "MQ REST API returned HTTP %d — check credentials.",
                    response.status_code,
                )
    except Exception as e:
        logger.error(
            "Cannot reach MQ REST API. Ensure 'dspmqweb' is running. Error: %s", e
        )
