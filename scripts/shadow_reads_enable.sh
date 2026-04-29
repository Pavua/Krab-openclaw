#!/bin/bash
# Включает shadow-reads + рестартит Krab + проверяет что logs показывают shadow events
set -euo pipefail
ENV_FILE="$HOME/Antigravity_AGENTS/Краб/.env"

# Ensure env
grep -q "^KRAB_RAG_PHASE2_SHADOW=" "$ENV_FILE" || echo "KRAB_RAG_PHASE2_SHADOW=1" >> "$ENV_FILE"

# Restart Krab
"$HOME/Antigravity_AGENTS/new Stop Krab.command" >/dev/null 2>&1
sleep 5
"$HOME/Antigravity_AGENTS/new start_krab.command" >/dev/null 2>&1 &

# Wait up
timeout 60 bash -c 'until curl -sf http://127.0.0.1:8080/api/health/lite | grep -q running; do sleep 3; done'

# Trigger several memory queries via API (force retrieval path)
for q in "привет" "как дела" "погода"; do
  curl -sS "http://127.0.0.1:8080/api/memory/search?q=$q" >/dev/null 2>&1 || true
done
sleep 5

# Check shadow events in log
count=$(grep -c "memory_phase2_shadow_compare" "$HOME/Antigravity_AGENTS/Краб/logs/krab_launchd.out.log" 2>/dev/null || echo 0)
echo "Shadow events in log: $count"
if [ "$count" -gt 0 ]; then
  echo "✅ Shadow-reads активен"
else
  echo "⚠️ Shadow events пока нет (может медленный first query — проверь через 2 мин)"
fi
