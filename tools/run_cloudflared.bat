@echo off
REM REBA - tunel nombrado Cloudflare (rebaserver.tech) -> localhost:5000
REM --protocol http2: QUIC/UDP hace timeout en la red institucional (PROGRESO sesion 11)
REM --logfile: estado del tunel en logs\cloudflared.log
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --protocol http2 --loglevel info --logfile "D:\Backup_Sebas\UES\Ciclo 6\ARC115\Proyecto\REBA\logs\cloudflared.log" run
