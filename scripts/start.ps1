<#
.SYNOPSIS
  Production-ish single-URL mode: builds the web UI, serves API + UI from one port.
.EXAMPLE
  ./scripts/start.ps1
  ./scripts/start.ps1 -Port 8000 -SkipBuild
#>
param(
  [int]$Port = 8000,
  [switch]$SkipBuild,
  [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_common.ps1"

$root = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $root "backend"
$web = Join-Path $root "apps/studio-web"
$py = Join-Path $backend ".venv/Scripts/python.exe"

if (-not (Test-Path $py)) {
  throw "Backend venv missing. Run in ${backend}: python -m venv .venv; ./.venv/Scripts/python -m pip install -e ."
}
if (-not $SkipBuild) {
  Write-Host "Building web UI..." -ForegroundColor Cyan
  Push-Location $web
  try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Web build failed." }
  } finally {
    Pop-Location
  }
}
if (-not (Test-Path (Join-Path $web "dist/index.html"))) {
  throw "Web build missing at $web/dist - run without -SkipBuild once."
}

Write-Host "Starting Komaverse on http://127.0.0.1:$Port (API + UI, one port)" -ForegroundColor Cyan
$apiCmd = "cd '$backend'; `$env:SERVE_WEB='true'; ./.venv/Scripts/python.exe -m uvicorn komaverse.main:app --host 127.0.0.1 --port $Port"
Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command", $apiCmd

if (Wait-Url "http://127.0.0.1:$Port/health" -TimeoutSec 60) {
  Write-Host "Komaverse healthy: http://127.0.0.1:$Port" -ForegroundColor Green
} else {
  Write-Warning "App not responding yet - check the backend window."
}
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
Write-Host "Tip: ./scripts/stop.ps1 -Ports @($Port) to stop it." -ForegroundColor DarkGray
