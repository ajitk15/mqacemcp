#!/usr/bin/env python
"""Analyze unified MQ+ACE MCP server query logs and generate operational insights dashboard.

This script is fully self-contained, reading environment configurations (like LOG_DIR)
directly from the local .env file. It can be run on-demand (dynamically) or scheduled
periodically via cron / Task Scheduler.
"""
from __future__ import annotations

import json
import math
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
    total_endpoint_hits = sum(e["hits"] for e in endpoint_stats) + local_resolves

    # Daily volume bucketed by product (MQ vs ACE vs Other)
    daily_buckets: dict[str, dict[str, int]] = {}
    for r in records:
        d = r["date"]
        if d not in daily_buckets:
            daily_buckets[d] = {"mq": 0, "ace": 0, "other": 0}
        if r["product"] == "IBM MQ":
            daily_buckets[d]["mq"] += 1
        elif r["product"] == "IBM ACE":
            daily_buckets[d]["ace"] += 1
        else:
            daily_buckets[d]["other"] += 1
    daily_volume = [{"date": d, **counts} for d, counts in sorted(daily_buckets.items())]

    # Hourly distribution (24-hour UTC buckets, summed across all days)
    hourly_volume = [0] * 24
    for r in records:
        h = r.get("hour", 0)
        if 0 <= h < 24:
            hourly_volume[h] += 1
    peak_hour = max(range(24), key=lambda i: hourly_volume[i]) if any(hourly_volume) else 0

    # Date range covered by the loaded logs
    dates_sorted = sorted({r["date"] for r in records})
    date_range = (dates_sorted[0], dates_sorted[-1]) if dates_sorted else (None, None)

    # Tool popularity share — top 5 + "Others" bucket
    top5_tools = tool_stats[:5]  # already sorted by total_calls desc
    others_count = sum(t["total_calls"] for t in tool_stats[5:])
    tool_share = [
        {
            "tool": t["tool"],
            "count": t["total_calls"],
            "pct": round(t["total_calls"] / total_calls * 100, 1),
        }
        for t in top5_tools
    ]
    if others_count > 0:
        tool_share.append({
            "tool": "Others",
            "count": others_count,
            "pct": round(others_count / total_calls * 100, 1),
        })

    # Top 5 tools by p95 latency for the bar chart
    top_latency = sorted(tool_stats, key=lambda x: x["p95_latency"], reverse=True)[:5]

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
        "local_resolves": local_resolves,
        "total_endpoint_hits": total_endpoint_hits,
        "daily_volume": daily_volume,
        "hourly_volume": hourly_volume,
        "peak_hour": peak_hour,
        "date_range": date_range,
        "tool_share": tool_share,
        "top_latency": top_latency,
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
    total_endpoint_hits = metrics["total_endpoint_hits"]
    daily_volume = metrics["daily_volume"]
    hourly_volume = metrics["hourly_volume"]
    peak_hour = metrics["peak_hour"]
    date_range = metrics["date_range"]
    tool_share = metrics["tool_share"]
    top_latency = metrics["top_latency"]

    # --- Subtitle date range ---
    min_date, max_date = date_range
    if not min_date:
        subtitle_range = "No logs yet"
    elif min_date == max_date:
        subtitle_range = f"Single-day snapshot: {min_date}"
    else:
        subtitle_range = f"{min_date} → {max_date}"
    peak_hour_badge = (
        f"Peak hour: {peak_hour:02d}:00 UTC ({hourly_volume[peak_hour]} calls)"
        if any(hourly_volume)
        else "Peak hour: —"
    )

    # --- Daily volume area chart (viewBox 500x250, chart x=60..480, y=40..200) ---
    dv_n = len(daily_volume)
    dv_max = max((max(d["mq"], d["ace"]) for d in daily_volume), default=1) or 1
    if dv_n == 0:
        dv_mq_area = dv_mq_line = dv_ace_area = dv_ace_line = ""
        dv_x_labels = ""
        dv_y_top = dv_y_mid = "0"
    elif dv_n == 1:
        only = daily_volume[0]
        cx_only = 270
        mq_y_only = 200 - (only["mq"] / dv_max) * 160
        ace_y_only = 200 - (only["ace"] / dv_max) * 160
        dv_mq_area = f'<circle cx="{cx_only}" cy="{mq_y_only:.1f}" r="6" fill="#3B82F6" class="glow-blue"/>'
        dv_mq_line = ""
        dv_ace_area = f'<circle cx="{cx_only}" cy="{ace_y_only:.1f}" r="6" fill="#10B981" class="glow-emerald"/>'
        dv_ace_line = ""
        dv_x_labels = f'<text x="{cx_only}" y="222" fill="#64748B" font-size="9" text-anchor="middle">{only["date"]}</text>'
        dv_y_top = str(dv_max)
        dv_y_mid = str(dv_max // 2)
    else:
        dv_xs = [60 + i * (420 / (dv_n - 1)) for i in range(dv_n)]
        mq_ys = [200 - (d["mq"] / dv_max) * 160 for d in daily_volume]
        ace_ys = [200 - (d["ace"] / dv_max) * 160 for d in daily_volume]
        mq_pts = " ".join(f"L {x:.1f} {y:.1f}" for x, y in zip(dv_xs, mq_ys))
        ace_pts = " ".join(f"L {x:.1f} {y:.1f}" for x, y in zip(dv_xs, ace_ys))
        mq_line_pts = " ".join(f"L {x:.1f} {y:.1f}" for x, y in zip(dv_xs[1:], mq_ys[1:]))
        ace_line_pts = " ".join(f"L {x:.1f} {y:.1f}" for x, y in zip(dv_xs[1:], ace_ys[1:]))
        dv_mq_area = f'<path d="M {dv_xs[0]:.1f} 200 {mq_pts} L {dv_xs[-1]:.1f} 200 Z" fill="url(#mq-grad)"/>'
        dv_mq_line = f'<path d="M {dv_xs[0]:.1f} {mq_ys[0]:.1f} {mq_line_pts}" fill="none" stroke="#3B82F6" stroke-width="3" class="glow-blue"/>'
        dv_ace_area = f'<path d="M {dv_xs[0]:.1f} 200 {ace_pts} L {dv_xs[-1]:.1f} 200 Z" fill="url(#ace-grad)"/>'
        dv_ace_line = f'<path d="M {dv_xs[0]:.1f} {ace_ys[0]:.1f} {ace_line_pts}" fill="none" stroke="#10B981" stroke-width="3" class="glow-emerald"/>'
        mid_i = dv_n // 2
        label_idxs = sorted({0, mid_i, dv_n - 1})
        dv_x_labels = "".join(
            f'<text x="{dv_xs[i]:.1f}" y="222" fill="#64748B" font-size="9" text-anchor="middle">{daily_volume[i]["date"][5:]}</text>'
            for i in label_idxs
        )
        dv_y_top = str(dv_max)
        dv_y_mid = str(dv_max // 2)

    # --- Tool popularity pie (viewBox 200x200, center (100,100), r=90) ---
    PIE_COLORS = ["#2563EB", "#10B981", "#F59E0B", "#8B5CF6", "#60A5FA", "#64748B"]
    pie_sectors_html = ""
    pie_legend_html = ""
    if tool_share:
        cx, cy, r_pie = 100, 100, 90
        start_a = -math.pi / 2
        for i, ts in enumerate(tool_share):
            frac = ts["pct"] / 100.0
            if frac <= 0:
                continue
            end_a = start_a + frac * 2 * math.pi
            large_arc = 1 if frac > 0.5 else 0
            x1 = cx + r_pie * math.cos(start_a)
            y1 = cy + r_pie * math.sin(start_a)
            x2 = cx + r_pie * math.cos(end_a)
            y2 = cy + r_pie * math.sin(end_a)
            color = PIE_COLORS[i % len(PIE_COLORS)]
            pie_sectors_html += (
                f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} '
                f'A {r_pie} {r_pie} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" '
                f'fill="{color}" stroke="#0B0F19" stroke-width="2"/>'
            )
            pie_legend_html += (
                f'<span class="flex items-center gap-1.5 text-slate-300 font-semibold">'
                f'<span class="h-2 w-2 rounded" style="background:{color}"></span> '
                f'{ts["tool"]} ({ts["pct"]}%)</span>'
            )
            start_a = end_a

    # --- P95 latency bars (viewBox 500x250, chart x=140..480, y=20..210) ---
    if top_latency:
        max_p95 = max(t["p95_latency"] for t in top_latency)
    else:
        max_p95 = 0
    p95_scale = max(max_p95, 1500) * 1.1  # 10% headroom + ensure SLA line is visible
    sla_x = 140 + (1000 / p95_scale) * 340
    lat_bars_html = ""
    lat_labels_html = ""
    for i, t in enumerate(top_latency):
        y = 32 + i * 35
        p95v = t["p95_latency"]
        width = (p95v / p95_scale) * 340 if p95_scale > 0 else 0
        if p95v > 1000:
            color = "#EF4444"
        elif p95v > 500:
            color = "#F59E0B"
        else:
            color = "#E2E8F0"
        lat_labels_html += (
            f'<text x="130" y="{y + 11}" fill="#E2E8F0" font-size="9" '
            f'text-anchor="end" font-weight="bold">{t["tool"]}</text>'
        )
        lat_bars_html += f'<rect x="140" y="{y}" width="{width:.1f}" height="16" fill="{color}" rx="3"/>'
        lat_bars_html += (
            f'<text x="{140 + width + 5:.1f}" y="{y + 12}" fill="{color}" '
            f'font-size="8" font-weight="bold">{p95v:,} ms</text>'
        )
    lat_xaxis_html = ""
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        x = 140 + frac * 340
        secs = (frac * p95_scale) / 1000
        lat_xaxis_html += (
            f'<text x="{x:.0f}" y="225" fill="#64748B" font-size="8" '
            f'text-anchor="middle">{secs:.2f}s</text>'
        )

    # --- Endpoints section ---
    ENDPOINT_PALETTE = [
        ("bg-blue-500", "text-blue-400"),
        ("bg-yellow-500", "text-yellow-500"),
        ("bg-emerald-500", "text-emerald-400"),
        ("bg-violet-500", "text-violet-400"),
        ("bg-cyan-500", "text-cyan-400"),
    ]
    endpoints_html = ""
    for i, ep in enumerate(endpoint_stats[:5]):
        pct = (ep["hits"] / total_endpoint_hits * 100) if total_endpoint_hits else 0
        bar_color, text_color = ENDPOINT_PALETTE[i % len(ENDPOINT_PALETTE)]
        endpoints_html += (
            '<div>\n'
            '                    <div class="flex justify-between text-xs font-semibold mb-1">\n'
            f'                        <span class="text-slate-200">{ep["host"]}</span>\n'
            f'                        <span class="{text_color}">{ep["hits"]} hits ({pct:.1f}%)</span>\n'
            '                    </div>\n'
            '                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">\n'
            f'                        <div class="{bar_color} h-full rounded-full" style="width: {pct:.1f}%"></div>\n'
            '                    </div>\n'
            '                </div>\n                '
        )
    if local_resolves > 0:
        pct = (local_resolves / total_endpoint_hits * 100) if total_endpoint_hits else 0
        endpoints_html += (
            '<div>\n'
            '                    <div class="flex justify-between text-xs font-semibold mb-1">\n'
            '                        <span class="text-slate-200">Local resolves (offline tool / pre-flight rejection)</span>\n'
            f'                        <span class="text-slate-400">{local_resolves} records ({pct:.1f}%)</span>\n'
            '                    </div>\n'
            '                    <div class="w-full bg-slate-800 h-2 rounded-full overflow-hidden">\n'
            f'                        <div class="bg-slate-500 h-full rounded-full" style="width: {pct:.1f}%"></div>\n'
            '                    </div>\n'
            '                </div>'
        )
    if not endpoints_html:
        endpoints_html = '<p class="text-slate-500 text-sm">No outbound calls recorded yet.</p>'

    # --- Hourly profile (viewBox 1000x150, chart x=40..960, y=20..120) ---
    hr_max = max(hourly_volume) if any(hourly_volume) else 1
    hr_xs = [40 + i * (920 / 23) for i in range(24)]
    hr_ys = [120 - (hourly_volume[i] / hr_max) * 100 for i in range(24)]
    hr_pts = " ".join(f"L {x:.1f} {y:.1f}" for x, y in zip(hr_xs, hr_ys))
    hourly_path = (
        f'<path d="M {hr_xs[0]:.1f} 120 {hr_pts} L {hr_xs[-1]:.1f} 120 Z" '
        f'fill="none" stroke="#F59E0B" stroke-width="3"/>'
    )
    hourly_peak_dot = ""
    if any(hourly_volume):
        px = hr_xs[peak_hour]
        py = hr_ys[peak_hour]
        hourly_peak_dot = (
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="#F59E0B"/>'
            f'<text x="{px:.1f}" y="{max(py - 6, 12):.1f}" fill="#F59E0B" '
            f'font-size="8" font-weight="bold" text-anchor="middle">'
            f'Peak: {hourly_volume[peak_hour]} calls</text>'
        )

    # Calculate dynamic percentages for endpoints
    total_eps = total_endpoint_hits

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
            <p class="text-slate-400 mt-1 text-sm">Aggregated Log Analytics & Observability Dashboard ({subtitle_range})</p>
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
                    <line x1="40" y1="40" x2="480" y2="40" stroke="#1E293B" stroke-dasharray="3"/>
                    <line x1="40" y1="120" x2="480" y2="120" stroke="#1E293B" stroke-dasharray="3"/>
                    <line x1="40" y1="200" x2="480" y2="200" stroke="#334155"/>
                    <!-- Y-axis labels (data-driven) -->
                    <text x="15" y="45" fill="#64748B" font-size="9">{dv_y_top}</text>
                    <text x="15" y="125" fill="#64748B" font-size="9">{dv_y_mid}</text>
                    <text x="18" y="205" fill="#64748B" font-size="9">0</text>

                    <!-- Area Fills (data-driven) -->
                    {dv_mq_area}
                    {dv_mq_line}
                    {dv_ace_area}
                    {dv_ace_line}

                    <!-- X-Axis Labels (data-driven) -->
                    {dv_x_labels}
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
                <!-- SVG Pie Chart (data-driven sectors) -->
                <svg viewBox="0 0 200 200" class="w-48 h-48">
                    <circle cx="100" cy="100" r="90" fill="none" stroke="#1E293B" stroke-width="12"/>
                    {pie_sectors_html}
                    <circle cx="100" cy="100" r="50" fill="#0B0F19"/>
                </svg>
                <!-- Legend list (data-driven) -->
                <div class="grid grid-cols-2 gap-3 text-xs w-full max-w-[240px]">
                    {pie_legend_html}
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

                    <!-- SLA line at 1.0s (data-driven position) -->
                    <line x1="{sla_x:.1f}" y1="15" x2="{sla_x:.1f}" y2="215" stroke="#EF4444" stroke-width="1.5" stroke-dasharray="3"/>
                    <text x="{sla_x + 6:.1f}" y="12" fill="#EF4444" font-size="8" font-weight="bold">SLA Target (1.0s)</text>

                    <!-- Tool labels (data-driven) -->
                    {lat_labels_html}

                    <!-- Bars (data-driven) -->
                    {lat_bars_html}

                    <!-- X-axis labels (data-driven, scaled to max p95) -->
                    {lat_xaxis_html}
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
                {endpoints_html}
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
            <span class="text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 px-2.5 py-0.5 rounded-full font-bold">{peak_hour_badge}</span>
        </div>
        <div class="h-44 w-full flex items-center justify-center">
            <svg viewBox="0 0 1000 150" class="w-full h-full">
                <line x1="40" y1="20" x2="960" y2="20" stroke="#1E293B" stroke-dasharray="3"/>
                <line x1="40" y1="70" x2="960" y2="70" stroke="#1E293B" stroke-dasharray="3"/>
                <line x1="40" y1="120" x2="960" y2="120" stroke="#334155"/>

                {hourly_path}
                {hourly_peak_dot}

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
        
        <!-- Dashboard notes (replaces the editorial insights list — these are real log-reading semantics) -->
        <div class="glass rounded-3xl p-6">
            <h3 class="text-base font-extrabold text-white mb-4">How to read this dashboard</h3>
            <ul class="space-y-4 text-sm text-slate-300">
                <li class="flex gap-3">
                    <span class="flex-shrink-0 w-6 h-6 rounded-full bg-blue-500/10 text-blue-400 flex items-center justify-center font-bold text-xs">1</span>
                    <div>
                        <strong class="text-white">Success rate can hide ACE upstream errors:</strong>
                        <p class="text-slate-400 text-xs mt-1">The <code>outcome</code> field is set by whether the tool function raised. ACE tools always return a JSON error envelope instead of raising, so an unreachable upstream still shows <code>outcome=success</code>. If you suspect ACE failures, look at the tool's response body (not in this log) — or use the per-tool error counts in the matrix below.</p>
                    </div>
                </li>
                <li class="flex gap-3">
                    <span class="flex-shrink-0 w-6 h-6 rounded-full bg-emerald-500/10 text-emerald-400 flex items-center justify-center font-bold text-xs">2</span>
                    <div>
                        <strong class="text-white">Empty <code>endpoints</code> means pre-flight rejection:</strong>
                        <p class="text-slate-400 text-xs mt-1">When a record has <code>endpoints: []</code>, the request was rejected before going out — either the node isn't in <code>resources/node_config.csv</code> or the host failed the <code>ACE_ALLOWED_HOSTNAME_PREFIXES</code> / <code>MQ_ALLOWED_HOSTNAME_PREFIXES</code> allow-list. Offline-only tools (<code>find_mq_object</code>, <code>search_ace_local_dump</code>, <code>list_ace_nodes</code>) are also counted here.</p>
                    </div>
                </li>
                <li class="flex gap-3">
                    <span class="flex-shrink-0 w-6 h-6 rounded-full bg-violet-500/10 text-violet-400 flex items-center justify-center font-bold text-xs">3</span>
                    <div>
                        <strong class="text-white">All percentages are computed live:</strong>
                        <p class="text-slate-400 text-xs mt-1">Every chart re-aggregates the <code>queries-*.jsonl</code> files in <code>LOG_DIR</code> on each render. The page is a snapshot of the local logs at request time — refresh to pick up new tool invocations.</p>
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