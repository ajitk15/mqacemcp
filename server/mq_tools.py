"""IBM MQ MCP tool registrations.

Each tool's docstring starts with "IBM MQ:" so the central orchestrator's
LLM can unambiguously route MQ vs. ACE intents to the right tool.

Only DISPLAY-style MQSC commands are permitted; modification verbs are
blocked at the tool layer and the user is redirected to the support team.
"""
from __future__ import annotations

import json
import re

from mcp.server.fastmcp import FastMCP

from server.config import MQ_URL_BASE
from server.logger import get_logger
from server.mq_helpers import (
    CSRF_TOKEN,
    build_url,
    friendly_error,
    hostname_allowed,
    load_csv,
    mq_get,
    mq_post,
    prettify_dspmq,
    prettify_dspmqver,
    run_mqsc_raw,
    search_objects_structured,
)
from server.query_log import logged_tool
from server.safety import MODIFY_BLOCKED_MSG, is_modification_command

logger = get_logger("mqacemcpserver.mq.tools")


def _parse_attr(text: str, attr: str) -> str | None:
    """Extract ATTR(value) from MQSC output. Returns None for missing/blank."""
    m = re.search(rf"\b{attr}\(([^)]*)\)", text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _resolve_qm_host(qmgr_name: str) -> str | None:
    """Resolve a queue-manager name to its manifest hostname, or None."""
    df = load_csv()
    if df.empty or "qmgr" not in df.columns or "hostname" not in df.columns:
        return None
    matches = df[df["qmgr"].astype(str).str.upper() == qmgr_name.upper()]
    if matches.empty:
        return None
    return str(matches.iloc[0]["hostname"]).strip()


async def _resolve_depth_chain(
    qmgr: str,
    queue_name: str,
    hostname: str,
    max_hops: int = 12,
    visited: set[tuple[str, str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Follow a queue's chain (QALIAS -> QREMOTE -> QLOCAL) and report depth.

    Probes each hop's real TYPE with ``DISPLAY QUEUE(<q>) TYPE`` rather than
    assuming an alias target is always a local queue. Hops onto the destination
    queue manager when a QREMOTE points elsewhere (subject to the allow-list).
    Returns ``(chain_labels, detail_sections)``; the terminal hop fetches
    CURDEPTH.
    """
    if visited is None:
        visited = set()

    chain_labels: list[str] = []
    details: list[str] = []
    cur_qm, cur_q, cur_host = qmgr, queue_name, hostname

    for _ in range(max_hops):
        key = (cur_qm.upper(), cur_q.upper())
        if key in visited:
            details.append(
                f"⚠️  Loop detected at {cur_q}({cur_qm}); stopping chain resolution."
            )
            break
        visited.add(key)
        chain_labels.append(f"{cur_q}({cur_qm})")

        type_out = await run_mqsc_raw(
            cur_qm, f"DISPLAY QUEUE({cur_q}) TYPE", cur_host
        )
        qtype = _parse_attr(type_out, "TYPE")

        if qtype is None:
            details.append(f"--- {cur_qm} ({cur_host}) ---")
            details.append(f"[{cur_q}] could not be displayed:")
            details.append(type_out)
            break

        qtype = qtype.upper()

        if qtype == "QALIAS":
            alias_out = await run_mqsc_raw(
                cur_qm, f"DISPLAY QALIAS({cur_q})", cur_host
            )
            details.append(f"--- {cur_qm} ({cur_host}) [Alias] ---")
            details.append(alias_out)
            target = _parse_attr(alias_out, "TARGET")
            if not target:
                details.append(
                    f"⚠️  Could not resolve TARGET for alias {cur_q} on {cur_qm}."
                )
                break
            cur_q = target  # alias target lives on the same QM
            continue

        if qtype == "QREMOTE":
            remote_out = await run_mqsc_raw(
                cur_qm, f"DISPLAY QREMOTE({cur_q}) ALL", cur_host
            )
            details.append(f"--- {cur_qm} ({cur_host}) [Remote queue] ---")
            details.append(remote_out)
            rname = _parse_attr(remote_out, "RNAME")
            rqmname = _parse_attr(remote_out, "RQMNAME")
            if not (rname and rqmname):
                break
            next_host = _resolve_qm_host(rqmname)
            if not next_host:
                chain_labels.append(f"{rname}({rqmname})")
                details.append(
                    f"ℹ️  Destination QM '{rqmname}' is not in the manifest; "
                    f"cannot inspect {rname} there."
                )
                break
            allowed, _msg = hostname_allowed(next_host)
            if not allowed:
                chain_labels.append(f"{rname}({rqmname})")
                details.append(
                    f"🚫 Destination QM '{rqmname}' ({next_host}) is not "
                    "allow-listed; stopping at the destination name."
                )
                break
            cur_qm, cur_q, cur_host = rqmname, rname, next_host
            continue

        # Terminal: QLOCAL (or QMODEL/other) — report the depth.
        depth_out = await run_mqsc_raw(
            cur_qm, f"DISPLAY QLOCAL({cur_q}) CURDEPTH", cur_host
        )
        details.append(f"--- {cur_qm} ({cur_host}) [Target: {cur_q} Depth] ---")
        details.append(depth_out)
        break
    else:
        details.append("⚠️  Maximum hop count reached; chain may be incomplete.")

    return chain_labels, details


def register(mcp: FastMCP) -> None:
    """Attach every IBM MQ tool to the given FastMCP instance."""

    @mcp.tool()
    @logged_tool
    def find_mq_object(search_string: str, object_type: str | None = None) -> str:
        """IBM MQ: Search the OFFLINE queue-manager manifest (`resources/qmgr_dump.csv`) for an object.

        This does NOT query a live queue manager — it searches the cached
        manifest produced by the periodic extract job. Freshness depends on
        the CSV's `extractedat` column. For real-time data on a known
        queue manager, use `runmqsc`. For end-to-end workflows that combine
        discovery + a live MQSC command, prefer:
        - run_mqsc_for_object: auto-searches then runs any MQSC command
        - get_queue_depth: auto-searches, resolves aliases, returns depth
        - get_channel_status: auto-searches then returns channel status

        Args:
            search_string: String to search (e.g., queue name).
            object_type: Optional filter (e.g., 'QLOCAL', 'QUEUES', 'CHANNEL').
        """
        results = search_objects_structured(search_string, object_type)

        if not results:
            if object_type and search_objects_structured(search_string):
                return f"❌ '{search_string}' exists but is not of type '{object_type}'."
            return f"❌ '{search_string}' not found in the manifest."

        accessible = [r for r in results if not r["restricted"]]
        restricted = [r for r in results if r["restricted"]]

        if not accessible:
            return (
                f"🚫 '{search_string}' was found, but only on restricted/production "
                "systems. I do not have access to these."
            )

        output_lines = [
            f"QM:{r['qmgr']} Host:{r['hostname']} Type:{r['object_type']}"
            for r in accessible
        ]
        for r in restricted:
            output_lines.append(
                f"QM:{r['qmgr']} [RESTRICTED: {r['hostname']}] Type:{r['object_type']}"
            )
        return "\n".join(output_lines)

    @mcp.tool()
    @logged_tool
    async def dspmq(qmgr_name: str | None = None) -> str:
        """IBM MQ: List queue managers and their state on a queue-manager host (dspmq equivalent).

        Args:
            qmgr_name: Optional. When omitted, the call goes to the default
                mqweb URL (MQ_URL_BASE) and lists every QM on that host.
                When given, the manifest is looked up to find the host that
                OWNS this queue manager, and the call returns every QM
                running on that host (not just the one you named).
        """
        headers = {
            "Content-Type": "application/json",
            "ibm-mq-rest-csrf-token": CSRF_TOKEN,
        }

        target_hostname = ""
        url = MQ_URL_BASE + "qmgr/"
        if qmgr_name:
            df = load_csv()
            qmgr_matches = df[df["qmgr"].str.upper() == qmgr_name.upper()]
            if qmgr_matches.empty:
                return f"❌ Queue Manager '{qmgr_name}' not found in the manifest."
            target_hostname = str(qmgr_matches.iloc[0]["hostname"]).strip()
            allowed, message = hostname_allowed(target_hostname)
            if not allowed:
                return message
            url = build_url(target_hostname, "qmgr/")

        try:
            response = await mq_get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return prettify_dspmq(response.content)
        except Exception as err:
            return friendly_error(err, hostname=target_hostname)

    @mcp.tool()
    @logged_tool
    async def dspmqver(qmgr_name: str | None = None) -> str:
        """IBM MQ: Display IBM MQ version and installation info for a queue-manager host (dspmqver equivalent).

        Args:
            qmgr_name: Optional. When omitted, the call goes to the default
                mqweb URL (MQ_URL_BASE). When given, the manifest is looked
                up to find the host that OWNS this queue manager, and the
                version info returned is for that host's MQ installation
                (not specific to the named QM).
        """
        headers = {
            "Content-Type": "application/json",
            "ibm-mq-rest-csrf-token": CSRF_TOKEN,
        }
        target_hostname = ""
        url = MQ_URL_BASE + "installation"
        if qmgr_name:
            df = load_csv()
            qmgr_matches = df[df["qmgr"].str.upper() == qmgr_name.upper()]
            if qmgr_matches.empty:
                return f"❌ Queue Manager '{qmgr_name}' not found in the manifest."
            target_hostname = str(qmgr_matches.iloc[0]["hostname"]).strip()
            allowed, message = hostname_allowed(target_hostname)
            if not allowed:
                return message
            url = build_url(target_hostname, "installation")

        try:
            response = await mq_get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return prettify_dspmqver(response.content)
        except Exception as err:
            return friendly_error(err, hostname=target_hostname)

    @mcp.tool()
    @logged_tool
    async def runmqsc(
        qmgr_name: str, mqsc_command: str, hostname: str | None = None
    ) -> str:
        """IBM MQ: Run a read-only MQSC command against a specific queue manager.

        You MUST know the queue manager name before calling this tool.
        If you only know the object name (queue/channel), use
        run_mqsc_for_object instead.

        Hostname resolution: when `hostname` is omitted, the manifest
        (`resources/qmgr_dump.csv`) is consulted. If the queue manager is not
        found there AND no explicit hostname is provided, the call is REJECTED
        — there is no silent fallback. The resolved hostname is then checked
        against the configured allow-list before any HTTP call is made.

        Only DISPLAY commands are allowed. Modification commands
        (ALTER, DEFINE, DELETE, etc.) are blocked at the tool layer.

        Args:
            qmgr_name:    The queue manager name (NOT a queue or channel name).
            mqsc_command: An MQSC command to run (e.g., 'DISPLAY QMGR ALL').
            hostname:     Optional explicit host. If omitted and the QM is not
                          in the manifest, the call is rejected.
        """
        if is_modification_command(mqsc_command):
            logger.warning(
                "Blocked modification command: %s (qmgr=%s)",
                mqsc_command,
                qmgr_name,
            )
            return MODIFY_BLOCKED_MSG

        headers = {
            "Content-Type": "application/json",
            "ibm-mq-rest-csrf-token": CSRF_TOKEN,
        }

        # Resolve target host. Every path MUST end in an allow-list check —
        # there is no silent fallback to using the QM name as the hostname.
        df = load_csv()
        target_hostname: str | None = None
        if hostname:
            target_hostname = hostname.strip()
        else:
            qmgr_matches = (
                df[df["qmgr"].str.upper() == qmgr_name.upper()]
                if not df.empty
                else df
            )
            if not df.empty and not qmgr_matches.empty:
                target_hostname = str(qmgr_matches.iloc[0]["hostname"]).strip()

        if not target_hostname:
            logger.warning(
                "runmqsc rejected — QM %s not in manifest and no hostname given",
                qmgr_name,
            )
            return (
                f"❌ Queue Manager '{qmgr_name}' is not in the manifest and no "
                "explicit hostname was supplied. Pass `hostname=` or use "
                "`run_mqsc_for_object` instead."
            )

        allowed, message = hostname_allowed(target_hostname)
        if not allowed:
            return message

        data = json.dumps(
            {"type": "runCommand", "parameters": {"command": mqsc_command}}
        )
        url = build_url(target_hostname, f"action/qmgr/{qmgr_name}/mqsc")

        try:
            response = await mq_post(
                url, data=data, headers=headers, timeout=30.0
            )
            response.raise_for_status()
            from server.mq_helpers import prettify_runmqsc
            return prettify_runmqsc(response.content)
        except Exception as err:
            return friendly_error(err, hostname=target_hostname)

    @mcp.tool()
    @logged_tool
    async def run_mqsc_for_object(
        object_name: str, mqsc_command: str, object_type: str | None = None
    ) -> str:
        """IBM MQ: Search for an object and run a read-only MQSC command on every queue manager hosting it.

        Discovers the hosting QMs from the manifest, then executes the MQSC
        command on each accessible one and returns consolidated output.

        NOTE: Only DISPLAY commands are allowed. Modification commands
        (ALTER, DEFINE, DELETE, etc.) are blocked.

        Args:
            object_name:  Name of the MQ object (queue, channel, etc.).
            mqsc_command: The MQSC command to execute (e.g.,
                'DISPLAY QLOCAL(QL.IN.APP1) CURDEPTH').
            object_type:  Optional type filter ('QLOCAL', 'CHANNEL', 'QUEUES', ...).
        """
        if is_modification_command(mqsc_command):
            logger.warning(
                "Blocked modification command: %s (object=%s)",
                mqsc_command,
                object_name,
            )
            return MODIFY_BLOCKED_MSG

        logger.info(
            "run_mqsc_for_object",
            extra={"context": {"object": object_name, "command": mqsc_command}},
        )
        results = search_objects_structured(object_name, object_type)
        if not results:
            return f"❌ '{object_name}' not found in the manifest."

        accessible = [r for r in results if not r["restricted"]]
        restricted = [r for r in results if r["restricted"]]

        if not accessible:
            return (
                f"🚫 '{object_name}' was found, but only on restricted/production "
                "systems. I do not have access to these."
            )

        output_lines = [
            f"🔍 Found '{object_name}' on {len(accessible)} accessible queue manager(s).\n"
        ]
        for entry in accessible:
            qm = entry["qmgr"]
            host = entry["hostname"]
            output_lines.append(f"--- {qm} ({host}) ---")
            output_lines.append(await run_mqsc_raw(qm, mqsc_command, host))
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
    @logged_tool
    async def get_queue_depth(queue_name: str) -> str:
        """IBM MQ: Return the current depth of a queue across every queue manager hosting it.

        Auto-discovers the host(s), resolves alias queues (QA*) to their
        target local queues, and returns the actual depth.

        Args:
            queue_name: Queue name, e.g. 'QL.IN.APP1' or 'QA.IN.APP1'.
        """
        logger.info("get_queue_depth", extra={"context": {"queue": queue_name}})
        results = search_objects_structured(queue_name)
        if not results:
            return f"❌ '{queue_name}' not found in the manifest."

        accessible = [r for r in results if not r["restricted"]]
        restricted = [r for r in results if r["restricted"]]

        if not accessible:
            return (
                f"🚫 '{queue_name}' was found, but only on restricted/production "
                "systems. I do not have access to these."
            )

        output_lines: list[str] = []
        for entry in accessible:
            qm = entry["qmgr"]
            host = entry["hostname"]
            chain_labels, details = await _resolve_depth_chain(qm, queue_name, host)
            output_lines.append("Resolution chain: " + " --> ".join(chain_labels))
            output_lines.extend(details)
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
    @logged_tool
    async def get_channel_status(channel_name: str) -> str:
        """IBM MQ: Return the status of a channel across every queue manager hosting it.

        Args:
            channel_name: The MQ channel name.
        """
        logger.info(
            "get_channel_status", extra={"context": {"channel": channel_name}}
        )
        results = search_objects_structured(channel_name, "CHANNEL")
        if not results:
            results = search_objects_structured(channel_name)
        if not results:
            return f"❌ '{channel_name}' not found in the manifest."

        accessible = [r for r in results if not r["restricted"]]
        restricted = [r for r in results if r["restricted"]]

        if not accessible:
            return (
                f"🚫 '{channel_name}' was found, but only on restricted/production "
                "systems. I do not have access to these."
            )

        output_lines = [
            f"🔍 Channel '{channel_name}' found on {len(accessible)} "
            "accessible queue manager(s).\n"
        ]
        for entry in accessible:
            qm = entry["qmgr"]
            host = entry["hostname"]
            output_lines.append(f"--- {qm} ({host}) ---")
            output_lines.append(
                await run_mqsc_raw(qm, f"DISPLAY CHSTATUS({channel_name}) ALL", host)
            )
            output_lines.append("")

        if restricted:
            restricted_qms = ", ".join(
                f"{r['qmgr']} ({r['hostname']})" for r in restricted
            )
            output_lines.append(
                f"🚫 Also found on restricted systems (not queried): {restricted_qms}"
            )

        return "\n".join(output_lines)
