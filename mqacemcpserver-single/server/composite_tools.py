"""Composite MCP tool registrations for the single-tool-call build.

Each tool bundles the full discovery-plus-execution workflow into a single
call so an orchestrator that can only invoke one tool per user turn can still
answer the common MQ and ACE diagnostic intents end-to-end.

Tool routing conventions preserved from the granular server:
- Every MQ tool's docstring opens with `IBM MQ:`.
- Every ACE tool's docstring opens with `IBM ACE:`.
- The certificate tool's docstring opens with `Certificate:`.
- Tool names start with `mq_` or `ace_` (or are unambiguous, e.g. `get_cert_details`).

Safety conventions preserved:
- All HTTP via `mq_get`/`mq_post`/`fetch_ace` so endpoints land in the audit log.
- All resolved hostnames pass through `hostname_allowed` before any HTTP call.
- All MQSC strings pass through `is_modification_command`.
- All exception paths go through `friendly_error` / `safe_error_message`.
"""
from __future__ import annotations

import asyncio
import json
import re

from mcp.server.fastmcp import FastMCP

from server.ace_helpers import (
    fetch_ace,
    load_node_config,
    load_node_dump,
    nodes_on_host,
    search_node_dump,
)
from server.cert_helpers import load_cert_dump, search_certs
from server.config import MQ_URL_BASE
from server.logger import get_logger
from server.mq_helpers import (
    CSRF_TOKEN,
    build_url,
    friendly_error,
    hostname_allowed,
    load_csv,
    mq_get,
    prettify_dspmq,
    prettify_dspmqver,
    run_mqsc_raw,
    search_objects_structured,
)
from server.query_log import logged_tool
from server.safety import MODIFY_BLOCKED_MSG, is_modification_command

logger = get_logger("mqacemcpserver-single.composite")


# ---------------------------------------------------------------------------
# Shared MQ helpers — internal, not registered as tools
# ---------------------------------------------------------------------------
def _resolve_target_host(
    qmgr_name: str, explicit_hostname: str | None
) -> tuple[str | None, str | None]:
    """Resolve the host for a known QM. Returns (hostname, error_message)."""
    if explicit_hostname:
        return explicit_hostname.strip(), None
    df = load_csv()
    if not df.empty:
        matches = df[df["qmgr"].str.upper() == qmgr_name.upper()]
        if not matches.empty:
            return str(matches.iloc[0]["hostname"]).strip(), None
    return None, (
        f"❌ Queue Manager '{qmgr_name}' is not in the manifest and no "
        "explicit hostname was supplied. Pass `hostname=` to target it directly."
    )


def _restricted_footer(restricted: list[dict]) -> str:
    if not restricted:
        return ""
    qms = ", ".join(f"{r['qmgr']} ({r['hostname']})" for r in restricted)
    return f"\n🚫 Also found on restricted systems (not queried): {qms}"


