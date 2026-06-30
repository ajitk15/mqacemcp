"""Streamlit chat UI for the MCP chatbot backend.

Talks to the FastAPI backend (`chatbot/agent/app.py`) over its
existing endpoints — no backend changes required.

Run:
    streamlit run app.py
"""
from __future__ import annotations

import os
import uuid
from html import escape

from dotenv import load_dotenv

load_dotenv()  # picks up MCP_BACKEND_URL / PAGE_TITLE from .env if present

import streamlit as st

from client import connect_server, get_health, get_servers, reset_thread, stream_chat
from renderers import render_assistant_body, render_block, render_markdown, render_tool_step


# ---------------------------------------------------------------------------
# Page + theme
# ---------------------------------------------------------------------------

_PAGE_TITLE_OVERRIDE = os.getenv("PAGE_TITLE", "").strip()
_PAGE_ICON = os.getenv("PAGE_ICON", "").strip() or "💬"

# Sidebar quick-links (open in a new browser tab). The sample-questions page is
# served by Streamlit's static file server (frontend/static/, enabled in
# .streamlit/config.toml) at a relative URL.
_DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://localhost:8004/dashboard").strip()
_SAMPLE_QUESTIONS_URL = os.getenv(
    "SAMPLE_QUESTIONS_URL", "app/static/mq_ace_cert_questions.html"
).strip()

_CUSTOM_SERVER_LABEL = "Custom…"

st.set_page_config(
    page_title=_PAGE_TITLE_OVERRIDE or "MCP Chatbot",
    page_icon=_PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",  # visible on load; collapse/expand via the chevron
)

_CUSTOM_CSS = """
<style>
  .stApp { background-color: #ffffff; color: #1A1A1A; }

  /* Fixed full-width top nav — brand purple */
  .top-nav {
    position: fixed; top: 0; left: 0; width: 100%;
    background: linear-gradient(90deg, #A100FF 0%, #7500C0 100%);
    color: #ffffff; padding: 6px 24px; z-index: 1000;
    box-shadow: 0 1px 5px rgba(0,0,0,0.12);
    display: flex; justify-content: space-between; align-items: center;
  }
  .top-nav h2 {
    color: #ffffff !important; margin: 0 0 0 42px !important;  /* clear the sidebar toggle */
    font-size: 16px !important; font-weight: 600; letter-spacing: .2px;
  }
  .top-nav .nav-health { display: flex; gap: 18px; font-size: 12.5px; font-weight: 500; color: #ffffff; }
  .top-nav .nav-health span { display: flex; align-items: center; gap: 5px; white-space: nowrap; }

  /* Clear the fixed header + footer. Full width so the conversation lines up
     with the (full-width) chat input bar. */
  .block-container { padding-top: 3.2rem !important; padding-bottom: 5.5rem !important; max-width: 100% !important; }

  /* Sidebar breadcrumb */
  .sidebar-crumb {
    font-size: 12px; font-weight: 600; color: #6b21a8;
    background: #F1E9FB; border: 1px solid #E6D9F5; border-radius: 8px;
    padding: 6px 10px; margin: 2px 0 12px;
  }
  .sidebar-crumb .sep { color: #A100FF; margin: 0 5px; }
  /* Transparent (not height:0 — that clipped the sidebar expand control) */
  header[data-testid="stHeader"] { background: transparent; }
  /* Hide ONLY the Deploy button + top decoration — NOT the whole toolbar
     (the sidebar expand `»` control lives in the toolbar area in Streamlit 1.58).
     Correct 1.58 id for Deploy is stAppDeployButton. */
  [data-testid="stDecoration"], [data-testid="stAppDeployButton"] { display: none !important; }
  #MainMenu { display: none !important; }
  /* Sidebar open/close controls must sit ABOVE the fixed purple bar (z 1000).
     Streamlit 1.58 ids: stExpandSidebarButton (collapsed) / stSidebarCollapseButton (open). */
  [data-testid="stExpandSidebarButton"],
  [data-testid="stSidebarCollapseButton"] { z-index: 1003 !important; visibility: visible !important; }
  /* The expand control overlays the purple bar when collapsed → white icon */
  [data-testid="stExpandSidebarButton"] button,
  [data-testid="stExpandSidebarButton"] svg { color: #ffffff !important; fill: #ffffff !important; }
  /* Push sidebar content below the fixed top bar so it isn't hidden under it */
  section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 2.2rem; }

  /* Empty-state hint */
  .mcp-empty { text-align: center; color: #6b7280; font-size: 0.9rem; margin-top: 3rem; }

  /* Chat messages — rounded, light */
  [data-testid="stChatMessage"] {
    border-radius: 12px; margin-bottom: 8px; padding: 6px 14px;
    background: #faf8fd; border: 1px solid #ece3f7;
  }
  /* Agent (assistant) → left, roomy */
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    margin-right: auto; max-width: 92%;
  }
  /* User → right-aligned bubble (avatar on the right), purple tint */
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    flex-direction: row-reverse;
    margin-left: auto; max-width: 80%;
    background: rgba(161, 0, 255, 0.06); border-color: rgba(161, 0, 255, 0.28);
  }

  /* Pulsing "thinking" indicator — brand purple dots */
  @keyframes pulse { 0%{opacity:.6;transform:scale(.98)} 50%{opacity:1;transform:scale(1.01)} 100%{opacity:.6;transform:scale(.98)} }
  .thinking-box {
    background:#ffffff; border:1px solid #ece3f7; border-radius:8px;
    padding:6px 12px; display:flex; align-items:center; gap:8px; width:fit-content;
    animation:pulse 1.6s infinite ease-in-out; box-shadow:0 2px 5px rgba(0,0,0,0.05);
  }
  .thinking-box span { font-size:13px; color:#6b21a8; font-weight:500; letter-spacing:.2px; }
  .thinking-dot { width:6px; height:6px; background:#A100FF; border-radius:50%; }

  /* Expander (tool step) */
  [data-testid="stExpander"] { border: 1px solid #ece3f7; border-radius: 8px; margin: 8px 0; background: #faf8fd; }
  [data-testid="stExpander"] summary { font-size: 0.82rem; }

  /* Sidebar — faint purple tint */
  section[data-testid="stSidebar"] { background: #F7F3FC; border-right: 1px solid #E6D9F5; }

  /* Fixed footer */
  .fixed-footer {
    position: fixed; left:0; bottom:0; width:100%;
    background:#ffffff; color:#8a8a8a; text-align:center;
    padding:7px 0; font-size:11px; border-top:1px solid #eee; z-index:999;
  }

  /* Hide Streamlit's default menu/footer */
  #MainMenu { visibility: hidden; }
  footer { visibility: hidden; }
</style>
"""
st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "info" not in st.session_state:
    st.session_state.info = get_health()
