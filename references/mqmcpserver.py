import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pandas as pd
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Environment variables and Logging
# ---------------------------------------------------------------------------
# Try to find .env in cwd or one level up (handles different launch paths)
env_path = os.path.join(os.getcwd(), ".env")
if not os.path.exists(env_path):
    env_path = os.path.join(os.path.dirname(os.getcwd()), ".env")

load_dotenv(dotenv_path=env_path)

# ---------------------------------------------------------------------------
# Configure Logging Setup
# ---------------------------------------------------------------------------
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.logger import get_mcp_logger
logger = get_mcp_logger()

logger.debug("Loading .env from %s", env_path)

URL_BASE  = os.getenv("MQ_URL_BASE")
USER_NAME = os.getenv("MQ_USER_NAME")
PASSWORD  = os.getenv("MQ_PASSWORD")

logger.debug("Target MQ URL:  %s", URL_BASE)
logger.debug("Target MQ User: %s", USER_NAME)

if not URL_BASE or not USER_NAME:
    logger.error("CRITICAL: MQ_URL_BASE or MQ_USER_NAME is not set in .env")

# Allowed hostname prefixes for safety (excludes production)
ALLOWED_PREFIXES_STR = os.getenv("MQ_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")
ALLOWED_HOSTNAME_PREFIXES = [p.strip() for p in ALLOWED_PREFIXES_STR.split(",")]

# Standard CSRF token value accepted by IBM MQ REST API (any non-empty value works)
_CSRF_TOKEN = "token"

# MQSC verbs that modify configuration — these are blocked in read-only mode
_MODIFY_VERBS = {
    "ALTER", "DEFINE", "DELETE", "CLEAR", "MOVE", "SET",
    "RESET", "START", "STOP", "PURGE", "REFRESH", "RESOLVE",
    "ARCHIVE", "BACKUP",
}

# Support contact details for modification requests (loaded from .env)
MQ_SUPPORT_TEAM = os.getenv("MQ_SUPPORT_TEAM", "")
MQ_ADMIN_GROUP  = os.getenv("MQ_ADMIN_GROUP", "")

# ---------------------------------------------------------------------------
# HTTP Client (Shared)
# ---------------------------------------------------------------------------
_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Return a shared HTTP client to reuse TLS handshakes across tool calls."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        auth = httpx.BasicAuth(username=USER_NAME, password=PASSWORD)
        _HTTP_CLIENT = httpx.AsyncClient(verify=False, auth=auth, timeout=30.0)
    return _HTTP_CLIENT


def _is_modification_command(mqsc_command: str) -> bool:
    """Return True if the MQSC command would modify queue-manager configuration."""
    first_word = mqsc_command.strip().split()[0].upper() if mqsc_command.strip() else ""
    return first_word in _MODIFY_VERBS


_MODIFY_BLOCKED_MSG = (
    "🚫 **Modification requests are not permitted through this tool.**\n\n"
    "This MCP server is configured for **read-only diagnostics only** and cannot "
    "execute commands that alter, create, or delete MQ objects.\n\n"
    "To make configuration changes, please:\n"
    f"  1. 📧 Reach out to the **{MQ_SUPPORT_TEAM}** team, or\n"
    f"  2. 🎫 Raise a ticket from **ServiceNow** → go/gen → assign to group **{MQ_ADMIN_GROUP}**\n\n"
    "They will be happy to assist you with the requested change."
)

# MCP endpoint authentication (optional — only applies to SSE transport)
MCP_AUTH_USER = os.getenv("MCP_AUTH_USER", "")
MCP_AUTH_PASSWORD = os.getenv("MCP_AUTH_PASSWORD", "")

# ---------------------------------------------------------------------------
# CSV helpers — cached at module level so disk is only read once per startup
# ---------------------------------------------------------------------------
CSV_PATH = Path(project_root) / "resources" / "qmgr_dump.csv"

_CSV_CACHE: pd.DataFrame | None = None


