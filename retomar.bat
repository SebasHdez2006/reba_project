@echo off
REM ============================================================
REM REBA - Retorno tras apagar la PC
REM Levanta: servidor (:5000) + tunel Cloudflare + tunel zrok
REM          + resume v5 + monitor
REM Doble clic para ejecutar. Dejar las ventanas abiertas.
REM
REM Tunel Cloudflare (definitivo): rebaserver.tech -> localhost:5000
REM  (http=ESP32 | https=dashboard). Autostart via Startup folder;
REM  aqui solo se relanza si no esta corriendo.
REM Tunel zrok (respaldo HTTPS dashboard): reba-cam.shares.zrok.io.
REM ============================================================
cd /d "%~dp0"
if not exist logs mkdir logs

echo === Servidor REBA ===
start "REBA-Server" cmd /k ".venv-reba\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 5000 --proxy-headers --forwarded-allow-ips=*"

echo === Tunel Cloudflare (rebaserver.tech) ===
tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | findstr /I "cloudflared.exe" >nul
if errorlevel 1 (
    wscript "%~dp0tools\cloudflared_tunnel_hidden.vbs"
    echo   (lanzado via tools\cloudflared_tunnel_hidden.vbs)
) else (
    echo   (ya estaba corriendo)
)

echo === Tunel zrok (respaldo) ===
start "REBA-Tunnel" powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0tools\zrok_tunnel.ps1"

echo === Hotspot REBA-AP + watchdog ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\enable_hotspot.ps1" >nul
powershell -NoProfile -Command "exit [Math]::Min(@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'hotspot_watchdog.ps1' -and $_.Name -eq 'powershell.exe'}).Count - 1, 1)"
if errorlevel 1 (echo   watchdog ya corriendo) else (wscript "%~dp0tools\hotspot_watchdog_hidden.vbs" & echo   watchdog lanzado: reactiva el hotspot si Windows lo apaga)

echo === Entrenamiento v5 (resume) ===
start "REBA-Train" cmd /k ".venv-reba\Scripts\python.exe ai_pipeline\train_outdoor.py --resume --name REBA_outdoor_v5"

echo === Monitor (opcional, se puede cerrar) ===
start "REBA-Monitor" cmd /k ".venv-reba\Scripts\python.exe ai_pipeline\train_monitor.py --run REBA_outdoor_v5"

echo.
echo Listo. Verificaciones:
echo   - Dashboard remoto: https://rebaserver.tech   (tunel Cloudflare)
echo   - ESP32 remoto:     http://rebaserver.tech  (server_url_alt)
echo   - Respaldo zrok:    https://reba-cam.shares.zrok.io
echo   - Salud: http://localhost:5000/api/health  (backend debe decir onnx)
echo   - Tuneles: cloudflared -> logs\cloudflared.log | zrok -> logs\tunnel.log
echo   - Entrenamiento: ventana REBA-Train (epoca X/50) o monitor
echo   - Si v5 ya habia terminado, REBA-Train solo reexporta ONNX.
echo.
pause
