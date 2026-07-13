#!/usr/bin/env bash
#
# start-all.sh — start the full MQ+ACE stack on one RHEL/Linux host.
#
# Launches each service in the background with nohup, redirecting its output to
# scripts/.logs/<service>.log and recording PIDs in scripts/.pids so
# stop-all.sh can terminate them.
#
# Startup order (each component reads its own .env from its own directory):
#
#   1. MCP server   (mqacemcpserver/mqacemcpserver.py, Streamable HTTP on :8010)
#   2. Chat backend (agent/app.py, FastAPI on :8002)
#   3. Streamlit UI (frontend/app.py, on :8003)
#   4. Dashboard    (dashboard/dashboard_server.py, on :8004)
#
# The MCP server reads its own mqacemcpserver/.env (:8010). There is no
# repo-root .env. The backend connects to it by default; users can point at a
# custom server from the Streamlit sidebar.
#
# Each component is self-contained with its own .env and requirements.txt and
# its own module-level .venv (mqacemcpserver/.venv, agent/.venv, frontend/.venv,
# dashboard/.venv). No repo-root .venv is created. Pass --setup to create
# missing venvs and pip install each component's requirements before launching.
#
# --setup first seeds each module's .env from its .env.example (asks for
# confirmation; never overwrites an existing .env). It also asks which LLM
# provider the agent should use (openai|gemini|claude), installs the matching
# package overlay, and records LLM_PROVIDER in agent/.env. Use --llm <provider>
# to answer the provider question non-interactively.
#
# Usage:
#   ./scripts/start-all.sh --setup            # first run: build venvs, then start
#   ./scripts/start-all.sh --setup --llm claude
#   ./scripts/start-all.sh                    # start the MCP server + the stack
#   ./scripts/start-all.sh --skip-mcp --skip-dashboard
#   ./scripts/start-all.sh --check-only
#
set -euo pipefail

# --- options ---------------------------------------------------------------
DO_SETUP=0
SKIP_MCP=0
SKIP_BACKEND=0
SKIP_FRONTEND=0
SKIP_DASHBOARD=0
CHECK_ONLY=0
PORT=8003
LLM=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --setup)           DO_SETUP=1 ;;
        --llm)             LLM="$2"; shift ;;
        --skip-mcp)        SKIP_MCP=1 ;;
        --skip-backend)    SKIP_BACKEND=1 ;;
        --skip-frontend)   SKIP_FRONTEND=1 ;;
        --skip-dashboard)  SKIP_DASHBOARD=1 ;;
        --check-only)      CHECK_ONLY=1 ;;
        --port)            PORT="$2"; shift ;;
        -h|--help)         grep '^#' "$0" | sed 's/^#//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$LLM" in ""|openai|gemini|claude) ;; *) echo "Invalid --llm '$LLM' (use openai|gemini|claude)" >&2; exit 2 ;; esac

# --- paths -----------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# The MCP server reads its own mqacemcpserver/.env.
MCP_DIR="$REPO_ROOT/mqacemcpserver"
MCP_ENTRY="$MCP_DIR/mqacemcpserver.py"
MCP_ENV="$MCP_DIR/.env"
MCP_REQS="$MCP_DIR/requirements.txt"
BACKEND_DIR="$REPO_ROOT/agent"
BACKEND_ENV="$BACKEND_DIR/.env"
FRONTEND_DIR="$REPO_ROOT/frontend"
FRONTEND_ENV="$FRONTEND_DIR/.env"
DASHBOARD_DIR="$REPO_ROOT/dashboard"
DASHBOARD_ENV="$DASHBOARD_DIR/.env"
# The MCP server has its OWN module-level venv (no repo-root .venv is created).
MCP_VENV_PY="$MCP_DIR/.venv/bin/python"
PID_FILE="$SCRIPT_DIR/.pids"
LOG_DIR="$SCRIPT_DIR/.logs"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  OK  %s\033[0m\n' "$1"; }
bad()  { printf '\033[31m  !!  %s\033[0m\n' "$1"; }
note() { printf '\033[90m      %s\033[0m\n' "$1"; }

# get_env <file> <key> <default> — read KEY=value from a .env so the endpoint
# output reflects the actual ports/scheme the services bind at runtime.
get_env() {
    local file="$1" key="$2" default="$3" val=""
    if [[ -f "$file" ]]; then
        val="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | head -n1 \
               | sed -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//")"
        val="${val%$'\r'}"   # strip trailing CR (Windows-edited .env)
    fi
    [[ -n "$val" ]] && echo "$val" || echo "$default"
}

