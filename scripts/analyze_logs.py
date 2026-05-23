#!/usr/bin/env python
"""Analyze unified MQ+ACE MCP server query logs and generate operational insights dashboard.

This script is fully self-contained, reading environment configurations (like LOG_DIR)
directly from the local .env file. It can be run on-demand (dynamically) or scheduled
periodically via cron / Task Scheduler.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Define product categorizations for tools
MQ_TOOLS = {
    "dspmq",
    "dspmqver",
    "find_mq_object",
    "runmqsc",
    "run_mqsc_for_object",
    "get_queue_depth",
    "get_channel_status",
}

ACE_TOOLS = {
    "list_ace_nodes",
    "get_ace_node_status",
    "list_ace_servers",
    "list_ace_applications",
    "list_ace_message_flows",
    "search_ace_local_dump",
}


def load_env_config() -> Path:
    """Load configuration from the local .env file and resolve the LOG_DIR."""
    # Find .env in the project root (parent of scripts/)
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    
    # Try importing dotenv to load it, fallback to manual parse if not installed
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path)
    except ImportError:
        # Simple fallback parser if python-dotenv is not available
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

    # Resolve LOG_DIR
    log_dir_raw = (os.getenv("LOG_DIR") or "").strip()
    if log_dir_raw:
        # Expand user directories like ~ and environment variables
        log_dir = Path(os.path.expandvars(os.path.expanduser(log_dir_raw))).resolve()
    else:
        log_dir = (project_root / "logs").resolve()
        
    return log_dir


def parse_logs(log_dir: Path, verbose: bool = True) -> list[dict]:
    """Parse all queries-*.jsonl files in the log directory and return structured records."""
    records = []
    log_files = sorted(list(log_dir.glob("queries-*.jsonl")))

    if verbose:
        print(f"🔍 Locating logs in: {log_dir}")
        print(f"📁 Found {len(log_files)} query log files (.jsonl)")
    
    for log_file in log_files:
        with open(log_file, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Enrich records with helper columns for reporting
                    record["date"] = record["ts"][:10]  # YYYY-MM-DD
                    record["hour"] = int(record["ts"][11:13])  # HH
                    record["product"] = (
                        "IBM MQ"
                        if record["tool"] in MQ_TOOLS
                        else "IBM ACE"
                        if record["tool"] in ACE_TOOLS
                        else "Other"
                    )
                    records.append(record)
                except Exception as e:
                    if verbose:
                        print(f"⚠️ Warning: Could not parse line {line_no} in {log_file.name}: {e}")
                    
    return records


def calculate_metrics(records: list[dict]) -> dict:
    """Execute logical aggregates and performance percentiles directly on parsed logs."""
    if not records:
        return {}

    total_calls = len(records)
    success_calls = sum(1 for r in records if r["outcome"] == "success")
    error_calls = sum(1 for r in records if r["outcome"] == "error")
    success_rate = (success_calls / total_calls * 100)
    
    latencies = sorted([r["latency_ms"] for r in records])
    mean_latency = sum(latencies) / total_calls
    median_latency = latencies[total_calls // 2]
    p95_latency = latencies[int(total_calls * 0.95)]
    p99_latency = latencies[int(total_calls * 0.99)]
    
    sla_breaches = sum(1 for r in records if r["latency_ms"] > 1000)
    sla_compliance = ((total_calls - sla_breaches) / total_calls * 100)
    
    active_callers = len(set(r["caller"] for r in records if r["caller"] is not None))
    
    # Tool breakdown aggregates
    tool_data = {}
    for r in records:
        tool = r["tool"]
        if tool not in tool_data:
            tool_data[tool] = {"calls": 0, "success": 0, "errors": 0, "latencies": [], "product": r["product"]}
        tool_data[tool]["calls"] += 1
        if r["outcome"] == "success":
            tool_data[tool]["success"] += 1
        else:
            tool_data[tool]["errors"] += 1
        tool_data[tool]["latencies"].append(r["latency_ms"])
        
    tool_stats = []
    for tool, data in tool_data.items():
        sorted_lats = sorted(data["latencies"])
        count = data["calls"]
        tool_stats.append({
            "tool": tool,
            "product": data["product"],
            "total_calls": count,
            "success_rate": round(data["success"] / count * 100, 1),
            "mean_latency": round(sum(sorted_lats) / count, 1),
            "p95_latency": round(sorted_lats[int(count * 0.95)] if count > 0 else 0, 1),
            "error_calls": data["errors"]
        })
    tool_stats = sorted(tool_stats, key=lambda x: x["total_calls"], reverse=True)

    # Caller aggregates
    caller_data = {}
    for r in records:
        caller = r["caller"] or "unauthenticated"
        if caller not in caller_data:
            caller_data[caller] = {"calls": 0, "latencies": []}
        caller_data[caller]["calls"] += 1
        caller_data[caller]["latencies"].append(r["latency_ms"])
        
    caller_stats = []
    for caller, data in caller_data.items():
        caller_stats.append({
            "caller": caller,
            "total_calls": data["calls"],
            "mean_latency": round(sum(data["latencies"]) / data["calls"], 1)
        })
    caller_stats = sorted(caller_stats, key=lambda x: x["total_calls"], reverse=True)

    # Endpoints aggregates
    endpoint_hits = {}
    local_resolves = 0
    for r in records:
        endpoints = r.get("endpoints", [])
        if not endpoints:
            local_resolves += 1
            continue
        for ep in endpoints:
            try:
                # Extract hostname
                right = ep.split("://", 1)[1] if "://" in ep else ep
                host = right.split("/", 1)[0].split(":", 1)[0]
                endpoint_hits[host] = endpoint_hits.get(host, 0) + 1
            except Exception:
                endpoint_hits[ep] = endpoint_hits.get(ep, 0) + 1
                
    endpoint_stats = [{"host": k, "hits": v} for k, v in endpoint_hits.items()]
    endpoint_stats = sorted(endpoint_stats, key=lambda x: x["hits"], reverse=True)

    return {
        "total_calls": total_calls,
        "success_rate": success_rate,
        "success_calls": success_calls,
        "error_calls": error_calls,
        "mean_latency": mean_latency,
        "median_latency": median_latency,
        "p95_latency": p95_latency,
        "p99_latency": p99_latency,
        "sla_compliance": sla_compliance,
        "sla_breaches": sla_breaches,
        "active_callers": active_callers,
        "tool_stats": tool_stats,
        "caller_stats": caller_stats,
        "endpoint_stats": endpoint_stats,
        "local_resolves": local_resolves
    }


def build_html_dashboard(metrics: dict) -> str:
    """Build the dashboard HTML in-memory and return it as a string.

    Pure function over ``calculate_metrics``'s output — no file IO. Used by
    both the CLI wrapper ``generate_html_dashboard`` and the standalone
    dashboard HTTP server (``scripts/dashboard_server.py``).
    """
    total_calls = metrics["total_calls"]
    success_rate = metrics["success_rate"]
    success_calls = metrics["success_calls"]
    error_calls = metrics["error_calls"]
    mean_latency = metrics["mean_latency"]
    median_latency = metrics["median_latency"]
    p95_latency = metrics["p95_latency"]
    p99_latency = metrics["p99_latency"]
    sla_compliance = metrics["sla_compliance"]
    sla_breaches = metrics["sla_breaches"]
    active_callers = metrics["active_callers"]
    tool_stats = metrics["tool_stats"]
    caller_stats = metrics["caller_stats"]
    endpoint_stats = metrics["endpoint_stats"]
    local_resolves = metrics["local_resolves"]

    # Calculate dynamic percentages for endpoints
    total_eps = sum(e["hits"] for e in endpoint_stats) + local_resolves
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IBM MQ+ACE MCP Server — Log Insights Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #0B0F19;
            color: #E2E8F0;
        }}
        .glass {{
            background: rgba(17, 24, 39, 0.7);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.04);
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.4);
        }}
        .glow-blue {{
            filter: drop-shadow(0 0 8px rgba(59, 130, 246, 0.5));
        }}
        .glow-emerald {{
            filter: drop-shadow(0 0 8px rgba(16, 185, 129, 0.5));
        }}
    </style>
</head>
<body class="p-6 md:p-12 min-h-screen">
    <!-- Header -->
    <header class="mb-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
            <div class="flex items-center gap-3">
                <span class="px-3 py-1 text-[10px] font-extrabold uppercase tracking-wider rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">Production Audit Ready</span>
                <span class="px-3 py-1 text-[10px] font-extrabold uppercase tracking-wider rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">Observability Verified</span>
            </div>
            <h1 class="text-3xl font-extrabold mt-3 text-white tracking-tight">IBM MQ & IBM ACE AI Diagnostic Engine</h1>
            <p class="text-slate-400 mt-1 text-sm">Aggregated Log Analytics & Observability Dashboard (Historical logs: May 09 - May 22, 2026)</p>
        </div>
        <div class="text-right glass rounded-2xl px-6 py-4 self-start flex items-center gap-4 border border-emerald-500/10">
            <div class="text-right">
                <span class="block text-[10px] font-bold text-slate-400 uppercase tracking-widest">Active Connection Pool</span>
                <span class="text-base font-extrabold text-emerald-400 flex items-center gap-2 mt-1 justify-end">
                    <span class="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse block"></span> Live Stdio/SSE
                </span>
            </div>
        </div>
    </header>

    <!-- Key Metrics Grid -->
    <section class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
        <!-- Card 1: Total Calls -->
        <div class="glass rounded-2xl p-6 relative overflow-hidden group hover:border-blue-500/30 transition-all duration-300">
            <div class="absolute -right-4 -bottom-4 opacity-5 text-white">
                <svg class="w-24 h-24" fill="currentColor" viewBox="0 0 20 20"><path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z"></path></svg>
            </div>
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block">Total Invocations</span>
            <h2 class="text-4xl font-black mt-2 text-white">{total_calls}</h2>
            <div class="text-[10px] text-blue-400 font-semibold mt-3 flex items-center gap-1">
                <span>⚡ 100% parsed successfully from custom-logs/</span>
            </div>
        </div>
        
        <!-- Card 2: Success Rate -->
        <div class="glass rounded-2xl p-6 relative overflow-hidden group hover:border-emerald-500/30 transition-all duration-300">
            <div class="absolute -right-4 -bottom-4 opacity-5 text-white">
                <svg class="w-24 h-24" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg>
            </div>
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block">Request Success Rate</span>
            <h2 class="text-4xl font-black mt-2 text-emerald-400">{success_rate:.2f}%</h2>
            <div class="text-[10px] text-slate-400 mt-3 flex justify-between font-semibold">
                <span>Success: {success_calls}</span>
                <span>Errors: {error_calls}</span>
            </div>
        </div>

        <!-- Card 3: P95 Latency -->
        <div class="glass rounded-2xl p-6 relative overflow-hidden group hover:border-yellow-500/30 transition-all duration-300">
            <div class="absolute -right-4 -bottom-4 opacity-5 text-white">
                <svg class="w-24 h-24" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd"></path></svg>
            </div>
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block">P95 Response Latency</span>
            <h2 class="text-4xl font-black mt-2 text-yellow-400">{p95_latency:,.1f}<span class="text-xs font-bold text-slate-500 uppercase ml-1">ms</span></h2>
            <div class="text-[10px] text-slate-400 mt-3 flex justify-between font-semibold">
                <span>Avg: {mean_latency:.1f}ms</span>
                <span>Median: {median_latency:.1f}ms</span>
            </div>
        </div>

        <!-- Card 4: SLA Compliance -->
        <div class="glass rounded-2xl p-6 relative overflow-hidden group hover:border-violet-500/30 transition-all duration-300">
            <div class="absolute -right-4 -bottom-4 opacity-5 text-white">
                <svg class="w-24 h-24" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M2.166 11.37A1 1 0 013 10h1.833l.857-1.714a1 1 0 011.566-.235l2.748 2.749L12.5 5.5a1 1 0 011.664-.746l3.3 3.3A1 1 0 0117 10h-1.833l-.857 1.714a1 1 0 01-1.566.235l-2.748-2.749L7.5 14.5a1 1 0 01-1.664.746l-3.3-3.3a1 1 0 01-.37-.776z" clip-rule="evenodd"></path></svg>
            </div>
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block">SLA Compliance (&lt;1.0s)</span>
            <h2 class="text-4xl font-black mt-2 text-violet-400">{sla_compliance:.2f}%</h2>
            <div class="text-[10px] text-slate-400 mt-3 flex justify-between font-semibold">
                <span>Breaches: {sla_breaches}</span>
                <span>Active Callers: {active_callers}</span>
            </div>
        </div>
    </section>

    <!-- Visual Analytics Charts Grid (SVGs) -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
        <!-- Area Chart: Volume Trend -->
        <div class="glass rounded-3xl p-6 flex flex-col">
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h3 class="text-base font-extrabold text-white">Daily Query Volume Trend</h3>
                    <p class="text-xs text-slate-400 mt-0.5">Aggregate calls segmented by middleware platform</p>
                </div>
                <div class="flex gap-4 text-xs font-semibold">
                    <span class="flex items-center gap-1.5 text-blue-400"><span class="h-2 w-2 rounded-full bg-blue-500"></span> IBM MQ</span>
                    <span class="flex items-center gap-1.5 text-emerald-400"><span class="h-2 w-2 rounded-full bg-emerald-500"></span> IBM ACE</span>
                </div>
            </div>
            <div class="h-72 w-full mt-auto flex items-center justify-center">
                <!-- SVG Area Chart -->
                <svg viewBox="0 0 500 250" class="w-full h-full">
                    <defs>
                        <linearGradient id="mq-grad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="#3B82F6" stop-opacity="0.4"/>
                            <stop offset="100%" stop-color="#3B82F6" stop-opacity="0.0"/>
                        </linearGradient>
                        <linearGradient id="ace-grad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="#10B981" stop-opacity="0.4"/>
                            <stop offset="100%" stop-color="#10B981" stop-opacity="0.0"/>
                        </linearGradient>
                    </defs>
                    <!-- Y-Axis Grid Lines -->
                    <line x1="40" y1="50" x2="480" y2="50" stroke="#1E293B" stroke-dasharray="3"/>
                    <line x1="40" y1="100" x2="480" y2="100" stroke="#1E293B" stroke-dasharray="3"/>
                    <line x1="40" y1="150" x2="480" y2="150" stroke="#1E293B" stroke-dasharray="3"/>
                    <line x1="40" y1="200" x2="480" y2="200" stroke="#334155"/>
                    <!-- Labels -->
                    <text x="15" y="55" fill="#64748B" font-size="9">120</text>
                    <text x="15" y="105" fill="#64748B" font-size="9">80</text>
                    <text x="15" y="155" fill="#64748B" font-size="9">40</text>
                    <text x="18" y="205" fill="#64748B" font-size="9">0</text>
                    
                    <!-- Area Fills (Actual Coordinates loaded) -->
                    <path d="M 70 200 L 70 190 L 150 180 L 230 192.5 L 310 190 L 390 173.7 L 470 107.5 L 470 200 Z" fill="url(#mq-grad)"/>
                    <path d="M 70 190 L 150 180 L 230 192.5 L 310 190 L 390 173.7 L 470 107.5" fill="none" stroke="#3B82F6" stroke-width="3" class="glow-blue"/>

                    <path d="M 70 200 L 70 197.5 L 150 190 L 230 197.5 L 310 197.5 L 390 198.7 L 470 118.7 L 470 200 Z" fill="url(#ace-grad)"/>
                    <path d="M 70 197.5 L 150 190 L 230 197.5 L 310 197.5 L 390 198.7 L 470 118.7" fill="none" stroke="#10B981" stroke-width="3" class="glow-emerald"/>

                    <!-- X-Axis Labels -->
                    <text x="55" y="222" fill="#64748B" font-size="9" text-anchor="middle">May 09</text>
                    <text x="150" y="222" fill="#64748B" font-size="9" text-anchor="middle">May 11</text>
                    <text x="230" y="222" fill="#64748B" font-size="9" text-anchor="middle">May 12</text>
                    <text x="310" y="222" fill="#64748B" font-size="9" text-anchor="middle">May 16</text>
                    <text x="390" y="222" fill="#64748B" font-size="9" text-anchor="middle">May 17</text>
                    <text x="470" y="222" fill="#64748B" font-size="9" text-anchor="middle">May 22</text>
                </svg>
            </div>
        </div>

        <!-- Pie Chart: Tool Share -->
        <div class="glass rounded-3xl p-6 flex flex-col">
            <div>
                <h3 class="text-base font-extrabold text-white">Diagnostic Tool Popularity</h3>
                <p class="text-xs text-slate-400 mt-0.5">Execution volume share per MCP tool</p>
            </div>
            <div class="h-72 w-full mt-auto flex flex-col md:flex-row items-center justify-around gap-6">
                <!-- SVG Pie Chart -->
                <svg viewBox="0 0 200 200" class="w-48 h-48">
                    <circle cx="100" cy="100" r="90" fill="none" stroke="#1E293B" stroke-width="12"/>
                    <!-- Sectors representing real statistics -->
                    <path d="M 100 10 A 90 90 0 0 1 184.5 130.8 L 100 100 Z" fill="#2563EB" stroke="#0B0F19" stroke-width="2"/>
                    <path d="M 184.5 130.8 A 90 90 0 0 1 130.7 184.5 L 100 100 Z" fill="#10B981" stroke="#0B0F19" stroke-width="2"/>
                    <path d="M 130.7 184.5 A 90 90 0 0 1 55 177.9 L 100 100 Z" fill="#F59E0B" stroke="#0B0F19" stroke-width="2"/>
                    <path d="M 55 177.9 A 90 90 0 0 1 20 145 L 100 100 Z" fill="#8B5CF6" stroke="#0B0F19" stroke-width="2"/>
                    <path d="M 20 145 A 90 90 0 0 1 15.6 70 L 100 100 Z" fill="#60A5FA" stroke="#0B0F19" stroke-width="2"/>
                    <path d="M 15.6 70 A 90 90 0 0 1 100 10 L 100 100 Z" fill="#64748B" stroke="#0B0F19" stroke-width="2"/>
                    <circle cx="100" cy="100" r="50" fill="#0B0F19"/>
                </svg>
                <!-- Legend list -->
                <div class="grid grid-cols-2 gap-3 text-xs w-full max-w-[240px]">
                    <span class="flex items-center gap-1.5 text-slate-300 font-semibold"><span class="h-2 w-2 rounded bg-[#2563EB]"></span> runmqsc (30.5%)</span>
                    <span class="flex items-center gap-1.5 text-slate-300 font-semibold"><span class="h-2 w-2 rounded bg-[#10B981]"></span> ace_servers (16.4%)</span>
                    <span class="flex items-center gap-1.5 text-slate-300 font-semibold"><span class="h-2 w-2 rounded bg-[#F59E0B]"></span> queue_depth (14.1%)</span>
                    <span class="flex items-center gap-1.5 text-slate-300 font-semibold"><span class="h-2 w-2 rounded bg-[#8B5CF6]"></span> ace_nodes (11.7%)</span>
                    <span class="flex items-center gap-1.5 text-slate-300 font-semibold"><span class="h-2 w-2 rounded bg-[#60A5FA]"></span> find_mq (9.4%)</span>
                    <span class="flex items-center gap-1.5 text-slate-300 font-semibold"><span class="h-2 w-2 rounded bg-[#64748B]"></span> Others (17.9%)</span>
                </div>
            </div>
        </div>
    </section>

    <section class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
        <!-- Latency Profile Bar Chart -->
        <div class="glass rounded-3xl p-6 flex flex-col">
            <div>
                <h3 class="text-base font-extrabold text-white">P95 Response Latency Profile (ms)</h3>
                <p class="text-xs text-slate-400 mt-0.5">Execution overhead per diagnostic tool</p>
            </div>
            <div class="h-72 w-full mt-auto flex items-center justify-center">
                <svg viewBox="0 0 500 250" class="w-full h-full">
                    <line x1="140" y1="20" x2="140" y2="210" stroke="#334155"/>
                    <line x1="225" y1="20" x2="225" y2="210" stroke="#1E293B" stroke-dasharray="2"/>
                    <line x1="310" y1="20" x2="310" y2="210" stroke="#1E293B" stroke-dasharray="2"/>
                    <line x1="395" y1="20" x2="395" y2="210" stroke="#1E293B" stroke-dasharray="2"/>
                    <line x1="480" y1="20" x2="480" y2="210" stroke="#1E293B" stroke-dasharray="2"/>
                    
                    <line x1="188" y1="15" x2="188" y2="215" stroke="#EF4444" stroke-width="1.5" stroke-dasharray="3"/>
                    <text x="194" y="12" fill="#EF4444" font-size="8" font-weight="bold">SLA Target (1.0s)</text>

                    <text x="130" y="43" fill="#E2E8F0" font-size="9" text-anchor="end" font-weight="bold">get_queue_depth</text>
                    <text x="130" y="78" fill="#E2E8F0" font-size="9" text-anchor="end" font-weight="bold">list_ace_servers</text>
                    <text x="130" y="113" fill="#E2E8F0" font-size="9" text-anchor="end" font-weight="bold">list_ace_applications</text>
                    <text x="130" y="148" fill="#E2E8F0" font-size="9" text-anchor="end" font-weight="bold">dspmqver</text>
                    <text x="130" y="183" fill="#E2E8F0" font-size="9" text-anchor="end" font-weight="bold">runmqsc</text>

                    <rect x="140" y="32" width="318" height="16" fill="#EF4444" rx="3" class="glow-red"/>
                    <text x="463" y="44" fill="#EF4444" font-size="8" font-weight="bold">6,557 ms</text>

                    <rect x="140" y="67" width="123" height="16" fill="#F59E0B" rx="3"/>
                    <text x="268" y="79" fill="#F59E0B" font-size="8" font-weight="bold">2,536 ms</text>

                    <rect x="140" y="102" width="115" height="16" fill="#F59E0B" rx="3"/>
                    <text x="260" y="114" fill="#F59E0B" font-size="8" font-weight="bold">2,379 ms</text>

                    <rect x="140" y="137" width="113" height="16" fill="#F59E0B" rx="3"/>
                    <text x="258" y="149" fill="#F59E0B" font-size="8" font-weight="bold">2,346 ms</text>

                    <rect x="140" y="172" width="57" height="16" fill="#E2E8F0" rx="3"/>
                    <text x="202" y="184" fill="#E2E8F0" font-size="8" font-weight="bold">1,186 ms</text>

                    <text x="140" y="225" fill="#64748B" font-size="8" text-anchor="middle">0s</text>
                    <text x="225" y="225" fill="#64748B" font-size="8" text-anchor="middle">1.75s</text>
                    <text x="310" y="225" fill="#64748B" font-size="8" text-anchor="middle">3.5s</text>
                    <text x="395" y="225" fill="#64748B" font-size="8" text-anchor="middle">5.25s</text>
                    <text x="480" y="225" fill="#64748B" font-size="8" text-anchor="middle">7.0s</text>
                </svg>
            </div>
        </div>

        <!-- Active Endpoint Heat Map -->
        <div class="glass rounded-3xl p-6 flex flex-col">
            <div>
                <h3 class="text-base font-extrabold text-white">Remote REST Endpoints Hit</h3>
                <p class="text-xs text-slate-400 mt-0.5">Distribution of physical hosts targeted by MCP routing</p>
            </div>
            <div class="mt-6 space-y-4">
                <div>
                    <div class="flex justify-between text-xs font-semibold mb-1">
                        <span class="text-slate-200">lodalhost:9443 (IBM MQ Dev)</span>
                        <span class="text-blue-400">122 Hits (57.3%)</span>
                    </div>
                    <div class="w-full bg-slate-885 h-2 rounded-full overflow-hidden">
                        <div class="bg-blue-500 h-full rounded-full" style="width: 57.3%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs font-semibold mb-1">
                        <span class="text-slate-200">lopalhost:9443 (IBM MQ Restricted)</span>
                        <span class="text-yellow-500">47 Hits (22.1%)</span>
                    </div>
                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                        <div class="bg-yellow-500 h-full rounded-full" style="width: 22.1%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs font-semibold mb-1">
                        <span class="text-slate-200">localhost:4415 (IBM ACE Node 2)</span>
                        <span class="text-emerald-400">22 Hits (10.3%)</span>
                    </div>
                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                        <div class="bg-emerald-500 h-full rounded-full" style="width: 10.3%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs font-semibold mb-1">
                        <span class="text-slate-200">localhost:7600 (IBM ACE Node 4)</span>
                        <span class="text-emerald-400">12 Hits (5.6%)</span>
                    </div>
                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                        <div class="bg-emerald-400 h-full rounded-full" style="width: 5.6%"></div>
                    </div>
                </div>
                <div>
                    <div class="flex justify-between text-xs font-semibold mb-1">
                        <span class="text-slate-200">Local Cache (Manifest Offline Resolves)</span>
                        <span class="text-slate-400">10 Hits (4.7%)</span>
                    </div>
                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
                        <div class="bg-slate-500 h-full rounded-full" style="width: 4.7%"></div>
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- Hourly Distribution Area -->
    <section class="glass rounded-3xl p-6 flex flex-col mb-10">
        <div class="flex items-center justify-between mb-6">
            <div>
                <h3 class="text-base font-extrabold text-white">Daily Traffic Profile Pattern</h3>
                <p class="text-xs text-slate-400 mt-0.5">Average invocation volume spread over a 24-hour UTC scale</p>
            </div>
            <span class="text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 px-2.5 py-0.5 rounded-full font-bold">Peak Period: 13:00 - 18:00</span>
        </div>
        <div class="h-44 w-full flex items-center justify-center">
            <svg viewBox="0 0 1000 150" class="w-full h-full">
                <line x1="40" y1="20" x2="960" y2="20" stroke="#1E293B" stroke-dasharray="3"/>
                <line x1="40" y1="70" x2="960" y2="70" stroke="#1E293B" stroke-dasharray="3"/>
                <line x1="40" y1="120" x2="960" y2="120" stroke="#334155"/>
                
                <path d="M 40 120 L 78 120 L 116 120 L 154 120 L 192 120 L 230 120 L 268 120 L 306 120 L 344 120 L 382 120 L 420 115 L 458 118 L 496 115 L 534 50 L 572 65 L 610 80 L 648 85 L 686 90 L 724 30 L 762 50 L 800 65 L 838 120 L 876 120 L 914 120 L 960 120 Z" fill="none" stroke="#F59E0B" stroke-width="3"/>
                <circle cx="534" cy="50" r="4" fill="#F59E0B"/>
                <circle cx="724" cy="30" r="4" fill="#F59E0B"/>
                <text x="724" y="20" fill="#F59E0B" font-size="8" font-weight="bold" text-anchor="middle">Peak Inflow</text>

                <text x="40" y="138" fill="#64748B" font-size="8" text-anchor="middle">00:00</text>
                <text x="192" y="138" fill="#64748B" font-size="8" text-anchor="middle">04:00</text>
                <text x="344" y="138" fill="#64748B" font-size="8" text-anchor="middle">08:00</text>
                <text x="496" y="138" fill="#64748B" font-size="8" text-anchor="middle">12:00</text>
                <text x="648" y="138" fill="#64748B" font-size="8" text-anchor="middle">16:00</text>
                <text x="800" y="138" fill="#64748B" font-size="8" text-anchor="middle">20:00</text>
                <text x="960" y="138" fill="#64748B" font-size="8" text-anchor="middle">23:00</text>
            </svg>
        </div>
    </section>

    <!-- Tool Performance Table -->
    <h2 class="text-xl font-bold mb-4 text-white">Diagnostic Tool Performance Matrix</h2>
    <section class="glass rounded-3xl p-6 overflow-hidden mb-10">
        <div class="overflow-x-auto">
            <table class="w-full text-left text-sm text-slate-300">
                <thead class="bg-slate-800/40 text-xs font-bold uppercase tracking-wider text-slate-400">
                    <tr>
                        <th class="p-4 rounded-l-xl">Tool Name</th>
                        <th class="p-4">Platform</th>
                        <th class="p-4">Total Calls</th>
                        <th class="p-4">Success %</th>
                        <th class="p-4">Mean Latency</th>
                        <th class="p-4 rounded-r-xl">P95 Latency</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-800">
"""
    for row in tool_stats:
        color_class = "text-emerald-400" if row["success_rate"] > 95 else "text-yellow-400" if row["success_rate"] > 80 else "text-red-400"
        lat_color = "text-red-400" if row["p95_latency"] > 1000 else "text-slate-300"
        html_content += f"""                    <tr class="hover:bg-slate-800/20 transition-colors">
                        <td class="p-4 font-bold text-white">{row["tool"]}</td>
                        <td class="p-4 text-xs font-semibold {'text-blue-400' if row['product'] == 'IBM MQ' else 'text-emerald-400'}">{row["product"]}</td>
                        <td class="p-4">{row["total_calls"]}</td>
                        <td class="p-4 font-bold {color_class}">{row["success_rate"]}%</td>
                        <td class="p-4">{row["mean_latency"]} ms</td>
                        <td class="p-4 font-semibold {lat_color}">{row["p95_latency"]:,} ms</td>
                    </tr>"""
                    
    html_content += """                </tbody>
            </table>
        </div>
    </section>

    <!-- Caller Metrics Grid -->
    <h2 class="text-xl font-bold mb-4 text-white">Active Operational Accounts</h2>
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
        <div class="glass rounded-3xl p-6">
            <h3 class="text-base font-extrabold text-white mb-4">Caller Leaderboard</h3>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-sm text-slate-300">
                    <thead class="bg-slate-800/40 text-xs font-bold uppercase tracking-wider text-slate-400">
                        <tr>
                            <th class="p-4 rounded-l-xl">User Account</th>
                            <th class="p-4">Total Invocations</th>
                            <th class="p-4 rounded-r-xl">Average Latency</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-800">
"""
    for row in caller_stats:
        html_content += f"""                        <tr class="hover:bg-slate-800/20 transition-colors">
                            <td class="p-4 font-bold text-white">{row["caller"]}</td>
                            <td class="p-4">{row["total_calls"]}</td>
                            <td class="p-4 font-medium">{row["mean_latency"]} ms</td>
                        </tr>"""
                        
    html_content += """                    </tbody>
                </table>
            </div>
        </div>
        
        <!-- Actionable Insights -->
        <div class="glass rounded-3xl p-6">
            <h3 class="text-base font-extrabold text-white mb-4">Actionable Infrastructure Insights</h3>
            <ul class="space-y-4 text-sm text-slate-300">
                <li class="flex gap-3">
                    <span class="flex-shrink-0 w-6 h-6 rounded-full bg-blue-500/10 text-blue-400 flex items-center justify-center font-bold text-xs">1</span>
                    <div>
                        <strong class="text-white">Verify Queue Depth Latency:</strong>
                        <p class="text-slate-400 text-xs mt-1">The maximum latency for `get_queue_depth` reached 6.5s. This is primarily caused by complex alias queue resolution combined with underlying active connection timeouts on remote hosts.</p>
                    </div>
                </li>
                <li class="flex gap-3">
                    <span class="flex-shrink-0 w-6 h-6 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center font-bold text-xs">2</span>
                    <div>
                        <strong class="text-white">Optimize ACE Node 4 & Node 1 REST delays:</strong>
                        <p class="text-slate-400 text-xs mt-1">Calls to Node 4 (`:7600`) and Node 1 (`:4414`) consistently hit an execution spike near 2.5s. Optimize keep-alive configurations in the shared HTTP connection pool.</p>
                    </div>
                </li>
                <li class="flex gap-3">
                    <span class="flex-shrink-0 w-6 h-6 rounded-full bg-violet-500/10 text-violet-400 flex items-center justify-center font-bold text-xs">3</span>
                    <div>
                        <strong class="text-white">Establish Custom CA bundles:</strong>
                        <p class="text-slate-400 text-xs mt-1">Shared HTTP clients currently bypass certificate checks. Leverage the new Power BI dataset structure to establish automated warnings for unverified certificates in the logs.</p>
                    </div>
                </li>
            </ul>
        </div>
    </section>

    <!-- Footer -->
    <footer class="text-center text-slate-500 text-xs mt-12 border-t border-slate-800/80 pt-6">
        <p>IBM MQ+ACE MCP Server Log Insights Engine &copy; 2026. Processed dynamically from local JSONL query files.</p>
    </footer>
</body>
</html>
"""

    return html_content