def _load_csv_from_disk() -> pd.DataFrame:
    """Read and parse the qmgr_dump CSV from disk."""
    if not CSV_PATH.exists():
        logger.warning("CSV file not found at %s", CSV_PATH)
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            CSV_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=0,
        )
        # Strip whitespace from all string columns and column names
        df.columns = [c.strip() for c in df.columns]
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)

        df = df.rename(columns={
            "qmname":     "qmgr",
            "objecttype": "object_type",
            "objectdef":  "mqsc_command",
        })
        if "extractedat" in df.columns:
            df["extractedat"] = pd.to_datetime(df["extractedat"], errors="coerce")
        logger.info("CSV loaded successfully: %d rows, %d columns", len(df), len(df.columns))
        logger.debug("Columns: %s", list(df.columns))
        return df
    except Exception:
        logger.exception("ERROR loading CSV")
        return pd.DataFrame()


def load_csv() -> pd.DataFrame:
    """Return the cached CSV dataframe, loading from disk on first call."""
    global _CSV_CACHE
    if _CSV_CACHE is None:
        _CSV_CACHE = _load_csv_from_disk()
    return _CSV_CACHE


# ---------------------------------------------------------------------------
# Hostname allow-list guard
# ---------------------------------------------------------------------------
def is_hostname_allowed(hostname: str) -> tuple[bool, str]:
    """
    Check if a hostname is allowed based on prefix filtering.
    Returns (True, "") if allowed, or (False, "reason message") if blocked.
    """
    hostname_lower = hostname.lower().strip()
    for prefix in ALLOWED_HOSTNAME_PREFIXES:
        if hostname_lower.startswith(prefix.lower()):
            return True, ""

    allowed_list = ", ".join(ALLOWED_HOSTNAME_PREFIXES)
    message = (
        f"🚫 Access to this system is restricted for safety. "
        f"Hostname '{hostname}' is not in the allowed list ({allowed_list}).\n\n"
    )
    return False, message


def _build_url(target_hostname: str, path: str) -> str:
    """Replace the hostname in URL_BASE with target_hostname and append path."""
    parsed = urlparse(URL_BASE)
    new_netloc = f"{target_hostname}:{parsed.port}" if parsed.port else target_hostname
    return parsed._replace(netloc=new_netloc).geturl() + path


def _friendly_error(err: Exception, hostname: str = "") -> str:
    """Convert raw HTTP / connection exceptions into user-friendly messages."""
    err_str = str(err)
    host_label = f" on '{hostname}'" if hostname else ""

    # httpx.HTTPStatusError carries the status code
    if hasattr(err, "response") and hasattr(err.response, "status_code"):
        code = err.response.status_code
        if code == 503:
            return (
                f"⚠️ MQ is not available{host_label}. "
                f"Please report this issue to MqAceInfra Support team "
                f"or try after some time."
            )
        if code == 401:
            return (
                f"🔒 Authentication failed{host_label}. "
                f"Check MQ_USER_NAME and MQ_PASSWORD in your .env file."
            )
        if code == 403:
            return (
                f"🔒 Access denied{host_label}. "
                f"The configured user does not have permission for this operation."
            )
        if code == 404:
            return (
                f"⚠️ Endpoint not found{host_label}. "
                f"The queue manager or REST endpoint may not exist."
            )
        return f"⚠️ HTTP {code} error{host_label}. Server returned: {err.response.reason_phrase}"

    # Connection-level errors
    err_lower = err_str.lower()
    if "connect" in err_lower and ("refused" in err_lower or "error" in err_lower):
        return (
            f"⚠️ Cannot connect to MQ REST API{host_label}. "
            f"The host may be offline or the mqweb server is not running."
        )
    if "timeout" in err_lower:
        return (
            f"⏱️ Connection timed out{host_label}. "
            f"The host may be slow or unreachable."
        )
    if "ssl" in err_lower or "certificate" in err_lower:
        return (
            f"🔐 SSL/TLS error{host_label}. "
            f"There may be a certificate issue with the MQ REST API."
        )

    # Fallback
    return f"⚠️ Connection error{host_label}: {err_str}"


# ---------------------------------------------------------------------------
# Basic Authentication middleware for SSE transport
# ---------------------------------------------------------------------------


