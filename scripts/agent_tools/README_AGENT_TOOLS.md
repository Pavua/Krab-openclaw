# Agent Tools (Wave 44-R-script-tools)

Bash-вызываемые скрипты для codex-cli (gpt-5.5) внутри Krab agent loop.
Контекст: после Wave 9-B (02.05) Telegram MCP отключены в codex
(`/Users/pablito/.codex/config.toml`) из-за hallucinated tool calls.
Эти скрипты — альтернатива: codex вызывает их через нативный bash tool,
а действия выполняются через kraab.session (main userbot session).

## Скрипты

Все возвращают JSON `{"ok": bool, ...}` и логируют в `/tmp/krab_agent_tools.log`.

### 1. `krab_send_to_swarm.py`

Постит сообщение в Krab Swarm group (-1003703978531).

```bash
venv/bin/python scripts/agent_tools/krab_send_to_swarm.py \
    --text "!swarm task create --auto analysts CryptoBot M0: research"
# {"ok": true, "message_id": 4321, "chat_id": -1003703978531, ...}

# С topic_id:
venv/bin/python scripts/agent_tools/krab_send_to_swarm.py \
    --text "!swarm coders loop 2 ..." --topic 5
```

### 2. `krab_send_dm.py`

Сообщение в whitelisted chat (Krab Swarm group или owner DM 312322764).

```bash
venv/bin/python scripts/agent_tools/krab_send_dm.py \
    --chat-id 312322764 --text "Готово"
# Любой другой chat: добавь --allow-any
```

### 3. `krab_screenshot.py`

Скриншот через `screencapture -x` (без shutter). Валидация: > 20KB и
не all-white (если установлен Pillow).

```bash
venv/bin/python scripts/agent_tools/krab_screenshot.py \
    --output /tmp/screen.png
# {"ok": true, "path": "/tmp/screen.png", "size_bytes": 245678, ...}
```

### 4. `krab_run_command.py`

Выполняет !command — сначала через HTTP /api/* (owner panel :8080), при
fallback'е отправляет в owner DM, где работающий Krab userbot её обработает.

```bash
venv/bin/python scripts/agent_tools/krab_run_command.py --command "!status"
# {"ok": true, "mode": "http", "result": {...}}

venv/bin/python scripts/agent_tools/krab_run_command.py --command "!swarm teams"
# {"ok": true, "mode": "dm-delivery", "message_id": ..., note: "..."}
```

## Правила для агента

1. **Всегда** проверяй `ok` поле — если `false`, **не говори "отправил"**;
   репортинь real `error` владельцу.
2. **Идемпотентность**: re-run = новое сообщение. Не повторяй send для одной
   и той же логической задачи без причины.
3. Для arbitrary !команд используй `krab_run_command.py --prefer-dm` —
   результат вернётся в Telegram, не в JSON.
4. Логи всех запусков: `tail -50 /tmp/krab_agent_tools.log`.

## Pyrogram session contention

Скрипты открывают `kraab.session` в `no_updates=True` режиме. Параллельный
запуск с running Krab userbot может вызвать SQLite lock race. Если
`{"ok": false, "error": "database is locked"}` — ретрай через 1-2 секунды.
