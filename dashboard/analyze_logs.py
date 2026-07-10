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
from datetime import datetime, timedelta, timezone
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


# --- Color themes -----------------------------------------------------------
# Each theme is a dict of color tokens referenced everywhere the dashboard emits
# a color. `emerald` (success) and the inverted `slate` greys are identical in
# both themes — success always reads green; only the brand accents and the ACE
# series change. Select by color name via DASHBOARD_THEME (env) or the ?theme=
# URL override (values: "purple", "green").
THEMES: dict[str, dict] = {
    # Brand-purple palette (matches the chat UI).
    "purple": {
        "primary": "#A100FF",
        "primary_dark": "#7500C0",
        "accent_mid": "#8B2FD6",
        "heading": "#2A0A4A",
        "tooltip_time": "#E9D5FF",
        "pie_extra": "#C77DFF",
        "page_bg": "#F7F3FC",
        "border": "#E6D9F5",
        "hover": "#F1E9FB",
        "btn_text": "#6b21a8",
        "grid": "#EAE0F6",
        "baseline": "#D9C7EF",
        "glow_rgba": "161, 0, 255",
        "shadow_rgba": "117, 0, 192",
        "ace_series": "#10B981",       # ACE reads green (distinct from MQ purple)
        "ace_text_class": "text-emerald-400",
        "ace_bg_class": "bg-emerald-500",
        "cyan_remap": None,
    },
    # Green palette: MQ green, ACE teal, success still green.
    "green": {
        "primary": "#78BE20",
        "primary_dark": "#5A9E31",
        "accent_mid": "#6AAE2A",
        "heading": "#14380A",
        "tooltip_time": "#DDEFC2",
        "pie_extra": "#A9D66B",
        "page_bg": "#F1F7E9",
        "border": "#D7E8C2",
        "hover": "#E9F3D8",
        "btn_text": "#3F7A1E",
        "grid": "#E4EFD5",
        "baseline": "#CBE0B0",
        "glow_rgba": "120, 190, 32",
        "shadow_rgba": "90, 158, 49",
        "ace_series": "#06B6D4",       # ACE reads teal (distinct from MQ green)
        "ace_text_class": "text-cyan-400",
        "ace_bg_class": "bg-cyan-500",
        "cyan_remap": {"400": "#06B6D4", "500": "#06B6D4"},
    },
}

DEFAULT_THEME = "purple"


def _get_theme(name: str | None = None) -> dict:
    """Resolve a theme dict from an explicit name or DASHBOARD_THEME; falls back
    to the default on any unknown value."""
    key = (name or os.getenv("DASHBOARD_THEME", DEFAULT_THEME) or DEFAULT_THEME).strip().lower()
    return THEMES.get(key, THEMES[DEFAULT_THEME])


def _refresh_meta() -> str:
    """`<meta http-equiv=refresh>` tag so dashboard pages reload themselves.

    Interval (seconds) comes from MCP_DASHBOARD_REFRESH_SECONDS (default 60);
    0 disables auto-refresh. The tag lives on the *inner* per-server pages, so
    reloading happens inside the iframe and the selected tab in the wrapper is
    preserved.
    """
    try:
        secs = int(os.getenv("MCP_DASHBOARD_REFRESH_SECONDS", "60"))
    except ValueError:
        secs = 60
    return f'<meta http-equiv="refresh" content="{secs}">' if secs > 0 else ""


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


def compute_windowed_usage(records: list[dict]) -> dict:
    """Rolling hourly usage over 24h / 48h / 7d / 30d windows.

    Each window is a list of per-hour call counts, oldest→newest. Anchored to
    wall-clock now; if no record falls within the largest (30-day) window, fall
    back to anchoring on the most recent record so the charts still show data
    for historical/demo logs.
    """
    times: list[datetime] = []
    for r in records:
        raw = (r.get("ts") or "").replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        times.append(dt)
    if not times:
        return {"windows": {}, "note": ""}

    windows_def = [("24h", 24), ("48h", 48), ("7d", 168), ("month", 720)]
    now = datetime.now(timezone.utc)
    latest = max(times)
    largest_hours = max(h for _, h in windows_def)
    if any(t >= now - timedelta(hours=largest_hours) for t in times):
        anchor = now
        note = f"Anchored to current time ({anchor.strftime('%Y-%m-%d %H:%M')} UTC)."
    else:
        anchor = latest
        note = (
            f"No activity in the last {largest_hours // 24} days — anchored to the "
            f"latest recorded call ({anchor.strftime('%Y-%m-%d %H:%M')} UTC)."
        )

    anchor_hour = anchor.replace(minute=0, second=0, microsecond=0)
    out: dict[str, dict] = {}
    for key, hours in windows_def:
        start = anchor_hour - timedelta(hours=hours - 1)
        counts = [0] * hours
        for t in times:
            idx = int((t - start).total_seconds() // 3600)
            if 0 <= idx < hours:
                counts[idx] += 1
        labels = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
        out[key] = {"counts": counts, "labels": labels}
    return {"windows": out, "note": note}


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
        "usage_windows": compute_windowed_usage(records),
    }


