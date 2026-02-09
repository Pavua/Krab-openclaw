#!/bin/bash
cd "$(dirname "$0")"

# If we are in 'scratch' and 'openclaw_official' exists, enter it
if [ [ -d "openclaw_official" ] ]; then
    cd openclaw_official
fi

# Log files
OC_LOG="/tmp/openclaw_sys.log"
NX_LOG="/tmp/nexus_sys.log"
DB_LOG="/tmp/nexus_dash.log"

echo "🚀 ЗАПУСК ВСЕЙ СИСТЕМЫ NEXUS (FULL START)..."
echo "📂 Рабочая папка: $(pwd)"
echo "----------------------------------------"

# 1. Kill old instances
echo "🧹 Очистка старых процессов..."
pkill -f "openclaw"
pkill -f "nexus_bridge.py"
pkill -f "streamlit"

# 2. Check dependencies
if ! python3 -c "import streamlit" &> /dev/null; then
    echo "📦 Устанавливаю Streamlit..."
    pip3 install streamlit pandas --break-system-packages &> /dev/null
fi

if ! python3 -c "import telethon" &> /dev/null; then
    echo "📦 Устанавливаю библиотеки Nexus..."
    pip3 install telethon aiohttp --break-system-packages &> /dev/null
fi

# 3. Start OpenClaw (Brain)
echo "🧠 Запускаю Мозг (OpenClaw)..."
if [ ! -f "Start OpenClaw.command" ]; then
    echo "❌ Ошибка: Не найден скрипт 'Start OpenClaw.command'!"
    echo "Вы находитесь в: $(pwd)"
    exit 1
fi

nohup ./Start\ OpenClaw.command > "$OC_LOG" 2>&1 &
OC_PID=$!
echo "   PID: $OC_PID"

# 4. Wait for Brain
echo "⏳ Жду пробуждения Мозга..."
MAX_RETRIES=30
COUNT=0
while ! nc -z localhost 18789; do
  sleep 1
  COUNT=$((COUNT+1))
  if [ $COUNT -ge $MAX_RETRIES ]; then
    echo "❌ Ошибка: Мозг не запустился. Проверьте лог: $OC_LOG"
    exit 1
  fi
  printf "."
done
echo ""
echo "✅ Мозг онлайн!"

# 5. Start Nexus Bridge (Body)
echo "👤 Запускаю Тело (Nexus Userbot)..."
nohup python3 nexus_bridge.py > "$NX_LOG" 2>&1 &
NX_PID=$!
echo "   PID: $NX_PID"

# 6. Start Dashboard (UI)
echo "🖥️  Запускаю Dashboard..."
nohup streamlit run dashboard.py --server.headless true --server.runOnSave true > "$DB_LOG" 2>&1 &
DB_PID=$!
echo "   PID: $DB_PID"

echo "----------------------------------------"
echo "✅ СИСТЕМА ПОЛНОСТЬЮ АКТИВНА!"
echo "📄 Логи: /tmp/openclaw_sys.log | /tmp/nexus_sys.log"
echo "🌐 Dashboard: http://localhost:8501"
echo ""
echo "Терминал можно закрыть."
