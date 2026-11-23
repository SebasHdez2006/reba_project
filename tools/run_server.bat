@echo off
REM REBA - watchdog del servidor FastAPI (:5000).
REM Si uvicorn muere (ventana cerrada, crash, puerto ocupado) se relanza a los 5 s.
REM Sin esto el tunel queda vivo pero responde 502 a la ESP32 (visto 22/23-nov).
REM Log: logs\uvicorn.log
cd /d "%~dp0.."
if not exist logs mkdir logs
:loop
echo [%date% %time%] arrancando uvicorn >> logs\uvicorn.log
".venv-reba\Scripts\python.exe" -m uvicorn server.main:app --host 0.0.0.0 --port 5000 --proxy-headers --forwarded-allow-ips=* >> logs\uvicorn.log 2>&1
echo [%date% %time%] uvicorn termino (codigo %errorlevel%), reinicio en 5 s >> logs\uvicorn.log
timeout /t 5 /nobreak >nul
goto loop
