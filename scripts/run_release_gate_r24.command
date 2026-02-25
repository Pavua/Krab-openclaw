#!/bin/zsh
# Krab: Run Release Gate R24
# Однокликовый запуск всех проверок с генерацией отчета.
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || source .venv_krab/bin/activate 2>/dev/null
python3 scripts/r24_orchestrator.py
echo "\n✅ Проверки завершены. Отчет: output/reports/R24_SMOKE_REPORT.md"
echo "Нажми любую клавишу для выхода..."
read -k 1