class BasicAuthMiddleware:
    """ASGI middleware that enforces HTTP Basic Authentication."""

    def __init__(self, app, username: str, password: str):
        self.app = app
        self.username = username
        self.password = password

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()

            if not self._check_auth(auth_header):
                await self._send_401(send)
                return

        await self.app(scope, receive, send)

    def _check_auth(self, auth_header: str) -> bool:
        if not auth_header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            user, pwd = decoded.split(":", 1)
            return user == self.username and pwd == self.password
        except Exception:
            return False

    @staticmethod
    async def _send_401(send):
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"www-authenticate", b'Basic realm="MCP Server"'],
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Unauthorized",
            }
        )


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp_host = os.getenv("MQ_MCP_HOST", "0.0.0.0")
mcp_port = int(os.getenv("MQ_MCP_PORT", 8000))
mcp = FastMCP("mqmcpserver", host=mcp_host, port=mcp_port)


@mcp.tool()
def find_mq_object(search_string: str, object_type: str | None = None) -> str:
    """
    Search QM dump for a string to find which QM hosts an object.
    Returns the queue manager name(s), hostname(s), and object type.
    
    TIP: For end-to-end workflows, prefer the composite tools instead:
    - run_mqsc_for_object: auto-searches then runs any MQSC command
    - get_queue_depth: auto-searches, resolves aliases, returns depth
    - get_channel_status: auto-searches then returns channel status
    
    Args:
        search_string: String to search (e.g., queue name)
        object_type: Optional filter (e.g., 'QLOCAL', 'QUEUES', 'CHANNEL')
    """
    results = _search_objects_structured(search_string, object_type)

    if not results:
        # Check if the object exists at all (without type filter) to give a better message
        if object_type and _search_objects_structured(search_string):
            return f"❌ '{search_string}' exists but is not of type '{object_type}'."
        return f"❌ '{search_string}' not found in the manifest."

    accessible = [r for r in results if not r["restricted"]]
    restricted = [r for r in results if r["restricted"]]

    if not accessible:
        return f"🚫 '{search_string}' was found, but only on restricted/production systems. I do not have access to these."

    # Format output for allowed matches
    output_lines = [
        f"QM:{r['qmgr']} Host:{r['hostname']} Type:{r['object_type']}"
        for r in accessible
    ]

    # Include restricted matches with a clear tag
    for r in restricted:
        output_lines.append(
            f"QM:{r['qmgr']} [RESTRICTED: {r['hostname']}] Type:{r['object_type']}"
        )

    return "\n".join(output_lines)


@mcp.tool()
async def dspmq(qmgr_name: str | None = None) -> str:
    """List all IBM MQ queue managers and their status.
    
    Args:
        qmgr_name: Optional queue manager name to list all QMs running on its host.
    """
    headers = {
        "Content-Type": "application/json",
        "ibm-mq-rest-csrf-token": _CSRF_TOKEN,
    }
    
    target_hostname = ""
    url = URL_BASE + "qmgr/"
    if qmgr_name:
        df = load_csv()
        qmgr_matches = df[df["qmgr"].str.upper() == qmgr_name.upper()]
        if not qmgr_matches.empty:
            target_hostname = str(qmgr_matches.iloc[0]["hostname"]).strip()
            allowed, message = is_hostname_allowed(target_hostname)
            if not allowed:
                return message
            url = _build_url(target_hostname, "qmgr/")
        else:
            return f"❌ Queue Manager '{qmgr_name}' not found in the manifest."

    client = get_http_client()
    try:
        response = await client.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
        return prettify_dspmq(response.content)
    except Exception as err:
        return _friendly_error(err, hostname=target_hostname)


def prettify_dspmq(payload: bytes) -> str:
    json_output = json.loads(payload.decode("utf-8"))
    lines = []
    for x in json_output["qmgr"]:
        lines.append(f"name={x['name']}, state={x['state']}")
    return "\n".join(lines)


