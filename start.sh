#!/usr/bin/env bash
# macOS / Linux：有 uv 就用 uv（自動準備 Python 3.12），沒有就用系統的 python3
cd "$(dirname "$0")"
if command -v uv >/dev/null 2>&1; then
  [ -x .venv/bin/python ] || uv venv --python 3.12 .venv
  if ! cmp -s requirements.txt .venv/.req; then
    uv pip install --python .venv/bin/python -r requirements.txt onnxruntime && cp requirements.txt .venv/.req
  fi
else
  [ -x .venv/bin/python ] || python3 -m venv .venv
  if ! cmp -s requirements.txt .venv/.req; then
    .venv/bin/pip install -r requirements.txt onnxruntime && cp requirements.txt .venv/.req
  fi
fi
exec .venv/bin/python run.py "$@"
