@echo off
REM ============================================================
REM REBA - Arranque completo para pruebas de campo
REM Levanta: servidor FastAPI (:5000) + tunel Cloudflare (rebaserver.tech)
REM          + tunel zrok (respaldo dashboard)
REM Doble clic para ejecutar. Dejar esta ventana abierta.
REM
REM Tunel Cloudflare (definitivo, autostart via Startup folder):
REM  - rebaserver.tech -> http://localhost:5000
REM  - http:// = ESP32 (server_url_alt) | https:// = dashboard remoto
REM  - tools\run_cloudflared.bat (--protocol http2), log logs\cloudflared.log
REM  - Si ya corre (Startup), no se duplica; si no, se lanza aqui.
REM
REM Tunel zrok (tools\zrok_tunnel.ps1, RESPALDO solo-HTTPS del dashboard):
REM  - https://reba-cam.shares.zrok.io (reservado, URL fija)
REM ============================================================
cd /d "%~dp0"
if not exist logs mkdir logs

echo === Servidor REBA ===
netstat -ano | findstr /R /C:":5000 .*LISTENING" >nul
if errorlevel 1 (
    wscript "%~dp0tools\server_hidden.vbs"
    echo   (watchdog lanzado: tools\run_server.bat, log logs\uvicorn.log)
) else (
    echo   (ya estaba corriendo en :5000)
)

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

echo.
echo Listo.
echo   Dashboard remoto:  https://rebaserver.tech
echo   ESP32 remoto:      http://rebaserver.tech  (server_url_alt)
echo   Respaldo zrok:     https://reba-cam.shares.zrok.io
  echo   Salud:            http://localhost:5000/api/health
echo   Dashboard LAN:     http://192.168.137.1:5000  (si el hotspot esta activo)
echo   Simulador ESP32:   /simulador_esp32.html
echo.
pause
