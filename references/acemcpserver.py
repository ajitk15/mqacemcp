#
# Copyright (c) 2026 IBM Corp.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Environment variables and Logging
# ---------------------------------------------------------------------------
env_path = Path(__file__).resolve().parent.parent / ".env"

load_dotenv(dotenv_path=env_path)

log_level_str = os.getenv("MQ_LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)

logging.basicConfig(
    level=log_level,
    stream=sys.stderr,
    format="%(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("acemcpserver")

logger.debug("Loading .env from %s", env_path)

# ---------------------------------------------------------------------------
# CSV helpers — cached at module level so disk is only read once per startup
# --------------------------------------------------------------------------- #
CSV_PATH = Path(os.path.dirname(os.path.abspath(__file__))).parent / "resources" / "node_dump.csv"

_CSV_CACHE: pd.DataFrame | None = None


def _load_csv_from_disk() -> pd.DataFrame:
    """Read and parse the node_dump CSV from disk."""
    if not CSV_PATH.exists():
        logger.warning("CSV file not found at %s", CSV_PATH)
        return pd.DataFrame()

    try:
        # Based on the provided CSV snippet, there is no header row.
        # columns: timestamp, host, node, status
        df = pd.read_csv(
            CSV_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=None,
            names=["timestamp", "host", "node", "status"]
        )
        
        # Strip whitespace from all string columns more efficiently
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].str.strip()

        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            
        logger.info("CSV loaded successfully: %d rows, %d columns", len(df), len(df.columns))
        return df
    except Exception:
        logger.exception("ERROR loading CSV")
        return pd.DataFrame()


def load_csv() -> pd.DataFrame:
    """Return the cached CSV dataframe, loading from disk on first call."""
    global _CSV_CACHE
    if _CSV_CACHE is None:
        _CSV_CACHE = _load_csv_from_disk()
    return _CSV_CACHE


# ---------------------------------------------------------------------------
# Node Config helpers
# ---------------------------------------------------------------------------
NODE_CONFIG_PATH = Path(os.path.dirname(os.path.abspath(__file__))).parent / "resources" / "node_config.csv"

_NODE_CONFIG_CACHE: pd.DataFrame | None = None


def _load_node_config_from_disk() -> pd.DataFrame:
    """Read and parse the node_config CSV from disk."""
    if not NODE_CONFIG_PATH.exists():
        logger.warning("Node Config file not found at %s", NODE_CONFIG_PATH)
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            NODE_CONFIG_PATH,
            delimiter="|",
            skipinitialspace=True,
            header=0
        )
        
        # Strip whitespace from all string columns
        df.columns = [c.strip() for c in df.columns]
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        
        # Ensure nodeport is handled properly
        if "nodeport" in df.columns:
            df["nodeport"] = pd.to_numeric(df["nodeport"], errors="coerce").fillna(7600).astype(int)
            
        return df
    except Exception:
        logger.exception("ERROR loading Node Config CSV")
        return pd.DataFrame()


def load_node_config() -> pd.DataFrame:
    """Return the cached Node Config dataframe."""
    global _NODE_CONFIG_CACHE
    if _NODE_CONFIG_CACHE is None:
        _NODE_CONFIG_CACHE = _load_node_config_from_disk()
    return _NODE_CONFIG_CACHE


def get_node_endpoint(node: str) -> tuple[str, int]:
    """Return the (host, port) for a given node name from the config."""
    df = load_node_config()
    if df.empty:
        raise ValueError("Node configuration is empty or missing.")
        
    # Case-insensitive match on node name
    matches = df[df["node"].str.upper() == node.upper()]
    if matches.empty:
        raise ValueError(f"Integration Node '{node}' is not defined in node_config.csv.")
        
    row = matches.iloc[0]
    return row["host"], row["nodeport"]



# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp_host = os.getenv("ACE_MCP_HOST", "0.0.0.0")
mcp_port = int(os.getenv("ACE_MCP_PORT", 7600))
mcp = FastMCP("acemcpserver", host=mcp_host, port=mcp_port)