@mcp.tool()
async def dspmqver(qmgr_name: str | None = None) -> str:
    """Display IBM MQ version and installation information.

    Args:
        qmgr_name: Optional queue manager name to check the specific host version for.
    """
    headers = {
        "Content-Type": "application/json",
        "ibm-mq-rest-csrf-token": _CSRF_TOKEN,
    }
    
    # Establish target URL
    target_hostname = ""
    url = URL_BASE + "installation"
    if qmgr_name:
        df = load_csv()
        qmgr_matches = df[df["qmgr"].str.upper() == qmgr_name.upper()]
        if not qmgr_matches.empty:
            target_hostname = str(qmgr_matches.iloc[0]["hostname"]).strip()
            allowed, message = is_hostname_allowed(target_hostname)
            if not allowed:
                return message
            url = _build_url(target_hostname, "installation")
        else:
            return f"❌ Queue Manager '{qmgr_name}' not found in the manifest."

    client = get_http_client()
    try:
        response = await client.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
        return prettify_dspmqver(response.content)
    except Exception as err:
        return _friendly_error(err, hostname=target_hostname)


def prettify_dspmqver(payload: bytes) -> str:
    json_output = json.loads(payload.decode("utf-8"))
    lines = ["\n---"]
    for x in json_output["installation"]:
        lines.append(
            f"Name: {x.get('name', 'N/A')}\n"
            f"Version: {x.get('version', 'N/A')}\n"
            f"Architecture: {x.get('architecture', 'N/A')}\n"
            f"Installation Path: {x.get('installationPath', 'N/A')}\n---"
        )
    return "\n".join(lines)


@mcp.tool()
async def runmqsc(qmgr_name: str, mqsc_command: str, hostname: str | None = None) -> str:
    """Run a read-only MQSC command against a specific queue manager.
    You MUST know the queue manager name before calling this tool.
    If you only know the object name (queue/channel), use run_mqsc_for_object instead.
    The hostname is auto-resolved from the manifest if not provided.

    NOTE: Only DISPLAY commands are allowed. Modification commands
    (ALTER, DEFINE, DELETE, etc.) are blocked — users will be directed
    to the InfraSupport / MQACE_ADMIN team.

    Args:
        qmgr_name:    The queue manager name (NOT a queue or channel name)
        mqsc_command: An MQSC command to run on the queue manager
        hostname:     Optional: Host from find_mq_object (auto-resolved if omitted)
    """
    if _is_modification_command(mqsc_command):
        logger.warning(
            "Blocked modification command: %s (qmgr=%s)",
            mqsc_command, qmgr_name,
        )
        return _MODIFY_BLOCKED_MSG
    headers = {
        "Content-Type": "application/json",
        "ibm-mq-rest-csrf-token": _CSRF_TOKEN,
    }

    # Hostname resolution logic
    df = load_csv()
    target_hostname = qmgr_name  # Default fallback (restored to QM name)
    
    if hostname:
        # Use provided hostname directly
        target_hostname = hostname.strip()
        logger.debug("Using explicit hostname from tool call: %s", target_hostname)
        allowed, message = is_hostname_allowed(target_hostname)
        if not allowed:
            return message
    elif not df.empty:
        # Fallback to manifest lookup
        qmgr_matches = df[df["qmgr"].str.upper() == qmgr_name.upper()]
        if not qmgr_matches.empty:
            target_hostname = str(qmgr_matches.iloc[0]["hostname"]).strip()
            allowed, message = is_hostname_allowed(target_hostname)
            if not allowed:
                return message
        else:
            logger.warning("QM %s not found in manifest, using as hostname", qmgr_name)

    # Use json.dumps to safely serialise the command
    data = json.dumps({"type": "runCommand", "parameters": {"command": mqsc_command}})

    url = _build_url(target_hostname, "action/qmgr/" + qmgr_name + "/mqsc")

    client = get_http_client()
    try:
        response = await client.post(url, data=data, headers=headers, timeout=30.0)
        response.raise_for_status()
        return prettify_runmqsc(response.content)
    except Exception as err:
        return _friendly_error(err, hostname=target_hostname)


