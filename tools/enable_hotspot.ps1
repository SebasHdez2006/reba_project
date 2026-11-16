# Enciende el hotspot movil de Windows (Mobile Hotspot) via WinRT.
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await-Op($op, $resultType) {
  $asTask.MakeGenericMethod($resultType).Invoke($null, @($op)).Result
}

[void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringAccessPointConfiguration, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]

$profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if (-not $profile) { Write-Output "NO_INTERNET_PROFILE"; exit 1 }

$mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
Write-Output ("Capability: " + $mgr.TetheringCapability)

if ($mgr.TetheringOperationalState -eq 'On') {
  Write-Output "ALREADY_ON"
} else {
  $op = $mgr.StartTetheringAsync()
  $res = Await-Op $op ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
  Write-Output ("StartStatus: " + $res.Status)
  if ($res.AdditionalErrorMessage) { Write-Output ("ErrorMsg: " + $res.AdditionalErrorMessage) }
}

Start-Sleep -Seconds 3
$cfg = $mgr.GetCurrentAccessPointConfiguration()
Write-Output ("SSID: " + $cfg.Ssid)
Write-Output ("Band: " + $cfg.Band)
Write-Output ("State: " + $mgr.TetheringOperationalState)
$clients = $mgr.ClientCount
Write-Output ("Clients: " + $clients)
