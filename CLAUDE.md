# CLAUDE.md

Этот файл даёт краткий, но truthful-контекст для Claude Code при работе
с репозиторием Krab.

Он не заменяет runtime-source-of-truth. Если этот файл расходится с живым
runtime OpenClaw, верить нужно runtime.

## Что читать первым

Перед любыми выводами о проекте сначала прочитай:

1. `/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/docs/MASTER_PLAN_VNEXT_RU.md`
3. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
4. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/SESSION_HANDOFF.md`
5. Runtime truth:
   - `~/.openclaw/openclaw.json`
   - `~/.openclaw/agents/main/agent/models.json`
   - `~/.openclaw/agents/main/agent/auth-profiles.json`
   - `~/.openclaw/workspace-main-messaging/*`

Без этого нельзя честно утверждать:

- какая модель сейчас primary;
- какая fallback-цепочка реально жива;
- есть ли у канала owner-права;
- какой % проекта считать актуальным.

## Что это за проект

Краб — это не просто Telegram-бот. Это персональный Telegram userbot владельца
на MTProto, связанный с OpenClaw Gateway, owner-панелью на `:8080`,
нативным dashboard OpenClaw на `:18789`, голосовым и browser-контуром, плюс
локальными и облачными AI-провайдерами.

Важно различать три разных контура:

- **Telegram userbot** — боевой канал доставки и owner-взаимодействия.
- **Owner panel `http://127.0.0.1:8080`** — операционная панель Краба.
- **Native OpenClaw dashboard `http://127.0.0.1:18789`** — нативный chat/control
  интерфейс OpenClaw.

Это **не один и тот же уровень полномочий**.

## Что считать истиной по правам и каналам

### Telegram userbot

Это самый привилегированный практический канал. Через него доступны:

- реальные входящие и исходящие owner-сообщения;
- userbot-команды;
- transport-поведение, близкое к боевому сценарию;
- ACL и routing в том виде, как они важны пользователю.

### Owner panel `:8080`

Это truth-oriented web-панель Краба для:

- health/runtime статуса;
- routing/autoswitch;
- ACL и provider readiness;
- owner-oriented ops-диагностики.

Но это **не замена userbot** и не доказательство, что transport в Telegram
работает так же.

### Native OpenClaw dashboard `:18789`

Это нативный dashboard/чат самого OpenClaw.

Он полезен для:

- проверки tool activity;
- проверки chat runtime;
- быстрой диагностики provider/tool chain;
- наблюдения за тем, что агент реально вызывает инструменты.

Но у него **не гарантированно те же права и интеграции**, что у Telegram userbot.
Если нужно доказать поведение боевого контура, проверяй Telegram отдельно.

## Текущая truthful operational картина

Актуальный baseline на `19.03.2026` нужно сверять по
`docs/handoff/SESSION_HANDOFF.md`, но на момент синхронизации этого файла
картина такая:

- live primary: `codex-cli/gpt-5.4`
- cloud safety fallback: `google-gemini-cli/gemini-3-flash-preview`
- `openai-codex/gpt-5.4` оставлять только как нестабильный fallback
- `qwen-portal/coder-model` допустим как поздний резерв, но там возможен
  `rate_limit`
- `google-antigravity/*` не считать live-источником: этот контур сейчас
  намеренно исключён из рабочей цепочки

### Что важно про провайдеров

- `codex-cli/gpt-5.4` сейчас основной путь, если нужен маршрут через подписку
  OpenAI Plus.
- `openai-codex/gpt-5.4` нельзя считать надёжным production-primary:
  он умеет отвечать, но на серии запросов деградирует по latency и route stability.
- Google REST API должен использовать **платный** ключ, а не free-key.
  Truth по этому вопросу проверяй через `.env`, runtime config и live probe,
  а не по старым заметкам.

## Ключевые правила работы в этом репозитории

- Не дублируй нативный функционал OpenClaw, если он уже существует в runtime
  или CLI.
- Repo-level документация не должна притворяться боевой памятью Краба.
- После правок в routing/runtime/UI обновляй handoff-доки, иначе следующий агент
  начнёт работать по устаревшей картине.
- Проценты готовности считать по
  `/Users/pablito/Antigravity_AGENTS/Краб/docs/MASTER_PLAN_VNEXT_RU.md`,
  а не по локальному инциденту.
- Канонический shared repo path для multi-account работы:
  `/Users/Shared/Antigravity_AGENTS/Краб`
- Практический fast-path, пока legacy shared repo не reconciled:
  `/Users/Shared/Antigravity_AGENTS/Краб-active`
- Runtime/auth/browser state при multi-account всегда per-account.
- Правила владения shared path, прав записи и reclaim/freeze брать из
  `/Users/pablito/Antigravity_AGENTS/Краб/docs/MULTI_ACCOUNT_SWITCHOVER_RU.md`.
- Перед уходом на другую учётку сначала запускать
  `/Users/pablito/Antigravity_AGENTS/Краб/Prepare Next Account Session.command`.

## Команды и запуск

### Канонические macOS launchers

- `/Users/pablito/Antigravity_AGENTS/new start_krab.command`
- `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Restart Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Start Voice Gateway.command`

### Полезные локальные команды

```bash
python -m src.main
pytest tests/
pytest tests/unit/test_openclaw_client.py -q
ruff check src/
ruff format src/
```

## Что проверять после изменений

Минимальный truthful набор:

1. unit-тесты по изменённому контуру;
2. `http://127.0.0.1:8080/api/health/lite`;
3. owner panel `:8080`;
4. native dashboard `:18789`, если менялся chat/tool/runtime слой;
5. Telegram owner roundtrip, если менялся userbot/transport/progress UX.

Если проблема касается model routing, дополнительно проверь:

- `http://127.0.0.1:8080/api/openclaw/model-routing/status`
- live route в `api/health/lite`
- фактический runtime config в `~/.openclaw/*`

## Что сейчас считается незакрытым

Не называй эти блоки «решёнными», пока нет свежего acceptance:

- true token streaming/partial delivery в Telegram;
- полная предсказуемость fallback-переходов при долгом first response;
- отдельное чистое `advanced`-окружение для `Krab Ear` под
  `torch + torchaudio + pyannote`;
- полное выравнивание dashboard/tool UX и Telegram progress UX.

## Что делать, если картина расходится

Если этот файл, handoff, owner panel и runtime показывают разное:

1. сначала верь `~/.openclaw/*`;
2. затем верь live endpoints (`:8080/api/...`);
3. затем обновляй docs;
4. и только потом делай выводы о регрессии.

## MCP Telegram Server

**Файлы:**
```
mcp-servers/telegram/telegram_bridge.py   ← Pyrogram singleton, без MCP-зависимостей
mcp-servers/telegram/server.py            ← FastMCP + 10 инструментов + CLI argparse
```

**Запуск:**
```bash
# stdio (Claude Desktop, Codex, Cursor)
python mcp-servers/telegram/server.py --transport stdio

# SSE (web-клиенты, отладка)
python mcp-servers/telegram/server.py --transport sse --port 8000 --host 127.0.0.1
```

**Инструменты (10 штук):**

| Инструмент | Описание |
|---|---|
| `telegram_get_dialogs` | Список последних диалогов |
| `telegram_get_chat_history` | История сообщений чата |
| `telegram_send_message` | Отправить текстовое сообщение |
| `telegram_download_media` | Скачать медиафайл по message_id |
| `telegram_transcribe_voice` | Транскрипция голосового → KrabEar IPC, fallback mlx-whisper |
| `telegram_search` | Глобальный поиск по всем чатам |
| `telegram_edit_message` | Редактировать отправленное сообщение |
| `krab_status` | GET /api/health/lite — статус OpenClaw gateway |
| `krab_tail_logs` | Хвост openclaw.log (n строк) |
| `krab_restart_gateway` | Перезапуск gateway через openclaw CLI |

**Транскрипция (приоритет):**
1. KrabEar IPC (`~/Library/Application Support/KrabEar/krabear.sock`) — Metal GPU, whisper-large-v3-turbo уже тёплый
2. Fallback: `mlx_whisper.transcribe()` напрямую — если KrabEar не запущен

**Сессия:** хранится как `{TELEGRAM_SESSION_NAME}_mcp` в `~/.krab_mcp_sessions/`
— не конфликтует с боевым сеансом основного Краба.

**Регистрация в реестре:** запись `"telegram"` в `src/core/mcp_registry.py`
— `risk="high"`, используется venv Python из `.venv/bin/python`.

**Claude Desktop:** конфиг в `~/Library/Application Support/Claude/claude_desktop_config.json`
— сервер `"krab-telegram"` + 6 managed серверов через `scripts/run_managed_mcp_server.py`.
