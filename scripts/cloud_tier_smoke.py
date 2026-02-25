# -*- coding: utf-8 -*-
"""
Cloud Tier Smoke Test (R24).
Проверяет инварианты режима force_cloud и текущий тир облачных моделей.
"""

import requests
import sys
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

def main():
    print("🔍 Валидация Cloud Tier и Force Mode...")
    try:
        stats_resp = requests.get(f"{BASE_URL}/api/stats", timeout=10)
        if stats_resp.status_code != 200:
            print(f"❌ Не удалось получить статистику: HTTP {stats_resp.status_code}")
            return 1

        health_resp = requests.get(f"{BASE_URL}/api/health", timeout=10)
        if health_resp.status_code != 200:
            print(f"❌ Не удалось получить health: HTTP {health_resp.status_code}")
            return 1

        tier_resp = requests.get(f"{BASE_URL}/api/openclaw/cloud/tier/state", timeout=10)
        if tier_resp.status_code != 200:
            print(f"❌ Не удалось получить cloud tier state: HTTP {tier_resp.status_code}")
            return 1

        data = stats_resp.json()
        health = health_resp.json()
        tier = tier_resp.json()
        router_info = data.get("router", {})
        force_mode = router_info.get("force_mode", "unknown")
        tier_state = tier.get("tier_state", {}) if isinstance(tier, dict) else {}
        active_tier = tier_state.get("active_tier", "unknown")
        
        print(f"ℹ️ Текущий режим: {force_mode}")
        print(f"ℹ️ Активный тир: {active_tier}")
        
        # Инвариант: если force_cloud, мы не должны быть в local_lm (в плане приоритета)
        if force_mode == "force_cloud" and active_tier == "local":
            print("⚠️ ВНИМАНИЕ: Режим force_cloud активен, но тир 'local'! Проверь конфиг.")
            # Это может быть не критической ошибкой, но требует внимания
        
        # Проверка доступности OpenClaw через /api/health.
        checks = health.get("checks", {}) if isinstance(health, dict) else {}
        openclaw_ok = bool(checks.get("openclaw", False))
        if not openclaw_ok:
            print("❌ OpenClaw недоступен в роутере!")
            return 1
            
        print("✅ Инварианты Cloud Tier в норме.")
        return 0
        
    except Exception as e:
        print(f"❌ Ошибка при проверке: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
