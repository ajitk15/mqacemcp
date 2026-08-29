<#
.SYNOPSIS
    Starts the local processes for the full MQ+ACE stack on one machine.

.DESCRIPTION
    Opens one new PowerShell window per service so each has its own visible log
    stream:
      1. MCP server   (mqacemcpserver\mqacemcpserver.py, Streamable HTTP on :8010)
      2. Agent (agent\app.py, FastAPI on :8002)
      3. Streamlit UI (frontend\app.py, on :8003)
      4. Dashboard    (dashboard\dashboard_server.py, on :8004)

    The MCP server reads its OWN mqacemcpserver\.env (MCP_PORT=8010).
    There is no repo-root .env. The backend connects to it by default; users can
    point at a custom server from the Streamlit sidebar.

    Ports/scheme for MCP, backend, and dashboard are read from the .env files at
    runtime (MCP_PORT/MCP_TLS_*, backend CHAT_PORT, MCP_DASHBOARD_PORT); the
    Streamlit port is set by -Port below. The values above are this repo's
    current configuration.

    Each component is self-contained with its own module-level .venv
    (mqacemcpserver\.venv, agent\.venv, frontend\.venv, dashboard\.venv). No
    repo-root .venv is created. Pass -Setup to create any missing venvs and
    `pip install -r` each component's requirements before launching.

    PIDs of spawned windows are written to scripts\.pids so stop-all.ps1 can
    clean them up.

.PARAMETER Setup
    Seed each module's .env from its .env.example (asks for confirmation first;
    never overwrites an existing .env), then create any missing venvs and install
    each (non-skipped) component's requirements.txt before starting. Safe to
    re-run. For the agent it also installs the LLM provider chosen via -Llm (or
    prompted for interactively) and records LLM_PROVIDER in agent\.env.

.PARAMETER Llm
    LLM provider for the agent: openai | gemini | claude. When -Setup is given
    and this is omitted, the script prompts for it interactively. Ignored when
    -SkipBackend is set.

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

.PARAMETER Yes
    Accept setup confirmation prompts. Intended for CI and other
    non-interactive runs; existing .env files are still never overwritten.

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
    [ValidateSet("openai", "gemini", "claude")]
    [string]$Llm,
    [switch]$SkipMcp,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$SkipDashboard,
    [switch]$CheckOnly,
    [switch]$Yes,
    [int]$Port = 8003
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location so the script works from any cwd.
$RepoRoot     = Split-Path -Parent $PSScriptRoot
# The MCP server reads its own .env for MCP_PORT (mqacemcpserver\.env, :8010).
$McpDir       = Join-Path $RepoRoot "mqacemcpserver"
$McpEntry     = Join-Path $McpDir "mqacemcpserver.py"
$McpReqs      = Join-Path $McpDir "requirements.txt"
$BackendDir   = Join-Path $RepoRoot "agent"
$FrontendDir  = Join-Path $RepoRoot "frontend"
$DashboardDir = Join-Path $RepoRoot "dashboard"
# The MCP server has its OWN module-level venv (no repo-root .venv is created).
$McpVenvPy    = Join-Path $McpDir ".venv\Scripts\python.exe"
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
# TLS cert/key from the MCP build's .env. Paths there are relative to the BUILD
# folder (config resolves them against the build dir), so make them ABSOLUTE —
# the now-standalone dashboard resolves relative paths under dashboard\, not the
# build dir, so we hand it fully-resolved paths (injected into its env below).
$McpTlsCertRaw = Get-EnvValue $McpEnv "MCP_TLS_CERT" ""
$McpTlsKeyRaw  = Get-EnvValue $McpEnv "MCP_TLS_KEY" ""
function Resolve-UnderMcp([string]$p) {
    if (-not $p) { return "" }
    if ([System.IO.Path]::IsPathRooted($p)) { return $p }
    return [System.IO.Path]::GetFullPath((Join-Path $McpDir $p))
}
$McpTlsCert  = Resolve-UnderMcp $McpTlsCertRaw
$McpTlsKey   = Resolve-UnderMcp $McpTlsKeyRaw
$McpScheme   = if ($McpTlsCert -and $McpTlsKey) { "https" } else { "http" }
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
# The dashboard mirrors the MCP build's TLS (cert/key injected into its env below).
$DashScheme  = $McpScheme

