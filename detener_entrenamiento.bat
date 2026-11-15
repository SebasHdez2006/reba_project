@echo off
REM ============================================================
REM REBA - Detener el entrenamiento de forma segura
REM  - Respaldar last.pt/best.pt ANTES de matar (por si el kill
REM    cae justo a mitad de una escritura de checkpoint)
REM  - Mata los procesos python de train_outdoor.py (launcher+hijo)
REM    NOTA: filtra por Name='python.exe' para no matarse a si
REM    mismo (el CommandLine del propio powershell contiene el patron)
REM
REM  Al volver: se pierde solo la epoca parcial en curso (max ~55 min
REM  CPU). retomar.bat reanuda desde la ultima epoca completa.
REM ============================================================
cd /d "%~dp0"
set RUN=runs\detect\REBA_outdoor_v5

echo === Respaldando pesos de %RUN% ===
if exist "%RUN%\weights\last.pt" copy /y "%RUN%\weights\last.pt" "%RUN%\weights\last.pt.bak" >nul && echo last.pt -^> last.pt.bak
if exist "%RUN%\weights\best.pt" copy /y "%RUN%\weights\best.pt" "%RUN%\weights\best.pt.bak" >nul && echo best.pt -^> best.pt.bak

echo.
echo === Matando procesos train_outdoor.py ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*train_outdoor*' } | ForEach-Object { Write-Host ('Deteniendo PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

echo.
echo Entrenamiento detenido. Ultima epoca completa a salvo en %RUN%\weights\last.pt
echo Para reanudar al encender la PC: doble clic en retomar.bat
echo.
pause