_global_http_client = None

def get_http_client() -> httpx.AsyncClient:
    global _global_http_client
    if _global_http_client is None:
        auth = None
        user = os.getenv("ACE_USER_NAME")
        pwd = os.getenv("ACE_PASSWORD")
        if user and pwd:
            auth = httpx.BasicAuth(username=user, password=pwd)
        _global_http_client = httpx.AsyncClient(verify=False, auth=auth)
    return _global_http_client

async def _fetch_ace(target_node: str, path: str, component: str, **kwargs) -> str:
    """Helper method to fetch data from ACE Admin REST API and format the response."""
    
    try:
        host, port = get_node_endpoint(target_node)
    except Exception as e:
        error_res = {
            "status": "error",
            "message": str(e),
            "details": {"node": target_node}
        }
        return json.dumps(error_res, indent=2)

    url = f"https://{host}:{port}/apiv2{path}"
    
    client = get_http_client()
    try:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
            
        # Try parsing as JSON
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text}
            
        state = data.get("state", "unknown") if isinstance(data, dict) else "unknown"
        
        # Some responses might have a state field inside another wrapper, but we keep it simple
        # and fallback to "unknown" if not found directly
        
        success_res = {
            "status": "success",
            "component": component,
            **kwargs,
            "runtime_state": state,
            "raw_response": data
        }
        return json.dumps(success_res, indent=2)
        
    except httpx.HTTPStatusError as err:
        error_res = {
            "status": "error",
            "message": f"HTTP {err.response.status_code} Error: {err.response.text}",
            "details": {"url": url}
        }
        return json.dumps(error_res, indent=2)
    except Exception as err:
        error_res = {
            "status": "error",
            "message": f"Connection Error: {str(err)}",
            "details": {"url": url}
        }
        return json.dumps(error_res, indent=2)


@mcp.tool()
async def list_nodes() -> str:
    """List all available Integration Nodes configured in the system."""
    df = load_node_config()
    if df.empty:
        return json.dumps({"status": "error", "message": "No nodes configured in node_config.csv.", "details": {}}, indent=2)
        
    nodes = df.to_dict(orient="records")
    return json.dumps({
        "status": "success",
        "component": "node",
        "configured_nodes": nodes
    }, indent=2)


@mcp.tool()
async def get_node_status(node: str) -> str:
    """
    Get the real-time status of a specific Integration Node.
    
    Returns a JSON string containing:
    - status: 'success' or 'error'
    - properties: Object containing configuration like defaultQueueManagerName and connector ports
    - descriptiveProperties: Object containing the ACE version, platformName, and architecture
    """
    full_res_str = await _fetch_ace(node, "", "node", node=node)
    try:
        full_res = json.loads(full_res_str)
        if "raw_response" in full_res:
            raw = full_res["raw_response"]
            filtered_res = {
                "status": full_res.get("status"),
                "properties": raw.get("properties"),
                "descriptiveProperties": raw.get("descriptiveProperties")
            }
            # Remove keys that might be None if the node response lacked them
            filtered_res = {k: v for k, v in filtered_res.items() if v is not None}
            return json.dumps(filtered_res, indent=2)
        return full_res_str
    except json.JSONDecodeError:
        return full_res_str


@mcp.tool()
async def list_servers(node: str) -> str:
    """
    List Integration Servers on the specified node.
    
    Returns a JSON string containing:
    - status: 'success' or 'error'
    - servers: Array of objects, each containing:
        - name: Name of the Integration Server
        - active: Object with runtime states (e.g. isRunning, processId, state)
        - properties: Object with configuration properties (e.g. jvmMaxHeapSize)
    """
    full_res_str = await _fetch_ace(node, f"/servers?depth=2", "server", node=node)
    try:
        full_res = json.loads(full_res_str)
        if "raw_response" in full_res:
            raw = full_res["raw_response"]
            children = raw.get("children", [])
            filtered_children = []
            for child in children:
                filtered_children.append({
                    "name": child.get("name"),
                    "active": child.get("active"),
                    "properties": child.get("properties")
                })
            
            filtered_res = {
                "status": full_res.get("status"),
                "servers": filtered_children
            }
            return json.dumps(filtered_res, indent=2)
        return full_res_str
    except json.JSONDecodeError:
        return full_res_str


