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


def _as_str_list(value) -> list[str]:
    """Normalise a multi-target argument to a clean, de-duplicated list of strings.

    The tools advertise `list[str]` in their schema, so well-behaved clients send
    an array. This is belt-and-suspenders: it also tolerates a stray bare string
    (wraps it), drops blanks/whitespace-only entries, and removes duplicates while
    preserving the caller's order.
    """
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    cleaned = [str(v).strip() for v in items if v is not None and str(v).strip()]
    return list(dict.fromkeys(cleaned))


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


async def _inspect_one_queue(
    queue_name: str, qmgr_name: str | None, hostname: str | None
) -> str:
    """Full single-queue inspect workflow (FAST PATH or manifest discovery)."""
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


async def _inspect_one_channel(
    channel_name: str, qmgr_name: str | None, hostname: str | None
) -> str:
    """Full single-channel inspect workflow (FAST PATH or manifest discovery)."""
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


async def _host_overview_one(
    qmgr_name: str | None, hostname: str | None, mqsc_command: str | None
) -> str:
    """Single host/QM overview: dspmq + dspmqver (+ optional read-only MQSC)."""
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
            mqsc_result = await run_mqsc_raw(qmgr_name, mqsc_command, target_host)
            sections.append(f"\n[MQSC `{mqsc_command}` on {qmgr_name}]")
            sections.append(mqsc_result)

    return "\n".join(sections)


async def _node_overview_one(node: str) -> dict:
    """Single-node overview envelope (node status + integration servers)."""
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

    return {k: v for k, v in envelope.items() if v is not None}


