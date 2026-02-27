#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SPEC_FILE="$ROOT_DIR/CLI Monitor.spec"
APP_PATH="$ROOT_DIR/dist/CLI Monitor.app"
BIN_PATH="$APP_PATH/Contents/MacOS/CLI Monitor"
INFO_PLIST="$APP_PATH/Contents/Info.plist"
APP_VERSION="0.0.3"
PYI_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT_DIR/.pyinstaller}"
BUILD_ASSETS_DIR="$ROOT_DIR/.build_assets"
RELEASE_PANEL_HTML="$BUILD_ASSETS_DIR/panel.html"

echo "[build] root: $ROOT_DIR"
echo "[build] spec: $SPEC_FILE"
echo "[build] pyinstaller config dir: $PYI_CONFIG_DIR"

cd "$ROOT_DIR"
mkdir -p "$PYI_CONFIG_DIR"
python3 "$ROOT_DIR/scripts/obfuscate_panel.py" "$ROOT_DIR/panel.html" "$RELEASE_PANEL_HTML"
export PYINSTALLER_CONFIG_DIR="$PYI_CONFIG_DIR"
export CLI_MONITOR_PANEL_HTML="$RELEASE_PANEL_HTML"
pyinstaller --noconfirm "$SPEC_FILE"

if [[ ! -d "$APP_PATH" ]]; then
  echo "[build] error: app not found: $APP_PATH" >&2
  exit 1
fi

if [[ ! -x "$BIN_PATH" ]]; then
  echo "[build] error: executable not found: $BIN_PATH" >&2
  exit 1
fi

if [[ -f "$INFO_PLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$INFO_PLIST" \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$INFO_PLIST"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$INFO_PLIST" \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$INFO_PLIST"
  codesign --force --deep --sign - "$APP_PATH" >/dev/null 2>&1 || true
fi

echo "[verify] app bundle exists"
echo "[verify] executable architecture:"
file "$BIN_PATH"

echo "[verify] code signing:"
codesign -dv --verbose=2 "$APP_PATH" 2>&1 | head -n 20
echo "[verify] app version:"
plutil -p "$INFO_PLIST" | rg 'CFBundleShortVersionString|CFBundleVersion' || true

echo "[done] build completed: $APP_PATH"
