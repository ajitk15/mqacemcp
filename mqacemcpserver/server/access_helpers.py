"""Read-only identity and effective-access evaluation helpers.

The user-to-group relationship is resolved from LDAP at request time. MQ
authorization evidence comes from the existing qmgr_dump.csv AUTHREC/QMGR/
CHLAUTH rows. ACE authorization evidence comes from ace_auth_dump.csv, a
normalized extract produced by fleet automation.
"""
from __future__ import annotations

import fnmatch
import re
import ssl
from urllib.parse import urlparse

import pandas as pd

from server.config import (
    ACCESS_MAX_SNAPSHOT_AGE_HOURS,
    ACE_AUTH_DUMP_PATH,
    DOMAIN_LDAP_ALLOW_INSECURE,
    DOMAIN_LDAP_BASE_DN,
    DOMAIN_LDAP_BIND_DN,
    DOMAIN_LDAP_BIND_PASSWORD,
    DOMAIN_LDAP_CA_CERT_FILE,
    DOMAIN_LDAP_CANONICAL_ATTRIBUTE,
    DOMAIN_LDAP_GROUP_ATTRIBUTE,
    DOMAIN_LDAP_GROUP_FILTER,
    DOMAIN_LDAP_TIMEOUT_SECONDS,
    DOMAIN_LDAP_URI,
    DOMAIN_LDAP_USER_FILTER,
)
from server.csv_cache import CsvCache
from server.logger import get_logger
from server.mq_helpers import load_csv

logger = get_logger("mqacemcpserver.access")


def _load_ace_auth_from_disk() -> pd.DataFrame | None:
    if not ACE_AUTH_DUMP_PATH.exists():
        logger.warning("ACE authorization manifest not found at %s", ACE_AUTH_DUMP_PATH)
        return None
    try:
        df = pd.read_csv(
            ACE_AUTH_DUMP_PATH, delimiter="|", skipinitialspace=True, header=0
        )
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        if "extractedat" in df.columns:
            df["extractedat"] = pd.to_datetime(
                df["extractedat"], errors="coerce", utc=True
            )
        return df
    except Exception:
        logger.exception("ERROR loading ACE authorization manifest")
        return None


_ace_auth_cache = CsvCache(
    ACE_AUTH_DUMP_PATH,
    _load_ace_auth_from_disk,
    logger,
    "ACE authorization manifest",
)


def load_ace_auth_dump() -> pd.DataFrame:
    """Return the auto-reloading ACE authorization manifest."""
    return _ace_auth_cache.get()


def _short_name(value: str) -> str:
    value = (value or "").strip()
    if "\\" in value:
        value = value.rsplit("\\", 1)[-1]
    if "@" in value:
        value = value.split("@", 1)[0]
    return value


def _identity_aliases(values: list[str]) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        aliases.add(cleaned.casefold())
        short = _short_name(cleaned)
        if short:
            aliases.add(short.casefold())
    return aliases


