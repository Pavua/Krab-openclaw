#!/usr/bin/env bash
# Memory Doctor — diagnose + repair Krab Memory Layer (Krab suggestion 2026-04-21)
#
# Проверки:
#  1. archive.db — наличие и размер
#  2. Целостность (PRAGMA integrity_check)
#  3. messages / chats / chunks — количества
#  4. Encoded ratio (embedded / total chunks)
#  5. Indexer queue depth через panel API (:8080)
#  6. MCP memory_search reachability (:8011)
#  7. Топ чатов по объёму
#
# Ремонт (--fix):
#  - WAL checkpoint (PRAGMA wal_checkpoint(TRUNCATE))
#  - encode_memory_phase2.py если encoded ratio < 50%
#  - Перезапуск MCP yung-nagato если транспорт завис
#
# Usage:
#   ./scripts/memory_doctor.command
#   ./scripts/memory_doctor.command --fix

set -euo pipefail
cd "$(dirname "$0")/.."

DB="${HOME}/.openclaw/krab_memory/archive.db"
PANEL="http://127.0.0.1:8080"
MCP_PORT=8011
FIX=0

for arg in "$@"; do
    [ "$arg" = "--fix" ] && FIX=1
done

# ── Цвета ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
YEL='\033[0;33m'
GRN='\033[0;32m'
BLU='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GRN}[OK]${NC}  $*"; }
warn() { echo -e "  ${YEL}[WARN]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; }
info() { echo -e "  ${BLU}[INFO]${NC} $*"; }

echo -e "\n${BOLD}=== Memory Doctor ===  $(date '+%Y-%m-%d %H:%M:%S')${NC}"
[ "$FIX" -eq 1 ] && echo -e "  ${YEL}Режим --fix включён: будут выполнены авто-ремонтные действия${NC}"
echo

ISSUES=0

# ── Проверка 1: Наличие и размер archive.db ──────────────────────────────────
echo -e "${BOLD}[1] archive.db — наличие и размер${NC}"
if [ ! -f "$DB" ]; then
    fail "archive.db не найден: $DB"
    ISSUES=$((ISSUES + 1))
    echo -e "\n${RED}Критическая ошибка: Memory Layer отсутствует. Дальнейшие проверки невозможны.${NC}"
    exit 1
fi

SIZE_BYTES=$(stat -f%z "$DB" 2>/dev/null || stat -c%s "$DB" 2>/dev/null || echo 0)
SIZE_MB=$(echo "scale=1; $SIZE_BYTES / 1048576" | bc)
info "Путь: $DB"
info "Размер: ${SIZE_MB} МБ (${SIZE_BYTES} байт)"

if [ "$SIZE_BYTES" -lt 1024 ]; then
    fail "archive.db слишком маленький (${SIZE_BYTES} байт) — вероятно пустой"
    ISSUES=$((ISSUES + 1))
elif [ "$SIZE_BYTES" -gt 2147483648 ]; then  # > 2 ГБ
    warn "archive.db > 2 ГБ — рекомендуется VACUUM или партиционирование"
    ISSUES=$((ISSUES + 1))
else
    ok "Размер в норме (${SIZE_MB} МБ)"
fi

# ── Проверка 2: Целостность SQLite ───────────────────────────────────────────
echo -e "\n${BOLD}[2] Целостность SQLite (integrity_check)${NC}"
INTEGRITY=$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>&1 | head -5)
if echo "$INTEGRITY" | grep -qi "^ok$"; then
    ok "integrity_check: OK"
else
    fail "integrity_check вернул проблемы: $INTEGRITY"
    ISSUES=$((ISSUES + 1))
fi

# WAL checkpoint если --fix
if [ "$FIX" -eq 1 ]; then
    info "Выполняем WAL checkpoint (TRUNCATE)..."
    WAL_RESULT=$(sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>&1 || true)
    ok "WAL checkpoint выполнен: $WAL_RESULT"
fi

# ── Проверка 3: Счётчики messages / chats / chunks ──────────────────────────
echo -e "\n${BOLD}[3] Счётчики: messages / chats / chunks${NC}"

