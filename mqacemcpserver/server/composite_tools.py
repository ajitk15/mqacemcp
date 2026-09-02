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
import difflib
import json
import re

from mcp.server.fastmcp import FastMCP

from server.ace_helpers import (
    dump_rows,
    fetch_ace,
    known_servers,
    load_node_config,
    load_node_dump,
    nodes_hosting_application,
    nodes_hosting_server,
    nodes_on_host,
    resolve_server_name,
    search_node_dump,
    server_inventory,
    suggest_servers,
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

logger = get_logger("mqacemcpserver.composite")


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


# --- ACE resource managers ---------------------------------------------------
# An integration server exposes ~35 resource managers under
# /apiv2/servers/<eg>/resource-managers (global-cache, jvm-manager,
# kafka-manager, mq-connection-manager, http-connector, odm, ...). Note the
# hyphenated URI: the server document's `children` KEY is `resourceManagers`,
# but that spelling 404s — always use the `uri` form.
#
# Callers ask in plain words ("is cache enabled?"), so requested names are
# resolved against the live listing through an alias map rather than pasted
# into the URL. Fetching `?depth=2` once per server gives every manager's
# properties AND active state in one round-trip, plus the full name list used
# for resolution and "did you mean" — so an unknown name never becomes a 404.

_RM_ALIASES: dict[str, tuple[str, ...]] = {
    # "cache" is genuinely ambiguous on an integration server, so answer both.
    "cache": ("global-cache", "xpath-cache"),
    "caching": ("global-cache", "xpath-cache"),
    "globalcache": ("global-cache",),
    "global": ("global-cache",),
    "xpath": ("xpath-cache",),
    "jvm": ("jvm-manager",),
    "java": ("jvm-manager",),
    "heap": ("jvm-manager",),
    "memory": ("jvm-manager",),
    "kafka": ("kafka-manager",),
    "mq": ("mq-connection-manager",),
    "mq-connection": ("mq-connection-manager",),
    "queue-manager": ("mq-connection-manager",),
    "http": ("http-connector",),
    "https": ("https-connector",),
    "database": ("database-connection-manager",),
    "db": ("database-connection-manager",),
    "jdbc": ("database-connection-manager",),
    "redis": ("redis-connection-manager",),
    "odm": ("odm",),
    "decision-server": ("odm",),
    "otel": ("opentelemetry-manager",),
    "opentelemetry": ("opentelemetry-manager",),
    "tracing": ("opentelemetry-manager",),
    "activity-log": ("activity-log-manager",),
    "esql": ("esql-manager",),
    "nodejs": ("nodejs",),
    "node-js": ("nodejs",),
    "socket": ("socket-connection-manager",),
    "webhook": ("webhook-listener",),
    "soap": ("soap-pipeline-manager",),
    "callable-flow": ("callable-flow-manager",),
    "parser": ("parser-manager",),
}

# What a caller who names no resource manager gets. Deliberately short: the
# full depth=2 payload is ~30KB per integration server, which would swamp the
# orchestrator's context on a multi-server question. Every available name still
# ships in the envelope so a follow-up can narrow without a discovery call.
_RM_DEFAULT = (
    "global-cache",
    "jvm-manager",
    "mq-connection-manager",
    "http-connector",
    "https-connector",
    "kafka-manager",
)


def _normalise_rm(name: str) -> str:
    """Fold a human-typed resource-manager name to its hyphenated URI form."""
    return re.sub(r"[\s_]+", "-", str(name).strip().lower()).strip("-")


def _resolve_rm_names(
    requested: list[str], available: list[str]
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Map caller-supplied terms onto real resource-manager names.

    Tried in order: exact name, alias map, the `-manager`/`-connector` suffix
    the ACE naming convention adds, then a fuzzy match — so an unknown name
    comes back as a "did you mean" rather than an upstream 404.

    Returns `(resolved, unknown, did_you_mean)`, the same shape
    `_resolve_named_servers` uses for EG names:
      - `resolved` — real manager names, de-duplicated, caller order kept;
      - `unknown` — the caller's terms that matched nothing, as a plain list;
      - `did_you_mean` — `{term: suggestions}` for unknown terms that DO have
        a close match. A term with no close match is simply absent; mapping it
        to an empty list reads as a truncation bug to both a human and the
        orchestrator's LLM.
    """
    by_norm = {_normalise_rm(a): a for a in available}
    # Callers type the stem ("kafka", "jvm"), not the full ACE name, so the
    # fuzzy pass matches against stems too — otherwise a one-character typo in
    # "kafka" scores badly against "kafka-manager" and resolves to nothing.
    by_stem: dict[str, str] = {}
    for norm, real in by_norm.items():
        for suffix in ("-manager", "-connector"):
            if norm.endswith(suffix):
                by_stem.setdefault(norm[: -len(suffix)], real)
    resolved: list[str] = []
    unknown: list[str] = []
    did_you_mean: dict[str, list[str]] = {}

    for term in requested:
        norm = _normalise_rm(term)
        if not norm:
            continue

        hits: list[str] = []
        if norm in by_norm:
            hits = [by_norm[norm]]
        elif norm in _RM_ALIASES:
            hits = [by_norm[n] for n in _RM_ALIASES[norm] if n in by_norm]
        if not hits:
            for suffix in ("-manager", "-connector"):
                if norm + suffix in by_norm:
                    hits = [by_norm[norm + suffix]]
                    break
        if not hits:
            haystack = {**by_stem, **by_norm}
            close = difflib.get_close_matches(norm, list(haystack), n=3, cutoff=0.6)
            suggestions: list[str] = []
            for c in close:
                real = haystack[c]
                if real not in suggestions:
                    suggestions.append(real)
            if len(suggestions) == 1:
                # One unambiguous near-match is a typo, not a question.
                hits = suggestions
            else:
                unknown.append(term)
                if suggestions:
                    did_you_mean[term] = suggestions

        for h in hits:
            if h not in resolved:
                resolved.append(h)

    return resolved, unknown, did_you_mean


# --- Target discovery -------------------------------------------------------
# The hosting client makes exactly ONE tool call per user question, so a tool
# can never answer "tell me which node/queue manager you meant". When a target
# is omitted, the tool resolves it from the offline manifests itself. See
# CLAUDE.md on composite discovery-plus-execution tools.


def _all_configured_nodes() -> list[str]:
    """Every integration node in the offline node config, in file order."""
    df = load_node_config()
    if df.empty or "node" not in df.columns:
        return []
    return _as_str_list(df["node"].tolist())


def _nodes_hosting(names: list[str]) -> list[str]:
    """Integration nodes that actually HOST any of `names` (server or app).

    Used when the caller names an execution group or application but no node.
    Matches the parsed `eg`/`application` columns EXACTLY — a node whose dump
    merely mentions the string (e.g. a file or policy named after it) is not a
    host and is not returned.
    """
    found: list[str] = []
    for name in names:
        for node in nodes_hosting_server(name) + nodes_hosting_application(name):
            if node and node not in found:
                found.append(node)
    return found


def _all_manifest_qmgrs() -> list[str]:
    """Every distinct queue manager in the MQ manifest, in file order."""
    df = load_csv()
    if df.empty or "qmgr" not in df.columns:
        return []
    return _as_str_list(df["qmgr"].tolist())


def _parse_attr(text: str, attr: str) -> str | None:
    """Extract ATTR(value) from MQSC output. Returns None for missing/blank."""
    m = re.search(rf"\b{attr}\(([^)]*)\)", text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _mqsc_channel_name(text: str) -> str | None:
    """Extract the channel name from a `DEFINE CHANNEL('NAME') ...` MQSC row.

    The name is the first parenthesised token after CHANNEL; it is quoted in the
    manifest but tolerate an unquoted form too. Returns None if absent/blank.
    """
    m = re.search(r"CHANNEL\('?([^')]+?)'?\)", text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _parse_conname(text: str) -> tuple[str | None, int | None]:
    """Extract (host, port) from a channel's `CONNAME('host(port)')` attribute.

    Quote-aware on purpose: the generic `_parse_attr` regex stops at the first
    `)` and would mangle the nested parens in `CONNAME('server1(1414)')`. Returns
    (None, None) when CONNAME is absent or blank (e.g. `CONNAME(' ')`).
    """
    m = re.search(r"CONNAME\('([^']*)'\)", text, re.IGNORECASE)
    if not m:
        return None, None
    inner = m.group(1).strip()
    if not inner:
        return None, None
    hp = re.match(r"([^(]+)\((\d+)\)\s*$", inner)
    if hp:
        return hp.group(1).strip(), int(hp.group(2))
    return inner, None  # host without an explicit port


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


def _server_entry(child: dict) -> dict:
    """One integration server, with its LIVE start time lifted out of `active`.

    `active.startupTime` is the current process's start — i.e. the LAST restart
    of this integration server. It is the only start/restart time this build can
    produce; the offline dump has none (see ace_helpers._rows_to_results). It is
    lifted to a flat `startup_time` rather than left nested because a renderer
    that tabulates these entries drops dict-valued columns, which would hide it
    entirely. `active` is still passed through whole (`startupEpoch`,
    `processId`, `lastMessageTime`, trace flags, …).
    """
    active = child.get("active") or {}
    entry: dict = {"name": child.get("name")}
    if isinstance(active, dict) and active.get("startupTime"):
        entry["startup_time"] = active["startupTime"]
    entry["active"] = child.get("active")
    entry["properties"] = child.get("properties")
    return entry


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
            _server_entry(c) for c in children
        ]
    else:
        envelope["servers_error"] = servers_doc.get("message")

    return {k: v for k, v in envelope.items() if v is not None}


def _as_doc(raw: str) -> dict:
    """Parse a fetch_ace envelope, degrading a non-JSON body to an error dict."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "error", "message": raw}


def _flow_path(server: str, application: str) -> str:
    """Message flows live ONLY under an application — never under the server."""
    return f"/servers/{server}/applications/{application}/messageflows?depth=2"


def _flow_entries(flows_doc: dict) -> list[dict]:
    """Message-flow children reduced to name + run state."""
    children = (flows_doc.get("raw_response") or {}).get("children") or []
    entries = []
    for c in children:
        if not isinstance(c, dict):
            continue
        active = c.get("active") or {}
        entries.append(
            {
                "name": c.get("name"),
                "running": active.get("isRunning"),
                "state": active.get("state"),
            }
        )
    return entries


async def _server_explore_one(
    node: str, server: str, application: str | None
) -> dict:
    """Single integration-server exploration envelope (apps + message flows).

    An integration server has NO `messageflows` collection of its own in the
    ACE Admin REST API — its children are applications, restApis, services,
    sharedLibraries, policies and friends. Asking for
    `/servers/<s>/messageflows` therefore 404s every time, which used to
    surface as a spurious "Endpoint not found" note on an otherwise complete
    answer. Flows are fetched per application instead, concurrently, once the
    application list is known.
    """
    apps_raw = await fetch_ace(
        node,
        f"/servers/{server}/applications?depth=2",
        "app",
        node=node,
        server=server,
    )

    envelope: dict = {"node": node, "server": server}
    if application:
        envelope["application"] = application

    apps_doc = _as_doc(apps_raw)
    applications: list[dict] = []
    if apps_doc.get("status") == "success":
        children = (apps_doc.get("raw_response") or {}).get("children", [])
        applications = [
            {
                "name": c.get("name"),
                "active": c.get("active"),
                "properties": c.get("properties"),
                "descriptiveProperties": c.get("descriptiveProperties"),
            }
            for c in children
        ]
        envelope["applications"] = applications
    else:
        envelope["applications_error"] = apps_doc.get("message")

    if application:
        # Caller named an application: one scoped call, top-level shape kept.
        flows_doc = _as_doc(
            await fetch_ace(
                node,
                _flow_path(server, application),
                "flow",
                node=node,
                server=server,
                application=application,
            )
        )
        if flows_doc.get("status") == "success":
            envelope["message_flows"] = _flow_entries(flows_doc)
        else:
            envelope["message_flows_error"] = flows_doc.get("message")
        return envelope

    named = [a["name"] for a in applications if a.get("name")]
    if named:
        raws = await asyncio.gather(
            *[
                fetch_ace(
                    node,
                    _flow_path(server, app),
                    "flow",
                    node=node,
                    server=server,
                    application=app,
                )
                for app in named
            ]
        )
        by_name = {a.get("name"): a for a in applications}
        errors: dict = {}
        for app, raw in zip(named, raws):
            doc = _as_doc(raw)
            if doc.get("status") == "success":
                by_name[app]["message_flows"] = _flow_entries(doc)
            else:
                errors[app] = doc.get("message")
        # Every remaining failure is real now, so never swallow it.
        if errors:
            envelope["message_flows_errors"] = errors

    return envelope


def _rm_activity(active: dict) -> tuple[str | None, dict]:
    """Distil `active.statistics` into a factual activity verdict.

    Deliberately the ONLY signal this tool derives. It is tempting to read a
    boolean like `enabled` as "the feature is configured", but that inference
    is wrong on a stock ACE server: `kafka-manager` ships `enabled: true`,
    `nodejs` ships `nodejsEnabled: true` and `activity-log-manager` ships
    `activityLogEnabled: true` on a server where nobody has configured any of
    them. Every non-empty string property is a stock default too. Statistics
    are different - a non-zero counter is evidence the node itself recorded,
    not a guess about what a property name means.

    Returns `(verdict, non_zero_counters)`; verdict is None when the manager
    publishes no statistics at all, which is most of them.
    """
    stats = (active or {}).get("statistics")
    if not isinstance(stats, dict) or not stats:
        return None, {}
    summary = stats.get("summary") if isinstance(stats.get("summary"), dict) else stats
    non_zero = {
        k: v
        for k, v in summary.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v
    }
    return ("has-activity" if non_zero else "no-recorded-activity"), non_zero


async def _live_servers_on(node: str) -> tuple[list[str], str | None]:
    """Integration server names read from the LIVE node, not the offline dump.

    Deliberately NOT `known_servers()` / `_nodes_hosting()`: those read
    `node_dump.csv`, which is a periodic extract and can lag the node. On the
    shipped estate the dump lists four servers for NODE1 while the node
    actually runs five — discovering from it would silently drop an EG from a
    "list every EG" answer, which is worse than returning an error.

    `depth=1` because only the names are needed here; `_node_overview_one`
    uses `depth=2` when it also wants each server's properties.

    Returns `(names, error)` — exactly one of the two is populated.
    """
    doc = _as_doc(await fetch_ace(node, "/servers?depth=1", "server", node=node))
    if doc.get("status") != "success":
        return [], doc.get("message")
    children = (doc.get("raw_response") or {}).get("children") or []
    names = {
        str(c["name"]).strip()
        for c in children
        if isinstance(c, dict) and str(c.get("name") or "").strip()
    }
    return sorted(names), None


async def _resolve_named_servers(
    names: list[str], target_node: str
) -> tuple[list[tuple[str, str]], dict]:
    """Canonicalise EG names and pair each with the node(s) hosting it.

    Two failures this exists to prevent, both found by probing the live nodes:

    1. The ACE REST API is case-sensitive, so `servers=["ace_demo_cache"]`
       used to 404 into a bare "Endpoint not found".
    2. `_nodes_hosting` reads the offline dump, which can lag the node. An EG
       that is running but not yet in the extract (ACE_DEMO_RESTAPI on the
       shipped estate) used to be rejected as "not found on any configured
       integration node" even with the node sitting right there.

    Resolution order is chosen to keep the hot path free: the dump resolves
    names with NO HTTP at all, and only names it does not know trigger one
    live listing per candidate node. A name that resolves nowhere comes back
    in `unknown_servers` with suggestions instead of a 404.

    Returns `(pairs, extra)` where `pairs` is [(node, canonical_server)] and
    `extra` carries the envelope fields describing what happened.
    """
    extra: dict = {}
    canonical: dict[str, str] = {}       # requested -> canonical EG name
    live_nodes: dict[str, list[str]] = {}  # canonical EG -> nodes hosting it
    unresolved: list[str] = []

    for name in names:
        hit = resolve_server_name(name)  # offline dump, case-insensitive, free
        if hit:
            canonical[name] = hit
        else:
            unresolved.append(name)

    live_names: set[str] = set()
    if unresolved:
        # Only now is HTTP worth it. Scan the named node, or the whole estate
        # when the caller did not name one.
        scan = [target_node] if target_node else _all_configured_nodes()
        found = await asyncio.gather(*[_live_servers_on(n) for n in scan])

        index: dict[str, tuple[str, list[str]]] = {}
        node_errors: list[dict] = []
        for node_name, (server_names, err) in zip(scan, found):
            if err:
                node_errors.append(
                    {"node": node_name, "servers_discovery_error": err}
                )
                continue
            for server in server_names:
                live_names.add(server)
                entry = index.setdefault(server.lower(), (server, []))
                entry[1].append(node_name)

        still_unknown: list[str] = []
        for name in unresolved:
            hit = index.get(name.strip().lower())
            if hit:
                canonical[name] = hit[0]
                live_nodes[hit[0]] = hit[1]
            else:
                still_unknown.append(name)

        if node_errors:
            extra["node_errors"] = node_errors
        if still_unknown:
            extra["unknown_servers"] = still_unknown
            # Suggest from the dump AND the live listing, so a live-only EG
            # can be offered as a correction too. Same `did_you_mean` shape
            # ace_search already returns for an unknown EG. Names with no
            # close match are simply absent - never an empty list, which
            # reads as a bug.
            pool = sorted(set(known_servers()) | live_names)
            hints = {
                name: close
                for name in still_unknown
                if (close := difflib.get_close_matches(name, pool, n=3, cutoff=0.5))
            }
            if hints:
                extra["did_you_mean"] = hints

    if not canonical:
        return [], extra

    resolved = [canonical[n] for n in names if n in canonical]
    # De-duplicate: two spellings of one EG must not be inspected twice.
    resolved = list(dict.fromkeys(resolved))
    extra["servers_resolved"] = resolved

    if target_node:
        return [(target_node, s) for s in resolved], extra

    pairs: list[tuple[str, str]] = []
    hosting: list[str] = []
    for server in resolved:
        nodes = live_nodes.get(server) or nodes_hosting_server(server)
        for n in nodes:
            if n not in hosting:
                hosting.append(n)
            pairs.append((n, server))
    extra["discovered_nodes"] = hosting
    return pairs, extra


async def _resource_inspect_one(
    node: str, server: str, requested: list[str]
) -> dict:
    """Single integration-server resource-manager envelope.

    One `?depth=2` call returns every manager with its configured
    `properties` and its running `active` state; the selection is applied
    in-process. That keeps the answer to one HTTP round-trip while still
    reporting the full name list, so a caller who guessed wrong gets a
    suggestion instead of an upstream 404.
    """
    raw = await fetch_ace(
        node,
        f"/servers/{server}/resource-managers?depth=2",
        "resource-manager",
        node=node,
        server=server,
    )

    envelope: dict = {"node": node, "server": server}
    doc = _as_doc(raw)
    if doc.get("status") != "success":
        envelope["resource_managers_error"] = doc.get("message")
        return envelope

    children = (doc.get("raw_response") or {}).get("children") or []
    by_name = {
        str(c.get("name")): c
        for c in children
        if isinstance(c, dict) and c.get("name")
    }
    available = sorted(by_name)
    envelope["available_resource_managers"] = available

    if requested:
        selected, unknown, hints = _resolve_rm_names(requested, available)
        envelope["selected_by"] = "requested"
        if unknown:
            envelope["unknown_resource_managers"] = unknown
        if hints:
            # Same key `_resolve_named_servers` uses for EG names. They cannot
            # collide in one response: server-name resolution runs before any
            # per-server call and short-circuits when nothing resolves.
            envelope["did_you_mean"] = hints
        if not selected:
            # An empty `resource_managers` is dangerously ambiguous on its
            # own: it reads as "this server HAS no resource managers", which
            # would be flatly wrong - this server has
            # len(available) of them. Say which reading is correct.
            envelope["selection_note"] = (
                f"None of the requested names ({', '.join(requested)}) matched "
                f"a resource manager on this integration server. This is a "
                f"name-matching miss, NOT an empty server: "
                f"{len(available)} resource managers are present and listed in "
                "`available_resource_managers`."
            )
    else:
        selected = [n for n in _RM_DEFAULT if n in by_name]
        envelope["selected_by"] = "default"
        if selected:
            envelope["selection_note"] = (
                "No resource manager was named, so a curated default set was "
                "returned. Every available name is listed in "
                "`available_resource_managers`."
            )
        else:
            # A server carrying none of the curated defaults would otherwise
            # get the note above, which would misdescribe an empty result.
            envelope["selection_note"] = (
                "No resource manager was named, and this integration server "
                "carries none of the curated default set "
                f"({', '.join(_RM_DEFAULT)}). Its {len(available)} available "
                "managers are listed in `available_resource_managers`; name "
                "one to inspect it."
            )

    entries: list[dict] = []
    for name in selected:
        child = by_name[name]
        desc = child.get("descriptiveProperties") or {}
        props = child.get("properties") or {}
        active = child.get("active") or {}
        entry = {
            "name": name,
            "identifier": props.get("identifier"),
            "className": desc.get("className"),
            "isDynamic": desc.get("isDynamic"),
            "configured": props,
            "active": active,
        }
        verdict, non_zero = _rm_activity(active)
        if verdict:
            entry["activity"] = verdict
            if non_zero:
                entry["activity_counters"] = non_zero
        entries.append(entry)
    envelope["resource_managers"] = entries
    if entries:
        # The false answer this prevents: asked "which EGs have Kafka
        # configured", the model saw `kafka-manager` in the list with
        # `enabled: true` and reported Kafka configured on every server. Every
        # ACE integration server carries the full set of managers with stock
        # values, so presence proves nothing on its own.
        envelope["presence_note"] = (
            f"All {len(available)} resource managers exist on every ACE "
            "integration server, pre-populated with stock defaults. Presence "
            "in this list is NOT evidence that a feature is configured or in "
            "use. Judge that only from an explicit setting (e.g. "
            "`global-cache.cacheOn`) or from `activity`/`activity_counters`, "
            "which come from counters the node recorded. A manager with "
            "neither cannot be called configured from this data."
        )
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
    async def mq_connection_verify(
        qmgr_name: str,
        hostname: str | None = None,
        port: int | None = None,
        channel: str | None = None,
    ) -> str:
        """IBM MQ: Fact-check connection details from an error against the OFFLINE manifest.

        Use this when a user pastes an MQ connection error (e.g. AMQ9213 /
        AMQ9999 / MQRC 2059) and asks whether the connection details are
        correct. Extract the claimed values from the error text and pass them
        in: the queue manager name, and any of the host, port, and channel it
        mentions (a CONNAME like `server1(1414)` gives both host and port).

        Each supplied field is compared, one by one, against the offline
        `resources/qmgr_dump.csv` extract and reported as CORRECT / MISMATCH /
        NOT-FOUND with the authoritative value:
          - queue manager — present in the manifest (else close-match suggestions);
          - channel — defined on that queue manager (else the list of its channels);
          - port — the queue manager's listener PORT(s) and/or the channel's
            CONNAME port;
          - host — the channel's CONNAME host (the real client endpoint; the
            manifest's own hostname column is only the extract host, so a host
            can be confirmed only when the channel is supplied).

        This is OFFLINE — it says what the details SHOULD be per the last
        extract, so it works even when the endpoint is unreachable (the usual
        state during a connection error). It makes NO network call.

        Args:
            qmgr_name: The queue manager named in the error (required).
            hostname: Optional claimed host (compared to the channel CONNAME host).
            port: Optional claimed port (compared to listener/CONNAME port).
            channel: Optional claimed channel name (e.g. an SVRCONN).
        """
        try:
            qm = (qmgr_name or "").strip()
            if not qm:
                return '❌ No queue manager supplied. Pass qmgr_name="MQREPO1".'

            df = load_csv()
            if df.empty:
                return (
                    "⚠️ The queue-manager manifest is empty or unavailable; "
                    "cannot fact-check."
                )

            qm_rows = df[df["qmgr"].str.upper() == qm.upper()]
            claimed_ch = (channel or "").strip() or None

            lines: list[str] = [
                f"🔎 Fact-check of connection details for queue manager "
                f"'{qm}' (offline manifest):\n"
            ]
            verdicts: list[bool] = []

            # --- Queue manager -------------------------------------------------
            if qm_rows.empty:
                known = sorted(
                    df["qmgr"].dropna().astype(str).str.strip().unique()
                )
                suggestions = difflib.get_close_matches(qm, known, n=3, cutoff=0.5)
                hint = (
                    f" Did you mean: {', '.join(suggestions)}?"
                    if suggestions
                    else f" Known queue managers: {', '.join(known) or '(none)'}."
                )
                lines.append(
                    f"❌ Queue Manager: '{qm}' is NOT in the manifest.{hint}"
                )
                lines.append(
                    "\nOverall: ❌ the queue manager itself does not check out — "
                    "the remaining details cannot be verified against it."
                )
                return "\n".join(lines)
            lines.append(f"✅ Queue Manager: '{qm}' found in the manifest.")

            # --- Channel (also yields the authoritative CONNAME endpoint) ------
            conname_host: str | None = None
            conname_port: int | None = None
            ch_rows = qm_rows[qm_rows["object_type"].str.upper() == "CHANNEL"]
            channel_names = sorted(
                filter(
                    None,
                    (_mqsc_channel_name(t) for t in ch_rows["mqsc_command"]),
                )
            )
            if claimed_ch:
                match_text = None
                for t in ch_rows["mqsc_command"]:
                    if (_mqsc_channel_name(t) or "").upper() == claimed_ch.upper():
                        match_text = t
                        break
                if match_text is None:
                    verdicts.append(False)
                    avail = ", ".join(channel_names) or "(none defined)"
                    lines.append(
                        f"❌ Channel: '{claimed_ch}' is NOT defined on {qm}. "
                        f"Channels on {qm}: {avail}."
                    )
                else:
                    chltype = _parse_attr(match_text, "CHLTYPE") or "?"
                    conname_host, conname_port = _parse_conname(match_text)
                    verdicts.append(True)
                    if conname_host and conname_port is not None:
                        endpoint = f" → CONNAME {conname_host}({conname_port})"
                    elif conname_host:
                        endpoint = f" → CONNAME {conname_host}"
                    else:
                        endpoint = ""
                    lines.append(
                        f"✅ Channel: '{claimed_ch}' found on {qm} "
                        f"(CHLTYPE {chltype}){endpoint}."
                    )

            # --- Authoritative port set (listeners + channel CONNAME) ----------
            lstr_rows = qm_rows[qm_rows["object_type"].str.upper() == "LISTENER"]
            auth_ports: set[int] = set()
            for t in lstr_rows["mqsc_command"]:
                p = _parse_attr(t, "PORT")
                if p and p.isdigit() and int(p) != 0:  # PORT(0) = no fixed port
                    auth_ports.add(int(p))
            if conname_port is not None:
                auth_ports.add(conname_port)

            # --- Port claim ----------------------------------------------------
            if port is not None:
                if not auth_ports:
                    lines.append(
                        f"ℹ️ Port: claimed {port} — no listener/CONNAME port is "
                        f"recorded for {qm}, so it can't be confirmed offline."
                    )
                elif int(port) in auth_ports:
                    verdicts.append(True)
                    lines.append(f"✅ Port: {port} matches {qm}.")
                else:
                    verdicts.append(False)
                    ports_str = ", ".join(map(str, sorted(auth_ports)))
                    lines.append(
                        f"❌ Port: claimed {port} does NOT match {qm}. "
                        f"Manifest port(s): {ports_str}."
                    )

            # --- Hostname claim ------------------------------------------------
            if hostname:
                claimed_host = hostname.strip()
                if conname_host:
                    if claimed_host.lower() == conname_host.lower():
                        verdicts.append(True)
                        lines.append(
                            f"✅ Host: '{claimed_host}' matches the channel "
                            "CONNAME host."
                        )
                    else:
                        verdicts.append(False)
                        lines.append(
                            f"❌ Host: claimed '{claimed_host}' does NOT match the "
                            f"channel CONNAME host '{conname_host}'."
                        )
                else:
                    lines.append(
                        f"ℹ️ Host: '{claimed_host}' can't be confirmed from the "
                        "offline manifest — supply the channel so its CONNAME "
                        "host can be compared (the manifest's hostname column is "
                        "the extract host, not the client endpoint)."
                    )

            if port is None and hostname is None and claimed_ch is None:
                lines.append(
                    "ℹ️ Only the queue manager was supplied — pass hostname=, "
                    "port=, and/or channel= to fact-check those too."
                )

            if not verdicts:
                summary = "ℹ️ nothing to compare beyond the queue manager."
            elif all(verdicts):
                summary = "✅ all supplied details check out."
            elif any(verdicts):
                summary = "⚠️ some details do not match the manifest (see ❌ above)."
            else:
                summary = "❌ the supplied details do not match the manifest."
            lines.append(f"\nOverall: {summary}")
            return "\n".join(lines)
        except Exception as err:
            return friendly_error(err, hostname=hostname or qmgr_name)

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

        For an ESTATE-WIDE question — "does every queue manager have a dead
        letter queue", "what listener port is each one on" — supply only
        `mqsc_command` and leave `qmgr_names` empty. Every queue manager in the
        manifest is then discovered and the command runs against each of them.
        Never ask the user to list the queue managers; look them up.

        Args:
            qmgr_names: Optional list of queue manager names to target. Omit,
                together with `hostnames`, to run `mqsc_command` against every
                queue manager in the manifest.
            hostnames: Optional list of explicit hosts. An explicit host is
                used directly (skips manifest lookup).
            mqsc_command: Optional read-only MQSC DISPLAY command. Modification
                verbs are blocked. Keep the attribute list conservative — one
                unsupported attribute fails the whole command (AMQ8405I).
        """
        qms = _as_str_list(qmgr_names)
        hosts = _as_str_list(hostnames)

        # An MQSC command needs a queue-manager target. With none supplied the
        # question is estate-wide ("does every QM have a DLQ"), and the client
        # gets only one call — so discover every QM from the manifest rather
        # than silently dropping the command.
        discovered_qmgrs: list[str] = []
        if mqsc_command and not qms and not hosts:
            discovered_qmgrs = _all_manifest_qmgrs()
            qms = list(discovered_qmgrs)

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

        header = f"🔍 Inspecting {len(targets)} hosts/queue managers."
        if discovered_qmgrs:
            header += (
                "\nNo queue manager was named, so every queue manager in the "
                f"manifest was discovered: {', '.join(discovered_qmgrs)}."
            )
        sections = [header + "\n"]
        for q, h in targets:
            label = q or h or "default MQ_URL_BASE"
            sections.append(f"════════ {label} ════════")
            sections.append(await _host_overview_one(q, h, mqsc_command))
            sections.append("")
        return "\n".join(sections)

    # ----- ACE ----------------------------------------------------------------

    @mcp.tool()
    @logged_tool
    async def ace_node_overview(nodes: list[str] | None = None) -> str:
        """IBM ACE: Node-level overview — node status + every integration server in one call.

        For each node it issues the node-status and `/servers?depth=2` calls
        concurrently and builds an envelope: `{status, node, properties,
        descriptiveProperties, servers: [{name, startup_time, active,
        properties}]}`. This is a LIVE Admin REST call, not a cached extract.

        THIS IS THE TOOL FOR "WHEN WAS EG X STARTED / RESTARTED" and "how long
        has EG Y been up". Each server carries `startup_time` (also
        `active.startupTime`, with `active.startupEpoch` and `active.processId`
        alongside) — the start of the currently running process, i.e. that
        integration server's MOST RECENT RESTART. Compute uptime as now minus
        that. NEVER answer this from `ace_search`: the offline dump has no event
        times, only the time its extract job ran.

        LIMIT — there is NO RESTART HISTORY. Only the current process start is
        available; how many times an EG has restarted, or when it restarted
        before this one, is not exposed by any tool here (it lives in the ACE
        syslog / event log). Say so rather than inferring one.

        Pass MULTIPLE nodes to overview them all in one call — e.g. "what's on
        NODE1 and NODE2?" → `nodes=["NODE1","NODE2"]`. A single node returns
        that envelope directly; multiple nodes return
        `{status, count, nodes: [<envelope>, ...]}`.

        OMIT `nodes` ENTIRELY for an estate-wide question — "are any traces
        enabled anywhere", "which integration servers are stopped across all
        nodes". Every configured node is then discovered from the offline node
        config and overviewed, and the envelope carries `discovered_targets`.
        There is no need to ask the user which node to look at.

        Args:
            nodes: Optional list of integration node names — e.g. ["NODE1"] or
                ["NODE1","NODE2"]. Omit or pass an empty list to cover every
                configured node.
        """
        names = _as_str_list(nodes)
        discovered = False
        if not names:
            names = _all_configured_nodes()
            discovered = True
            if not names:
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            "No integration nodes are configured "
                            "(resources/node_config.csv is empty or missing)."
                        ),
                    },
                    indent=2,
                )
        if len(names) == 1 and not discovered:
            return json.dumps(await _node_overview_one(names[0]), indent=2)

        results = await asyncio.gather(*[_node_overview_one(n) for n in names])
        envelope: dict = {
            "status": "success",
            "count": len(results),
            "nodes": list(results),
        }
        if discovered:
            envelope["discovered_targets"] = names
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    @logged_tool
    def ace_connection_verify(
        node: str,
        host: str | None = None,
        port: int | None = None,
    ) -> str:
        """IBM ACE: Fact-check integration-node connection details against the OFFLINE config.

        Use this when a user pastes an ACE/BIP error (or asks to validate the
        Admin REST connection details for an integration node) and wants to know
        whether the node/host/port are correct. Extract the claimed values from
        the error and pass them in.

        Each supplied field is compared, one by one, against the offline
        `resources/node_config.csv` extract (the authoritative node → host:port
        mapping used to reach the ACE Admin REST API) and reported as CORRECT /
        MISMATCH / NOT-FOUND with the authoritative value:
          - node — present in node_config.csv (else close-match suggestions);
          - host — the configured host for that node;
          - port — the configured Admin REST port (nodeport).
        It also surfaces the last-extract status lines for the node from
        `node_dump.csv` as context (not a hard verdict).

        This is OFFLINE — it says what the details SHOULD be per the last
        extract and makes NO network call, so it works even when the node is
        unreachable.

        Args:
            node: The integration node named in the error (required).
            host: Optional claimed host (compared to the configured host).
            port: Optional claimed Admin REST port (compared to nodeport).
        """
        try:
            nd = (node or "").strip()
            if not nd:
                return '❌ No integration node supplied. Pass node="NODE1".'

            df = load_node_config()
            if df.empty:
                return (
                    "⚠️ The ACE node config (node_config.csv) is empty or "
                    "unavailable; cannot fact-check."
                )

            matches = df[df["node"].str.upper() == nd.upper()]
            lines: list[str] = [
                f"🔎 Fact-check of connection details for integration node "
                f"'{nd}' (offline config):\n"
            ]
            verdicts: list[bool] = []

            if matches.empty:
                known = sorted(
                    df["node"].dropna().astype(str).str.strip().unique()
                )
                suggestions = difflib.get_close_matches(nd, known, n=3, cutoff=0.5)
                hint = (
                    f" Did you mean: {', '.join(suggestions)}?"
                    if suggestions
                    else f" Configured nodes: {', '.join(known) or '(none)'}."
                )
                lines.append(
                    f"❌ Integration Node: '{nd}' is NOT in node_config.csv.{hint}"
                )
                lines.append(
                    "\nOverall: ❌ the node itself does not check out — the "
                    "remaining details cannot be verified against it."
                )
                return "\n".join(lines)

            row = matches.iloc[0]
            actual_host = str(row["host"]).strip()
            actual_port = int(row["nodeport"])
            lines.append(
                f"✅ Integration Node: '{nd}' found — Admin REST endpoint "
                f"{actual_host}:{actual_port}."
            )

            if host:
                claimed = host.strip()
                if claimed.lower() == actual_host.lower():
                    verdicts.append(True)
                    lines.append(
                        f"✅ Host: '{claimed}' matches the configured host."
                    )
                else:
                    verdicts.append(False)
                    lines.append(
                        f"❌ Host: claimed '{claimed}' does NOT match the "
                        f"configured host '{actual_host}'."
                    )

            if port is not None:
                if int(port) == actual_port:
                    verdicts.append(True)
                    lines.append(
                        f"✅ Port: {port} matches the configured Admin REST port."
                    )
                else:
                    verdicts.append(False)
                    lines.append(
                        f"❌ Port: claimed {port} does NOT match the configured "
                        f"Admin REST port {actual_port}."
                    )

            # Context: last-extract status from node_dump.csv (not a hard verdict).
            dump = search_node_dump(nd)
            if dump:
                sample = dump[0].get("status", "")
                lines.append(
                    f"ℹ️ Last extract: {len(dump)} node_dump line(s) reference "
                    f"'{nd}'. Sample: {sample[:120]}"
                )
            else:
                lines.append(
                    f"ℹ️ Last extract: no node_dump.csv lines reference '{nd}' "
                    "(no recent status captured)."
                )

            if host is None and port is None:
                lines.append(
                    "ℹ️ Only the node was supplied — pass host= and/or port= to "
                    "fact-check those too."
                )

            if not verdicts:
                summary = "ℹ️ nothing to compare beyond the node."
            elif all(verdicts):
                summary = "✅ all supplied details check out."
            elif any(verdicts):
                summary = "⚠️ some details do not match the config (see ❌ above)."
            else:
                summary = "❌ the supplied details do not match the config."
            lines.append(f"\nOverall: {summary}")
            return "\n".join(lines)
        except Exception as err:
            return friendly_error(err, hostname=host or node)

    @mcp.tool()
    @logged_tool
    async def ace_server_explore(
        servers: list[str],
        node: str | None = None,
        application: str | None = None,
    ) -> str:
        """IBM ACE: Explore one or more integration servers — applications + message flows.

        For each server it returns the list of applications AND the relevant
        message flows. When `application` is given the flows are scoped to that
        application; otherwise flows directly on the integration server are
        returned alongside the application list.

        Pass MULTIPLE servers to explore them all in one call — e.g. "apps on
        IS001 and IS002 on NODE2" → `servers=["IS001","IS002"], node="NODE2"`.
        A single server on a known node returns its envelope directly; anything
        else returns `{status, node(s), count, servers: [<envelope>, ...]}`.

        OMIT `node` when the user names only an execution group or application
        — "list the applications under EG ACE_DEMO_MESSAGING". The hosting
        node(s) are then discovered from the offline dump and EVERY node that
        hosts the server is explored, with `discovered_nodes` in the envelope.
        Never ask the user which node a server is on; look it up.

        Args:
            servers: One or more integration server names, as a list — e.g.
                ["IS001"] or ["IS001","IS002"].
            node: Optional integration node name shared by all servers. Omit to
                discover the hosting node(s) automatically.
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

        target_node = (node or "").strip()
        discovered_nodes: list[str] = []
        if not target_node:
            discovered_nodes = _nodes_hosting(names)
            if application:
                # INTERSECT, never union: an application that lives on a
                # different execution group must not drag its node in here.
                app_nodes = _nodes_hosting([application])
                narrowed = [n for n in discovered_nodes if n in app_nodes]
                if narrowed:
                    discovered_nodes = narrowed
            if not discovered_nodes:
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"Could not find {', '.join(names)} on any configured "
                            "integration node in the offline dump. Pass node= to "
                            "query a node directly."
                        ),
                        "servers": names,
                    },
                    indent=2,
                )

        if target_node:
            if len(names) == 1:
                return json.dumps(
                    await _server_explore_one(target_node, names[0], application),
                    indent=2,
                )
            pairs = [(target_node, s) for s in names]
        else:
            pairs = [(n, s) for n in discovered_nodes for s in names]

        results = await asyncio.gather(
            *[_server_explore_one(n, s, application) for n, s in pairs]
        )
        envelope: dict = {
            "status": "success",
            "count": len(results),
            "servers": list(results),
        }
        if target_node:
            envelope["node"] = target_node
        else:
            envelope["discovered_nodes"] = discovered_nodes
        if application:
            envelope["application"] = application
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    @logged_tool
    async def ace_resource_inspect(
        servers: list[str] | None = None,
        resource_managers: list[str] | None = None,
        node: str | None = None,
    ) -> str:
        """IBM ACE: Inspect an integration server's RESOURCE MANAGERS — cache, JVM, Kafka, connectors.

        This is the ONLY tool that can answer whether the GLOBAL CACHE is
        enabled (`cacheOn`) on an execution group, and the only source for
        every other resource-manager setting. `ace_node_overview` returns node
        and EG properties but NOT resource managers, so it can never answer
        these — do not use it for a cache question.

        Covers roughly 35 managers per integration server, including:
        `global-cache` (cacheOn, cache type, catalog/container service,
        listener host/port, map read/write statistics), `xpath-cache`,
        `jvm-manager`, `kafka-manager`, `mq-connection-manager`,
        `http-connector` / `https-connector`, `database-connection-manager`,
        `redis-connection-manager`, `odm`, `opentelemetry-manager`,
        `activity-log-manager`, `esql-manager`, `nodejs`.

        Each manager comes back with `configured` (the server.conf.yaml values)
        AND `active` (what the running server is actually using), so a pending
        restart shows up as a difference between the two.

        Names are matched loosely — "cache", "global cache", "jvm", "heap",
        "kafka", "mq" all resolve. Ambiguous "cache" returns BOTH the global
        cache and the XPath cache. An unrecognised name comes back in
        `unknown_resource_managers`, with `did_you_mean` when there is a
        close match, never as an error.

        OMIT `resource_managers` for a general "how is this EG configured"
        question: a curated default set is returned plus every available name
        in `available_resource_managers`.

        OMIT `servers` ENTIRELY for a "WHICH / ALL EGs" question — "list all
        global cache enabled EGs on NODE1", "which execution groups have Kafka
        configured". Every integration server on the target node is then
        discovered FROM THE LIVE NODE and inspected, and the envelope carries
        `discovered_servers`. Do NOT pass `servers=[""]` or a placeholder —
        just leave the argument out. With `node` omitted too, the sweep covers
        every configured node.

        BUT when the user NAMES an execution group, PASS IT. Omitting `servers`
        on a targeted question turns a one-server lookup into a whole-node
        sweep for no benefit.

        OMIT `node` when the user names only an execution group — the hosting
        node(s) are discovered from the offline dump and every one is
        inspected, with `discovered_nodes` in the envelope. Never ask the user
        which node an EG is on.

        Pass MULTIPLE servers to inspect them all in one call — e.g. "is cache
        on for ACE_DEMO_CACHE and ACE_DEMO_CONNECTORS?" →
        `servers=["ACE_DEMO_CACHE","ACE_DEMO_CONNECTORS"],
        resource_managers=["cache"]`.

        Args:
            servers: Optional list of integration server (execution group)
                names — e.g. ["ACE_DEMO_CONNECTORS"]. Omit to sweep every
                server on the target node(s), discovered live.
            resource_managers: Optional list of resource managers to report —
                e.g. ["cache"] or ["jvm","kafka"]. Omit for the default set.
            node: Optional integration node name shared by all servers. Omit to
                discover the hosting node(s) automatically.
        """
        names = _as_str_list(servers)
        requested = _as_str_list(resource_managers)
        target_node = (node or "").strip()

        envelope: dict = {"status": "success"}
        discovery_errors: list[dict] = []
        pairs: list[tuple[str, str]] = []

        if names:
            # Caller named the servers: canonicalise them and find their
            # host(s). See _resolve_named_servers for why this is not a plain
            # dump lookup any more.
            pairs, extra = await _resolve_named_servers(names, target_node)
            if not pairs:
                error: dict = {
                    "status": "error",
                    "message": (
                        f"Could not find {', '.join(names)} on "
                        + (
                            f"integration node {target_node}."
                            if target_node
                            else "any configured integration node."
                        )
                    ),
                    "servers": names,
                }
                error.update(extra)
                return json.dumps(error, indent=2)

            envelope.update(extra)
            if target_node:
                envelope["node"] = target_node
                # Preserved fast path: one server on a known node answers with
                # the bare per-server envelope, no wrapper. Skipped when a
                # name failed to resolve - the bare envelope has nowhere to
                # carry `unknown_servers`, and silently dropping a name the
                # caller asked about is worse than the extra nesting.
                if len(pairs) == 1 and not extra.get("unknown_servers"):
                    return json.dumps(
                        await _resource_inspect_one(
                            target_node, pairs[0][1], requested
                        ),
                        indent=2,
                    )
        else:
            # No server named: sweep. Nodes first (explicit, else every
            # configured one), then each node's servers FROM THE LIVE NODE -
            # see _live_servers_on for why the offline dump is not usable here.
            if target_node:
                nodes = [target_node]
                envelope["node"] = target_node
            else:
                nodes = _all_configured_nodes()
                if not nodes:
                    return json.dumps(
                        {
                            "status": "error",
                            "message": (
                                "No integration nodes are configured "
                                "(resources/node_config.csv is empty or missing)."
                            ),
                        },
                        indent=2,
                    )
                envelope["discovered_targets"] = nodes

            found = await asyncio.gather(*[_live_servers_on(n) for n in nodes])
            discovered_servers: dict[str, list[str]] = {}
            for n, (server_names, err) in zip(nodes, found):
                if err:
                    # A node that could not be listed must stay visible, or a
                    # partial sweep reads as a complete one.
                    discovery_errors.append(
                        {"node": n, "servers_discovery_error": err}
                    )
                    continue
                discovered_servers[n] = server_names
                pairs.extend((n, s) for s in server_names)

            envelope["discovered_servers"] = discovered_servers
            if not pairs:
                envelope["status"] = "error"
                envelope["message"] = (
                    f"No integration servers could be listed on {', '.join(nodes)}."
                )
                if discovery_errors:
                    envelope["node_errors"] = discovery_errors
                return json.dumps(envelope, indent=2)

        results = await asyncio.gather(
            *[_resource_inspect_one(n, s, requested) for n, s in pairs]
        )
        envelope["count"] = len(results)
        envelope["servers"] = list(results)
        if discovery_errors:
            envelope["node_errors"] = discovery_errors
        if requested:
            envelope["requested_resource_managers"] = requested
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    @logged_tool
    def ace_search(
        search_strings: list[str],
        scope: str | None = None,
        server: str | None = None,
        application: str | None = None,
    ) -> str:
        """IBM ACE: Combined OFFLINE search across configured nodes and the BIP-message dump.

        Searches `resources/node_config.csv` (configured nodes) and/or
        `resources/node_dump.csv` (cached BIP messages from the periodic
        extract job) in a single call.

        THIS DUMP HAS NO EVENT TIMES. `extracted_at` on every returned row is
        WHEN THE EXTRACT JOB RAN — it is identical across the whole file and
        says nothing about when anything started, stopped, restarted or was
        deployed. NEVER report it as a start/restart/stop/deployment time. For
        a live "when was EG X last started / restarted / how long has it been
        up" question use `ace_node_overview`, whose per-EG `active` block
        carries the real startup time.

        EXECUTION GROUPS ARE MATCHED EXACTLY. When a search string (or the
        `server` argument) names a known integration server, the dump result
        is scoped to rows that genuinely belong to that EG: a structured
        inventory comes back in `servers` and `dump_matches` holds only that
        EG's rows. This is the right call for "what applications run under EG
        X". Any other term falls back to an unanchored substring sweep,
        reported as `match_kind: "substring"`.

        A `server` that does not exist returns `status: "not_found"` with a
        `did_you_mean` list, never another execution group's rows.

        Pass MULTIPLE search strings to match any of them in one call - e.g.
        "find anything about OrderFlow or PaymentFlow" ->
        `search_strings=["OrderFlow","PaymentFlow"]`. A row matches if it
        matches ANY supplied string; matches are merged and de-duplicated.
        When one term names an EG, that scoping wins and the remaining loose
        terms are dropped from the dump sweep (echoed in
        `ignored_search_strings`) so they cannot pull in other EGs' rows.

        Args:
            search_strings: One or more terms, as a list. A term that exactly
                names an integration server is EG-scoped; anything else is a
                case-insensitive substring. Pass `[""]` (or an empty list)
                with `scope="nodes"` to list every configured node.
            scope: One of `"nodes"`, `"dump"`, or `"all"` (default `"all"`).
                - `"nodes"` searches only `node_config.csv`.
                - `"dump"` searches only `node_dump.csv`.
                - `"all"` or `None` searches both.
            server: Optional integration server (execution group), matched
                EXACTLY. Use when the EG is already known.
            application: Optional application name, matched EXACTLY.
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

        named_server = (server or "").strip()
        named_app = (application or "").strip()

        # An explicitly named EG must exist. Never fall through to a substring
        # sweep here: that is how rows from other EGs used to leak in.
        if named_server:
            canonical = resolve_server_name(named_server)
            if canonical is None:
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": (
                            f"No execution group named '{named_server}' exists "
                            "in the offline ACE dump."
                        ),
                        "server": named_server,
                        "did_you_mean": suggest_servers(named_server),
                    },
                    indent=2,
                )
            named_server = canonical

        # Auto-scope: a search string that IS a known EG name is an exact EG
        # lookup, not a substring, even when the caller did not use `server=`.
        eg_queries: list[str] = []
        text_queries: list[str] = []
        for q in queries:
            resolved = resolve_server_name(q) if q else None
            if resolved:
                if resolved not in eg_queries:
                    eg_queries.append(resolved)
            else:
                text_queries.append(q)
        if named_server and named_server not in eg_queries:
            eg_queries.append(named_server)

        ignored: list[str] = []
        if eg_queries or named_app:
            ignored = [q for q in text_queries if q]
            text_queries = []

        envelope: dict = {"status": "success", "search_strings": queries,
                          "scope": s}
        if named_server:
            envelope["server"] = named_server
        if named_app:
            envelope["application"] = named_app
        if ignored:
            envelope["ignored_search_strings"] = ignored
            envelope["ignored_reason"] = (
                "An execution group was named, so these loose terms were "
                "dropped rather than widened to other execution groups."
            )

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

                def _add(rows: list[dict]) -> None:
                    for row in rows:
                        key = json.dumps(row, sort_keys=True, default=str)
                        if key not in seen:
                            seen.add(key)
                            merged.append(row)

                if eg_queries:
                    inventories = []
                    for eg in eg_queries:
                        inv = server_inventory(eg)
                        if inv:
                            if named_app:
                                inv = dict(inv)
                                inv["applications"] = [
                                    a
                                    for a in inv["applications"]
                                    if a["name"].lower() == named_app.lower()
                                ]
                                inv["application_count"] = len(inv["applications"])
                            inventories.append(inv)
                        _add(dump_rows(server=eg, application=named_app or None))
                    envelope["servers"] = inventories
                    envelope["match_kind"] = "exact-eg"
                elif named_app:
                    _add(dump_rows(application=named_app))
                    envelope["match_kind"] = "exact-application"
                else:
                    for q in text_queries:
                        _add(search_node_dump(q))
                    envelope["match_kind"] = "substring"
                    # A near-miss on an EG name is the classic cause of a
                    # confusingly wide result; surface the correction.
                    near = {}
                    for q in text_queries:
                        if not q:
                            continue
                        hits = suggest_servers(q)
                        if hits and q not in hits:
                            near[q] = hits
                    if near:
                        envelope["did_you_mean"] = near

                envelope["dump_matches"] = merged
                envelope["data_source"] = (
                    "offline extract (resources/node_dump.csv)"
                )
                envelope["provenance_note"] = (
                    "`extracted_at` is when the extract job ran, NOT when the "
                    "event happened. This dump holds BIP status statements "
                    "with no event or transition times at all — never report "
                    "`extracted_at` as a start, restart, stop or deployment "
                    "time. Live start/restart/uptime comes from "
                    "`ace_node_overview`."
                )

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
