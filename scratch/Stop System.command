#!/bin/bash
echo "🛑 ОСТАНОВКА ВСЕЙ СИСТЕМЫ NEXUS..."

# Kill Node.js (OpenClaw)
pkill -f "openclaw"
echo "🧠 Мозг отключен."

# Kill Python (Nexus Bridge)
pkill -f "nexus_bridge.py"
echo "👤 Тело отключено."

# Kill Streamlit (Dashboard)
pkill -f "streamlit"
echo "🖥️  Dashboard отключен."

echo "✅ (Все процессы завершены)"
