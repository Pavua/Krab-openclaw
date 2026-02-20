#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Link (one-click) для OpenClaw через secondary device
# -----------------------------------------------------------------------------
# Что делает:
# 1) Запускает signal-cli link и печатает sgnl:// ссылку для линковки.
# 1.1) Автоматически копирует sgnl:// ссылку в буфер обмена (macOS), если найдена.
# 1.2) Пытается сгенерировать QR PNG для быстрой линковки (если доступен qrencode
#      или python-модуль qrcode).
# 2) Ждёт подтверждение линковки с телефона (Signal -> Linked devices).
# 3) После успеха показывает список account'ов и следующий шаг для daemon.
#
# Зачем:
# Это обходной путь, когда register/captcha упирается в 429 Rate Limited.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source ./.env
  set +a
fi

if ! command -v signal-cli >/dev/null 2>&1; then
  echo "❌ signal-cli не найден. Установи: brew install signal-cli"
  exit 1
fi

DEVICE_NAME="${OPENCLAW_SIGNAL_DEVICE_NAME:-Krab OpenClaw}"
ARTIFACTS_DIR="${ROOT_DIR}/artifacts/signal"
mkdir -p "$ARTIFACTS_DIR"
LINK_LOG_DIR="${ARTIFACTS_DIR}/link_logs"
mkdir -p "$LINK_LOG_DIR"
MAX_ATTEMPTS="${1:-3}"

if [[ ! "$MAX_ATTEMPTS" =~ '^[0-9]+$' ]]; then
  echo "❌ Аргумент attempts должен быть числом. Пример: ./openclaw_signal_link.command 3"
  exit 1
fi
if [[ "$MAX_ATTEMPTS" -lt 1 ]]; then
  MAX_ATTEMPTS=1
fi

generate_qr_png() {
  local link="$1"
  local stamp
  stamp="$(date '+%Y%m%d_%H%M%S')"
  local qr_path="$ARTIFACTS_DIR/signal_link_qr_${stamp}.png"

  if command -v qrencode >/dev/null 2>&1; then
    if qrencode -o "$qr_path" "$link" >/dev/null 2>&1; then
      echo "$qr_path"
      return 0
    fi
  fi

  if command -v python3 >/dev/null 2>&1; then
    if SIGNAL_LINK="$link" QR_PATH="$qr_path" python3 - <<'PY' >/dev/null 2>&1
import os
import sys

link = os.environ.get("SIGNAL_LINK", "").strip()
qr_path = os.environ.get("QR_PATH", "").strip()

if not link or not qr_path:
    raise SystemExit(2)

try:
    import qrcode
except Exception:
    raise SystemExit(3)

img = qrcode.make(link)
img.save(qr_path)
PY
    then
      echo "$qr_path"
      return 0
    fi
  fi

  return 1
}

run_link_attempt() {
  local attempt="$1"
  local link_log
  local link_value_file
  local stamp

  stamp="$(date '+%Y%m%d_%H%M%S')"
  link_log="${LINK_LOG_DIR}/signal_link_attempt_${stamp}_a${attempt}.log"
  link_value_file="$(mktemp -t signal-link-value.XXXXXX.txt)"
  LAST_LINK_VALUE=""
  LAST_LINK_LOG_PATH="$link_log"

  echo
  echo "=== Попытка ${attempt}/${MAX_ATTEMPTS} ==="
  echo "Готовь телефон на экране: Signal -> Linked devices -> Link New Device."
  read -r "?Нажми Enter и сразу сканируй QR (таймаут ~45-60 секунд)..."

  set +e
  signal-cli link -n "$DEVICE_NAME" 2>&1 | tee "$link_log" | while IFS= read -r line; do
    if [[ "$line" == sgnl://* ]]; then
      echo "$line" > "$link_value_file"
      if command -v pbcopy >/dev/null 2>&1; then
        printf "%s" "$line" | pbcopy
        echo "✅ sgnl:// ссылка скопирована в буфер обмена."
      fi
      if qr_path="$(generate_qr_png "$line")"; then
        echo "✅ QR для линковки сохранен: $qr_path"
        if command -v open >/dev/null 2>&1; then
          open "$qr_path" >/dev/null 2>&1 || true
        fi
      else
        echo "ℹ️ QR не сгенерирован (нет qrencode/qrcode)."
      fi
    fi
  done
  local link_exit="${pipestatus[1]}"
  set -e

  if [[ -f "$link_value_file" ]]; then
    LAST_LINK_VALUE="$(cat "$link_value_file" 2>/dev/null || true)"
  fi
  rm -f "$link_value_file"

  return "$link_exit"
}

echo
echo "Запускаю линковку Signal как secondary device."
echo "На телефоне открой Signal -> Linked devices -> Link New Device."
echo "Важно: обычной камерой не сканируй, только из самого Signal."
echo
echo "Имя устройства: $DEVICE_NAME"
echo "Макс. попыток: $MAX_ATTEMPTS"
echo

LAST_LINK_VALUE=""
LAST_LINK_LOG_PATH=""
LINK_EXIT=1

for ((attempt=1; attempt<=MAX_ATTEMPTS; attempt++)); do
  run_link_attempt "$attempt"
  ATTEMPT_EXIT=$?
  if [[ $ATTEMPT_EXIT -eq 0 ]]; then
    LINK_EXIT=0
    break
  fi
  LINK_EXIT=$ATTEMPT_EXIT
  echo
  echo "⚠️ Попытка ${attempt} не завершилась успешно."
  if [[ -n "$LAST_LINK_LOG_PATH" && -f "$LAST_LINK_LOG_PATH" ]]; then
    echo "🧾 Лог попытки: $LAST_LINK_LOG_PATH"
    echo "Последние строки лога:"
    tail -n 5 "$LAST_LINK_LOG_PATH" || true
  fi
  if [[ -n "$LAST_LINK_VALUE" ]]; then
    echo "ℹ️ Ссылка была выдана, но подтверждение не прошло вовремя."
  fi
done

echo
if [[ $LINK_EXIT -ne 0 ]]; then
  echo "❌ Линковка не завершилась (код: $LINK_EXIT)."
  if [[ -n "$LAST_LINK_LOG_PATH" && -f "$LAST_LINK_LOG_PATH" ]]; then
    echo "🧾 Последний лог линковки: $LAST_LINK_LOG_PATH"
  fi
  if [[ -n "$LAST_LINK_VALUE" ]]; then
    echo "ℹ️ Последняя sgnl:// ссылка была получена, но линковка не подтвердилась вовремя."
  fi
  echo "Варианты:"
  echo "1) Повтори запуск (можно увеличить попытки: ./openclaw_signal_link.command 5)."
  echo "2) Проверь интернет/дату-время на Mac и iPhone."
  exit $LINK_EXIT
fi

echo "✅ Линковка завершена."
echo
echo "Найденные Signal account'ы в signal-cli:"
signal-cli listAccounts || true
echo
echo "Следующий шаг:"
echo "1) При необходимости задай OPENCLAW_SIGNAL_NUMBER в .env"
echo "2) Запусти ./openclaw_signal_daemon.command"
echo "3) Проверь ./openclaw_signal_daemon_status.command"
echo
read -k "_ANY?Нажми любую клавишу для закрытия..."
echo
