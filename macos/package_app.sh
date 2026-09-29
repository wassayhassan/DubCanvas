#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG="$SCRIPT_DIR/DubCanvasApp"
DIST="$ROOT/dist"
APP="$DIST/DubCanvas.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
BACKEND="$RESOURCES/backend"

EMBED_VENV=1
INSTALL=0
INSTALL_TO=""
PRINT_TARGET=0
OPEN_APP=0
SIGN=1
UNIVERSAL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-embed-venv) EMBED_VENV=0 ;;
    --install) INSTALL=1 ;;
    --print-install-target) PRINT_TARGET=1 ;;
    --install-to)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --install-to needs the full path to a .app bundle" >&2
        exit 2
      fi
      INSTALL=1
      INSTALL_TO="$2"
      shift
      ;;
    --open) OPEN_APP=1 ;;
    --no-sign) SIGN=0 ;;
    --universal) UNIVERSAL=1 ;;
    --help)
      cat <<'EOF'
Usage: ./macos/package_app.sh [options]

Options:
  --no-embed-venv  Build a lightweight app bundle for CI/development.
  --install        Install to /Applications, replacing the existing app there.
                   Remove an old ~/Applications copy after installation succeeds.
  --install-to PATH  Install to a specific .app instead.
  --print-install-target  Show the selected destination without building.
  --open           Open the app after packaging/installing.
  --no-sign        Skip local ad-hoc code signing.
  --universal      Build for both Apple silicon and Intel Macs.
EOF
      exit 0
      ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

