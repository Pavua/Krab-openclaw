#!/bin/bash
# =============================================================================
# Валидация совместимости web-прототипа с базовым UI-каркасом.
# Зачем: вернуть one-click проверку из старого frontend-репорта R13.
# Связь: сравнивает наличие критичных ID между index.html и redesign-прототипом.
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_FILE="src/web/index.html"
PROTO_FILE="src/web/prototypes/nano/index_redesign.html"

if [[ ! -f "$BASE_FILE" ]]; then
  echo "❌ Не найден базовый файл: $BASE_FILE"
  exit 2
fi

if [[ ! -f "$PROTO_FILE" ]]; then
  echo "❌ Не найден прототип: $PROTO_FILE"
  exit 2
fi

python3 - "$BASE_FILE" "$PROTO_FILE" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

base = Path(sys.argv[1])
proto = Path(sys.argv[2])

required_ids = {
    "fileProtocolWarning",
    "opsAlerts",
    "opsAlertSearch",
    "opsHistory",
    "assistantOutput",
    "assistantMeta",
    "ocMeta",
}

id_pattern = re.compile(r'id\s*=\s*["\']([^"\']+)["\']')

base_ids = set(id_pattern.findall(base.read_text(encoding="utf-8", errors="ignore")))
proto_ids = set(id_pattern.findall(proto.read_text(encoding="utf-8", errors="ignore")))

missing = sorted(i for i in required_ids if i not in proto_ids)
unknown = sorted(i for i in required_ids if i not in base_ids)

print("🧪 validate_web_prototype_compat")
print(f"- base: {base}")
print(f"- proto: {proto}")
print(f"- required ids: {len(required_ids)}")

if unknown:
    print("⚠️ Внимание: часть ID отсутствует даже в базовом файле:")
    for item in unknown:
        print(f"  - {item}")

if missing:
    print(f"❌ Прототип НЕ совместим (missing ids: {len(missing)}):")
    for item in missing:
        print(f"  - {item}")
    raise SystemExit(1)

print("✅ Прототип совместим для интеграции (missing ids: 0).")
PY
