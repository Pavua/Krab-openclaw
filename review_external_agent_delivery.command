#!/bin/zsh
# -----------------------------------------------------------------------------
# Приёмка результатов от внешних нейросетей перед интеграцией (one-click)
# -----------------------------------------------------------------------------
# Что делает:
# 1) (Если есть frontend-прототип) проверяет DOM-совместимость и runtime parity.
# 2) Проверяет ownership-overlap.
# 3) Запускает merge guard.
# 4) Опционально запускает расширенный merge guard --full.
#
# Использование:
# - ./review_external_agent_delivery.command
# - ./review_external_agent_delivery.command --full
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

FULL_MODE="${1:-}"

if [[ -f "src/web/prototypes/nano/index_redesign.html" && -x "scripts/validate_web_prototype_compat.command" ]]; then
  echo "0/3 Frontend prototype compatibility"
  scripts/validate_web_prototype_compat.command
  echo
fi

if [[ -f "src/web/prototypes/nano/index_redesign.html" && -f "scripts/validate_web_runtime_parity.py" ]]; then
  echo "1/3 Frontend runtime parity"
  python3 scripts/validate_web_runtime_parity.py \
    --base src/web/index.html \
    --prototype src/web/prototypes/nano/index_redesign.html
  echo
fi

echo "2/3 Overlap check"
python3 scripts/check_workstream_overlap.py

echo
echo "3/3 Merge guard"
python3 scripts/merge_guard.py

if [[ "$FULL_MODE" == "--full" ]]; then
  echo
  echo "4/4 Merge guard (full)"
  python3 scripts/merge_guard.py --full
fi

echo
echo "✅ Приёмка завершена успешно."
if [[ -t 0 && -t 1 ]]; then
  read -k "_ANY?Нажми любую клавишу для закрытия..."
  echo
fi