def resolve_domain_groups(user_id: str) -> dict:
    """Resolve a user to direct and nested groups through read-only LDAP."""
    supplied = (user_id or "").strip()
    if not supplied:
        return {"status": "error", "message": "No user ID supplied."}
    if not (DOMAIN_LDAP_URI and DOMAIN_LDAP_BASE_DN):
        return {
            "status": "unavailable",
            "user_id": supplied,
            "message": (
                "Real-time domain lookup is not configured. Set DOMAIN_LDAP_URI "
                "and DOMAIN_LDAP_BASE_DN."
            ),
            "groups": [],
        }

    parsed = urlparse(DOMAIN_LDAP_URI)
    scheme = parsed.scheme.lower()
    if scheme not in {"ldap", "ldaps"} or not parsed.hostname:
        return {
            "status": "error",
            "user_id": supplied,
            "message": "DOMAIN_LDAP_URI must be an ldap:// or ldaps:// URL.",
            "groups": [],
        }
    if scheme == "ldap" and not DOMAIN_LDAP_ALLOW_INSECURE:
        return {
            "status": "error",
            "user_id": supplied,
            "message": (
                "Plain LDAP is disabled. Use ldaps:// or explicitly set "
                "DOMAIN_LDAP_ALLOW_INSECURE=true for a controlled environment."
            ),
            "groups": [],
        }
    if "{user}" not in DOMAIN_LDAP_USER_FILTER:
        return {
            "status": "error",
            "user_id": supplied,
            "message": "DOMAIN_LDAP_USER_FILTER must contain {user}.",
            "groups": [],
        }
    if "{user_dn}" not in DOMAIN_LDAP_GROUP_FILTER:
        return {
            "status": "error",
            "user_id": supplied,
            "message": "DOMAIN_LDAP_GROUP_FILTER must contain {user_dn}.",
            "groups": [],
        }

    try:
        from ldap3 import AUTO_BIND_NO_TLS, NONE, SUBTREE, Connection, Server, Tls
        from ldap3.utils.conv import escape_filter_chars
    except ImportError:
        return {
            "status": "error",
            "user_id": supplied,
            "message": "The ldap3 dependency is not installed.",
            "groups": [],
        }

    conn = None
    try:
        tls = None
        if scheme == "ldaps":
            tls = Tls(
                validate=ssl.CERT_REQUIRED,
                ca_certs_file=DOMAIN_LDAP_CA_CERT_FILE or None,
            )
        server = Server(
            parsed.hostname,
            port=parsed.port or (636 if scheme == "ldaps" else 389),
            use_ssl=scheme == "ldaps",
            tls=tls,
            get_info=NONE,
            connect_timeout=DOMAIN_LDAP_TIMEOUT_SECONDS,
        )
        conn = Connection(
            server,
            user=DOMAIN_LDAP_BIND_DN or None,
            password=DOMAIN_LDAP_BIND_PASSWORD or None,
            auto_bind=AUTO_BIND_NO_TLS,
            receive_timeout=DOMAIN_LDAP_TIMEOUT_SECONDS,
            raise_exceptions=True,
        )
        user_filter = DOMAIN_LDAP_USER_FILTER.format(
            user=escape_filter_chars(_short_name(supplied))
        )
        conn.search(
            DOMAIN_LDAP_BASE_DN,
            user_filter,
            search_scope=SUBTREE,
            attributes=[DOMAIN_LDAP_CANONICAL_ATTRIBUTE],
            size_limit=2,
        )
        if len(conn.entries) != 1:
            message = (
                "User was not found in the directory."
                if not conn.entries
                else "User lookup was ambiguous; use a canonical domain user ID."
            )
            return {
                "status": "not_found" if not conn.entries else "error",
                "user_id": supplied,
                "message": message,
                "groups": [],
            }

        user_entry = conn.entries[0]
        user_dn = user_entry.entry_dn
        canonical = supplied
        if DOMAIN_LDAP_CANONICAL_ATTRIBUTE in user_entry.entry_attributes:
            value = user_entry[DOMAIN_LDAP_CANONICAL_ATTRIBUTE].value
            if value:
                canonical = str(value)

        group_filter = DOMAIN_LDAP_GROUP_FILTER.format(
            user_dn=escape_filter_chars(user_dn)
        )
        conn.search(
            DOMAIN_LDAP_BASE_DN,
            group_filter,
            search_scope=SUBTREE,
            attributes=[DOMAIN_LDAP_GROUP_ATTRIBUTE],
            paged_size=1000,
        )
        groups = sorted(
            {
                str(entry[DOMAIN_LDAP_GROUP_ATTRIBUTE].value).strip()
                for entry in conn.entries
                if DOMAIN_LDAP_GROUP_ATTRIBUTE in entry.entry_attributes
                and entry[DOMAIN_LDAP_GROUP_ATTRIBUTE].value
            },
            key=str.casefold,
        )
        return {
            "status": "success",
            "user_id": supplied,
            "canonical_user": canonical,
            "groups": groups,
            "group_count": len(groups),
            "source": parsed.hostname,
        }
    except Exception as err:
        logger.warning(
            "Directory group lookup failed for %s: %s", supplied, type(err).__name__
        )
        return {
            "status": "error",
            "user_id": supplied,
            "message": f"Directory lookup failed ({type(err).__name__}).",
            "groups": [],
        }
    finally:
        if conn is not None:
            try:
                conn.unbind()
            except Exception:
                pass


