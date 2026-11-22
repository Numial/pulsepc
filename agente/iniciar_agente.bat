@echo off
setlocal
cd /d "%~dp0"
title PulsePC

rem --- Buscar Python: lanzador "py", "python" real (no el atajo de la Microsoft Store) o rutas habituales ---
set "PY="
set "LANZADOR="
where py >nul 2>nul && (py -3 -c "import sys" >nul 2>nul && set "PY=py -3" && set "LANZADOR=1")
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
      echo %%P | find /i "WindowsApps" >nul || set "PY="%%P""
    )
  )
)
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-3*" "%LOCALAPPDATA%\Python\bin" "%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*" "C:\Python3*") do (
    if not defined PY if exist "%%~D\python.exe" set "PY="%%~D\python.exe""
  )
)
if not defined PY (
  echo.
  echo  No encuentro Python en este PC.
  echo  Instalalo desde https://www.python.org/downloads/
  echo  y marca la casilla "Add python.exe to PATH" al instalar.
  echo  Tambien puedes instalarlo con:  winget install Python.Python.3.12
  echo.
  pause
  exit /b 1
)

rem --- Version sin consola (pythonw / pyw) ---
rem (PY puede venir entre comillas, asi que no se compara su texto: se usa la marca LANZADOR)
set "PYW=%PY:python.exe=pythonw.exe%"
if defined LANZADOR set "PYW=pyw -3"

rem Si ya esta todo instalado, no hace falta pip (asi abrir PulsePC es instantaneo).
%PY% -c "import psutil, pystray, PIL, pychromecast" >nul 2>nul && goto arrancar
echo Preparando PulsePC (solo la primera vez tarda un poco)...
%PY% -m pip install --quiet --disable-pip-version-check psutil pystray pillow pychromecast
if errorlevel 1 (
  echo No se pudieron instalar las dependencias. Revisa tu conexion a Internet.
  pause
  exit /b 1
)
:arrancar
rem Arranca en segundo plano: aparece el icono junto al reloj y esta ventana se cierra.
rem Windows pedira permiso de administrador una vez (para leer la temperatura de la CPU).
start "" %PYW% "%~dp0pulsopc_bandeja.pyw" --elevar
exit /b 0
