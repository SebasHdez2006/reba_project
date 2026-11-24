#Requires -Version 5.1
<#
.SYNOPSIS
  REBA - watchdog del hotspot movil (REBA-AP).

.DESCRIPTION
  Windows apaga el tethering por ahorro de energia, suspension del PC o
  perdida del perfil de internet, y NO lo reenciende solo — la ESP32 se
  queda sin red (visto 24-nov: OTA interrumpida a mitad de descarga).

  Este bucle revisa cada 60 s el estado del tethering y lo reactiva si
  esta apagado. Mismo patron watchdog que tools\run_server.bat. El
  enable es via WinRT y NO requiere admin. Log: logs\hotspot.log

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\hotspot_watchdog.ps1
#>

$ErrorActionPreference = "Continue"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir "hotspot.log"

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await-Op($op, $resultType) {
  $asTask.MakeGenericMethod($resultType).Invoke($null, @($op)).Result
}

function Write-Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -Encoding UTF8
}

Write-Log "watchdog iniciado (PID $PID)"

while ($true) {
  try {
    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if (-not $profile) {
      # Sin perfil de internet no hay tethering posible (suspendido,
      # adaptador caido, VPN...). Reintentar en el siguiente ciclo.
      Write-Log "sin perfil de internet; reintento en 60 s"
    } else {
      $mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
      if ($mgr.TetheringOperationalState -ne 'On') {
        $res = Await-Op ($mgr.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
        Write-Log ("tethering apagado -> Start ({0}) {1}" -f $res.Status, $res.AdditionalErrorMessage)
      }
    }
  } catch {
    Write-Log ("error: {0}" -f $_)
  }
  Start-Sleep -Seconds 60
}
