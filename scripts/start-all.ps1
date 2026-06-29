<#
.SYNOPSIS
    Starts the local processes for the full MQ+ACE stack on one machine.

.DESCRIPTION
    Opens one new PowerShell window per service so each has its own visible log
    stream:
      1. MCP server   (mqacemcpserver\mqacemcpserver.py, Streamable HTTP on :8010)
      2. Chat backend (backend\app.py, FastAPI on :8002)
      3. Streamlit UI (frontend\app.py, on :8003)
      4. Dashboard    (dashboard\dashboard_server.py, on :8004)

    The MCP server reads its OWN mqacemcpserver\.env (MCP_PORT=8010).
    There is no repo-root .env. The backend connects to it by default; users can
    point at a custom server from the Streamlit sidebar.

    Ports/scheme for MCP, backend, and dashboard are read from the .env files at
    runtime (MCP_PORT/MCP_TLS_*, backend CHAT_PORT, MCP_DASHBOARD_PORT); the
    Streamlit port is set by -Port below. The values above are this repo's
    current configuration.

    Each component is self-contained with its own requirements.txt. The MCP
    server uses the repo-root .venv; backend, frontend, and dashboard each have
    their own .venv. Pass -Setup to create any missing venvs and
    `pip install -r` each component's requirements before launching.

    PIDs of spawned windows are written to scripts\.pids so stop-all.ps1 can
    clean them up.

.PARAMETER Setup
    Create any missing venvs and install each (non-skipped) component's
    requirements.txt before starting. Safe to re-run.

.PARAMETER SkipMcp
    Do not start the MCP server (e.g. it is already running elsewhere).

.PARAMETER SkipBackend
    Do not start the chat backend.

.PARAMETER SkipFrontend
    Do not start the Streamlit UI.

.PARAMETER SkipDashboard
    Do not start the dashboard.

.PARAMETER CheckOnly
    Run all pre-flight checks (and -Setup if given) and exit without starting.

.PARAMETER Port
    Streamlit port (default 8003).

.EXAMPLE
    .\scripts\start-all.ps1 -Setup          # first run: build venvs, then start all

.EXAMPLE
    .\scripts\start-all.ps1                  # start the MCP server + the stack

.EXAMPLE
    .\scripts\start-all.ps1 -SkipMcp -SkipDashboard
#>
[CmdletBinding()]
param(
    [switch]$Setup,
    [switch]$SkipMcp,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$SkipDashboard,
    [switch]$CheckOnly,
    [int]$Port = 8003
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location so the script works from any cwd.
$RepoRoot     = Split-Path -Parent $PSScriptRoot
# The MCP server reads its own .env for MCP_PORT (mqacemcpserver\.env, :8010).
$McpDir       = Join-Path $RepoRoot "mqacemcpserver"
$McpEntry     = Join-Path $McpDir "mqacemcpserver.py"
$McpReqs      = Join-Path $McpDir "requirements.txt"
$BackendDir   = Join-Path $RepoRoot "backend"
$FrontendDir  = Join-Path $RepoRoot "frontend"
$DashboardDir = Join-Path $RepoRoot "dashboard"
$RootVenvPy   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PidFile      = Join-Path $PSScriptRoot ".pids"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)   { Write-Host "  !!  $msg" -ForegroundColor Red }
function Write-Note($msg)  { Write-Host "      $msg" -ForegroundColor DarkGray }

# Read a KEY=value from a .env file so the endpoint output reflects the actual
# ports/scheme the services bind (they read these same vars at runtime).
function Get-EnvValue {
    param([string]$File, [string]$Key, [string]$Default)
    if (Test-Path $File) {
        foreach ($line in Get-Content $File) {
            if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") {
                $v = $Matches[1].Trim()
                if ($v) { return $v }
            }
        }
    }
    return $Default
}

