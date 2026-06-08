"""TLS/SSL certificate MCP tool registration.

The single tool's docstring starts with "Certificate:" so the central
orchestrator's LLM can unambiguously route certificate-expiry intents here
(distinct from the "IBM MQ:" / "IBM ACE:" tools). The tool name
`get_cert_details` is also unambiguous on its own.

Read-only: it only searches the OFFLINE inventory CSV — no network calls.
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from server.ace_helpers import nodes_on_host
from server.cert_helpers import load_cert_dump, search_certs
from server.logger import get_logger
from server.query_log import logged_tool

logger = get_logger("mqacemcpserver.cert.tools")


def register(mcp: FastMCP) -> None:
    """Attach the certificate inventory tool to the given FastMCP instance."""

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
        offline node dump; empty for a pure-MQ host with no ACE node).

        The search matches the given string (case-insensitive substring)
        against ALL fields, so you can look up by hostname, alias, or CN.

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
