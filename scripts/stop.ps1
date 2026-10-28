<#
.SYNOPSIS
  Stops whatever is listening on the Komaverse ports (default 8000 + 5173).
.EXAMPLE
  ./scripts/stop.ps1
  ./scripts/stop.ps1 -Ports @(8000) -Confirm:$false
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param(
  [int[]]$Ports = @(8000, 5173)
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
