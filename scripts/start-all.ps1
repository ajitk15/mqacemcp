<#
.SYNOPSIS
    Starts the local processes for the full MQ+ACE stack on one machine.

.DESCRIPTION
    Opens one new PowerShell window per service so each has its own visible log
    stream:
      1. MCP main     (mqacemcpserver\mqacemcpserver.py, SSE on :8009)
      2. MCP single   (mqacemcpserver-single\single_server.py, SSE on :8010)
      3. Chat backend (backend\app.py, FastAPI on :8002)
      4. Streamlit UI (frontend\app.py, on :8003)
      5. Dashboard    (dashboard\dashboard_server.py, on :8004)

    Both MCP builds run side by side: the main build reads the repo-root .env
    (MCP_PORT=8009), the single build reads mqacemcpserver-single\.env
    (MCP_PORT=8010). The backend defaults to the main build; users can switch to
    the single build (or a custom server) from the Streamlit sidebar.

    Ports/scheme for MCP, backend, and dashboard are read from the .env files at
    runtime (MCP_PORT/MCP_TLS_*, backend CHAT_PORT, MCP_DASHBOARD_PORT); the
    Streamlit port is set by -Port below. The values above are this repo's
    current configuration.

    Each component is self-contained with its own requirements.txt. The MCP
    server shares the repo-root .venv; backend, frontend, and dashboard each
    have their own .venv. Pass -Setup to create any missing venvs and
    `pip install -r` each component's requirements before launching.

    PIDs of spawned windows are written to scripts\.pids so stop-all.ps1 can
    clean them up.

.PARAMETER Setup
    Create any missing venvs and install each (non-skipped) component's
    requirements.txt before starting. Safe to re-run.

.PARAMETER SkipMcp
    Do not start either MCP server (e.g. they are already running elsewhere).

.PARAMETER SkipMcpMain
    Do not start the main build (mqacemcpserver, :8009).

.PARAMETER SkipMcpSingle
    Do not start the single build (mqacemcpserver-single, :8010).

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
    .\scripts\start-all.ps1                  # start both MCP builds + the stack

.EXAMPLE
    .\scripts\start-all.ps1 -SkipMcpSingle -SkipDashboard
#>
[CmdletBinding()]
param(
    [switch]$Setup,
    [switch]$SkipMcp,
    [switch]$SkipMcpMain,
    [switch]$SkipMcpSingle,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$SkipDashboard,
    [switch]$CheckOnly,
    [int]$Port = 8003
)

