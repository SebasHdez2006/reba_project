@echo off
rem REBA - watchdog del hotspot (wrapper con respawn, igual que run_server.bat).
rem Si el .ps1 muere (excepcion, AV, etc.) se relanza tras 60 s.
title REBA Hotspot Watchdog
:loop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hotspot_watchdog.ps1"
ping -n 61 127.0.0.1 >nul
goto loop
