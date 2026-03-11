#!/bin/bash
# Назначение: one-click controlled restart и live-проверка блока parallelism truth на учётке pablito.
# Связь с проектом: закрывает остаточный acceptance-хвост для owner UI `:8080`, который нельзя было
# честно переподтвердить из-под временной учётки USER2 из-за ownership live runtime.

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

ARTIFACTS_DIR="$DIR/artifacts/ops"
LOG_DIR="$DIR/logs"
RESTART_LOG="$LOG_DIR/live_parallelism_restart.log"
CHECK_LOG="$LOG_DIR/live_parallelism_verify.log"
ARTIFACT_PATH="$ARTIFACTS_DIR/live_parallelism_truth_latest.json"

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"

echo "🧪 Verify Live Parallelism On Pablito"
echo "📂 Root: $DIR"
echo "👤 Current user: $(whoami)"
echo

if [ "$(whoami)" != "pablito" ]; then
  echo "⚠️ Этот helper рассчитан на запуск из-под учётки pablito."
  echo "Сейчас запущен пользователь: $(whoami)"
  echo
fi

echo "1) Controlled stop..."
./new\ Stop\ Krab.command | tee "$CHECK_LOG"

echo
echo "2) Detached start..."
nohup ./new\ start_krab.command > "$RESTART_LOG" 2>&1 &
START_PID="$!"
echo "   launcher pid: $START_PID"
echo "   restart log: $RESTART_LOG"

echo
echo "3) Ждём health `:8080` и `:18789`..."
python3 - <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

health_urls = [
    "http://127.0.0.1:8080/api/health/lite",
    "http://127.0.0.1:18789/health",
]

deadline = time.time() + 120
results = {}
while time.time() < deadline:
    all_ok = True
    for url in health_urls:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            all_ok = False
            continue
        results[url] = payload
        if url.endswith("/health"):
            if not (bool(payload.get("ok")) or str(payload.get("status", "")).lower() == "live"):
                all_ok = False
        else:
            if not bool(payload.get("ok")):
                all_ok = False
    if all_ok:
        print("health_ready")
        raise SystemExit(0)
    time.sleep(1.5)

print("health_timeout")
raise SystemExit(1)
PY

echo
echo "4) Читаем `/api/model/catalog` и сохраняем artifact..."
python3 - "$ARTIFACT_PATH" <<'PY'
import json
import sys
import urllib.request
from datetime import datetime, timezone

artifact_path = sys.argv[1]
catalog_url = "http://127.0.0.1:8080/api/model/catalog"
health_url = "http://127.0.0.1:8080/api/health/lite"

with urllib.request.urlopen(catalog_url, timeout=10) as resp:
    catalog_payload = json.loads(resp.read().decode("utf-8", "replace"))

with urllib.request.urlopen(health_url, timeout=10) as resp:
    health_payload = json.loads(resp.read().decode("utf-8", "replace"))

parallelism = (
    catalog_payload.get("catalog", {}).get("parallelism_truth", {})
    if isinstance(catalog_payload, dict)
    else {}
)

artifact = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "ok": bool(parallelism),
    "health_lite": health_payload,
    "parallelism_truth": parallelism,
}

with open(artifact_path, "w", encoding="utf-8") as fh:
    json.dump(artifact, fh, ensure_ascii=False, indent=2)
    fh.write("\n")

print(json.dumps(artifact, ensure_ascii=False, indent=2))
PY

echo
echo "5) Открываем owner UI..."
open "http://127.0.0.1:8080"

echo
echo "✅ Готово."
echo "Artifact: $ARTIFACT_PATH"
echo "Restart log: $RESTART_LOG"
echo "Verify log: $CHECK_LOG"
echo
read -p "Нажми Enter, чтобы закрыть окно..."