def prettify_runmqsc(payload: bytes) -> str:
    """Format MQSC command response for both z/OS and distributed queue managers."""
    json_output = json.loads(payload.decode("utf-8"))
    lines = []
    
    # Headers to strip from the beginning of lines
    STRIP_HEADERS = [
        "AMQ8409I: Display Queue details.",
        "AMQ8450I: Display Channel details.",
        "AMQ8420I: Display Queue Manager details."
    ]

    for x in json_output.get("commandResponse", []):
        text_list = x.get("text", [])
        # z/OS responses start with CSQN205I
        if text_list and text_list[0].startswith("CSQN205I"):
            text_list.pop(0)
            if text_list: text_list.pop()
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
                
                # 2. Strip known headers from start of line
                for h in STRIP_HEADERS:
                    if line_s.startswith(h):
                        line_s = line_s[len(h):].strip()
                        break
                
                if not line_s:
                    continue
                
                # 3. Handle data-rich lines: Split multi-attribute lines (separated by 2+ spaces)
                # This also fixes the "one long line" problem on some platforms
                parts = [p.strip() for p in re.split(r'\s{2,}', line_s) if p.strip()]
                lines.extend(parts)
    
    if not lines:
        return "✅ Command executed successfully, but no objects matched or no diagnostic output was returned."
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers for composite tools
# ---------------------------------------------------------------------------


def _search_objects_structured(
    search_string: str, object_type: str | None = None
) -> list[dict]:
    """
    Internal helper: search CSV and return structured results.
    Returns list of dicts with keys: qmgr, hostname, object_type, restricted.
    """
    df = load_csv()
    if df.empty:
        return []

    # Restrict search to relevant columns and use vectorized str.contains
    # This is significantly faster than using .apply() across all columns
    search_cols = [c for c in ["qmgr", "hostname", "mqsc_command", "object_type"] if c in df.columns]
    
    mask = pd.Series(False, index=df.index)
    for col in search_cols:
        mask |= df[col].astype(str).str.contains(
            re.escape(search_string), case=False, na=False
        )
        
    matches = df[mask]

    if matches.empty:
        return []

    # Infer object type from naming convention if not provided
    inf_type = object_type
    if not inf_type:
        s_upper = search_string.upper()
        if s_upper.startswith("QL."):
            inf_type = "QLOCAL"
        elif s_upper.startswith("QA."):
            inf_type = "QALIAS"
        elif s_upper.startswith("QR."):
            inf_type = "QREMOTE"

    # Apply type filter
    if inf_type:
        inf_upper = inf_type.upper()
        if inf_upper == "QUEUES":
            queue_types = ["QLOCAL", "QREMOTE", "QMODEL", "QALIAS"]
            matches = matches[
                matches["object_type"].str.upper().isin(queue_types)
            ]
        else:
            matches = matches[
                matches["object_type"].str.upper() == inf_upper
            ]

    if matches.empty:
        return []

    # Deduplicate and build structured result
    display = matches[["hostname", "qmgr", "object_type"]].drop_duplicates()
    results = []
    for _, r in display.iterrows():
        hostname = str(r["hostname"]).strip()
        allowed, _ = is_hostname_allowed(hostname)
        results.append(
            {
                "qmgr": str(r["qmgr"]).strip(),
                "hostname": hostname,
                "object_type": str(r["object_type"]).strip(),
                "restricted": not allowed,
            }
        )

    return results


async def _run_mqsc_raw(
    qmgr_name: str, mqsc_command: str, target_hostname: str
) -> str:
    """
    Internal helper: execute an MQSC command and return formatted output.
    Caller is responsible for hostname resolution and allow-list checks.
    """
    headers = {
        "Content-Type": "application/json",
        "ibm-mq-rest-csrf-token": _CSRF_TOKEN,
    }

    data = json.dumps(
        {"type": "runCommand", "parameters": {"command": mqsc_command}}
    )

    url = _build_url(target_hostname, "action/qmgr/" + qmgr_name + "/mqsc")

    client = get_http_client()
    try:
        response = await client.post(
            url, data=data, headers=headers, timeout=30.0
        )
        response.raise_for_status()
        return prettify_runmqsc(response.content)
    except Exception as err:
        return _friendly_error(err, hostname=target_hostname)


# ---------------------------------------------------------------------------
# Composite MCP tools — workflow-aware, self-sufficient
# ---------------------------------------------------------------------------


