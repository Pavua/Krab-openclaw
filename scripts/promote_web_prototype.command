#!/bin/zsh
# Безопасная интеграция frontend-прототипа в боевой index.html.
#
# Что делает:
# 1) Прогоняет гейты совместимости и runtime parity.
# 2) Создаёт timestamp-бэкап текущего src/web/index.html.
# 3) Подменяет src/web/index.html содержимым прототипа.
#
# Режим dry-run:
#   ./scripts/promote_web_prototype.command --dry-run

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_FILE="src/web/index.html"
PROTOTYPE_FILE="src/web/prototypes/nano/index_redesign.html"
BACKUP_DIR="artifacts/web_backups"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

if [[ ! -f "$BASE_FILE" ]]; then
  echo "❌ Базовый файл не найден: $BASE_FILE"
  exit 2
fi

if [[ ! -f "$PROTOTYPE_FILE" ]]; then
  echo "❌ Прототип не найден: $PROTOTYPE_FILE"
  exit 2
fi

echo "🧪 Шаг 1/3: Проверка совместимости прототипа..."
python3 scripts/validate_web_prototype_compat.py --base "$BASE_FILE" --prototype "$PROTOTYPE_FILE"

echo "🧪 Шаг 2/3: Проверка runtime parity..."
python3 scripts/validate_web_runtime_parity.py --base "$BASE_FILE" --prototype "$PROTOTYPE_FILE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "✅ Dry-run завершён: все проверки пройдены, подмена не выполнялась."
  exit 0
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date '+%Y%m%d_%H%M%S')"
BACKUP_FILE="$BACKUP_DIR/index_before_promote_${STAMP}.html"

echo "💾 Шаг 3/3: Создаю бэкап: $BACKUP_FILE"
cp "$BASE_FILE" "$BACKUP_FILE"

echo "🚀 Подменяю $BASE_FILE <- $PROTOTYPE_FILE"
cp "$PROTOTYPE_FILE" "$BASE_FILE"

echo "✅ Интеграция завершена."
echo "   Backup: $BACKUP_FILE"
echo "   Current: $BASE_FILE"
