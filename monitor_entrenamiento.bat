@echo off
cd /d "%~dp0"
.venv-reba\Scripts\python.exe ai_pipeline\train_monitor.py --run REBA_outdoor_v5
pause
