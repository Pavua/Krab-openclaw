#!/bin/zsh
# Быстрая диагностика RAM-hotspots на macOS.
#
# Что делает:
# 1) Показывает top процессов по RSS.
# 2) Отдельно подсвечивает pyrefly/language_server процессы.
# 3) Опционально гасит pyrefly (если передан флаг --kill-pyrefly).

set -euo pipefail

ACTION="${1:-report}"

echo "🧠 Memory hotspots (RSS, MB):"
ps -ax -o pid,ppid,user,rss,command \
  | awk 'NR==1 {print $0; next} {printf "%-7s %-7s %-10s %-8.1f %s\n",$1,$2,$3,$4/1024,$5}' \
  | sort -k4 -nr \
  | head -n 20

echo
echo "🔎 LSP/pyrefly-related:"
ps -ax -o pid,ppid,user,rss,command \
  | rg -i "pyrefly|language_server|antigravity.*helper" || true

if [[ "$ACTION" == "--kill-pyrefly" ]]; then
  echo
  echo "🛑 Завершаю pyrefly процессы..."
  pkill -f "pyrefly" || true
  sleep 1
  echo "✅ Готово. Повтори './scripts/memory_hotspots.command' для проверки."
fi