_USAGE_SECTION_TEMPLATE = """
    <!-- Usage Over Time (rolling windows, hourly) -->
    <section class="glass rounded-3xl p-6 flex flex-col mb-10">
        <div class="flex items-center justify-between mb-5 flex-wrap gap-3">
            <div>
                <h3 class="text-base font-extrabold text-white">Usage Over Time (hourly)</h3>
                <p class="text-xs text-slate-400 mt-0.5">Tool calls bucketed by hour across a rolling window. __ANCHOR_NOTE__</p>
            </div>
            <div class="flex items-center gap-4 flex-wrap">
                <div id="usage-buttons" class="flex gap-2">
                    <button class="usage-btn active" data-key="24h" onclick="pickUsage(this)">24h</button>
                    <button class="usage-btn" data-key="48h" onclick="pickUsage(this)">48h</button>
                    <button class="usage-btn" data-key="7d" onclick="pickUsage(this)">7 days</button>
                    <button class="usage-btn" data-key="month" onclick="pickUsage(this)">Month</button>
                </div>
                <span id="usage-summary" class="text-xs bg-blue-500/10 text-blue-400 border border-blue-500/20 px-2.5 py-0.5 rounded-full font-bold">&mdash;</span>
            </div>
        </div>
        <div class="h-56 w-full flex items-center justify-center">
            <svg id="usage-svg" viewBox="0 0 1000 160" class="w-full h-full">
                <defs>
                    <linearGradient id="usage-grad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stop-color="__PRIMARY__" stop-opacity="0.35"/>
                        <stop offset="100%" stop-color="__PRIMARY__" stop-opacity="0.0"/>
                    </linearGradient>
                </defs>
                <line x1="40" y1="20" x2="980" y2="20" stroke="__GRID__" stroke-dasharray="3"/>
                <line x1="40" y1="75" x2="980" y2="75" stroke="__GRID__" stroke-dasharray="3"/>
                <line x1="40" y1="130" x2="980" y2="130" stroke="__BASELINE__"/>
                <text id="usage-ymax" x="12" y="24" fill="#64748B" font-size="9">0</text>
                <text id="usage-ymid" x="12" y="79" fill="#64748B" font-size="9">0</text>
                <text x="20" y="134" fill="#64748B" font-size="9">0</text>
                <path id="usage-area" d="" fill="url(#usage-grad)"/>
                <path id="usage-line" d="" fill="none" stroke="__PRIMARY__" stroke-width="2.5"/>
                <circle id="usage-peak" r="4" fill="__PRIMARY_DARK__" style="display:none"/>
                <g id="usage-xlabels"></g>
                <!-- Hover tooltip (line + dot + panel); capture rect on top, cues below it via pointer-events:none -->
                <rect id="usage-hit" x="40" y="20" width="940" height="110" fill="transparent" pointer-events="all" style="cursor:crosshair"/>
                <line id="usage-hoverline" y1="20" y2="130" stroke="__PRIMARY_DARK__" stroke-width="1" stroke-dasharray="3" pointer-events="none" style="display:none"/>
                <circle id="usage-hoverdot" r="4.5" fill="__PRIMARY__" stroke="#ffffff" stroke-width="1.5" pointer-events="none" style="display:none"/>
                <g id="usage-tip" pointer-events="none" style="display:none">
                    <rect id="usage-tip-bg" rx="5" fill="__HEADING__" opacity="0.96"/>
                    <text id="usage-tip-time" fill="__TOOLTIP_TIME__" font-size="8" font-weight="600"></text>
                    <text id="usage-tip-val" fill="#ffffff" font-size="9" font-weight="700"></text>
                </g>
            </svg>
        </div>
    </section>
    <script>
      const USAGE_DATA = __USAGE_JSON__;
      let _usageState = null;   // {counts, labels, key, n, maxv} for the drawn window
      const USAGE_GEO = { x0: 40, x1: 980, y0: 20, y1: 130 };
      function pickUsage(btn){
        document.querySelectorAll('#usage-buttons .usage-btn').forEach(b=>b.classList.remove('active'));
        btn.classList.add('active');
        try { sessionStorage.setItem('usageWin', btn.dataset.key); } catch(e){}
        drawUsage(btn.dataset.key);
      }
      function _fmtLabel(iso, key){
        const p = iso.split('T');
        if (key === '24h' || key === '48h') return p[1];   // HH:MM
        return p[0].slice(5);                                // MM-DD
      }
      function _usageXY(i, n, maxv, count){
        const g = USAGE_GEO;
        const x = n<=1 ? (g.x0+g.x1)/2 : g.x0 + i*(g.x1-g.x0)/(n-1);
        const y = g.y1 - (count/maxv)*(g.y1-g.y0);
        return { x: x, y: y };
      }
      function _usageHover(evt){
        const st = _usageState;
        if (!st || !st.n) return;
        const svg = document.getElementById('usage-svg');
        const ctm = svg.getScreenCTM();
        if (!ctm) return;
        const pt = svg.createSVGPoint();
        pt.x = evt.clientX; pt.y = evt.clientY;
        const loc = pt.matrixTransform(ctm.inverse());
        const g = USAGE_GEO;
        let frac = (loc.x - g.x0) / (g.x1 - g.x0);
        frac = Math.max(0, Math.min(1, frac));
        const i = st.n<=1 ? 0 : Math.round(frac * (st.n - 1));
        const val = st.counts[i];
        const pos = _usageXY(i, st.n, st.maxv, val);
        const hl = document.getElementById('usage-hoverline');
        hl.setAttribute('x1', pos.x.toFixed(1)); hl.setAttribute('x2', pos.x.toFixed(1));
        hl.style.display = '';
        const hd = document.getElementById('usage-hoverdot');
        hd.setAttribute('cx', pos.x.toFixed(1)); hd.setAttribute('cy', pos.y.toFixed(1));
        hd.style.display = '';
        const t1 = (st.labels[i] || '').replace('T', ' ');
        const t2 = val + (val === 1 ? ' call' : ' calls');
        const tip = document.getElementById('usage-tip');
        const bg = document.getElementById('usage-tip-bg');
        const tt = document.getElementById('usage-tip-time');
        const tv = document.getElementById('usage-tip-val');
        tt.textContent = t1; tv.textContent = t2;
        const w = Math.max(t1.length, t2.length) * 4.9 + 14, h = 30;
        let tx = pos.x + 10;
        if (tx + w > g.x1) tx = pos.x - 10 - w;          // flip left near right edge
        let ty = Math.max(g.y0, pos.y - h - 8);
        bg.setAttribute('x', tx.toFixed(1)); bg.setAttribute('y', ty.toFixed(1));
        bg.setAttribute('width', w.toFixed(1)); bg.setAttribute('height', h);
        tt.setAttribute('x', (tx + 7).toFixed(1)); tt.setAttribute('y', (ty + 12).toFixed(1));
        tv.setAttribute('x', (tx + 7).toFixed(1)); tv.setAttribute('y', (ty + 24).toFixed(1));
        tip.style.display = '';
      }
      function _usageLeave(){
        ['usage-hoverline','usage-hoverdot','usage-tip'].forEach(function(id){
          const el = document.getElementById(id); if (el) el.style.display = 'none';
        });
      }
      function drawUsage(key){
        const svgNS = 'http://www.w3.org/2000/svg';
        const w = (USAGE_DATA.windows || {})[key];
        const area = document.getElementById('usage-area');
        const line = document.getElementById('usage-line');
        const peak = document.getElementById('usage-peak');
        const xlabels = document.getElementById('usage-xlabels');
        xlabels.innerHTML = '';
        if (!w || !w.counts || !w.counts.length){
          area.setAttribute('d',''); line.setAttribute('d','');
          peak.style.display='none';
          document.getElementById('usage-summary').textContent = 'no data';
          _usageState = null; _usageLeave();
          return;
        }
        const counts = w.counts, labels = w.labels || [];
        const n = counts.length;
        const maxv = Math.max(1, ...counts);
        const x0=40, x1=980, y0=20, y1=130;
        const X = i => n<=1 ? (x0+x1)/2 : x0 + i*(x1-x0)/(n-1);
        const Y = v => y1 - (v/maxv)*(y1-y0);
        let ap = 'M ' + X(0).toFixed(1) + ' ' + y1;
        let lp = '';
        for (let i=0;i<n;i++){
          ap += ' L ' + X(i).toFixed(1) + ' ' + Y(counts[i]).toFixed(1);
          lp += (i? 'L':'M') + ' ' + X(i).toFixed(1) + ' ' + Y(counts[i]).toFixed(1) + ' ';
        }
        ap += ' L ' + X(n-1).toFixed(1) + ' ' + y1 + ' Z';
        area.setAttribute('d', ap);
        line.setAttribute('d', lp.trim());
        let pi=0; for (let i=1;i<n;i++) if (counts[i]>counts[pi]) pi=i;
        if (counts[pi]>0){
          peak.setAttribute('cx', X(pi).toFixed(1));
          peak.setAttribute('cy', Y(counts[pi]).toFixed(1));
          peak.style.display='';
        } else { peak.style.display='none'; }
        document.getElementById('usage-ymax').textContent = maxv;
        document.getElementById('usage-ymid').textContent = Math.round(maxv/2);
        const ticks = Math.min(7, n);
        for (let t=0;t<ticks;t++){
          const i = ticks<=1 ? 0 : Math.round(t*(n-1)/(ticks-1));
          const tx = document.createElementNS(svgNS,'text');
          tx.setAttribute('x', X(i).toFixed(1));
          tx.setAttribute('y', 148);
          tx.setAttribute('fill', '#64748B');
          tx.setAttribute('font-size','8');
          tx.setAttribute('text-anchor','middle');
          tx.textContent = labels[i] ? _fmtLabel(labels[i], key) : '';
          xlabels.appendChild(tx);
        }
        const total = counts.reduce((a,b)=>a+b,0);
        document.getElementById('usage-summary').textContent = total + ' calls · peak ' + counts[pi] + '/h';
        _usageState = { counts: counts, labels: labels, key: key, n: n, maxv: maxv };
        _usageLeave();
      }
      (function(){
        let init = '24h';
        try { init = sessionStorage.getItem('usageWin') || '24h'; } catch(e){}
        const buttons = document.querySelectorAll('#usage-buttons .usage-btn');
        let btn = document.querySelector('#usage-buttons .usage-btn[data-key="'+init+'"]');
        if (!btn && buttons.length) btn = buttons[0];
        buttons.forEach(b=>b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        drawUsage(btn ? btn.dataset.key : '24h');
        const hit = document.getElementById('usage-hit');
        if (hit){
          hit.addEventListener('mousemove', _usageHover);
          hit.addEventListener('mouseleave', _usageLeave);
        }
      })();
    </script>
"""