_ATTR_PATTERNS: dict[str, re.Pattern] = {}


def _attr(command: str, name: str) -> str | None:
    pattern = _ATTR_PATTERNS.setdefault(
        name.upper(), re.compile(rf"\b{re.escape(name)}\(([^)]*)\)", re.IGNORECASE)
    )
    match = pattern.search(str(command or ""))
    if not match:
        return None
    value = match.group(1).strip().strip("'").strip()
    return value or None


def parse_authrec(command: str) -> dict | None:
    """Parse one SET AUTHREC row from dmpmqcfg output."""
    entity_match = re.search(
        r"\b(GROUP|PRINCIPAL)\('([^']+)'\)", str(command or ""), re.IGNORECASE
    )
    if not entity_match:
        return None
    auth_value = _attr(command, "AUTHADD") or ""
    return {
        "profile": _attr(command, "PROFILE") or "",
        "entity_type": entity_match.group(1).upper(),
        "entity": entity_match.group(2).strip(),
        "object_type": (_attr(command, "OBJTYPE") or "").upper(),
        "authorities": sorted(
            {a.strip().upper() for a in auth_value.split(",") if a.strip()}
        ),
    }


def _snapshot_metadata(rows: pd.DataFrame, max_age_hours: float) -> dict:
    if rows.empty or "extractedat" not in rows.columns:
        return {"stale": True}
    timestamps = pd.to_datetime(rows["extractedat"], errors="coerce", utc=True)
    if timestamps.dropna().empty:
        return {"stale": True}
    latest = timestamps.max()
    age = max(
        0.0,
        (pd.Timestamp.now(tz="UTC") - latest).total_seconds() / 3600,
    )
    # Age and extraction timestamps are deliberately kept internal.  Callers
    # only need the stale/fresh decision and must never expose snapshot age to
    # end users.
    return {"stale": age > max_age_hours}


def _profile_matches(profile: str, resource: str) -> bool:
    return fnmatch.fnmatchcase(resource.casefold(), profile.casefold())