# ---------------------------------------------------------------------------
# Setup helper — create a venv (if missing) and install its requirements.
# $VenvDir is where the .venv lives; $ReqFiles is one or more requirements
# files (multiple = a base file plus a provider overlay). Every venv is
# module-level — nothing is ever created at the repo root.
# ---------------------------------------------------------------------------
function Initialize-Venv {
    param([string]$Label, [string]$VenvDir, [string[]]$ReqFiles)
    $py = Join-Path $VenvDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Step "[$Label] creating venv in $VenvDir\.venv"
        & python -m venv (Join-Path $VenvDir ".venv")
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed for $Label" }
    }
    $reqArgs = @()
    foreach ($r in $ReqFiles) { $reqArgs += @("-r", $r) }
    Write-Step "[$Label] pip install $($reqArgs -join ' ')"
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install @reqArgs
    if ($LASTEXITCODE -ne 0) { throw "pip install failed for $Label" }
    Write-Ok "[$Label] dependencies installed"
}

# Ask (once) which LLM provider the agent should use. Returns openai|gemini|claude.
function Get-LlmProvider {
    param([string]$Preselected)
    if ($Preselected) { return $Preselected.ToLower() }
    Write-Host ""
    Write-Step "Select the LLM provider for the agent"
    Write-Host "    [1] OpenAI  (langchain-openai, OPENAI_API_KEY)"       -ForegroundColor Gray
    Write-Host "    [2] Gemini  (langchain-google-genai, GOOGLE_API_KEY)" -ForegroundColor Gray
    Write-Host "    [3] Claude  (langchain-anthropic, ANTHROPIC_API_KEY)" -ForegroundColor Gray
    while ($true) {
        $ans = (Read-Host "Provider [1/2/3 or openai/gemini/claude] (default 1)").Trim().ToLower()
        switch ($ans) {
            { $_ -in @("", "1", "openai") } { return "openai" }
            { $_ -in @("2", "gemini", "google") } { return "gemini" }
            { $_ -in @("3", "claude", "anthropic") } { return "claude" }
            default { Write-Bad "Enter 1, 2, or 3 (or openai/gemini/claude)." }
        }
    }
}

# Map a provider to the requirements files the agent venv needs (base + overlay).
function Get-AgentReqFiles {
    param([string]$Provider)
    $base = Join-Path $BackendDir "requirements.txt"
    switch ($Provider) {
        "gemini" { return @($base, (Join-Path $BackendDir "requirements-gemini.txt")) }
        "claude" { return @($base, (Join-Path $BackendDir "requirements-claude.txt")) }
        default  { return @($base) }   # openai lives in the base requirements
    }
}

# Copy each module's .env.example -> .env when the module has no .env yet, after
# a single confirmation. NEVER overwrites an existing .env (so re-running -Setup
# is safe and never clobbers real credentials). $Modules is an array of
# @{ Label=..; Dir=.. } hashtables.
function Initialize-EnvFiles {
    param([hashtable[]]$Modules, [switch]$AssumeYes)
    $toCreate = @()
    foreach ($m in $Modules) {
        $envFile = Join-Path $m.Dir ".env"
        $example = Join-Path $m.Dir ".env.example"
        if (Test-Path $envFile) { Write-Note "$($m.Label): .env already exists - keeping it"; continue }
        if (-not (Test-Path $example)) { Write-Note "$($m.Label): no .env.example - skipping"; continue }
        $toCreate += $m
    }
    if ($toCreate.Count -eq 0) { Write-Ok "All module .env files already present (nothing to copy)."; return }
    Write-Step "These modules have no .env and will be created from .env.example:"
    $toCreate | ForEach-Object { Write-Host "    - $($_.Label)  ($($_.Dir)\.env)" -ForegroundColor Gray }
    $ans = ""
    if (-not $AssumeYes) {
        $answer = Read-Host "Create these .env files now? [Y/n]"
        $ans = if ($null -eq $answer) { "" } else { $answer.Trim().ToLower() }
    }
    if ($ans -in @("n", "no")) {
        Write-Note "Skipped .env creation. Copy each module's .env.example to .env before running."
        return
    }
    foreach ($m in $toCreate) {
        Copy-Item -Path (Join-Path $m.Dir ".env.example") -Destination (Join-Path $m.Dir ".env")
        Write-Ok "$($m.Label): created .env from .env.example"
    }
}

