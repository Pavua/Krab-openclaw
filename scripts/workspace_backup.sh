#!/usr/bin/env bash
# Daily workspace backup — tar.gz, 30 штук rotate
# Запускается из launchd daily 03:07 (после archive.db backup в 02:07)
#
# Что бэкапим (только ценный state, без venv/browser/тяжёлых логов):
#   krab_runtime_state/ — без *.log, WAL, venv (inbox_state, swarm_*, history_cache.db и пр.)
#   krab_memory/archive.db — основная БД памяти (473MB)
#   krab_memory/whitelist.json
#   agents/main/agent/models.json — routing конфиг
#   workspace-main-messaging/*.md/*.json/*.py/*.sh/*.command + Inbox/
set -euo pipefail

BACKUP_DIR="$HOME/.openclaw/backups/workspace"
OPENCLAW_DIR="$HOME/.openclaw"
mkdir -p "$BACKUP_DIR"

TS=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/workspace_${TS}.tar.gz"
FILELIST_TMP=$(mktemp)
trap 'rm -f "$FILELIST_TMP"' EXIT

{
    # krab_runtime_state — без логов, WAL и venv
    find "$OPENCLAW_DIR/krab_runtime_state" \
        -not -name "*.log" \
        -not -name "*.db-wal" \
        -not -name "*.db-shm" \
        -not -path "*/.venv*" \
        -not -path "*/venv*" \
        2>/dev/null | sed "s|$OPENCLAW_DIR/||"

    # krab_memory: только основной archive.db и whitelist
    [[ -f "$OPENCLAW_DIR/krab_memory/archive.db" ]] && echo "krab_memory/archive.db"
    [[ -f "$OPENCLAW_DIR/krab_memory/whitelist.json" ]] && echo "krab_memory/whitelist.json"

    # models.json
    [[ -f "$OPENCLAW_DIR/agents/main/agent/models.json" ]] && echo "agents/main/agent/models.json"

    # workspace-main-messaging: только мелкие файлы верхнего уровня + Inbox
    WS="$OPENCLAW_DIR/workspace-main-messaging"
    if [[ -d "$WS" ]]; then
        find "$WS" -maxdepth 1 -type f \
            \( -name "*.md" -o -name "*.json" -o -name "*.py" -o -name "*.sh" -o -name "*.command" \) \
            2>/dev/null | sed "s|$OPENCLAW_DIR/||"
        [[ -d "$WS/Inbox" ]] && find "$WS/Inbox" 2>/dev/null | sed "s|$OPENCLAW_DIR/||"
    fi
} | sort -u > "$FILELIST_TMP"

tar czf "$FILE" -C "$OPENCLAW_DIR" --files-from="$FILELIST_TMP" 2>/dev/null || true

# Rotate: keep last 30
ls -t "$BACKUP_DIR"/workspace_*.tar.gz 2>/dev/null | tail -n +31 | xargs -I {} rm -f {}

# Report
SIZE=$(du -h "$FILE" | cut -f1)
COUNT=$(ls "$BACKUP_DIR"/workspace_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
TOTAL=$(du -sh "$BACKUP_DIR" | cut -f1)
echo "[workspace_backup] saved: $FILE ($SIZE). Backups: $COUNT/30. Total dir: $TOTAL"