def evaluate_mq_access(
    user_id: str,
    groups: list[str],
    qmgr_names: list[str],
    *,
    canonical_user: str | None = None,
    identity_resolved: bool = True,
    channel: str | None = None,
    resource: str | None = None,
    dataframe: pd.DataFrame | None = None,
    max_age_hours: float = ACCESS_MAX_SNAPSHOT_AGE_HOURS,
) -> list[dict]:
    """Evaluate MQ OAM evidence for one user across queue managers."""
    df = load_csv() if dataframe is None else dataframe
    user_aliases = _identity_aliases([user_id, canonical_user or ""])
    group_aliases = _identity_aliases(groups)
    results: list[dict] = []

    for requested_qm in qmgr_names:
        qm = requested_qm.strip()
        if df.empty or "qmgr" not in df.columns:
            results.append(
                {
                    "qmgr": qm,
                    "verdict": "UNKNOWN",
                    "reason": "MQ authorization manifest is empty or unavailable.",
                }
            )
            continue
        qm_rows = df[df["qmgr"].astype(str).str.casefold() == qm.casefold()]
        if qm_rows.empty:
            results.append(
                {
                    "qmgr": qm,
                    "verdict": "UNKNOWN",
                    "reason": "Queue manager is not present in qmgr_dump.csv.",
                }
            )
            continue

        snapshot = _snapshot_metadata(qm_rows, max_age_hours)
        auth_rows = qm_rows[
            qm_rows["object_type"].astype(str).str.upper() == "AUTHREC"
        ]
        parsed_records: list[dict] = []
        for command in auth_rows.get("mqsc_command", []):
            parsed = parse_authrec(str(command))
            if not parsed:
                continue
            aliases = (
                group_aliases
                if parsed["entity_type"] == "GROUP"
                else user_aliases
            )
            if parsed["entity"].casefold() in aliases:
                parsed_records.append(parsed)

        qmgr_records = [
            row
            for row in parsed_records
            if row["object_type"] == "QMGR"
            and row["profile"].casefold() in {"self", qm.casefold()}
        ]
        qmgr_authorities = sorted(
            {authority for row in qmgr_records for authority in row["authorities"]}
        )
        connect = "CONNECT" in qmgr_authorities

        object_summary: dict[str, set[str]] = {}
        for row in parsed_records:
            object_summary.setdefault(row["object_type"] or "UNKNOWN", set()).update(
                row["authorities"]
            )

        resource_records: list[dict] = []
        resource_authorities: list[str] = []
        if resource:
            resource_records = [
                row
                for row in parsed_records
                if row["profile"] and _profile_matches(row["profile"], resource)
            ]
            resource_authorities = sorted(
                {
                    authority
                    for row in resource_records
                    for authority in row["authorities"]
                    if authority != "NONE"
                }
            )

        qmgr_config = ""
        qmgr_def = qm_rows[
            qm_rows["object_type"].astype(str).str.upper() == "QMGR"
        ]
        if not qmgr_def.empty:
            qmgr_config = str(qmgr_def.iloc[0].get("mqsc_command", ""))
        chlauth_enabled = (
            (_attr(qmgr_config, "CHLAUTH") or "ENABLED").upper() != "DISABLED"
        )

        if snapshot["stale"]:
            verdict = "UNKNOWN"
            reason = "Authorization snapshot is missing or stale."
        elif not identity_resolved and not parsed_records:
            verdict = "UNKNOWN"
            reason = (
                "Domain groups could not be resolved and no direct principal "
                "grant matched."
            )
        elif not connect:
            verdict = "DENIED"
            reason = "No matching queue-manager CONNECT authority was found."
        elif resource and not resource_authorities:
            verdict = "DENIED"
            reason = (
                f"CONNECT is granted, but no matching authority profile covers "
                f"'{resource}'."
            )
        elif chlauth_enabled:
            verdict = "CONDITIONAL"
            reason = (
                "OAM CONNECT is granted, but CHLAUTH admission requires runtime "
                + (
                    "evaluation for the supplied channel."
                    if channel
                    else "channel context."
                )
            )
        else:
            verdict = "ALLOWED"
            reason = "Queue-manager CONNECT is granted and CHLAUTH is disabled."

        results.append(
            {
                "qmgr": qm,
                "verdict": verdict,
                "reason": reason,
                "connect": connect,
                "qmgr_authorities": qmgr_authorities,
                "matched_entities": sorted(
                    {f"{r['entity_type']}:{r['entity']}" for r in parsed_records}
                ),
                "object_authority_summary": {
                    key: sorted(value)
                    for key, value in sorted(object_summary.items())
                },
                "resource": resource,
                "resource_authorities": resource_authorities,
                "resource_access": resource_records,
                "channel": channel,
                "chlauth_enabled": chlauth_enabled,
                "snapshot": snapshot,
            }
        )
    return results


