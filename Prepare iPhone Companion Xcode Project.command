#!/bin/zsh
# -----------------------------------------------------------------------------
# Готовит локальный Xcode-проект для iPhone companion в текущей учётке.
# Связь с проектом: использует shared SwiftUI skeleton из Krab Voice Gateway,
# но создаёт .xcodeproj и signing-метаданные локально, без конфликтов между учётками.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export KRAB_VOICE_GATEWAY_DIR="${KRAB_VOICE_GATEWAY_DIR:-${SCRIPT_DIR}/../Krab Voice Gateway}"

python3 "$SCRIPT_DIR/scripts/generate_iphone_companion_xcode_project.py" --open-xcode

echo
echo "✅ iPhone companion project подготовлен и открыт в Xcode."
