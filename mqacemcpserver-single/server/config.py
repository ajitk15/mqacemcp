"""Centralised configuration loaded from .env at import time.

Same shape as the root `server/config.py`, with one difference: resource-file
paths default to the sibling repo's `resources/` directory (one level up from
this folder) and accept env-var overrides. That lets this self-contained
"single-call" build live in its own subfolder while still reading the same
CSV manifests the external extract jobs update.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_PATH: Path = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_PATH)

_bootstrap_logger = logging.getLogger("mqacemcpserver.config")


def _split_csv(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio").lower()
MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT: int = int(os.getenv("MCP_PORT", "8010"))

MCP_AUTH_USER: str = os.getenv("MCP_AUTH_USER", "")
MCP_AUTH_PASSWORD: str = os.getenv("MCP_AUTH_PASSWORD", "")


def _expand_path(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value.strip())) if value else ""


MCP_TLS_CERT: str = _expand_path(os.getenv("MCP_TLS_CERT", ""))
MCP_TLS_KEY: str = _expand_path(os.getenv("MCP_TLS_KEY", ""))


def tls_enabled() -> bool:
    """True when both cert and key paths are set (existence checked at boot)."""
    return bool(MCP_TLS_CERT and MCP_TLS_KEY)

LOG_LEVEL: str = os.getenv("MQACE_LOG_LEVEL", "INFO").upper()

_LOG_DIR_RAW = (os.getenv("LOG_DIR") or "").strip()
if _LOG_DIR_RAW:
    LOG_DIR: Path = Path(
        os.path.expandvars(os.path.expanduser(_LOG_DIR_RAW))
    ).resolve()
else:
    # Default to a sibling dir of this folder so query logs don't collide with
    # the granular-tools server. Override via LOG_DIR in .env.
    LOG_DIR = (PROJECT_ROOT.parent / "logs-single").resolve()

LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))
QUERY_LOG_ENABLED: bool = os.getenv("QUERY_LOG_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}

LOG_DIR.mkdir(parents=True, exist_ok=True)

MQ_URL_BASE: str = os.getenv("MQ_URL_BASE", "")
MQ_USER_NAME: str = os.getenv("MQ_USER_NAME", "")
MQ_PASSWORD: str = os.getenv("MQ_PASSWORD", "")

MQ_ALLOWED_HOSTNAME_PREFIXES: list[str] = _split_csv(
    os.getenv("MQ_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")
)

MQ_SUPPORT_TEAM: str = os.getenv("MQ_SUPPORT_TEAM", "MQ Infra Support")
MQ_ADMIN_GROUP: str = os.getenv("MQ_ADMIN_GROUP", "MQACE_ADMIN")

ACE_USER_NAME: str = os.getenv("ACE_USER_NAME", "")
ACE_PASSWORD: str = os.getenv("ACE_PASSWORD", "")

ACE_ALLOWED_HOSTNAME_PREFIXES: list[str] = _split_csv(
    os.getenv("ACE_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")
)

# Resource files (CSV manifests). Default to the sibling repo's resources/
# folder so the external extract jobs feed both servers from one location.
# Override individual paths in .env if the deployment splits them.
_DEFAULT_RESOURCES_DIR = (PROJECT_ROOT.parent / "resources").resolve()
RESOURCES_DIR: Path = Path(
    os.getenv("RESOURCES_DIR", str(_DEFAULT_RESOURCES_DIR))
).resolve()
MQ_QMGR_DUMP_PATH: Path = Path(
    os.getenv("MQ_QMGR_DUMP_PATH", str(RESOURCES_DIR / "qmgr_dump.csv"))
).resolve()
ACE_NODE_DUMP_PATH: Path = Path(
    os.getenv("ACE_NODE_DUMP_PATH", str(RESOURCES_DIR / "node_dump.csv"))
).resolve()
ACE_NODE_CONFIG_PATH: Path = Path(
    os.getenv("ACE_NODE_CONFIG_PATH", str(RESOURCES_DIR / "node_config.csv"))
).resolve()


def mq_configured() -> bool:
    """Return True when the MQ half has the minimum env to operate."""
    return bool(MQ_URL_BASE and MQ_USER_NAME)


def ace_configured() -> bool:
    """Return True when ACE node config is on disk (creds are optional)."""
    return ACE_NODE_CONFIG_PATH.exists()


if not mq_configured():
    _bootstrap_logger.warning(
        "MQ_URL_BASE or MQ_USER_NAME not set — IBM MQ tools will return "
        "errors when invoked."
    )

if not ace_configured():
    _bootstrap_logger.warning(
        "%s not found — IBM ACE tools will return errors when invoked.",
        ACE_NODE_CONFIG_PATH,
    )

if MCP_TRANSPORT == "sse" and not (MCP_AUTH_USER and MCP_AUTH_PASSWORD):
    _bootstrap_logger.warning(
        "SSE transport selected without MCP_AUTH_USER/MCP_AUTH_PASSWORD — "
        "the endpoint will be unauthenticated."
    )

if MCP_TRANSPORT == "sse":
    if tls_enabled():
        for label, path in (("MCP_TLS_CERT", MCP_TLS_CERT), ("MCP_TLS_KEY", MCP_TLS_KEY)):
            if not Path(path).is_file():
                _bootstrap_logger.warning(
                    "%s=%s does not exist — server will fail to start with TLS.",
                    label, path,
                )
    elif MCP_TLS_CERT or MCP_TLS_KEY:
        _bootstrap_logger.warning(
            "MCP_TLS_CERT and MCP_TLS_KEY must BOTH be set to enable HTTPS; "
            "falling back to plain HTTP."
        )
    else:
        _bootstrap_logger.warning(
            "SSE transport without TLS — set MCP_TLS_CERT and MCP_TLS_KEY in "
            ".env to enable HTTPS."
        )
