@echo off
REM ============================================================
REM REBA - Arranque completo para pruebas de campo
REM Levanta: servidor FastAPI (:5000) + tunel zrok
REM Doble clic para ejecutar. Dejar esta ventana abierta.
REM
REM Tunel zrok (tools\zrok_tunnel.ps1):
REM  - Con ZROK_RESERVED_NAME en .env -> URL FIJA https://NOMBRE.shares.zrok.io
REM    (se reserva una vez con setup_zrok.bat; gratis, sin dominio)
REM  - Sin reserva -> share efimero; URL nueva en cada arranque
REM    (queda en logs\tunnel.log y logs\zrok_url.txt)
REM La ESP32 sigue por HTTP plano en la LAN/hotspot: zrok es HTTPS
REM y solo lo usan el dashboard del celular y el simulador.
REM ============================================================
cd /d "%~dp0"
if not exist logs mkdir logs

echo === Servidor REBA ===
start "REBA-Server" cmd /k ".venv-reba\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 5000 --proxy-headers --forwarded-allow-ips=*"

echo === Tunel zrok ===
start "REBA-Tunnel" powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0tools\zrok_tunnel.ps1"

echo.
echo Listo. URL del tunel: la que muestra la ventana REBA-Tunnel
echo              o en logs\zrok_url.txt / logs\tunnel.log
echo Dashboard LAN: http://192.168.137.1:5000  (si el hotspot esta activo)
echo              o http://%COMPUTERNAME%:5000 / IP de esta PC
echo Simulador ESP32 (celular, por HTTPS): /simulador_esp32.html
echo.
pause
