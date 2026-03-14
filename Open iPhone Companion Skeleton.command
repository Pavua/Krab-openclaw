#!/bin/zsh
# -----------------------------------------------------------------------------
# Открывает iPhone companion SwiftUI skeleton для работы в Xcode.
# Связь с проектом: использует Krab Voice Gateway/ios/KrabVoiceiOS.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VOICE_DIR="${KRAB_VOICE_GATEWAY_DIR:-${SCRIPT_DIR}/../Krab Voice Gateway}"
OPEN_SCRIPT="${VOICE_DIR}/scripts/open_ios_skeleton.command"
IOS_DIR="${VOICE_DIR}/ios/KrabVoiceiOS"

if [[ -x "${OPEN_SCRIPT}" ]]; then
  "${OPEN_SCRIPT}"
elif [[ -d "${IOS_DIR}" ]]; then
  open "${IOS_DIR}"
else
  echo "❌ Не найден iOS skeleton: ${IOS_DIR}"
  exit 1
fi

echo "✅ Открыт iPhone companion skeleton."
