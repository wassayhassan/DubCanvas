#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

APP_VERSION="4.0.0a6"
echo "DubCanvas v${APP_VERSION} — setup"
echo "================================"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: This setup script targets macOS. Use setup-cross-platform.sh/ps1 on Linux/Windows."
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "ERROR: The native macOS build currently targets Apple Silicon."
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "ERROR: Homebrew is required. Install it from https://brew.sh and run setup again."
  exit 1
fi

echo "Installing system dependencies (safe to re-run)…"
brew install ffmpeg yt-dlp deno python@3.11

PY="$(brew --prefix python@3.11)/bin/python3.11"
if [[ ! -x "$PY" ]]; then
  echo "ERROR: Could not locate Homebrew Python 3.11 at: $PY"
  exit 1
fi

echo "Running source-code preflight before Python package installation…"
"$PY" verify_source.py

echo "Creating Python 3.11 virtual environment…"
if [[ -d .venv ]]; then
  VENV_VER="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if [[ "$VENV_VER" != "3.11" ]]; then
    echo "Existing virtual environment uses Python $VENV_VER; rebuilding it with Python 3.11."
    rm -rf .venv
  fi
fi

"$PY" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo "Installing a current app-local yt-dlp build…"
if ! python -m pip install -c constraints.txt --upgrade "yt-dlp[default,curl-cffi] @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz"; then
  echo "WARNING: Could not install yt-dlp master from GitHub; falling back to PyPI."
  python -m pip install -c constraints.txt --upgrade --pre "yt-dlp[default,curl-cffi]"
fi
echo "App-local yt-dlp version:"
python -m yt_dlp --version

echo "Installing optional high-accuracy speaker encoder…"
if ! python -m pip install -c constraints.txt "speechbrain>=1.0,<2"; then
  echo "WARNING: SpeechBrain could not be installed. DubCanvas will use its acoustic speaker-clustering fallback."
fi

echo "Installing local dubbing voices for supported target languages…"
/bin/zsh macos/install_voice_engines.sh --no-rebuild
python -m pip check

echo "Running post-install source preflight…"
python verify_source.py

echo "Running bundled regression/integration tests…"
python -m unittest discover -s tests -v

echo "Import-checking backend, CLI, and transport modules…"
python - <<'PYIMPORT'
import anime_dubber
import anime_dubber.core
import anime_dubber.characters
import anime_dubber.application
import anime_dubber.cli
import anime_dubber.transport.stdio_server
import dubcanvas
print("Python imports: OK")
PYIMPORT

echo "Running CLI parser smoke test…"
python -m dubcanvas --help >/dev/null

echo "Running backend system check…"
python -m dubcanvas doctor || true

if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$PWD" 2>/dev/null || true
fi

echo
if command -v swift >/dev/null 2>&1; then
  echo "Building and installing the native macOS app…"
  /bin/zsh macos/package_app.sh --install
  echo
  echo "Native app installed at the location reported above."
else
  echo "WARNING: Swift was not found, so the native .app was not built."
  echo "Install Xcode Command Line Tools with: xcode-select --install"
  echo "Then run: /bin/zsh macos/package_app.sh --install"
fi

echo
echo "Setup complete."
echo "CLI: .venv/bin/python -m dubcanvas --help"
echo "Run /bin/zsh macos/install_voice_engines.sh later to update local voice engines."
