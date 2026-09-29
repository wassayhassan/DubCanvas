#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "ERROR: DubCanvas's .venv does not exist. Run ./setup.sh first."
  exit 1
fi

if command -v brew >/dev/null 2>&1; then
  echo "Installing Kokoro speech dependency espeak-ng…"
  brew install espeak-ng
else
  echo "WARNING: Homebrew was not found. Kokoro requires espeak-ng."
fi

PY="$ROOT/.venv/bin/python"
"$PY" -m pip install --upgrade pip setuptools wheel

echo
echo "Installing Kokoro and Chatterbox together with the backend dependencies…"
echo "Chatterbox pins its compatible PyTorch/torchaudio versions, so this step can take a while."
if ! "$PY" -m pip install -r requirements.txt -r requirements-premium-voices.txt; then
  echo
  echo "WARNING: Full voice-engine installation failed. Trying Kokoro with the backend dependencies."
  "$PY" -m pip install -c constraints.txt -r requirements.txt "kokoro>=0.9.4,<1" soundfile
  echo "DubCanvas will automatically fall back to Kokoro/macOS voices."
fi
"$PY" -m pip check

echo
echo "Voice provider status:"
"$PY" - <<'PY'
from anime_dubber.providers.tts import premium_voice_status
for name, ok in premium_voice_status().items():
    print(f"{'✓' if ok else '✗'} {name}")
PY

echo
if [[ "${1:-}" != "--no-rebuild" ]]; then
  echo "Running backend tests after voice-engine installation…"
  "$PY" -m unittest discover -s tests -v
fi

echo
if [[ "${1:-}" == "--no-rebuild" ]]; then
  echo "The app will be built by setup after this step."
elif command -v swift >/dev/null 2>&1; then
  echo "Rebuilding the native app so its bundled backend includes the new voice engines…"
  /bin/zsh macos/package_app.sh --install
  echo "Updated the app at the location reported above."
else
  echo "Swift is not available. Rebuild the app later with:"
  echo "  /bin/zsh macos/package_app.sh --install"
fi
