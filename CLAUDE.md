# CLAUDE.md

Этот файл даёт краткий, но truthful-контекст для Claude Code при работе
с репозиторием `/Users/pablito/Antigravity_AGENTS/Краб`.

Он не заменяет runtime-source-of-truth. Если этот файл расходится с живым
runtime OpenClaw, верить нужно runtime.

## Что читать первым

Перед любыми выводами о проекте сначала прочитай:

1. `/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
3. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/SESSION_HANDOFF.md`
4. Runtime truth:
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
- Проценты готовности считать по master-plan из
  `/Users/USER3/PLAN-Краб+переводчик 12.03.2026.md`,
  а не по локальному инциденту.

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
