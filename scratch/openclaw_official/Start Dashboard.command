#!/bin/bash
cd "$(dirname "$0")"

# Check if streamlit is installed
if ! python3 -c "import streamlit" &> /dev/null; then
    echo "⚠️ Streamlit not found. Installing..."
    pip3 install streamlit pandas
fi

echo "🚀 ЗАПУСК DASHBOARD..."
echo "Открываю панель управления в браузере..."
streamlit run dashboard.py --server.headless true --server.runOnSave true &
