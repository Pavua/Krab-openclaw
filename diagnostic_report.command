#!/bin/zsh
# -*- coding: utf-8 -*-

# Diagnostic Report Tool for Krab Ecosystem [R11]
# Позволяет быстро проверить здоровье, ресурсы и бюджет из терминала.

echo "🔍 Запрос диагностических данных... (Host: ${WEB_HOST:-localhost})"

# Получаем данные из API
DIAG_JSON=$(curl -s "http://localhost:18790/api/system/diagnostics")

if [[ -z "$DIAG_JSON" ]]; then
    echo "❌ Ошибка: Не удалось получить данные. Убедитесь, что сервер Krab запущен."
    exit 1
fi

# Простая визуализация через python (для красоты без jq)
python3 - <<EOF
import json, sys
data = json.loads('''$DIAG_JSON''')

if not data.get("ok"):
    print(f"❌ API Error: {data.get('error')}")
    sys.exit(1)

res = data.get("resources", {})
budget = data.get("budget", {})
local = data.get("local_ai", {})

print("\n" + "="*50)
print("🛡️  KRAB SYSTEM DIAGNOSTICS [R11]")
print("="*50)

print(f"\n📊 РЕСУРСЫ (macOS):")
print(f"   CPU:  {res.get('cpu_percent', 'N/A')}%")
print(f"   RAM:  {res.get('ram_percent', 'N/A')}% (Доступно: {res.get('ram_available_gb', 'N/A')} GB)")

print(f"\n🧠 ЛОКАЛЬНЫЙ AI:")
print(f"   Движок: {local.get('engine', 'none')}")
print(f"   Модель: {local.get('model', 'none')}")
print(f"   Статус: {'READY' if local.get('available') else 'OFFLINE'}")

print(f"\n💰 КОНТРОЛЬ ЗАТРАТ (Gemini):")
print(f"   Потрачено: ${budget.get('monthly_spent', 0)} / \${budget.get('monthly_budget', 0)}")
print(f"   Лимит:     {budget.get('usage_percent', 0)}%")
print(f"   Регламент: {'🔴 РЕЖИМ ЭКОНОМИИ' if budget.get('is_economy_mode') else '🟢 НОРМА'}")
print(f"   Прогноз:   {budget.get('runway_days', 0)} дней работы")

print("\n" + "="*50)
EOF
