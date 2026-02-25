# -*- coding: utf-8 -*-
"""
Routing Smoke Test (R24).
Проверяет базовую доступность API и корректность структуры ответов.
"""

import requests
import sys
import json
import os
from pathlib import Path


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()
BASE_URL = os.getenv("KRAB_SMOKE_BASE_URL") or f"http://{os.getenv('WEB_HOST', '127.0.0.1')}:{os.getenv('WEB_PORT', '8080')}"

def check_endpoint(name, path):
    url = f"{BASE_URL}{path}"
    print(f"🔍 Проверка {name}: {url}")
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            print(f"✅ {name} OK (200)")
            return True, resp.json()
        else:
            print(f"❌ {name} Ошибка: HTTP {resp.status_code}")
            return False, None
    except Exception as e:
        print(f"❌ {name} Ошибка соединения: {e}")
        return False, None

def main():
    endpoints = [
        ("Health", "/api/health"),
        ("Stats", "/api/stats"),
        ("EcoHealth", "/api/ecosystem/health"),
    ]
    
    all_ok = True
    results = {}
    
    for name, path in endpoints:
        ok, data = check_endpoint(name, path)
        if not ok:
            all_ok = False
        results[name] = {"ok": ok, "data": data}

    if all_ok:
        print("\n✨ Все эндпоинты роутинга доступны.")
        return 0
    else:
        print("\n⚠️ Обнаружены проблемы с доступностью API.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
