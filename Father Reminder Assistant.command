#!/bin/zsh
# -*- coding: utf-8 -*-
#
# Лаунчер личного помощника по напоминаниям отцу.
# Открывается двойным кликом в macOS и по умолчанию ничего не отправляет:
# показывает статус приватного конфига и черновик сообщения.

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

echo "== Krab Father Reminder Assistant =="
echo
echo "1) Статус приватного конфига:"
"$PYTHON" scripts/agent_tools/krab_father_reminder.py status
echo
echo "2) Черновик напоминания:"
"$PYTHON" scripts/agent_tools/krab_father_reminder.py draft
echo
echo "3) Dry-run Telegram отправки:"
"$PYTHON" scripts/agent_tools/krab_father_reminder.py send --channel telegram --dry-run
echo
echo "4) Проверка расписания без реальной отправки:"
"$PYTHON" scripts/agent_tools/krab_father_reminder.py run-due --channel telegram --dry-run
echo
echo "Готово. Реальная отправка вручную: run-due/send с --confirm-send."
