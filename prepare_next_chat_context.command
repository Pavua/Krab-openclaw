#!/bin/zsh
# -----------------------------------------------------------------------------
# Подготовка компактного контекста для нового диалога (anti-413)
# -----------------------------------------------------------------------------
# Что делает:
# 1) Снимает текущий статус ветки/коммита и изменённых файлов.
# 2) Добавляет краткий diff-стат и контрольные команды.
# 3) Формирует markdown-файл в artifacts/context/.
# 4) Копирует путь к файлу и его содержимое в буфер обмена (если доступно).
#
# Зачем:
# Чтобы быстро переносить контекст в новый чат без переполнения payload.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

mkdir -p artifacts/context

STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_FILE="artifacts/context/next_chat_context_${STAMP}.md"

BRANCH="$(git branch --show-current 2>/dev/null || echo 'unknown-branch')"
HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown-sha')"

{
  echo "# Контекст для нового диалога (anti-413)"
  echo
  echo "- Дата: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "- Ветка: \`${BRANCH}\`"
  echo "- Коммит: \`${HEAD_SHA}\`"
  echo
  echo "## Статус изменений (кратко)"
  echo
  echo '```text'
  git status --short || true
  echo '```'
  echo
  echo "## Diff summary"
  echo
  echo '```text'
  git diff --stat || true
  echo '```'
  echo
  echo "## Ключевые команды проверки"
  echo
  echo "- \`pytest -q tests/test_model_router_output_sanitizer.py tests/test_model_set_parser.py tests/test_forward_context.py tests/test_auto_reply_queue.py tests/test_handlers.py -k 'forward_context or auto_reply'\`"
  echo "- \`./openclaw_runtime_repair.command\`"
  echo "- \`./openclaw_signal_register.command\`"
  echo
  echo "## Следующий шаг (для нового чата)"
  echo
  echo "Продолжай с приоритетом Signal link/pairing и стабилизации каналов. Сначала проверь runtime, потом Signal, затем обнови HANDOVER/ROADMAP только по факту верификации."
} > "$OUT_FILE"

if command -v pbcopy >/dev/null 2>&1; then
  cat "$OUT_FILE" | pbcopy
  echo "✅ Контекст скопирован в буфер обмена."
fi

echo "✅ Готово: $OUT_FILE"
echo "Открой файл и вставь его в новый диалог."