async def _server_explore_one(
    node: str, server: str, application: str | None
) -> dict:
    """Single integration-server exploration envelope (apps + message flows)."""
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

    return envelope


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
def register(mcp: FastMCP) -> None:
    """Attach every composite tool to the given FastMCP instance."""

    # ----- MQ -----------------------------------------------------------------

    @mcp.tool()
    @logged_tool
    async def mq_queue_inspect(
        queue_names: list[str],
        qmgr_name: str | None = None,
        hostname: str | None = None,
    ) -> str:
        """IBM MQ: Inspect one or more queues end-to-end in a single call.

        Bundles manifest discovery + alias resolution + a full attribute fetch
        (`DISPLAY QLOCAL(<Q>) ALL`), so it answers ANY queue-property question:
        depth (CURDEPTH/MAXDEPTH), persistence (DEFPSIST), max message length
        (MAXMSGL), default priority (DEFPRTY), get/put status (GET/PUT),
        triggering (TRIGGER/TRIGTYPE), backout (BOTHRESH/BOQNAME), creation and
        last-altered timestamps (CRDATE/CRTIME, ALTDATE/ALTTIME), and the rest.
        For QA* aliases it follows the TARGET to the underlying QLOCAL and
        returns both the alias mapping and the target's full attributes; for QR*
        remote queues it returns the QREMOTE definition (RNAME/RQMNAME/XMITQ).

        Pass MULTIPLE queue names to inspect them all in one call — e.g. for
        "what is the depth of QL.IN.APP1 and QL.IN.APP2?" send
        `queue_names=["QL.IN.APP1", "QL.IN.APP2"]`. Each queue is inspected
        independently and the results are concatenated; one queue failing or
        being absent does not stop the others.

        Args:
            queue_names: One or more queue names (QL.*, QA.*, QR.*, or any
                other), as a list — e.g. ["QL.IN.APP1"] or
                ["QL.IN.APP1", "QL.IN.APP2"].
            qmgr_name: Optional. When given, goes straight to the live queue
                manager (FAST PATH) instead of consulting the manifest. Applies
                to every queue in `queue_names`.
            hostname: Optional explicit host. Used when the QM is not in the
                manifest; otherwise the manifest's hostname wins.
        """
        names = _as_str_list(queue_names)
        if not names:
            return "❌ No queue name supplied. Pass queue_names=[\"QL.IN.APP1\", ...]."
        if len(names) == 1:
            return await _inspect_one_queue(names[0], qmgr_name, hostname)

        sections = [f"🔍 Inspecting {len(names)} queues.\n"]
        for q in names:
            sections.append(f"════════ Queue: {q} ════════")
            sections.append(await _inspect_one_queue(q, qmgr_name, hostname))
            sections.append("")
        return "\n".join(sections)

    @mcp.tool()
    @logged_tool
    async def mq_channel_inspect(
        channel_names: list[str],
        qmgr_name: str | None = None,
        hostname: str | None = None,
    ) -> str:
        """IBM MQ: Inspect one or more channels end-to-end in a single call.

        Returns BOTH `DISPLAY CHSTATUS(<C>) ALL` (runtime status) AND
        `DISPLAY CHANNEL(<C>) CHLTYPE CONNAME SSLCIPH SSLPEER CERTLABL
        MAXMSGL BATCHSZ HBINT` (configuration) per hosting queue manager.
        One call answers "is it running", "what's the config", "SSL set up",
        and "where does it connect to".

        Pass MULTIPLE channel names to inspect them all in one call — e.g. for
        "are CH.A and CH.B up?" send `channel_names=["CH.A", "CH.B"]`. Each
        channel is inspected independently and the results are concatenated.

        Args:
            channel_names: One or more MQ channel names, as a list — e.g.
                ["CH.APP.SVRCONN"] or ["CH.TO.PARTNER", "CH.SDR.TO.QM2"].
            qmgr_name: Optional. When given, goes straight to that QM (FAST PATH).
                Applies to every channel in `channel_names`.
            hostname: Optional explicit host. Used when the QM is not in the
                manifest; otherwise the manifest's hostname wins.
        """
        names = _as_str_list(channel_names)
        if not names:
            return "❌ No channel name supplied. Pass channel_names=[\"CH.A\", ...]."
        if len(names) == 1:
            return await _inspect_one_channel(names[0], qmgr_name, hostname)

        sections = [f"🔍 Inspecting {len(names)} channels.\n"]
        for c in names:
            sections.append(f"════════ Channel: {c} ════════")
            sections.append(await _inspect_one_channel(c, qmgr_name, hostname))
            sections.append("")
        return "\n".join(sections)

    @mcp.tool()
    @logged_tool
    async def mq_host_overview(
        qmgr_names: list[str] | None = None,
        hostnames: list[str] | None = None,
        mqsc_command: str | None = None,
    ) -> str:
        """IBM MQ: Host-level overview — dspmq + dspmqver, plus one optional read-only MQSC.

        For each target it resolves the host as follows:
          1. An explicit hostname is used directly.
          2. Else a queue-manager name is looked up in the manifest.
          3. Else (no targets at all) the configured default `MQ_URL_BASE`.

        Returns the list of queue managers on the host (`dspmq` equivalent)
        and the MQ installation/version info (`dspmqver` equivalent). When a
        queue manager is targeted AND `mqsc_command` is supplied, the command
        is validated against the read-only allow-list and its output appended.

        Pass MULTIPLE queue managers or hosts to overview them all in one call
        — e.g. "MQ version on QM1 and QM2" → `qmgr_names=["QM1","QM2"]`, or
        "dspmq on hostA and hostB" → `hostnames=["hostA","hostB"]`. A single
        `qmgr_names` + single `hostnames` pair is treated as one paired target
        (run the MQSC on that QM via that explicit host). `mqsc_command` is
        applied to every queue-manager target.

        Args:
            qmgr_names: Optional list of queue manager names to target.
            hostnames: Optional list of explicit hosts. An explicit host is
                used directly (skips manifest lookup).
            mqsc_command: Optional read-only MQSC DISPLAY command. Requires a
                queue-manager target. Modification verbs are blocked.
        """
        qms = _as_str_list(qmgr_names)
        hosts = _as_str_list(hostnames)

        # A single QM + single host is the existing "paired" target (run the
        # MQSC on that QM, reached via that explicit host).
        if len(qms) == 1 and len(hosts) == 1:
            targets: list[tuple[str | None, str | None]] = [(qms[0], hosts[0])]
        else:
            targets = [(q, None) for q in qms] + [(None, h) for h in hosts]
        if not targets:
            targets = [(None, None)]  # default MQ_URL_BASE overview

        if len(targets) == 1:
            q, h = targets[0]
            return await _host_overview_one(q, h, mqsc_command)

        sections = [f"🔍 Inspecting {len(targets)} hosts/queue managers.\n"]
        for q, h in targets:
            label = q or h or "default MQ_URL_BASE"
            sections.append(f"════════ {label} ════════")
            sections.append(await _host_overview_one(q, h, mqsc_command))
            sections.append("")
        return "\n".join(sections)

    # ----- ACE ----------------------------------------------------------------

    @mcp.tool()
    @logged_tool
    async def ace_node_overview(nodes: list[str]) -> str:
        """IBM ACE: Node-level overview — node status + every integration server in one call.

        For each node it issues the node-status and `/servers?depth=2` calls
        concurrently and builds an envelope: `{status, node, properties,
        descriptiveProperties, servers: [{name, active, properties}]}`.

        Pass MULTIPLE nodes to overview them all in one call — e.g. "what's on
        NODE1 and NODE2?" → `nodes=["NODE1","NODE2"]`. A single node returns
        that envelope directly; multiple nodes return
        `{status, count, nodes: [<envelope>, ...]}`.

        Args:
            nodes: One or more integration node names, as a list — e.g.
                ["NODE1"] or ["NODE1","NODE2"].
        """
        names = _as_str_list(nodes)
        if not names:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No node supplied. Pass nodes=[\"NODE1\", ...].",
                },
                indent=2,
            )
        if len(names) == 1:
            return json.dumps(await _node_overview_one(names[0]), indent=2)

        results = await asyncio.gather(*[_node_overview_one(n) for n in names])
        return json.dumps(
            {"status": "success", "count": len(results), "nodes": list(results)},
            indent=2,
        )

    @mcp.tool()
    @logged_tool
    async def ace_server_explore(
        node: str, servers: list[str], application: str | None = None
    ) -> str:
        """IBM ACE: Explore one or more integration servers — applications + message flows.

        For each server it returns the list of applications AND the relevant
        message flows. When `application` is given the flows are scoped to that
        application; otherwise flows directly on the integration server are
        returned alongside the application list.

        Pass MULTIPLE servers to explore them all in one call — e.g. "apps on
        IS001 and IS002 on NODE2" → `node="NODE2", servers=["IS001","IS002"]`.
        All servers must live on the same `node`. A single server returns its
        envelope directly; multiple return `{status, node, count, servers:
        [<envelope>, ...]}`.

        Args:
            node: The integration node name (shared by all servers).
            servers: One or more integration server names on that node, as a
                list — e.g. ["IS001"] or ["IS001","IS002"].
            application: Optional application to scope message flows to
                (applied to every server).
        """
        names = _as_str_list(servers)
        if not names:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No server supplied. Pass servers=[\"IS001\", ...].",
                },
                indent=2,
            )
        if len(names) == 1:
            return json.dumps(
                await _server_explore_one(node, names[0], application), indent=2
            )

        results = await asyncio.gather(
            *[_server_explore_one(node, s, application) for s in names]
        )
        envelope: dict = {
            "status": "success",
            "node": node,
            "count": len(results),
            "servers": list(results),
        }
        if application:
            envelope["application"] = application
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    @logged_tool
    def ace_search(search_strings: list[str], scope: str | None = None) -> str:
        """IBM ACE: Combined OFFLINE search across configured nodes and the BIP-message dump.

        Searches `resources/node_config.csv` (configured nodes) and/or
        `resources/node_dump.csv` (cached BIP messages from the periodic
        extract job) in a single call.

        Pass MULTIPLE search strings to match any of them in one call — e.g.
        "find anything about OrderFlow or PaymentFlow" →
        `search_strings=["OrderFlow","PaymentFlow"]`. A row matches if it
        matches ANY supplied string; matches are merged and de-duplicated.

        Args:
            search_strings: One or more substrings to match (case-insensitive),
                as a list. Pass `[""]` (or an empty list) with `scope="nodes"`
                to list every configured node.
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

        # Keep blanks here (unlike _as_str_list): an empty string means
        # "match everything". An empty/blank list collapses to a single
        # match-all query.
        queries = [q.strip() for q in (search_strings or []) if q is not None]
        queries = list(dict.fromkeys(queries))
        if not queries:
            queries = [""]
        match_all = "" in queries

        envelope: dict = {"status": "success", "search_strings": queries,
                          "scope": s}

        if s in {"all", "nodes"}:
            df = load_node_config()
            if df.empty:
                envelope["nodes"] = []
                envelope["nodes_message"] = (
                    "node_config.csv is empty or missing."
                )
            else:
                if match_all:
                    matches = df
                else:
                    combined = None
                    for q in queries:
                        pattern = re.escape(q)
                        mask = df.astype(str).apply(
                            lambda row: row.str.contains(
                                pattern, case=False, na=False
                            ).any(),
                            axis=1,
                        )
                        combined = mask if combined is None else (combined | mask)
                    matches = df[combined]
                envelope["nodes"] = matches.to_dict(orient="records")

        if s in {"all", "dump"}:
            if load_node_dump().empty:
                envelope["dump_matches"] = []
                envelope["dump_message"] = (
                    "node_dump.csv is empty or missing."
                )
            else:
                seen: set[str] = set()
                merged: list[dict] = []
                for q in queries:
                    for row in search_node_dump(q):
                        key = json.dumps(row, sort_keys=True, default=str)
                        if key not in seen:
                            seen.add(key)
                            merged.append(row)
                envelope["dump_matches"] = merged

        return json.dumps(envelope, indent=2)

    # ----- Certificates -------------------------------------------------------

    @mcp.tool()
    @logged_tool
    def get_cert_details(search_strings: list[str]) -> str:
        """Certificate: Look up TLS/SSL certificate details from the OFFLINE inventory (`resources/cert_dump.csv`).

        Use this whenever a user asks about a certificate — its expiry,
        validity dates, common name (CN), or alias — for a host or service.

        This does NOT inspect a live certificate or endpoint; it searches the
        cached inventory produced by the periodic extract job. Each match
        returns: hostname, alias, cn_name (the certificate's CN/subject),
        valid_from and valid_until (the validity window, as date strings;
        valid_until is the expiry date), expirydays (whole days until expiry,
        computed live against today — negative means already expired),
        ace_nodes (the ACE integration node(s) running on that hostname per the
        offline node dump; empty for a pure-MQ host with no ACE node), and
        matched_query (which of the supplied search strings matched this cert).
        Each search string matches (case-insensitive substring) against ALL
        fields, so you can look up by hostname, alias, or CN.

        Pass MULTIPLE search strings to look up several certificates in one
        call — e.g. for "when do the certs on lodmq01 and lotace03 expire?"
        send `search_strings=["lodmq01", "lotace03"]`. Matches from all queries
        are merged into one `results` array, de-duplicated by
        (hostname, alias, cn_name).

        Args:
            search_strings: One or more hostname/alias/CN substrings to match,
                as a list — e.g. ["lodmq01"] or ["lodmq01", "mqweb-https"].
        """
        queries = _as_str_list(search_strings)
        if not queries:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No search string supplied. Pass search_strings=[\"lodmq01\", ...].",
                    "details": {},
                },
                indent=2,
            )

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

        # Merge matches across all queries, deduped by (hostname, alias, cn_name),
        # preserving first-seen order and recording which queries matched each row.
        merged: dict[tuple, dict] = {}
        order: list[tuple] = []
        for s in queries:
            for row in search_certs(s):
                key = (row.get("hostname"), row.get("alias"), row.get("cn_name"))
                if key in merged:
                    if s not in merged[key]["matched_query"]:
                        merged[key]["matched_query"].append(s)
                    continue
                row["ace_nodes"] = nodes_on_host(row.get("hostname", ""))
                row["matched_query"] = [s]
                merged[key] = row
                order.append(key)

        results = [merged[k] for k in order]
        q_word = "query" if len(queries) == 1 else "queries"

        if not results:
            return json.dumps(
                {
                    "status": "success",
                    "message": f"No certificates found matching {len(queries)} {q_word}: {queries}.",
                    "results": [],
                },
                indent=2,
            )

        return json.dumps(
            {
                "status": "success",
                "message": f"Found {len(results)} certificate(s) matching {len(queries)} {q_word}.",
                "results": results,
            },
            indent=2,
        )