TOTAL_MSGS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM messages;" 2>/dev/null || echo "0")
TOTAL_CHATS=$(sqlite3 "$DB" "SELECT COUNT(DISTINCT chat_id) FROM messages;" 2>/dev/null || echo "0")

# Поддержка обеих схем: chunks (v2) и memory_chunks (legacy)
TOTAL_CHUNKS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM chunks;" 2>/dev/null || echo "0")
if [ "$TOTAL_CHUNKS" = "0" ]; then
    TOTAL_CHUNKS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memory_chunks;" 2>/dev/null || echo "0")
fi

info "Сообщений: $TOTAL_MSGS"
info "Уникальных чатов: $TOTAL_CHATS"
info "Chunks (индексируемые фрагменты): $TOTAL_CHUNKS"

if [ "$TOTAL_MSGS" -eq 0 ]; then
    warn "Нет сообщений в archive.db — Memory Layer пуст"
    ISSUES=$((ISSUES + 1))
elif [ "$TOTAL_MSGS" -gt 0 ]; then
    ok "Сообщений: $TOTAL_MSGS, чатов: $TOTAL_CHATS"
fi

if [ "$TOTAL_CHUNKS" -eq 0 ] && [ "$TOTAL_MSGS" -gt 0 ]; then
    warn "Chunks отсутствуют при наличии сообщений — chunking не выполнялся"
    ISSUES=$((ISSUES + 1))
fi

# ── Проверка 4: Encoded ratio ────────────────────────────────────────────────
echo -e "\n${BOLD}[4] Encoded ratio (embedded / total chunks)${NC}"

ENCODED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM vec_chunks_rowids;" 2>/dev/null || echo "0")
if [ "$ENCODED" = "0" ]; then
    # Legacy fallback
    ENCODED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memory_chunks WHERE embedding IS NOT NULL;" 2>/dev/null || echo "0")
fi

info "Encoded chunks: $ENCODED / $TOTAL_CHUNKS"

