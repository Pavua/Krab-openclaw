# Father Reminder Assistant

Личный контур для аккуратных напоминаний отцу через Telegram/iMessage.

## Зачем

Задача не в автоспаме, а в управляемом цикле:

- хранить приватные контакты вне git;
- смотреть iMessage-историю только read-only, включая современные сообщения из `attributedBody`;
- собирать короткий черновик напоминания;
- по умолчанию делать только dry-run;
- реальную отправку разрешать только явным `--confirm-send`.
- периодически проверять due-состояние без дублей чаще заданного cadence.

## Файлы

- `scripts/agent_tools/krab_father_reminder.py` — основной tool;
- `Father Reminder Assistant.command` — запуск одним кликом в macOS;
- `Install Father Reminder Schedule.command` — установка macOS LaunchAgent;
- `~/.openclaw/krab_runtime_state/father_reminder.json` — приватный локальный конфиг, не коммитится;
- `~/.openclaw/krab_runtime_state/father_reminder_state.json` — журнал последней реальной отправки;
- `tests/unit/test_father_reminder_tool.py` — тесты безопасных режимов.

## Использование

Проверить статус:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py status
```

Собрать черновик:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py draft
```

Dry-run Telegram:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py send --channel telegram --dry-run
```

Проверить, пора ли напоминать, без реальной отправки:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py run-due --channel telegram --dry-run
```

Read-only iMessage-анализ:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py analyze --limit 40 --context-items 8
```

Реальная отправка требует явного подтверждения:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py send --channel telegram --confirm-send
```

Реальная due-отправка с защитой от повторов:

```bash
venv/bin/python scripts/agent_tools/krab_father_reminder.py run-due --channel telegram --confirm-send --first-time-confirm
```

Установка расписания в macOS:

```bash
./Install\ Father\ Reminder\ Schedule.command
```

LaunchAgent просыпается раз в час, но сам tool отправляет только если прошёл
`cadence_days` из приватного конфига. Это не автоповтор каждый час, а проверка
условия с журналом последней отправки.

## Ограничения

- iMessage-чтение требует Full Disk Access для процесса, который запускает скрипт.
- Messages.app может хранить текст в `message.attributedBody`, поэтому анализатор
  читает plain-text из `NSAttributedString` typedstream и не меняет локальную БД.
- Telegram-отправка использует Pyrogram session `kraab`; при параллельном runtime возможен SQLite lock.
- Автоматизация включается отдельным `.command`, чтобы расписание было явным,
  проверяемым и снимаемым через `launchctl`.
