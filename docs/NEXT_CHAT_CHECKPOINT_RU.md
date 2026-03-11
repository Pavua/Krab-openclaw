"""
Канонический checkpoint для перехода в новый диалог по ветке GPT-5.4 / userbot-primary.

Нужен, чтобы следующий диалог стартовал от фактического live-состояния на 2026-03-12,
а не от ранних handoff-файлов, где ещё считались открытыми уже закрытые блокеры.
"""

# Checkpoint Krab/OpenClaw

Дата: 2026-03-12
Ветка: `codex/live-8080-parallelism-acceptance`
Ориентировочная готовность большого плана: **~99%**

## Что уже подтверждено

- `openai-codex/gpt-5.4` живёт как runtime primary в OpenClaw.
- Cloud fallback chain собран так:
  - `google-gemini-cli/gemini-3.1-pro-preview`
  - `google/gemini-3.1-pro-preview`
  - `qwen-portal/coder-model`
  - `google/gemini-2.5-flash-lite`
- Telegram userbot живёт на общем workspace `~/.openclaw/workspace-main-messaging`.
- `!remember / !recall` userbot пишут и читают общую markdown-memory OpenClaw.
- Owner/full/partial ACL для userbot реализованы и доступны и через Telegram-команды, и через web panel.
- Telegram Bot ужесточён в reserve-safe режим:
  - `dmPolicy=allowlist`
  - `allowFrom=["312322764"]`
  - `groupPolicy=allowlist`
  - `groupAllowFrom=["312322764"]`
  - внешние tool-guards включены.
- Owner UI на `:8080` привязан к live runtime truth OpenClaw:
  - browser/MCP readiness закрыт,
  - cloud/local model catalog берётся из реального runtime,
  - staged health не врёт про `401` и stale fallback.

## Что закрыто в этой итерации

### Browser / UI / runtime truth

- `Browser / MCP Readiness` доведён до состояния `ready`:
  - UI показывает `Вкладка подключена`
  - `Tabs: 1`
  - `Required MCP: 3/3 ready`
  - кнопка `Запустить Browser Relay` корректно остаётся disabled после готовности.
- Локальный каталог owner UI теперь берётся из живого `LM Studio API`, а не из stale-кэша.
- Cloud-список в owner UI синхронизирован с реальной fallback-цепочкой OpenClaw.
- Ложный `current_primary_broken` убран: broken-state теперь не поднимается по историческим ошибкам.
- В `Интерфейс AI Ассистента` добавлен truthful meta-блок про параллелизм OpenClaw:
  - queue concurrency для main-agent;
  - queue concurrency для subagent lane;
  - явное разделение с named semantics `parallel / sequential` из других runtime-контуров.
- Изолированный browser smoke на `http://127.0.0.1:18081` подтвердил:
  - текст `main lane до 4 задач одновременно`;
  - текст `subagent lane до 8 задач одновременно`;
  - живой клик `Синхронизировать каталог`.
- Попытка переподтвердить тот же блок на live `:8080` из-под `USER2` упёрлась в ownership-барьер:
  stale `src.main` принадлежит `pablito`, переживает restart-попытку и не даёт заменить процесс без доступа владельца.

### Userbot / reserve / launcher

- Исправлен owner fast-path в [src/userbot_bridge.py](/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py):
  - запросы вида `проведи полную диагностику`
  - `проведи полную диагностику рантайма`
  - `cron у тебя уже работает`
  теперь идут в truthful `runtime self-check`, а не в свободную LLM-генерацию.
- Исправлен launcher-регресс в:
  - [new start_krab.command](/Users/pablito/Antigravity_AGENTS/Краб/new%20start_krab.command)
  - [new Stop Krab.command](/Users/pablito/Antigravity_AGENTS/Краб/new%20Stop%20Krab.command)
  проблема была в том, что `openclaw gateway stop` мог зависнуть бесконечно, если gateway уже не слушал порт.
- После этого patched launcher снова проходит controlled restart:
  - `:8080` поднимается,
  - `:18789` поднимается,
  - `telegram_userbot_state=running`.

### E2E / live probes

- `live_channel_smoke.py` зелёный, `success_rate = 100%`.
- `channels_photo_chrome_acceptance.py` зелёный.
- После controlled restart подтверждена живая reserve delivery через Telegram:
  - `openclaw message send --channel telegram --target 312322764 ... --json`
  - `payload.ok = true`
  - post-restart probe дал `messageId = 1187` на 2026-03-11.
- Через копию Telegram session прочитана живая история owner-чата `yung_nagato ↔ p0lrd`:
  - на 2026-03-11 05:02 userbot дал truthful self-check с `openai-codex/gpt-5.4`
  - на 2026-03-11 05:39 userbot отвечал в owner-чате после реальных сообщений владельца.

## Что проверено

### Unit

- `pytest -q tests/unit/test_web_app_runtime_endpoints.py -k 'browser_start_endpoint_returns_updated_readiness or browser_mcp_readiness_marks_authorized_running_browser_with_tabs_as_ready or browser_mcp_readiness_retries_transient_empty_cli_state_when_relay_authorized'`
  - `3 passed`
- `./.venv/bin/pytest tests/unit/test_web_app_runtime_endpoints.py -q`
  - `61 passed`
- `pytest -q tests/unit/test_userbot_capability_truth.py -k 'runtime_truth_question_detects_full_diagnostics_intent or full_diagnostics_question_uses_runtime_truth_fast_path or runtime_truth_question_uses_fast_path_without_llm'`
  - `3 passed`

### Browser / UI

- Живой click-through через owner UI:
  - `stop -> start from UI -> attached -> tabs=1 -> 3/3 ready`
