@echo off
REM ============================================================
REM  REBA - Tunel reverso persistente PC -> Oracle VPS
REM  ESP32 --HTTP--> VPS_IP:39173 --SSH--> localhost:5000
REM  Reintenta indefinidamente si la conexion cae.
REM  VPS_HOST se completa tras crear la VM en Oracle Cloud.
REM ============================================================
setlocal

set VPS_USER=azureuser
set VPS_HOST=__VPS_PUBLIC_IP__
set VPS_SSH_PORT=22
set LOCAL_PORT=5000
set REMOTE_PORT=39173
set KEY=%USERPROFILE%\.ssh\reba_oracle
set SSH=C:\Windows\System32\OpenSSH\ssh.exe

:loop
echo [%date% %time%] Conectando tunel a %VPS_USER%@%VPS_HOST% ...
"%SSH%" -N ^
  -i "%KEY%" ^
  -p %VPS_SSH_PORT% ^
  -o ServerAliveInterval=30 ^
  -o ServerAliveCountMax=3 ^
  -o TCPKeepAlive=yes ^
  -o ExitOnForwardFailure=yes ^
  -o ConnectTimeout=15 ^
  -o ConnectionAttempts=3 ^
  -o StrictHostKeyChecking=accept-new ^
  -R 0.0.0.0:%REMOTE_PORT%:localhost:%LOCAL_PORT% ^
  %VPS_USER%@%VPS_HOST%

echo [%date% %time%] Tunel terminado (exit %errorlevel%). Reintento en 10s...
timeout /t 10 /nobreak >nul
goto loop
