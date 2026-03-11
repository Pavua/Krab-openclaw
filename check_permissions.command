#!/bin/zsh

# Скрипт для глубокой проверки и исправления прав доступа macOS (Screen Recording / Accessibility)
# Используется для Krab AI, когда "ползунки включены, но не работают".

echo "🔍 Проверка прав доступа для компонентов Krab..."

# Список бандлов, которым могут быть нужны права
BUNDLES=("com.krabear.agent" "ai.krab.core" "fun.krab.telegram" "com.krab.telegram")

check_tcc() {
    local service=$1
    echo "\n--- Сервис: $service ---"
    for bundle in "${BUNDLES[@]}"; do
        if sqlite3 /Library/Application\ Support/com.apple.TCC/TCC.db "SELECT client FROM access WHERE service='$service' AND client='$bundle';" 2>/dev/null | grep -q "$bundle"; then
            echo "✅ [DB] $bundle имеет доступ к $service"
        else
            echo "❌ [DB] $bundle ОТСУТСТВУЕТ в БД для $service"
        fi
    done
}

echo "1. Проверка базы данных TCC (требует Full Disk Access для Терминала или sudo)"
# check_tcc "kTCCServiceScreenCapture"
# check_tcc "kTCCServiceAccessibility"

echo "\n2. Попытка принудительного сброса и перезапуска прав (иногда помогает, если системные настройки 'глючат')"
echo "Если бот всё еще не видит экран, выполни в Терминале:"
echo "sudo tccutil reset ScreenCapture com.krabear.agent"
echo "sudo tccutil reset Accessibility com.krabear.agent"

echo "\n3. Перезапуск графических агентов..."
launchctl kickstart -k gui/$(id -u)/com.krabear.agent 2>/dev/null

echo "\n✅ Диагностика завершена. Если проблема осталась, проверь, что 'Antigravity.app' или 'Cursor.app' (в зависимости от того, кто хостит агента) включены в 'Запись экрана' в Системных настройках."