# Update-or-append KEY=value in a .env file (created if missing). Used to record
# the chosen LLM_PROVIDER so the agent picks the matching model at runtime.
function Set-EnvValue {
    param([string]$File, [string]$Key, [string]$Value)
    $line = "$Key=$Value"
    if (Test-Path $File) {
        $content = Get-Content $File
        if ($content -match "^\s*$([regex]::Escape($Key))\s*=") {
            $content = $content -replace "^\s*$([regex]::Escape($Key))\s*=.*$", $line
            Set-Content -Path $File -Value $content -Encoding ascii
        } else {
            Add-Content -Path $File -Value $line -Encoding ascii
        }
    } else {
        Set-Content -Path $File -Value $line -Encoding ascii
    }
    Write-Ok "agent\.env: $line"
}

if ($Setup) {
    # First, seed each module's .env from its .env.example (with confirmation) so
    # the LLM_PROVIDER write below lands in a real agent\.env.
    $envModules = @()
    if (-not $SkipMcp)       { $envModules += @{ Label = "mcp";       Dir = $McpDir } }
    if (-not $SkipBackend)   { $envModules += @{ Label = "agent";     Dir = $BackendDir } }
    if (-not $SkipFrontend)  { $envModules += @{ Label = "frontend";  Dir = $FrontendDir } }
    if (-not $SkipDashboard) { $envModules += @{ Label = "dashboard"; Dir = $DashboardDir } }
    Initialize-EnvFiles -Modules $envModules -AssumeYes:$Yes
    Write-Host ""

    Write-Step "Setup: installing per-component requirements"
    # Every component gets its OWN module-level venv — nothing at the repo root.
    if (-not $SkipMcp)       { Initialize-Venv -Label "mcp"       -VenvDir $McpDir       -ReqFiles $McpReqs }
    if (-not $SkipBackend)   {
        # Ask which LLM to install for the agent, install base + provider overlay,
        # and record the choice so agent.py instantiates the matching model.
        $provider = Get-LlmProvider -Preselected $Llm
        Write-Ok "LLM provider: $provider"
        Initialize-Venv -Label "agent" -VenvDir $BackendDir -ReqFiles (Get-AgentReqFiles $provider)
        Set-EnvValue -File $BackendEnv -Key "LLM_PROVIDER" -Value $provider
        Write-Note "Remember to set the matching API key in agent\.env (OPENAI_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY)."
    }
    if (-not $SkipFrontend)  { Initialize-Venv -Label "frontend"  -VenvDir $FrontendDir  -ReqFiles (Join-Path $FrontendDir "requirements.txt") }
    if (-not $SkipDashboard) { Initialize-Venv -Label "dashboard" -VenvDir $DashboardDir -ReqFiles (Join-Path $DashboardDir "requirements.txt") }
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
$problems = @()

if (-not $SkipMcp) {
    Write-Step "Checking MCP server prerequisites"
    if (-not (Test-Path $McpVenvPy)) {
        $problems += "Missing MCP venv. Fix: .\scripts\start-all.ps1 -Setup   (or: cd `"$McpDir`" ; python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r `"$McpReqs`")"
        Write-Bad "mqacemcpserver\.venv\Scripts\python.exe not found"
    } else { Write-Ok "mqacemcpserver\.venv present" }
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
        Write-Bad "agent\.venv\Scripts\python.exe not found"
    } else { Write-Ok "backend venv present" }
    if (-not (Test-Path (Join-Path $BackendDir "app.py"))) {
        $problems += "Missing agent\app.py."; Write-Bad "agent\app.py not found"
    } else { Write-Ok "backend app.py present" }
    if (-not (Test-Path (Join-Path $BackendDir ".env"))) {
        $problems += "Missing agent\.env. Fix: cd `"$BackendDir`" ; copy .env.example .env ; then edit it (OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*)"
        Write-Bad "agent\.env not found"
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

# Poll a health endpoint until it answers, instead of guessing with a fixed
# sleep. A slow TLS bind used to let the next tier start against a dead port.
# Never fatal: the backend retries its MCP connection on its own, so a timeout
# here is a warning, not a reason to abort the launch.
function Wait-HttpReady {
    param(
        [string]$Url,
        [string]$Label,
        [int]$TimeoutSec = 45
    )
    Write-Note "waiting for $Label at $Url"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    # PowerShell 7 has -SkipCertificateCheck; 5.1 needs the global callback
    # (the endpoints use a self-signed cert).
    $ps7 = $PSVersionTable.PSVersion.Major -ge 6
    $oldCallback = $null
    if (-not $ps7) {
        $oldCallback = [System.Net.ServicePointManager]::ServerCertificateValidationCallback
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    }
    try {
        while ((Get-Date) -lt $deadline) {
            try {
                if ($ps7) {
                    $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -SkipCertificateCheck -UseBasicParsing
                } else {
                    $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing
                }
                if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
                    Write-Ok "$Label is ready"
                    return $true
                }
            } catch {
                Start-Sleep -Seconds 1
            }
        }
    } finally {
        if (-not $ps7) {
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $oldCallback
        }
    }
    Write-Bad "$Label did not respond within ${TimeoutSec}s - continuing anyway."
    Write-Note "check its window for errors; the backend will keep retrying the MCP connection."
    return $false
}

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
    # cwd stays at the repo root (shared resources/ resolve); use the MCP module venv.
    $cmd = "`$env:MCP_TRANSPORT='$McpTransport'; .\mqacemcpserver\.venv\Scripts\python.exe `"$entryRel`""
    $pids += Start-Service-Window -Title "MCP Server (:$McpPort $McpTransport)" `
        -WorkingDirectory $RepoRoot -Command $cmd
    # Block until the MCP server actually serves /healthz — the backend loads
    # its tool list once at startup, so connecting too early left it with none.
    Wait-HttpReady -Url "${McpScheme}://localhost:$McpPort/healthz" -Label "MCP server" | Out-Null
}

if (-not $SkipBackend) {
    $cmd = ".\.venv\Scripts\python.exe app.py"
    $pids += Start-Service-Window -Title "Agent (FastAPI :$BackendPort)" `
        -WorkingDirectory $BackendDir -Command $cmd
    # The frontend reads /api/health on first paint; wait for it to exist.
    Wait-HttpReady -Url "http://localhost:$BackendPort/api/health" -Label "Agent backend" | Out-Null
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
    #
    # dashboard_server.py is self-contained: it loads its OWN dashboard\.env, but
    # process env wins. We inject the config below (inherited by the spawned
    # window) so the dashboard matches the MCP build — including TLS, so its
    # scheme lines up with $DashScheme. Setting the JSON via the parent
    # environment (rather than inlining it) avoids Start-Process quote mangling.
    $dashServers = @(
        @{ name = "mqacemcpserver (:$McpPort)"; key = "single"; log_dir = "$McpLogDir" }
    )
    $env:MCP_DASHBOARD_PORT        = $DashPort
    $env:MCP_DASHBOARD_SERVERS_JSON = ($dashServers | ConvertTo-Json -Compress -Depth 5)
    # Auto-refresh each dashboard page every N seconds (0 disables).
    $env:MCP_DASHBOARD_REFRESH_SECONDS = "60"
    # Mirror the MCP build's TLS so the dashboard serves the same scheme. When the
    # build has no TLS, clear any inherited values so it falls back to plain HTTP.
    if ($McpTlsCert -and $McpTlsKey) {
        $env:MCP_TLS_CERT = $McpTlsCert
        $env:MCP_TLS_KEY  = $McpTlsKey
    } else {
        Remove-Item Env:MCP_TLS_CERT -ErrorAction SilentlyContinue
        Remove-Item Env:MCP_TLS_KEY  -ErrorAction SilentlyContinue
    }
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
    Write-Host "  Agent (:$BackendPort)" -ForegroundColor Cyan
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