#@mcp.tool()
#async def get_server_status(node: str, server: str) -> str:
#    """Get the status of a specific Integration Server."""
#    return await _fetch_ace(node, f"/servers/{server}", "server", node=node, server=server)


@mcp.tool()
async def list_applications(node: str, server: str) -> str:
    """List Applications on a specific Integration Server."""
    full_res_str = await _fetch_ace(node, f"/servers/{server}/applications?depth=2", "app", node=node, server=server)
    try:
        full_res = json.loads(full_res_str)
        if "raw_response" in full_res:
            raw = full_res["raw_response"]
            children = raw.get("children", [])
            filtered_children = []
            for child in children:
                filtered_children.append({
                    "name": child.get("name"),
                    "properties": child.get("properties"),
                    "descriptiveProperties": child.get("descriptiveProperties"),
                    "active": child.get("active")
                })
            
            filtered_res = {
                "status": full_res.get("status"),
                "component": full_res.get("component"),
                "node": full_res.get("node"),
                "server": full_res.get("server"),
                "raw_response": {
                    "children": filtered_children
                }
            }
            return json.dumps(filtered_res, indent=2)
        return full_res_str
    except json.JSONDecodeError:
        return full_res_str


#@mcp.tool()
#async def get_application_status(node: str, server: str, app: str) -> str:
#    """Get the status of a specific Application."""
#    return await _fetch_ace(node, f"/servers/{server}/applications/{app}", "app", node=node, server=server, application=app)


@mcp.tool()
async def list_message_flows(node: str, server: str, app: str | None = None) -> str:
    """List Message Flows on an Integration Server (and optionally within an Application)."""
    path = f"/servers/{server}/applications/{app}/messageflows?depth=2"
    return await _fetch_ace(node, path, "flow", node=node, server=server, application=app)
    
#@mcp.tool()
#async def get_message_flow_status(node: str, server: str, flow: str, app: str | None = None) -> str:
#    """Get the status of a specific Message Flow."""
#    if app:
#        path = f"/servers/{server}/applications/{app}/messageflows/{flow}"
#        return await _fetch_ace(node, path, "flow", node=node, server=server, application=app, flow=flow)
#    else:
#        path = f"/servers/{server}/messageflows/{flow}"
#        return await _fetch_ace(node, path, "flow", node=node, server=server, flow=flow)


@mcp.tool()
def search_local_dump(search_string: str) -> str:
    """
    Search the local offline node_dump.csv for a specific string.
    Searches across node, server, application, flow, and status.
    
    Args:
        search_string: String to search for
    """
    df = load_csv()
    if df.empty:
        return json.dumps({
            "status": "error",
            "message": "No records found. CSV file may be empty or missing.",
            "details": {}
        }, indent=2)

    # Search for string across all string columns (case-insensitive)
    matches = df[df.astype(str).apply(
        lambda row: row.str.contains(re.escape(search_string), case=False, na=False).any(), axis=1
    )]
    
    if matches.empty:
        return json.dumps({
            "status": "success",
            "message": f"'{search_string}' not found in the manifest.",
            "results": []
        }, indent=2)

    # Convert results to a list of dicts
    results = []
    # If the CSV has standard string timestamp, keep it simple
    for _, r in matches.iterrows():
        ts_str = r['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(r['timestamp']) else ""
        results.append({
            "timestamp": ts_str,
            "host": r["host"],
            "node": r["node"],
            "status": r["status"]
        })

    return json.dumps({
        "status": "success",
        "message": f"Found {len(results)} matches for '{search_string}'.",
        "results": results,
    }, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("ACE_MCP_TRANSPORT", "stdio")
    logger.debug("Starting ACE MCP Server with transport=%s", transport)
    mcp.run(transport=transport)
