#!/bin/zsh
# Проверка валидности cloud-ключей (Gemini/OpenAI) через OpenClawClient.
# Зачем: быстрый one-click smoke-test без ручных curl и без вывода секретов.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source ".env"
  set +a
fi

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python3"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

echo "🔍 Проверяю cloud-ключи (google/openai) ..."
"$PYTHON_BIN" - <<'PY'
import asyncio
import json

from src.core.openclaw_client import OpenClawClient


async def main() -> None:
    client = OpenClawClient(
        base_url="http://127.0.0.1:18789",
        api_key="sk-nexus-bridge",
    )
    diag = await client.get_cloud_provider_diagnostics(["google", "openai"])

    print("")
    print("=== CLOUD KEY DIAGNOSTICS ===")
    for provider, info in (diag.get("providers") or {}).items():
        status = "OK" if info.get("ok") else "FAIL"
        source = info.get("key_source") or "missing"
        code = info.get("error_code") or "unknown"
        summary = info.get("summary") or "-"
        print(f"[{provider}] {status} | source={source} | code={code} | {summary}")

    print("")
    print("JSON:")
    print(json.dumps(diag, ensure_ascii=False, indent=2))


asyncio.run(main())
PY

echo ""
echo "✅ Проверка завершена."
