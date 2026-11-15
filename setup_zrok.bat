@echo off
REM ============================================================
REM REBA - Setup unico del tunel zrok (URL publica fija gratuita)
REM
REM Que hace:
REM  1) Verifica que zrok (v1) o zrok2 (v2) este instalado y habilitado
REM     (cuenta gratuita en https://zrok.io -> zrok enable <token>).
REM  2) Reserva un nombre publico fijo (ej. reba-cam) -> URL
REM     https://<nombre>.shares.zrok.io permanente, sin comprar dominio.
REM  3) Escribe ZROK_RESERVED_NAME en .env para que campo.bat y
REM     retomar.bat arranquen el tunel fijo automaticamente.
REM
REM Ejecutar UNA sola vez (o si cambias de cuenta/dispositivo).
REM ============================================================
cd /d "%~dp0"
setlocal

echo ============================================================
echo   REBA - Setup del tunel zrok
echo   URL publica fija y gratuita: https://^<nombre^>.shares.zrok.io
echo ============================================================
echo.

REM -------------------------------------------------- 1) binario --
set "ZROK_EXE="
set "ZROK_VER="
where zrok >nul 2>nul && (set "ZROK_EXE=zrok"&set "ZROK_VER=1"&goto :bin_ok)
where zrok2 >nul 2>nul && (set "ZROK_EXE=zrok2"&set "ZROK_VER=2"&goto :bin_ok)

echo [ERROR] No se encontro 'zrok' ni 'zrok2' en el PATH.
echo.
echo  Instalacion ^(una sola vez^):
echo    1. Crea una cuenta GRATUITA en  https://zrok.io
echo    2. Descarga el binario Windows desde
echo         https://github.com/openziti/zrok/releases
echo       ^(zrok v1.x o zrok2 v2.x — ambos funcionan con REBA^)
echo    3. Descomprime el .exe en una carpeta y agregala al PATH
echo       ^(ej. C:\zrok\  y luego:  setx PATH "%%PATH%%;C:\zrok"^)
echo    4. Vuelve a ejecutar este archivo.
echo.
pause
exit /b 1

:bin_ok
echo [OK] Binario detectado: %ZROK_EXE% ^(v%ZROK_VER%^) en el PATH
echo.

REM -------------------------------------------------- 2) habilitar --
echo --- Estado de la cuenta zrok ---
%ZROK_EXE% status
echo.

REM zrok/zrok2 status sale !=0 si el entorno no esta habilitado.
%ZROK_EXE% status >nul 2>nul
if not errorlevel 1 goto :enabled_ok

echo [INFO] La cuenta zrok aun no esta habilitada en esta PC.
echo        Necesitas el token de tu cuenta:
echo          - Entra a https://zrok.io y copia el token del panel.
echo        Pega el token aqui ^(queda en la config local, no en el repo^):
set /p "ZROK_TOKEN=Token zrok: "
if not defined ZROK_TOKEN (
  echo [ERROR] Token vacio. Abortando.
  pause
  exit /b 1
)
echo.
echo --- %ZROK_EXE% enable ---
%ZROK_EXE% enable %ZROK_TOKEN%
if errorlevel 1 (
  echo [ERROR] Fallo la habilitacion. Revisa el token y tu conexion.
  pause
  exit /b 1
)
echo.

:enabled_ok

REM -------------------------------------------------- 3) nombre --
set "ZROK_NAME=reba-cam"
set /p "ZROK_NAME=Nombre publico del share [reba-cam]: "
if not defined ZROK_NAME set "ZROK_NAME=reba-cam"