def _permission_map(value: str) -> dict[str, bool]:
    normalized = str(value or "").replace(":", ",")
    permissions = {"read": False, "write": False, "execute": False}
    for token in normalized.split(","):
        token = token.strip().lower()
        if token == "all+":
            return {name: True for name in permissions}
        for name in permissions:
            if token == f"{name}+":
                permissions[name] = True
    return permissions


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _cell(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def evaluate_ace_access(
    user_id: str,
    groups: list[str],
    nodes: list[str],
    *,
    canonical_user: str | None = None,
    identity_resolved: bool = True,
    resource: str | None = None,
    dataframe: pd.DataFrame | None = None,
    max_age_hours: float = ACCESS_MAX_SNAPSHOT_AGE_HOURS,
) -> list[dict]:
    """Evaluate normalized ACE user/group-to-role permission evidence."""
    df = load_ace_auth_dump() if dataframe is None else dataframe
    user_aliases = _identity_aliases([user_id, canonical_user or ""])
    group_aliases = _identity_aliases(groups)
    results: list[dict] = []

    for requested_node in nodes:
        node = requested_node.strip()
        if df.empty or "node" not in df.columns:
            results.append(
                {
                    "node": node,
                    "verdict": "UNKNOWN",
                    "reason": "ACE authorization manifest is empty or unavailable.",
                }
            )
            continue
        rows = df[df["node"].astype(str).str.casefold() == node.casefold()]
        if rows.empty:
            results.append(
                {
                    "node": node,
                    "verdict": "UNKNOWN",
                    "reason": "Node is not present in ace_auth_dump.csv.",
                }
            )
            continue

        snapshot = _snapshot_metadata(rows, max_age_hours)
        authmode = _cell(rows.iloc[0].get("authmode", "")) or "unknown"
        authmode = authmode.lower()
        security_value = _cell(rows.iloc[0].get("securityenabled", ""))
        if snapshot["stale"]:
            results.append(
                {
                    "node": node,
                    "verdict": "UNKNOWN",
                    "reason": "ACE authorization snapshot is missing or stale.",
                    "authmode": authmode,
                    "snapshot": snapshot,
                }
            )
            continue
        if not security_value:
            results.append(
                {
                    "node": node,
                    "verdict": "UNKNOWN",
                    "reason": "ACE security-enabled state is missing from the snapshot.",
                    "authmode": authmode,
                    "snapshot": snapshot,
                }
            )
            continue
        security_enabled = _truthy(security_value)
        if not security_enabled:
            results.append(
                {
                    "node": node,
                    "verdict": "ALLOWED",
                    "reason": (
                        "ACE administration security is disabled; access is not "
                        "authorized per-user and is exposed through the default identity."
                    ),
                    "authmode": authmode,
                    "security_enabled": False,
                    "permissions": {
                        "read": True,
                        "write": True,
                        "execute": True,
                    },
                    "snapshot": snapshot,
                    "warning": (
                        "Enable ACE administration security to enforce user access."
                    ),
                }
            )
            continue

        matched: list[dict] = []
        effective = {"read": False, "write": False, "execute": False}
        for _, row in rows.iterrows():
            subject_type = _cell(row.get("subjecttype", "")).upper()
            subject = _cell(row.get("subject", ""))
            aliases = group_aliases if subject_type == "GROUP" else user_aliases
            if subject.casefold() not in aliases:
                continue
            row_resource = _cell(row.get("resource", ""))
            if resource and row_resource and not _profile_matches(
                row_resource, resource
            ):
                continue
            permissions = _permission_map(_cell(row.get("permissions", "")))
            for name, granted in permissions.items():
                effective[name] = effective[name] or granted
            matched.append(
                {
                    "subject_type": subject_type,
                    "subject": subject,
                    "role": _cell(row.get("role", "")),
                    "resource_type": _cell(row.get("resource_type", "")),
                    "resource": row_resource,
                    "permissions": permissions,
                }
            )

        if not identity_resolved and not matched:
            verdict = "UNKNOWN"
            reason = (
                "Domain groups could not be resolved and no direct user role matched."
            )
        elif not matched or not any(effective.values()):
            verdict = "DENIED"
            reason = (
                "No matching ACE role grants read, write, or execute permission."
            )
        else:
            verdict = "ALLOWED"
            reason = (
                "One or more matching ACE roles grant administration permissions."
            )

        results.append(
            {
                "node": node,
                "verdict": verdict,
                "reason": reason,
                "authmode": authmode,
                "security_enabled": True,
                "permissions": effective,
                "matched_roles": matched,
                "resource": resource,
                "snapshot": snapshot,
            }
        )
    return results
