"""
Канонический checkpoint для перехода в новый диалог по ветке GPT-5.4 / userbot-primary.

Нужен, чтобы следующий диалог стартовал от реального текущего состояния проекта,
а не от старых мартовских handoff-файлов.
"""

# Checkpoint Krab/OpenClaw

Дата: 2026-03-11
Ветка: `codex/gpt54-userbot-primary`
Ориентировочная готовность большого плана: **~82%**

## Что уже подтверждено

- `GPT-5.4` доступен в текущем Codex-контуре пользователя.
- `openai-codex/gpt-4.5-preview` в live OpenClaw падал с `404 model not found`.
- `GPT-5.4` добавлен в runtime registry OpenClaw безопасным canary-sync шагом.
- Live compatibility probe подтвердил: `openai-codex/gpt-5.4` = `READY` через OpenClaw gateway.
- Runtime primary уже переведён на `openai-codex/gpt-5.4`.
- Telegram userbot подключён к общему workspace `~/.openclaw/workspace-main-messaging`.
- `!remember / !recall` userbot пишут и читают общую markdown-memory OpenClaw.
- Введён ACL `owner / full / partial / guest` для userbot.
- Владелец может выдавать `full/partial` права через Telegram-команды и через web panel.
- Telegram Bot ужесточён в reserve-safe режим:
  - `dmPolicy=allowlist`
  - `allowFrom=["312322764"]`
  - `groupPolicy=allowlist`
  - `groupAllowFrom=["-1001804661353"]`
  - внешние tool-guards включены.

## Что сделано в этой ветке

### Документация и source-of-truth

- Repo-level `AGENTS.md`, `SKILLS.md`, `TOOLS.md` переписаны как developer docs.
- Канонический план ведётся в [docs/OPENCLAW_KRAB_ROADMAP.md](/Users/pablito/Antigravity_AGENTS/Краб/docs/OPENCLAW_KRAB_ROADMAP.md).
- Runtime source-of-truth зафиксирован как `~/.openclaw/*`.

### Userbot / ACL / память

- Userbot читает общий prompt/workspace bundle OpenClaw.
- Реализован ACL-слой в [src/core/access_control.py](/Users/pablito/Antigravity_AGENTS/Краб/src/core/access_control.py).
- Реализованы owner-команды `!acl / !access`.
- В web panel добавлен owner-oriented блок `Userbot ACL`.

### Routing / модели

- Добавлен runtime-aware model catalog в web panel.
- Добавлены профили autoswitch:
  - `production-safe`
  - `gpt54-canary`
- Добавлен read-only compat probe:
  - [scripts/openclaw_model_compat_probe.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_model_compat_probe.py)
- Добавлен safe registry sync:
  - [scripts/openclaw_model_registry_sync.py](/Users/pablito/Antigravity_AGENTS/Краб/scripts/openclaw_model_registry_sync.py)

### Безопасность внешних каналов

- Repair-слой умеет безопасно переводить `dmPolicy=allowlist` без wildcard-дыры.
- Repair-слой умеет выставлять `groupPolicy=allowlist` и выводить `groupAllowFrom` из live-конфига.
- Добавлен one-click файл:
  - [Apply Reserve Telegram Policy.command](/Users/pablito/Antigravity_AGENTS/Краб/Apply%20Reserve%20Telegram%20Policy.command)

## Последние ключевые коммиты

- `1357eaa` `Add web ACL controls for userbot`
- `d52e9fd` `Harden reserve Telegram bot policy`
- `409c21b` `Add GPT-5.4 canary registry sync`

## Что проверено

### Unit

- Web ACL endpoints: `47 passed`
- Runtime repair для Telegram reserve policy: `53 passed`
- GPT-5.4 registry sync + autoswitch + compat probe: `12 passed`

### Browser / UI

- Изолированный browser-smoke подтвердил:
  - `Userbot ACL` в панели читает owner/full/partial
  - `Refresh ACL` работает
  - `Grant / Revoke` реально обновляют runtime ACL на временном файле

### Live runtime

- `scripts/openclaw_model_compat_probe.py --model openai-codex/gpt-5.4 --reasoning high`
  вернул `READY`.
- `openclaw models status` теперь показывает:
  - `Default: openai-codex/gpt-5.4`
- Live `openclaw.json` подтверждает reserve-safe Telegram policy.

## Что ещё не закрыто

### 1. Browser / MCP readiness

- Довести owner browser-контур до полного readiness.
- Дать staged browser state в `:8080`.
- Собрать MCP readiness поверх уже существующего runtime-реестра.

### 2. E2E для каналов

- Полный smoke owner message через userbot после controlled restart.
- Emergency message через Telegram Bot в reserve-safe режиме.
- Проверка, что reserve bot действительно остался диагностическим/аварийным контуром.

### 3. Runtime/web консистентность

- Боевой процесс панели на `:8080` запущен без hot-reload и нуждается в controlled restart, чтобы подхватить новые endpoints/UI.
- После live-promotion `openclaw models status` показывает новый `Default`, но секция `Configured models` ещё требует проверки на консистентность со свежим registry.
- `google-antigravity` всё ещё disabled и пока не годится как боевой fallback.

## Важные риски

- В рабочем дереве есть чужие незакоммиченные изменения; не трогать их без необходимости.
- `src/core/provider_manager.py` уже существует как незакоммиченный файл и требует аккуратной миграции.
- Нельзя считать migration завершённой без live-smoke userbot и reserve bot после controlled restart.

## Рекомендуемый следующий этап

1. Controlled restart runtime / web panel.
2. Проверка, что `:8080` подхватил новые ACL endpoints и актуальный routing.
3. Browser/MCP readiness.
4. E2E userbot.
5. E2E reserve Telegram bot.

## Рекомендуемые настройки для следующего окна

- Глубина рассуждений: `high`
- `fast`: выключен
- Новый branch не нужен: продолжаем в `codex/gpt54-userbot-primary`, пока не закроем browser/MCP + E2E блок

## Короткий handoff-текст для нового окна

```text
Продолжаем Krab/OpenClaw в ветке codex/gpt54-userbot-primary.

Текущее состояние:
- готовность плана ~82%
- GPT-5.4 уже зарегистрирован в runtime registry OpenClaw
- live compat probe для openai-codex/gpt-5.4 = READY
- runtime primary уже переведён на openai-codex/gpt-5.4
- userbot сидит на общем workspace ~/.openclaw/workspace-main-messaging
- owner/full/partial ACL для userbot реализован
- Telegram Bot уже ужесточён в reserve-safe режим (allowlist для DM и групп)
- web ACL для userbot реализован и подтверждён browser-smoke

Сначала прочитай:
1) docs/NEXT_CHAT_CHECKPOINT_RU.md
2) docs/OPENCLAW_KRAB_ROADMAP.md
3) свежий artifacts/handoff_<timestamp>/START_NEXT_CHAT.md, если bundle уже собран

Следующий этап:
1) controlled restart runtime / web panel
2) browser/MCP readiness
3) E2E userbot
4) E2E reserve Telegram bot
5) проверить консистентность openclaw models status после GPT-5.4 promotion
```