@mcp.tool()
async def run_mqsc_for_object(
    object_name: str, mqsc_command: str, object_type: str | None = None
) -> str:
    """Search for an MQ object and run a read-only MQSC command on ALL queue managers that host it.

    This tool automatically discovers which queue managers host the object by
    searching the manifest first, then executes the MQSC command on each
    accessible queue manager and returns the consolidated results.

    NOTE: Only DISPLAY commands are allowed. Modification commands
    (ALTER, DEFINE, DELETE, etc.) are blocked — users will be directed
    to the InfraSupport / MQACE_ADMIN team.

    Args:
        object_name:  Name of the MQ object (queue, channel, etc.)
        mqsc_command: The MQSC command to execute
                      (e.g., 'DISPLAY QLOCAL(QL.IN.APP1) CURDEPTH')
        object_type:  Optional type filter (e.g., 'QLOCAL', 'CHANNEL', 'QUEUES')
    """
    if _is_modification_command(mqsc_command):
        logger.warning(
            "Blocked modification command: %s (object=%s)",
            mqsc_command, object_name,
        )
        return _MODIFY_BLOCKED_MSG

    logger.info(
        "run_mqsc_for_object called",
        extra={"context": {"object": object_name, "command": mqsc_command}},
    )
    results = _search_objects_structured(object_name, object_type)

    if not results:
        return f"❌ '{object_name}' not found in the manifest."

    accessible = [r for r in results if not r["restricted"]]
    restricted = [r for r in results if r["restricted"]]

    if not accessible:
        return (
            f"🚫 '{object_name}' was found, but only on "
            f"restricted/production systems. I do not have access to these."
        )

    output_lines = [
        f"🔍 Found '{object_name}' on {len(accessible)} "
        f"accessible queue manager(s).\n"
    ]

    for entry in accessible:
        qm = entry["qmgr"]
        host = entry["hostname"]
        output_lines.append(f"--- {qm} ({host}) ---")
        result = await _run_mqsc_raw(qm, mqsc_command, host)
        output_lines.append(result)
        output_lines.append("")

    if restricted:
        restricted_qms = ", ".join(
            f"{r['qmgr']} ({r['hostname']})" for r in restricted
        )
        output_lines.append(
            f"🚫 Also found on restricted systems (not queried): {restricted_qms}"
        )

    return "\n".join(output_lines)


@mcp.tool()
async def get_queue_depth(queue_name: str) -> str:
    """Get the current depth of a queue across all queue managers that host it.

    Automatically discovers the hosting queue manager(s), resolves alias
    queues (QA*) to their target local queues, and returns the actual depth.

    Args:
        queue_name: Name of the queue (e.g., 'QL.IN.APP1' or 'QA.IN.APP1')
    """
    logger.info(
        "get_queue_depth called",
        extra={"context": {"queue": queue_name}},
    )
    results = _search_objects_structured(queue_name)

    if not results:
        return f"❌ '{queue_name}' not found in the manifest."

    accessible = [r for r in results if not r["restricted"]]
    restricted = [r for r in results if r["restricted"]]

    if not accessible:
        return (
            f"🚫 '{queue_name}' was found, but only on "
            f"restricted/production systems. I do not have access to these."
        )

    output_lines = []
    is_alias = queue_name.upper().startswith("QA.") or any(
        r["object_type"].upper() == "QALIAS" for r in accessible
    )

    for entry in accessible:
        qm = entry["qmgr"]
        host = entry["hostname"]

        if is_alias:
            # Step 1: Resolve alias to its target queue
            alias_result = await _run_mqsc_raw(
                qm, f"DISPLAY QALIAS({queue_name})", host
            )
            output_lines.append(f"--- {qm} ({host}) [Alias Resolution] ---")
            output_lines.append(alias_result)

            # Extract TARGET from the output
            target = None
            for line in alias_result.split("\n"):
                if "TARGET(" in line.upper():
                    match = re.search(
                        r"TARGET\(([^)]+)\)", line, re.IGNORECASE
                    )
                    if match:
                        target = match.group(1).strip()

            if target:
                # Step 2: Get depth of the resolved target queue
                depth_result = await _run_mqsc_raw(
                    qm, f"DISPLAY QLOCAL({target}) CURDEPTH", host
                )
                output_lines.append(
                    f"\n--- {qm} ({host}) [Target: {target} Depth] ---"
                )
                output_lines.append(depth_result)
            else:
                output_lines.append(
                    f"⚠️  Could not resolve TARGET for alias "
                    f"{queue_name} on {qm}"
                )
        else:
            # Direct local queue — get depth
            depth_result = await _run_mqsc_raw(
                qm, f"DISPLAY QLOCAL({queue_name}) CURDEPTH", host
            )
            output_lines.append(f"--- {qm} ({host}) ---")
            output_lines.append(depth_result)

        output_lines.append("")

    if restricted:
        restricted_qms = ", ".join(
            f"{r['qmgr']} ({r['hostname']})" for r in restricted
        )
        output_lines.append(
            f"🚫 Also found on restricted systems (not queried): {restricted_qms}"
        )

    return "\n".join(output_lines)


