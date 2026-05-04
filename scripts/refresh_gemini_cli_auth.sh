#!/bin/bash
# Wave 19-E: обновление OAuth токена google-gemini-cli (истёк ~2026-04-13).
#
# Стратегия:
#   1. Проверить текущий expiry в auth-profiles.json
#   2. Если токен ещё действителен (>1 дня до истечения) — выйти с 0
#   3. Иначе показать опции обновления:
#      a) openclaw infer model auth refresh google-gemini-cli (предпочтительно)
#      b) gemini auth login (интерактивно, открывает браузер)
#      c) openclaw agents add google-gemini-cli (полная переустановка)
#   4. После обновления — проверить через тестовый вызов openclaw infer
#   5. Вывести подсказку для восстановления fallback chain Krab

set -euo pipefail

# --- константы ---
AUTH_FILE="${KRAB_AUTH_FILE:-$HOME/.openclaw/agents/main/agent/auth-profiles.json}"
PROFILE_KEY="google-gemini-cli:pavelr7@gmail.com"
VERIFY_MODEL="${KRAB_GEMINI_VERIFY_MODEL:-google-gemini-cli/gemini-2.5-flash}"
# Минимальный запас действия токена (в секундах) — по умолчанию 1 день
MIN_TTL_SECONDS="${KRAB_GEMINI_MIN_TTL:-86400}"

echo "Wave 19-E: проверка OAuth токена google-gemini-cli"
echo "---------------------------------------------------"
echo ""

# --- Проверяем наличие auth-файла ---
if [ ! -f "$AUTH_FILE" ]; then
    echo "ОШИБКА: файл $AUTH_FILE не найден." >&2
    echo "Убедись, что OpenClaw установлен и хотя бы один раз запущен." >&2
    exit 1
fi

# --- Парсим expiry через Python (jq может не быть) ---
EXPIRY_MS=$(python3 - "$AUTH_FILE" "$PROFILE_KEY" <<'PYEOF'
import json
import sys

auth_file = sys.argv[1]
profile_key = sys.argv[2]

try:
    with open(auth_file) as f:
        data = json.load(f)
    # Поддерживаем оба возможных формата хранения профилей
    profiles = data.get("profiles", {})
    profile = profiles.get(profile_key, {})
    expires = profile.get("expires", 0)
    print(int(expires))
except Exception as e:
    print(f"PARSE_ERROR: {e}", file=sys.stderr)
    print(0)
PYEOF
)

# --- Вычисляем TTL ---
NOW_S=$(date +%s)
EXPIRY_S=$(( EXPIRY_MS / 1000 ))
TTL_S=$(( EXPIRY_S - NOW_S ))

if [ "$EXPIRY_MS" -eq 0 ]; then
    # Профиль не найден или expiry отсутствует
    echo "ПРЕДУПРЕЖДЕНИЕ: профиль '$PROFILE_KEY' не найден в auth-profiles.json"
    echo "Необходима полная авторизация."
    echo ""
    NEEDS_REFRESH=1
elif [ "$TTL_S" -gt "$MIN_TTL_SECONDS" ]; then
    # Токен действителен
    DAYS_LEFT=$(( TTL_S / 86400 ))
    EXPIRE_DATE=$(date -r "$EXPIRY_S" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
                  || date -d "@$EXPIRY_S" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
                  || echo "неизвестно")
    echo "Токен действителен ещё $DAYS_LEFT дн. (истекает $EXPIRE_DATE)"
    echo "Обновление не требуется."
    exit 0
else
    # Токен истёк или истекает скоро
    if [ "$TTL_S" -lt 0 ]; then
        DAYS_AGO=$(( (-TTL_S) / 86400 ))
        EXPIRE_DATE=$(date -r "$EXPIRY_S" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
                      || date -d "@$EXPIRY_S" "+%Y-%m-%d %H:%M:%S" 2>/dev/null \
                      || echo "неизвестно")
        echo "ОШИБКА: токен истёк $DAYS_AGO дн. назад ($EXPIRE_DATE)"
    else
        HOURS_LEFT=$(( TTL_S / 3600 ))
        echo "ПРЕДУПРЕЖДЕНИЕ: токен истекает через $HOURS_LEFT ч. — рекомендуется обновить"
    fi
    echo ""
    NEEDS_REFRESH=1
fi

# --- Показываем опции обновления ---
if [ "${NEEDS_REFRESH:-0}" -eq 1 ]; then
    echo "Опции обновления:"
    echo ""
    echo "  [A] openclaw infer model auth refresh google-gemini-cli"
    echo "      ПРЕДПОЧТИТЕЛЬНО — обновляет токен через OpenClaw CLI"
    echo ""
    echo "  [B] gemini auth login"
    echo "      Интерактивно — открывает браузер для OAuth"
    echo ""
    echo "  [C] openclaw agents add google-gemini-cli"
    echo "      Полная переустановка агента (если [A] не работает)"
    echo ""

    # Интерактивный режим — пропускаем если нет tty (CI/тесты)
    if [ -t 0 ] && [ "${KRAB_NONINTERACTIVE:-0}" != "1" ]; then
        read -r -p "Запустить [A] автоматически? (y/n): " ANSWER
        if [[ "$ANSWER" =~ ^[Yy]$ ]]; then
            echo ""
            echo "Запускаем: openclaw infer model auth refresh google-gemini-cli ..."
            if openclaw infer model auth refresh google-gemini-cli 2>&1; then
                echo "OpenClaw refresh: OK"
            else
                echo "OpenClaw refresh завершился с ошибкой, пробуем openclaw agents add ..."
                openclaw agents add google-gemini-cli
            fi

            echo ""
            echo "Проверяем токен после обновления ..."
            _check_after_refresh
        else
            echo "Пропускаем автоматическое обновление."
            echo "Выполни одну из опций выше вручную, затем перезапусти Krab."
        fi
    else
        # Неинтерактивный режим (CI / --no-tty)
        echo "Неинтерактивный режим: автоматический refresh пропущен."
        echo "Выполни вручную: openclaw infer model auth refresh google-gemini-cli"
    fi
fi

# --- Функция верификации (вызывается после refresh) ---
_check_after_refresh() {
    echo ""
    echo "Тестовый запрос к $VERIFY_MODEL ..."
    if openclaw infer model run \
            --model "$VERIFY_MODEL" \
            --prompt "ping" \
            --json 2>&1 | tail -10; then
        echo ""
        echo "Токен успешно обновлён и проверен!"
    else
        echo ""
        echo "ПРЕДУПРЕЖДЕНИЕ: тестовый запрос завершился ошибкой."
        echo "Проверь логи openclaw или попробуй опцию [B]: gemini auth login"
        exit 2
    fi

    echo ""
    echo "Следующие шаги:"
    echo "  1. Убедись, что Krab запущен: ps aux | grep userbot_bridge"
    echo "  2. Проверь health panel: curl http://127.0.0.1:8080/api/health/lite | python3 -m json.tool"
    echo "  3. Отправь тестовое сообщение в Telegram — fallback chain #2 (google-gemini-cli) должен работать"
}

exit 0