# Derive the real bind ports/scheme. MCP and the dashboard share MCP_TLS_* (so
# both serve HTTPS when a cert is configured); the backend is plain HTTP.
$BackendEnv   = Join-Path $BackendDir ".env"
$DashboardEnv = Join-Path $DashboardDir ".env"
# The MCP server reads its OWN mqacemcpserver\.env. Read MCP_PORT /
# MCP_TLS_CERT / LOG_DIR from it so the banner and the dashboard tab match what binds.
$McpEnv      = Join-Path $McpDir ".env"
$McpPort     = Get-EnvValue $McpEnv "MCP_PORT" "8010"
$McpScheme   = if (Get-EnvValue $McpEnv "MCP_TLS_CERT" "") { "https" } else { "http" }
# LOG_DIR in the build's .env is relative to the BUILD folder (config resolves it
# against the build dir, not cwd) — resolve it the same way for the dashboard tab.
$McpLogDirRaw = Get-EnvValue $McpEnv "LOG_DIR" "logs"
$McpLogDir   = if ([System.IO.Path]::IsPathRooted($McpLogDirRaw)) { $McpLogDirRaw } else { Join-Path $McpDir $McpLogDirRaw }
# Transport from .env (default streamable-http). Drives the banner path and is
# forwarded to the child so an HTTP transport is guaranteed even if .env omits it.
$McpTransport = (Get-EnvValue $McpEnv "MCP_TRANSPORT" "streamable-http").ToLower()
$McpPath     = if ($McpTransport -eq "sse") { "/sse" } else { "/mcp" }
$BackendPort = Get-EnvValue $BackendEnv "CHAT_PORT" "8002"
# The dashboard's own port lives in dashboard\.env (its authoritative config);
# fall back to the code default. Keep this the single source so the banner
# matches the port we hand the process below.
$DashPort    = Get-EnvValue $DashboardEnv "MCP_DASHBOARD_PORT" "8004"
# The dashboard shares the MCP build's TLS config (MCP_SERVER_DIR points there).
$DashScheme  = $McpScheme

# ---------------------------------------------------------------------------
# Setup helper — create a venv (if missing) and install its requirements.
# $VenvDir is where the .venv lives; $ReqFile is the requirements to install.
# ---------------------------------------------------------------------------
function Initialize-Venv {
    param([string]$Label, [string]$VenvDir, [string]$ReqFile)
    $py = Join-Path $VenvDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Step "[$Label] creating venv in $VenvDir\.venv"
        & python -m venv (Join-Path $VenvDir ".venv")
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed for $Label" }
    }
    Write-Step "[$Label] pip install -r $ReqFile"
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install failed for $Label" }
    Write-Ok "[$Label] dependencies installed"
}

if ($Setup) {
    Write-Step "Setup: installing per-component requirements"
    # The MCP server uses the repo-root .venv.
    if (-not $SkipMcp)       { Initialize-Venv -Label "mcp"       -VenvDir $RepoRoot     -ReqFile $McpReqs }
    if (-not $SkipBackend)   { Initialize-Venv -Label "backend"   -VenvDir $BackendDir   -ReqFile (Join-Path $BackendDir "requirements.txt") }
    if (-not $SkipFrontend)  { Initialize-Venv -Label "frontend"  -VenvDir $FrontendDir  -ReqFile (Join-Path $FrontendDir "requirements.txt") }
    if (-not $SkipDashboard) { Initialize-Venv -Label "dashboard" -VenvDir $DashboardDir -ReqFile (Join-Path $DashboardDir "requirements.txt") }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
$problems = @()

if (-not $SkipMcp) {
    Write-Step "Checking MCP server prerequisites"
    if (-not (Test-Path $RootVenvPy)) {
        $problems += "Missing MCP venv. Fix: .\scripts\start-all.ps1 -Setup   (or: cd `"$RepoRoot`" ; python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r `"$McpReqs`")"
        Write-Bad ".venv\Scripts\python.exe not found"
    } else { Write-Ok ".venv present" }
    if (-not (Test-Path $McpEntry)) {
        $problems += "Missing MCP entry $McpEntry."
        Write-Bad "$McpEntry not found"
    } else { Write-Ok "mqacemcpserver.py present (:$McpPort)" }
    if (-not (Test-Path $McpEnv)) {
        Write-Note "mqacemcpserver\.env missing (server will start but tools may error). Copy mqacemcpserver\.env.example to mqacemcpserver\.env and fill MQ_*/ACE_* values if you need real data."
    } else { Write-Ok "mqacemcpserver\.env present" }
}