- DOM-проверка подтвердила, что кнопка запуска relay остаётся disabled при `ready`.
- Дополнительно подтверждён изолированный browser smoke нового блока параллелизма:
  - сохранён snapshot после клика `Синхронизировать каталог`;
  - сохранён screenshot viewport.

### Live runtime

- `GET /api/health/lite` сейчас подтверждает:
  - `ok = true`
  - `telegram_session_state = ready`
  - `telegram_userbot_state = running`
  - `scheduler_enabled = true`
  - `last_runtime_route.model = openai-codex/gpt-5.4`
- `openclaw models status` подтверждает:
  - `Default: openai-codex/gpt-5.4`
  - fallbacks: `google-gemini-cli`, `google/gemini-3.1-pro-preview`, `qwen-portal`, `gemini-2.5-flash-lite`

## Что ещё не закрыто до абсолютного финиша

### 1. Строгий owner E2E после controlled restart

- Живой owner-чат уже подтверждён историей сообщений.
- Но строго автоматизированный active probe именно `owner -> userbot -> reply` после controlled restart пока не сделан.
- Причина не в runtime, а в том, что локально у нас нет автоматизированного доступа к owner-аккаунту `p0lrd`; доступна только сессия аккаунта `yung_nagato`.

### 2. Полный inbound round-trip reserve Telegram Bot

- Post-restart delivery из runtime в Telegram уже подтверждена.
- Но полный цикл `owner -> reserve bot -> agent reply` пока не автоматизирован по той же причине: нет отдельной owner-сессии для активной отправки с `p0lrd`.

### 3. OAuth-хвост у `google-gemini-cli`

- `openclaw models status` на 2026-03-11 показывает для `google-gemini-cli:default` статус `ok expires in 0m`.
- Gateway-log в этот же день фиксировал `OAuth token refresh failed for google-gemini-cli`.
- То есть fallback-слой собран правильно, но первый Google OAuth fallback сейчас считается хрупким, пока не будет переподтверждён повторным login/refresh.

### 4. Provenance warning плагина

- В smoke остаётся warning:
  - `krab-output-sanitizer loaded without install/load-path provenance`
- Это не runtime-blocker, но хвост доверенной provenance всё ещё не закрыт.

### 5. Live reload ownership barrier

- Controlled restart из-под `USER2` восстановил `:8080`, `:18789` и `telegram_userbot_state=running`, но не заменил старый `pablito`-owned `src.main`.
- Поэтому новый блок параллелизма уже подтверждён кодом, unit и изолированным browser smoke, но не должен считаться live-verified на основном `:8080`, пока restart не выполнит владелец `pablito`.

## Важные риски

- В рабочем дереве есть чужие незакоммиченные изменения; не трогать их без необходимости.
- `src/core/provider_manager.py` уже существует как незакоммиченный файл и требует аккуратной миграции, а не перезаписи.
- Нельзя объявлять migration на `100%`, пока не будет либо автоматизирован строгий owner round-trip, либо пользователь вручную не подтвердит его после свежего controlled restart.

## Рекомендуемый следующий этап

1. Переподтвердить live `:8080` для блока параллелизма после restart от владельца `pablito`.
2. Переподтвердить или перелогинить `google-gemini-cli`, потому что fallback сейчас на грани expiry.
3. Если нужен именно `100%` milestone:
   - либо сделать ручной owner-message сразу после свежего restart,
   - либо дать отдельную owner-session для автоматизированного probe.
4. После этого закрывать уже только provenance warning и финальный merge-gate.

## Рекомендуемые настройки для следующего окна

- Глубина рассуждений: `high`
- `fast`: выключен
- Рабочий branch текущего recovery-цикла: `codex/live-8080-parallelism-acceptance`

## Короткий handoff-текст для нового окна

```text
Продолжаем Krab/OpenClaw в ветке codex/live-8080-parallelism-acceptance.

Текущее состояние на 2026-03-12:
- готовность плана ~99%
- runtime primary = openai-codex/gpt-5.4
- browser/MCP readiness в owner UI закрыт и подтверждён живым click-through
- owner UI показывает реальный cloud/local catalog и truthful runtime health
- новый truthful meta-блок про parallelism уже реализован и подтверждён unit + изолированным browser smoke
- live :8080 для этого блока пока не переподтверждён, потому что stale src.main принадлежит pablito и не заменился из-под USER2
- reserve Telegram delivery подтверждена после controlled restart (`messageId=1187`)
- owner-chat history подтверждает реальные ответы userbot 2026-03-11 05:02 и 05:39
- исправлен launcher bug: `new start_krab.command` / `new Stop Krab.command` больше не должны виснуть на `openclaw gateway stop`
- исправлен userbot fast-path: `проведи полную диагностику` / `cron у тебя уже работает` теперь идут в deterministic self-check

Остались хвосты:
1) строгий active owner -> userbot -> reply E2E после restart ещё не автоматизирован
2) полный inbound owner -> reserve bot -> reply тоже ещё не автоматизирован
3) live :8080 нужно переподтвердить после restart именно от владельца pablito
4) `google-gemini-cli` хрупкий: в models status `expires in 0m`, в gateway-log был refresh failure
5) warning про `krab-output-sanitizer` provenance остаётся

Сначала прочитай:
1) docs/NEXT_CHAT_CHECKPOINT_RU.md
2) docs/OPENCLAW_KRAB_ROADMAP.md

Следующий лучший шаг:
1) переподтвердить live :8080 для блока parallelism после restart от pablito
2) проверить/перелогинить google-gemini-cli
3) затем закрыть строгий owner-E2E, если будет доступ к owner-аккаунту
```
