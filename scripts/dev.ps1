<#
.SYNOPSIS
  Quick dev mode: API with auto-reload + Vite HMR, each in its own window.
.EXAMPLE
  ./scripts/dev.ps1
  ./scripts/dev.ps1 -ApiPort 8001 -WebPort 5173 -NoBrowser
#>
param(
  [int]$ApiPort = 8001,
  [int]$WebPort = 5173,
  [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
. "$PSScriptRoot/_common.ps1"

$root = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $root "backend"
$web = Join-Path $root "apps/studio-web"
$py = Join-Path $backend ".venv/Scripts/python.exe"

if (-not (Test-Path $py)) {
  throw "Backend venv missing. Run in ${backend}: python -m venv .venv; ./.venv/Scripts/python -m pip install -e '.[dev]'"
}
if (-not (Test-Path (Join-Path $web "node_modules")) -and -not (Test-Path (Join-Path $root "node_modules"))) {
  throw "Node modules missing. Run 'npm install' in $web first."
}
Assert-Command "npm" "Install Node.js 20+ (https://nodejs.org)."

Write-Host "Starting Komaverse (dev) - API :$ApiPort + web :$WebPort" -ForegroundColor Cyan

$apiCmd = "cd '$backend'; ./.venv/Scripts/python.exe -m uvicorn komaverse.main:app --host 127.0.0.1 --port $ApiPort --reload"
$webCmd = "cd '$web'; npm run dev -- --port $WebPort --strictPort"
Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command", $apiCmd
Start-Process powershell -WorkingDirectory $web -ArgumentList "-NoExit", "-Command", $webCmd

if (Wait-Url "http://127.0.0.1:$ApiPort/health") {
  Write-Host "API healthy:   http://127.0.0.1:$ApiPort  (docs: /docs)" -ForegroundColor Green
} else {
  Write-Warning "API not responding yet - check the backend window."
}
if (Wait-Url "http://127.0.0.1:$WebPort/") {
  Write-Host "Web healthy:   http://127.0.0.1:$WebPort" -ForegroundColor Green
} else {
  Write-Warning "Web not responding yet - check the frontend window."
}
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$WebPort" }
Write-Host "Tip: ./scripts/stop.ps1 to stop everything." -ForegroundColor DarkGray
