#!/bin/bash
# Запуск тестов зоны ответственности Antigravity
cd "$(dirname "$0")/.."

echo "🚀 Running Antigravity Tests..."
source .venv/bin/activate
pytest tests/test_telegram_control.py \
       tests/test_telegram_summary_service.py \
       tests/test_telegram_chat_resolver.py \
       tests/test_group_moderation_v2.py \
       tests/test_group_moderation_scenarios.py \
       tests/test_voice_gateway_hardening.py \
       tests/test_provisioning_hardening.py \
       tests/test_provisioning_service.py \
       tests/test_voice_gateway_client.py

echo "✅ Done."
read -p "Press Enter to close..."
