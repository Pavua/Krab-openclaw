# GEMINI.md

Этот файл даёт truthful-контекст для Gemini / Google Antigravity при работе
с репозиторием `/Users/pablito/Antigravity_AGENTS/Краб`.

Он не является боевой памятью Краба и не заменяет runtime-source-of-truth.

## Обязательный порядок чтения

Перед любой серьёзной работой сначала прочитай:

1. `/Users/pablito/Antigravity_AGENTS/Краб/AGENTS.md`
2. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`
3. `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/SESSION_HANDOFF.md`
4. Runtime truth:
   - `~/.openclaw/openclaw.json`
   - `~/.openclaw/agents/main/agent/models.json`
   - `~/.openclaw/agents/main/agent/auth-profiles.json`
   - `~/.openclaw/workspace-main-messaging/*`

Если этого не сделать, легко принять за истину старую документацию или
устаревший UI-cache.

## Что это за проект

Краб — это экосистема вокруг персонального Telegram userbot владельца,
OpenClaw Gateway, owner-панели `:8080`, native dashboard `:18789`,
browser/voice-контуров и набора локальных и облачных провайдеров.

Это **не** просто "Telegram бот с LLM".

## Что важно про каналы и полномочия

### Telegram userbot

Боевой канал с максимально важным для пользователя поведением:

- реальные owner-сообщения;
- userbot ACL;
- transport delivery;
- настоящий пользовательский маршрут.

### Owner panel `:8080`

Операционная панель Краба:

- health и runtime truth;
- routing/autoswitch;
- provider readiness;
- owner-oriented диагностика.

Но она не заменяет Telegram roundtrip.

### Native OpenClaw dashboard `:18789`

Нативный dashboard OpenClaw полезен, когда нужно видеть:

- какие tools реально вызываются;
- жив ли chat runtime;
- делает ли агент что-то под капотом, а не просто молчит.

Но это отдельный контур и не гарантия, что у него те же права, что у Telegram userbot.

## Актуальная operational truth

Проверяй свежий статус по `docs/handoff/SESSION_HANDOFF.md`, но на момент
синхронизации этого файла:

- основной live route: `codex-cli/gpt-5.4`
- быстрый cloud fallback: `google-gemini-cli/gemini-3-flash-preview`
- `openai-codex/gpt-5.4` считать нестабильным fallback, а не надёжным primary
- `qwen-portal/coder-model` держать как поздний резерв
- `google-antigravity/*` не считать рабочим live-путём по умолчанию

## Что важно именно для Gemini

- Если речь о Google REST API, нужно проверять, что runtime действительно использует
  **платный** ключ, а не free-key.
- Не делай вывод "Google сломан" только по старым логам: сначала смотри `.env`,
  runtime config и direct probe к `generativelanguage.googleapis.com`.
- Не путай `google/` REST provider и `google-gemini-cli/*` CLI/OAuth-provider:
  это разные operational пути.

## Правила разработки

1. Пиши и комментируй код на русском.
2. После правок сам запускай проверки.
3. Для UI/flow-задач обязательно проверяй браузером, а не только чтением кода.
4. Не дублируй нативный OpenClaw-функционал, если он уже существует.
5. После правок обновляй handoff-доки, если изменилась operational truth.

## Канонические launchers

- `/Users/pablito/Antigravity_AGENTS/new start_krab.command`
- `/Users/pablito/Antigravity_AGENTS/new Stop Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Restart Krab.command`
- `/Users/pablito/Antigravity_AGENTS/Краб/Start Voice Gateway.command`

## Минимальная верификация после изменений

1. unit-тесты затронутого контура;
2. `http://127.0.0.1:8080/api/health/lite`;
3. owner panel `:8080`;
4. native dashboard `:18789`, если менялись chat/tool/runtime пути;
5. Telegram roundtrip, если менялся userbot/transport/progress UX.

## Что нельзя считать полностью закрытым

Пока нет свежего acceptance, не объявляй полностью решёнными:

- истинный streaming/partial delivery в Telegram;
- полную наблюдаемость fallback-переходов;
- `Krab Ear advanced` с чистым совместимым стеком `torch + torchaudio + pyannote`;
- полный паритет observability между dashboard и Telegram.

## Как считать проценты

Проценты считать не по локальному инциденту, а по master-plan:

- источник: `/Users/USER3/PLAN-Краб+переводчик 12.03.2026.md`
- правило расчёта: `/Users/pablito/Antigravity_AGENTS/Краб/docs/handoff/MASTER_PLAN_SOURCE_OF_TRUTH.md`

Если встречаются старые проценты из прежних диалогов, сначала проверь, к какому
срезу они относились.
