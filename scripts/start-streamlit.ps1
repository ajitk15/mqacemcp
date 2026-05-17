<#
.SYNOPSIS
    Starts the MCP chatbot stack with the Streamlit frontend instead of Next.js.

.DESCRIPTION
    Spawns three PowerShell windows:
      1. MCP server      (mqacemcpserver.py, SSE on :8000)
      2. Chat backend    (FastAPI on :8001)
      3. Streamlit UI    (streamlit run, default :8501)

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
    Streamlit port (default 8501).

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
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"

$RepoRoot       = Split-Path -Parent $PSScriptRoot
$BackendDir     = Join-Path $RepoRoot "chatbot\backend"
$StreamlitDir   = Join-Path $RepoRoot "chatbot\streamlit_frontend"
$PidFile        = Join-Path $PSScriptRoot ".pids"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Bad($msg)   { Write-Host "  !!  $msg" -ForegroundColor Red }
function Write-Note($msg)  { Write-Host "      $msg" -ForegroundColor DarkGray }

$problems = @()

if (-not $SkipMcp) {
    Write-Step "Checking MCP server prerequisites"
    $mcpVenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $mcpEntry      = Join-Path $RepoRoot "mqacemcpserver.py"
    if (-not (Test-Path $mcpVenvPython)) {
        $problems += "Missing MCP venv. Fix: cd `"$RepoRoot`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad ".venv\Scripts\python.exe not found"
    } else { Write-Ok ".venv present" }
    if (-not (Test-Path $mcpEntry)) {
        $problems += "Missing mqacemcpserver.py at repo root."
        Write-Bad "mqacemcpserver.py not found"
    } else { Write-Ok "mqacemcpserver.py present" }
}

if (-not $SkipBackend) {
    Write-Step "Checking chat backend prerequisites"
    $beVenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    $beApp        = Join-Path $BackendDir "app.py"
    $beEnv        = Join-Path $BackendDir ".env"
    if (-not (Test-Path $beVenvPython)) {
        $problems += "Missing backend venv. Fix: cd `"$BackendDir`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad "chatbot\backend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "backend venv present" }
    if (-not (Test-Path $beApp)) {
        $problems += "Missing chatbot\backend\app.py."
        Write-Bad "chatbot\backend\app.py not found"
    } else { Write-Ok "backend app.py present" }
    if (-not (Test-Path $beEnv)) {
        $problems += "Missing chatbot\backend\.env. Fix: cd `"$BackendDir`" ; copy .env.example .env ; then edit it (OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*)"
        Write-Bad "chatbot\backend\.env not found"
    } else { Write-Ok "backend .env present" }
}

if (-not $SkipFrontend) {
    Write-Step "Checking Streamlit UI prerequisites"
    $stVenvPython = Join-Path $StreamlitDir ".venv\Scripts\python.exe"
    $stApp        = Join-Path $StreamlitDir "app.py"
    $stEnv        = Join-Path $StreamlitDir ".env"
    if (-not (Test-Path $stVenvPython)) {
        $problems += "Missing Streamlit venv. Fix: cd `"$StreamlitDir`" ; python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
        Write-Bad "chatbot\streamlit_frontend\.venv\Scripts\python.exe not found"
    } else { Write-Ok "Streamlit venv present" }
    if (-not (Test-Path $stApp)) {
        $problems += "Missing chatbot\streamlit_frontend\app.py."
        Write-Bad "chatbot\streamlit_frontend\app.py not found"
    } else { Write-Ok "Streamlit app.py present" }
    if (-not (Test-Path $stEnv)) {
        Write-Note "chatbot\streamlit_frontend\.env missing - defaults to MCP_BACKEND_URL=http://localhost:8001. Copy .env.example if you need to override."
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
    $cmd = "`$env:MCP_TRANSPORT='sse'; .\.venv\Scripts\python.exe mqacemcpserver.py"
    $pids += Start-Service-Window -Title "MCP Server (SSE :8000)" `
        -WorkingDirectory $RepoRoot -Command $cmd
    Start-Sleep -Seconds 2
}

if (-not $SkipBackend) {
    $cmd = ".\.venv\Scripts\python.exe app.py"
    $pids += Start-Service-Window -Title "Chat Backend (FastAPI :8001)" `
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
Write-Host "  MCP health    : http://localhost:8000/healthz" -ForegroundColor Gray
Write-Host "  Backend health: http://localhost:8001/api/health" -ForegroundColor Gray
Write-Host "  Streamlit UI  : http://localhost:$Port" -ForegroundColor Gray
Write-Host ""
Write-Host "  To stop everything, run:  .\scripts\stop-all.ps1" -ForegroundColor DarkGray
