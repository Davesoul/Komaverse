<#
.SYNOPSIS
  Stops whatever is listening on the Komaverse ports (default 8000 + 5173).
.EXAMPLE
  ./scripts/stop.ps1
  ./scripts/stop.ps1 -Ports @(8000) -Confirm:$false
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [int[]]$Ports = @(8000, 8001, 5173)
)

$found = $false
foreach ($port in $Ports) {
  $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($targetPid in $owners) {
    try {
      $proc = Get-Process -Id $targetPid -ErrorAction Stop
    } catch {
      continue
    }
    $found = $true
    $label = "$($proc.ProcessName) (pid $targetPid) on port $port"
    if ($PSCmdlet.ShouldProcess($label, "Stop process")) {
      Stop-Process -Id $targetPid -Force
      Write-Host "Stopped $label" -ForegroundColor Yellow
    }
  }
}
if (-not $found) {
  Write-Host ("Nothing listening on :" + ($Ports -join ", :") + ".")
}

# Second pass: uvicorn --reload orphans. When a reloader supervisor dies,
# its live worker children keep serving while their sockets stay attributed
# to the dead supervisor PID, so the sweep above cannot see them. Match the
# API servers by command line as well (node/vite on 5173 is unaffected).
foreach ($port in $Ports) {
  $workers = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "komaverse\.main:app" -and $_.CommandLine -match "--port\s+$port\b" }
  foreach ($w in $workers) {
    $label = "uvicorn worker (pid $($w.ProcessId)) on port $port"
    if ($PSCmdlet.ShouldProcess($label, "Stop process")) {
      Stop-Process -Id $w.ProcessId -Force -ErrorAction SilentlyContinue
      Write-Host "Stopped $label" -ForegroundColor Yellow
    }
  }
}
