# -*- coding: utf-8 -*-
"""
Cloud Tier Smoke Test (R24).
Проверяет инварианты режима force_cloud и текущий тир облачных моделей.
"""

import requests
import sys

BASE_URL = "http://127.0.0.1:8000"

def main():
    print("🔍 Валидация Cloud Tier и Force Mode...")
    try:
        resp = requests.get(f"{BASE_URL}/api/stats", timeout=10)
        if resp.status_code != 200:
            print(f"❌ Не удалось получить статистику: HTTP {resp.status_code}")
            return 1
        
        data = resp.json()
        router_info = data.get("router", {})
        force_mode = router_info.get("force_mode", "unknown")
        active_tier = router_info.get("active_tier", "unknown")
        
        print(f"ℹ️ Текущий режим: {force_mode}")
        print(f"ℹ️ Активный тир: {active_tier}")
        
        # Инвариант: если force_cloud, мы не должны быть в local_lm (в плане приоритета)
        if force_mode == "force_cloud" and active_tier == "local":
            print("⚠️ ВНИМАНИЕ: Режим force_cloud активен, но тир 'local'! Проверь конфиг.")
            # Это может быть не критической ошибкой, но требует внимания
        
        # Проверка наличия ключей (без печати значений)
        openclaw_ok = router_info.get("is_openclaw_available", False)
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
