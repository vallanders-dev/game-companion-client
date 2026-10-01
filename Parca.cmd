@echo off
rem Starts Parca from this folder with its console (the icons start it
rem without one). If your game runs as administrator, right-click the icon and
rem choose "Executar como administrador" - otherwise Windows hides your key
rem presses from Parca while the game has focus. "Parca.cmd --settings" opens
rem the settings window (language, voice, token).
cd /d "%~dp0"
title Parca
set "PY=runtime\python.exe"
if exist "%PY%" goto :run
set "PY=.venv\Scripts\python.exe"
if exist "%PY%" goto :run
echo  O Parca nao esta instalado nesta pasta. Instale de novo com o Parca-Setup.exe.
pause
exit /b 1
:run
"%PY%" -m client.main %*
if errorlevel 1 pause
