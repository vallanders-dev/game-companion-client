@echo off
rem Starts Parca from this folder. The desktop icon made by instalar-parca.cmd
rem points here. If your game runs as administrator, right-click the icon and
rem choose "Executar como administrador" - otherwise Windows hides your key
rem presses from Parca while the game has focus.
cd /d "%~dp0"
title Parca
if exist ".venv\Scripts\python.exe" goto :run
echo  O Parca nao esta instalado nesta pasta. Rode o instalar-parca.cmd de novo.
pause
exit /b 1
:run
".venv\Scripts\python.exe" -m client.main
if errorlevel 1 pause
