@echo off
setlocal EnableExtensions
title Instalador do Parca
echo.
echo  ===== Instalador do Parca (beta) =====
echo.

rem Installs Parca into %LOCALAPPDATA%\Parca\app from the latest GitHub
rem release, creates its Python environment and a desktop icon. Safe to run
rem again: it updates the files and keeps the environment. PARCA_HOME,
rem PARCA_ZIP, PARCA_NO_SHORTCUT, PARCA_NO_LAUNCH and PARCA_NO_PAUSE exist
rem only to test this installer; testers never set them.
if not defined PARCA_HOME set "PARCA_HOME=%LOCALAPPDATA%\Parca\app"

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto :have_python
echo  Nao encontrei o Python neste PC.
echo  Vou abrir a pagina de download do Python. Instale e, na primeira tela,
echo  MARQUE a opcao "Add python.exe to PATH". Depois rode este instalador de novo.
start "" "https://www.python.org/downloads/"
goto :fail_quiet
:have_python

echo  [1/4] Baixando o Parca...
set "TMPZIP=%TEMP%\parca-release.zip"
set "TMPDIR=%TEMP%\parca-release"
if defined PARCA_ZIP goto :local_zip
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $r = Invoke-RestMethod 'https://api.github.com/repos/vallanders-dev/game-companion-client/releases/latest'; Invoke-WebRequest -UseBasicParsing -OutFile $env:TMPZIP -Uri ('https://github.com/vallanders-dev/game-companion-client/archive/refs/tags/' + $r.tag_name + '.zip')" || goto :fail
goto :unzip
:local_zip
copy /y "%PARCA_ZIP%" "%TMPZIP%" >nul || goto :fail
:unzip
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Expand-Archive -LiteralPath $env:TMPZIP -DestinationPath $env:TMPDIR -Force" || goto :fail
set "SRC="
for /d %%D in ("%TMPDIR%\*") do set "SRC=%%D"
if not defined SRC goto :fail

echo  [2/4] Copiando os arquivos...
if not exist "%PARCA_HOME%" mkdir "%PARCA_HOME%" || goto :fail
robocopy "%SRC%" "%PARCA_HOME%" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
echo installed> "%PARCA_HOME%\.parca-install"
rmdir /s /q "%TMPDIR%" >nul 2>&1
del /q "%TMPZIP%" >nul 2>&1

echo  [3/4] Instalando os componentes - pode levar alguns minutos...
if exist "%PARCA_HOME%\.venv\Scripts\python.exe" goto :have_venv
%PY% -m venv "%PARCA_HOME%\.venv" || goto :fail
:have_venv
"%PARCA_HOME%\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check --upgrade pip
"%PARCA_HOME%\.venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r "%PARCA_HOME%\requirements.txt" || goto :fail

echo  [4/4] Criando o atalho na area de trabalho...
if defined PARCA_NO_SHORTCUT goto :shortcut_done
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = [Environment]::GetFolderPath('Desktop'); $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d ('Par' + [char]0x00E7 + 'a.lnk'))); $s.TargetPath = (Join-Path $env:PARCA_HOME '.venv\Scripts\pythonw.exe'); $s.Arguments = '-m client.main'; $s.WorkingDirectory = $env:PARCA_HOME; $s.IconLocation = (Join-Path $env:PARCA_HOME 'clientssets\parca.ico'); $s.Save()" || echo  Nao consegui criar o atalho. Abra o Parca pelo arquivo %PARCA_HOME%\Parca.cmd
:shortcut_done

echo.
echo  Pronto! O Parca foi instalado.
echo  Da proxima vez, abra pelo icone "Parca" na area de trabalho.
if defined PARCA_NO_LAUNCH goto :end
echo  Abrindo o Parca agora...
start "" /D "%PARCA_HOME%" "%PARCA_HOME%\.venv\Scripts\pythonw.exe" -m client.main
:end
if not defined PARCA_NO_PAUSE pause
exit /b 0

:fail
echo.
echo  Algo deu errado na instalacao.
echo  Tire um print desta janela e mande para quem te convidou.
:fail_quiet
if not defined PARCA_NO_PAUSE pause
exit /b 1
