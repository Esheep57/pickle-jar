@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Pickle Jar

rem ---- 用 uv 準備獨立的 Python：不需要先裝 Python，也不會動到電腦上其他的 Python ----
set "TOOLS=%~dp0tools"
set "UV=%TOOLS%\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%TOOLS%\python"
set "UV_CACHE_DIR=%TOOLS%\cache"
set "UV_LINK_MODE=copy"

if not exist "%UV%" if exist "%TOOLS%\bin\uv.exe" set "UV=%TOOLS%\bin\uv.exe"
if not exist "%UV%" (
  echo [1/3] 下載 uv（Python 安裝工具，約 20MB）...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_INSTALL_DIR='%TOOLS%'; $env:UV_NO_MODIFY_PATH='1'; irm https://astral.sh/uv/install.ps1 | iex"
  if not exist "%UV%" if exist "%TOOLS%\bin\uv.exe" set "UV=%TOOLS%\bin\uv.exe"
)
if not exist "%UV%" goto :fallback

if not exist ".venv\Scripts\python.exe" (
  echo [2/3] 準備 Python 3.12...
  "%UV%" venv --python 3.12 .venv || goto :fail
)

rem requirements.txt 有變（更新版本）才重新安裝
fc /b requirements.txt ".venv\.req" >nul 2>&1 && goto :run
echo [3/3] 安裝套件（第一次需要幾分鐘）...
"%UV%" pip install --python ".venv\Scripts\python.exe" -r requirements.txt || goto :fail
set "ORT=onnxruntime-directml"
if exist ".venv\.ort" set /p ORT=<".venv\.ort"
"%UV%" pip install --python ".venv\Scripts\python.exe" "%ORT%" || goto :fail
copy /y requirements.txt ".venv\.req" >nul
goto :run

:fallback
echo 無法下載 uv，改用電腦上已安裝的 Python...
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo 找不到 Python。請確認網路可以連到 astral.sh，或自行安裝 Python 3.12：https://www.python.org/downloads/
  pause & exit /b 1
)
fc /b requirements.txt ".venv\.req" >nul 2>&1 && goto :run
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
set "ORT=onnxruntime-directml"
if exist ".venv\.ort" set /p ORT=<".venv\.ort"
".venv\Scripts\python.exe" -m pip install "%ORT%" || goto :fail
copy /y requirements.txt ".venv\.req" >nul

:run
".venv\Scripts\python.exe" run.py %*
pause
exit /b 0

:fail
echo.
echo 安裝失敗。請把上面的錯誤訊息截圖；也可以刪掉 .venv 資料夾再執行一次。
pause
exit /b 1
