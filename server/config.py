"""Centralised configuration loaded from .env at import time.

Exposes module-level constants used across the MQ and ACE halves of the
unified MCP server. Missing credentials log a warning rather than raising,
so an operator who only configures one half (MQ or ACE) still gets a
working server for the configured side.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# .env discovery: project root (the parent of the `server/` package)
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_PATH: Path = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_PATH)

# A bootstrap logger — server.logger uses MQACE_LOG_LEVEL set below, but we
# need to surface config issues before that module is configured.
_bootstrap_logger = logging.getLogger("mqacemcpserver.config")


def _split_csv(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


# ---------------------------------------------------------------------------
# MCP transport / bind / auth
# ---------------------------------------------------------------------------
MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio").lower()
MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT: int = int(os.getenv("MCP_PORT", "8000"))

MCP_AUTH_USER: str = os.getenv("MCP_AUTH_USER", "")
MCP_AUTH_PASSWORD: str = os.getenv("MCP_AUTH_PASSWORD", "")

LOG_LEVEL: str = os.getenv("MQACE_LOG_LEVEL", "INFO").upper()

# Logging — file output, rotation, retention, and per-call query log toggle.
# LOG_DIR honours .env. Empty / unset falls back to <project_root>/logs.
# Supports ~ and $VAR expansion for operator convenience.
_LOG_DIR_RAW = (os.getenv("LOG_DIR") or "").strip()
if _LOG_DIR_RAW:
    LOG_DIR: Path = Path(
        os.path.expandvars(os.path.expanduser(_LOG_DIR_RAW))
    ).resolve()
else:
    LOG_DIR = (PROJECT_ROOT / "logs").resolve()

LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))
QUERY_LOG_ENABLED: bool = os.getenv("QUERY_LOG_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on",
}

# Ensure the log directory exists at import time so logger setup can open files.
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# IBM MQ
# ---------------------------------------------------------------------------
MQ_URL_BASE: str = os.getenv("MQ_URL_BASE", "")
MQ_USER_NAME: str = os.getenv("MQ_USER_NAME", "")
MQ_PASSWORD: str = os.getenv("MQ_PASSWORD", "")

MQ_ALLOWED_HOSTNAME_PREFIXES: list[str] = _split_csv(
    os.getenv("MQ_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")
)

MQ_SUPPORT_TEAM: str = os.getenv("MQ_SUPPORT_TEAM", "MQ Infra Support")
MQ_ADMIN_GROUP: str = os.getenv("MQ_ADMIN_GROUP", "MQACE_ADMIN")

# ---------------------------------------------------------------------------
# IBM ACE
# ---------------------------------------------------------------------------
ACE_USER_NAME: str = os.getenv("ACE_USER_NAME", "")
ACE_PASSWORD: str = os.getenv("ACE_PASSWORD", "")

ACE_ALLOWED_HOSTNAME_PREFIXES: list[str] = _split_csv(
    os.getenv("ACE_ALLOWED_HOSTNAME_PREFIXES", "lod,loq,lot")
)

# ---------------------------------------------------------------------------
# Resource files (CSV manifests)
# ---------------------------------------------------------------------------
RESOURCES_DIR: Path = PROJECT_ROOT / "resources"
MQ_QMGR_DUMP_PATH: Path = RESOURCES_DIR / "qmgr_dump.csv"
ACE_NODE_DUMP_PATH: Path = RESOURCES_DIR / "node_dump.csv"
ACE_NODE_CONFIG_PATH: Path = RESOURCES_DIR / "node_config.csv"


def mq_configured() -> bool:
    """Return True when the MQ half has the minimum env to operate."""
    return bool(MQ_URL_BASE and MQ_USER_NAME)


def ace_configured() -> bool:
    """Return True when ACE node config is on disk (creds are optional)."""
    return ACE_NODE_CONFIG_PATH.exists()


# ---------------------------------------------------------------------------
# Boot-time visibility (warnings only — never crash on missing creds)
# ---------------------------------------------------------------------------
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
