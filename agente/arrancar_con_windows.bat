@echo off
rem Crea un acceso directo en la carpeta de Inicio para que el agente arranque
rem (minimizado) cada vez que enciendas el PC. Para quitarlo, borra
rem "PulsoPC agente" de:  shell:startup
cd /d "%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\PulsoPC agente.lnk'); $s.TargetPath='%~dp0iniciar_agente.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (echo No se pudo crear el acceso directo.) else (echo Listo: el agente arrancara solo al iniciar Windows.)
pause