# -SkipMcp is the master switch: skip both builds.
if ($SkipMcp) { $SkipMcpMain = $true; $SkipMcpSingle = $true }
$AnyMcp = (-not $SkipMcpMain) -or (-not $SkipMcpSingle)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location so the script works from any cwd.
$RepoRoot     = Split-Path -Parent $PSScriptRoot
# Both MCP builds run side by side. Each reads its own .env for MCP_PORT: the
# main build the repo-root .env (:8009), the single build its own (:8010).
$McpMainDir    = Join-Path $RepoRoot "mqacemcpserver"
$McpMainEntry  = Join-Path $McpMainDir "mqacemcpserver.py"
$McpSingleDir  = Join-Path $RepoRoot "mqacemcpserver-single"
$McpSingleEntry= Join-Path $McpSingleDir "single_server.py"
$McpReqs       = Join-Path $McpMainDir "requirements.txt"
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
$RootEnv      = Join-Path $RepoRoot ".env"
$BackendEnv   = Join-Path $BackendDir ".env"
$DashboardEnv = Join-Path $DashboardDir ".env"
# Each MCP build reads its own .env: the main build loads the repo-root .env,
# the single build loads mqacemcpserver-single\.env. Read MCP_PORT / MCP_TLS_CERT
# / LOG_DIR from each so the banner and the dashboard tabs match what binds.
$McpMainEnv     = $RootEnv
$McpSingleEnv   = Join-Path $McpSingleDir ".env"
$McpMainPort    = Get-EnvValue $McpMainEnv "MCP_PORT" "8009"
$McpSinglePort  = Get-EnvValue $McpSingleEnv "MCP_PORT" "8010"
$McpMainScheme  = if (Get-EnvValue $McpMainEnv "MCP_TLS_CERT" "") { "https" } else { "http" }
$McpSingleScheme= if (Get-EnvValue $McpSingleEnv "MCP_TLS_CERT" "") { "https" } else { "http" }
# Per-build log dirs for the dashboard tabs (fall back to each build's default).
$McpMainLogDir   = Get-EnvValue $McpMainEnv "LOG_DIR" (Join-Path $RepoRoot "custom-logs")
$McpSingleLogDir = Get-EnvValue $McpSingleEnv "LOG_DIR" (Join-Path $McpSingleDir "logs")
$BackendPort = Get-EnvValue $BackendEnv "CHAT_PORT" "8001"
# The dashboard's own port lives in dashboard\.env (its authoritative config);
# fall back to the root .env, then the code default. Keep this the single source
# so the banner matches the port we hand the process below.
$DashPort    = Get-EnvValue $DashboardEnv "MCP_DASHBOARD_PORT" (Get-EnvValue $RootEnv "MCP_DASHBOARD_PORT" "8002")
# The dashboard shares the main build's TLS config (MCP_SERVER_DIR points there).
$DashScheme  = $McpMainScheme

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
    # Both MCP builds share the repo-root .venv (same dependency set).
    if ($AnyMcp)             { Initialize-Venv -Label "mcp"       -VenvDir $RepoRoot     -ReqFile $McpReqs }
    if (-not $SkipBackend)   { Initialize-Venv -Label "backend"   -VenvDir $BackendDir   -ReqFile (Join-Path $BackendDir "requirements.txt") }
    if (-not $SkipFrontend)  { Initialize-Venv -Label "frontend"  -VenvDir $FrontendDir  -ReqFile (Join-Path $FrontendDir "requirements.txt") }
    if (-not $SkipDashboard) { Initialize-Venv -Label "dashboard" -VenvDir $DashboardDir -ReqFile (Join-Path $DashboardDir "requirements.txt") }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
$problems = @()

if ($AnyMcp) {
    Write-Step "Checking MCP server prerequisites (main + single builds)"
    if (-not (Test-Path $RootVenvPy)) {
        $problems += "Missing MCP venv. Fix: .\scripts\start-all.ps1 -Setup   (or: cd `"$RepoRoot`" ; python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r `"$McpReqs`")"
        Write-Bad ".venv\Scripts\python.exe not found"
    } else { Write-Ok ".venv present" }
    if (-not $SkipMcpMain) {
        if (-not (Test-Path $McpMainEntry)) {
            $problems += "Missing main MCP entry $McpMainEntry."
            Write-Bad "$McpMainEntry not found"
        } else { Write-Ok "mqacemcpserver.py present (:$McpMainPort)" }
    }
    if (-not $SkipMcpSingle) {
        if (-not (Test-Path $McpSingleEntry)) {
            $problems += "Missing single MCP entry $McpSingleEntry."
            Write-Bad "$McpSingleEntry not found"
        } else { Write-Ok "single_server.py present (:$McpSinglePort)" }
    }
    if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
        Write-Note ".env missing at repo root (server will start but tools may error). Copy .env.example to .env and fill MQ_*/ACE_* values if you need real data."
    } else { Write-Ok ".env present" }
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
        Write-Note "frontend\.env missing - defaults to MCP_BACKEND_URL=http://localhost:8001. Copy .env.example if you need to override."
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

