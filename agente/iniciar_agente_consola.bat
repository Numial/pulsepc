@echo off
setlocal
cd /d "%~dp0"
title PulsePC - agente (consola)

rem --- Buscar Python: lanzador "py", "python" real (no el atajo de la Microsoft Store) o rutas habituales ---
set "PY="
where py >nul 2>nul && (py -3 -c "import sys" >nul 2>nul && set "PY=py -3")
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

echo Usando Python: %PY%
%PY% -m pip install --quiet --disable-pip-version-check psutil
%PY% pulsopc_agente.py
pause