TARGET="$APP"
if [[ "$INSTALL" -eq 1 ]]; then
  TARGET="/Applications/DubCanvas.app"
  if [[ -n "$INSTALL_TO" ]]; then
    if [[ "$INSTALL_TO" != /* || "$INSTALL_TO" != *.app ]]; then
      echo "ERROR: --install-to must be an absolute path ending in .app" >&2
      exit 2
    fi
    TARGET="$INSTALL_TO"
  fi
  echo "App installation target: $TARGET"
fi
if [[ "$PRINT_TARGET" -eq 1 ]]; then
  echo "$TARGET"
  exit 0
fi

if [[ "$INSTALL" -eq 1 && "$TARGET" == "/Applications/DubCanvas.app" && ! -w /Applications ]]; then
  echo "ERROR: This account cannot write to /Applications. Build without --install, then move dist/DubCanvas.app into /Applications using Finder." >&2
  exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
  echo "ERROR: Swift is required. Install Xcode Command Line Tools with: xcode-select --install"
  exit 1
fi

if [[ "$EMBED_VENV" -eq 1 && ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "ERROR: .venv is missing. Run ./setup.sh first, or use --no-embed-venv."
  exit 1
fi

VERSION="$("$ROOT/.venv/bin/python" -c 'import anime_dubber; print(anime_dubber.__version__)' 2>/dev/null || echo "4.0.0a7")"
SHORT_VERSION="${VERSION%%a*}"

echo "Building SwiftUI app (release)…"
if [[ "$UNIVERSAL" -eq 1 ]]; then
  swift build -c release --arch arm64 --package-path "$PKG"
  swift build -c release --arch x86_64 --package-path "$PKG"
  ARM_BIN="$(swift build -c release --arch arm64 --package-path "$PKG" --show-bin-path)/DubCanvasApp"
  INTEL_BIN="$(swift build -c release --arch x86_64 --package-path "$PKG" --show-bin-path)/DubCanvasApp"
  mkdir -p "$DIST"
  BIN="$DIST/DubCanvas-universal"
  lipo -create "$ARM_BIN" "$INTEL_BIN" -output "$BIN"
else
  swift build -c release --package-path "$PKG"
  BIN_DIR="$(swift build -c release --package-path "$PKG" --show-bin-path)"
  BIN="$BIN_DIR/DubCanvasApp"
fi
SOURCE_REVISION="${DUBCANVAS_SOURCE_REVISION:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo development)}"
SOURCE_TIMESTAMP="${DUBCANVAS_SOURCE_TIMESTAMP:-$(git -C "$ROOT" show -s --format=%ct HEAD 2>/dev/null || echo 0)}"
if [[ ! -x "$BIN" ]]; then
  echo "ERROR: Swift build completed but executable was not found at $BIN"
  exit 1
fi

echo "Assembling $APP…"
rm -rf "$APP"
mkdir -p "$MACOS" "$BACKEND" "$RESOURCES"
cp "$BIN" "$MACOS/DubCanvas"
chmod +x "$MACOS/DubCanvas"

ICON_SOURCE="$ROOT/macos/assets/DubCanvasIcon.png"
ICONSET="$DIST/DubCanvas.iconset"
mkdir -p "$ICONSET"
for icon_size in 16 32 128 256 512; do
  sips -z "$icon_size" "$icon_size" "$ICON_SOURCE" --out "$ICONSET/icon_${icon_size}x${icon_size}.png" >/dev/null
  double_size=$((icon_size * 2))
  sips -z "$double_size" "$double_size" "$ICON_SOURCE" --out "$ICONSET/icon_${icon_size}x${icon_size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RESOURCES/DubCanvas.icns"
rm -rf "$ICONSET"

ditto "$ROOT/anime_dubber" "$BACKEND/anime_dubber"
ditto "$ROOT/dubcanvas" "$BACKEND/dubcanvas"
cp "$ROOT/requirements.txt" "$BACKEND/requirements.txt"
cp "$ROOT/requirements-cross-platform.txt" "$BACKEND/requirements-cross-platform.txt"
cp "$ROOT/requirements-premium-voices.txt" "$BACKEND/requirements-premium-voices.txt"
cp "$ROOT/constraints.txt" "$BACKEND/constraints.txt"
cp "$ROOT/verify_source.py" "$BACKEND/verify_source.py"
cp "$ROOT/macos/install_update.sh" "$RESOURCES/install_update.sh"
find "$BACKEND" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$BACKEND" -name '*.pyc' -delete 2>/dev/null || true

if [[ "$EMBED_VENV" -eq 1 ]]; then
  echo "Embedding the existing Python environment (this is the large part of the app)…"
  ditto "$ROOT/.venv" "$BACKEND/.venv"
fi

cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>DubCanvas</string>
  <key>CFBundleIdentifier</key>
  <string>com.animedubber.app</string>
  <key>CFBundleDisplayName</key>
  <string>DubCanvas</string>
  <key>CFBundleName</key>
  <string>DubCanvas</string>
  <key>CFBundleIconFile</key>
  <string>DubCanvas.icns</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$SHORT_VERSION</string>
  <key>CFBundleVersion</key>
  <string>$SOURCE_TIMESTAMP</string>
  <key>DubCanvasSourceRevision</key>
  <string>$SOURCE_REVISION</string>
  <key>DubCanvasSourceTimestamp</key>
  <string>$SOURCE_TIMESTAMP</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key>
  <true/>
</dict>
</plist>
EOF

if [[ "$SIGN" -eq 1 ]]; then
  echo "Applying local ad-hoc signature…"
  codesign --force --deep --sign - "$APP"
fi

if [[ "$INSTALL" -eq 1 ]]; then
  if pgrep -x DubCanvas >/dev/null 2>&1 || pgrep -x AnimeDubber >/dev/null 2>&1; then
    echo "ERROR: Quit DubCanvas or the older AnimeDubber app before installing." >&2
    exit 1
  fi
  mkdir -p "${TARGET:h}"
  STAGED="${TARGET}.installing.$$"
  BACKUP="${TARGET}.previous.$$"
  ditto "$APP" "$STAGED"
  if [[ -e "$TARGET" ]]; then mv "$TARGET" "$BACKUP"; fi
  if mv "$STAGED" "$TARGET"; then
    if [[ -e "$BACKUP" ]]; then rm -rf "$BACKUP"; fi
    if [[ "$TARGET" != "$APP" ]]; then rm -rf "$APP"; fi
    if [[ -z "$INSTALL_TO" && -e "$HOME/Applications/DubCanvas.app" ]]; then
      rm -rf "$HOME/Applications/DubCanvas.app"
      echo "Removed old copy: $HOME/Applications/DubCanvas.app"
    fi
    echo "Updated: $TARGET"
  else
    if [[ -e "$BACKUP" ]]; then mv "$BACKUP" "$TARGET"; fi
    echo "ERROR: Could not install DubCanvas at $TARGET" >&2
    exit 1
  fi
else
  echo "Built: $APP"
fi

if [[ "$OPEN_APP" -eq 1 ]]; then
  open "$TARGET"
fi

echo
echo "DubCanvas is ready."
echo "Models remain in the normal Hugging Face / ML caches and are not duplicated inside the app."
