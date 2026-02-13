#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SPEC_FILE="$ROOT_DIR/CLI Monitor.spec"
APP_PATH="$ROOT_DIR/dist/CLI Monitor.app"
BIN_PATH="$APP_PATH/Contents/MacOS/CLI Monitor"
PYI_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT_DIR/.pyinstaller}"

echo "[build] root: $ROOT_DIR"
echo "[build] spec: $SPEC_FILE"
echo "[build] pyinstaller config dir: $PYI_CONFIG_DIR"

cd "$ROOT_DIR"
mkdir -p "$PYI_CONFIG_DIR"
export PYINSTALLER_CONFIG_DIR="$PYI_CONFIG_DIR"
pyinstaller --noconfirm "$SPEC_FILE"

if [[ ! -d "$APP_PATH" ]]; then
  echo "[build] error: app not found: $APP_PATH" >&2
  exit 1
fi

if [[ ! -x "$BIN_PATH" ]]; then
  echo "[build] error: executable not found: $BIN_PATH" >&2
  exit 1
fi

echo "[verify] app bundle exists"
echo "[verify] executable architecture:"
file "$BIN_PATH"

echo "[verify] code signing:"
codesign -dv --verbose=2 "$APP_PATH" 2>&1 | head -n 20

echo "[done] build completed: $APP_PATH"
