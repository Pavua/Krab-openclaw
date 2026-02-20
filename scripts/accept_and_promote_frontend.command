#!/bin/zsh
# Единый сценарий приёмки frontend-поставки и (опционально) промоушена.
#
# Использование:
#   ./scripts/accept_and_promote_frontend.command
#     -> Полная приёмка + dry-run промоушена (без подмены index.html).
#
#   ./scripts/accept_and_promote_frontend.command --promote
#     -> Полная приёмка + реальный промоушен (с бэкапом и подменой index.html).
#
#   ./scripts/accept_and_promote_frontend.command --full
#     -> Приёмка с merge_guard --full + dry-run промоушена.
#
#   ./scripts/accept_and_promote_frontend.command --full --promote
#     -> Максимальная приёмка + реальный промоушен.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DO_PROMOTE=0
REVIEW_MODE=""

for arg in "$@"; do
  case "$arg" in
    --promote)
      DO_PROMOTE=1
      ;;
    --full)
      REVIEW_MODE="--full"
      ;;
    *)
      echo "❌ Неизвестный аргумент: $arg"
      echo "Разрешено: --promote, --full"
      exit 2
      ;;
  esac
done

echo "🔎 Шаг 1/2: Запускаю приёмку внешней поставки..."
if [[ -n "$REVIEW_MODE" ]]; then
  ./review_external_agent_delivery.command "$REVIEW_MODE"
else
  ./review_external_agent_delivery.command
fi

echo
echo "🧪 Шаг 2/2: Проверка/промоушен frontend-прототипа..."
if [[ "$DO_PROMOTE" -eq 1 ]]; then
  ./scripts/promote_web_prototype.command
  echo
  echo "✅ Frontend промоушен выполнен."
else
  ./scripts/promote_web_prototype.command --dry-run
  echo
  echo "✅ Проверки пройдены. Для реальной подмены запусти с флагом --promote."
fi