if "servers" not in st.session_state:
    st.session_state.servers = get_servers()
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "turns" not in st.session_state:
    # Each turn: {role, text, tool_steps: [{name, args, call_id, result}], error?, done}
    st.session_state.turns = []
if "pending" not in st.session_state:
    st.session_state.pending = None  # message awaiting streaming


def _refresh_health() -> None:
    st.session_state.info = get_health()
    st.session_state.servers = get_servers()


info = st.session_state.info or {}
header_title = (info.get("header_title") or "").strip() or "MCP Chatbot"
bot_domain = (info.get("bot_domain") or "").strip()
explicit_sub = (info.get("header_subtitle") or "").strip()
backend_reachable = bool(info)

if explicit_sub:
    subtitle = explicit_sub
    subtitle_warn = False
elif not backend_reachable:
    subtitle = "backend unreachable"
    subtitle_warn = True
elif bot_domain:
    subtitle = f"scope: {bot_domain}"
    subtitle_warn = False
else:
    subtitle = "connected to MCP backend"
    subtitle_warn = False


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

_tool_count = info.get("tool_count", 0)
_mcp_icon = "🟢" if (backend_reachable and _tool_count > 0) else "🔴"
_agent_icon = "🟢" if backend_reachable else "🔴"
_ui_icon = "🟢"  # if this page rendered, the UI is up
st.markdown(
    f"""
    <div class="top-nav">
      <h2>{escape(header_title)}</h2>
      <div class="nav-health">
        <span>{_mcp_icon} MCP</span>
        <span>{_agent_icon} Agent</span>
        <span>{_ui_icon} UI</span>
      </div>
    </div>
    <div class="fixed-footer">{escape(header_title)} &middot; MCP-powered</div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    # Breadcrumb — orients the user: app › active MCP server.
    _crumb_active = ((st.session_state.servers or {}).get("active_name") or "MCP server").strip()
    st.markdown(
        f'<div class="sidebar-crumb">🏠 Assistant <span class="sep">›</span> '
        f'{escape(_crumb_active)}</div>',
        unsafe_allow_html=True,
    )

    st.markdown("### Conversation")
    if st.button("New conversation", width='stretch'):
        reset_thread(st.session_state.thread_id)
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.turns = []
        st.session_state.pending = None
        st.rerun()

    if st.button("Refresh backend info", width='stretch'):
        _refresh_health()
        st.rerun()

    st.divider()
    st.markdown("### MCP Server")
    servers_info = st.session_state.servers or {}
    known = servers_info.get("servers") or []
    active_url = (servers_info.get("active_url") or "").strip()
    active_name = (servers_info.get("active_name") or active_url).strip()

    options = [s.get("name") or s.get("url") for s in known] + [_CUSTOM_SERVER_LABEL]
    # Preselect whichever known server is currently active.
    active_idx = next(
        (i for i, s in enumerate(known) if (s.get("url") or "").strip() == active_url),
        0,
    )
    choice = st.selectbox("Connect to", options, index=active_idx)

    if choice == _CUSTOM_SERVER_LABEL:
        target_url = st.text_input("SSE URL", placeholder="https://host:port/sse").strip()
        target_name = None
        with st.expander("Auth (optional)", expanded=False):
            custom_user = st.text_input("Username", key="mcp_custom_user").strip() or None
            custom_pwd = st.text_input(
                "Password", type="password", key="mcp_custom_pwd"
            ).strip() or None
    else:
        selected = next((s for s in known if (s.get("name") or s.get("url")) == choice), {})
        target_url = (selected.get("url") or "").strip()
        target_name = selected.get("name")
        custom_user = custom_pwd = None

    if st.button("Connect", type="primary", width='stretch'):
        if not target_url:
            st.warning("Enter an SSE URL first.")
        else:
            with st.spinner(f"Connecting to {target_url}…"):
                result = connect_server(
                    target_url, name=target_name,
                    auth_user=custom_user, auth_password=custom_pwd,
                )
            if result.get("status") == "ok":
                st.success(
                    f"Connected to {result.get('active_name') or target_url} "
                    f"({result.get('tool_count', 0)} tools)."
                )
                _refresh_health()
                # Switching servers changes the toolset — start a fresh thread.
                reset_thread(st.session_state.thread_id)
                st.session_state.thread_id = str(uuid.uuid4())
                st.session_state.turns = []
                st.session_state.pending = None
                st.rerun()
            else:
                st.error(result.get("message") or "Connect failed.")

    if active_name:
        st.caption(f"Active: {active_name}")

    st.divider()
    st.markdown("### Backend")
    tool_count = info.get("tool_count", 0)
    st.caption(f"Status: {'connected' if backend_reachable else 'unreachable'}")
    st.caption(f"Tools loaded: {tool_count}")
    if info.get("mcp_sse_url"):
        st.caption(f"MCP SSE: {info['mcp_sse_url']}")
    if info.get("prompt_source"):
        st.caption(f"Prompt: {info['prompt_source']}")
    if bot_domain:
        st.caption(f"Scope: {bot_domain}")

    tools = info.get("tools") or []
    if tools:
        with st.expander(f"Available tools ({len(tools)})", expanded=False):
            for tool_name in tools:
                st.markdown(f"- `{tool_name}`")

    allowlist = info.get("tool_allowlist") or []
    denylist = info.get("tool_denylist") or []
    if allowlist or denylist:
        with st.expander("Filters", expanded=False):
            if allowlist:
                st.caption("Allowlist:")
                st.code("\n".join(allowlist), language="text")
            if denylist:
                st.caption("Denylist:")
                st.code("\n".join(denylist), language="text")

    st.divider()
    st.markdown("### Links")
    st.markdown(
        f"""
        <div style="display:flex; flex-direction:column; gap:6px;">
          <a href="{escape(_DASHBOARD_URL)}" target="_blank" rel="noopener">📊 Log dashboard</a>
          <a href="{escape(_SAMPLE_QUESTIONS_URL)}" target="_blank" rel="noopener">❓ Sample questions</a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()
    st.caption(f"Thread: `{st.session_state.thread_id[:8]}…`")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def _render_history() -> None:
    if not st.session_state.turns and st.session_state.pending is None:
        hint = (
            f"Ask about {bot_domain}. The assistant will pick the right MCP tool and stream the answer back."
            if bot_domain
            else "Ask anything. The assistant will pick the right MCP tool and stream the answer back."
        )
        st.markdown(f"<div class='mcp-empty'>{escape(hint)}</div>", unsafe_allow_html=True)
        return

    for turn in st.session_state.turns:
        role = turn["role"]
        with st.chat_message(role):
            if role == "user":
                st.markdown(turn["text"])
            else:
                render_assistant_body(
                    turn.get("text", ""),
                    turn.get("tool_steps") or [],
                    turn.get("error"),
                )


_render_history()


# ---------------------------------------------------------------------------
# Streaming a new turn
# ---------------------------------------------------------------------------

def _match_step_index(tool_steps: list, name: str, call_id) -> int:
    """Find the tool-step record this result belongs to."""
    if call_id:
        for index, step in enumerate(tool_steps):
            if step.get("call_id") == call_id:
                return index
    # Fallback: first step with matching name that still has no result.
    for index, step in enumerate(tool_steps):
        if step.get("name") == name and step.get("result") is None:
            return index
    return -1


_THINKING_HTML = (
    '<div class="thinking-box">'
    '<div class="thinking-dot"></div>'
    '<div class="thinking-dot"></div>'
    '<div class="thinking-dot"></div>'
    '<span>Processing…</span>'
    '</div>'
)


def _stream_pending() -> None:
    pending_message = st.session_state.pending
    if not pending_message:
        return

    assistant_turn = {
        "role": "assistant",
        "text": "",
        "tool_steps": [],
        "error": None,
        "done": False,
    }
    # Append to history immediately so it persists across the run.
    st.session_state.turns.append(assistant_turn)

    with st.chat_message("assistant"):
        thinking_placeholder = st.empty()
        tool_area = st.container()
        text_placeholder = st.empty()
        error_placeholder = st.empty()

        step_placeholders: list = []  # parallel to assistant_turn["tool_steps"]

        # Pulsing indicator while we wait for the first stream event.
        thinking_placeholder.markdown(_THINKING_HTML, unsafe_allow_html=True)
        thinking_active = True

        try:
            for event in stream_chat(pending_message, st.session_state.thread_id):
                if thinking_active:
                    thinking_placeholder.empty()
                    thinking_active = False
                kind = event.get("kind")

                if kind == "token":
                    assistant_turn["text"] += event.get("text", "")
                    with text_placeholder.container():
                        render_markdown(assistant_turn["text"])

                elif kind == "tool_call":
                    step = {
                        "name": event.get("name", "tool"),
                        "args": event.get("args") or {},
                        "call_id": event.get("call_id"),
                        "result": None,
                    }
                    assistant_turn["tool_steps"].append(step)
                    with tool_area:
                        placeholder = st.empty()
                    step_placeholders.append(placeholder)
                    with placeholder.container():
                        render_tool_step(step, running=True)

                elif kind == "tool_result":
                    index = _match_step_index(
                        assistant_turn["tool_steps"],
                        event.get("name", ""),
                        event.get("call_id"),
                    )
                    if index >= 0:
                        assistant_turn["tool_steps"][index]["result"] = event.get("block")
                        with step_placeholders[index].container():
                            render_tool_step(assistant_turn["tool_steps"][index], running=False)
                    else:
                        # No matching step (shouldn't normally happen) — surface anyway.
                        with tool_area:
                            new_placeholder = st.empty()
                        step_placeholders.append(new_placeholder)
                        stray_step = {
                            "name": event.get("name", "tool"),
                            "args": {},
                            "call_id": event.get("call_id"),
                            "result": event.get("block"),
                        }
                        assistant_turn["tool_steps"].append(stray_step)
                        with new_placeholder.container():
                            render_tool_step(stray_step, running=False)

                elif kind == "error":
                    assistant_turn["error"] = event.get("message") or "Unknown error"
                    error_placeholder.error(assistant_turn["error"])

                elif kind == "done":
                    assistant_turn["done"] = True
                    break

                elif kind == "final":
                    # The backend currently sends an empty `blocks` list as a
                    # structural cue. If a future backend ever fills it, render
                    # the extra blocks after the streamed narrative.
                    for block in event.get("blocks") or []:
                        render_block(block)

        except Exception as err:  # noqa: BLE001
            assistant_turn["error"] = f"Stream failed: {err}"
            error_placeholder.error(assistant_turn["error"])
        finally:
            thinking_placeholder.empty()
            assistant_turn["done"] = True
            st.session_state.pending = None


_stream_pending()


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

placeholder = f"Ask about {bot_domain}…" if bot_domain else "Ask the MCP assistant…"
user_input = st.chat_input(placeholder)

if user_input:
    st.session_state.turns.append(
        {
            "role": "user",
            "text": user_input,
            "tool_steps": [],
            "error": None,
            "done": True,
        }
    )
    st.session_state.pending = user_input
    st.rerun()