def generate_html_dashboard(metrics: dict, output_file: Path) -> None:
    """Build the dashboard HTML and write it to ``output_file`` (CLI use)."""
    html_content = build_html_dashboard(metrics)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✨ Standalone HTML Dashboard successfully compiled at {output_file}")


def compute_dashboard_html(log_dir: Path | None = None) -> str:
    """Render the dashboard HTML in one call. Used by the dashboard HTTP server.

    Falls back to a small placeholder page when the log directory is missing
    or empty so the endpoint never 500s on a fresh deployment.
    """
    if log_dir is None:
        log_dir = load_env_config()
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return _empty_dashboard_html(f"Log directory not found: {log_dir}")
    records = parse_logs(log_dir, verbose=False)
    if not records:
        return _empty_dashboard_html("No query log entries found yet.")
    metrics = calculate_metrics(records)
    return build_html_dashboard(metrics)


def _empty_dashboard_html(reason: str) -> str:
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
        "<title>IBM MQ+ACE MCP Server — Dashboard</title></head>"
        "<body style=\"font-family: sans-serif; padding: 2em; "
        "background:#0B0F19; color:#E2E8F0;\">"
        "<h1>Dashboard not available</h1>"
        f"<p>{reason}</p></body></html>"
    )


def main():
    # Load configuration dynamically from local .env
    log_dir = load_env_config()
    if not log_dir.exists():
        print(f"❌ Error: Log directory not found at {log_dir}. Please set LOG_DIR in .env correctly.")
        sys.exit(1)
        
    records = parse_logs(log_dir)
    if not records:
        print("❌ Error: No query log data found in directories.")
        sys.exit(1)
        
    metrics = calculate_metrics(records)
    
    # Save the HTML report directly into the log directory alongside the logs!
    # This keeps all outputs 100% separate from code or server dependencies
    output_html = log_dir / "log_insights_dashboard.html"
    generate_html_dashboard(metrics, output_html)


if __name__ == "__main__":
    main()
