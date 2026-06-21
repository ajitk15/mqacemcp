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
# .env / resource discovery
# ---------------------------------------------------------------------------
# PROJECT_ROOT is the parent of the `server/` package — i.e. this build's own
# folder (`mqacemcpserver/`). The build can be deployed two ways:
#   - standalone: ships its own `resources/` (and `.env`) next to the code.
#   - mono-repo:  shares the parent repo's root-level `resources/` and `.env`,
#                 so the daily extract job feeds every build from one place.
# Detect which once, and base default resource/log/.env locations on it.
# Explicit env overrides (RESOURCES_DIR, *_PATH, LOG_DIR) always win.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_STANDALONE: bool = (PROJECT_ROOT / "resources").is_dir()
_BASE_DIR: Path = PROJECT_ROOT if _STANDALONE else PROJECT_ROOT.parent

ENV_PATH: Path = _BASE_DIR / ".env"

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

# Optional TLS for the SSE endpoint. When both cert + key are set the server
# binds with HTTPS; otherwise it falls back to plain HTTP. Paths support ~ and
# $VAR expansion. Use unencrypted PEM-format files.
def _expand_path(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value.strip())) if value else ""


MCP_TLS_CERT: str = _expand_path(os.getenv("MCP_TLS_CERT", ""))
MCP_TLS_KEY: str = _expand_path(os.getenv("MCP_TLS_KEY", ""))


def tls_enabled() -> bool:
    """True when both cert and key paths are set (existence checked at boot)."""
    return bool(MCP_TLS_CERT and MCP_TLS_KEY)

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
    LOG_DIR = (_BASE_DIR / "logs").resolve()

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
# Default to the local resources/ for a standalone deploy, else the parent
# repo's resources/ so the external extract jobs feed every build from one
# location. Override individual paths in .env if the deployment splits them.
_DEFAULT_RESOURCES_DIR = (_BASE_DIR / "resources").resolve()
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
CERT_DUMP_PATH: Path = Path(
    os.getenv("CERT_DUMP_PATH", str(RESOURCES_DIR / "cert_dump.csv"))
).resolve()


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