def build_html_dashboard(metrics: dict, theme: str | None = None) -> str:
    """Build the dashboard HTML in-memory and return it as a string.

    Pure function over ``calculate_metrics``'s output — no file IO. Used by
    both the CLI wrapper ``generate_html_dashboard`` and the standalone
    dashboard HTTP server (``scripts/dashboard_server.py``). ``theme`` selects a
    color palette (see ``THEMES``); ``None`` falls back to ``DASHBOARD_THEME``.
    """
    pal = _get_theme(theme)
    c_primary = pal["primary"]
    c_primary_dark = pal["primary_dark"]
    c_accent_mid = pal["accent_mid"]
    c_heading = pal["heading"]
    c_tooltip_time = pal["tooltip_time"]
    c_pie_extra = pal["pie_extra"]
    c_page_bg = pal["page_bg"]
    c_border = pal["border"]
    c_hover = pal["hover"]
    c_btn_text = pal["btn_text"]
    c_grid = pal["grid"]
    c_baseline = pal["baseline"]
    c_glow = pal["glow_rgba"]
    c_shadow = pal["shadow_rgba"]
    c_ace = pal["ace_series"]
    ace_text_class = pal["ace_text_class"]
    ace_bg_class = pal["ace_bg_class"]

    # Tailwind palette remap built from the theme tokens. `emerald`/`slate` are
    # theme-invariant; `cyan` is added only when the theme needs it (green ACE).
    _tw_colors = {
        "blue": {"400": c_primary, "500": c_primary},
        "indigo": {"400": c_primary_dark, "500": c_primary_dark},
        "violet": {"400": c_primary_dark, "500": c_accent_mid},
        "emerald": {"400": "#059669", "500": "#10B981"},
        "yellow": {"400": "#B45309", "500": "#F59E0B"},
        "slate": {
            "200": "#334155", "300": "#3f4657", "400": "#5b6472", "500": "#6b7280",
            "600": "#b3a0cc", "700": "#c9b6e3", "800": "#ece3f7", "900": "#ffffff",
        },
    }
    if pal.get("cyan_remap"):
        _tw_colors["cyan"] = pal["cyan_remap"]
    tw_config_json = json.dumps({"theme": {"extend": {"colors": _tw_colors}}})

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
    usage_windows = metrics.get("usage_windows", {"windows": {}, "note": ""})

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
        dv_mq_area = f'<circle cx="{cx_only}" cy="{mq_y_only:.1f}" r="6" fill="{c_primary}" class="glow-blue"/>'
        dv_mq_line = ""
        dv_ace_area = f'<circle cx="{cx_only}" cy="{ace_y_only:.1f}" r="6" fill="{c_ace}" class="glow-emerald"/>'
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
        dv_mq_line = f'<path d="M {dv_xs[0]:.1f} {mq_ys[0]:.1f} {mq_line_pts}" fill="none" stroke="{c_primary}" stroke-width="3" class="glow-blue"/>'
        dv_ace_area = f'<path d="M {dv_xs[0]:.1f} 200 {ace_pts} L {dv_xs[-1]:.1f} 200 Z" fill="url(#ace-grad)"/>'
        dv_ace_line = f'<path d="M {dv_xs[0]:.1f} {ace_ys[0]:.1f} {ace_line_pts}" fill="none" stroke="{c_ace}" stroke-width="3" class="glow-emerald"/>'
        mid_i = dv_n // 2
        label_idxs = sorted({0, mid_i, dv_n - 1})
        dv_x_labels = "".join(
            f'<text x="{dv_xs[i]:.1f}" y="222" fill="#64748B" font-size="9" text-anchor="middle">{daily_volume[i]["date"][5:]}</text>'
            for i in label_idxs
        )
        dv_y_top = str(dv_max)
        dv_y_mid = str(dv_max // 2)

    # --- Tool popularity pie (viewBox 200x200, center (100,100), r=90) ---
    PIE_COLORS = [c_primary, c_ace, "#F59E0B", c_primary_dark, c_pie_extra, "#94a3b8"]
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
                f'fill="{color}" stroke="#ffffff" stroke-width="2"/>'
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
            color = c_primary
        lat_labels_html += (
            f'<text x="130" y="{y + 11}" fill="#374151" font-size="9" '
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

    # Rolling-window usage section (self-contained HTML+JS; braces stay out of the
    # main f-string by rendering via .replace on a plain-string template).
    usage_section_html = (
        _USAGE_SECTION_TEMPLATE
        .replace("__USAGE_JSON__", json.dumps(usage_windows))
        .replace("__ANCHOR_NOTE__", usage_windows.get("note", ""))
        .replace("__PRIMARY_DARK__", c_primary_dark)
        .replace("__PRIMARY__", c_primary)
        .replace("__HEADING__", c_heading)
        .replace("__TOOLTIP_TIME__", c_tooltip_time)
        .replace("__GRID__", c_grid)
        .replace("__BASELINE__", c_baseline)
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {_refresh_meta()}
    <title>IBM MQ+ACE MCP Server — Log Insights Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
      // Light theme: remap the dark-theme utility palettes so the existing markup
      // renders on a light background without touching every class. Colors come
      // from the selected theme (see THEMES); the `slate` scale is INVERTED (was
      // light text on dark; now dark text on light).
      tailwind.config = {tw_config_json};
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: linear-gradient(180deg, {c_page_bg} 0%, #ffffff 240px);
            background-attachment: fixed;
            color: #1A1A1A;
        }}
        /* Slim brand bar echoing the chat UI's fixed top nav. */
        .brand-bar {{
            position: fixed; top: 0; left: 0; width: 100%; height: 5px;
            background: linear-gradient(90deg, {c_primary} 0%, {c_primary_dark} 100%);
            z-index: 1000;
        }}
        /* Headings were `text-white` on dark — force to a deep brand tone on light.
           Higher specificity than Tailwind's single-class utility, so it wins. */
        body .text-white {{ color: {c_heading}; }}
        .brand-title {{
            background: linear-gradient(90deg, {c_primary} 0%, {c_primary_dark} 100%);
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent; color: transparent;
        }}
        .glass {{
            background: #ffffff;
            border: 1px solid {c_border};
            box-shadow: 0 4px 20px rgba({c_shadow}, 0.06);
        }}
        .glow-blue {{
            filter: drop-shadow(0 0 8px rgba({c_glow}, 0.35));
        }}
        .glow-emerald {{
            filter: drop-shadow(0 0 8px rgba(16, 185, 129, 0.35));
        }}
        /* Window toggle buttons for the usage-over-time chart. */
        .usage-btn {{
            font-size: 12px; font-weight: 700; padding: 5px 14px; border-radius: 999px;
            border: 1px solid {c_border}; background: {c_page_bg}; color: {c_btn_text};
            cursor: pointer; transition: all .15s ease;
        }}
        .usage-btn:hover {{ background: {c_hover}; }}
        .usage-btn.active {{
            background: linear-gradient(90deg, {c_primary} 0%, {c_primary_dark} 100%);
            color: #ffffff; border-color: transparent;
        }}
    </style>
</head>
<body class="p-6 md:p-12 min-h-screen">
    <div class="brand-bar"></div>
    <!-- Header -->
    <header class="mb-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
            <div class="flex items-center gap-3">
                <span class="px-3 py-1 text-[10px] font-extrabold uppercase tracking-wider rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">Production Audit Ready</span>
                <span class="px-3 py-1 text-[10px] font-extrabold uppercase tracking-wider rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">Observability Verified</span>
            </div>
            <h1 class="text-3xl font-extrabold mt-3 brand-title tracking-tight">IBM MQ & IBM ACE AI Diagnostic Engine</h1>
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
    <div class="mb-4">
        <h2 class="text-xl font-bold text-white">Key Metrics at a Glance</h2>
        <p class="text-xs text-slate-400 mt-0.5">The four headline health numbers for this MCP server. Hover any title (&#9432;) for its definition.</p>
    </div>
    <section class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-10">
        <!-- Card 1: Total Calls -->
        <div class="glass rounded-2xl p-6 relative overflow-hidden group hover:border-blue-500/30 transition-all duration-300">
            <div class="absolute -right-4 -bottom-4 opacity-5 text-white">
                <svg class="w-24 h-24" fill="currentColor" viewBox="0 0 20 20"><path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z"></path></svg>
            </div>
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block" title="Total number of MCP tool calls recorded in the logs for the selected date range.">Total Invocations <span class="text-slate-600">&#9432;</span></span>
            <h2 class="text-4xl font-black mt-2 text-white">{total_calls}</h2>
            <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">How many times a diagnostic tool was called over the period shown.</p>
            <div class="text-[10px] text-blue-400 font-semibold mt-3 flex items-center gap-1">
                <span>&#9889; Parsed live from JSONL query logs</span>
            </div>
        </div>
        
        <!-- Card 2: Success Rate -->
        <div class="glass rounded-2xl p-6 relative overflow-hidden group hover:border-emerald-500/30 transition-all duration-300">
            <div class="absolute -right-4 -bottom-4 opacity-5 text-white">
                <svg class="w-24 h-24" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg>
            </div>
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block" title="Share of calls whose tool function returned without raising an error. Note: ACE tools return a JSON error envelope instead of raising, so upstream ACE failures can still count as success — see 'How to read this dashboard'.">Request Success Rate <span class="text-slate-600">&#9432;</span></span>
            <h2 class="text-4xl font-black mt-2 text-emerald-400">{success_rate:.2f}%</h2>
            <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">Percent of calls that completed without raising an error.</p>
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
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block" title="95th-percentile response time: 95% of calls finished faster than this. A tail-latency metric — more representative of worst-case user experience than the average.">P95 Response Latency <span class="text-slate-600">&#9432;</span></span>
            <h2 class="text-4xl font-black mt-2 text-yellow-400">{p95_latency:,.1f}<span class="text-xs font-bold text-slate-500 uppercase ml-1">ms</span></h2>
            <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">95% of calls were faster than this (worst-case feel, not the average).</p>
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
            <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest block" title="Share of calls that completed within the 1.0-second target. A 'breach' is any call slower than 1000 ms. 'Active Callers' is the number of distinct authenticated users seen in the logs.">SLA Compliance (&lt;1.0s) <span class="text-slate-600">&#9432;</span></span>
            <h2 class="text-4xl font-black mt-2 text-violet-400">{sla_compliance:.2f}%</h2>
            <p class="text-[11px] text-slate-500 mt-1.5 leading-snug">Percent of calls finishing under the 1-second target.</p>
            <div class="text-[10px] text-slate-400 mt-3 flex justify-between font-semibold">
                <span>Breaches: {sla_breaches}</span>
                <span>Active Callers: {active_callers}</span>
            </div>
        </div>
    </section>

    {usage_section_html}
    <!-- Visual Analytics Charts Grid (SVGs) -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
        <!-- Area Chart: Volume Trend -->
        <div class="glass rounded-3xl p-6 flex flex-col">
            <div class="flex items-center justify-between mb-6">
                <div>
                    <h3 class="text-base font-extrabold text-white">Daily Query Volume Trend</h3>
                    <p class="text-xs text-slate-400 mt-0.5">Calls per day, split by platform. Shows whether usage is growing and which platform drives it. Each line is one day's total for IBM MQ vs IBM ACE.</p>
                </div>
                <div class="flex gap-4 text-xs font-semibold">
                    <span class="flex items-center gap-1.5 text-blue-400"><span class="h-2 w-2 rounded-full bg-blue-500"></span> IBM MQ</span>
                    <span class="flex items-center gap-1.5 {ace_text_class}"><span class="h-2 w-2 rounded-full {ace_bg_class}"></span> IBM ACE</span>
                </div>
            </div>
            <div class="h-72 w-full mt-auto flex items-center justify-center">
                <!-- SVG Area Chart -->
                <svg viewBox="0 0 500 250" class="w-full h-full">
                    <defs>
                        <linearGradient id="mq-grad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="{c_primary}" stop-opacity="0.4"/>
                            <stop offset="100%" stop-color="{c_primary}" stop-opacity="0.0"/>
                        </linearGradient>
                        <linearGradient id="ace-grad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="{c_ace}" stop-opacity="0.4"/>
                            <stop offset="100%" stop-color="{c_ace}" stop-opacity="0.0"/>
                        </linearGradient>
                    </defs>
                    <!-- Y-Axis Grid Lines -->
                    <line x1="40" y1="40" x2="480" y2="40" stroke="{c_grid}" stroke-dasharray="3"/>
                    <line x1="40" y1="120" x2="480" y2="120" stroke="{c_grid}" stroke-dasharray="3"/>
                    <line x1="40" y1="200" x2="480" y2="200" stroke="{c_baseline}"/>
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
                <p class="text-xs text-slate-400 mt-0.5">Which tools are used most. Each slice is one tool's share of all calls (top 5 shown, the rest grouped as &ldquo;Others&rdquo;).</p>
            </div>
            <div class="h-72 w-full mt-auto flex flex-col md:flex-row items-center justify-around gap-6">
                <!-- SVG Pie Chart (data-driven sectors) -->
                <svg viewBox="0 0 200 200" class="w-48 h-48">
                    <circle cx="100" cy="100" r="90" fill="none" stroke="{c_grid}" stroke-width="12"/>
                    {pie_sectors_html}
                    <circle cx="100" cy="100" r="50" fill="#ffffff"/>
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
                <p class="text-xs text-slate-400 mt-0.5">Slowest tools by tail latency. Each bar is a tool's P95 (95% of its calls were faster). Bars past the red line breach the 1.0s SLA target.</p>
            </div>
            <div class="h-72 w-full mt-auto flex items-center justify-center">
                <svg viewBox="0 0 500 250" class="w-full h-full">
                    <line x1="140" y1="20" x2="140" y2="210" stroke="{c_baseline}"/>
                    <line x1="225" y1="20" x2="225" y2="210" stroke="{c_grid}" stroke-dasharray="2"/>
                    <line x1="310" y1="20" x2="310" y2="210" stroke="{c_grid}" stroke-dasharray="2"/>
                    <line x1="395" y1="20" x2="395" y2="210" stroke="{c_grid}" stroke-dasharray="2"/>
                    <line x1="480" y1="20" x2="480" y2="210" stroke="{c_grid}" stroke-dasharray="2"/>

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
                <p class="text-xs text-slate-400 mt-0.5">Which back-end hosts (MQ web / ACE nodes) the server actually called. &ldquo;Local resolves&rdquo; are calls answered offline or rejected before any network hop.</p>
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
                <p class="text-xs text-slate-400 mt-0.5">When traffic happens. Calls bucketed by hour of day (UTC), summed across every day in range — the peak marks your busiest hour.</p>
            </div>
            <span class="text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 px-2.5 py-0.5 rounded-full font-bold">{peak_hour_badge}</span>
        </div>
        <div class="h-44 w-full flex items-center justify-center">
            <svg viewBox="0 0 1000 150" class="w-full h-full">
                <line x1="40" y1="20" x2="960" y2="20" stroke="{c_grid}" stroke-dasharray="3"/>
                <line x1="40" y1="70" x2="960" y2="70" stroke="{c_grid}" stroke-dasharray="3"/>
                <line x1="40" y1="120" x2="960" y2="120" stroke="{c_baseline}"/>

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
    <h2 class="text-xl font-bold mb-1 text-white">Diagnostic Tool Performance Matrix</h2>
    <p class="text-xs text-slate-400 mb-4">Per-tool breakdown, busiest first. <span class="text-slate-300">Success %</span> green &gt;95, amber &gt;80, red below. <span class="text-slate-300">P95</span> turns red when it breaches the 1.0s SLA.</p>
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
                        <td class="p-4 text-xs font-semibold {'text-blue-400' if row['product'] == 'IBM MQ' else ace_text_class}">{row["product"]}</td>
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
            <h3 class="text-base font-extrabold text-white mb-1">Caller Leaderboard</h3>
            <p class="text-xs text-slate-400 mb-4">Who is using the server. Authenticated user accounts (from SSE Basic Auth) ranked by call count; <code>unauthenticated</code> covers stdio or anonymous calls.</p>
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

    <!-- Metric Definitions Glossary -->
    <h2 class="text-xl font-bold mb-1 text-white">Metric Definitions</h2>
    <p class="text-xs text-slate-400 mb-4">Plain-English meaning of every number on this page.</p>
    <section class="glass rounded-3xl p-6 mb-10">
        <dl class="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5 text-sm">
            <div>
                <dt class="font-bold text-white">Total Invocations</dt>
                <dd class="text-slate-400 text-xs mt-1">Count of every tool call in the logs for the selected date range. One row in a <code>queries-*.jsonl</code> file = one invocation.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">Request Success Rate</dt>
                <dd class="text-slate-400 text-xs mt-1">Successful calls &divide; total calls. &ldquo;Success&rdquo; means the tool returned without raising. ACE tools return an error envelope instead of raising, so this can over-count ACE health &mdash; cross-check the error counts per tool.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">Mean / Median Latency</dt>
                <dd class="text-slate-400 text-xs mt-1">Average and middle response time (ms). Median ignores outliers; a mean much higher than the median signals a few very slow calls.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">P95 / P99 Latency</dt>
                <dd class="text-slate-400 text-xs mt-1">95% (or 99%) of calls were faster than this value. These &ldquo;tail&rdquo; numbers reflect worst-case experience better than the average.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">SLA Compliance &amp; Breaches</dt>
                <dd class="text-slate-400 text-xs mt-1">Share of calls completing under the 1.0-second target. A <em>breach</em> is any call slower than 1000 ms.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">Active Callers</dt>
                <dd class="text-slate-400 text-xs mt-1">Number of distinct authenticated users seen in the logs over the range.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">Endpoints Hit / Local Resolves</dt>
                <dd class="text-slate-400 text-xs mt-1">Back-end hosts the server called over the network, ranked by hit count. <em>Local resolves</em> are calls answered from offline CSV data or rejected by the allow-list before any network call.</dd>
            </div>
            <div>
                <dt class="font-bold text-white">Platform (IBM MQ / IBM ACE)</dt>
                <dd class="text-slate-400 text-xs mt-1">Which middleware a tool targets, derived from the tool name. Lets you see MQ vs ACE demand at a glance.</dd>
            </div>
        </dl>
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


def compute_dashboard_html(log_dir: Path | None = None, theme: str | None = None) -> str:
    """Render the dashboard HTML in one call. Used by the dashboard HTTP server.

    Falls back to a small placeholder page when the log directory is missing
    or empty so the endpoint never 500s on a fresh deployment. ``theme`` selects
    the color palette (see ``THEMES``); ``None`` uses ``DASHBOARD_THEME``.
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
    return build_html_dashboard(metrics, theme=theme)


def _empty_dashboard_html(reason: str) -> str:
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
        f"{_refresh_meta()}"
        "<title>IBM MQ+ACE MCP Server — Dashboard</title></head>"
        "<body style=\"font-family: sans-serif; padding: 2em; "
        "background:#F7F3FC; color:#1A1A1A;\">"
        "<h1>Dashboard not available</h1>"
        f"<p>{reason}</p></body></html>"
    )


# Shared dark-theme <head> used by the per-server dashboard pages.
_PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {refresh}
    <title>{title}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Plus Jakarta Sans', sans-serif; background: #F7F3FC; color: #1A1A1A; }}
        .glass {{ background: #ffffff; border: 1px solid #E6D9F5; box-shadow: 0 4px 20px rgba(117,0,192,0.06); }}
    </style>
</head>
"""


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