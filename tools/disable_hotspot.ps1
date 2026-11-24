# Apaga el hotspot movil de Windows (para probar el watchdog / mantenimiento).
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await-Op($op, $resultType) {
  $asTask.MakeGenericMethod($resultType).Invoke($null, @($op)).Result
}

[void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]

$profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if (-not $profile) { Write-Output "NO_INTERNET_PROFILE"; exit 1 }

$mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
Write-Output ("StateBefore: " + $mgr.TetheringOperationalState)

if ($mgr.TetheringOperationalState -eq 'On') {
  $op = $mgr.StopTetheringAsync()
  $res = Await-Op $op ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
  Write-Output ("StopStatus: " + $res.Status)
} else {
  Write-Output "ALREADY_OFF"
}
