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
LATEST_FILE="artifacts/context/NEXT_CHAT_CONTEXT_LATEST.md"

BRANCH="$(git branch --show-current 2>/dev/null || echo 'unknown-branch')"
HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown-sha')"

STATUS_SHORT="$(git status --short 2>/dev/null || true)"
DIFF_STAT="$(git diff --stat 2>/dev/null || true)"
RECENT_COMMITS="$(git log --oneline -n 10 2>/dev/null || true)"

trim_block() {
  local content="$1"
  local max_lines="$2"
  echo "$content" | sed -n "1,${max_lines}p"
}

STATUS_TRIMMED="$(trim_block "$STATUS_SHORT" 120)"
DIFF_TRIMMED="$(trim_block "$DIFF_STAT" 120)"
COMMITS_TRIMMED="$(trim_block "$RECENT_COMMITS" 20)"

{
  echo "# Контекст для нового диалога (anti-413)"
  echo
  echo "- Дата: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "- Ветка: \`${BRANCH}\`"
  echo "- Коммит: \`${HEAD_SHA}\`"
  echo
  echo "## 413-safe TL;DR (вставлять первым сообщением)"
  echo
  echo '```text'
  echo "[CHECKPOINT]"
  echo "branch=${BRANCH}"
  echo "head=${HEAD_SHA}"
  echo "focus=стабилизация каналов + web/control + приемка внешних задач"
  echo "done=основные R-пакеты интегрированы, есть web control center и runtime API"
  echo "next=принять свежие правки, прогнать targeted pytest, зафиксировать handoff"
  echo "risks=шумные изменения из параллельных окон, возможные конфликты UI/API-контрактов"
  echo '```'
  echo
  echo "## Статус изменений (кратко)"
  echo
  echo '```text'
  echo "${STATUS_TRIMMED}"
  echo '```'
  echo
  echo "## Diff summary"
  echo
  echo '```text'
  echo "${DIFF_TRIMMED}"
  echo '```'
  echo
  echo "## Последние коммиты"
  echo
  echo '```text'
  echo "${COMMITS_TRIMMED}"
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
  echo
  echo "## Какие файлы приложить в новый диалог"
  echo
  echo "1. \`$OUT_FILE\`"
  echo "2. \`AGENTS.md\`"
  echo "3. \`HANDOVER.md\`"
  echo "4. \`ROADMAP.md\`"
  echo "5. \`docs/CHAT_TRANSITION_PLAYBOOK_RU.md\`"
} > "$OUT_FILE"

cp "$OUT_FILE" "$LATEST_FILE"

if command -v pbcopy >/dev/null 2>&1; then
  cat "$OUT_FILE" | pbcopy
  echo "✅ Контекст скопирован в буфер обмена."
fi

echo "✅ Готово: $OUT_FILE"
echo "✅ Обновлен latest: $LATEST_FILE"
echo "Открой файл и вставь его в новый диалог."
