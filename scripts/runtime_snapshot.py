# -*- coding: utf-8 -*-
"""
Runtime Snapshot Utility (R24).
Собирает полный срез состояния системы для отчета.
"""

import requests
import sys
import json
import os
from pathlib import Path
from datetime import datetime


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
SNAPSHOT_FILE = "temp/runtime_snapshot.json"

def main():
    print("📸 Сбор снимка рантайма...")
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "endpoints": {}
    }
    
    targets = [
        ("/api/health", "health"),
        ("/api/stats", "stats"),
        ("/api/system/diagnostics", "diagnostics"),
        ("/api/ecosystem/health", "ecosystem")
    ]
    
    os.makedirs("temp", exist_ok=True)
    
    for path, key in targets:
        try:
            resp = requests.get(f"{BASE_URL}{path}", timeout=15)
            if resp.status_code == 200:
                snapshot["endpoints"][key] = resp.json()
            else:
                snapshot["endpoints"][key] = {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            snapshot["endpoints"][key] = {"error": str(e)}
            
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Снимок сохранен в {SNAPSHOT_FILE}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
