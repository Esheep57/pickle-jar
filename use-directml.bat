@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 改回 onnxruntime-directml（任何支援 DirectX 12 的顯卡：NVIDIA / AMD / Intel）。
set "UV=%~dp0tools\uv.exe"
if not exist "%UV%" set "UV=%~dp0tools\bin\uv.exe"
echo onnxruntime-directml> ".venv\.ort"
if exist "%UV%" (
  "%UV%" pip uninstall --python ".venv\Scripts\python.exe" onnxruntime-gpu onnxruntime
  "%UV%" pip install --python ".venv\Scripts\python.exe" onnxruntime-directml
) else (
  ".venv\Scripts\python.exe" -m pip uninstall -y onnxruntime-gpu onnxruntime
  ".venv\Scripts\python.exe" -m pip install onnxruntime-directml
)
pause