if (-not $SkipBackend) {
    Write-Step "Checking chat backend prerequisites"
    if (-not (Test-Path (Join-Path $BackendDir ".venv\Scripts\python.exe"))) {
        $problems += "Missing backend venv. Fix: .\scripts\start-all.ps1 -Setup"
        Write-Bad "backend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "backend venv present" }
    if (-not (Test-Path (Join-Path $BackendDir "app.py"))) {
        $problems += "Missing backend\app.py."; Write-Bad "backend\app.py not found"
    } else { Write-Ok "backend app.py present" }
    if (-not (Test-Path (Join-Path $BackendDir ".env"))) {
        $problems += "Missing backend\.env. Fix: cd `"$BackendDir`" ; copy .env.example .env ; then edit it (OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*)"
        Write-Bad "backend\.env not found"
    } else { Write-Ok "backend .env present" }
}

if (-not $SkipFrontend) {
    Write-Step "Checking Streamlit UI prerequisites"
    if (-not (Test-Path (Join-Path $FrontendDir ".venv\Scripts\python.exe"))) {
        $problems += "Missing Streamlit venv. Fix: .\scripts\start-all.ps1 -Setup"
        Write-Bad "frontend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "frontend venv present" }
    if (-not (Test-Path (Join-Path $FrontendDir "app.py"))) {
        $problems += "Missing frontend\app.py."; Write-Bad "frontend\app.py not found"
    } else { Write-Ok "frontend app.py present" }
    if (-not (Test-Path (Join-Path $FrontendDir ".env"))) {
        Write-Note "frontend\.env missing - defaults to MCP_BACKEND_URL=http://localhost:8002. Copy .env.example if you need to override."
    } else { Write-Ok "frontend .env present" }
}

if (-not $SkipDashboard) {
    Write-Step "Checking dashboard prerequisites"
    if (-not (Test-Path (Join-Path $DashboardDir ".venv\Scripts\python.exe"))) {
        $problems += "Missing dashboard venv. Fix: .\scripts\start-all.ps1 -Setup"
        Write-Bad "dashboard\.venv\Scripts\python.exe not found"
    } else { Write-Ok "dashboard venv present" }
    if (-not (Test-Path (Join-Path $DashboardDir "dashboard_server.py"))) {
        $problems += "Missing dashboard\dashboard_server.py."; Write-Bad "dashboard\dashboard_server.py not found"
    } else { Write-Ok "dashboard_server.py present" }
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Bad "Pre-flight failed. Resolve the items above (tip: -Setup builds the venvs):"
    $problems | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    exit 1
}

if ($CheckOnly) {
    Write-Host ""
    Write-Ok "All checks passed. (CheckOnly was specified, not starting services.)"
    exit 0
}

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
$pids = @()

function Start-Service-Window {
    param([string]$Title, [string]$WorkingDirectory, [string]$Command)
    Write-Step "Starting $Title"
    Write-Note "cwd: $WorkingDirectory"
    Write-Note "cmd: $Command"
    $script = "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoExit", "-NoLogo", "-Command", $script) `
        -WorkingDirectory $WorkingDirectory `
        -PassThru
    Write-Ok "$Title PID=$($proc.Id)"
    return $proc.Id
}

if (-not $SkipMcp) {
    # The MCP build resolves its own .env via __file__; cwd is the repo root so
    # the shared resources/ and the relative entry path resolve.
    $entryRel = $McpEntry.Substring($RepoRoot.Length).TrimStart('\')
    $cmd = "`$env:MCP_TRANSPORT='$McpTransport'; .\.venv\Scripts\python.exe `"$entryRel`""
    $pids += Start-Service-Window -Title "MCP Server (:$McpPort $McpTransport)" `
        -WorkingDirectory $RepoRoot -Command $cmd
    Start-Sleep -Seconds 3  # let the MCP server bind before the backend connects
}

if (-not $SkipBackend) {
    $cmd = ".\.venv\Scripts\python.exe app.py"
    $pids += Start-Service-Window -Title "Chat Backend (FastAPI :$BackendPort)" `
        -WorkingDirectory $BackendDir -Command $cmd
    Start-Sleep -Seconds 3  # let the backend load tools before the frontend hits it
}

if (-not $SkipFrontend) {
    $cmd = ".\.venv\Scripts\python.exe -m streamlit run app.py --server.port $Port"
    $pids += Start-Service-Window -Title "Streamlit UI (:$Port)" `
        -WorkingDirectory $FrontendDir -Command $cmd
    Start-Sleep -Seconds 3  # settle before the dashboard window opens
}

if (-not $SkipDashboard) {
    # Run from repo root so relative paths (e.g. TLS certs/cert.pem, resources)
    # resolve the same way they do for the MCP server. Imports use __file__.
    #
    # The dashboard renders one tab for the MCP build. We hand it the build's log
    # dir via MCP_DASHBOARD_SERVERS_JSON; it reads that directory directly, so the
    # tab shows the build's logs regardless of whether the server is running.
    # MCP_SERVER_DIR points at the MCP build for its shared TLS config.
    #
    # dashboard_server.py never loads dashboard\.env itself — it reads process
    # env (set below, inherited by the spawned window) plus the build's
    # server.config. Setting the JSON via the parent environment (rather than
    # inlining it in the command) avoids Start-Process double-quote mangling.
    $dashServers = @(
        @{ name = "mqacemcpserver (:$McpPort)"; key = "single"; log_dir = "$McpLogDir" }
    )
    $env:MCP_SERVER_DIR            = $McpDir
    $env:MCP_DASHBOARD_PORT        = $DashPort
    $env:MCP_DASHBOARD_SERVERS_JSON = ($dashServers | ConvertTo-Json -Compress -Depth 5)
    # Auto-refresh each dashboard page every N seconds (0 disables).
    $env:MCP_DASHBOARD_REFRESH_SECONDS = "60"
    $cmd = ".\dashboard\.venv\Scripts\python.exe dashboard\dashboard_server.py"
    $pids += Start-Service-Window -Title "Dashboard (:$DashPort)" `
        -WorkingDirectory $RepoRoot -Command $cmd
}

# Persist PIDs so stop-all.ps1 can find them.
$pids | Out-File -FilePath $PidFile -Encoding ascii

Write-Host ""
Write-Ok "All requested services launched."
Write-Host ""
Write-Host "Endpoints" -ForegroundColor White
if (-not $SkipMcp) {
    Write-Host "  MCP server (:$McpPort)" -ForegroundColor Cyan
    Write-Host "    Endpoint   : ${McpScheme}://localhost:$McpPort$McpPath"   -ForegroundColor Gray
    Write-Host "    Health     : ${McpScheme}://localhost:$McpPort/healthz"  -ForegroundColor Gray
}
if (-not $SkipBackend) {
    Write-Host "  Chat backend (:$BackendPort)" -ForegroundColor Cyan
    Write-Host "    Health     : http://localhost:$BackendPort/api/health"      -ForegroundColor Gray
    Write-Host "    Chat stream: http://localhost:$BackendPort/api/chat/stream" -ForegroundColor Gray
    Write-Host "    Chat reset : http://localhost:$BackendPort/api/chat/reset"  -ForegroundColor Gray
}
if (-not $SkipFrontend) {
    Write-Host "  Streamlit UI (:$Port)" -ForegroundColor Cyan
    Write-Host "    UI         : http://localhost:$Port"                -ForegroundColor Gray
    Write-Host "    Health     : http://localhost:$Port/_stcore/health" -ForegroundColor Gray
}
if (-not $SkipDashboard) {
    Write-Host "  Dashboard (:$DashPort)" -ForegroundColor Cyan
    Write-Host "    Dashboard  : ${DashScheme}://localhost:$DashPort/dashboard" -ForegroundColor Gray
    Write-Host "    Health     : ${DashScheme}://localhost:$DashPort/healthz"   -ForegroundColor Gray
}
Write-Host ""
Write-Host "  To stop everything, run:  .\scripts\stop-all.ps1" -ForegroundColor DarkGray
