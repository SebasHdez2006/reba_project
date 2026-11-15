#Requires -Version 5.1
<#
.SYNOPSIS
  REBA — lanza el tunel zrok headless (reemplazo de cloudflared).

.DESCRIPTION
  Detecta el binario zrok instalado (`zrok` = v1, `zrok2` = v2) y abre un
  share publico hacia el servidor local. Toda la salida se muestra en
  consola y se guarda en logs\tunnel.log; la URL publica detectada queda
  ademas en logs\zrok_url.txt.

  Modos:
    - Reservado: si .env define ZROK_RESERVED_NAME (creado una vez con
      setup_zrok.bat) la URL es FIJA entre reinicios:
        v1: zrok  share reserved <nombre> --headless
        v2: zrok2 share public <target> -n public:<nombre> --headless
    - Efimero: si no hay nombre reservado, share publico con URL nueva
      en cada arranque (equivalente al antiguo quick tunnel).
    - Si el share reservado falla al arrancar, cae automaticamente al
      modo efimero para no dejar el acceso remoto muerto.
    - v2: si el nombre sigue ligado a un share huerfano de un tunel
      anterior cerrado sin limpieza, lo libera y reintenta antes de
      caer al modo efimero.

  Variables (.env):
    ZROK_RESERVED_NAME  nombre publico fijo (vacio = efimero)
    ZROK_TARGET_URL     objetivo local (default http://127.0.0.1:5000)

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\zrok_tunnel.ps1
#>
param(
  [string]$Root = "",
  [string]$LogFile = ""
)

$ErrorActionPreference = "Continue"

# ------------------------------------------------------------------ rutas --
if (-not $Root) { $Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
$Root = $Root.TrimEnd('\', '/')
if (-not $LogFile) { $LogFile = Join-Path $Root "logs\tunnel.log" }
$logDir = Split-Path $LogFile -Parent
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$urlFile = Join-Path $logDir "zrok_url.txt"

# Log fresco por ejecucion (los intentos reserv->efimero comparten el archivo)
if (Test-Path $LogFile) { Clear-Content -Path $LogFile -Force }
if (Test-Path $urlFile) { Remove-Item $urlFile -Force }

# ------------------------------------------------------------- config .env --
$reservedName = ""
$target = "http://127.0.0.1:5000"
$envPath = Join-Path $Root ".env"
if (Test-Path $envPath) {
  foreach ($line in Get-Content $envPath -Encoding UTF8) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    $k = $k.Trim(); $v = $v.Trim()
    if ($k -eq 'ZROK_RESERVED_NAME') { $reservedName = $v }
    if ($k -eq 'ZROK_TARGET_URL')    { $target = $v }
  }
}
$target = $target.TrimEnd('/')

# --------------------------------------------------------- deteccion binario --
$zrokExe = $null; $zrokVer = $null
$cmd = Get-Command zrok -ErrorAction SilentlyContinue
if ($cmd) { $zrokExe = $cmd.Source; $zrokVer = 1 }
else {
  $cmd = Get-Command zrok2 -ErrorAction SilentlyContinue
  if ($cmd) { $zrokExe = $cmd.Source; $zrokVer = 2 }
}

function Write-Line([string]$msg) {
  [Console]::WriteLine($msg)
  Add-Content -Path $script:LogFile -Value $msg -Encoding UTF8
}

if (-not $zrokExe) {
  Write-Line "[zrok] ERROR: no se encontro 'zrok' ni 'zrok2' en el PATH."
  Write-Line "[zrok] Instalacion (una sola vez): descarga el zip de Windows desde"
  Write-Line "[zrok]   https://github.com/openziti/zrok/releases  (zrok o zrok2)"
  Write-Line "[zrok] descomprime en una carpeta y agrega el .exe al PATH, luego ejecuta"
  Write-Line "[zrok]   setup_zrok.bat  (cuenta zrok.io + reserva de nombre)"
  exit 2
}

Write-Line ("[zrok] binario: {0}  (v{1})" -f $zrokExe, $(if ($zrokVer -eq 1) { "1.x" } else { "2.x" }))
Write-Line ("[zrok] target : {0}" -f $target)

# ------------------------------------------------------- ejecucion del share --
$script:ShareUrl = ""
$script:NameInUse = $false
# zrok2 --headless imprime el hostname PELADO ("nombre.shares.zrok.io") dentro de
# una linea JSON; zrok v1 suele imprimir la URL completa con https://.
# El patron acepta ambos y exige el dominio .share(s).zrok.io para no confundir
# hosts de infraestructura (api-v2.zrok.io, etc.).
$script:UrlPattern = '(?:https?://)?[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*)*\.(?:shares?|zrok)\.zrok\.io[^\s"''<>]*'

function Invoke-ZrokShare {
  param([string[]]$ArgList)

  Write-Line ("[zrok] >> {0} {1}" -f $zrokExe, ($ArgList -join ' '))
  & $zrokExe @ArgList 2>&1 | ForEach-Object {
    $line = "$_"
    [Console]::WriteLine($line)
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    if ($line -match 'in use by share') { $script:NameInUse = $true }
    if ($line -match $script:UrlPattern -and -not $script:ShareUrl) {
      $u = $Matches[0]
      if ($u -notmatch '^https?://') { $u = 'https://' + $u }
      $script:ShareUrl = $u
      Set-Content -Path $urlFile -Value $script:ShareUrl -Encoding ascii
      Write-Line ("[zrok] >>> URL del tunel: {0}" -f $script:ShareUrl)
    }
  }
  return $LASTEXITCODE
}

function Get-ReservedArgs {
  if ($zrokVer -eq 1) {
    # v1: el token del share reservado ES el unique-name
    return @('share', 'reserved', $reservedName, '--headless')
  }
  # v2: namespaces/names — asegurar que el nombre exista y quede reservado
  & $zrokExe create name $reservedName -n public 2>&1 | ForEach-Object { Write-Line "[zrok2-setup] $_" }
  & $zrokExe modify name $reservedName -n public --reserved 2>&1 | ForEach-Object { Write-Line "[zrok2-setup] $_" }
  return @('share', 'public', $target, '-n', "public:$reservedName", '--headless')
}

$exitCode = 0
if ($reservedName) {
  $reservedArgs = Get-ReservedArgs
  $exitCode = Invoke-ZrokShare -ArgList $reservedArgs
  if (-not $script:ShareUrl -and $exitCode -ne 0 -and $zrokVer -eq 2 -and $script:NameInUse) {
    # zrok2: el share es un registro persistente — un tunel anterior cerrado
    # sin limpieza deja el nombre "in use". Liberarlo y reintentar una vez.
    Write-Line "[zrok] Nombre ligado a un share huerfano. Liberando 'public:$reservedName' y reintentando..."
    & $zrokExe delete share "public:$reservedName" 2>&1 | ForEach-Object { Write-Line "[zrok2-setup] $_" }
    $script:NameInUse = $false
    $exitCode = Invoke-ZrokShare -ArgList $reservedArgs
  }
  if (-not $script:ShareUrl -and $exitCode -ne 0) {
    Write-Line "[zrok] Share reservado fallo (exit $exitCode). Reintentando en modo EFIMERO..."
    Write-Line "[zrok] (si persiste, ejecuta setup_zrok.bat para revisar la reserva)"
    $exitCode = Invoke-ZrokShare -ArgList @('share', 'public', $target, '--headless')
  }
} else {
  Write-Line "[zrok] Sin ZROK_RESERVED_NAME: share EFIMERO (URL nueva por arranque)."
  Write-Line "[zrok] Para URL fija: ejecuta setup_zrok.bat una sola vez."
  $exitCode = Invoke-ZrokShare -ArgList @('share', 'public', $target, '--headless')
}

if ($script:ShareUrl) {
  Write-Line ("[zrok] Listo. Dashboard: {0}  (tambien en logs\zrok_url.txt)" -f $script:ShareUrl)
}
exit $exitCode
