#!/bin/bash
# 🌊 Krab Dashboard Starter
# Позволяет запустить панель управления в один клик.

cd "$(dirname "$0")"
echo "🚀 Запуск Krab Intelligence Dashboard..."

# Активация venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Запуск Streamlit
python3 -m streamlit run src/utils/dashboard_app.py --server.port 8501 --server.address 0.0.0.0
