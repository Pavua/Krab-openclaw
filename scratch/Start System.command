#!/bin/bash
cd "$(dirname "$0")"

# 1. Smart Directory Switching
# If we are in 'scratch' (parent), we need to go into 'openclaw_official'
if [ -d "openclaw_official" ]; then
    echo "📂 Перехожу в папку openclaw_official..."
    cd openclaw_official
fi

# Log files
OC_LOG="/tmp/openclaw_sys.log"
NX_LOG="/tmp/nexus_sys.log"
DB_LOG="/tmp/nexus_dash.log"

echo "🚀 ЗАПУСК ВСЕЙ СИСТЕМЫ NEXUS (FULL START)..."
echo "📂 Рабочая папка: $(pwd)"
echo "----------------------------------------"

# 2. Check for critical script
if [ ! -f "Start OpenClaw.command" ]; then
    echo "❌ КРИТИЧЕСКАЯ ОШИБКА: Не могу найти 'Start OpenClaw.command'"
    echo "   Я ищу в папке: $(pwd)"
    echo "   Убедитесь, что папка 'openclaw_official' лежит рядом с этим скриптом."
    exit 1
fi

# 3. Kill old instances
echo "🧹 Очистка старых процессов..."
pkill -f "openclaw"
pkill -f "nexus_bridge.py"
pkill -f "streamlit"

# 4. Check dependencies
if ! python3 -c "import streamlit" &> /dev/null; then
    echo "📦 Устанавливаю Streamlit..."
    pip3 install streamlit pandas &> /dev/null
fi

# 5. Start OpenClaw (Brain)
echo "🧠 Запускаю Мозг (OpenClaw)..."
nohup ./Start\ OpenClaw.command > "$OC_LOG" 2>&1 &
OC_PID=$!
echo "   PID: $OC_PID"

# 6. Wait for Brain
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

# 7. Start Nexus Bridge (Body)
echo "👤 Запускаю Тело (Nexus Userbot)..."
nohup python3 nexus_bridge.py > "$NX_LOG" 2>&1 &
NX_PID=$!
echo "   PID: $NX_PID"

# 8. Start Dashboard (UI)
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
