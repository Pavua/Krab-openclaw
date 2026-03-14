#!/bin/zsh
# -----------------------------------------------------------------------------
# Прогоняет generator + simulator build для iPhone companion без code signing.
# Нужен как быстрый smoke, чтобы понять, что Xcode-проект и SwiftUI skeleton
# компилируются до реального шага free signing на физическом iPhone.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export KRAB_VOICE_GATEWAY_DIR="${KRAB_VOICE_GATEWAY_DIR:-${SCRIPT_DIR}/../Krab Voice Gateway}"

python3 "$SCRIPT_DIR/scripts/generate_iphone_companion_xcode_project.py"

echo
echo "✅ Simulator build прошёл успешно."
