#!/usr/bin/env bash
# krab_session_diagnostic.sh — диагностика Telegram MCP session (Session 25).
#
# Что проверяет:
#   - Какие *.session файлы лежат в ~/.krab_mcp_sessions/
#   - Для каждой active session: is_bot flag, user_id, peer_count
#   - Какая session реально используется текущим MCP server (через TELEGRAM_SESSION_NAME)
#
# Когда использовать:
#   - Yung Nagato выдаёт "bot not started in DM" в DM (debug)
#   - Хочется проверить что MCP session — userbot а не bot
#   - Перед re-auth убедиться что сессия действительно сломана
#
# Read-only: НЕ трогает session-файлы, читает SQLite в mode=ro.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION_DIR="${MCP_TELEGRAM_SESSION_DIR:-$HOME/.krab_mcp_sessions}"
PYTHON_BIN="${PROJECT_ROOT}/venv/bin/python3"

echo "=== Krab Telegram MCP Session Diagnostic ==="
echo "Project: ${PROJECT_ROOT}"
echo "Session dir: ${SESSION_DIR}"
echo ""

# 1. Determine active session name
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    ACTIVE_BASE=$(grep -E '^TELEGRAM_SESSION_NAME=' "${PROJECT_ROOT}/.env" 2>/dev/null \
        | tail -n 1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" | xargs || true)
    ACTIVE_BASE="${ACTIVE_BASE:-krab}"
else
    ACTIVE_BASE="krab"
fi
echo "TELEGRAM_SESSION_NAME (default 'krab'): ${ACTIVE_BASE}"
echo "Expected active session file: ${ACTIVE_BASE}_mcp.session"
echo ""

# 2. List все *.session файлы
echo "=== All *.session files ==="
ls -la "${SESSION_DIR}"/*.session 2>/dev/null | awk '{print "  "$5"\t"$9}' || echo "  (no sessions found)"
echo ""

# 3. Inspect каждый session-файл через SQLite
echo "=== Per-session diagnostic ==="
for session_path in "${SESSION_DIR}"/*.session; do
    [[ -f "$session_path" ]] || continue
    name=$(basename "$session_path" .session)
    if [[ "$name" == *-journal ]]; then continue; fi

    echo "--- ${name} ---"
    "${PYTHON_BIN}" - "$session_path" <<'PYEOF' || echo "  (SQLite read failed — session locked?)"
import sqlite3
import sys
path = sys.argv[1]
try:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    rows = conn.execute("SELECT is_bot, user_id, dc_id, api_id FROM sessions LIMIT 1").fetchall()
    if not rows:
        print("  (sessions table empty — session not initialized)")
    else:
        is_bot, user_id, dc_id, api_id = rows[0]
        peer_count = conn.execute("SELECT COUNT(*) FROM peers").fetchone()[0]
        kind = "USERBOT" if (is_bot == 0) else ("BOT" if (is_bot == 1) else "UNKNOWN")
        print(f"  Type:       {kind} (is_bot={is_bot})")
        print(f"  user_id:    {user_id}")
        print(f"  dc_id:      {dc_id}")
        print(f"  peer_count: {peer_count}")
    conn.close()
except sqlite3.OperationalError as e:
    print(f"  ERROR: {e}")
PYEOF
done
echo ""

# 4. Active MCP server status
echo "=== MCP server runtime status ==="
launchctl list com.krab.mcp-yung-nagato 2>/dev/null \
    | grep -E '"PID"|"LastExitStatus"' \
    | sed 's/^/  /' || echo "  (not loaded)"
echo ""

# 5. Quick HTTP probe SSE endpoint
if command -v curl >/dev/null 2>&1; then
    echo "=== SSE probe (8011/sse) ==="
    timeout 3 curl -s "http://127.0.0.1:8011/sse" 2>&1 | head -c 200 || echo "  (timeout/refused)"
    echo ""
fi

echo "=== Recommendations ==="
echo "  - Если active session is_bot=0 → userbot OK, всё работает"
echo "  - Если is_bot=1 → нужна re-auth: rm session && auth_setup.py"
echo "  - Если 'bot not started in DM' от Yung Nagato при is_bot=0 →"
echo "    скорее всего галлюцинация LLM. Persona update Session 25 решает."
