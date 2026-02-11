#!/bin/zsh
# ============================================================
# 🦀📊 Krab v7.0 Dashboard Launcher
# ============================================================

cd "$(dirname "$0")"
echo "🚀 Запуск Dashboard..."

source .venv/bin/activate

# Установка streamlit если его нет
pip install streamlit pandas > /dev/null 2>&1

streamlit run src/utils/dashboard_app.py
