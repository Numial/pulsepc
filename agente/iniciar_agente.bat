@echo off
cd /d "%~dp0"
python -m pip install --quiet psutil
python pulsopc_agente.py
pause