def _parse_attr(text: str, attr: str) -> str | None:
    """Extract ATTR(value) from MQSC output. Returns None for missing/blank."""
    m = re.search(rf"\b{attr}\(([^)]*)\)", text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


async def _resolve_queue_chain(
    qmgr: str,
    queue_name: str,
    hostname: str,
    max_hops: int = 12,
    visited: set[tuple[str, str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Follow a queue's resolution chain (QALIAS -> QREMOTE -> QLOCAL) across QMs.

    Each hop's real type is probed with ``DISPLAY QUEUE(<q>) TYPE`` rather than
    guessed from the name, so an alias whose TARGET is itself a remote queue
    resolves correctly (the previous code wrongly assumed every alias target was
    a QLOCAL). When a QREMOTE points at another queue manager, the chain hops
    onto it — provided that QM is in the manifest and its host is allow-listed.

    Returns ``(chain_labels, detail_sections)``.
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
                f"⚠️ Loop detected at {cur_q}({cur_qm}); stopping chain resolution."
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
            details.append(f"--- {cur_qm} ({cur_host}) ---")
            details.append(f"[QALIAS({cur_q})]")
            details.append(alias_out)
            target = _parse_attr(alias_out, "TARGET")
            if not target:
                details.append(
                    f"⚠️ Could not resolve TARGET for alias {cur_q} on {cur_qm}."
                )
                break
            cur_q = target  # alias target lives on the same QM
            continue

        if qtype == "QREMOTE":
            remote_out = await run_mqsc_raw(
                cur_qm, f"DISPLAY QREMOTE({cur_q}) ALL", cur_host
            )
            details.append(f"--- {cur_qm} ({cur_host}) ---")
            details.append(f"[QREMOTE({cur_q})]")
            details.append(remote_out)
            rname = _parse_attr(remote_out, "RNAME")
            rqmname = _parse_attr(remote_out, "RQMNAME")
            if not (rname and rqmname):
                # QM alias (blank RNAME) or cluster transmit path — stop here.
                break
            next_host, err = _resolve_target_host(rqmname, None)
            if not next_host:
                chain_labels.append(f"{rname}({rqmname})")
                details.append(
                    f"ℹ️ Destination QM '{rqmname}' is not in the manifest; "
                    f"cannot inspect {rname} there. {err or ''}".rstrip()
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

        # Terminal: QLOCAL (or QMODEL/other) — fetch the full attribute set.
        local_out = await run_mqsc_raw(
            cur_qm, f"DISPLAY QLOCAL({cur_q}) ALL", cur_host
        )
        details.append(f"--- {cur_qm} ({cur_host}) ---")
        details.append(f"[QLOCAL({cur_q}) full attributes]")
        details.append(local_out)
        break
    else:
        details.append("⚠️ Maximum hop count reached; chain may be incomplete.")

    return chain_labels, details


async def _inspect_queue_on_qm(
    qmgr: str, queue_name: str, hostname: str, hint_type: str | None = None
) -> str:
    """Resolve and render a queue's full routing chain starting on one QM.

    Follows QALIAS -> QREMOTE -> QLOCAL, hopping across queue managers when a
    remote queue points elsewhere (subject to the allow-list). ``hint_type`` is
    accepted for backward compatibility but ignored — the live TYPE probe is
    authoritative.
    """
    chain_labels, details = await _resolve_queue_chain(qmgr, queue_name, hostname)
    header = "Resolution chain: " + " --> ".join(chain_labels)
    return header + "\n\n" + "\n".join(details)


async def _inspect_channel_on_qm(
    qmgr: str, channel_name: str, hostname: str
) -> str:
    """Run the channel-inspect MQSC pair on a single QM and return formatted output."""
    status_task = run_mqsc_raw(
        qmgr, f"DISPLAY CHSTATUS({channel_name}) ALL", hostname
    )
    config_task = run_mqsc_raw(
        qmgr,
        f"DISPLAY CHANNEL({channel_name}) CHLTYPE CONNAME SSLCIPH SSLPEER "
        f"CERTLABL MAXMSGL BATCHSZ HBINT",
        hostname,
    )
    status_result, config_result = await asyncio.gather(status_task, config_task)
    return (
        f"--- {qmgr} ({hostname}) ---\n"
        f"[Channel status]\n{status_result}\n"
        f"\n[Channel configuration]\n{config_result}"
    )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
def register(mcp: FastMCP) -> None:
    """Attach every composite tool to the given FastMCP instance."""

    # ----- MQ -----------------------------------------------------------------

    @mcp.tool()
    @logged_tool
    async def mq_queue_inspect(
        queue_name: str,
        qmgr_name: str | None = None,
        hostname: str | None = None,
    ) -> str:
        """IBM MQ: Inspect a queue end-to-end in a single call.

        Bundles manifest discovery + alias resolution + a full attribute fetch
        (`DISPLAY QLOCAL(<Q>) ALL`), so it answers ANY queue-property question:
        depth (CURDEPTH/MAXDEPTH), persistence (DEFPSIST), max message length
        (MAXMSGL), default priority (DEFPRTY), get/put status (GET/PUT),
        triggering (TRIGGER/TRIGTYPE), backout (BOTHRESH/BOQNAME), creation and
        last-altered timestamps (CRDATE/CRTIME, ALTDATE/ALTTIME), and the rest.
        For QA* aliases it follows the TARGET to the underlying QLOCAL and
        returns both the alias mapping and the target's full attributes; for QR*
        remote queues it returns the QREMOTE definition (RNAME/RQMNAME/XMITQ).

        Args:
            queue_name: The queue name (QL.*, QA.*, QR.*, or any other).
            qmgr_name: Optional. When given, goes straight to the live queue
                manager (FAST PATH) instead of consulting the manifest.
            hostname: Optional explicit host. Used when the QM is not in the
                manifest; otherwise the manifest's hostname wins.
        """
        if qmgr_name:
            target_host, err = _resolve_target_host(qmgr_name, hostname)
            if err:
                return err
            allowed, message = hostname_allowed(target_host)
            if not allowed:
                return message
            return await _inspect_queue_on_qm(qmgr_name, queue_name, target_host)

        results = search_objects_structured(queue_name)
        if not results:
            return (
                f"❌ '{queue_name}' not found in the manifest. "
                "Pass `qmgr_name=` (and optionally `hostname=`) to query a "
                "live queue manager directly."
            )

        accessible = [r for r in results if not r["restricted"]]
        restricted = [r for r in results if r["restricted"]]

        if not accessible:
            return (
                f"🚫 '{queue_name}' was found, but only on restricted/production "
                "systems. I do not have access to these."
            )

        sections = [
            f"🔍 '{queue_name}' found on {len(accessible)} accessible "
            f"queue manager(s).\n"
        ]
        for entry in accessible:
            sections.append(
                await _inspect_queue_on_qm(
                    entry["qmgr"],
                    queue_name,
                    entry["hostname"],
                    entry["object_type"],
                )
            )
        footer = _restricted_footer(restricted)
        if footer:
            sections.append(footer)
        return "\n".join(sections)

    @mcp.tool()
    @logged_tool
    async def mq_channel_inspect(
        channel_name: str,
        qmgr_name: str | None = None,
        hostname: str | None = None,
    ) -> str:
        """IBM MQ: Inspect a channel end-to-end in a single call.

        Returns BOTH `DISPLAY CHSTATUS(<C>) ALL` (runtime status) AND
        `DISPLAY CHANNEL(<C>) CHLTYPE CONNAME SSLCIPH SSLPEER CERTLABL
        MAXMSGL BATCHSZ HBINT` (configuration) per hosting queue manager.
        One call answers "is it running", "what's the config", "SSL set up",
        and "where does it connect to".

        Args:
            channel_name: The MQ channel name.
            qmgr_name: Optional. When given, goes straight to that QM (FAST PATH).
            hostname: Optional explicit host. Used when the QM is not in the
                manifest; otherwise the manifest's hostname wins.
        """
        if qmgr_name:
            target_host, err = _resolve_target_host(qmgr_name, hostname)
            if err:
                return err
            allowed, message = hostname_allowed(target_host)
            if not allowed:
                return message
            return await _inspect_channel_on_qm(qmgr_name, channel_name, target_host)

        results = search_objects_structured(channel_name, "CHANNEL")
        if not results:
            results = search_objects_structured(channel_name)
        if not results:
            return (
                f"❌ '{channel_name}' not found in the manifest. "
                "Pass `qmgr_name=` (and optionally `hostname=`) to query a "
                "live queue manager directly."
            )

        accessible = [r for r in results if not r["restricted"]]
        restricted = [r for r in results if r["restricted"]]

        if not accessible:
            return (
                f"🚫 '{channel_name}' was found, but only on restricted/production "
                "systems. I do not have access to these."
            )

        sections = [
            f"🔍 Channel '{channel_name}' found on {len(accessible)} accessible "
            f"queue manager(s).\n"
        ]
        for entry in accessible:
            sections.append(
                await _inspect_channel_on_qm(
                    entry["qmgr"], channel_name, entry["hostname"]
                )
            )
        footer = _restricted_footer(restricted)
        if footer:
            sections.append(footer)
        return "\n".join(sections)

    @mcp.tool()
    @logged_tool
    async def mq_host_overview(
        qmgr_name: str | None = None,
        hostname: str | None = None,
        mqsc_command: str | None = None,
    ) -> str:
        """IBM MQ: Host-level overview — dspmq + dspmqver, plus one optional read-only MQSC.

        Resolves the target host as follows:
          1. Explicit `hostname` wins if supplied.
          2. Else `qmgr_name` is looked up in the manifest.
          3. Else the configured default `MQ_URL_BASE` is used.

        Returns the list of queue managers on the host (`dspmq` equivalent)
        and the MQ installation/version info (`dspmqver` equivalent). When
        BOTH `qmgr_name` and `mqsc_command` are supplied, the command is
        validated against the read-only allow-list and its output is appended.

        Args:
            qmgr_name: Optional queue manager name to target.
            hostname: Optional explicit host. Wins over manifest lookup.
            mqsc_command: Optional read-only MQSC DISPLAY command. Requires
                `qmgr_name`. Modification verbs are blocked.
        """
        target_host = ""
        dspmq_url = MQ_URL_BASE + "qmgr/"
        dspmqver_url = MQ_URL_BASE + "installation"

        if hostname:
            target_host = hostname.strip()
        elif qmgr_name:
            resolved, err = _resolve_target_host(qmgr_name, None)
            if err:
                return err
            target_host = resolved

        if target_host:
            allowed, message = hostname_allowed(target_host)
            if not allowed:
                return message
            dspmq_url = build_url(target_host, "qmgr/")
            dspmqver_url = build_url(target_host, "installation")

        headers = {
            "Content-Type": "application/json",
            "ibm-mq-rest-csrf-token": CSRF_TOKEN,
        }

        async def _do_dspmq() -> str:
            try:
                resp = await mq_get(dspmq_url, headers=headers, timeout=30.0)
                resp.raise_for_status()
                return prettify_dspmq(resp.content)
            except Exception as err:
                return friendly_error(err, hostname=target_host)

        async def _do_dspmqver() -> str:
            try:
                resp = await mq_get(dspmqver_url, headers=headers, timeout=30.0)
                resp.raise_for_status()
                return prettify_dspmqver(resp.content)
            except Exception as err:
                return friendly_error(err, hostname=target_host)

        dspmq_result, dspmqver_result = await asyncio.gather(
            _do_dspmq(), _do_dspmqver()
        )

        sections = [
            f"--- Host overview ({target_host or 'default MQ_URL_BASE'}) ---",
            "[Queue managers (dspmq)]",
            dspmq_result,
            "\n[MQ version (dspmqver)]",
            dspmqver_result,
        ]

        if mqsc_command:
            if not qmgr_name:
                sections.append(
                    "\n⚠️ `mqsc_command` was supplied without `qmgr_name`; "
                    "MQSC was not executed. Pass `qmgr_name=` to target a QM."
                )
            elif is_modification_command(mqsc_command):
                logger.warning(
                    "Blocked modification command from mq_host_overview: %s (qmgr=%s)",
                    mqsc_command,
                    qmgr_name,
                )
                sections.append("\n" + MODIFY_BLOCKED_MSG)
            else:
                mqsc_result = await run_mqsc_raw(
                    qmgr_name, mqsc_command, target_host
                )
                sections.append(f"\n[MQSC `{mqsc_command}` on {qmgr_name}]")
                sections.append(mqsc_result)

        return "\n".join(sections)

    # ----- ACE ----------------------------------------------------------------

    @mcp.tool()
    @logged_tool
    async def ace_node_overview(node: str) -> str:
        """IBM ACE: Node-level overview — node status + every integration server in one call.

        Confirms the node is in `node_config.csv`, then issues the node-status
        and `/servers?depth=2` calls concurrently and returns a single JSON
        envelope: `{status, node, properties, descriptiveProperties,
        servers: [{name, active, properties}]}`.

        Args:
            node: The integration node name (must exist in node_config.csv).
        """
        node_task = fetch_ace(node, "", "node", node=node)
        servers_task = fetch_ace(node, "/servers?depth=2", "server", node=node)
        node_raw, servers_raw = await asyncio.gather(node_task, servers_task)

        envelope: dict = {"node": node}

        try:
            node_doc = json.loads(node_raw)
        except json.JSONDecodeError:
            node_doc = {"status": "error", "message": node_raw}

        if node_doc.get("status") == "success":
            raw = node_doc.get("raw_response", {}) or {}
            envelope["status"] = "success"
            envelope["properties"] = raw.get("properties")
            envelope["descriptiveProperties"] = raw.get("descriptiveProperties")
        else:
            envelope["status"] = node_doc.get("status", "error")
            envelope["message"] = node_doc.get("message")

        try:
            servers_doc = json.loads(servers_raw)
        except json.JSONDecodeError:
            servers_doc = {"status": "error", "message": servers_raw}

        if servers_doc.get("status") == "success":
            children = (servers_doc.get("raw_response") or {}).get("children", [])
            envelope["servers"] = [
                {
                    "name": c.get("name"),
                    "active": c.get("active"),
                    "properties": c.get("properties"),
                }
                for c in children
            ]
        else:
            envelope["servers_error"] = servers_doc.get("message")

        envelope = {k: v for k, v in envelope.items() if v is not None}
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    @logged_tool
    async def ace_server_explore(
        node: str, server: str, application: str | None = None
    ) -> str:
        """IBM ACE: Explore an integration server — applications + message flows in one call.

        Returns the list of applications on `server` AND the relevant message
        flows in a single JSON envelope. When `application` is given the
        flows are scoped to that application; otherwise flows directly on the
        integration server are returned alongside the application list.

        Args:
            node: The integration node name.
            server: The integration server name on that node.
            application: Optional application to scope message flows to.
        """
        apps_task = fetch_ace(
            node,
            f"/servers/{server}/applications?depth=2",
            "app",
            node=node,
            server=server,
        )
        if application:
            flow_path = (
                f"/servers/{server}/applications/{application}/messageflows?depth=2"
            )
            flows_task = fetch_ace(
                node, flow_path, "flow",
                node=node, server=server, application=application,
            )
        else:
            flow_path = f"/servers/{server}/messageflows?depth=2"
            flows_task = fetch_ace(
                node, flow_path, "flow", node=node, server=server
            )

        apps_raw, flows_raw = await asyncio.gather(apps_task, flows_task)

        envelope: dict = {"node": node, "server": server}
        if application:
            envelope["application"] = application

        try:
            apps_doc = json.loads(apps_raw)
        except json.JSONDecodeError:
            apps_doc = {"status": "error", "message": apps_raw}

        if apps_doc.get("status") == "success":
            children = (apps_doc.get("raw_response") or {}).get("children", [])
            envelope["applications"] = [
                {
                    "name": c.get("name"),
                    "active": c.get("active"),
                    "properties": c.get("properties"),
                    "descriptiveProperties": c.get("descriptiveProperties"),
                }
                for c in children
            ]
        else:
            envelope["applications_error"] = apps_doc.get("message")

        try:
            flows_doc = json.loads(flows_raw)
        except json.JSONDecodeError:
            flows_doc = {"status": "error", "message": flows_raw}

        if flows_doc.get("status") == "success":
            envelope["message_flows"] = (
                flows_doc.get("raw_response") or {}
            ).get("children", [])
        else:
            envelope["message_flows_error"] = flows_doc.get("message")

        return json.dumps(envelope, indent=2)

    @mcp.tool()
    @logged_tool
    def ace_search(search_string: str, scope: str | None = None) -> str:
        """IBM ACE: Combined OFFLINE search across configured nodes and the BIP-message dump.

        Searches `resources/node_config.csv` (configured nodes) and/or
        `resources/node_dump.csv` (cached BIP messages from the periodic
        extract job) in a single call.

        Args:
            search_string: Substring to match (case-insensitive). Pass an
                empty string with `scope="nodes"` to list every configured node.
            scope: One of `"nodes"`, `"dump"`, or `"all"` (default `"all"`).
                - `"nodes"` searches only `node_config.csv`.
                - `"dump"` searches only `node_dump.csv`.
                - `"all"` or `None` searches both.
        """
        s = (scope or "all").lower()
        if s not in {"all", "nodes", "dump"}:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"Unknown scope '{scope}'. Use 'all', 'nodes', or 'dump'."
                    ),
                },
                indent=2,
            )

        envelope: dict = {"status": "success", "search_string": search_string,
                          "scope": s}

        if s in {"all", "nodes"}:
            df = load_node_config()
            if df.empty:
                envelope["nodes"] = []
                envelope["nodes_message"] = (
                    "node_config.csv is empty or missing."
                )
            else:
                if search_string:
                    pattern = re.escape(search_string)
                    mask = df.astype(str).apply(
                        lambda row: row.str.contains(
                            pattern, case=False, na=False
                        ).any(),
                        axis=1,
                    )
                    matches = df[mask]
                else:
                    matches = df
                envelope["nodes"] = matches.to_dict(orient="records")

        if s in {"all", "dump"}:
            if load_node_dump().empty:
                envelope["dump_matches"] = []
                envelope["dump_message"] = (
                    "node_dump.csv is empty or missing."
                )
            else:
                envelope["dump_matches"] = search_node_dump(search_string)

        return json.dumps(envelope, indent=2)

    # ----- Certificates -------------------------------------------------------

    @mcp.tool()
    @logged_tool
    def get_cert_details(search_string: str) -> str:
        """Certificate: Look up TLS/SSL certificate details from the OFFLINE inventory (`resources/cert_dump.csv`).

        Use this whenever a user asks about a certificate — its expiry,
        validity dates, common name (CN), or alias — for a host or service.

        This does NOT inspect a live certificate or endpoint; it searches the
        cached inventory produced by the periodic extract job. Each match
        returns: hostname, alias, cn_name (the certificate's CN/subject),
        valid_from and valid_until (the validity window, as date strings;
        valid_until is the expiry date), expirydays (whole days until expiry,
        computed live against today — negative means already expired), and
        ace_nodes (the ACE integration node(s) running on that hostname per the
        offline node dump; empty for a pure-MQ host with no ACE node). The
        search matches the given string (case-insensitive substring) against
        ALL fields, so you can look up by hostname, alias, or CN.

        Args:
            search_string: Hostname, alias, or CN substring to match
                (e.g. 'lodmq01', 'mqweb-https', 'example.com').
        """
        results = search_certs(search_string)
        for row in results:
            row["ace_nodes"] = nodes_on_host(row.get("hostname", ""))
        if not results:
            # Distinguish "no inventory loaded" from "no matches".
            if load_cert_dump().empty:
                return json.dumps(
                    {
                        "status": "error",
                        "message": "No certificate records found. cert_dump.csv may be empty or missing.",
                        "details": {},
                    },
                    indent=2,
                )
            return json.dumps(
                {
                    "status": "success",
                    "message": f"'{search_string}' not found in the certificate inventory.",
                    "results": [],
                },
                indent=2,
            )

        return json.dumps(
            {
                "status": "success",
                "message": f"Found {len(results)} certificate(s) matching '{search_string}'.",
                "results": results,
            },
            indent=2,
        )