if (-not $SkipMcpMain) {
    # Run from repo root so the root .env/resources resolve; entry path is relative.
    $entryRel = $McpMainEntry.Substring($RepoRoot.Length).TrimStart('\')
    $cmd = "`$env:MCP_TRANSPORT='sse'; .\.venv\Scripts\python.exe `"$entryRel`""
    $pids += Start-Service-Window -Title "MCP Main (SSE :$McpMainPort)" `
        -WorkingDirectory $RepoRoot -Command $cmd
}

if (-not $SkipMcpSingle) {
    # Single build resolves its own .env via __file__, so cwd just needs to be the
    # repo root for the shared resources/ and the relative entry path.
    $entryRel = $McpSingleEntry.Substring($RepoRoot.Length).TrimStart('\')
    $cmd = "`$env:MCP_TRANSPORT='sse'; .\.venv\Scripts\python.exe `"$entryRel`""
    $pids += Start-Service-Window -Title "MCP Single (SSE :$McpSinglePort)" `
        -WorkingDirectory $RepoRoot -Command $cmd
}

if ($AnyMcp) {
    Start-Sleep -Seconds 2  # let the servers bind before the backend connects
}

if (-not $SkipBackend) {
    $cmd = ".\.venv\Scripts\python.exe app.py"
    $pids += Start-Service-Window -Title "Chat Backend (FastAPI :$BackendPort)" `
        -WorkingDirectory $BackendDir -Command $cmd
    Start-Sleep -Seconds 2  # let backend load tools before frontend starts hitting it
}

if (-not $SkipFrontend) {
    $cmd = ".\.venv\Scripts\python.exe -m streamlit run app.py --server.port $Port"
    $pids += Start-Service-Window -Title "Streamlit UI (:$Port)" `
        -WorkingDirectory $FrontendDir -Command $cmd
}

if (-not $SkipDashboard) {
    # Run from repo root so relative paths (e.g. TLS certs/cert.pem, resources)
    # resolve the same way they do for the MCP server. Imports use __file__.
    #
    # The dashboard renders one tab per MCP build. We hand it both builds' log
    # dirs via MCP_DASHBOARD_SERVERS_JSON; it reads those directories directly,
    # so each tab shows its build's logs regardless of which servers are running.
    # MCP_SERVER_DIR points at the main build purely for shared TLS config.
    #
    # dashboard_server.py never loads dashboard\.env itself — it reads process
    # env (set below, inherited by the spawned window) plus the build's
    # server.config. Setting the JSON via the parent environment (rather than
    # inlining it in the command) avoids Start-Process double-quote mangling.
    $dashServers = @(
        @{ name = "mqacemcpserver (:$McpMainPort)";          key = "main";   log_dir = "$McpMainLogDir" },
        @{ name = "mqacemcpserver-single (:$McpSinglePort)"; key = "single"; log_dir = "$McpSingleLogDir" }
    )
    $env:MCP_SERVER_DIR            = $McpMainDir
    $env:MCP_DASHBOARD_PORT        = $DashPort
    $env:MCP_DASHBOARD_SERVERS_JSON = ($dashServers | ConvertTo-Json -Compress -Depth 5)
    # Head-to-head benchmark results (written by backend\tests\compare_servers.py)
    # feed the dashboard's Compare tab.
    $env:MCP_DASHBOARD_COMPARE_JSON = (Join-Path $McpMainLogDir "compare_results.json")
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
if (-not $SkipMcpMain) {
    Write-Host "  MCP main (:$McpMainPort)" -ForegroundColor Cyan
    Write-Host "    SSE        : ${McpMainScheme}://localhost:$McpMainPort/sse"      -ForegroundColor Gray
    Write-Host "    Health     : ${McpMainScheme}://localhost:$McpMainPort/healthz"  -ForegroundColor Gray
}
if (-not $SkipMcpSingle) {
    Write-Host "  MCP single (:$McpSinglePort)" -ForegroundColor Cyan
    Write-Host "    SSE        : ${McpSingleScheme}://localhost:$McpSinglePort/sse"      -ForegroundColor Gray
    Write-Host "    Health     : ${McpSingleScheme}://localhost:$McpSinglePort/healthz"  -ForegroundColor Gray
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
