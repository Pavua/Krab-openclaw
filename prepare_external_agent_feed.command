#!/bin/zsh
# -----------------------------------------------------------------------------
# Сборка пакета файлов для внешних нейросетей (one-click)
# -----------------------------------------------------------------------------
# Что делает:
# 1) Обновляет контекст для нового диалога.
# 2) Собирает единый feed-пакет в artifacts/agent_feed_pack/<timestamp>.
# 3) Копирует ключевые документы и промпты по ролям.
# 4) Печатает готовые пути, которые можно сразу "скормить" в другой чат.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -x "./prepare_next_chat_context.command" ]]; then
  ./prepare_next_chat_context.command >/dev/null
fi

LATEST_CONTEXT="$(ls -t artifacts/context/next_chat_context_*.md 2>/dev/null | head -n 1 || true)"
if [[ -z "$LATEST_CONTEXT" ]]; then
  echo "❌ Не найден файл контекста artifacts/context/next_chat_context_*.md"
  exit 1
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_DIR="artifacts/agent_feed_pack/pack_${STAMP}"
mkdir -p "$OUT_DIR"

cp "AGENTS.md" "$OUT_DIR/"
cp "docs/NEURAL_PARALLEL_MASTER_PLAN_RU.md" "$OUT_DIR/"
cp "docs/CHAT_TRANSITION_PLAYBOOK_RU.md" "$OUT_DIR/"
cp "docs/EXTERNAL_AGENT_FEED_INDEX_RU.md" "$OUT_DIR/"
cp "docs/ANTIGRAVITY_START_HERE.md" "$OUT_DIR/"
cp "docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md" "$OUT_DIR/"
cp "docs/ANTIGRAVITY_BACKLOG_V8.md" "$OUT_DIR/"
cp "docs/ANTIGRAVITY_NEXT_SPRINTS_V8.md" "$OUT_DIR/"
cp "docs/ANTIGRAVITY_REMAINING_V8.md" "$OUT_DIR/"
cp "docs/EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_RU.md" "$OUT_DIR/"
cp "docs/EXTERNAL_PROMPT_NANOBANANA_UI_RU.md" "$OUT_DIR/"
cp "docs/parallel_execution_split_v8.md" "$OUT_DIR/"
cp "docs/OPENCLAW_DASHBOARD_PLAYBOOK_RU.md" "$OUT_DIR/"
cp "src/web/index.html" "$OUT_DIR/"
cp "config/workstreams/antigravity_paths.txt" "$OUT_DIR/"
cp "config/workstreams/codex_paths.txt" "$OUT_DIR/"
cp "config/workstreams/gemini_design_paths.txt" "$OUT_DIR/"
cp "config/workstreams/nanobanana_ui_paths.txt" "$OUT_DIR/"
cp "$LATEST_CONTEXT" "$OUT_DIR/"

cat > "$OUT_DIR/PACK_INSTRUCTIONS.md" <<EOF
# Feed Pack Instructions

Папка собрана автоматически: ${OUT_DIR}

## Общий пакет (всем)
1. AGENTS.md
2. NEURAL_PARALLEL_MASTER_PLAN_RU.md
3. CHAT_TRANSITION_PLAYBOOK_RU.md
4. $(basename "$LATEST_CONTEXT")

## Antigravity
1. ANTIGRAVITY_START_HERE.md
2. ANTIGRAVITY_WORKSTREAM_PROMPT.md
3. ANTIGRAVITY_BACKLOG_V8.md
4. ANTIGRAVITY_NEXT_SPRINTS_V8.md
5. ANTIGRAVITY_REMAINING_V8.md
6. parallel_execution_split_v8.md
7. antigravity_paths.txt
8. codex_paths.txt

## Gemini 3 Pro (frontend/design)
1. EXTERNAL_PROMPT_GEMINI3PRO_FRONTEND_RU.md
2. gemini_design_paths.txt
3. index.html
4. OPENCLAW_DASHBOARD_PLAYBOOK_RU.md
5. $(basename "$LATEST_CONTEXT")

## Nano Banana Pro (UI)
1. EXTERNAL_PROMPT_NANOBANANA_UI_RU.md
2. nanobanana_ui_paths.txt
3. index.html
4. OPENCLAW_DASHBOARD_PLAYBOOK_RU.md
5. $(basename "$LATEST_CONTEXT")

## Не передавать
1. .env и любые ключи.
2. Полные длинные runtime-логи.
3. Архивы из _trash.
EOF

echo "✅ Feed-пакет готов: $OUT_DIR"
echo "Открой: $OUT_DIR/PACK_INSTRUCTIONS.md"
echo
read -k "_ANY?Нажми любую клавишу для закрытия..."
echo