REM validar: solo letras, digitos y guiones (la URL sera https://NOMBRE.shares.zrok.io)
echo %ZROK_NAME%| findstr /r /c:"^[a-zA-Z0-9-]*$" >nul
if errorlevel 1 (
  echo [ERROR] Nombre invalido: usa solo letras, numeros y guiones.
  pause
  exit /b 1
)
REM normalizar a minusculas via PowerShell (sin espacios ni simbolos ya valido)
for /f "usebackq delims=" %%n in (`powershell -NoProfile -Command "('%ZROK_NAME%').ToLower()"`) do set "ZROK_NAME=%%n"

REM -------------------------------------------------- 4) target --
set "TARGET=http://127.0.0.1:5000"
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in ('findstr /b /c:"ZROK_TARGET_URL=" ".env" 2^>nul') do set "TARGET=%%b"
)
if not defined TARGET set "TARGET=http://127.0.0.1:5000"
echo.
echo Reservando 'http://%COMPUTERNAME%:5000' -> target %TARGET% como %ZROK_NAME% ...
echo.

REM -------------------------------------------------- 5) reservar --
if "%ZROK_VER%"=="1" goto :reserve_v1

REM ---- zrok2 (v2): namespaces/names ----
echo --- zrok2 create name %ZROK_NAME% ---
%ZROK_EXE% create name %ZROK_NAME% -n public
if errorlevel 1 echo [AVISO] create name devolvio error ^(quizas ya existe — se continua^)
echo --- zrok2 modify name %ZROK_NAME% --reserved ---
%ZROK_EXE% modify name %ZROK_NAME% -n public --reserved
if errorlevel 1 echo [AVISO] modify name devolvio error ^(se continua; el share lo intentara igual^)
goto :reserve_done

:reserve_v1
REM ---- zrok (v1): reserved share ----
echo --- %ZROK_EXE% reserve public %TARGET% --unique-name %ZROK_NAME% ---
%ZROK_EXE% reserve public %TARGET% --unique-name %ZROK_NAME%
if errorlevel 1 (
  echo.
  echo [ERROR] No se pudo reservar el nombre. Posibles causas:
  echo   - El nombre ya esta en uso en tu cuenta o por otro usuario.
  echo   - La cuenta zrok.io tiene un limite de shares reservados.
  echo Prueba con otro nombre o revisa https://zrok.io console.
  pause
  exit /b 1
)

:reserve_done
echo.
echo [OK] Nombre reservado: %ZROK_NAME%
echo      URL publica fija:  https://%ZROK_NAME%.shares.zrok.io  ^(dominio exacto en el log de zrok^)
echo.

REM -------------------------------------------------- 6) .env --
if not exist ".env" copy ".env.example" ".env" >nul

REM upsert ZROK_RESERVED_NAME: quita la linea existente (si hay) y agrega la nueva
REM ('&' y no '&&': findstr devuelve 1 si .env solo contiene esa clave)
(findstr /v /b /c:"ZROK_RESERVED_NAME=" ".env" > ".env.tmp") & (echo ZROK_RESERVED_NAME=%ZROK_NAME%>> ".env.tmp") & (move /y ".env.tmp" ".env" >nul)
if exist ".env.tmp" del ".env.tmp" >nul 2>nul

REM ZROK_TARGET_URL: solo se agrega si falta (no pisa un valor personalizado)
findstr /b /c:"ZROK_TARGET_URL=" ".env" >nul || echo ZROK_TARGET_URL=%TARGET%>> ".env"

echo [OK] .env actualizado: ZROK_RESERVED_NAME=%ZROK_NAME%
echo.
echo ============================================================
echo   Listo. A partir de ahora:
echo     - campo.bat  y  retomar.bat  arrancan el tunel zrok
echo       con la URL FIJA  https://%ZROK_NAME%.shares.zrok.io
echo     - El tunel queda en la ventana REBA-Tunnel y el log
echo       en  logs\tunnel.log  (+  logs\zrok_url.txt  con la URL)
echo     - Dashboard en el celular: abre esa URL ^(HTTPS^).
echo       La ESP32 sigue por LAN/HTTP plano — zrok es solo
echo       para el dashboard y el simulador.
echo ============================================================
echo.
pause