# Derive the real bind ports/scheme/log dir from the MCP build's .env.
MCP_PORT="$(get_env "$MCP_ENV" MCP_PORT 8010)"
# TLS cert/key from the MCP build's .env. Paths there are relative to the BUILD
# folder (config resolves them against the build dir), so make them ABSOLUTE —
# the now-standalone dashboard resolves relative paths under dashboard/, not the
# build dir, so we hand it fully-resolved paths (injected into its env below).
resolve_under_mcp() {
    local p="$1"
    [[ -z "$p" ]] && { echo ""; return; }
    case "$p" in
        /*) echo "$p" ;;
        *)  realpath -m -- "$MCP_DIR/$p" ;;
    esac
}
MCP_TLS_CERT_ABS="$(resolve_under_mcp "$(get_env "$MCP_ENV" MCP_TLS_CERT '')")"
MCP_TLS_KEY_ABS="$(resolve_under_mcp "$(get_env "$MCP_ENV" MCP_TLS_KEY '')")"
if [[ -n "$MCP_TLS_CERT_ABS" && -n "$MCP_TLS_KEY_ABS" ]]; then MCP_SCHEME=https; else MCP_SCHEME=http; fi
# LOG_DIR in the build's .env is relative to the BUILD folder (config resolves it
# against the build dir, not cwd) — resolve it the same way for the dashboard tab.
MCP_LOGDIR_RAW="$(get_env "$MCP_ENV" LOG_DIR logs)"
case "$MCP_LOGDIR_RAW" in /*) MCP_LOGDIR="$MCP_LOGDIR_RAW";; *) MCP_LOGDIR="$MCP_DIR/$MCP_LOGDIR_RAW";; esac
# Transport from .env (default streamable-http); drives the banner path and is
# forwarded to the child so an HTTP transport is guaranteed even if .env omits it.
MCP_TRANSPORT_V="$(get_env "$MCP_ENV" MCP_TRANSPORT streamable-http)"
if [[ "$MCP_TRANSPORT_V" == "sse" ]]; then MCP_PATH=/sse; else MCP_PATH=/mcp; fi
BACKEND_PORT_V="$(get_env "$BACKEND_ENV" CHAT_PORT 8002)"
DASH_PORT_V="$(get_env "$DASHBOARD_ENV" MCP_DASHBOARD_PORT 8004)"
# The dashboard mirrors the MCP build's TLS (cert/key injected into its env below).
DASH_SCHEME="$MCP_SCHEME"

# --- setup helper ----------------------------------------------------------
# init_venv <label> <venv_dir> <requirements_file...>  (one or more -r files)
# Every venv is module-level — nothing is ever created at the repo root.
init_venv() {
    local label="$1" venv_dir="$2"; shift 2
    local py="$venv_dir/.venv/bin/python"
    if [[ ! -x "$py" ]]; then
        step "[$label] creating venv in $venv_dir/.venv"
        "$PYTHON_BIN" -m venv "$venv_dir/.venv"
    fi
    local req_args=()
    for r in "$@"; do req_args+=("-r" "$r"); done
    step "[$label] pip install ${req_args[*]}"
    "$py" -m pip install --quiet --upgrade pip
    "$py" -m pip install "${req_args[@]}"
    ok "[$label] dependencies installed"
}

# prompt_llm — set global LLM to openai|gemini|claude (honours --llm if given).
prompt_llm() {
    if [[ -n "$LLM" ]]; then return; fi
    echo
    step "Select the LLM provider for the agent"
    note "[1] OpenAI  (langchain-openai, OPENAI_API_KEY)"
    note "[2] Gemini  (langchain-google-genai, GOOGLE_API_KEY)"
    note "[3] Claude  (langchain-anthropic, ANTHROPIC_API_KEY)"
    while [[ -z "$LLM" ]]; do
        read -r -p "Provider [1/2/3 or openai/gemini/claude] (default 1): " ans
        case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
            ""|1|openai)      LLM=openai ;;
            2|gemini|google)  LLM=gemini ;;
            3|claude|anthropic) LLM=claude ;;
            *) bad "Enter 1, 2, or 3 (or openai/gemini/claude)." ;;
        esac
    done
}

# agent_req_files — echo the base + provider-overlay requirements for $LLM.
agent_req_files() {
    local base="$BACKEND_DIR/requirements.txt"
    case "$LLM" in
        gemini) echo "$base $BACKEND_DIR/requirements-gemini.txt" ;;
        claude) echo "$base $BACKEND_DIR/requirements-claude.txt" ;;
        *)      echo "$base" ;;   # openai lives in the base requirements
    esac
}

# init_env_files <label:dir>... — copy each module's .env.example to .env when
# it has no .env yet, after a single confirmation. NEVER overwrites an existing
# .env, so re-running --setup is safe.
init_env_files() {
    local to_create=()
    local entry label dir
    for entry in "$@"; do
        label="${entry%%:*}"; dir="${entry#*:}"
        if [[ -f "$dir/.env" ]]; then note "$label: .env already exists - keeping it"; continue; fi
        if [[ ! -f "$dir/.env.example" ]]; then note "$label: no .env.example - skipping"; continue; fi
        to_create+=("$entry")
    done
    if [[ ${#to_create[@]} -eq 0 ]]; then ok "All module .env files already present (nothing to copy)."; return; fi
    step "These modules have no .env and will be created from .env.example:"
    for entry in "${to_create[@]}"; do note "- ${entry%%:*} (${entry#*:}/.env)"; done
    read -r -p "Create these .env files now? [Y/n]: " ans
    case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
        n|no) note "Skipped .env creation. Copy each module's .env.example to .env before running."; return ;;
    esac
    for entry in "${to_create[@]}"; do
        dir="${entry#*:}"
        cp "$dir/.env.example" "$dir/.env"
        ok "${entry%%:*}: created .env from .env.example"
    done
}

# set_env_value <file> <key> <value> — update-or-append KEY=value in a .env.
set_env_value() {
    local file="$1" key="$2" value="$3"
    if [[ -f "$file" ]] && grep -qE "^[[:space:]]*${key}[[:space:]]*=" "$file"; then
        sed -i -E "s|^[[:space:]]*${key}[[:space:]]*=.*$|${key}=${value}|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
    ok "agent/.env: ${key}=${value}"
}

if [[ $DO_SETUP -eq 1 ]]; then
    # First, seed each module's .env from its .env.example (with confirmation) so
    # the LLM_PROVIDER write below lands in a real agent/.env.
    env_modules=()
    [[ $SKIP_MCP -eq 0 ]]       && env_modules+=("mcp:$MCP_DIR")
    [[ $SKIP_BACKEND -eq 0 ]]   && env_modules+=("agent:$BACKEND_DIR")
    [[ $SKIP_FRONTEND -eq 0 ]]  && env_modules+=("frontend:$FRONTEND_DIR")
    [[ $SKIP_DASHBOARD -eq 0 ]] && env_modules+=("dashboard:$DASHBOARD_DIR")
    [[ ${#env_modules[@]} -gt 0 ]] && init_env_files "${env_modules[@]}"
    echo

    step "Setup: installing per-component requirements"
    # Every component gets its OWN module-level venv — nothing at the repo root.
    [[ $SKIP_MCP -eq 0 ]]       && init_venv "mcp"       "$MCP_DIR"       "$MCP_REQS"
    if [[ $SKIP_BACKEND -eq 0 ]]; then
        prompt_llm
        ok "LLM provider: $LLM"
        # shellcheck disable=SC2046
        init_venv "agent" "$BACKEND_DIR" $(agent_req_files)
        set_env_value "$BACKEND_ENV" "LLM_PROVIDER" "$LLM"
        note "Remember to set the matching API key in agent/.env (OPENAI_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY)."
    fi
    [[ $SKIP_FRONTEND -eq 0 ]]  && init_venv "frontend"  "$FRONTEND_DIR"  "$FRONTEND_DIR/requirements.txt"
    [[ $SKIP_DASHBOARD -eq 0 ]] && init_venv "dashboard" "$DASHBOARD_DIR" "$DASHBOARD_DIR/requirements.txt"
    echo
fi

# --- pre-flight ------------------------------------------------------------
problems=()

if [[ $SKIP_MCP -eq 0 ]]; then
    step "Checking MCP server prerequisites"
    if [[ ! -x "$MCP_VENV_PY" ]]; then
        problems+=("Missing MCP venv. Fix: ./scripts/start-all.sh --setup"); bad "mqacemcpserver/.venv/bin/python not found"
    else ok "mqacemcpserver/.venv present"; fi
    [[ -f "$MCP_ENTRY" ]] && ok "mqacemcpserver.py present (:$MCP_PORT)" || { problems+=("Missing MCP entry $MCP_ENTRY."); bad "$MCP_ENTRY not found"; }
    [[ -f "$MCP_ENV" ]] && ok "mqacemcpserver/.env present" || note "mqacemcpserver/.env missing (server will start but tools may error)."
fi

if [[ $SKIP_BACKEND -eq 0 ]]; then
    step "Checking chat backend prerequisites"
    [[ -x "$BACKEND_DIR/.venv/bin/python" ]] && ok "backend venv present" || { problems+=("Missing backend venv. Fix: ./scripts/start-all.sh --setup"); bad "agent/.venv/bin/python not found"; }
    [[ -f "$BACKEND_DIR/app.py" ]] && ok "backend app.py present" || { problems+=("Missing agent/app.py."); bad "agent/app.py not found"; }
    [[ -f "$BACKEND_ENV" ]] && ok "backend .env present" || { problems+=("Missing agent/.env. Fix: cd agent && cp .env.example .env && edit it (OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*)"); bad "agent/.env not found"; }
fi

if [[ $SKIP_FRONTEND -eq 0 ]]; then
    step "Checking Streamlit UI prerequisites"
    [[ -x "$FRONTEND_DIR/.venv/bin/python" ]] && ok "frontend venv present" || { problems+=("Missing Streamlit venv. Fix: ./scripts/start-all.sh --setup"); bad "frontend/.venv/bin/python not found"; }
    [[ -f "$FRONTEND_DIR/app.py" ]] && ok "frontend app.py present" || { problems+=("Missing frontend/app.py."); bad "frontend/app.py not found"; }
    [[ -f "$FRONTEND_ENV" ]] && ok "frontend .env present" || note "frontend/.env missing - defaults to MCP_BACKEND_URL=http://localhost:8002."
fi

if [[ $SKIP_DASHBOARD -eq 0 ]]; then
    step "Checking dashboard prerequisites"
    [[ -x "$DASHBOARD_DIR/.venv/bin/python" ]] && ok "dashboard venv present" || { problems+=("Missing dashboard venv. Fix: ./scripts/start-all.sh --setup"); bad "dashboard/.venv/bin/python not found"; }
    [[ -f "$DASHBOARD_DIR/dashboard_server.py" ]] && ok "dashboard_server.py present" || { problems+=("Missing dashboard/dashboard_server.py."); bad "dashboard/dashboard_server.py not found"; }
    [[ -f "$DASHBOARD_ENV" ]] && ok "dashboard .env present" || note "dashboard/.env missing - defaults used (host=0.0.0.0, port=8004)."
fi

if [[ ${#problems[@]} -gt 0 ]]; then
    echo; bad "Pre-flight failed. Resolve the items above (tip: --setup builds the venvs):"
    for p in "${problems[@]}"; do printf '\033[33m    - %s\033[0m\n' "$p"; done
    exit 1
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
    echo; ok "All checks passed. (--check-only specified, not starting services.)"
    exit 0
fi

# --- launch ----------------------------------------------------------------
mkdir -p "$LOG_DIR"
: > "$PID_FILE"

# start_service <title> <workdir> <logname> <command...>
start_service() {
    local title="$1" workdir="$2" logname="$3"; shift 3
    step "Starting $title"
    note "cwd: $workdir"
    note "log: $LOG_DIR/$logname.log"
    ( cd "$workdir" && nohup "$@" >"$LOG_DIR/$logname.log" 2>&1 & echo $! >>"$PID_FILE" )
    ok "$title started (PID $(tail -n1 "$PID_FILE"))"
}

# 1. MCP server (reads mqacemcpserver/.env via __file__ -> :8010)
if [[ $SKIP_MCP -eq 0 ]]; then
    ( cd "$REPO_ROOT" && MCP_TRANSPORT="$MCP_TRANSPORT_V" nohup "$MCP_VENV_PY" "$MCP_ENTRY" >"$LOG_DIR/mcp.log" 2>&1 & echo $! >>"$PID_FILE" )
    ok "MCP Server (:$MCP_PORT $MCP_TRANSPORT_V) started (PID $(tail -n1 "$PID_FILE"))"
    sleep 3  # let the MCP server bind before the backend connects
fi

# 2. Chat backend
if [[ $SKIP_BACKEND -eq 0 ]]; then
    start_service "Agent (FastAPI :$BACKEND_PORT_V)" "$BACKEND_DIR" "agent" "$BACKEND_DIR/.venv/bin/python" app.py
    sleep 3  # let the backend load tools before the frontend hits it
fi

# 3. Streamlit UI (frontend)
if [[ $SKIP_FRONTEND -eq 0 ]]; then
    start_service "Streamlit UI (:$PORT)" "$FRONTEND_DIR" "frontend" \
        "$FRONTEND_DIR/.venv/bin/python" -m streamlit run app.py \
        --server.port "$PORT" --server.address 0.0.0.0 --server.headless true
    sleep 3  # settle before the dashboard starts
fi

# 4. Dashboard
if [[ $SKIP_DASHBOARD -eq 0 ]]; then
    # Render one tab for the MCP build: hand it the build's log dir via
    # MCP_DASHBOARD_SERVERS_JSON. dashboard_server.py is self-contained (loads its
    # own dashboard/.env), but process env wins — so we inject the config here,
    # including TLS mirrored from the MCP build so the scheme matches DASH_SCHEME.
    # Build the JSON with python so paths and quotes are escaped correctly.
    DASH_SERVERS_JSON="$("$PYTHON_BIN" -c 'import json,sys; print(json.dumps([{"name":sys.argv[1],"key":"single","log_dir":sys.argv[2]}]))' \
        "mqacemcpserver (:$MCP_PORT)" "$MCP_LOGDIR")"
    dash_env=(
        "MCP_DASHBOARD_PORT=$DASH_PORT_V"
        "MCP_DASHBOARD_SERVERS_JSON=$DASH_SERVERS_JSON"
        "MCP_DASHBOARD_REFRESH_SECONDS=60"
    )
    if [[ -n "$MCP_TLS_CERT_ABS" && -n "$MCP_TLS_KEY_ABS" ]]; then
        dash_env+=("MCP_TLS_CERT=$MCP_TLS_CERT_ABS" "MCP_TLS_KEY=$MCP_TLS_KEY_ABS")
    else
        # No TLS on the build → clear any inherited values so it serves plain HTTP.
        dash_env=(-u MCP_TLS_CERT -u MCP_TLS_KEY "${dash_env[@]}")
    fi
    start_service "Dashboard (:$DASH_PORT_V)" "$DASHBOARD_DIR" "dashboard" \
        env "${dash_env[@]}" \
        "$DASHBOARD_DIR/.venv/bin/python" dashboard_server.py
fi

echo
ok "All requested services launched."
echo
echo "Endpoints"
if [[ $SKIP_MCP -eq 0 ]]; then
    echo "  MCP server (:$MCP_PORT)"
    echo "    Endpoint   : $MCP_SCHEME://localhost:$MCP_PORT$MCP_PATH"
    echo "    Health     : $MCP_SCHEME://localhost:$MCP_PORT/healthz"
fi
if [[ $SKIP_BACKEND -eq 0 ]]; then
    echo "  Chat backend (:$BACKEND_PORT_V)"
    echo "    Health     : http://localhost:$BACKEND_PORT_V/api/health"
    echo "    Chat stream: http://localhost:$BACKEND_PORT_V/api/chat/stream"
    echo "    Chat reset : http://localhost:$BACKEND_PORT_V/api/chat/reset"
fi
if [[ $SKIP_FRONTEND -eq 0 ]]; then
    echo "  Streamlit UI (:$PORT)"
    echo "    UI         : http://localhost:$PORT"
    echo "    Health     : http://localhost:$PORT/_stcore/health"
fi
if [[ $SKIP_DASHBOARD -eq 0 ]]; then
    echo "  Dashboard (:$DASH_PORT_V)"
    echo "    Dashboard  : $DASH_SCHEME://localhost:$DASH_PORT_V/dashboard"
    echo "    Health     : $DASH_SCHEME://localhost:$DASH_PORT_V/healthz"
fi
echo
echo "  Logs    : $LOG_DIR/*.log"
echo "  To stop : ./scripts/stop-all.sh"