if [ "$TOTAL_CHUNKS" -gt 0 ]; then
    RATIO=$(echo "scale=1; 100 * $ENCODED / $TOTAL_CHUNKS" | bc)
    info "Encoded ratio: ${RATIO}%"
    RATIO_INT=$(echo "$RATIO" | cut -d. -f1)
    if [ "$RATIO_INT" -lt 50 ]; then
        fail "Encoded ratio ${RATIO}% < 50% — семантический поиск деградирован"
        ISSUES=$((ISSUES + 1))
        if [ "$FIX" -eq 1 ]; then
            info "Запускаем encode_memory_phase2.py для backfill..."
            if [ -f "scripts/encode_memory_phase2.py" ]; then
                PYTHON="venv/bin/python"
                [ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
                "$PYTHON" scripts/encode_memory_phase2.py --limit 5000 && \
                    ok "Backfill запущен (limit 5000 chunks)" || \
                    fail "Backfill завершился с ошибкой"
            else
                warn "scripts/encode_memory_phase2.py не найден — пропускаем backfill"
            fi
        else
            warn "Запустите с --fix или: venv/bin/python scripts/encode_memory_phase2.py"
        fi
    elif [ "$RATIO_INT" -lt 80 ]; then
        warn "Encoded ratio ${RATIO}% — ниже рекомендуемых 80%"
        ISSUES=$((ISSUES + 1))
    else
        ok "Encoded ratio ${RATIO}% — в норме"
    fi
else
    info "Chunks отсутствуют — encoded ratio не вычисляется"
fi

# ── Проверка 5: Indexer queue depth (panel API) ──────────────────────────────
echo -e "\n${BOLD}[5] Indexer queue depth (panel API ${PANEL})${NC}"

INDEXER_RESP=$(curl -sf --max-time 3 "${PANEL}/api/memory/indexer" 2>/dev/null || echo "")
if [ -z "$INDEXER_RESP" ]; then
    warn "Panel API недоступен (${PANEL}) — пропускаем проверку indexer"
else
    QUEUE_SIZE=$(echo "$INDEXER_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('queue_size', 'N/A'))" 2>/dev/null || echo "N/A")
    IS_RUNNING=$(echo "$INDEXER_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('is_running', False))" 2>/dev/null || echo "False")
    PROCESSED=$(echo "$INDEXER_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('processed_total', 0))" 2>/dev/null || echo "0")
    info "is_running: $IS_RUNNING | queue_size: $QUEUE_SIZE | processed_total: $PROCESSED"
    if [ "$IS_RUNNING" = "True" ]; then
        ok "Indexer запущен и работает"
    else
        warn "Indexer не запущен (is_running=False)"
        ISSUES=$((ISSUES + 1))
    fi
    if [ "$QUEUE_SIZE" != "N/A" ] && [ "$QUEUE_SIZE" -gt 1000 ] 2>/dev/null; then
        warn "Очередь indexer'а > 1000 элементов ($QUEUE_SIZE) — возможна задержка"
        ISSUES=$((ISSUES + 1))
    fi
fi

# ── Проверка 6: MCP memory_search reachability ───────────────────────────────
echo -e "\n${BOLD}[6] MCP memory_search reachability (:${MCP_PORT})${NC}"

MCP_RESP=$(curl -sf --max-time 3 "http://127.0.0.1:${MCP_PORT}/sse" \
    -H "Accept: text/event-stream" 2>/dev/null | head -c 100 || echo "")
if echo "$MCP_RESP" | grep -q "event\|data\|ping\|:"; then
    ok "MCP yung-nagato (:${MCP_PORT}) отвечает на SSE"
else
    # Попробуем просто TCP-коннект
    if nc -z 127.0.0.1 "$MCP_PORT" 2>/dev/null; then
        ok "MCP yung-nagato (:${MCP_PORT}) TCP доступен"
    else
        fail "MCP yung-nagato (:${MCP_PORT}) недоступен"
        ISSUES=$((ISSUES + 1))
        if [ "$FIX" -eq 1 ]; then
            info "Перезапускаем LaunchAgent com.krab.mcp-yung-nagato..."
            launchctl kickstart -k "gui/$(id -u)/com.krab.mcp-yung-nagato" 2>/dev/null && \
                ok "LaunchAgent перезапущен" || \
                fail "Не удалось перезапустить — проверьте: launchctl list | grep krab"
        else
            warn "Для перезапуска: launchctl kickstart -k gui/\$(id -u)/com.krab.mcp-yung-nagato"
        fi
    fi
fi

# ── Проверка 7: Топ чатов по объёму ─────────────────────────────────────────
echo -e "\n${BOLD}[7] Топ чатов по объёму${NC}"
TOP_CHATS=$(sqlite3 "$DB" \
    "SELECT chat_id, COUNT(*) AS cnt FROM messages GROUP BY chat_id ORDER BY cnt DESC LIMIT 5;" \
    2>/dev/null || echo "")
if [ -n "$TOP_CHATS" ]; then
    ok "Топ-5 чатов:"
    echo "$TOP_CHATS" | while IFS='|' read -r cid cnt; do
        echo -e "       chat_id=${BLU}${cid}${NC}  messages=${cnt}"
    done
else
    warn "Не удалось получить список топ чатов (таблица messages пуста или ошибка)"
fi

# ── Итог ─────────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}=== Итог ===${NC}"
if [ "$ISSUES" -eq 0 ]; then
    echo -e "  ${GRN}${BOLD}Memory Layer здоров — проблем не обнаружено.${NC}"
else
    echo -e "  ${YEL}${BOLD}Обнаружено проблем: ${ISSUES}${NC}"
    if [ "$FIX" -eq 0 ]; then
        echo -e "  Запустите ${BLU}./scripts/memory_doctor.command --fix${NC} для авто-ремонта."
    fi
fi
echo

read -rp "Нажмите Enter, чтобы закрыть..." || true
exit $( [ "$ISSUES" -eq 0 ] && echo 0 || echo 1 )
