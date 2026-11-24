@echo off
setlocal
title Instalar PulsePC
rem Instalador de PulsePC en un solo archivo: descarga el agente desde GitHub,
rem lo copia en %LOCALAPPDATA%\PulsePC y lo abre. Sirve tambien para actualizar
rem (conserva tu diseno, ajustes e imagenes).

set "DESTINO=%LOCALAPPDATA%\PulsePC"
set "ZIP=%TEMP%\pulsepc-main.zip"
set "TMPDIR=%TEMP%\pulsepc-main"

echo Descargando PulsePC desde GitHub...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "Invoke-WebRequest -UseBasicParsing 'https://github.com/Numial/pulsepc/archive/refs/heads/main.zip' -OutFile '%ZIP%';" ^
  "if (Test-Path '%TMPDIR%') { Remove-Item -Recurse -Force '%TMPDIR%' };" ^
  "Expand-Archive -Force '%ZIP%' '%TMPDIR%'"
if errorlevel 1 (
  echo.
  echo  No se pudo descargar PulsePC. Revisa tu conexion a Internet.
  echo.
  pause
  exit /b 1
)

echo Copiando archivos a %DESTINO% ...
rem robocopy sin /MIR: no borra tu configuracion (pulsopc_config.json, imagenes, etc.)
robocopy "%TMPDIR%\pulsepc-main\agente" "%DESTINO%\agente" /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ >nul
if errorlevel 8 (
  echo  No se pudieron copiar los archivos a %DESTINO%.
  pause
  exit /b 1
)
del /q "%ZIP%" >nul 2>nul
rmdir /s /q "%TMPDIR%" >nul 2>nul

echo Listo. Abriendo PulsePC...
call "%DESTINO%\agente\iniciar_agente.bat"
exit /b %errorlevel%
