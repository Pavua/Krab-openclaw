#!/bin/bash
# Запуск аудита разрешений macOS для Краба
echo "🦀 Запускаю Аудит Разрешений macOS для Краба..."

# 1. Full Disk Access (FDA)
echo "----------------------------------------"
echo "📂 Проверка Full Disk Access (FDA)..."
if sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db "SELECT client FROM access WHERE service='kTCCServiceSystemPolicyAllFiles'" &>/dev/null; then
    echo "✅ Терминал имеет доступ к базе TCC.db (Full Disk Access включен для Терминала/iTerm)."
else
    echo "❌ ОШИБКА: Нет доступа к базе TCC.db! Выдайте Full Disk Access вашему терминалу в Системных Настройках."
fi

# 2. Accessibility
echo "----------------------------------------"
echo "🤖 Проверка Accessibility (Универсальный доступ)..."
if sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db "SELECT client FROM access WHERE service='kTCCServiceAccessibility'" | grep -q 'Terminal\|iTerm'; then
    echo "✅ Терминал имеет права Accessibility. Puppeteer/нажатия клавиш должны работать."
else
    echo "⚠️ ВНИМАНИЕ: Терминал может не иметь прав Accessibility. Проверьте настройки Системы -> Конфиденциальность -> Универсальный доступ."
fi

# 3. Screen Recording
echo "----------------------------------------"
echo "🖥️ Проверка Screen Recording (Запись Экрана)..."
if sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db "SELECT client FROM access WHERE service='kTCCServiceScreenCapture'" | grep -q 'Terminal\|iTerm'; then
    echo "✅ Терминал имеет права Screen Recording. Снимки экрана будут сделаны корректно."
else
    echo "⚠️ ВНИМАНИЕ: Терминал может не иметь прав Screen Recording. Это нужно для чтения экрана Крабом."
fi

# 4. System Events / AppleScript (Automation)
echo "----------------------------------------"
echo "⚙️ Проверка Automation (System Events/AppleScript)..."
echo "Попробуем выполнить тестовый AppleScript:"
if osascript -e 'tell application "System Events" to get name of current user' &>/dev/null; then
    echo "✅ Успешно! Доступ к System Events (AppleScript) работает."
else
    echo "❌ ОШИБКА: Доступ к System Events заблокирован (Automation). Проверьте настройки Системы -> Конфиденциальность -> Автоматизация."
fi

# 5. Gatekeeper status
echo "----------------------------------------"
echo "🛡️ Проверка Gatekeeper..."
SPCTL_STATUS=$(spctl --status)
echo "Статус Gatekeeper: $SPCTL_STATUS"
if [[ "$SPCTL_STATUS" == *"assessments enabled"* ]]; then
    echo "ℹ️ Gatekeeper включен (норма). Скачанные скрипты могут требовать 'xattr -d com.apple.quarantine'."
else
    echo "⚠️ Gatekeeper выключен."
fi

echo "----------------------------------------"
echo "✅ Аудит завершен."
echo "Нажмите Enter для выхода..."
read
