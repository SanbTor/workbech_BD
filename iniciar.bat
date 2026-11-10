@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SQL Workbench

rem O ambiente fica fora da pasta do app (e fora do OneDrive) para nao ser sincronizado.
set "VENV=%LOCALAPPDATA%\SQLWorkbench\venv"
set "VPY=%VENV%\Scripts\python.exe"

if exist "%VPY%" if exist "%VENV%\pronto-wb1.txt" goto rodar

echo.
echo  Preparando o SQL Workbench pela primeira vez (leva 1 a 2 minutos)...
echo.

set "PY="
rem 1) Launcher oficial do Python (py)
py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
rem 2) python no PATH (o atalho falso da Microsoft Store e rejeitado pelo teste)
if not defined PY call :tentar python
rem 3) Pastas padrao de instalacao
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :tentar "%%~D\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do call :tentar "%%~D\python.exe"
if not defined PY for /d %%D in ("C:\Python3*") do call :tentar "%%~D\python.exe"

if not defined PY (
  echo  Nao encontrei um Python 3.10 ou superior que funcione neste computador.
  echo.
  echo  Instale em https://www.python.org/downloads/windows/ e marque
  echo  "Add python.exe to PATH" na primeira tela. Depois rode este arquivo de novo.
  echo  O atalho "python" da Microsoft Store nao conta: ele nao e um Python de verdade.
  echo.
  pause
  exit /b 1
)
echo  Usando: %PY%

if exist "%VENV%" if not exist "%VPY%" rmdir /s /q "%VENV%" >nul 2>nul
if not exist "%VPY%" (
  if not exist "%LOCALAPPDATA%\SQLWorkbench" mkdir "%LOCALAPPDATA%\SQLWorkbench"
  %PY% -m venv "%VENV%"
)
if not exist "%VPY%" (
  echo.
  echo  Nao foi possivel criar o ambiente virtual em:
  echo  %VENV%
  echo  Se o computador bloqueia essa pasta, peca liberacao a TI.
  pause
  exit /b 1
)

"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo  Falha ao instalar os pacotes. Se a rede usa proxy, configure o pip e rode de novo.
  pause
  exit /b 1
)
echo.
echo  Instalando os drivers de banco de dados (MySQL e Oracle)...
"%VPY%" -m pip install pymysql
"%VPY%" -m pip install oracledb
echo ok> "%VENV%\pronto-wb1.txt"

:rodar
"%VPY%" app.py
pause
exit /b 0

:tentar
if defined PY goto :eof
%1 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 set PY=%1
goto :eof
