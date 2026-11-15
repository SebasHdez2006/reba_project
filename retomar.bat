@echo off
REM ============================================================
REM REBA - Retorno tras apagar la PC
REM Levanta: servidor (:5000) + tunel zrok + resume v5 + monitor
REM Doble clic para ejecutar. Dejar las ventanas abiertas.
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

echo === Entrenamiento v5 (resume) ===
start "REBA-Train" cmd /k ".venv-reba\Scripts\python.exe ai_pipeline\train_outdoor.py --resume --name REBA_outdoor_v5"

echo === Monitor (opcional, se puede cerrar) ===
start "REBA-Monitor" cmd /k ".venv-reba\Scripts\python.exe ai_pipeline\train_monitor.py --run REBA_outdoor_v5"

echo.
echo Listo. Verificaciones:
echo   - URL del tunel: ventana REBA-Tunnel, logs\tunnel.log o logs\zrok_url.txt
echo     (con reserva configurada la URL es siempre la misma)
echo   - Salud: http://localhost:5000/api/health  (backend debe decir onnx)
echo   - Entrenamiento: ventana REBA-Train (epoca X/50) o monitor
echo   - Si v5 ya habia terminado, REBA-Train solo reexporta ONNX.
echo.
pause
