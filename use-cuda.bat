@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 改用 onnxruntime-gpu（CUDA 12，需要 NVIDIA 顯卡）。之後若診斷頁仍顯示 CPU，請執行 use-directml.bat 改回。
set "UV=%~dp0tools\uv.exe"
if not exist "%UV%" set "UV=%~dp0tools\bin\uv.exe"
echo onnxruntime-gpu[cuda,cudnn]> ".venv\.ort"
if exist "%UV%" (
  "%UV%" pip uninstall --python ".venv\Scripts\python.exe" onnxruntime-directml onnxruntime
  "%UV%" pip install --python ".venv\Scripts\python.exe" "onnxruntime-gpu[cuda,cudnn]"
) else (
  ".venv\Scripts\python.exe" -m pip uninstall -y onnxruntime-directml onnxruntime
  ".venv\Scripts\python.exe" -m pip install "onnxruntime-gpu[cuda,cudnn]"
)
pause
