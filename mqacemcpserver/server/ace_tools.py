"""IBM ACE (App Connect Enterprise) MCP tool registrations.

Each tool's docstring starts with "IBM ACE:" so the central orchestrator's
LLM can unambiguously route ACE vs. MQ intents. Tool names are also
prefixed with `ace_` (or include `integration` / `ace`) for clarity.

All tools are read-only (HTTP GET only against the Admin REST API).
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from server.ace_helpers import (
    fetch_ace,
    load_node_config,
    search_node_dump,
)
from server.logger import get_logger
from server.query_log import logged_tool

logger = get_logger("mqacemcpserver.ace.tools")


def register(mcp: FastMCP) -> None:
    """Attach every IBM ACE tool to the given FastMCP instance."""

    @mcp.tool()
    @logged_tool
    async def list_ace_nodes() -> str:
        """IBM ACE: List all integration nodes configured in this server.

        Reads from resources/node_config.csv. Output is a JSON envelope.
        """
        df = load_node_config()
        if df.empty:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No ACE nodes configured in resources/node_config.csv.",
                    "details": {},
                },
                indent=2,
            )
        nodes = df.to_dict(orient="records")
        return json.dumps(
            {"status": "success", "component": "node", "configured_nodes": nodes},
            indent=2,
        )

    @mcp.tool()
    @logged_tool
    async def get_ace_node_status(node: str) -> str:
        """IBM ACE: Get the real-time status of a specific integration node.

        Returns a JSON envelope with the node's `properties` (defaultQueueManagerName,
        connector ports, etc.) and `descriptiveProperties` (ACE version, platform).

        Args:
            node: The integration node name (must exist in node_config.csv).
        """
        full_res_str = await fetch_ace(node, "", "node", node=node)
        try:
            full_res = json.loads(full_res_str)
            if "raw_response" in full_res:
                raw = full_res["raw_response"]
                filtered_res = {
                    "status": full_res.get("status"),
                    "properties": raw.get("properties"),
                    "descriptiveProperties": raw.get("descriptiveProperties"),
                }
                filtered_res = {
                    k: v for k, v in filtered_res.items() if v is not None
                }
                return json.dumps(filtered_res, indent=2)
            return full_res_str
        except json.JSONDecodeError:
            return full_res_str

    @mcp.tool()
    @logged_tool
    async def list_ace_servers(node: str) -> str:
        """IBM ACE: List integration servers on a specific integration node.

        Returns a JSON envelope with `name`, `active` (runtime state), and
        `properties` (e.g. jvmMaxHeapSize) for each integration server.

        Args:
            node: The integration node name.
        """
        full_res_str = await fetch_ace(
            node, "/servers?depth=2", "server", node=node
        )
        try:
            full_res = json.loads(full_res_str)
            if "raw_response" in full_res:
                raw = full_res["raw_response"]
                children = raw.get("children", [])
                filtered_children = [
                    {
                        "name": child.get("name"),
                        "active": child.get("active"),
                        "properties": child.get("properties"),
                    }
                    for child in children
                ]
                return json.dumps(
                    {
                        "status": full_res.get("status"),
                        "servers": filtered_children,
                    },
                    indent=2,
                )
            return full_res_str
        except json.JSONDecodeError:
            return full_res_str

    @mcp.tool()
    @logged_tool
    async def list_ace_applications(node: str, server: str) -> str:
        """IBM ACE: List applications deployed on a specific integration server.

        Args:
            node: The integration node name.
            server: The integration server name on that node.
        """
        full_res_str = await fetch_ace(
            node,
            f"/servers/{server}/applications?depth=2",
            "app",
            node=node,
            server=server,
        )
        try:
            full_res = json.loads(full_res_str)
            if "raw_response" in full_res:
                raw = full_res["raw_response"]
                children = raw.get("children", [])
                filtered_children = [
                    {
                        "name": child.get("name"),
                        "properties": child.get("properties"),
                        "descriptiveProperties": child.get("descriptiveProperties"),
                        "active": child.get("active"),
                    }
                    for child in children
                ]
                return json.dumps(
                    {
                        "status": full_res.get("status"),
                        "component": full_res.get("component"),
                        "node": full_res.get("node"),
                        "server": full_res.get("server"),
                        "raw_response": {"children": filtered_children},
                    },
                    indent=2,
                )
            return full_res_str
        except json.JSONDecodeError:
            return full_res_str

    @mcp.tool()
    @logged_tool
    async def list_ace_message_flows(
        node: str, server: str, app: str | None = None
    ) -> str:
        """IBM ACE: List message flows on an integration server (optionally scoped to an application).

        Args:
            node: The integration node name.
            server: The integration server name.
            app: Optional application name. When given, lists flows in that
                application; otherwise lists flows directly on the server.
        """
        if app:
            path = f"/servers/{server}/applications/{app}/messageflows?depth=2"
            return await fetch_ace(
                node, path, "flow", node=node, server=server, application=app
            )
        path = f"/servers/{server}/messageflows?depth=2"
        return await fetch_ace(node, path, "flow", node=node, server=server)

    @mcp.tool()
    @logged_tool
    def search_ace_local_dump(search_string: str) -> str:
        """IBM ACE: Search the OFFLINE inventory (`resources/node_dump.csv`) for a string.

        This does NOT query live ACE nodes — it searches the cached BIP-message
        dump produced by the periodic extract job. Freshness depends on the
        CSV's `timestamp` column. For real-time data, use `get_ace_node_status`,
        `list_ace_servers`, `list_ace_applications`, or `list_ace_message_flows`.

        Searches across timestamp, host, node, and BIP status messages
        (covers integration servers, applications, message flows, and their
        runtime state).

        Args:
            search_string: Substring to match (case-insensitive).
        """
        results = search_node_dump(search_string)
        if not results:
            # Distinguish "no manifest loaded" from "no matches"
            from server.ace_helpers import load_node_dump

            if load_node_dump().empty:
                return json.dumps(
                    {
                        "status": "error",
                        "message": "No records found. node_dump.csv may be empty or missing.",
                        "details": {},
                    },
                    indent=2,
                )
            return json.dumps(
                {
                    "status": "success",
                    "message": f"'{search_string}' not found in the manifest.",
                    "results": [],
                },
                indent=2,
            )

        return json.dumps(
            {
                "status": "success",
                "message": f"Found {len(results)} matches for '{search_string}'.",
                "results": results,
            },
            indent=2,
        )