@mcp.tool()
async def get_channel_status(channel_name: str) -> str:
    """Get the status of an MQ channel across all queue managers that host it.

    Automatically discovers which queue managers host the channel and returns
    the channel status from each.

    Args:
        channel_name: Name of the channel
    """
    logger.info(
        "get_channel_status called",
        extra={"context": {"channel": channel_name}},
    )
    # Try with CHANNEL type filter first
    results = _search_objects_structured(channel_name, "CHANNEL")

    if not results:
        # Fallback: search without type filter
        results = _search_objects_structured(channel_name)

    if not results:
        return f"❌ '{channel_name}' not found in the manifest."

    accessible = [r for r in results if not r["restricted"]]
    restricted = [r for r in results if r["restricted"]]

    if not accessible:
        return (
            f"🚫 '{channel_name}' was found, but only on "
            f"restricted/production systems. I do not have access to these."
        )

    output_lines = [
        f"🔍 Channel '{channel_name}' found on {len(accessible)} "
        f"accessible queue manager(s).\n"
    ]

    for entry in accessible:
        qm = entry["qmgr"]
        host = entry["hostname"]

        status_result = await _run_mqsc_raw(
            qm, f"DISPLAY CHSTATUS({channel_name}) ALL", host
        )
        output_lines.append(f"--- {qm} ({host}) ---")
        output_lines.append(status_result)
        output_lines.append("")

    if restricted:
        restricted_qms = ", ".join(
            f"{r['qmgr']} ({r['hostname']})" for r in restricted
        )
        output_lines.append(
            f"🚫 Also found on restricted systems (not queried): {restricted_qms}"
        )

    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# Startup connectivity check
# ---------------------------------------------------------------------------
async def verify_connectivity():
    """Verify that the MQ REST API is reachable before starting the server."""
    if not URL_BASE or not USER_NAME:
        return

    logger.debug("Verifying connectivity to %s ...", URL_BASE)
    auth = httpx.BasicAuth(username=USER_NAME, password=PASSWORD)
    async with httpx.AsyncClient(verify=False, auth=auth) as client:
        try:
            response = await client.get(URL_BASE + "installation", timeout=5.0)
            if response.status_code == 200:
                logger.info("SUCCESS: MQ REST API is responsive.")
            else:
                logger.warning(
                    "MQ REST API returned status %d — check .env credentials.",
                    response.status_code,
                )
        except Exception as e:
            logger.error(
                "CRITICAL: Cannot reach MQ REST API. Ensure 'dspmqweb' is running.\n  Error: %s",
                e,
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("MQ_MCP_TRANSPORT", "stdio")
    logger.debug("Starting MCP Server with transport=%s", transport)
    asyncio.run(verify_connectivity())

    if transport == "sse" and MCP_AUTH_USER and MCP_AUTH_PASSWORD:
        # Wrap the SSE app with Basic Auth middleware
        import uvicorn

        app = mcp.sse_app()
        app = BasicAuthMiddleware(app, MCP_AUTH_USER, MCP_AUTH_PASSWORD)
        logger.info(
            "Starting SSE server with Basic Authentication (user: %s)",
            MCP_AUTH_USER,
        )
        uvicorn.run(app, host=mcp_host, port=mcp_port)
    else:
        if transport == "sse" and not (MCP_AUTH_USER and MCP_AUTH_PASSWORD):
            logger.warning(
                "SSE server starting WITHOUT authentication. "
                "Set MCP_AUTH_USER and MCP_AUTH_PASSWORD in .env to enable it."
            )
        mcp.run(transport=transport)