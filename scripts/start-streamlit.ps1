<#
.SYNOPSIS
    Starts the MCP chatbot stack with the Streamlit frontend. Equivalent to
    start-all.ps1 (the Streamlit UI is the only frontend); kept for the explicit
    name and -Port switch.

.DESCRIPTION
    Spawns three PowerShell windows:
      1. MCP server      (mqacemcpserver-single\single_server.py, SSE on :8010)
      2. Chat backend    (FastAPI on :8002)
      3. Streamlit UI    (streamlit run, default :8003)

    MCP / backend ports and scheme are read from the .env files at runtime
    (single build MCP_PORT/MCP_TLS_*, backend CHAT_PORT); the Streamlit port is
    set by -Port below. The values above are this repo's current configuration.

    Pre-flights every venv / .env and refuses to launch until missing
    pieces are fixed.

.PARAMETER SkipMcp
    Skip starting the MCP server (use when it's already running locally
    or remotely).

.PARAMETER SkipBackend
    Skip starting the chat backend.

.PARAMETER SkipFrontend
    Skip starting the Streamlit UI.

.PARAMETER CheckOnly
    Only run pre-flight checks, do not launch anything.

.PARAMETER Port
    Streamlit port (default 8003).

.EXAMPLE
    .\scripts\start-streamlit.ps1
    .\scripts\start-streamlit.ps1 -SkipMcp -SkipBackend   # just the UI
#>
[CmdletBinding()]
param(
    [switch]$SkipMcp,
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$CheckOnly,
    [int]$Port = 8003
)

$ErrorActionPreference = "Stop"

$RepoRoot       = Split-Path -Parent $PSScriptRoot
$McpDir         = Join-Path $RepoRoot "mqacemcpserver-single"
$BackendDir     = Join-Path $RepoRoot "backend"
$StreamlitDir   = Join-Path $RepoRoot "frontend"
$PidFile        = Join-Path $PSScriptRoot ".pids"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)   { Write-Host "  !!  $msg" -ForegroundColor Red }
function Write-Note($msg)  { Write-Host "      $msg" -ForegroundColor DarkGray }

# Read a KEY=value from a .env so the endpoint output reflects the actual ports/
# scheme the services bind (they read these same vars at runtime).
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

# The single build loads mqacemcpserver-single\.env; the backend loads backend\.env.
$McpEnv      = Join-Path $McpDir ".env"
$BackendEnv  = Join-Path $BackendDir ".env"
$McpPort     = Get-EnvValue $McpEnv "MCP_PORT" "8010"
$McpScheme   = if (Get-EnvValue $McpEnv "MCP_TLS_CERT" "") { "https" } else { "http" }
$BackendPort = Get-EnvValue $BackendEnv "CHAT_PORT" "8002"

$problems = @()

if (-not $SkipMcp) {
    Write-Step "Checking MCP server prerequisites"
    $mcpVenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $mcpEntry      = Join-Path $McpDir "single_server.py"
    if (-not (Test-Path $mcpVenvPython)) {
        $problems += "Missing MCP venv. Fix: cd `"$RepoRoot`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r mqacemcpserver-single\requirements.txt"
        Write-Bad ".venv\Scripts\python.exe not found"
    } else { Write-Ok ".venv present" }
    if (-not (Test-Path $mcpEntry)) {
        $problems += "Missing mqacemcpserver-single\single_server.py."
        Write-Bad "mqacemcpserver-single\single_server.py not found"
    } else { Write-Ok "mqacemcpserver-single\single_server.py present" }
}

if (-not $SkipBackend) {
    Write-Step "Checking chat backend prerequisites"
    $beVenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    $beApp        = Join-Path $BackendDir "app.py"
    $beEnv        = Join-Path $BackendDir ".env"
    if (-not (Test-Path $beVenvPython)) {
        $problems += "Missing backend venv. Fix: cd `"$BackendDir`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad "backend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "backend venv present" }
    if (-not (Test-Path $beApp)) {
        $problems += "Missing backend\app.py."
        Write-Bad "backend\app.py not found"
    } else { Write-Ok "backend app.py present" }
    if (-not (Test-Path $beEnv)) {
        $problems += "Missing backend\.env. Fix: cd `"$BackendDir`" ; copy .env.example .env ; then edit it (OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*)"
        Write-Bad "backend\.env not found"
    } else { Write-Ok "backend .env present" }
}

if (-not $SkipFrontend) {
    Write-Step "Checking Streamlit UI prerequisites"
    $stVenvPython = Join-Path $StreamlitDir ".venv\Scripts\python.exe"
    $stApp        = Join-Path $StreamlitDir "app.py"
    $stEnv        = Join-Path $StreamlitDir ".env"
    if (-not (Test-Path $stVenvPython)) {
        $problems += "Missing Streamlit venv. Fix: cd `"$StreamlitDir`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad "frontend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "Streamlit venv present" }
    if (-not (Test-Path $stApp)) {
        $problems += "Missing frontend\app.py."
        Write-Bad "frontend\app.py not found"
    } else { Write-Ok "Streamlit app.py present" }
    if (-not (Test-Path $stEnv)) {
        Write-Note "frontend\.env missing - defaults to MCP_BACKEND_URL=http://localhost:8002. Copy .env.example if you need to override."
    } else { Write-Ok "Streamlit .env present" }
}

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Bad "Pre-flight failed. Resolve the items above before running start-streamlit again:"
    $problems | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    exit 1
}

if ($CheckOnly) {
    Write-Host ""
    Write-Ok "All checks passed. (CheckOnly was specified, not starting services.)"
    exit 0
}

$pids = @()

function Start-Service-Window {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )
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
    # Run from repo root so .env/resources resolve; entry path is relative to it.
    $entryRel = $McpEntry.Substring($RepoRoot.Length).TrimStart('\')
    $cmd = "`$env:MCP_TRANSPORT='sse'; .\.venv\Scripts\python.exe `"$entryRel`""
    $pids += Start-Service-Window -Title "MCP Server (SSE :$McpPort)" `
        -WorkingDirectory $RepoRoot -Command $cmd
    Start-Sleep -Seconds 2
}

if (-not $SkipBackend) {
    $cmd = ".\.venv\Scripts\python.exe app.py"
    $pids += Start-Service-Window -Title "Chat Backend (FastAPI :$BackendPort)" `
        -WorkingDirectory $BackendDir -Command $cmd
    Start-Sleep -Seconds 2
}

if (-not $SkipFrontend) {
    $cmd = ".\.venv\Scripts\python.exe -m streamlit run app.py --server.port $Port"
    $pids += Start-Service-Window -Title "Streamlit UI (:$Port)" `
        -WorkingDirectory $StreamlitDir -Command $cmd
}

$pids | Out-File -FilePath $PidFile -Encoding ascii

Write-Host ""
Write-Ok "All requested services launched."
Write-Host ""
Write-Host "  MCP health    : ${McpScheme}://localhost:$McpPort/healthz" -ForegroundColor Gray
Write-Host "  Backend health: http://localhost:$BackendPort/api/health" -ForegroundColor Gray
Write-Host "  Streamlit UI  : http://localhost:$Port" -ForegroundColor Gray
Write-Host ""
Write-Host "  To stop everything, run:  .\scripts\stop-all.ps1" -ForegroundColor DarkGray
