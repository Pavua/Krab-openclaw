#!/bin/zsh
# -----------------------------------------------------------------------------
# OpenClaw Channels/Skills Bootstrap (Krab)
# -----------------------------------------------------------------------------
# Зачем:
# 1) быстро получить отчёт по readiness каналов и скиллов OpenClaw;
# 2) опционально применить безопасный baseline-конфиг;
# 3) опционально подключить каналы, если credentials уже есть в env.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
PROFILE="${OPENCLAW_PROFILE_NAME:-main}"

MODE="${1:-audit}"
ENABLE_CHANNELS="${2:-}"

ARGS=(--profile "${PROFILE}")

if [[ "${MODE}" == "apply" ]]; then
  ARGS+=(--apply-safe)
fi

if [[ -n "${ENABLE_CHANNELS}" ]]; then
  ARGS+=(--enable "${ENABLE_CHANNELS}")
fi

if command -v clear >/dev/null 2>&1; then
  clear || true
fi
echo "🧭 OpenClaw Channels/Skills Bootstrap"
echo "Профиль: ${PROFILE}"
echo "Режим: ${MODE}"
if [[ -n "${ENABLE_CHANNELS}" ]]; then
  echo "Каналы для auto-enable: ${ENABLE_CHANNELS}"
fi
echo "Дата: $(date)"
echo

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/openclaw_channels_skills_bootstrap.py" "${ARGS[@]}"

echo
echo "Готово."
echo "Примеры:"
echo "  1) Audit: ./openclaw_channels_skills_bootstrap.command"
echo "  2) Safe baseline: ./openclaw_channels_skills_bootstrap.command apply"
echo "  3) Enable каналы из env: ./openclaw_channels_skills_bootstrap.command apply discord,slack"
