#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.11+ is required."
  exit 1
fi

"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements-cross-platform.txt
.venv/bin/python -m pip check

echo
echo "Python dependencies installed."
echo "FFmpeg/ffprobe must also be available on PATH."
echo "For local Piper TTS, download a Piper .onnx voice model and pass --piper-model."
echo "Ollama is optional; install/start it separately if you want --translation ollama."
echo
.venv/bin/python -m dubcanvas doctor || true
