# Shared helpers for the Komaverse scripts. Dot-source from the other scripts:
#   . "$PSScriptRoot/_common.ps1"

function Wait-Url {
  param([string]$Url, [int]$TimeoutSec = 90)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
      if ($r.StatusCode -eq 200) { return $true }
    } catch {
      Start-Sleep -Milliseconds 800
    }
  }
  return $false
}

function Assert-Command {
  param([string]$Name, [string]$Hint)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Missing required command '$Name'. $Hint"
  }
}
