"""Shared safety primitives: hostname allow-list and read-only MQSC guard."""
from __future__ import annotations

from server.config import MQ_ADMIN_GROUP, MQ_SUPPORT_TEAM

# MQSC verbs that modify configuration — blocked in read-only mode.
_MODIFY_VERBS = {
    "ALTER", "DEFINE", "DELETE", "CLEAR", "MOVE", "SET",
    "RESET", "START", "STOP", "PURGE", "REFRESH", "RESOLVE",
    "ARCHIVE", "BACKUP",
}

MODIFY_BLOCKED_MSG = (
    "🚫 **Modification requests are not permitted through this tool.**\n\n"
    "This MCP server is configured for **read-only diagnostics only** and cannot "
    "execute commands that alter, create, or delete MQ objects.\n\n"
    "To make configuration changes, please:\n"
    f"  1. 📧 Reach out to the **{MQ_SUPPORT_TEAM}** team, or\n"
    f"  2. 🎫 Raise a ticket from **ServiceNow** → go/gen → assign to group **{MQ_ADMIN_GROUP}**\n\n"
    "They will be happy to assist you with the requested change."
)


def is_modification_command(mqsc_command: str) -> bool:
    """Return True if the MQSC command would mutate queue-manager configuration."""
    stripped = mqsc_command.strip()
    if not stripped:
        return False
    first_word = stripped.split()[0].upper()
    return first_word in _MODIFY_VERBS


def is_hostname_allowed(
    hostname: str, allowed_prefixes: list[str]
) -> tuple[bool, str]:
    """Check whether a hostname is permitted by the allow-list.

    Returns (True, "") when the hostname starts with any allowed prefix
    (case-insensitive), otherwise (False, friendly_message).
    """
    hostname_lower = hostname.lower().strip()
    for prefix in allowed_prefixes:
        if hostname_lower.startswith(prefix.lower()):
            return True, ""

    allowed_list = ", ".join(allowed_prefixes) if allowed_prefixes else "<none>"
    message = (
        f"🚫 Access to this system is restricted for safety. "
        f"Hostname '{hostname}' is not in the allowed list ({allowed_list}).\n\n"
    )
    return False, message
