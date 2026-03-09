"""
Краткий checkpoint для перехода в новый чат.
Нужен, чтобы не потерять текущее состояние стабилизации Krab/OpenClaw,
результаты по подпискам/OAuth и ближайшие приоритеты.
"""

# Checkpoint Krab/OpenClaw

Дата: 2026-03-09 (обновлён после stabilization-final)

## Текущая оценка готовности

- Общая готовность: **~95%**
- Все 263 unit-теста проходят (0 failed)
- Ветка: `codex/stabilization-final` (pushed to GitHub)
- Глубина рассуждений для следующего окна: `light`

## Что доведено

- Локальный контур `LM Studio + Nemotron` стабилизирован.
- Шум `GET /api/v1/models` сильно снижен.
- Userbot правдивее по self-check/model/runtime ответам.
- Userbot `photo-path` → по умолчанию cloud, Nemotron не выгружается.
- Runtime/UI truth в web panel и OpenClaw control ближе к факту.
- Вычищен stale session/pin/config debt в `~/.openclaw`.
- **iMessage `[[reply_to:*]]`** полностью зачищается плагином `krab-output-sanitizer` и `openclaw_runtime_repair.py`.
- **4 ранее падающих теста исправлены**: `test_config_defaults`, `test_vision_payload`, `test_get_best_model_local_first_in_auto`, `test_get_profile_recommendation`.
- **Побочные `MagicMock/` артефакты** удалены, добавлены в `.gitignore`.
- **67 тестов** по sanitizer/privacy/runtime repair — все пройдены.

## Что ещё не закрыто

### 1. Live-верификация (требует ручной проверки)

- Live-проверка photo-flow через Telegram userbot.
- Live-проверка iMessage на отсутствие `[[reply_to:*]]`.
- Live-проверка Web Panel runtime truth.

### 2. Delivery drift внешних OpenClaw-каналов

- Возможные расхождения между userbot и Telegram bot / WhatsApp / iMessage.
- Требует live-проверки с реальными сообщениями.

## Подписки / OAuth (статус не менялся)

### OpenAI / ChatGPT

- OAuth exchange провалился: `token_exchange_user_error`
- Проблема server-side, не browser callback.

### Gemini CLI

- Exchange провалился: `loadCodeAssist failed: 400 Bad Request`

### Google Antigravity

- Путь нестабилен: `google-antigravity-auth` помечен как stale.

> [!IMPORTANT]
> OAuth интеграции заблокированы на стороне провайдеров. Документировано в `docs/SAFE_SUBSCRIPTIONS_PLAN_RU.md`.

## Важные файлы

- `src/userbot_bridge.py` — мост Telegram ↔ OpenClaw
- `src/config.py` — все конфигурационные параметры
- `src/model_manager.py` — маршрутизация моделей
- `src/modules/web_router_compat.py` — UI-compatible routing
- `plugins/krab-output-sanitizer/index.mjs` — плагин зачистки
- `scripts/openclaw_runtime_repair.py` — runtime repair
- `docs/SAFE_SUBSCRIPTIONS_PLAN_RU.md` — план подписок

## Короткий handoff-текст для нового окна

```text
Продолжаем Krab/OpenClaw с checkpoint ~95%.

Уже сделано:
- 263/263 unit-тестов проходят (ветка codex/stabilization-final)
- iMessage [[reply_to:*]] полностью зачищается sanitizer-плагином
- 4 ранее падающих теста исправлены
- photo-path forced cloud, Nemotron не выгружается
- MagicMock артефакты удалены из git

Осталось (live-проверка):
1) Live photo-flow через Telegram
2) Live iMessage reply_to зачистка
3) Live Web Panel runtime truth
4) Delivery drift внешних OpenClaw-каналов

OAuth/subscriptions заблокированы провайдерами (см. SAFE_SUBSCRIPTIONS_PLAN_RU.md).
```
