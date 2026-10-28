<#
.SYNOPSIS
  Restart the whole app: stops the ports, then starts dev or prod mode.
.EXAMPLE
  ./scripts/restart.ps1
  ./scripts/restart.ps1 -Mode prod -NoBrowser
#>
param(
  [ValidateSet("dev", "prod")][string]$Mode = "dev",
  [int]$ApiPort = 8000,
  [int]$WebPort = 5173,
  [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"

& "$PSScriptRoot/stop.ps1" -Ports @($ApiPort, $WebPort) -Confirm:$false
if ($Mode -eq "dev") {
  & "$PSScriptRoot/dev.ps1" -ApiPort $ApiPort -WebPort $WebPort -NoBrowser:$NoBrowser
} else {
  & "$PSScriptRoot/start.ps1" -Port $ApiPort -NoBrowser:$NoBrowser
}
