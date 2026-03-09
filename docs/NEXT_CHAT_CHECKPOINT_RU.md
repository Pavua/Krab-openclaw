"""
Краткий checkpoint для перехода в новый чат.
Нужен, чтобы не потерять текущее состояние стабилизации Krab/OpenClaw,
результаты по подпискам/OAuth и ближайшие приоритеты.
"""

# Checkpoint Krab/OpenClaw

Дата: 2026-03-09

## Текущая оценка готовности

- Общая готовность: примерно 84%
- Глубина рассуждений для следующего окна: `medium`

## Что уже доведено

- Локальный контур `LM Studio + Nemotron` заметно стабилизирован.
- Сильно снижен шум `GET /api/v1/models`.
- Userbot стал правдивее по self-check/model/runtime ответам.
- Userbot `photo-path` теперь по умолчанию идёт в cloud и не должен самовольно выгружать `Nemotron` ради случайной local vision-модели.
- Runtime/UI truth в web panel и OpenClaw control стал заметно ближе к факту.
- Вычищен большой пласт stale session/pin/config debt в `~/.openclaw`.

## Что ещё не закрыто

### 1. Внешние каналы OpenClaw

- Есть delivery drift между userbot и внешними OpenClaw-каналами.
- Особенно важны:
  - Telegram bot
  - WhatsApp
  - iMessage

### 2. iMessage reply мусор

- В iMessage ещё просачивается `[[reply_to:...]]`.
- Нельзя чинить это через `replyToMode=off` в channel config: такой ключ невалиден по schema OpenClaw.
- Чинить надо в transport/send-path или sanitizer-слое.

### 3. Vision / model switching

- Userbot-photo по умолчанию уже переведён в cloud.
- Нужно убедиться live, что:
  - `Nemotron` не выгружается без нужды,
  - не загружается случайная маленькая local VL-модель,
  - язык ответа остаётся предсказуемым.

## Подписки / OAuth

### OpenAI / ChatGPT

- Пройден официальный flow через:

```bash
openclaw onboard --auth-choice openai-codex --mode local --skip-channels --skip-skills --skip-ui --skip-daemon
```

- Browser login был завершён пользователем.
- Callback URL был вставлен вручную.
- Итог: OAuth exchange провалился ошибкой:

```text
token_exchange_user_error
```

- Это не проблема browser callback, а проблема server-side token exchange.
- OAuth profile в OpenClaw не создался.

### Gemini CLI

- Плагин включён:

```bash
openclaw plugins enable google-gemini-cli-auth
```

- Login flow запускался:

```bash
openclaw models auth login --provider google-gemini-cli --set-default
```

- Пользователь успешно вошёл в Google в браузере.
- Итог: exchange провалился ошибкой:

```text
loadCodeAssist failed: 400 Bad Request
```

- Значит Gemini CLI OAuth тоже пока не подключён.

### Google Antigravity

- Путь выглядит нестабильным в текущей сборке:
  - OpenClaw помечает `google-antigravity-auth` как stale/removed config entry.
- Не использовать как основной боевой путь без отдельного аудита.

## Важные файлы последнего этапа

- `/Users/pablito/Antigravity_AGENTS/Краб/src/userbot_bridge.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/src/config.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/.env.example`
- `/Users/pablito/Antigravity_AGENTS/Краб/tests/unit/test_userbot_photo_flow.py`
- `/Users/pablito/Antigravity_AGENTS/Краб/docs/SAFE_SUBSCRIPTIONS_PLAN_RU.md`

## Что проверить первым в новом чате

1. Live-проверка userbot photo-flow после force-cloud фикса.
2. Live-проверка iMessage на `[[reply_to:*]]`.
3. Разобрать delivery drift внешних OpenClaw-каналов.
4. Вернуться к подпискам:
   - понять, почему `openai-codex` даёт `token_exchange_user_error`,
   - понять, почему `google-gemini-cli` даёт `loadCodeAssist failed: 400`.

## Короткий handoff-текст для нового окна

```text
Продолжаем Krab/OpenClaw с checkpoint ~84%.

Уже сделано:
- локальный LM Studio + Nemotron сильно стабилизирован
- userbot truthful self-check/model/runtime fast-path
- sanitizer ложных self-check ответов
- userbot photo-path по умолчанию forced cloud
- poll-noise LM Studio сильно снижен

Не закрыто:
1) iMessage reply_to мусор [[reply_to:*]]
2) delivery drift внешних OpenClaw-каналов
3) vision/model switching live-проверка после force-cloud фикса
4) OAuth/subscriptions:
   - openai-codex login дошёл до browser auth, но exchange упал с token_exchange_user_error
   - google-gemini-cli login дошёл до browser auth, но exchange упал с loadCodeAssist failed: 400 Bad Request

Важные файлы:
- src/userbot_bridge.py
- src/config.py
- .env.example
- docs/SAFE_SUBSCRIPTIONS_PLAN_RU.md
- docs/NEXT_CHAT_CHECKPOINT_RU.md

Работать дальше экономно, отвечать по-русски.
```
